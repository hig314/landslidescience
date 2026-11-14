/* dist.js — OPERA DIST land-surface disturbance: colour loader, date steppers, source defs.
 *
 * Moved out of map.js on 2026-11-14 without changing its text. map.js calls
 * window.LSDist.init(ctx) once, at the point in its start-up where this code
 * used to sit, and passes in the few things it needs from the map page:
 *   API_BASE, map, _ovRegistry, _ovSyncUI, _ovSwapSource.
 * It returns what map.js still uses: _distAnnDate, DIST_ALERT_ACTIVE, DIST_ALERT_OVERLAY, _distSourceDef, _distStepper, _distDatesReady.
 *
 * The distcolor tile protocol is registered here, beside its loader.
 */
(function () {
    'use strict';

    window.LSDist = { init: function (ctx) {
        var API_BASE = ctx.API_BASE;
        var map = ctx.map;
        var _ovRegistry = ctx._ovRegistry;
        var _ovSyncUI = ctx._ovSyncUI;
        var _ovSwapSource = ctx._ovSwapSource;

        // ---------------------------------------------------------------------------
        // OPERA DIST — land-surface disturbance (NASA GIBS upstream, proxied and
        // cached same-origin by inventory/dist.py). Two layers over one code space:
        // DIST-ALERT (daily, a live alert state that RESETS each year) and DIST-ANN
        // (the annual summary, which does not). Both are VEG-DIST-STATUS: GIBS
        // publishes no GEN-* (generic / non-vegetated) layer, so above treeline —
        // bare rock, talus, fresh debris — these show nothing. That gap needs the
        // LP DAAC COGs, not this route.
        //
        // Tiles arrive already coloured by GIBS as an 8-bit palette PNG, so unlike
        // the OPERA velocity tiles there is no value to decode. `distcolor` maps
        // GIBS's nine RGB triples back to class codes and repaints from
        // DIST_CLASSES — the palette below is the DECODE key, not a design choice,
        // and must track GIBS's colormap exactly or classes stop resolving.
        //
        // The colours we PAINT are ours, and deliberately not GIBS's: theirs puts
        // both axes on hue alone, which lets the huge and mostly meaningless
        // "first detection <50%" class dominate any view of Alaska.
        //
        // DARKNESS = IMPORTANCE = certainty x recency, reinforced by saturation.
        // The darkest class is therefore CONFIRMED (certain and still current),
        // NOT "finished" — equally certain, but its recency is spent, so it steps
        // back to a light dull brown. A plain monotonic ramp along maturity gets
        // this backwards and leaves the oldest, least actionable class loudest;
        // that is the bug this ramp was rewritten to fix. Magnitude rides the same
        // channel (<50% paler and duller than its >=50% partner, widest gap at
        // first detection), and maturity rides hue, red -> brown.
        //
        // tools/dist_color_status.txt is the source of truth — it carries the full
        // rationale, the accepted trade-off in the pale row, and the CVD check —
        // and it also feeds the colour key + export legend via
        // /inventory/api/ramps/. Change both together or the key stops describing
        // the pixels.
        // ---------------------------------------------------------------------------
        var DIST_TILE_V = '1';
        // [class code, GIBS rgb, our rgba]. GIBS rgb is what arrives; our rgba is
        // what we paint. Only class 0 differs: opaque near-black -> transparent.
        var DIST_CLASSES = [
            //  code  GIBS rgb (decode key)     ours (what we paint)
            [0, [ 18,  18,  18], [  0,   0,   0,   0]],   // no disturbance -> clear
            [1, [  0,  85,  85], [237, 212, 215, 255]],   // first detection  <50%   palest of all
            [2, [137, 127,  78], [229, 183, 169, 255]],   // provisional      <50%
            [3, [222, 224,  67], [197, 141,  99, 255]],   // confirmed        <50%   darkest pale
            [4, [  0, 136, 136], [240,  51,  73, 255]],   // first detection >=50%   loud via chroma
            [5, [228, 135,  39], [207,  66,  23, 255]],   // provisional     >=50%
            [6, [224,  27,   7], [110,  60,  23, 255]],   // confirmed       >=50%   darkest = most important
            [7, [119, 119, 119], [212, 205, 196, 255]],   // confirmed  <50% finished
            [8, [221, 221, 221], [164, 141, 112, 255]]    // confirmed >=50% finished  steps back
        ];
        // Exact-match lookup keyed by packed GIBS rgb, holding the PAINTED classes
        // (1-8) only. GIBS serves these as an 8-bit COLORMAP PNG carrying no gAMA
        // / sRGB / iCCP chunk (verified on a live tile, 2026-09-10), so the decoded
        // bytes are the palette entries verbatim and an exact match is enough.
        //
        // Class 0 is deliberately NOT in the table: everything unrecognised falls
        // through to transparent, which makes 0 ("no disturbance", GIBS #121212)
        // and any future palette change fail the same safe way. Matching 0 by
        // colour instead would mean that one missed match paints opaque near-black
        // over ~97% of every tile — an unusable map — and that is too sharp an
        // edge to balance on a colour comparison.
        var _distLut = (function () {
            var m = new Map();
            DIST_CLASSES.forEach(function (c) {
                if (!c[2][3]) return;                    // alpha 0 -> not painted
                m.set((c[1][0] << 16) | (c[1][1] << 8) | c[1][2], c[2]);
            });
            return m;
        })();

        // Which date each DIST layer is showing. '' means "latest", which resolves
        // against the GIBS time domain once /api/dist_dates/ answers; until then a
        // conservative fallback keeps the source definable at map-build time (the
        // layers start hidden, so the swap when the real answer lands is unseen).
        var _distDomains = null;              // {alert: {domain, default}, ann: {…}}
        var _distDatesPending = null;
        function _distFallbackDate(layer) {
            if (layer === 'ann') return (new Date().getUTCFullYear() - 1) + '-01-01';
            var d = new Date(Date.now() - 3 * 86400000);
            return d.toISOString().slice(0, 10);
        }
        // Expand an ISO8601 domain ('a/b/P1D,c/d/P1D') into an ascending date list.
        // Ranges, not an expanded list, come over the wire (~500 B vs ~17 kB); the
        // control has to walk them anyway to step past the gaps in GIBS's coverage.
        function _distExpand(domain) {
            var out = [];
            (domain || '').split(',').forEach(function (chunk) {
                var p = chunk.trim().split('/');
                if (!p[0]) return;
                if (p.length < 2) { out.push(p[0]); return; }
                var step = p[2] === 'P1Y' ? 'y' : 'd';
                var cur = new Date(p[0] + 'T00:00:00Z'), end = new Date(p[1] + 'T00:00:00Z');
                var guard = 0;
                while (cur <= end && guard++ < 20000) {
                    out.push(cur.toISOString().slice(0, 10));
                    if (step === 'y') cur.setUTCFullYear(cur.getUTCFullYear() + 1);
                    else cur.setUTCDate(cur.getUTCDate() + 1);
                }
            });
            return out;
        }
        function _distMakeDate(layer, storeKey) {
            var explicit = '', auto = false;
            try {
                explicit = localStorage.getItem(storeKey) || '';
                auto = localStorage.getItem(storeKey + '_auto') === '1';
            } catch (e) {}
            return {
                layer: layer,
                // The date actually used to build tile URLs.
                get: function () {
                    if (explicit) return explicit;
                    var d = _distDomains && _distDomains[layer];
                    return (d && d['default']) || _distFallbackDate(layer);
                },
                // No explicit date yet, so `get` is still on a fallback and the
                // source needs re-pointing once the real domain arrives.
                isUnresolved: function () { return !explicit; },
                // Still following "latest with data here" — either nothing has been
                // resolved yet, or what was resolved came from the coverage probe
                // rather than from the user. Re-enabling re-resolves these; a date
                // the user chose by hand is left alone.
                isTracking: function () { return !explicit || auto; },
                // set(v) marks a deliberate choice; set(v, true) an auto-resolved
                // one. '' returns the row to tracking.
                set: function (v, isAuto) {
                    explicit = v || '';
                    auto = !!isAuto && !!explicit;
                    try {
                        if (explicit) {
                            localStorage.setItem(storeKey, explicit);
                            localStorage.setItem(storeKey + '_auto', auto ? '1' : '0');
                        } else {
                            localStorage.removeItem(storeKey);
                            localStorage.removeItem(storeKey + '_auto');
                        }
                    } catch (e) {}
                },
                list: function () {
                    var d = _distDomains && _distDomains[layer];
                    return d ? _distExpand(d.domain) : [];
                }
            };
        }
        var _distAlertDate = _distMakeDate('alert', 'ls_dist_alert_date');
        var _distAnnDate   = _distMakeDate('ann',   'ls_dist_ann_date');

        // One fetch per session, shared by both rows and both panes.
        function _distLoadDates() {
            if (_distDatesPending) return _distDatesPending;
            _distDatesPending = fetch(API_BASE + 'api/dist_dates/')
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (j) {
                    _distDomains = (j && j.layers) || null;
                    return _distDomains;
                })
                .catch(function () { return null; });
            return _distDatesPending;
        }

        var _distEmptyTilePromise = null;
        function _distEmptyTile() {
            if (!_distEmptyTilePromise) {
                _distEmptyTilePromise = new Promise(function (resolve, reject) {
                    var c = document.createElement('canvas');
                    c.width = c.height = 1;
                    c.toBlob(function (blob) {
                        if (!blob) { reject(new Error('empty tile failed')); return; }
                        blob.arrayBuffer().then(function (buf) { resolve(buf); }, reject);
                    }, 'image/png');
                });
            }
            return _distEmptyTilePromise.then(function (buf) { return { data: buf }; });
        }
        function _distLoader(params, abortController) {
            // distcolor://<layer>/<date>/{z}/{x}/{y}?v=N  →  same-origin proxy tile,
            // canvas-decoded, class 0 dropped to transparent.
            var u = new URL(params.url.replace('distcolor://', 'https://dist.invalid/'));
            var p = u.pathname.split('/').filter(Boolean);   // [layer, date, z, x, y]
            var tileUrl = API_BASE + 'tiles/dist/' + p[0] + '/' + p[1] + '/' + p[2] + '/' +
                          p[3] + '/' + p[4] + '.png?v=' + (u.searchParams.get('v') || '1');
            return fetch(tileUrl, { signal: abortController ? abortController.signal : undefined })
                .then(function (r) {
                    // 404 = no coverage, or a date inside a gap in the GIBS domain.
                    if (r.status === 404) return null;
                    if (!r.ok) throw new Error('dist tile HTTP ' + r.status);
                    return r.blob().then(function (b) { return createImageBitmap(b); });
                })
                .then(function (bmp) {
                    if (!bmp) return _distEmptyTile();
                    var c = document.createElement('canvas');
                    c.width = c.height = 256;
                    var ctx = c.getContext('2d');
                    ctx.drawImage(bmp, 0, 0);
                    var img = ctx.getImageData(0, 0, 256, 256), d = img.data;
                    for (var i = 0; i < d.length; i += 4) {
                        if (d[i + 3] === 0) continue;    // GIBS no-data (index 255)
                        var hit = _distLut.get((d[i] << 16) | (d[i + 1] << 8) | d[i + 2]);
                        if (hit) {
                            d[i] = hit[0]; d[i + 1] = hit[1];
                            d[i + 2] = hit[2]; d[i + 3] = hit[3];
                        } else {
                            d[i] = d[i + 1] = d[i + 2] = d[i + 3] = 0;   // incl. class 0
                        }
                    }
                    ctx.putImageData(img, 0, 0);
                    return new Promise(function (resolve, reject) {
                        c.toBlob(function (blob) {
                            if (!blob) { reject(new Error('distcolor toBlob failed')); return; }
                            blob.arrayBuffer().then(function (buf) { resolve({ data: buf }); }, reject);
                        }, 'image/png');
                    });
                });
        }

        // ---- DIST-ALERT (daily): BUILT, EVALUATED, RETIRED 2026-09-10 ----------
        // Flip this to true to put the daily layer back on the map. Everything it
        // needs is still here and still works — the proxy route, the date
        // stepper, the coverage probe, tools/imagery/dist_animate.py.
        //
        // Why it is off, from the evaluation Hig and I ran:
        //  - COVERAGE. The daily layer holds only the granules acquired that day.
        //    Over a Talkeetna-sized box on 2026-09-08: 50.5% data, 20.1% no-data
        //    inside present tiles, 29.4% no tile at all. Half of Alaska is
        //    unobserved on any given date, which reads as "no disturbance here".
        //  - IT DOES NOT SHOW PROGRESSIVE CHANGE, which was the reason to want
        //    daily. The one unambiguous event we found (a Nepal landslide,
        //    28.288/85.518) appeared as a single step: 1.62 km2 of >=50%
        //    vegetation loss, every pixel dated 2026-08-26, next-largest date 76
        //    px. An event detector, not a trend detector.
        //  - The Alaska daily signal was dominated by seasonal snow — 86% of
        //    generic detections at Portage fall in October — and by the alert
        //    layer's own annual reset, which makes disturbance appear to vanish
        //    each year.
        // DIST-ANN has none of these problems: one composite per year, no
        // mid-year reset, and it is the layer that survived the evidence.
        var DIST_ALERT_ACTIVE = false;
        var DIST_ALERT_OVERLAY = {
            id: 'dist-alert', layerId: 'ov-dist-alert', sourceId: 'ov-dist-alert-src',
            label: 'Surface disturbance — alert (daily)',
            sub: 'OPERA DIST-ALERT-HLS, 30 m · ~50% of Alaska unobserved per day',
            sourceDef: function () { return _distSourceDef(_distAlertDate); },
            defOpacity: 0.85,
            stepper: _distStepper(_distAlertDate, 'date', 'date')
        };

        function _distSourceDef(dateCtl) {
            var date = dateCtl.get();
            // The merged 'all' tile is served with a 30-day cache header, and its
            // CONTENT changes when a new annual composite is published. Fold the
            // year set into the cache token so a new release rolls browser caches
            // without anyone remembering to bump DIST_TILE_V.
            var v = DIST_TILE_V;
            if (date === 'all') {
                var yrs = dateCtl.list();
                v += '.' + yrs.length + (yrs.length ? '-' + yrs[yrs.length - 1].slice(0, 4) : '');
            }
            return {
                type: 'raster',
                tiles: ['distcolor://' + dateCtl.layer + '/' + date +
                        '/{z}/{x}/{y}?v=' + v],
                tileSize: 256,
                maxzoom: 12,
                attribution: 'OPERA DIST-ALERT/ANN-HLS © NASA/JPL · LP DAAC · ' +
                             'imagery NASA GIBS'
            };
        }

        // Descriptor consumed by _overlayRow to draw the date control. Kept in the
        // same shape as `variant` (get/set + a title): global rather than per-pane,
        // because the two panes show the same product and a date that differed
        // between them would read as a wiper comparison it isn't.
        function _distStepper(dateCtl, label, kind, extra) {
            return {
                label: label,
                kind: kind,                 // 'date' (free date input) | 'select'
                title: kind === 'date'
                    ? 'Disturbance state as of this date. The daily product holds ' +
                      'only the granules acquired that day, so an empty view means ' +
                      '"not observed", not "nothing happened" — use "latest" to ' +
                      'jump to the newest date with data here.'
                    : 'Annual summary year.',
                get: function () { return dateCtl.get(); },
                isTracking: function () { return dateCtl.isTracking(); },
                set: function (v, isAuto) { dateCtl.set(v, isAuto); },
                resolve: function () { return _distResolveLatest(dateCtl); },
                list: function () { return dateCtl.list(); },
                // Options for the 'select' kind: value + what the reader sees.
                // `extra` holds pseudo-dates that are not real domain entries —
                // 'all' for the merged annual composite — kept out of list() so
                // stepping and snapping still only walk dates GIBS actually has.
                options: function () {
                    return (extra || []).concat(dateCtl.list().map(function (d) {
                        return { value: d, label: kind === 'select' ? d.slice(0, 4) : d };
                    }));
                },
                // Nearest valid date to `v`, so a typed date inside one of GIBS's
                // coverage gaps snaps to real data instead of silently drawing an
                // empty layer.
                snap: function (v) {
                    var list = dateCtl.list();
                    if (!list.length || !v) return v;
                    var best = list[0], bestGap = Infinity;
                    var t = Date.parse(v + 'T00:00:00Z');
                    if (isNaN(t)) return list[list.length - 1];
                    for (var i = 0; i < list.length; i++) {
                        var gap = Math.abs(Date.parse(list[i] + 'T00:00:00Z') - t);
                        if (gap < bestGap) { bestGap = gap; best = list[i]; }
                    }
                    return best;
                },
                // Walk the valid list, skipping GIBS's gaps. dir = -1 | +1.
                step: function (dir) {
                    var list = dateCtl.list();
                    if (!list.length) return null;
                    var cur = dateCtl.get();
                    var i = list.indexOf(cur);
                    if (i < 0) {                       // current isn't in the list
                        cur = this.snap(cur);
                        i = list.indexOf(cur);
                        if (i < 0) return null;
                    }
                    var j = Math.min(list.length - 1, Math.max(0, i + dir));
                    return list[j];
                },
                latest: function () {
                    var list = dateCtl.list();
                    return list.length ? list[list.length - 1] : '';
                },
                bounds: function () {
                    var list = dateCtl.list();
                    return list.length ? { min: list[0], max: list[list.length - 1] } : null;
                }
            };
        }

        // Ask the server for the newest date with data inside the CURRENT view.
        // The daily layer carries only the granules acquired that day, and about
        // half of Alaska goes unobserved on any given date, so "the newest
        // published date" routinely opens on an empty view — which reads as "no
        // disturbance here" rather than "nobody looked". Resolving against the
        // viewport is the difference between those two statements.
        function _distResolveLatest(dateCtl) {
            var b = map && map.getBounds && map.getBounds();
            if (!b) return Promise.resolve(null);
            var bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
                       .map(function (v) { return (+v).toFixed(4); }).join(',');
            return fetch(API_BASE + 'api/dist_coverage/?layer=' + dateCtl.layer +
                         '&bbox=' + encodeURIComponent(bbox))
                .then(function (r) { return r.ok ? r.json() : null; })
                .catch(function () { return null; });
        }

        // Rows that hold a stepper register here so the date list arriving late can
        // repaint them (and re-point any layer still following "latest").
        var _distDatesApplied = false;
        function _distDatesReady() {
            return _distLoadDates().then(function () {
                if (_distDatesApplied) return;   // idempotent: basemap switches re-call
                _distDatesApplied = true;
                _ovRegistry().forEach(function (ov) {
                    if (!ov.stepper) return;
                    // A row pinned to "latest" was built against the fallback date;
                    // now that the real newest date is known, re-point it.
                    if (ov.stepper.isTracking() && map && map.getLayer(ov.layerId)) {
                        _ovSwapSource(ov);
                    }
                });
                _ovSyncUI();
            });
        }
        // The tile protocol is registered here, beside its loader (it used to sit
        // with the OPERA one further up).
        if (typeof maplibregl !== 'undefined' && maplibregl.addProtocol) {
            maplibregl.addProtocol('distcolor', _distLoader);
        }

        return { _distAnnDate: _distAnnDate, DIST_ALERT_ACTIVE: DIST_ALERT_ACTIVE, DIST_ALERT_OVERLAY: DIST_ALERT_OVERLAY, _distSourceDef: _distSourceDef, _distStepper: _distStepper, _distDatesReady: _distDatesReady };
    } };
})();
