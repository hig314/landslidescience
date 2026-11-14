/* insar_ts.js — InSAR point time-series tool: click the map for OPERA DISP-S1 displacement histories (asc + desc), charted in a floating panel.
 *
 * Moved out of map.js on 2026-11-14 without changing its text. map.js calls
 * window.LSInsarTs.init(ctx) once, at the point in its start-up where this code
 * used to sit, and passes in the few things it needs from the map page:
 *   map, _swipe, makeFloatingPanel.
 * It returns what map.js still uses: nothing.
 */
(function () {
    'use strict';

    window.LSInsarTs = { init: function (ctx) {
        var map = ctx.map;
        var _swipe = ctx._swipe;
        var makeFloatingPanel = ctx.makeFloatingPanel;

        // -----------------------------------------------------------------------
        // InSAR point time-series tool ("InSAR" button, top-left stack).
        //
        // Click anywhere -> OPERA DISP-S1 short-wavelength displacement history,
        // asc + desc, charted in a floating panel. MULTI-POINT: each click adds a
        // sample (cap 6, oldest evicted); clicking an existing marker removes it —
        // the tool exists for comparison (landslide vs the stable slope beside
        // it), so several series overlay in one chart, color = sample, filled
        // dots = ascending, open rings = descending. Scatter only, no connecting
        // lines: acquisition gaps and stack boundaries drew as false trends.
        // Each reference stack remains relative to its OWN reference date.
        // Data flows through inventory/insar.py (7-day cache over ASF).
        // -----------------------------------------------------------------------
        (function () {
            var panel = document.getElementById('insar-panel');
            var canvas = document.getElementById('insar-canvas');
            var subEl = document.getElementById('insar-subtitle');
            if (!panel || !canvas) return;

            var MAX_SAMPLES = 6;
            // Okabe-Ito colorblind-safe palette (the earlier set paired green
            // and brown — indistinguishable under red-green CVD; Hig is
            // colorblind). Track identity is already shape-coded (filled vs
            // ring), so sample hue is the only color channel that must survive.
            var PALETTE = ['#0072B2', '#E69F00', '#009E73', '#CC79A7', '#56B4E9', '#D55E00'];
            var _samples = [];        // {id, letter, color, lat, lon, asc, desc, state:'loading'|'ok'|'err', err}
            var _nextId = 1;
            var _active = false;
            var _btn = null;

            _btn = LSTools.mode({
                id: 'insar', order: 40, label: 'InSAR',
                className: 'tool-text',
                title: 'InSAR time series — click points to chart + compare OPERA '
                       + 'displacement (Esc to exit)',
                aria: 'InSAR time series',
                onSelect: function () { setActive(true); },
                onRelease: function () { setActive(false); },
                cancel: function () { setActive(false); }   // nothing is ever "in progress" here
            });

            var fp = makeFloatingPanel(panel, {
                handle: panel.querySelector('.insar-header'),
                close: document.getElementById('insar-close'),
                onResize: draw
            });
            var clearBtn = document.getElementById('insar-clear');
            if (clearBtn) clearBtn.addEventListener('click', function () {
                _samples = [];
                syncMarkers(); syncSubtitle(); draw();
            });
            // Track toggles: chart-level filter (asc = filled, desc = rings).
            // Chart only — the fetches always get both tracks (cached anyway),
            // so re-enabling a track is instant.
            var showAsc = document.getElementById('insar-show-asc');
            var showDesc = document.getElementById('insar-show-desc');
            [showAsc, showDesc].forEach(function (cb) {
                if (cb) cb.addEventListener('change', draw);
            });
            function trackOn(k) {
                if (k === 'asc') return !showAsc || showAsc.checked;
                return !showDesc || showDesc.checked;
            }

            function setActive(on) {
                // Exclusion is structural now — claiming releases measure/draw.
                if (on && !LSTools.claim('insar')) return;
                if (!on) LSTools.release('insar');
                _active = on;
                if (on) window.LSTrack && LSTrack.event('map_tool', { tool: 'insar' });
                if (on) LSTools.cursor.set('tool', 'crosshair'); else LSTools.cursor.clear('tool');
                if (on) { fp.open(); draw(); }
            }

            // Markers: one source for all samples; sample-colored dot + letter.
            // Mirrored to the wiper pane (exception to the interaction-tool rule:
            // asc/desc wiper comparison IS this tool's core use, and un-mirrored
            // markers hide under the right pane).
            function _markerFC() {
                return { type: 'FeatureCollection', features: _samples.map(function (s) {
                    return { type: 'Feature',
                             geometry: { type: 'Point', coordinates: [s.lon, s.lat] },
                             properties: { color: s.color, letter: s.letter, sid: s.id,
                                           muted: !!s.muted } };
                }) };
            }
            function _ensureMarkerLayers(m, fc) {
                if (!m.getSource('insar-click-src')) {
                    m.addSource('insar-click-src', { type: 'geojson', data: fc });
                    // Sample color fills the dot (chart parity); a legend-muted
                    // sample stays on the map but FADES — parked, not lost.
                    m.addLayer({ id: 'insar-click-pt', type: 'circle', source: 'insar-click-src',
                        paint: { 'circle-radius': 7,
                                 'circle-color': ['get', 'color'],
                                 'circle-opacity': ['case', ['get', 'muted'], 0.12, 0.45],
                                 'circle-stroke-width': 2.5,
                                 'circle-stroke-color': ['get', 'color'],
                                 'circle-stroke-opacity': ['case', ['get', 'muted'], 0.25, 1] } });
                    m.addLayer({ id: 'insar-click-label', type: 'symbol', source: 'insar-click-src',
                        layout: { 'text-field': ['get', 'letter'],
                                  'text-font': ['Noto Sans Regular'],
                                  'text-size': 11, 'text-offset': [0, -1.4],
                                  'text-allow-overlap': true },
                        paint: { 'text-color': ['get', 'color'],
                                 'text-opacity': ['case', ['get', 'muted'], 0.35, 1],
                                 'text-halo-color': '#fff', 'text-halo-width': 1.4 } });
                } else {
                    m.getSource('insar-click-src').setData(fc);
                }
            }
            function syncMarkers() {
                var fc = _markerFC();
                _ensureMarkerLayers(map, fc);
                if (_swipe.map && _swipe.map.__lsStyleReady) _ensureMarkerLayers(_swipe.map, fc);
            }
            // Called by _swipeAddData when a wiper pane is (re)built, so a wiper
            // opened AFTER points were sampled still shows them.
            window.__insarSwipeSync = function (cmap) {
                _ensureMarkerLayers(cmap, _markerFC());
            };

            function syncSubtitle() {
                if (!_samples.length) {
                    subEl.textContent = 'Click the map to sample (up to ' + MAX_SAMPLES + ' points)';
                    return;
                }
                var parts = _samples.map(function (s) {
                    if (s.state === 'loading') return s.letter + ': \u2026';
                    if (s.state === 'err') return s.letter + ': ' + (s.err || 'failed');
                    if (s.err) return s.letter + ': one pass only (' + s.err + ')';
                    if (s.stale) return s.letter + ': cached (ASF unreachable)';
                    return s.letter + ': ' + (s.asc.n + s.desc.n);
                });
                subEl.textContent = _samples.length + ' point' + (_samples.length > 1 ? 's' : '') +
                    ' \u00b7 epochs ' + parts.join('  ');
            }

            function nextLetter() {
                var used = {};
                _samples.forEach(function (s) { used[s.letter] = 1; });
                for (var i = 0; i < 26; i++) {
                    var L = String.fromCharCode(65 + i);
                    if (!used[L]) return L;
                }
                return '?';
            }
            function nextColor() {
                var used = {};
                _samples.forEach(function (s) { used[s.color] = 1; });
                for (var i = 0; i < PALETTE.length; i++) {
                    if (!used[PALETTE[i]]) return PALETTE[i];
                }
                return PALETTE[0];
            }

            // Action entry: runs only while 'insar' holds the map. See measure.
            LSTools.clicks.register({ id: 'insar', kind: 'action', holders: ['insar'],
                                      handler: function (_f, e) {
                if (!_active) return;
                // Click on an existing marker = remove that sample.
                var hits = map.queryRenderedFeatures(e.point, { layers: map.getLayer('insar-click-pt') ? ['insar-click-pt'] : [] });
                if (hits.length) {
                    var sid = hits[0].properties.sid;
                    _samples = _samples.filter(function (s) { return s.id !== sid; });
                    syncMarkers(); syncSubtitle(); draw();
                    return;
                }
                if (_samples.length >= MAX_SAMPLES) _samples.shift();   // evict oldest
                var sample = { id: _nextId++, letter: nextLetter(), color: nextColor(),
                               lat: e.lngLat.lat, lon: e.lngLat.lng,
                               asc: null, desc: null, state: 'loading' };
                _samples.push(sample);
                syncMarkers(); syncSubtitle(); draw();
                fp.open();
                function get(dir) {
                    return fetch('/inventory/api/insar_timeseries/?lat=' + sample.lat.toFixed(4) +
                                 '&lon=' + sample.lon.toFixed(4) + '&dir=' + dir)
                        .then(function (r) {
                            // Guard the parse: an upstream hiccup can arrive as an
                            // HTML error page from a CDN, and r.json() would then
                            // throw "Unexpected token <", which is a useless thing
                            // to show a reader.
                            return r.text().then(function (t) {
                                var j = null;
                                try { j = JSON.parse(t); } catch (e) { j = null; }
                                if (!r.ok) {
                                    throw new Error((j && j.error) || ('HTTP ' + r.status));
                                }
                                if (!j) throw new Error('unreadable response');
                                return j;
                            });
                        })
                        // One retry on a transient failure. ASF answers 5xx under
                        // load, and a single retry turns most of those into data.
                        .catch(function (first) {
                            return new Promise(function (res) { setTimeout(res, 1200); })
                                .then(function () {
                                    return fetch('/inventory/api/insar_timeseries/?lat=' +
                                        sample.lat.toFixed(4) + '&lon=' +
                                        sample.lon.toFixed(4) + '&dir=' + dir)
                                        .then(function (r) {
                                            if (!r.ok) throw first;
                                            return r.json();
                                        });
                                });
                        })
                        // Resolve to an error MARKER rather than rejecting, so one
                        // track failing cannot discard the other. Promise.all used
                        // to lose a perfectly good descending series because
                        // ascending happened to 502.
                        .catch(function (err) { return { __err: err.message || 'failed' }; });
                }
                Promise.all([get('ascending'), get('descending')]).then(function (res) {
                    sample.asc = res[0] && res[0].__err ? null : res[0];
                    sample.desc = res[1] && res[1].__err ? null : res[1];
                    var errs = [res[0], res[1]].filter(function (x) { return x && x.__err; })
                                               .map(function (x) { return x.__err; });
                    sample.stale = !!((sample.asc && sample.asc.stale) ||
                                      (sample.desc && sample.desc.stale));
                    if (!sample.asc && !sample.desc) {
                        sample.state = 'err';
                        sample.err = errs[0] || 'no data';
                    } else {
                        sample.state = 'ok';
                        sample.err = errs.length ? errs[0] : null;
                    }
                    syncSubtitle(); draw();
                });
            } });

            function draw() {
                var r = canvas.getBoundingClientRect();
                if (!r.width || !r.height) return;
                var dpr = window.devicePixelRatio || 1;
                canvas.width = r.width * dpr; canvas.height = r.height * dpr;
                var ctx = canvas.getContext('2d');
                ctx.scale(dpr, dpr);
                var W = r.width, H = r.height;
                var ml = 44, mr = 10, mt = 8, mb = 22;
                ctx.clearRect(0, 0, W, H);
                ctx.font = '10px system-ui, sans-serif';

                // Two passes: axis extents come from ALL loaded data (muted
                // samples and toggled-off tracks included) so switching layers
                // never rescales the frame — points appear/disappear in place,
                // which is what makes visual comparison trustworthy. Only the
                // visible subset is drawn.
                var pts = [];
                var t0 = Infinity, t1 = -Infinity, v0 = Infinity, v1 = -Infinity;
                _samples.forEach(function (s) {
                    if (s.state !== 'ok') return;
                    ['asc', 'desc'].forEach(function (k) {
                        var visible = !s.muted && trackOn(k);
                        // A sample is 'ok' when EITHER pass answered; the other is
                        // null. Reading .series off that null threw here, after the
                        // canvas was cleared, so one failed pass blanked every
                        // point's plot (fixed 2026-11-14).
                        ((s[k] && s[k].series) || []).forEach(function (st) {
                            st.points.forEach(function (p) {
                                var t = Date.parse(p[0]);
                                if (t < t0) t0 = t; if (t > t1) t1 = t;
                                if (p[1] < v0) v0 = p[1]; if (p[1] > v1) v1 = p[1];
                                if (visible) pts.push({ c: s.color, k: k, t: t, v: p[1] });
                            });
                        });
                    });
                });
                var haveAny = isFinite(t0);
                if (!haveAny) {
                    ctx.fillStyle = '#999'; ctx.textAlign = 'center';
                    var anyLoading = _samples.some(function (s) { return s.state === 'loading'; });
                    ctx.fillText(anyLoading ? 'Fetching\u2026'
                                 : (_samples.length ? 'No DISP-S1 coverage at the sampled point(s).'
                                                    : (_active ? 'Click the map to sample a point.' : 'Tool inactive.')),
                                 W / 2, H / 2);
                    drawLegend(ctx, ml, mt);
                    return;
                }
                var pad = Math.max(2, (v1 - v0) * 0.08);
                v0 -= pad; v1 += pad;
                if (v0 > 0) v0 = 0;
                if (v1 < 0) v1 = 0;
                var cw = W - ml - mr, ch = H - mt - mb;
                function X(t) { return ml + (t - t0) / (t1 - t0 || 1) * cw; }
                function Y(v) { return mt + (v1 - v) / (v1 - v0 || 1) * ch; }
                ctx.fillStyle = '#777';
                ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
                var step = niceStep((v1 - v0) / 5);
                for (var v = Math.ceil(v0 / step) * step; v <= v1; v += step) {
                    var y = Y(v);
                    ctx.strokeStyle = Math.abs(v) < step / 2 ? '#999' : '#eee';
                    ctx.beginPath(); ctx.moveTo(ml, y); ctx.lineTo(W - mr, y); ctx.stroke();
                    ctx.fillText(Math.round(v) + '', ml - 5, y);
                }
                ctx.save();
                ctx.translate(11, mt + ch / 2); ctx.rotate(-Math.PI / 2);
                ctx.textAlign = 'center'; ctx.fillText('LOS displacement (mm)', 0, 0);
                ctx.restore();
                ctx.textAlign = 'center'; ctx.textBaseline = 'top';
                var yr0 = new Date(t0).getUTCFullYear(), yr1 = new Date(t1).getUTCFullYear();
                for (var yr = yr0; yr <= yr1 + 1; yr++) {
                    var t = Date.UTC(yr, 0, 1);
                    if (t < t0 || t > t1) continue;
                    var x = X(t);
                    ctx.strokeStyle = '#eee';
                    ctx.beginPath(); ctx.moveTo(x, mt); ctx.lineTo(x, mt + ch); ctx.stroke();
                    ctx.fillStyle = '#777';
                    ctx.fillText(String(yr), x, mt + ch + 4);
                }
                // scatter: filled = asc, ring = desc; color = sample
                pts.forEach(function (p) {
                    var x = X(p.t), y = Y(p.v);
                    ctx.beginPath();
                    if (p.k === 'asc') {
                        ctx.fillStyle = p.c;
                        ctx.arc(x, y, 2.0, 0, Math.PI * 2); ctx.fill();
                    } else {
                        ctx.strokeStyle = p.c; ctx.lineWidth = 1.1;
                        ctx.arc(x, y, 2.4, 0, Math.PI * 2); ctx.stroke();
                    }
                });
                drawLegend(ctx, ml, mt);
            }

            // Legend chips double as per-sample toggles: click hides that sample
            // from the CHART only (the map marker fades but stays — the sample is
            // parked, not lost). Hit rects recorded each draw for the click test.
            var _legendHits = [];
            function drawLegend(ctx, ml, mt) {
                _legendHits = [];
                ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
                ctx.font = '10px system-ui, sans-serif';
                var lx = ml + 6;
                _samples.forEach(function (s) {
                    if (s.state !== 'ok') return;
                    var w = 26;
                    if (s.muted) {
                        ctx.strokeStyle = s.color; ctx.lineWidth = 1;
                        ctx.globalAlpha = 0.45;
                        ctx.strokeRect(lx + 0.5, mt + 4.5, 8, 8);
                        ctx.fillStyle = '#999';
                        ctx.fillText(s.letter, lx + 11, mt + 12);
                        ctx.globalAlpha = 1;
                    } else {
                        ctx.fillStyle = s.color;
                        ctx.fillRect(lx, mt + 4, 8, 8);
                        ctx.fillStyle = '#444';
                        ctx.fillText(s.letter, lx + 11, mt + 12);
                    }
                    _legendHits.push({ x: lx - 3, y: mt, w: w, h: 16, id: s.id });
                    lx += w;
                });
            }
            canvas.addEventListener('click', function (e) {
                var r = canvas.getBoundingClientRect();
                var x = e.clientX - r.left, y = e.clientY - r.top;
                for (var i = 0; i < _legendHits.length; i++) {
                    var h = _legendHits[i];
                    if (x >= h.x && x <= h.x + h.w && y >= h.y && y <= h.y + h.h) {
                        var smp = _samples.filter(function (s) { return s.id === h.id; })[0];
                        if (smp) { smp.muted = !smp.muted; syncMarkers(); draw(); }
                        return;
                    }
                }
            });
            canvas.addEventListener('mousemove', function (e) {
                var r = canvas.getBoundingClientRect();
                var x = e.clientX - r.left, y = e.clientY - r.top;
                var over = _legendHits.some(function (h) {
                    return x >= h.x && x <= h.x + h.w && y >= h.y && y <= h.y + h.h;
                });
                canvas.style.cursor = over ? 'pointer' : '';
            });
            function niceStep(t) {
                var p = Math.pow(10, Math.floor(Math.log10(Math.max(1e-9, t))));
                var f = t / p;
                return (f >= 5 ? 10 : f >= 2 ? 5 : f >= 1 ? 2 : 1) * p;
            }
        })();

        return {  };
    } };
})();
