/* /glaciers — the Retreat tab: glacier identity and extent history.
 *
 * What is on the map (all from glaciers/sources.py, design in GLACIERS_PLAN.md):
 *
 *   glaciers      RGI 7.0 outlines, the thing a click selects.
 *   sources       dated outlines and terminus lines other people published,
 *                 mirrored as they shipped them. Each source is a layer that
 *                 can be switched off; all are coloured by YEAR with one ramp
 *                 (LSColors.glacierYearColor), so a retreat reads as a colour
 *                 sequence whoever drew the line.
 *   ice cover     NSIDC G10040, Landsat-derived statewide ice cover every two
 *                 years 1986-2020. ONE raster archive holds every epoch: a
 *                 pixel is a bit mask of the epochs that cell was ice
 *                 (tools/glaciers/build_cover_mask.py). The `glcover`
 *                 protocol below decodes a tile once and colours it for the
 *                 year on the bar: ice now, debris-covered ice, and ice that
 *                 was mapped in an earlier epoch and is gone.
 *
 * ONE YEAR BAR drives all three: a source row is drawn once the bar reaches
 * its year, and the ice cover shows the latest epoch at or before the bar.
 * For the selected glacier the latest observation per source at or before
 * the bar is drawn heavy, the earlier ones light, later ones not at all.
 *
 * These layers are other people's statements. The consensus layer that
 * editors improve is a later phase and will be a separate layer above them.
 *
 * Clicks go through LSTools (never a map.on('click') here): a glacier is a
 * `record`, a source line or the ice cover a `reference` popup.
 * URL params (additive, written by the shell): g = selected glacier id,
 * yr = year bar, src = sources switched on (absent = all), gc=0 = ice cover
 * off.
 */
window.GLRetreat = (function () {
    'use strict';

    var GL, map;
    // Right end of the year bar = the present. Was fixed at 2025, which hid
    // every face dated 2026 (the S1 auto faces run to the latest month).
    var NOW_YEAR = new Date().getFullYear();
    var DEF_MIN_YEAR = 1900;             // left end with nothing selected
    var COVER_YEARS = [];
    for (var cy = 1986; cy <= 2020; cy += 2) COVER_YEARS.push(cy);
    var COVER_ATTR = 'Ice cover: Roberts-Pierel, Kirchner, Kilbride &amp; Kennedy (2022), NSIDC G10040';
    var REF_ATTR = 'Glacier outlines: RGI 7.0 Consortium (2023)';

    var S = {
        stamp: '0',
        sources: [], srcOn: {}, identity: null,
        year: NOW_YEAR, minYear: DEF_MIN_YEAR,
        coverOn: true, baseOn: true, debrisOn: true, barOpen: false,
        sel: null,                       // api/glacier response
        selId: null,
        playing: false, playTimer: null,
        pick: {},                        // admin: card rows selected (obs id -> true)
        rpick: {},                       // admin: rejected-list rows selected
        lastRej: null                    // ids of the last reject, for its Undo
    };
    var el = {};

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function fmtArea(a) {
        if (a == null) return '';
        return (a >= 100 ? Math.round(a) : a >= 10 ? a.toFixed(1) : a.toFixed(2)) + ' km²';
    }
    // A date bracket as text (Hig's form, "18 Aug 2003"): one day, a whole
    // month ("Mar 2020", the S1 auto faces), or the year when that is all.
    var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    function fmtDate(p) {
        if (!p.date_min) return 'undated';
        var a = p.date_min, b = p.date_max || a;
        if (a === b) return +a.slice(8, 10) + ' ' + MON[+a.slice(5, 7) - 1] + ' ' + a.slice(0, 4);
        if (a.slice(0, 7) === b.slice(0, 7) && a.slice(8) === '01' &&
            +b.slice(8) === new Date(+b.slice(0, 4), +b.slice(5, 7), 0).getDate()) {
            return MON[+a.slice(5, 7) - 1] + ' ' + a.slice(0, 4);
        }
        if (a.slice(0, 4) === b.slice(0, 4)) return a.slice(0, 4);
        return p.date_min.slice(0, 4) + '–' + (p.date_max || '').slice(0, 4);
    }
    function srcById(id) {
        for (var i = 0; i < S.sources.length; i++) if (S.sources[i].id === id) return S.sources[i];
        return null;
    }
    function onSources() {
        return S.sources.filter(function (s) { return S.srcOn[s.id]; }).map(function (s) { return s.id; });
    }
    function visible() { return GL.tab() !== 'flow'; }

    // ------------------------------------------------------------------
    // Map layers. Idempotent: called on every style.load (a basemap change
    // replaces the style and takes every source and layer with it).
    // ------------------------------------------------------------------
    var OBS_LAYERS = ['gx-obs-line', 'gx-obs-debris', 'gx-obs-term'];
    var HIT_LAYERS = ['gx-obs-hit', 'gx-sel-hit'];

    function addLayers() {
        if (!window.__lsPmtilesReg && typeof pmtiles !== 'undefined') {
            window.__lsPmtilesReg = true;
            maplibregl.addProtocol('pmtiles', new pmtiles.Protocol().tile);
        }
        var yc = window.LSColors.glacierYearColor('y0');
        _coverTilesNow = null;
        if (!map.getSource('gx')) {
            map.addSource('gx', {
                type: 'vector',
                tiles: [location.origin + '/glaciers/tiles/{z}/{x}/{y}.pbf?v=' + S.stamp],
                minzoom: 3, maxzoom: 13, attribution: REF_ATTR
            });
        }
        if (!map.getSource('gx-sel')) {
            map.addSource('gx-sel', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        }
        if (!map.getSource('gx-selref')) {
            map.addSource('gx-selref', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        }
        function add(def) { if (!map.getLayer(def.id)) map.addLayer(def); }
        // The click target. Nearly transparent rather than transparent: a
        // fill at opacity 0 is not rendered, so it cannot be hit.
        add({ id: 'gx-ref-fill', type: 'fill', source: 'gx', 'source-layer': 'glaciers',
              paint: { 'fill-color': '#7fd3ff', 'fill-opacity': 0.06 } });
        add({ id: 'gx-ref-line', type: 'line', source: 'gx', 'source-layer': 'glaciers',
              paint: { 'line-color': '#d7f1ff', 'line-opacity': 0.55,
                       'line-width': ['interpolate', ['linear'], ['zoom'], 4, 0.3, 10, 0.8] } });
        add({ id: 'gx-obs-line', type: 'line', source: 'gx', 'source-layer': 'obs',
              layout: { 'line-join': 'round' },
              paint: { 'line-color': yc, 'line-opacity': 0.85,
                       'line-width': ['interpolate', ['linear'], ['zoom'], 6, 0.5, 11, 1.2] } });
        add({ id: 'gx-obs-debris', type: 'line', source: 'gx', 'source-layer': 'obs',
              paint: { 'line-color': yc, 'line-opacity': 0.7, 'line-width': 0.8,
                       'line-dasharray': [2, 2] } });
        add({ id: 'gx-obs-term', type: 'line', source: 'gx', 'source-layer': 'obs',
              layout: { 'line-cap': 'round', 'line-join': 'round' },
              paint: { 'line-color': yc, 'line-opacity': 0.95,
                       'line-width': ['interpolate', ['linear'], ['zoom'], 6, 0.8, 11, 1.8] } });
        // Wide, nearly invisible copies: a one-pixel line is not a click target.
        add({ id: 'gx-obs-hit', type: 'line', source: 'gx', 'source-layer': 'obs',
              paint: { 'line-color': '#000', 'line-opacity': 0.01, 'line-width': 10 } });
        add({ id: 'gx-selref-line', type: 'line', source: 'gx-selref',
              paint: { 'line-color': '#00e5ff', 'line-width': 1.6, 'line-dasharray': [3, 2] } });
        add({ id: 'gx-sel-casing', type: 'line', source: 'gx-sel',
              layout: { 'line-cap': 'round', 'line-join': 'round' },
              paint: { 'line-color': '#101820', 'line-opacity': 0.55, 'line-width': 4.5 } });
        add({ id: 'gx-sel-line', type: 'line', source: 'gx-sel',
              layout: { 'line-cap': 'round', 'line-join': 'round' },
              paint: { 'line-color': yc, 'line-width': 1.2 } });
        add({ id: 'gx-sel-hit', type: 'line', source: 'gx-sel',
              paint: { 'line-color': '#000', 'line-opacity': 0.01, 'line-width': 10 } });
        // Faces an admin has selected on the card, whatever the year bar says.
        add({ id: 'gx-pick', type: 'line', source: 'gx-sel', filter: ['in', ['get', 'id'], ['literal', []]],
              layout: { 'line-cap': 'round', 'line-join': 'round' },
              paint: { 'line-color': '#ffffff', 'line-opacity': 0.95, 'line-width': 4 } });
        applyAll();
        syncPick();
        // Other modules (the flow network) add their layers over these.
        (GL.layerHooks || []).forEach(function (fn) { fn(); });
    }

    function coverYear() {
        if (S.year < COVER_YEARS[0]) return null;
        return Math.min(COVER_YEARS[COVER_YEARS.length - 1], Math.floor(S.year / 2) * 2);
    }

    // ---- the epoch bit-mask tiles ---------------------------------------
    // A tile of the archive is data, not a picture: R | G<<8 | (B&3)<<16 is
    // a mask whose bit i says "ice in COVER_YEARS[i]". Decoded tiles are
    // kept, so moving the year bar recolours from memory and fetches
    // nothing.
    var _arch = {}, _maskCache = new Map(), MASK_CACHE_MAX = 400;
    function archive(kind) {
        if (!_arch[kind]) {
            _arch[kind] = new pmtiles.PMTiles(location.origin + '/overlays/glac_' + kind + '_mask.pmtiles');
        }
        return _arch[kind];
    }
    function maskTile(kind, z, x, y) {
        var key = kind + '/' + z + '/' + x + '/' + y;
        if (_maskCache.has(key)) {
            var hit = _maskCache.get(key);
            _maskCache.delete(key); _maskCache.set(key, hit);      // LRU order
            return hit;
        }
        var p = archive(kind).getZxy(z, x, y).then(function (r) {
            if (!r || !r.data) return null;
            // No premultiplication and no colour conversion: the bytes are bits.
            return createImageBitmap(new Blob([r.data], { type: 'image/png' }),
                                     { premultiplyAlpha: 'none', colorSpaceConversion: 'none' })
                .then(function (bmp) {
                    var c = new OffscreenCanvas(bmp.width, bmp.height);
                    var g = c.getContext('2d', { willReadFrequently: true });
                    g.drawImage(bmp, 0, 0);
                    var d = g.getImageData(0, 0, bmp.width, bmp.height).data;
                    var m = new Uint32Array(bmp.width * bmp.height);
                    for (var i = 0, j = 0; i < m.length; i++, j += 4) {
                        if (d[j + 3]) m[i] = d[j] | (d[j + 1] << 8) | ((d[j + 2] & 3) << 16);
                    }
                    return { w: bmp.width, h: bmp.height, m: m };
                });
        }).catch(function () { return null; });       // an archive that is not there = no data
        _maskCache.set(key, p);
        if (_maskCache.size > MASK_CACHE_MAX) _maskCache.delete(_maskCache.keys().next().value);
        return p;
    }
    // Colours as RGBA bytes.
    var C_ICE = [191, 233, 255, 170], C_DEBRIS = [138, 109, 90, 190], C_LOST = [255, 112, 67, 175];
    function coverLoader(params) {
        var u = new URL(params.url.replace('glcover://', 'https://glcover.invalid/'));
        var p = u.pathname.split('/').filter(Boolean);
        var z = +p[0], x = +p[1], y = +p[2];
        var bit = 1 << COVER_YEARS.indexOf(+u.searchParams.get('y'));
        var wantLost = u.searchParams.get('b') === '1', wantDebris = u.searchParams.get('d') === '1';
        return Promise.all([maskTile('cover', z, x, y),
                            wantDebris ? maskTile('debris', z, x, y) : null]).then(function (r) {
            var cov = r[0], deb = r[1];
            var W = cov ? cov.w : 256, H = cov ? cov.h : 256;
            var c = new OffscreenCanvas(W, H), g = c.getContext('2d');
            if (cov) {
                var img = g.createImageData(W, H), d = img.data, m = cov.m;
                var dm = (deb && deb.w === W) ? deb.m : null, earlier = bit - 1;
                for (var i = 0, j = 0; i < m.length; i++, j += 4) {
                    var v = m[i], col;
                    if (!v) continue;
                    if (v & bit) col = (dm && (dm[i] & bit)) ? C_DEBRIS : C_ICE;
                    else if (wantLost && (v & earlier)) col = C_LOST;
                    else continue;
                    d[j] = col[0]; d[j + 1] = col[1]; d[j + 2] = col[2]; d[j + 3] = col[3];
                }
                g.putImageData(img, 0, 0);
            }
            return { data: c.transferToImageBitmap() };
        });
    }
    function coverTiles() {
        return ['glcover://{z}/{x}/{y}?y=' + coverYear() + '&b=' + (S.baseOn ? 1 : 0) +
                '&d=' + (S.debrisOn ? 1 : 0)];
    }
    var _coverTilesNow = null;
    function applyCover() {
        // Not before addLayers has run: adding a source to a style that is
        // still loading throws, and this is called from init.
        if (!map.getLayer('gx-ref-fill')) return;
        var y = (S.coverOn && visible()) ? coverYear() : null;
        if (y != null) {
            if (!window.__glCoverReg) {
                window.__glCoverReg = true;
                maplibregl.addProtocol('glcover', coverLoader);
            }
            var tiles = coverTiles();
            if (!map.getSource('gcov')) {
                map.addSource('gcov', { type: 'raster', tiles: tiles, tileSize: 256,
                                        minzoom: 3, maxzoom: 11, attribution: COVER_ATTR });
                map.addLayer({ id: 'gcov', type: 'raster', source: 'gcov',
                               paint: { 'raster-resampling': 'nearest', 'raster-fade-duration': 0 } },
                             'gx-ref-fill');
                _coverTilesNow = tiles[0];
            } else if (_coverTilesNow !== tiles[0]) {
                // setTiles reloads in place: the old picture stays up until
                // the recoloured one is ready, so the bar does not blink.
                map.getSource('gcov').setTiles(tiles);
                _coverTilesNow = tiles[0];
            }
        }
        if (map.getLayer('gcov')) {
            map.setLayoutProperty('gcov', 'visibility', y != null ? 'visible' : 'none');
        }
        if (el.coverNote) {
            el.coverNote.textContent = !S.coverOn ? '' :
                (coverYear() == null ? 'no epoch before 1986' : 'showing ' + coverYear());
        }
    }
    // Which epochs a point was ice, read from the same decoded tiles.
    function coverAt(lngLat) {
        var z = Math.max(3, Math.min(11, Math.floor(map.getZoom())));
        var n = Math.pow(2, z);
        var fx = (lngLat.lng + 180) / 360 * n;
        var s = Math.sin(lngLat.lat * Math.PI / 180);
        var fy = (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * n;
        var x = Math.floor(fx), y = Math.floor(fy);
        return maskTile('cover', z, x, y).then(function (t) {
            if (!t) return 0;
            var px = Math.min(t.w - 1, Math.floor((fx - x) * t.w));
            var py = Math.min(t.h - 1, Math.floor((fy - y) * t.h));
            return t.m[py * t.w + px];
        });
    }

    function obsFilter(kinds) {
        return ['all',
            ['in', ['get', 'source'], ['literal', onSources()]],
            ['in', ['get', 'kind'], ['literal', kinds]],
            ['<=', ['coalesce', ['get', 'y0'], -9999], S.year],
            ['!=', ['coalesce', ['get', 'gid'], ''], S.selId || '']];
    }
    // Ids of the selected glacier's "current" observations: per source and
    // kind, the latest dated at or before the year bar.
    function currentIds() {
        var best = {};
        if (!S.sel) return [];
        S.sel.features.forEach(function (f) {
            var p = f.properties;
            if (!S.srcOn[p.source] || p.y0 == null || p.y0 > S.year) return;
            var k = p.source + '|' + p.kind;
            // Latest wins; among rows of one date the largest, because a
            // submission that divides the ice differently leaves fragments
            // of a neighbour matched here by overlap.
            var b = best[k];
            if (!b || (p.date_min || '') > (b.date_min || '') ||
                ((p.date_min || '') === (b.date_min || '') && (p.area_km2 || 0) > (b.area_km2 || 0))) best[k] = p;
        });
        return Object.keys(best).map(function (k) { return best[k].id; });
    }
    function applyObs() {
        if (!map.getLayer('gx-obs-line')) return;
        var vis = visible() ? 'visible' : 'none';
        map.setFilter('gx-obs-line', obsFilter(['outline']));
        map.setFilter('gx-obs-debris', obsFilter(['debris']));
        map.setFilter('gx-obs-term', obsFilter(['terminus']));
        map.setFilter('gx-obs-hit', obsFilter(['outline', 'debris', 'terminus']));
        var selF = ['all', ['in', ['get', 'source'], ['literal', onSources()]],
                    ['<=', ['coalesce', ['get', 'y0'], -9999], S.year]];
        var cur = currentIds();
        var isCur = ['in', ['get', 'id'], ['literal', cur]];
        map.setFilter('gx-sel-line', selF);
        map.setFilter('gx-sel-hit', selF);
        map.setFilter('gx-sel-casing', ['all', selF, isCur]);
        map.setPaintProperty('gx-sel-line', 'line-width', ['case', isCur, 3, 1.1]);
        map.setPaintProperty('gx-sel-line', 'line-opacity', ['case', isCur, 1, 0.75]);
        OBS_LAYERS.concat(HIT_LAYERS, ['gx-ref-fill', 'gx-ref-line', 'gx-selref-line',
                                       'gx-sel-line', 'gx-sel-casing', 'gx-pick']).forEach(function (id) {
            if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', vis);
        });
        markRows(cur);
    }
    function applyAll() {
        if (!map.getLayer('gx-ref-fill')) return;
        if (S.sel) {
            map.getSource('gx-sel').setData({ type: 'FeatureCollection', features: S.sel.features });
            map.getSource('gx-selref').setData({ type: 'Feature', properties: {},
                                                 geometry: S.sel.glacier.outline });
        } else {
            map.getSource('gx-sel').setData({ type: 'FeatureCollection', features: [] });
            map.getSource('gx-selref').setData({ type: 'FeatureCollection', features: [] });
        }
        applyObs();
        applyCover();
    }

    // ------------------------------------------------------------------
    // The year bar
    // ------------------------------------------------------------------
    function setYear(y, fromSlider) {
        S.year = Math.max(S.minYear, Math.min(NOW_YEAR, Math.round(y)));
        if (!fromSlider) el.time.value = String(S.year);
        el.year.textContent = String(S.year);
        applyObs();
        applyCover();
    }
    function setRange() {
        var lo = DEF_MIN_YEAR;
        if (S.sel) {
            S.sel.features.forEach(function (f) {
                var y = f.properties.y0;
                if (y != null && y < lo) lo = y;
            });
            lo = Math.floor((lo - 1) / 10) * 10;
        }
        S.minYear = lo;
        el.time.min = String(lo); el.time.max = String(NOW_YEAR);
        el.lo.textContent = String(lo); el.hi.textContent = String(NOW_YEAR);
        if (S.year < lo) S.year = lo;
        el.time.value = String(S.year);
        drawTicks();
    }
    // One tick per year the selected glacier has an observation.
    function drawTicks() {
        el.ticks.innerHTML = '';
        if (!S.sel) return;
        var seen = {}, span = (NOW_YEAR - S.minYear) || 1;
        S.sel.features.forEach(function (f) {
            var y = f.properties.y0;
            if (y == null || seen[y]) return;
            seen[y] = true;
            var t = document.createElement('i');
            t.style.left = (((y - S.minYear) / span) * 100) + '%';
            t.style.background = window.LSColors.glacierYearCss(y);
            t.title = String(y);
            el.ticks.appendChild(t);
        });
    }
    // The year bar: closed by default, opened by its button; neither shows
    // on the Flow tab, which has its own bar.
    function showBar() {
        var here = GL.tab() !== 'flow';
        el.bar.style.display = here && S.barOpen ? '' : 'none';
        el.open.style.display = here && !S.barOpen ? '' : 'none';
    }
    function play(on) {
        S.playing = on;
        el.play.textContent = on ? '❚❚' : '▶';
        if (S.playTimer) { clearInterval(S.playTimer); S.playTimer = null; }
        if (!on) { GL.writeHash(); return; }
        if (S.year >= NOW_YEAR) setYear(Math.max(S.minYear, S.sel ? S.minYear : 1986));
        S.playTimer = setInterval(function () {
            if (S.year >= NOW_YEAR) { play(false); return; }
            setYear(S.year + 1);
        }, 160);
    }

    // ------------------------------------------------------------------
    // Search and selection
    // ------------------------------------------------------------------
    var _searchT = null, _searchSeq = 0;
    function search() {
        var q = el.search.value.trim();
        if (q.length < 2) { el.results.innerHTML = ''; return; }
        var seq = ++_searchSeq;
        fetch('/glaciers/api/search/?q=' + encodeURIComponent(q))
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (seq !== _searchSeq) return;
                el.results.innerHTML = '';
                if (!d.results.length) {
                    el.results.innerHTML = '<div class="rt-none">No glacier by that name. ' +
                        'Only ' + (S.identity ? S.identity.n_named : 'a few hundred') +
                        ' of the ' + (S.identity ? S.identity.n.toLocaleString() : '') +
                        ' RGI glaciers are named; click any glacier on the map.</div>';
                    return;
                }
                d.results.forEach(function (r) {
                    var b = document.createElement('button');
                    b.type = 'button'; b.className = 'rt-result';
                    b.innerHTML = '<span>' + esc(r.name || r.id) + '</span><small>' +
                        fmtArea(r.area_km2) + (r.n_obs ? ' · ' + r.n_obs + ' obs' : '') + '</small>';
                    b.addEventListener('click', function () {
                        el.results.innerHTML = ''; el.search.value = '';
                        select(r.id, true);
                    });
                    el.results.appendChild(b);
                });
            }).catch(function () {});
    }

    function select(id, fit, reopenRejected) {
        if (!id) { clearSel(); return; }
        if (id !== S.selId) { S.pick = {}; S.lastRej = null; }
        S.selId = id;
        el.card.innerHTML = '<div class="rt-none">Loading…</div>';
        fetch('/glaciers/api/glacier/' + encodeURIComponent(id) + '/?v=' + S.stamp)
            .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
            .then(function (d) {
                if (S.selId !== id) return;
                S.sel = d;
                setRange();
                renderCard();
                if (reopenRejected && $('rt-rejected')) $('rt-rejected').open = true;
                applyAll();
                if (!fit) dropIfAway();
                if (fit) {
                    var b = d.glacier.bbox;
                    map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 60, maxZoom: 13, duration: 600 });
                }
                GL.writeHash();
            })
            .catch(function () {
                if (S.selId !== id) return;
                S.selId = null; S.sel = null;
                el.card.innerHTML = '<div class="rt-none">That glacier could not be loaded.</div>';
                applyAll();
            });
    }
    // A selection is dropped once its glacier is wholly out of view (Hig,
    // 2026-11-23): a stored view or a long pan left Columbia selected, and its
    // card and g= in the link, while the map was at Endicott.
    function dropIfAway() {
        if (!S.sel) return;
        var b = S.sel.glacier.bbox, v = map.getBounds();
        if (b[2] < v.getWest() || b[0] > v.getEast() || b[3] < v.getSouth() || b[1] > v.getNorth()) clearSel();
    }
    function clearSel() {
        S.selId = null; S.sel = null; S.pick = {}; S.lastRej = null;
        setRange();
        renderCard();
        applyAll();
        GL.writeHash();
    }

    var TERM_TYPE = { 0: 'land-terminating', 1: 'marine-terminating', 2: 'lake-terminating',
                      3: 'dry calving', 4: 'regenerated', 5: 'shelf-terminating' };

    function renderCard() {
        if (!S.sel) {
            el.card.innerHTML = '<div class="rt-none">Click a glacier on the map, or search by name, ' +
                'to see every dated outline and terminus position matched to it.</div>';
            return;
        }
        var g = S.sel.glacier, feats = S.sel.features;
        var h = '<div class="rt-card-head"><div><b>' + esc(g.name || 'Unnamed glacier') + '</b>' +
            '<div class="rt-sub">' + fmtArea(g.area_km2) +
            (g.src_date ? ' in ' + g.src_date.slice(0, 4) : '') +
            (TERM_TYPE[g.term_type] ? ' · ' + TERM_TYPE[g.term_type] : '') + '</div></div>' +
            '<span><button type="button" id="rt-zoom" title="Zoom to this glacier">⤢</button>' +
            '<button type="button" id="rt-clear" title="Clear the selection">×</button></span></div>' +
            '<div class="rt-ids">' + esc(g.id) + ' · GLIMS ' + esc(g.glims_id) + '</div>';
        if (!feats.length) {
            h += '<div class="rt-none">No dated observation from the loaded sources is matched ' +
                'to this glacier. The statewide ice cover (1986–2020) still applies.</div>';
        }
        var bySrc = {};
        feats.forEach(function (f) { (bySrc[f.properties.source] = bySrc[f.properties.source] || []).push(f); });
        S.sources.forEach(function (s) {
            var L = bySrc[s.id];
            if (!L) return;
            var y0 = null, y1 = null;
            L.forEach(function (f) {
                var y = f.properties.y0;
                if (y == null) return;
                if (y0 == null || y < y0) y0 = y;
                if (y1 == null || y > y1) y1 = y;
            });
            var pickable = GL.CFG.faceAdmin && s.geom === 'line';
            h += '<details class="rt-grp"' + (L.length <= 40 ? ' open' : '') + '><summary>' + esc(s.short) +
                ' <small>' + L.length + (s.geom === 'line' ? ' terminus positions' : ' outlines') +
                (y0 != null ? ', ' + (y0 === y1 ? y0 : y0 + '–' + y1) : '') + '</small></summary>' +
                (pickable ? '<div class="rt-pickbar"><span class="rt-sub">Click faces to select them</span>' +
                    '<button type="button" class="rt-rejsel" data-src="' + esc(s.id) + '" disabled>Reject selected</button></div>' : '') +
                '<div class="rt-rows">';
            L.forEach(function (f) {
                var p = f.properties;
                var extra = p.source === 's1auto' && p.props && p.props.score != null ? 'score ' + (+p.props.score).toFixed(2) : '';
                h += '<button type="button" class="rt-row' + (pickable ? ' pickable' : '') + (S.pick[p.id] ? ' pick' : '') +
                    '" data-id="' + p.id + '" data-src="' + esc(p.source) + '" data-y="' +
                    (p.y0 == null ? '' : p.y0) + '" title="' + esc(p.label) + '">' +
                    '<i style="background:' + window.LSColors.glacierYearCss(p.y0) + '"></i>' +
                    '<span>' + esc(fmtDate(p)) + '</span>' +
                    '<small>' + (p.kind === 'debris' ? 'debris cover ' : '') + extra +
                    (p.area_km2 != null ? fmtArea(p.area_km2) : '') + '</small></button>';
            });
            h += '</div></details>';
        });
        if (GL.CFG.faceAdmin && S.lastRej) {
            h += '<div class="rt-rej">Rejected ' + S.lastRej.length + ' face' + (S.lastRej.length === 1 ? '' : 's') +
                '. <button type="button" id="rt-undo">Undo</button></div>';
        }
        el.card.innerHTML = h;
        $('rt-clear').addEventListener('click', clearSel);
        if ($('rt-undo')) $('rt-undo').addEventListener('click', function () {
            var ids = S.lastRej; this.disabled = true;
            rejectFace(ids, true).then(function () { S.lastRej = null; renderCard(); });
        });
        $('rt-zoom').addEventListener('click', function () {
            var b = g.bbox;
            map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 60, maxZoom: 13, duration: 600 });
        });
        Array.prototype.forEach.call(el.card.querySelectorAll('.rt-row'), function (b) {
            b.addEventListener('click', function () {
                if (b.classList.contains('pickable')) {          // admin: select, not the year bar
                    var id = +b.getAttribute('data-id');
                    if (S.pick[id]) delete S.pick[id]; else S.pick[id] = true;
                    b.classList.toggle('pick', !!S.pick[id]);
                    syncPick();
                    return;
                }
                var y = b.getAttribute('data-y');
                if (y !== '') { setYear(+y); GL.writeHash(); }
            });
        });
        Array.prototype.forEach.call(el.card.querySelectorAll('.rt-rejsel'), function (b) {
            b.addEventListener('click', function () {
                var src = b.getAttribute('data-src');
                var ids = Array.prototype.map.call(el.card.querySelectorAll('.rt-row.pick[data-src="' + src + '"]'),
                                                   function (r) { return +r.getAttribute('data-id'); });
                if (!ids.length) return;
                b.disabled = true; b.textContent = 'Rejecting…';
                rejectFace(ids, false).then(function () {
                    ids.forEach(function (i) { delete S.pick[i]; });
                    S.lastRej = ids;
                }).catch(function (e) { b.disabled = false; b.textContent = e.message; });
            });
        });
        syncPick();
        if (GL.CFG.faceAdmin) {
            var rd = document.createElement('details');
            rd.className = 'rt-grp rt-rejected';
            rd.id = 'rt-rejected';
            rd.innerHTML = '<summary>Rejected faces <small>(admin)</small></summary><div class="rt-rows"><div class="rt-none">Loading…</div></div>';
            el.card.appendChild(rd);
            rd.addEventListener('toggle', function () {
                if (!rd.open) return;
                var box = rd.querySelector('.rt-rows');
                fetch('/glaciers/api/face/rejected/?glacier=' + encodeURIComponent(g.id)).then(function (r) { return r.json(); })
                    .then(function (d) {
                        if (!d.faces || !d.faces.length) { box.innerHTML = '<div class="rt-none">None.</div>'; return; }
                        S.rpick = {};
                        box.innerHTML = '<div class="rt-pickbar"><span class="rt-sub">Click faces to select them</span>' +
                            '<button type="button" id="rt-restsel" disabled>Restore selected</button></div>' +
                            d.faces.map(function (f) {
                                var sr = srcById(f.source);
                                return '<button type="button" class="rt-row pickable" data-rid="' + f.id + '" title="' + esc(f.label) + '">' +
                                    '<span>' + esc(fmtDate({ date_min: f.date, date_max: f.date_max })) + ' · ' + esc(sr ? sr.short : f.source) +
                                    '</span><small title="rejected ' + esc(f.at) + '">' + esc(f.by) + '</small></button>';
                            }).join('');
                        var rb = $('rt-restsel');
                        function syncR() {
                            var n = Object.keys(S.rpick).length;
                            rb.disabled = !n; rb.textContent = n ? 'Restore selected (' + n + ')' : 'Restore selected';
                        }
                        Array.prototype.forEach.call(box.querySelectorAll('[data-rid]'), function (b) {
                            b.addEventListener('click', function () {
                                var id = +b.getAttribute('data-rid');
                                if (S.rpick[id]) delete S.rpick[id]; else S.rpick[id] = true;
                                b.classList.toggle('pick', !!S.rpick[id]); syncR();
                            });
                        });
                        rb.addEventListener('click', function () {
                            var ids = Object.keys(S.rpick).map(Number);
                            if (!ids.length) return;
                            rb.disabled = true; rb.textContent = 'Restoring…';
                            rejectFace(ids, true).then(function () { S.rpick = {}; S.lastRej = null; })
                                .catch(function (e) { rb.disabled = false; rb.textContent = e.message; });
                        });
                    }).catch(function () { box.innerHTML = '<div class="rt-none">Could not load.</div>'; });
            });
        }
        markRows(currentIds());
        (GL.cardHooks || []).forEach(function (fn) { fn(el.card, S.sel); });
    }
    // In the list: heavy = the one drawn heavy on the map, dim = after the bar.
    function markRows(cur) {
        if (!el.card) return;
        var set = {};
        (cur || []).forEach(function (id) { set[id] = true; });
        Array.prototype.forEach.call(el.card.querySelectorAll('.rt-row'), function (b) {
            var y = b.getAttribute('data-y');
            b.classList.toggle('cur', !!set[b.getAttribute('data-id')]);
            b.classList.toggle('later', y !== '' && +y > S.year);
        });
    }

    // ------------------------------------------------------------------
    // The source list
    // ------------------------------------------------------------------
    function doiLink(doi) {
        return '<a target="_blank" rel="noopener" href="https://doi.org/' + esc(doi) + '">doi:' + esc(doi) + '</a>';
    }
    // Sources that are face LINES (terminus positions), as against outlines.
    function faceSources() { return S.sources.filter(function (s) { return s.geom === 'line'; }); }
    // Every face switch at once (Hig, 2026-11-23): our traced faces and each
    // terminus source; outline sources are not faces and stay as they are.
    function setAllFaces(on) {
        faceSources().forEach(function (s) { S.srcOn[s.id] = on; });
        if (window.GLNetwork && window.GLNetwork.setTraced) window.GLNetwork.setTraced(on);
        renderSources(); applyObs(); GL.writeHash(); facesChanged();
    }
    function facesChanged() {
        if (window.GLNetwork && window.GLNetwork.redraw) window.GLNetwork.redraw();
    }
    function renderSources() {
        var traced = !window.GLNetwork || !window.GLNetwork.traced || window.GLNetwork.traced();
        var h = '<div class="rt-faces"><b>Ice faces</b>' +
            '<button type="button" data-fa="1" title="Show every face: traced here and from every terminus source, on the map and the plot">all on</button>' +
            '<button type="button" data-fa="0" title="Hide every face: traced here and from every terminus source, on the map and the plot">all off</button>' +
            '<label title="Faces traced on this page (the network editor)"><input type="checkbox" id="rt-traced"' +
            (traced ? ' checked' : '') + '><i style="background:#ff4081"></i>traced here</label></div>';
        S.sources.forEach(function (s) {
            h += '<div class="rt-src"><label><input type="checkbox" data-src="' + esc(s.id) + '"' +
                (S.srcOn[s.id] ? ' checked' : '') + '><span>' + esc(s.short) + '</span>' +
                '<small>' + (s.n ? s.n.toLocaleString() + (s.geom === 'line' ? ' lines' : ' outlines') +
                    (s.year_min ? ', ' + s.year_min + '–' + s.year_max : '') : 'not loaded') + '</small></label>' +
                '<details><summary>about</summary><p>' + esc(s.what) + '</p><p>' + esc(s.cite) + ' ' +
                (s.doi ? doiLink(s.doi) : '<a target="_blank" rel="noopener" href="' + esc(s.url) + '">source</a>') +
                '</p></details></div>';
        });
        h += '<div class="rt-src"><label><input type="checkbox" id="rt-cover"' + (S.coverOn ? ' checked' : '') +
            '><span>Statewide ice cover</span><small id="rt-cover-note"></small></label>' +
            '<div class="rt-subopts"><label><input type="checkbox" id="rt-base"' + (S.baseOn ? ' checked' : '') +
            '><i style="background:#ff7043"></i>ice lost since 1986</label>' +
            '<label><input type="checkbox" id="rt-debris"' + (S.debrisOn ? ' checked' : '') +
            '><i style="background:#8a6d5a"></i>debris-covered ice</label></div>' +
            '<details><summary>about</summary><p>Ice cover mapped automatically from Landsat ' +
            'composites every two years, 1986–2020, 30 m. Not hand-checked: it misses some ' +
            'debris-covered ice and is weakest in the Brooks Range.</p><p>Roberts-Pierel, B. M., ' +
            'Kirchner, P. B., Kilbride, J. B. and Kennedy, R. E. (2022). Glacier Covered Area for ' +
            'the State of Alaska, 1985–2020. NSIDC. ' + doiLink('10.7265/8esq-w553') + '</p></details></div>';
        if (S.identity) {
            h += '<p class="rt-cite">Glaciers are those of the ' + esc(S.identity.title) + ' (' +
                S.identity.n.toLocaleString() + '). ' + doiLink(S.identity.doi) + '</p>';
        }
        el.sources.innerHTML = h;
        el.coverNote = $('rt-cover-note');
        Array.prototype.forEach.call(el.sources.querySelectorAll('input[data-src]'), function (cb) {
            cb.addEventListener('change', function () {
                S.srcOn[cb.getAttribute('data-src')] = cb.checked;
                applyObs(); GL.writeHash(); facesChanged();
            });
        });
        Array.prototype.forEach.call(el.sources.querySelectorAll('[data-fa]'), function (b) {
            b.addEventListener('click', function () { setAllFaces(b.getAttribute('data-fa') === '1'); });
        });
        $('rt-traced').addEventListener('change', function () {
            if (window.GLNetwork && window.GLNetwork.setTraced) window.GLNetwork.setTraced(this.checked);
        });
        $('rt-cover').addEventListener('change', function () { S.coverOn = this.checked; applyCover(); GL.writeHash(); });
        $('rt-base').addEventListener('change', function () { S.baseOn = this.checked; applyCover(); });
        $('rt-debris').addEventListener('change', function () { S.debrisOn = this.checked; applyCover(); });
        applyCover();
    }
    function renderLegend() {
        var st = window.LSColors.GLACIER_YEAR_STOPS, n = st.length - 1;
        var grad = st.map(function (s, i) { return s[1] + ' ' + (i / n * 100).toFixed(1) + '%'; }).join(', ');
        var h = '<div class="rt-ramp" style="background:linear-gradient(to right,' + grad + ')"></div><div class="rt-ramp-t">';
        st.forEach(function (s, i) {
            if ((i % 2 && i !== n) || i === n - 1) return;
            var shift = i === 0 ? '0' : (i === n ? '-100%' : '-50%');
            h += '<span style="left:' + (i / n * 100).toFixed(1) + '%;transform:translateX(' + shift + ')">' + s[0] + '</span>';
        });
        el.legend.innerHTML = h + '</div><div class="rt-sub">Line colour = year of the observation. ' +
            'Solid = glacier outline, dashed = debris cover, heavier = terminus position.</div>';
    }

    // ------------------------------------------------------------------
    // Clicks (through the shared dispatcher)
    // ------------------------------------------------------------------
    function propsTable(props) {
        var h = '<table class="rt-props">';
        Object.keys(props || {}).forEach(function (k) {
            var v = props[k];
            if (v == null || v === '' || v === 'None') return;
            h += '<tr><th>' + esc(k) + '</th><td>' + esc(v) + '</td></tr>';
        });
        return h + '</table>';
    }
    function obsPopup(f, e) {
        var p = f.properties, s = srcById(p.source) || { short: p.source, doi: '' };
        var pop = new maplibregl.Popup({ maxWidth: '340px' }).setLngLat(e.lngLat)
            .setHTML('<div class="rt-pop"><b>' + esc(s.short) + '</b><div class="rt-none">Loading…</div></div>')
            .addTo(map);
        window.LSTools.popup.show(pop);
        fetch('/glaciers/api/obs/' + encodeURIComponent(p.id) + '/?v=' + S.stamp)
            .then(function (r) { return r.json(); })
            .then(function (o) {
                var h = '<div class="rt-pop"><b>' + esc(s.short) + '</b> · ' +
                    esc(o.kind === 'terminus' ? 'terminus position' : o.kind === 'debris' ? 'debris cover' : 'outline') +
                    '<div>' + esc(fmtDate(o)) + (o.area_km2 != null ? ' · ' + fmtArea(o.area_km2) : '') + '</div>';
                if (o.glacier_id && o.glacier_id !== S.selId) {
                    h += '<div><a href="#" id="rt-pop-sel">Select ' + esc(o.glacier_name || 'this glacier') + '</a></div>';
                } else if (!o.glacier_id) {
                    h += '<div class="rt-sub">Not matched to a glacier.</div>';
                }
                if (o.source === 's1auto' && o.props && o.props.video_id) {
                    h += '<div><a href="#" id="rt-pop-vid">▶ Play the timelapse it was found in</a></div>';
                }
                h += '<div class="rt-sub">' + (o.source === 's1auto' ? 'As detected:' : 'As published:') + '</div>' +
                    propsTable(o.props) +
                    (s.doi ? '<div class="rt-sub">' + doiLink(s.doi) + '</div>' : '');
                if (GL.CFG.faceAdmin && o.kind === 'terminus') {
                    h += '<div class="rt-rej"><button type="button" id="rt-pop-rej" title="Take this face off the map and ' +
                        'the plots. It is kept, and can be restored from the glacier\'s card.">Reject this face</button></div>';
                }
                pop.setHTML(h + '</div>');
                var rj = document.getElementById('rt-pop-rej');
                if (rj) rj.addEventListener('click', function () {
                    rj.disabled = true;
                    rejectFace(o.id, false).then(function () {
                        rj.parentNode.innerHTML = 'Rejected. <button type="button" id="rt-pop-undo">Undo</button>';
                        document.getElementById('rt-pop-undo').addEventListener('click', function () {
                            this.disabled = true;
                            rejectFace(o.id, true).then(function () { window.LSTools.popup.close(); });
                        });
                    }).catch(function (e) { rj.disabled = false; rj.textContent = e.message; });
                });
                var pv = document.getElementById('rt-pop-vid');
                if (pv) pv.addEventListener('click', function (ev) {
                    ev.preventDefault();
                    if (window.GLVideos) window.GLVideos.open(+o.props.video_id, e.lngLat);
                });
                var a = document.getElementById('rt-pop-sel');
                if (a) a.addEventListener('click', function (ev) {
                    ev.preventDefault(); window.LSTools.popup.close(); select(o.glacier_id, false);
                });
            }).catch(function () {});
    }
    // Reject (undo = restore) one face; everything that shows faces is
    // refreshed: tiles (new stamp), the selected glacier, the open plot.
    function rejectFace(id, undo) {
        var ids = (Array.isArray(id) ? id : [id]).map(Number);
        return fetch('/glaciers/api/face/reject/', {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': GL.CFG.csrf },
            body: JSON.stringify({ ids: ids, undo: !!undo })
        }).then(function (r) {
            return r.json().catch(function () { throw new Error('HTTP ' + r.status); }).then(function (d) {
                if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
                afterReject(d);
                return d;
            });
        });
    }
    function afterReject(d) {
        S.stamp = d.stamp;
        var src = map.getSource('gx');
        if (src && src.setTiles) src.setTiles([location.origin + '/glaciers/tiles/{z}/{x}/{y}.pbf?v=' + S.stamp]);
        fetch('/glaciers/api/sources/').then(function (r) { return r.json(); }).then(function (x) {
            x.sources.forEach(function (n) {
                S.sources.forEach(function (o) { if (o.id === n.id) { o.n = n.n; o.year_min = n.year_min; o.year_max = n.year_max; } });
            });
            renderSources();
        }).catch(function () {});
        var reopen = !!($('rt-rejected') && $('rt-rejected').open);
        if (S.selId) select(S.selId, false, reopen);
        if (window.GLNetwork && window.GLNetwork.refresh) window.GLNetwork.refresh();
    }
    // The selected card rows: the button's count, and a white casing on the
    // map whatever the year bar says (the faces may be later than it).
    function syncPick() {
        if (!el.card) return;
        Array.prototype.forEach.call(el.card.querySelectorAll('.rt-rejsel'), function (b) {
            var n = el.card.querySelectorAll('.rt-row.pick[data-src="' + b.getAttribute('data-src') + '"]').length;
            b.disabled = !n; b.textContent = n ? 'Reject selected (' + n + ')' : 'Reject selected';
        });
        if (map.getLayer('gx-pick')) {
            map.setFilter('gx-pick', ['in', ['get', 'id'], ['literal', Object.keys(S.pick).map(Number)]]);
        }
    }
    function registerClicks() {
        var T = window.LSTools;
        T.clicks.register({
            id: 'gl-glacier', kind: 'record', layers: ['gx-ref-fill'], priority: 1,
            handler: function (f) {
                if (f && f.properties.gid && f.properties.gid !== S.selId) select(f.properties.gid, false);
            }
        });
        T.clicks.register({
            id: 'gl-obs', kind: 'reference', layers: HIT_LAYERS, priority: 10,
            handler: function (f, e) { if (f) obsPopup(f, e); }
        });
        // The ice cover has no features to hit: a raster reference, in the
        // contest whenever it is showing, that speaks only where there is ice.
        T.clicks.register({
            id: 'gl-cover', kind: 'reference', priority: 1,
            test: function () { return S.coverOn && visible() && coverYear() != null; },
            handler: function (f, e) {
                coverAt(e.lngLat).then(function (m) {
                    if (!m) return;
                    var yrs = COVER_YEARS.filter(function (y, i) { return m & (1 << i); });
                    // runs of consecutive epochs, e.g. "1986–2004, 2010"
                    var runs = [], a0 = null, prev = null;
                    yrs.forEach(function (y) {
                        if (prev != null && y === prev + 2) { prev = y; return; }
                        if (a0 != null) runs.push(a0 === prev ? '' + a0 : a0 + '–' + prev);
                        a0 = prev = y;
                    });
                    if (a0 != null) runs.push(a0 === prev ? '' + a0 : a0 + '–' + prev);
                    T.popup.show(new maplibregl.Popup({ maxWidth: '300px' }).setLngLat(e.lngLat).setHTML(
                        '<div class="rt-pop"><b>Ice cover at this point</b><div>mapped as ice in ' +
                        esc(runs.join(', ')) + '</div><div class="rt-sub">' + yrs.length + ' of ' +
                        COVER_YEARS.length + ' epochs, 30 m cells · ' + doiLink('10.7265/8esq-w553') +
                        '</div></div>').addTo(map));
                });
            }
        });
        ['gx-ref-fill'].concat(HIT_LAYERS).forEach(function (id) {
            map.on('mouseenter', id, function () { T.cursor.set('hover', 'pointer'); });
            map.on('mouseleave', id, function () { T.cursor.clear('hover'); });
        });
    }

    // ------------------------------------------------------------------
    function hashExtras() {
        var on = onSources();
        return {
            g: S.selId || null,
            yr: S.year !== NOW_YEAR ? String(S.year) : null,
            // before the registry has arrived, keep what the link said
            src: !S.sources.length ? (GL.hash.extras.src || null)
                : (on.length !== S.sources.length ? (on.join(',') || 'none') : null),
            gc: S.coverOn ? null : '0'
        };
    }

    function init(gl) {
        GL = gl; map = gl.map;
        ['search', 'results', 'card', 'sources', 'legend', 'time', 'year', 'play', 'ticks', 'lo', 'hi', 'bar', 'open', 'close']
            .forEach(function (k) { el[k] = $('rt-' + k); });
        var X = GL.hash.extras || {};
        if (X.yr != null && isFinite(+X.yr)) { S.year = +X.yr; S.barOpen = true; }   // a link to a past year opens the bar
        if (X.gc === '0') S.coverOn = false;
        var srcWanted = X.src != null ? X.src.split(',') : null;
        GL.addHashExtras(hashExtras, ['g', 'yr', 'src', 'gc']);

        el.search.addEventListener('input', function () {
            clearTimeout(_searchT); _searchT = setTimeout(search, 220);
        });
        el.time.addEventListener('input', function () { setYear(+el.time.value, true); });
        el.time.addEventListener('change', function () { GL.writeHash(); });
        el.play.addEventListener('click', function () { play(!S.playing); });
        el.open.addEventListener('click', function () { S.barOpen = true; showBar(); });
        // Closing goes back to the present: a past year must not stay applied
        // to the cover and outlines with nothing on screen saying so.
        el.close.addEventListener('click', function () {
            S.barOpen = false; if (S.playing) play(false);
            setYear(NOW_YEAR); showBar(); GL.writeHash();
        });
        GL.onTab(function (name) {
            showBar();
            if (name === 'flow' && S.playing) play(false);
            if (map.getLayer('gx-ref-fill')) { applyObs(); applyCover(); }
        });
        setRange();
        setYear(S.year);
        renderCard();
        renderLegend();
        registerClicks();
        map.on('moveend', function () { if (visible()) dropIfAway(); });

        fetch('/glaciers/api/sources/').then(function (r) { return r.json(); }).then(function (d) {
            S.sources = d.sources; S.identity = d.identity; S.stamp = d.stamp;
            d.sources.forEach(function (s) {
                S.srcOn[s.id] = srcWanted ? srcWanted.indexOf(s.id) >= 0 : true;
            });
            renderSources();
            function go() { addLayers(); if (X.g) select(X.g, !GL.hadView); }
            // isStyleLoaded() is false while tiles are still arriving even
            // after 'load' has fired, so wait on whichever comes first.
            if (map.isStyleLoaded()) go();
            else {
                var done = false, once = function () { if (!done) { done = true; go(); } };
                map.once('load', once); map.once('idle', once);
            }
            map.on('style.load', function () { if (S.sources.length) addLayers(); });
        }).catch(function () {
            el.sources.innerHTML = '<div class="rt-none">The glacier sources could not be loaded.</div>';
        });
    }

    return {
        init: init,
        setYear: function (y) { setYear(y); GL.writeHash(); },
        rerenderCard: function () { if (el.card) renderCard(); },
        // Is this source switched on (true until the registry has loaded)?
        srcOn: function (id) { return !S.sources.length || !!S.srcOn[id]; },
        rejectFace: rejectFace,
        fmtDate: fmtDate,
        srcShort: function (id) { var s = srcById(id); return s ? s.short : id; },
        renderSources: function () { if (S.sources.length) renderSources(); }
    };
})();
