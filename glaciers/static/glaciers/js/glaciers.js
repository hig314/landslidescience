/* /glaciers — page shell: the map, basemap, raster overlays, tabs and URL
 * hash for the glacier browser.
 *
 * Until 2026-11-16 this file WAS the page: a Lagrangian flow viewer with a
 * small floating panel. The page is now organised around glacier identity
 * (RGI 7.0, glaciers/sources.py) with one tab per topic, and the code is
 * split to match:
 *
 *   glaciers.js    this file. Map, basemap, the shared raster overlays
 *                  (ls_overlays.js) and IceBridge points, tabs, hash, and
 *                  the small API (window.GL) the topic modules use.
 *   gl_retreat.js  Retreat tab: glacier search and selection, the mirrored
 *                  extent sources as layers, the statewide ice-cover time
 *                  layer, the year bar.
 *   gl_network.js  The flow network (centrelines with feeders), our face
 *                  lines, the editing session and the distance plot.
 *   gl_tracers.js  Flow tab: the tracer engine, loaded on first use.
 *
 * A deliberately small sibling of the inventory map: basemaps come from the
 * shared LSBasemaps module, the ITS_LIVE/Hugonnet overlays from the shared
 * LSOverlays module (single source of truth with the inventory), clicks and
 * the popup slot from LSTools, the URL grammar from LSHash.
 */
(function () {
    'use strict';

    var CFG = window.GL_CONFIG || {};

    // ---------------------------------------------------------------------
    // Map + shared layers
    // ---------------------------------------------------------------------
    var DEFAULT_BASEMAP = 'esri-img';
    var basemaps = window.LSBasemaps.DEFAULTS.slice();
    function findBasemap(id) {
        for (var i = 0; i < basemaps.length; i++) if (basemaps[i].id === id) return basemaps[i];
        return null;
    }
    if (window.LSBasemaps.registerProtocols) window.LSBasemaps.registerProtocols();

    // URL hash (shared codec, same grammar as /inventory/): view + basemap +
    // overlays restore on load. This app's extras: tab, and per module
    // site / t (flow), g / src / yr / gc (retreat).
    var _hash = window.LSHash ? window.LSHash.parse(location.hash) : { extras: {} };
    var _currentBasemap = (_hash.base && findBasemap(_hash.base)) ? _hash.base : DEFAULT_BASEMAP;
    var _hashHadView = _hash.zoom != null;

    var map = new maplibregl.Map({
        container: 'gl-map',
        style: window.LSBasemaps.styleFor(findBasemap(_currentBasemap)),
        // No view in the link: southern Alaska, where most of the ice is.
        center: _hashHadView ? [_hash.lon, _hash.lat] : [-146.5, 60.6],
        zoom: _hashHadView ? _hash.zoom : 5.4,
        transformRequest: window.LSBasemaps.transformRequest,
        attributionControl: { compact: true }
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    // Globe projection — parity with the inventory map (re-assert per style).
    map.on('style.load', function () {
        if (typeof map.setProjection === 'function') {
            try { map.setProjection({ type: 'globe' }); } catch (e) {}
        }
    });

    // Overlays: the shared glacier set, one simple row each (checkbox +
    // opacity + the thinning variant toggle). No wiper here — the inventory
    // map is the comparison tool.
    // Our robust fit of the RAW image pairs (tools/fit_pair_rasters.py),
    // tiled with the same ramps as the ITS_LIVE layers above so any visible
    // difference is a data difference. Defined here rather than in the
    // shared module because it is glaciers-only and experimental — the
    // inventory map has no business carrying it.
    var EXPERIMENTAL = !!CFG.experimental;
    var FIT_TILE_V = '1';
    function _fitSourceDef(key) {
        return {
            type: 'raster',
            tiles: ['/tiles/glacierfit/' + key + '/{z}/{x}/{y}.png?v=' + FIT_TILE_V],
            tileSize: 256, minzoom: 6, maxzoom: 13,
            attribution: 'Robust fit of ITS_LIVE image pairs (experimental) · NASA MEaSUREs'
        };
    }
    var FIT_OVERLAYS = [
        { id: 'fit-v0', layerId: 'ov-fit-v0', sourceId: 'ov-fit-v0-src',
          label: 'Fitted speed (ours)', sub: 'robust fit of raw pairs @2024.5 · Columbia box only',
          sourceDef: function () { return _fitSourceDef('v0'); }, defOpacity: 0.9 },
        { id: 'fit-amp', layerId: 'ov-fit-amp', sourceId: 'ov-fit-amp-src',
          label: 'Fitted seasonal amplitude (ours)', sub: 'same ramp as ITS_LIVE v_amp · Columbia box only',
          sourceDef: function () { return _fitSourceDef('amp'); }, defOpacity: 0.9 },
        { id: 'fit-trend', layerId: 'ov-fit-trend', sourceId: 'ov-fit-trend-src',
          label: 'Fitted speed trend (ours)', sub: 'same ramp as ITS_LIVE dv/dt · Columbia box only',
          sourceDef: function () { return _fitSourceDef('trend'); }, defOpacity: 0.9 },
    ];

    // IceBridge UAF HF / ARES radar bed-elevation picks (tools/build_icebridge_points.py
    // -> data/glaciers/icebridge_<slug>.json, point GeoJSON, NOT raster). General
    // reference data, not experimental scaffolding — ships regardless of EXPERIMENTAL.
    // Color-by is shared across both instrument layers (see the "IceBridge color"
    // select built below) rather than per-row, so UAF HF and ARES stay visually
    // comparable; instrument identity is already carried by which toggle is on.
    var ICEBRIDGE_DATA_V = '1';
    function _icebridgeSourceDef(slug) {
        return { type: 'geojson', data: CFG.dataBase + 'icebridge_' + slug + '.json?v=' + ICEBRIDGE_DATA_V };
    }
    var ICEBRIDGE_OVERLAYS = [
        { id: 'ib-uaf-hf', layerId: 'ov-ib-uaf-hf', sourceId: 'ov-ib-uaf-hf-src', type: 'circle',
          label: 'IceBridge UAF HF radar', sub: 'bed elevation picks, 2013–2016',
          sourceDef: function () { return _icebridgeSourceDef('uaf_hf'); }, defOpacity: 0.9 },
        { id: 'ib-ares', layerId: 'ov-ib-ares', sourceId: 'ov-ib-ares-src', type: 'circle',
          label: 'IceBridge ARES radar', sub: 'bed elevation picks, 2016–2021',
          sourceDef: function () { return _icebridgeSourceDef('ares'); }, defOpacity: 0.9 },
    ];

    // The fitted-pair overlays are part of the /pairs/ experimental stack and
    // ship only where that data exists.
    var OVERLAYS = window.LSOverlays.glacierOverlays({})
        .concat(EXPERIMENTAL ? FIT_OVERLAYS : [])
        .concat(ICEBRIDGE_OVERLAYS);
    var _ovState = {};
    OVERLAYS.forEach(function (ov) {
        var s = _hash.ov && _hash.ov[ov.id];
        _ovState[ov.id] = { on: !!(s && s.left),
                            op: (s && s.opLeft != null) ? s.opLeft : ov.defOpacity };
        if (ov.variant && s) ov.variant.set(!!s.smooth);
    });

    // Color-by ramps for the IceBridge points. Thickness is a pure magnitude (no
    // zero-crossing) -> one hue, light->dark. Bed height is a hard threshold, not
    // a smooth diverging scale: below 0 m (flotation-relevant) vs. above 0 m is a
    // real physical boundary (sea/lake level), so it renders as an ABRUPT seam at
    // 0 — blue below, green above — each arm still gradated by magnitude via its
    // own interpolate. `stops` double as the legend gradient definition (a value
    // repeated back-to-back is the hard seam); `ticks` label the legend bar.
    // Bounds come from the real pulled data (tools/build_icebridge_points.py
    // printed min/p10/median/p90/max across both instruments): bed_height_m spans
    // roughly -454..2681 with p90 ~900-1100, so -450..900 covers the bulk without
    // letting rare high-elevation outliers wash out the near-zero gradation;
    // ice_thickness_m p90 ~570-880, so 0..700 likewise.
    var ICEBRIDGE_RAMPS = {
        bed_height_m: {
            // Still medium (not pale/near-white) right at the seam, so the
            // blue/green hue swap itself reads clearly at 0 — but lighter than
            // the first cut, which put low- and high-magnitude points too close
            // in lightness to tell apart. Widening light-near-0 -> dark-at-extreme
            // gives each arm its own full lightness range to differentiate by.
            expr: ['case',
                ['<', ['get', 'bed_height_m'], 0],
                ['interpolate', ['linear'], ['get', 'bed_height_m'], -450, '#082542', 0, '#6da7ec'],
                ['interpolate', ['linear'], ['get', 'bed_height_m'], 0, '#5cb873', 900, '#0a3016']
            ],
            stops: [[-450, '#082542'], [0, '#6da7ec'], [0, '#5cb873'], [900, '#0a3016']],
            ticks: [-450, 0, 450, 900], unit: ' m'
        },
        ice_thickness_m: {
            // Some rows carry bed_height_m but a null ice_thickness_m (the
            // build script writes None when the CSV field is empty). Feeding
            // null into interpolate fails per-feature and those points render
            // MapLibre's DEFAULT BLACK, plus a console warning per frame.
            // Guard on typeof: non-numbers render neutral gray — visibly
            // "no data" while keeping the flight line's spatial continuity
            // (hiding them would make point counts change with the color-by
            // selector).
            expr: ['case',
                ['!=', ['typeof', ['get', 'ice_thickness_m']], 'number'],
                '#9e9e9e',
                ['interpolate', ['linear'], ['get', 'ice_thickness_m'],
                  0, '#cde2fb',
                100, '#6da7ec',
                250, '#2a78d6',
                450, '#184f95',
                700, '#0d366b']],
            stops: [[0, '#cde2fb'], [100, '#6da7ec'], [250, '#2a78d6'], [450, '#184f95'], [700, '#0d366b']],
            ticks: [0, 350, 700], unit: ' m'
        }
    };
    var _icebridgeColorBy = 'bed_height_m';
    function _icebridgeColorExpr() { return ICEBRIDGE_RAMPS[_icebridgeColorBy].expr; }
    function ensureOverlays() {
        OVERLAYS.forEach(function (ov) {
            if (!map.getSource(ov.sourceId)) map.addSource(ov.sourceId, ov.sourceDef());
            if (!map.getLayer(ov.layerId)) {
                if (ov.type === 'circle') {
                    map.addLayer({
                        id: ov.layerId, type: 'circle', source: ov.sourceId,
                        layout: { 'visibility': 'none' },
                        paint: {
                            'circle-radius': 3,
                            'circle-color': _icebridgeColorExpr(),
                            'circle-opacity': 1,
                            'circle-stroke-width': 0
                        }
                    });
                } else {
                    map.addLayer({
                        id: ov.layerId, type: 'raster', source: ov.sourceId,
                        layout: { 'visibility': 'none' },
                        paint: { 'raster-opacity': 1, 'raster-resampling': 'nearest' }
                    });
                }
            }
        });
        applyOverlays();
    }
    function applyOverlays() {
        OVERLAYS.forEach(function (ov) {
            if (!map.getLayer(ov.layerId)) return;
            var st = _ovState[ov.id];
            map.setLayoutProperty(ov.layerId, 'visibility', st.on ? 'visible' : 'none');
            var opProp = ov.type === 'circle' ? 'circle-opacity' : 'raster-opacity';
            map.setPaintProperty(ov.layerId, opProp, st.op);
        });
    }
    function swapOverlaySource(ov) {
        if (!map.getLayer(ov.layerId)) return;
        map.removeLayer(ov.layerId);
        map.removeSource(ov.sourceId);
        ensureOverlays();
    }
    map.on('style.load', function () { ensureOverlays(); });

    // ---------------------------------------------------------------------
    // Layers tab: basemap select, overlay rows
    // ---------------------------------------------------------------------
    var bmRow = document.getElementById('gl-basemap-row');
    var bmSel = document.createElement('select');
    basemaps.forEach(function (bm) {
        var o = document.createElement('option');
        o.value = bm.id; o.textContent = bm.label;
        if (bm.id === DEFAULT_BASEMAP) o.selected = true;
        bmSel.appendChild(o);
    });
    bmSel.value = _currentBasemap;
    bmSel.addEventListener('change', function () {
        var bm = findBasemap(bmSel.value);
        if (!bm) return;
        _currentBasemap = bm.id;
        // styleFor, not buildRasterStyle: style-carrying basemaps (Blank
        // White) need their prebuilt style; diff:false forces the swap.
        map.setStyle(window.LSBasemaps.styleFor(bm), { diff: false });
        // overlays re-added on style.load
        writeHash();
    });
    bmRow.appendChild(bmSel);

    var ovWrap = document.getElementById('gl-overlays');
    OVERLAYS.forEach(function (ov) {
        var st = _ovState[ov.id];
        var row = document.createElement('div');
        row.className = 'gl-ov-row';
        var lab = document.createElement('label');
        var cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = st.on;   // hash-restored state
        var span = document.createElement('span');
        span.textContent = ov.label;
        span.title = ov.sub;
        lab.appendChild(cb); lab.appendChild(span);
        row.appendChild(lab);
        var op = document.createElement('input');
        op.type = 'range'; op.min = '10'; op.max = '100';
        op.value = String(Math.round(st.op * 100));
        op.style.display = st.on ? '' : 'none';
        op.addEventListener('input', function () { st.op = (+op.value) / 100; applyOverlays(); });
        op.addEventListener('change', writeHash);
        row.appendChild(op);
        var vlab = null, vcb = null;
        if (ov.variant) {
            vlab = document.createElement('label');
            vlab.style.cssText = 'font-size:11px;color:#555;';
            if (ov.variant.title) vlab.title = ov.variant.title;
            vcb = document.createElement('input');
            vcb.type = 'checkbox';
            vcb.checked = ov.variant.get();
            vcb.addEventListener('change', function () {
                ov.variant.set(vcb.checked);
                swapOverlaySource(ov);
                writeHash();
            });
            vlab.appendChild(vcb);
            vlab.appendChild(document.createTextNode(ov.variant.label));
            vlab.style.display = st.on ? 'flex' : 'none';
            row.appendChild(vlab);
        }
        cb.addEventListener('change', function () {
            st.on = cb.checked;
            op.style.display = st.on ? '' : 'none';
            if (vlab) vlab.style.display = st.on ? 'flex' : 'none';
            ensureOverlays();
            writeHash();
        });
        ovWrap.appendChild(row);
    });

    // Shared color-by select for the two IceBridge layers (bed height vs.
    // thickness) — one control drives both, so they stay comparable.
    var ibColorRow = document.createElement('div');
    ibColorRow.className = 'gl-ov-row';
    var ibColorLabel = document.createElement('span');
    ibColorLabel.className = 'gl-sub';
    ibColorLabel.textContent = 'IceBridge color: ';
    ibColorRow.appendChild(ibColorLabel);
    var ibColorSel = document.createElement('select');
    ibColorSel.style.cssText = 'width:auto; font-size:11px; padding:1px; margin:0;';
    [['bed_height_m', 'Bed height'], ['ice_thickness_m', 'Ice thickness']].forEach(function (o) {
        var e = document.createElement('option');
        e.value = o[0]; e.textContent = o[1];
        ibColorSel.appendChild(e);
    });
    ibColorSel.value = _icebridgeColorBy;
    ibColorSel.addEventListener('change', function () {
        _icebridgeColorBy = ibColorSel.value;
        ICEBRIDGE_OVERLAYS.forEach(function (ov) {
            if (map.getLayer(ov.layerId)) map.setPaintProperty(ov.layerId, 'circle-color', _icebridgeColorExpr());
        });
        updateIcebridgeLegend();
    });
    ibColorRow.appendChild(ibColorSel);
    ovWrap.appendChild(ibColorRow);

    // Color bar legend for whichever IceBridge property is active — stops
    // double as the CSS gradient definition (a repeated value = the hard seam
    // at 0 for bed height), ticks label a few values along it.
    var ibLegendBar = document.createElement('div');
    ibLegendBar.style.cssText = 'height:10px; border-radius:2px; border:1px solid rgba(0,0,0,0.15); margin-top:3px;';
    var ibLegendTicks = document.createElement('div');
    // position:relative + absolute-positioned children, NOT flex space-between —
    // space-between spaces labels evenly regardless of value, which is why "0m"
    // previously sat at the visual center instead of over the actual color seam.
    ibLegendTicks.style.cssText = 'position:relative; height:11px; font-size:9px; color:#777; margin-top:1px;';
    ovWrap.appendChild(ibLegendBar);
    ovWrap.appendChild(ibLegendTicks);
    function updateIcebridgeLegend() {
        var ramp = ICEBRIDGE_RAMPS[_icebridgeColorBy];
        var vmin = ramp.stops[0][0], vmax = ramp.stops[ramp.stops.length - 1][0];
        var span = (vmax - vmin) || 1;
        function pct(v) { return ((v - vmin) / span) * 100; }
        var grad = ramp.stops.map(function (s) {
            return s[1] + ' ' + pct(s[0]).toFixed(1) + '%';
        }).join(', ');
        ibLegendBar.style.background = 'linear-gradient(to right, ' + grad + ')';
        ibLegendTicks.innerHTML = '';
        ramp.ticks.forEach(function (v) {
            var p = pct(v);
            var s = document.createElement('span');
            s.textContent = (v > 0 ? '+' : '') + v + ramp.unit;
            // Keep edge labels from overflowing the bar; interior ones center on their tick.
            var shift = p <= 0.5 ? '0%' : (p >= 99.5 ? '-100%' : '-50%');
            s.style.cssText = 'position:absolute; left:' + p.toFixed(1) + '%; transform:translateX(' + shift + '); white-space:nowrap;';
            ibLegendTicks.appendChild(s);
        });
    }
    updateIcebridgeLegend();

    // ---------------------------------------------------------------------
    // Pointer policy: the shared dispatcher (ls_tools.js) owns clicks, the
    // cursor and the one popup slot, as on /inventory/ and /lidar/. Modules
    // register their layers with it; nothing here adds a map.on('click').
    // ---------------------------------------------------------------------
    window.LSTools.init(map, { position: 'top-right' });

    // ---------------------------------------------------------------------
    // URL hash. The shell writes it; each module contributes its own params
    // through addHashExtras. Params a not-yet-loaded module owns (site= and
    // t= before the Flow tab is opened) are carried through unchanged, so
    // opening an old flow link in another tab and copying the address keeps
    // them.
    // ---------------------------------------------------------------------
    var _extraProviders = [];
    var _carried = {};
    Object.keys(_hash.extras || {}).forEach(function (k) { _carried[k] = _hash.extras[k]; });
    function writeHash() {
        if (!window.LSHash || !map) return;
        var c = map.getCenter();
        var ov = {};
        OVERLAYS.forEach(function (o) {
            var st = _ovState[o.id];
            if (st.on) {
                ov[o.id] = { left: true, opLeft: st.op,
                             smooth: !!(o.variant && o.variant.get()) };
            }
        });
        var extras = {};
        Object.keys(_carried).forEach(function (k) { extras[k] = _carried[k]; });
        _extraProviders.forEach(function (fn) {
            var e = fn() || {};
            Object.keys(e).forEach(function (k) { extras[k] = e[k]; });
        });
        // Always written, never left to the default: a link with site= or
        // t= and no tab= is read as a pre-tabs flow link (see below), so
        // leaving tab= off a Retreat link that still carries a flow site
        // would reopen it on Flow.
        extras.tab = _tab;
        var h = window.LSHash.encode({
            zoom: map.getZoom(), lat: c.lat, lon: c.lng,
            base: _currentBasemap !== DEFAULT_BASEMAP ? _currentBasemap : null,
            ov: Object.keys(ov).length ? ov : null,
            extras: extras
        });
        if (location.hash !== h) history.replaceState(null, '', h);
    }
    map.on('moveend', writeHash);

    // ---------------------------------------------------------------------
    // Tabs. One glacier identity, one tab per topic: Retreat (default), Flow
    // (the tracers, loaded on first use), Layers (basemap + raster overlays).
    // A link written before the tabs existed has site= or t= and no tab=;
    // it opens on Flow, which is what it showed when it was written.
    // ---------------------------------------------------------------------
    var DEFAULT_TAB = 'retreat';
    var TABS = ['retreat', 'flow', 'layers'];
    var _tab = _hash.extras.tab && TABS.indexOf(_hash.extras.tab) >= 0 ? _hash.extras.tab
        : ((_hash.extras.site != null || _hash.extras.t != null) ? 'flow' : DEFAULT_TAB);
    var _tabListeners = [];
    var _tracers = null, _tracersLoading = false;

    function loadTracers() {
        if (_tracers || _tracersLoading) return;
        _tracersLoading = true;
        var s = document.createElement('script');
        s.src = CFG.tracersJs;
        s.onload = function () {
            _tracers = window.GLTracers.start({
                CFG: CFG, map: map, hash: _hash, writeHash: writeHash,
                // Opened from the Flow tab of a link that carried no view:
                // frame the site, as the page always did. Otherwise the
                // camera stays where the visitor put it.
                keepView: _hashHadView || _tabChanged
            });
            delete _carried.site; delete _carried.t;
            _extraProviders.push(_tracers.hashExtras);
            _tracers.setActive(_tab === 'flow');
        };
        s.onerror = function () { _tracersLoading = false; };
        document.body.appendChild(s);
    }

    var _tabChanged = false;
    function showTab(name, fromUser) {
        if (TABS.indexOf(name) < 0) name = DEFAULT_TAB;
        if (fromUser && name !== _tab) _tabChanged = true;
        _tab = name;
        TABS.forEach(function (t) {
            var pane = document.getElementById('gl-pane-' + t);
            var btn = document.getElementById('gl-tab-' + t);
            if (pane) pane.style.display = t === name ? '' : 'none';
            if (btn) btn.classList.toggle('active', t === name);
        });
        document.getElementById('gl-timebar').style.display = name === 'flow' ? '' : 'none';
        if (name === 'flow') loadTracers();
        if (_tracers) _tracers.setActive(name === 'flow');
        _tabListeners.forEach(function (fn) { fn(name); });
        writeHash();
    }
    TABS.forEach(function (t) {
        var btn = document.getElementById('gl-tab-' + t);
        if (btn) btn.addEventListener('click', function () { showTab(t, true); });
    });

    // What a topic module gets.
    var GL = {
        map: map, CFG: CFG, hash: _hash, hadView: _hashHadView,
        writeHash: writeHash,
        addHashExtras: function (fn, keys) {
            (keys || []).forEach(function (k) { delete _carried[k]; });
            _extraProviders.push(fn);
        },
        tab: function () { return _tab; },
        basemap: function () { return _currentBasemap; },
        onTab: function (fn) { _tabListeners.push(fn); },
        showTab: showTab
    };
    window.GL = GL;
    // The tabs come up even if a topic module fails to start.
    try { if (window.GLRetreat) window.GLRetreat.init(GL); }
    catch (e) { console.error('retreat module failed to start:', e); }
    try { if (window.GLNetwork) window.GLNetwork.init(GL); }
    catch (e) { console.error('network module failed to start:', e); }
    showTab(_tab, false);
})();
