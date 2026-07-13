(function () {
    'use strict';

    // Configuration seam. The live site leaves window.LS_CONFIG undefined, so
    // everything falls back to the live defaults. Snapshot bundles set this
    // object in their frozen index.html before this script runs, allowing
    // them to point the JS at their own pre-rendered API files (apiBase) and
    // pin basemap tile URLs at snapshot-time values that can be surgically
    // edited later if a provider changes its URL.
    var CFG = window.LS_CONFIG || {};
    var API_BASE = CFG.apiBase || '/inventory/';
    // Where the static assets live. Live site: /static/ (WhiteNoise +
    // Caddy serve with long cache headers). Snapshot bundles: './static/'
    // so they're self-contained.
    var STATIC_BASE = CFG.staticBase || '/static/';

    // USGS Belair et al. (2024) landslide-susceptibility overlays (Alaska).
    // Self-hosted, pre-colored XYZ PNGs (YlOrRd, transparent over NoData) built
    // by tools/build_susc_tiles.sh and served at /tiles/susc/<key>/. MapLibre
    // GL JS does not implement Mapbox's `raster-color`, so color is baked at
    // tile-gen time from tools/susc_color.txt — recoloring = re-running that
    // script, not editing JS.
    var SUSC_TILE_BASE = CFG.susTileBase || '/tiles/susc/';
    // Cache-buster for the susceptibility tiles. The tile route sends a 1-year
    // immutable Cache-Control, and tile URLs are NOT content-hashed, so any time
    // the tiles are rebuilt with different pixels (e.g. a recolor / reclass) this
    // MUST be bumped or clients keep the stale image for a year. v2 = discrete
    // frequency-ratio classes (was v1 = continuous YlOrRd ramp).
    var SUSC_TILE_V = '2';
    // The two USGS model variants. Mutually exclusive in the UI (one at a time).
    var SUSC_LAYERS = [
        { key: 'lw',  cb: 'cb-susc-lw',  attr: 'Susceptibility (lw): USGS, Belair et al. 2024' },
        { key: 'n10', cb: 'cb-susc-n10', attr: 'Susceptibility (n10): USGS, Belair et al. 2024' }
    ];
    // Per-landslide sampled susceptibility values {id: {n10, lw}}, used by the
    // n10/lw range sliders to filter the inventory points. Precomputed offline
    // (tools/sample, written to this static JSON) since the web container has no
    // GDAL. Loaded once; merged into the feature properties as `n10` / `lw`.
    var SUSC_VALUES = null;
    var _suscValuesPromise = fetch(STATIC_BASE + 'inventory/susc_values.json?v=' + DATA_V)
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) { SUSC_VALUES = j; })
        .catch(function () { SUSC_VALUES = null; });
    // 82×82 joint density of ALL Alaska terrain in lw×n10 space (grid[n10*82+lw]),
    // drawn as the heatmap behind the scatter dots. Precomputed offline.
    var SUSC_TERRAIN = null;
    fetch(STATIC_BASE + 'inventory/susc_terrain_density.json?v=' + DATA_V)
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) { SUSC_TERRAIN = j; scatterDrawAll(); })
        .catch(function () { SUSC_TERRAIN = null; });
    function mergeSuscValues(data) {
        if (!SUSC_VALUES || !data || !data.features) return;
        data.features.forEach(function (ft) {
            // Points key on the landslide id; polygons carry landslide_id.
            var key = ft.properties.id != null ? ft.properties.id : ft.properties.landslide_id;
            var v = SUSC_VALUES[String(key)];
            ft.properties.n10 = (v && v.n10 != null) ? v.n10 : null;
            ft.properties.lw  = (v && v.lw  != null) ? v.lw  : null;
        });
    }

    // Version token embedded by Django — changes on each worker start / data reload.
    // Appended to API URLs so browsers never serve stale cached responses.
    var DATA_V = document.getElementById('map').dataset.version || '';

    // Canonical class order for URL bitmask encoding (matches sidebar order in template).
    var CLASS_ORDER = [
        'Slow Obvious creep', 'Slow Patchy obvious creep',
        'Slow Subtle creep', 'Slow Geomorph creep', 'Small slow landslide',
        'Catastrophic Obvious creep', 'Catastrophic Patchy obvious creep',
        'Catastrophic Subtle creep', 'Catastrophic Geomorph creep',
        'Catastrophic Modern', 'Catastrophic Cryptic', 'Small catastrophic landslide',
        'Catastrophic Holocene',
        '__unclassified__'   // synthetic: matches NULL/empty landslide_class
    ];
    var ALL_CLASSES_MASK = (1 << CLASS_ORDER.length) - 1;

    // ---------------------------------------------------------------------------
    // Size the map to fill the content area below the header
    // ---------------------------------------------------------------------------
    function sizeMap() {
        var inner = document.getElementById('inventory-content');
        if (inner) {
            inner.style.height = (window.innerHeight - inner.getBoundingClientRect().top) + 'px';
        }
    }
    sizeMap();
    window.addEventListener('resize', function () { sizeMap(); map.resize(); onHistResize(); onTimingResize(); });

    // ---------------------------------------------------------------------------
    // URL hash state — `#map=zoom/lat/lon&base=<id>&id=<n>`
    // ---------------------------------------------------------------------------
    function parseHashState(hashStr) {
        var h = hashStr != null ? hashStr : (location.hash || '');
        if (h.charAt(0) === '#') h = h.substring(1);
        var out = {};
        h.split('&').forEach(function (kv) {
            if (!kv) return;
            var i = kv.indexOf('=');
            if (i < 0) return;
            var k = kv.substring(0, i), v = kv.substring(i + 1);
            if (k === 'map') {
                var p = v.split('/');
                if (p.length === 3) {
                    var z = parseFloat(p[0]), la = parseFloat(p[1]), lo = parseFloat(p[2]);
                    if (isFinite(z) && isFinite(la) && isFinite(lo)) {
                        out.zoom = z; out.lat = la; out.lon = lo;
                    }
                }
            } else if (k === 'base') {
                out.base = v;
            } else if (k === 'swipe') {
                if (v) out.swipe = v;
            } else if (k === 'sx') {
                var x = parseFloat(v);
                if (isFinite(x) && x >= 0 && x <= 100) out.sx = x;
            } else if (k === 'id') {
                var n = parseInt(v, 10);
                if (n > 0) out.id = n;
            }
        });
        return out;
    }
    var _initialHash = parseHashState();
    // Returning from a data form (no hash in the URL) restores the last view the
    // editor was at, so they can bounce between mapping and populating fields
    // without losing their place.
    if (!location.hash) {
        try {
            var _savedView = localStorage.getItem('ls_map_view');
            if (_savedView) {
                var _sv = parseHashState(_savedView);
                if (_sv.zoom != null) _initialHash = _sv;
            }
        } catch (e) {}
    }
    var _pendingDetailId = _initialHash.id || null;
    // Wiper state to restore once its basemap is resolvable (built-ins are
    // available immediately; a shared QMS layer only after api/qms/promoted).
    var _pendingSwipe = _initialHash.swipe
        ? { base: _initialHash.swipe, x: (_initialHash.sx != null ? _initialHash.sx : 50) }
        : null;

    function writeHashState() {
        var c = map.getCenter(), z = map.getZoom();
        var parts = ['map=' + z.toFixed(2) + '/' + c.lat.toFixed(4) + '/' + c.lng.toFixed(4)];
        if (_currentBasemap && _currentBasemap !== DEFAULT_BASEMAP_ID) parts.push('base=' + _currentBasemap);
        if (_swipe.on && _swipe.basemapId) {
            parts.push('swipe=' + _swipe.basemapId);
            parts.push('sx=' + Math.round(_swipe.x));
        }
        var newHash = '#' + parts.join('&');
        if (location.hash !== newHash) history.replaceState(null, '', newHash);
        try { localStorage.setItem('ls_map_view', newHash); } catch (e) {}
    }

    // ---------------------------------------------------------------------------
    // MeasureControl — custom maplibre IControl for distance / area measurement.
    // ---------------------------------------------------------------------------
    function fmtLen(m) {
        if (m < 1000) return Math.round(m) + ' m  ·  ' + Math.round(m * 3.28084) + ' ft';
        return (m / 1000).toFixed(2) + ' km  ·  ' + (m / 1609.344).toFixed(2) + ' mi';
    }
    function fmtArea(sqm) {
        if (sqm < 10000) return Math.round(sqm) + ' m²  ·  ' + Math.round(sqm * 10.7639) + ' ft²';
        if (sqm < 1e6)   return (sqm / 10000).toFixed(2) + ' ha  ·  ' + (sqm / 4046.8564).toFixed(1) + ' ac';
        return (sqm / 1e6).toFixed(2) + ' km²  ·  ' + (sqm / 2589988.11).toFixed(2) + ' mi²';
    }

    function MeasureControl() {
        this._mode = 'idle';     // 'idle' | 'line' | 'polygon'
        this._active = [];       // [lng, lat] vertices of the in-progress shape
        this._features = [];     // finalized GeoJSON Features
    }
    MeasureControl.prototype.onAdd = function (mapArg) {
        var self = this;
        this._map = mapArg;

        var el = document.createElement('div');
        el.className = 'maplibregl-ctrl maplibregl-ctrl-group inv-measure-ctrl';

        function mkBtn(label, title, onClick) {
            var b = document.createElement('button');
            b.type = 'button';
            b.title = title;
            b.setAttribute('aria-label', title);
            b.textContent = label;
            b.addEventListener('click', onClick);
            return b;
        }
        this._btnLine  = mkBtn('━', 'Measure distance (Esc to cancel)',
                               function () { self._setMode(self._mode === 'line' ? 'idle' : 'line'); });
        this._btnPoly  = mkBtn('▱', 'Measure area (Esc to cancel)',
                               function () { self._setMode(self._mode === 'polygon' ? 'idle' : 'polygon'); });
        this._btnClear = mkBtn('✕', 'Clear measurements and exit tool',
                               function () { self._clearAll(); });
        el.appendChild(this._btnLine);
        el.appendChild(this._btnPoly);
        el.appendChild(this._btnClear);
        this._container = el;

        this._readout = document.getElementById('measure-readout');
        this._tooltip = document.createElement('div');
        this._tooltip.className = 'inv-measure-tooltip';
        this._map.getContainer().appendChild(this._tooltip);

        // Bind handlers once so we can in principle remove them.
        this._onClick   = this._onClick.bind(this);
        this._onMove    = this._onMove.bind(this);
        this._onLeave   = this._onLeave.bind(this);
        this._onKey     = this._onKey.bind(this);
        this._map.on('click', this._onClick);
        this._map.on('mousemove', this._onMove);
        this._map.getContainer().addEventListener('mouseleave', this._onLeave);
        document.addEventListener('keydown', this._onKey);

        // Layers must be re-created on every style.load (basemap switch wipes them).
        this._ensureLayers = this._ensureLayers.bind(this);
        this._map.on('style.load', this._ensureLayers);
        if (this._map.isStyleLoaded()) this._ensureLayers();

        return el;
    };
    MeasureControl.prototype.onRemove = function () { /* not used */ };

    MeasureControl.prototype._setMode = function (mode) {
        // Mutual exclusion with the draw-new tool — don't start measuring mid-draw.
        if (mode !== 'idle' && this._map.__drawActive) return;
        if (this._mode === mode) return;
        // Switching out of an in-progress shape commits whatever is valid so far.
        if (this._mode !== 'idle') this._finalize();
        this._mode = mode;
        var drawing = mode !== 'idle';
        this._map.__measureActive = drawing;   // flag for landslide click/hover handlers to skip
        this._btnLine.classList.toggle('active', mode === 'line');
        this._btnPoly.classList.toggle('active', mode === 'polygon');
        this._map.getCanvas().style.cursor = drawing ? 'crosshair' : '';
        if (drawing) this._map.doubleClickZoom.disable();
        else         this._map.doubleClickZoom.enable();
        this._tooltip.style.display = 'none';
        this._setPreview([]);
        this._render();
    };

    MeasureControl.prototype._ensureLayers = function () {
        if (!this._map.getSource('measure-src')) {
            this._map.addSource('measure-src',
                { type: 'geojson', data: { type: 'FeatureCollection', features: [] }});
        }
        if (!this._map.getSource('measure-preview')) {
            this._map.addSource('measure-preview',
                { type: 'geojson', data: { type: 'FeatureCollection', features: [] }});
        }
        if (!this._map.getLayer('measure-fill')) {
            this._map.addLayer({
                id: 'measure-fill', type: 'fill', source: 'measure-src',
                filter: ['==', ['geometry-type'], 'Polygon'],
                paint: { 'fill-color': '#ffaa00', 'fill-opacity': 0.18 }
            });
        }
        if (!this._map.getLayer('measure-preview-line')) {
            this._map.addLayer({
                id: 'measure-preview-line', type: 'line', source: 'measure-preview',
                paint: { 'line-color': '#ffaa00', 'line-width': 2, 'line-dasharray': [2, 2] }
            });
        }
        if (!this._map.getLayer('measure-line')) {
            this._map.addLayer({
                id: 'measure-line', type: 'line', source: 'measure-src',
                filter: ['!=', ['geometry-type'], 'Point'],
                paint: { 'line-color': '#ffaa00', 'line-width': 2.5 }
            });
        }
        if (!this._map.getLayer('measure-points')) {
            this._map.addLayer({
                id: 'measure-points', type: 'circle', source: 'measure-src',
                filter: ['==', ['geometry-type'], 'Point'],
                paint: {
                    'circle-radius': 4,
                    'circle-color': '#fff',
                    'circle-stroke-color': '#ffaa00',
                    'circle-stroke-width': 2
                }
            });
        }
        this._render();
        // Layer order is structural — see the comment on initDataLayers().
        // The measure layers added above are at the top of the stack on every
        // style.load. initDataLayers re-inserts landslide layers below them
        // via beforeId='measure-fill', so no moveLayer chasing is needed here.
    };

    MeasureControl.prototype._onClick = function (e) {
        if (this._mode === 'idle') return;
        // Detect double-click as two clicks within 350ms at near the same pixel,
        // which we treat as "finalize". Avoids the dblclick-vs-click race entirely.
        var now = Date.now(), x = e.point.x, y = e.point.y;
        if (this._lastClickTime && (now - this._lastClickTime) < 350 &&
            Math.hypot(x - this._lastClickX, y - this._lastClickY) < 6) {
            this._lastClickTime = 0;
            this._finalize();
            return;
        }
        this._lastClickTime = now; this._lastClickX = x; this._lastClickY = y;

        // Click on an existing vertex of the active shape → remove it (handy
        // for misplacements).
        var hits = this._map.queryRenderedFeatures(e.point, { layers: ['measure-points'] });
        if (hits.length && hits[0].properties && hits[0].properties._active) {
            this._active.splice(hits[0].properties._idx, 1);
            this._render();
            return;
        }
        this._active.push([e.lngLat.lng, e.lngLat.lat]);
        this._render();
    };

    MeasureControl.prototype._onMove = function (e) {
        if (this._mode === 'idle' || !this._active.length) {
            this._tooltip.style.display = 'none';
            this._setPreview([]);
            return;
        }
        var last = this._active[this._active.length - 1];
        var cur  = [e.lngLat.lng, e.lngLat.lat];
        // Preview: last vertex → cursor (and back to first vertex for polygons).
        if (this._mode === 'polygon' && this._active.length >= 2) {
            this._setPreview([last, cur, this._active[0]]);
        } else {
            this._setPreview([last, cur]);
        }
        // Running total assuming the cursor were the next click.
        var coords = this._active.concat([cur]);
        var label;
        if (this._mode === 'line') {
            label = fmtLen(turf.length(turf.lineString(coords), { units: 'meters' }));
        } else if (coords.length >= 3) {
            label = fmtArea(turf.area(turf.polygon([coords.concat([coords[0]])])));
        } else {
            label = 'click 3+ points';
        }
        this._tooltip.textContent = label;
        this._tooltip.style.left = e.point.x + 'px';
        this._tooltip.style.top  = e.point.y + 'px';
        this._tooltip.style.display = 'block';
    };

    MeasureControl.prototype._onLeave = function () {
        this._tooltip.style.display = 'none';
    };

    MeasureControl.prototype._onKey = function (e) {
        if (e.key === 'Escape' && this._mode !== 'idle') {
            this._active = [];
            this._setPreview([]);
            this._tooltip.style.display = 'none';
            this._render();
        }
    };

    MeasureControl.prototype._setPreview = function (coords) {
        var src = this._map.getSource('measure-preview');
        if (!src) return;
        src.setData(coords.length >= 2
            ? { type: 'FeatureCollection', features: [{
                  type: 'Feature', properties: {},
                  geometry: { type: 'LineString', coordinates: coords }
              }]}
            : { type: 'FeatureCollection', features: [] });
    };

    MeasureControl.prototype._finalize = function () {
        var n = this._active.length;
        if (this._mode === 'line' && n >= 2) {
            this._features.push({
                type: 'Feature',
                properties: { _kind: 'line',
                              _val: turf.length(turf.lineString(this._active), { units: 'meters' }) },
                geometry: { type: 'LineString', coordinates: this._active.slice() }
            });
        } else if (this._mode === 'polygon' && n >= 3) {
            var ring = this._active.concat([this._active[0]]);
            this._features.push({
                type: 'Feature',
                properties: { _kind: 'polygon',
                              _val: turf.area(turf.polygon([ring])) },
                geometry: { type: 'Polygon', coordinates: [ring] }
            });
        }
        this._active = [];
        this._setPreview([]);
        this._tooltip.style.display = 'none';
        this._render();
    };

    MeasureControl.prototype._clearAll = function () {
        // Drop active + finalized features, then exit the tool entirely so
        // the user is back in normal click-to-select mode.
        this._features = [];
        this._active   = [];
        this._setPreview([]);
        this._tooltip.style.display = 'none';
        if (this._mode !== 'idle') {
            this._mode = 'idle';
            this._map.__measureActive = false;
            this._btnLine.classList.remove('active');
            this._btnPoly.classList.remove('active');
            this._map.getCanvas().style.cursor = '';
            this._map.doubleClickZoom.enable();
        }
        this._render();
    };

    MeasureControl.prototype._render = function () {
        var src = this._map.getSource('measure-src');
        if (!src) return;
        var feats = [];

        // Finalized shapes
        this._features.forEach(function (f) { feats.push(f); });

        // Active in-progress shape + its vertices (vertices flagged _active so clicks can remove them)
        if (this._active.length) {
            this._active.forEach(function (pt, i) {
                feats.push({ type: 'Feature',
                             properties: { _active: true, _idx: i },
                             geometry: { type: 'Point', coordinates: pt }});
            });
            if (this._active.length >= 2) {
                feats.push({ type: 'Feature', properties: { _active: true },
                             geometry: { type: 'LineString', coordinates: this._active }});
            }
        }
        // Vertices of finalized shapes (visual breadcrumbs, no _active flag → click won't remove)
        this._features.forEach(function (f) {
            var coords = f.geometry.type === 'LineString'
                ? f.geometry.coordinates
                : f.geometry.coordinates[0].slice(0, -1);   // drop closing dup
            coords.forEach(function (pt) {
                feats.push({ type: 'Feature', properties: {},
                             geometry: { type: 'Point', coordinates: pt }});
            });
        });
        src.setData({ type: 'FeatureCollection', features: feats });

        // Top-center readout aggregates finalized shapes
        if (this._readout) {
            var lines = this._features.map(function (f) {
                return (f.properties._kind === 'line' ? 'Distance: ' : 'Area: ') +
                       (f.properties._kind === 'line' ? fmtLen(f.properties._val)
                                                      : fmtArea(f.properties._val));
            });
            this._readout.innerHTML = lines.join('<br>');
            this._readout.style.display = lines.length ? 'block' : 'none';
        }
    };

    // ---------------------------------------------------------------------------
    // Basemap catalog and style builder — defined before map construction so the
    // initial style reflects the chosen basemap (no flash from streets to topo).
    // ---------------------------------------------------------------------------
    var DEFAULT_BASEMAP_ID = CFG.defaultBasemapId || 'esri-topo';

    // Snapshot bundles freeze BASEMAPS at snapshot time by setting
    // window.LS_CONFIG.basemaps. If a provider URL ever changes (EOX, USGS,
    // ESRI), the snapshot's tile config can be patched in place without
    // touching the live app or rebuilding the snapshot.
    // Built-in basemap descriptors live in the shared module (basemaps.js) so
    // the main map and the edit/review preview map share one definition.
    var BASEMAPS = CFG.basemaps || LSBasemaps.DEFAULTS.slice();
    var REFMAPS_CATEGORY_ORDER = ['Imagery', 'Topo', 'Historical', 'Other', 'Shared', 'QMS'];

    // Per-basemap thumbnail URL. Preference order:
    //   1. CFG.basemapThumbs[id]  — injected by the home template, which
    //      runs each path through {% static %}; on prod these come out as
    //      content-hashed filenames with Cache-Control: max-age=31536000,
    //      public, immutable. Best caching.
    //   2. bm.thumb               — absolute URL or static-relative path
    //      (used by snapshot bundles where the template-injected map
    //      isn't present).
    //   3. Substituted z=4 tile from the live tile template (last resort).
    function basemapThumbnailUrl(bm) {
        return LSBasemaps.thumbnailUrl(bm, { basemapThumbs: CFG.basemapThumbs, staticBase: STATIC_BASE });
    }

    // Tile-URL transforms (quadkey/subdomain via transformRequest, EPSG:3395
    // reprojection via the 'reproj' protocol) live in basemaps.js. The main map
    // wants globe projection + the demotiles glyph server.
    LSBasemaps.registerProtocols();
    function buildRasterStyle(bm) {
        return LSBasemaps.buildRasterStyle(bm, {
            globe: true,
            glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
        });
    }

    // User-added QuickMapServices basemaps (editor exploration tool), persisted
    // per browser in localStorage and merged into BASEMAPS under the 'QMS'
    // category. The QMS catalog is searched via the editor-only api/qms proxy.
    function _loadQmsBasemaps() {
        try { return JSON.parse(localStorage.getItem('ls_qms_basemaps') || '[]') || []; } catch (e) { return []; }
    }
    function _saveQmsBasemaps(list) {
        try { localStorage.setItem('ls_qms_basemaps', JSON.stringify(list)); } catch (e) {}
    }

    function findBasemap(id) {
        for (var i = 0; i < BASEMAPS.length; i++) if (BASEMAPS[i].id === id) return BASEMAPS[i];
        return null;
    }

    // Merge any previously-added QMS basemaps so they're selectable on load.
    _loadQmsBasemaps().forEach(function (b) {
        if (!b || !b.id || findBasemap(b.id)) return;
        // Self-heal layers saved before reproject existed: an EPSG:3395 layer
        // with no reproject flag would render as raw (misaligned) tiles.
        if (!b.reproject && b.coverage && b.coverage.indexOf('EPSG:3395') === 0) b.reproject = 'epsg3395';
        BASEMAPS.push(b);
    });

    var _initialBasemapId = (_initialHash.base && findBasemap(_initialHash.base))
                            ? _initialHash.base : DEFAULT_BASEMAP_ID;
    var _initialBasemap   = findBasemap(_initialBasemapId);

    // ---------------------------------------------------------------------------
    // Map initialisation
    // ---------------------------------------------------------------------------
    var map = new maplibregl.Map({
        container: 'map',
        style: _initialBasemap.style ? _initialBasemap.style : buildRasterStyle(_initialBasemap),
        center: (_initialHash.lon != null && _initialHash.lat != null)
                ? [_initialHash.lon, _initialHash.lat] : [-153, 62],
        zoom: (_initialHash.zoom != null) ? _initialHash.zoom : 4,
        transformRequest: LSBasemaps.transformRequest   // resolves quadkey/subdomain QMS tiles
    });
    // Globe projection — MapLibre 4.x. Re-assert on every style.load so
    // external-URL basemaps (whose JSON we don't control) also get it.
    function applyGlobe() {
        if (typeof map.setProjection === 'function') {
            try { map.setProjection({ type: 'globe' }); } catch (e) {}
        }
    }
    map.on('style.load', applyGlobe);
    applyGlobe();
    map.addControl(new maplibregl.NavigationControl(), 'top-left');
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric'   }), 'bottom-right');
    map.addControl(new maplibregl.ScaleControl({ unit: 'imperial' }), 'bottom-right');

    // ---------------------------------------------------------------------------
    // Measure tool — adapted from MapLibre's "Measure distances" example.
    // Pick a mode (distance / area); each click adds a vertex; double-click
    // (or click again at the last vertex) finalizes. The path / polygon
    // updates continuously, with a tooltip near the cursor showing the
    // running total. Escape cancels the in-progress shape.
    // ---------------------------------------------------------------------------
    if (window.turf) {
        map.addControl(new MeasureControl(), 'top-left');
    }

    // ---------------------------------------------------------------------------
    // Editor-only: "New" button → /inventory/manage/import/.
    // Single-button IControl; uses the same maplibregl-ctrl-group styling as
    // the measure control so they stack visually in the top-left.
    // ---------------------------------------------------------------------------
    function NewRecordControl() {}
    NewRecordControl.prototype.onAdd = function () {
        var el = document.createElement('div');
        el.className = 'maplibregl-ctrl maplibregl-ctrl-group inv-newrec-ctrl';
        var b = document.createElement('button');
        b.type = 'button';
        b.title = 'Add landslide data — opens the GeoJSON upload form';
        b.setAttribute('aria-label', 'Add landslide data');
        // A bare "+" was ambiguous with the zoom-in control and vanished as a
        // white glyph on light basemaps; "+data" labels the action clearly
        // (upload) and reads cleanly against any background.
        b.textContent = '+data';
        b.addEventListener('click', function () {
            window.location.href = '/inventory/manage/import/';
        });
        el.appendChild(b);
        return el;
    };
    NewRecordControl.prototype.onRemove = function () {};

    // Editor-only: "+draw" → /inventory/manage/new/ to draw a brand-new
    // landslide from scratch (Terra Draw), as opposed to "+data" file upload.
    // --- Draw-new tool: draw landslide polygons directly on the main map. ---
    // Each finished polygon gets a name + role and is staged server-side
    // (provisional_polygons). Polygons sharing a name become one landslide on
    // commit (the import synthesize-by-name path). Terra Draw holds at most the
    // one in-progress polygon; staged ones live in our own `prov-src` (re-added
    // on style.load, so they survive basemap switches).
    var DRAW_BASE = '/inventory/manage/draw/';
    var _td = null;                  // Terra Draw instance (0-1 in-progress feature)
    var _prov = [];                  // staged components [{id, unique_name, role, geometry}]
    var _drawPanel = null, _drawStatus = null;

    function _csrf() {
        if (window.CSRF_TOKEN) return window.CSRF_TOKEN;
        var m = document.cookie.match(/csrftoken=([^;]+)/);
        return m ? m[1] : '';
    }
    // An expired/absent editor session 302-redirects the manage endpoints to
    // the admin login page; fetch follows the redirect, so we get a 200 HTML
    // page rather than JSON. Detect that (redirected, or a non-JSON body) and
    // raise a clear "session expired" error instead of letting r.json() choke
    // on "<!DOCTYPE …".
    function _isAuthRedirect(r) {
        var ct = r.headers.get('content-type') || '';
        return r.redirected || ct.indexOf('application/json') === -1;
    }
    function _authExpiredError() {
        var e = new Error('Your editor session has expired — please log in again.');
        e.authExpired = true;
        return e;
    }
    function _drawPost(path, body) {
        return fetch(DRAW_BASE + path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _csrf() },
            body: JSON.stringify(body || {})
        }).then(function (r) {
            if (_isAuthRedirect(r)) throw _authExpiredError();
            return r.json().then(function (j) { return { ok: r.ok, j: j }; });
        });
    }
    // Plain-text flash for the panel/status line, with a login hint when the
    // session has lapsed (staged components are server-side, so they survive).
    function _drawAuthFlash() {
        window.__drawFlash('Session expired — log in at /admin/login/ (new tab), '
            + 'then reopen this panel. Staged components are saved.');
    }
    window.__drawFlash = function (msg) { if (_drawStatus) _drawStatus.textContent = msg; };

    // Provisional layers (re-added on every style.load, below the measure stack).
    function ensureProvLayers() {
        if (!map.getSource('prov-src')) {
            map.addSource('prov-src', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        }
        var bId = map.getLayer('measure-fill') ? 'measure-fill' : undefined;
        if (!map.getLayer('prov-fill')) {
            map.addLayer({ id: 'prov-fill', type: 'fill', source: 'prov-src',
                paint: { 'fill-color': '#00b3a4', 'fill-opacity': 0.25 } }, bId);
        }
        if (!map.getLayer('prov-line')) {
            map.addLayer({ id: 'prov-line', type: 'line', source: 'prov-src',
                paint: { 'line-color': '#00897b', 'line-width': 2, 'line-dasharray': [2, 1.5] } }, bId);
        }
        // (Name labels intentionally omitted — symbol layers depend on a glyph
        // server that varies by basemap and 404s; the queue panel shows names.)
    }
    function refreshProvData() {
        ensureProvLayers();   // guarantee the source + layers exist first
        var src = map.getSource('prov-src');
        if (!src) { console.warn('refreshProvData: prov-src missing'); return; }
        var feats = _prov.map(function (c) {
            return { type: 'Feature', geometry: c.geometry,
                     properties: { label: c.unique_name + ' (' + c.role + ')' } };
        });
        console.log('refreshProvData: staged=' + feats.length +
                    ' prov-fill=' + !!map.getLayer('prov-fill') +
                    ' geom0=' + (feats[0] && feats[0].geometry && feats[0].geometry.type));
        src.setData({ type: 'FeatureCollection', features: feats });
    }

    function startTD() {
        if (_td) return;
        _td = new terraDraw.TerraDraw({
            adapter: new terraDrawMaplibreGlAdapter.TerraDrawMapLibreGLAdapter({ map: map }),
            modes: [new terraDraw.TerraDrawPolygonMode({
                // Default 40px treats clicks near any existing vertex as
                // "close the ring" (premature finish + dropped points). Tighten
                // it so only a deliberate click on the first point closes; use
                // Enter to finish, Escape to cancel. No self-intersection
                // validation — complex/twisted outlines must be drawable; the
                // server runs ST_MakeValid on save.
                pointerDistance: 8,
                keyEvents: { finish: 'Enter', cancel: 'Escape' }
            })]
        });
        _td.start();
        _td.setMode('polygon');
        _td.on('change', onTDChange);
        // `change`/'create' fires on the FIRST click (feature created), not at
        // completion. The polygon is done on the `finish` event (double-click /
        // closing the ring) — that's when we ask for a name + role.
        _td.on('finish', onTDFinish);
        map.doubleClickZoom.disable();
    }
    function stopTD() {
        if (_td) { try { _td.stop(); } catch (e) {} _td = null; }
        map.doubleClickZoom.enable();
        map.__drawPolyOpen = false;
    }
    function onTDChange(ids, type) {
        // A non-empty snapshot means a polygon is mid-draw → lock the basemap.
        map.__drawPolyOpen = !!(_td && _td.getSnapshot().length);
    }
    function onTDFinish(id) {
        openNamePopup(id);
    }

    function openNamePopup(fid) {
        var feat = _td && _td.getSnapshot().filter(function (f) { return f.id === fid; })[0];
        if (!feat) return;
        var names = {}; _prov.forEach(function (c) { names[c.unique_name] = 1; });

        var ov = document.createElement('div'); ov.className = 'inv-draw-popup';
        ov.innerHTML =
            '<div class="inv-draw-popup-box">' +
            '<div style="font-weight:600;margin-bottom:6px;">Name this polygon</div>' +
            '<input id="idp-name" list="idp-names" placeholder="landslide name" autocomplete="off">' +
            '<datalist id="idp-names">' + Object.keys(names).map(function (n) {
                return '<option value="' + n.replace(/"/g, '&quot;') + '">'; }).join('') + '</datalist>' +
            '<select id="idp-role"><option value="source">source</option>' +
            '<option value="body" selected>body</option><option value="deposit">deposit</option></select>' +
            '<div id="idp-warn" style="font-size:11px;color:#a05a00;min-height:13px;margin-top:4px;"></div>' +
            '<div style="margin-top:8px;text-align:right;">' +
            '<button id="idp-cancel" type="button">Cancel</button> ' +
            '<button id="idp-ok" type="button" class="primary">Add</button></div>' +
            '<div style="font-size:11px;color:#888;margin-top:6px;">Tip: reuse a name to add another component (e.g. source + deposit) to the same landslide.</div>' +
            '</div>';
        document.body.appendChild(ov);
        var nameEl = ov.querySelector('#idp-name'), roleEl = ov.querySelector('#idp-role');
        var warnEl = ov.querySelector('#idp-warn');
        nameEl.focus();
        function close() { if (ov.parentNode) ov.parentNode.removeChild(ov); }
        function discard() { if (_td) { _td.removeFeatures([fid]); _td.setMode('polygon'); } map.__drawPolyOpen = false; close(); }
        ov.querySelector('#idp-cancel').addEventListener('click', discard);
        ov.querySelector('#idp-ok').addEventListener('click', function () {
            var nm = (nameEl.value || '').trim(), role = roleEl.value;
            if (!nm) { warnEl.textContent = 'Enter a name.'; return; }
            _drawPost('stage/', { unique_name: nm, role: role, geometry: feat.geometry }).then(function (res) {
                if (res.ok && res.j.ok) {
                    _prov.push(res.j.component);
                    refreshProvData(); renderQueue();
                    if (_td) { _td.removeFeatures([fid]); _td.setMode('polygon'); }
                    map.__drawPolyOpen = false; close();
                } else {
                    warnEl.textContent = (res.j && res.j.error) || 'Stage failed.';
                }
            }).catch(function (e) {
                if (e && e.authExpired) {
                    warnEl.innerHTML = 'Your editor session has expired. '
                        + '<a href="/admin/login/?next=/inventory/" target="_blank" '
                        + 'rel="noopener">Log in</a>, then click Add again — your '
                        + 'staged components are saved.';
                } else {
                    warnEl.textContent = 'Stage failed: ' + (e && e.message ? e.message : e);
                }
            });
        });
    }

    function loadProvisional() {
        fetch(DRAW_BASE + 'list/').then(function (r) { return r.json(); }).then(function (j) {
            _prov = (j && j.components) || [];
            refreshProvData(); renderQueue();
        }).catch(function () {});
    }

    function renderQueue() {
        if (!_drawPanel) return;
        var q = _drawPanel.querySelector('#idq-list');
        // Group by name client-side; fetch server preview for warnings + block.
        var groups = {};
        _prov.forEach(function (c) { (groups[c.unique_name] = groups[c.unique_name] || []).push(c); });
        var names = Object.keys(groups);
        q.innerHTML = names.length ? '' : '<div style="color:#888;font-size:12px;">No components yet — draw a polygon.</div>';
        names.forEach(function (nm) {
            var comps = groups[nm];
            var row = document.createElement('div'); row.className = 'idq-group';
            row.innerHTML = '<div class="idq-name">' + nm + ' <span style="color:#888;">· ' +
                comps.length + ' poly · ' + comps.map(function (c) { return c.role; }).join(', ') +
                '</span></div>';
            comps.forEach(function (c) {
                var x = document.createElement('button'); x.type = 'button'; x.className = 'idq-del';
                x.textContent = '✕ ' + c.role; x.title = 'Remove this component';
                x.addEventListener('click', function () {
                    _drawPost('delete/', { ids: [c.id] }).then(function () {
                        _prov = _prov.filter(function (p) { return p.id !== c.id; });
                        refreshProvData(); renderQueue();
                    }).catch(function (e) {
                        if (e && e.authExpired) _drawAuthFlash();
                    });
                });
                row.appendChild(x);
            });
            var warn = document.createElement('div'); warn.className = 'idq-warn'; warn.dataset.name = nm;
            row.appendChild(warn);
            q.appendChild(row);
        });
        // Server-side warnings + commit-block.
        var commitBtn = _drawPanel.querySelector('#idq-commit');
        if (!names.length) { if (commitBtn) commitBtn.disabled = true; return; }
        fetch(DRAW_BASE + 'preview/').then(function (r) { return r.json(); }).then(function (pv) {
            if (!pv || !pv.ok) return;
            (pv.groups || []).forEach(function (g) {
                var el = _drawPanel.querySelector('.idq-warn[data-name="' + (CSS && CSS.escape ? CSS.escape(g.unique_name) : g.unique_name) + '"]');
                if (el && g.warnings && g.warnings.length) {
                    // "→ adds to existing" is informational, not a warning.
                    el.textContent = g.warnings.map(function (w) {
                        return w.charAt(0) === '→' ? w : '⚠ ' + w;
                    }).join('  ·  ');
                }
            });
            if (commitBtn) commitBtn.disabled = !!pv.has_block;
        }).catch(function () {});
    }

    function openPanel() {
        if (_drawPanel) { _drawPanel.style.display = ''; return; }
        var p = document.createElement('div'); p.className = 'inv-draw-panel';
        p.innerHTML =
            '<div class="inv-draw-hd">✏ Draw new landslide</div>' +
            '<div style="font-size:11px;color:#666;line-height:1.4;margin-bottom:6px;">' +
            'Click to add vertices, press <b>Enter</b> to finish (Esc cancels), then name it. ' +
            'Same name = same landslide. Pick imagery before drawing each polygon.</div>' +
            '<div id="idq-list" class="inv-draw-list"></div>' +
            '<div id="idq-status" class="ls-poly-status" style="min-height:14px;"></div>' +
            '<div style="margin-top:8px;display:flex;gap:6px;">' +
            '<button id="idq-commit" type="button" class="primary" disabled>Commit → review</button>' +
            '<button id="idq-discard" type="button">Discard all</button>' +
            '<button id="idq-done" type="button" style="margin-left:auto;">Exit</button></div>';
        document.body.appendChild(p);
        _drawPanel = p; _drawStatus = p.querySelector('#idq-status');
        p.querySelector('#idq-commit').addEventListener('click', function () {
            window.__drawFlash('Committing…');
            _drawPost('commit/').then(function (res) {
                if (res.ok && res.j.ok) { window.location.href = res.j.redirect; }
                else { window.__drawFlash((res.j && res.j.error) || 'Commit failed.'); renderQueue(); }
            }).catch(function (e) {
                if (e && e.authExpired) _drawAuthFlash();
                else window.__drawFlash('Commit failed: ' + (e && e.message ? e.message : e));
            });
        });
        p.querySelector('#idq-discard').addEventListener('click', function () {
            if (!window.confirm('Discard all staged components?')) return;
            _drawPost('delete/', { all: true }).then(function () { _prov = []; refreshProvData(); renderQueue(); })
                .catch(function (e) { if (e && e.authExpired) _drawAuthFlash(); });
        });
        p.querySelector('#idq-done').addEventListener('click', function () { _drawCtrl.deactivate(); });
    }
    function closePanel() { if (_drawPanel) _drawPanel.style.display = 'none'; }

    function DrawModeControl() {}
    DrawModeControl.prototype.onAdd = function (mapArg) {
        var self = this; this._map = mapArg; this._on = false;
        var el = document.createElement('div');
        el.className = 'maplibregl-ctrl maplibregl-ctrl-group inv-newrec-ctrl';
        var b = document.createElement('button');
        b.type = 'button'; b.textContent = '✏ draw';
        b.title = 'Draw a new landslide on the map';
        b.setAttribute('aria-label', 'Draw a new landslide');
        b.addEventListener('click', function () { self.toggle(); });
        this._btn = b; el.appendChild(b);
        // Provisional layers + staged data survive basemap switches (re-added on
        // every style.load, MeasureControl pattern).
        map.on('style.load', refreshProvData);
        if (map.isStyleLoaded()) refreshProvData();
        return el;
    };
    DrawModeControl.prototype.onRemove = function () {};
    DrawModeControl.prototype.toggle = function () { this._on ? this.deactivate() : this.activate(); };
    DrawModeControl.prototype.activate = function () {
        if (map.__measureActive) { alert('Exit the measure tool first.'); return; }
        if (typeof terraDraw === 'undefined' || typeof terraDrawMaplibreGlAdapter === 'undefined') {
            alert('Drawing library failed to load — check your connection and reload.');
            return;
        }
        map.__drawActive = true; this._on = true; this._btn.classList.add('active');
        map.getCanvas().style.cursor = 'crosshair';
        var self = this;
        // Open the panel first so any Terra Draw init error is visible (and the
        // catch below fully deactivates, so a failure never locks the map up).
        try {
            ensureProvLayers();
            openPanel();
            startTD();
            loadProvisional();
            // Basemap switch (setStyle) wipes Terra Draw's layers — rebuild it on
            // the new style so drawing keeps working. (Switching is blocked while
            // a polygon is open, so there's no in-progress ring to lose here.)
            this._styleReload = function () { if (self._on) { stopTD(); startTD(); } };
            map.on('style.load', this._styleReload);
        } catch (e) {
            console.error('draw activate failed:', e);
            this.deactivate();
            alert('Could not start the draw tool: ' + (e && e.message ? e.message : e));
        }
    };
    DrawModeControl.prototype.deactivate = function () {
        map.__drawActive = false; this._on = false; this._btn.classList.remove('active');
        map.getCanvas().style.cursor = '';
        if (this._styleReload) { map.off('style.load', this._styleReload); this._styleReload = null; }
        stopTD(); closePanel();
    };

    // --- Editor-only: provisional (pending) landslides, shown in magenta. ---
    // Pending records are hidden from the public map; editors see them so they
    // can tell what they've already added while mapping a series. Click one to
    // open its review form. Survives basemap switches (style.load), like measure.
    var _pendingData = { points: { type: 'FeatureCollection', features: [] },
                         polygons: { type: 'FeatureCollection', features: [] } };
    function ensurePendingLayers() {
        if (!map.getSource('pending-poly-src')) map.addSource('pending-poly-src', { type: 'geojson', data: _pendingData.polygons });
        if (!map.getSource('pending-pt-src'))   map.addSource('pending-pt-src',   { type: 'geojson', data: _pendingData.points });
        var bId = map.getLayer('measure-fill') ? 'measure-fill' : undefined;
        _pendingLayerDefs().forEach(function (def) {
            if (!map.getLayer(def.id)) map.addLayer(def, bId);
        });
        // (Name labels omitted — glyph server varies by basemap and 404s.)
        setPendingData();
    }
    function setPendingData() {
        if (!map.getSource('pending-poly-src')) ensurePendingLayers();
        if (map.getSource('pending-poly-src')) map.getSource('pending-poly-src').setData(_pendingData.polygons);
        if (map.getSource('pending-pt-src'))   map.getSource('pending-pt-src').setData(_pendingData.points);
        _swipeSetPending();   // mirror to the comparison map too
    }
    function loadPending() {
        fetch(API_BASE + 'api/provisional/').then(function (r) { return r.json(); }).then(function (d) {
            _pendingData = { points: d.points || { type: 'FeatureCollection', features: [] },
                             polygons: d.polygons || { type: 'FeatureCollection', features: [] } };
            console.log('provisional loaded:', (_pendingData.points.features || []).length, 'points,',
                        (_pendingData.polygons.features || []).length, 'polygons');
            setPendingData();
        }).catch(function (e) { console.error('provisional load failed:', e); });
    }

    // --- Editor-only: pin a field to show its value as a label on every
    // landslide. The label layer rides on the 'landslides' source, so it is
    // re-added by initDataLayers on every basemap switch. Font 'Noto Sans
    // Regular' is served by both glyph servers (demotiles + OpenFreeMap). ---
    var _pinField = '';
    try { _pinField = localStorage.getItem('ls_pin_field') || ''; } catch (e) {}
    // Per-map worker: add/update/remove the pin-label layer on one map.
    // Returns true when the layer was newly added (caller re-applies the filter).
    function _ensurePinLabelOn(m) {
        if (!window._isInventoryEditor || !m || !m.getSource('landslides')) return false;
        if (!_pinField) { if (m.getLayer('pin-label')) m.removeLayer('pin-label'); return false; }
        var expr = ['to-string', ['coalesce', ['get', _pinField], '']];
        if (m.getLayer('pin-label')) { m.setLayoutProperty('pin-label', 'text-field', expr); return false; }
        try {
            m.addLayer({
                id: 'pin-label', type: 'symbol', source: 'landslides',
                layout: {
                    'text-field': expr,
                    'text-font': ['Noto Sans Regular'],
                    'text-size': 11,
                    'text-offset': [0, 1.1],
                    'text-anchor': 'top',
                    'text-allow-overlap': false,
                    'text-optional': true
                },
                paint: { 'text-color': '#1a1a1a', 'text-halo-color': '#fff', 'text-halo-width': 1.6 }
            });
            return true;
        } catch (e) { console.warn('pin-label add failed:', e); return false; }
    }
    // Mirrors to the swipe comparison map so a pinned label doesn't cut off at
    // the wiper divider.
    function ensurePinLabel() {
        var added = _ensurePinLabelOn(map);
        if (_swipe.map) added = _ensurePinLabelOn(_swipe.map) || added;
        // Apply the active filter to the newly-added layer(s).
        if (added && typeof buildFilter === 'function') buildFilter();
    }
    function setPinField(field) {
        _pinField = field || '';
        try { localStorage.setItem('ls_pin_field', _pinField); } catch (e) {}
        ensurePinLabel();
    }

    // Live-update one feature's property in the 'landslides' source after an
    // inline info-box edit, so the pinned label + active filter reflect it
    // without a full reload.
    function _patchFeatureProp(id, name, value) {
        if (!_featuresData || !_featuresData.features) return;
        var feats = _featuresData.features;
        for (var i = 0; i < feats.length; i++) {
            if (feats[i].properties && feats[i].properties.id === id) {
                feats[i].properties[name] = value;
                break;
            }
        }
        if (map.getSource('landslides')) map.getSource('landslides').setData(_featuresData);
        _swipeSetFeatures(_featuresData);   // keep the comparison pane's copy in step
        if (typeof buildFilter === 'function') buildFilter();
    }

    // Pinnable fields are manually-entered text only — no rule-derived columns
    // (class, creep behavior) and not `flagged` (cleared via its own banner).
    var _PIN_LABELS = { unique_name: 'Name', owner: 'Owner',
                        noted_by: 'Noted by', year_text: 'Year' };

    var _drawCtrl = null;
    if (window._isInventoryEditor) {
        map.addControl(new NewRecordControl(), 'top-left');
        _drawCtrl = new DrawModeControl();
        map.addControl(_drawCtrl, 'top-left');

        map.on('style.load', ensurePendingLayers);
        if (map.isStyleLoaded()) ensurePendingLayers();
        loadPending();
        document.addEventListener('visibilitychange', function () { if (!document.hidden) loadPending(); });

        function _openPending(id) { if (!map.__measureActive && !map.__drawActive && id) window.location.href = '/inventory/manage/' + id + '/review/'; }
        map.on('click', 'pending-pt',        function (e) { _openPending(e.features[0].properties.id); });
        map.on('click', 'pending-poly-fill', function (e) { _openPending(e.features[0].properties.landslide_id); });
        ['pending-pt', 'pending-poly-fill'].forEach(function (lyr) {
            map.on('mouseenter', lyr, function () { if (!map.__measureActive && !map.__drawActive) map.getCanvas().style.cursor = 'pointer'; });
            map.on('mouseleave', lyr, function () { if (!map.__measureActive && !map.__drawActive) map.getCanvas().style.cursor = ''; });
        });

        // Pin-field dropdown: restore the saved choice, relabel on change.
        var pinSel = document.getElementById('pin-field');
        if (pinSel) {
            if (_pinField) pinSel.value = _pinField;
            pinSel.addEventListener('change', function () { setPinField(pinSel.value); });
        }
    }

    // ---------------------------------------------------------------------------
    // Load settings + map ready — both must complete before adding layers
    // ---------------------------------------------------------------------------
    var POLYGON_ZOOM       = 7;
    var polygonLoadPending = false;
    var _settings = null, _mapReady = false, _settingsReady = false, _layersInitialized = false;
    var _featuresData = null;      // cached GeoJSON so re-init after basemap switch is instant
    var _polygonsData = null;      // last loaded polygons GeoJSON (mirrored to the swipe map)
    var _surveyCirclesData = null; // cached survey-circles GeoJSON (fetched on first toggle)
    var _faultsData = null;        // cached AK Quaternary faults GeoJSON (fetched at init; on by default)
    var _currentBasemap = _initialBasemapId;

    // Layer style variables set by initLayers, reused by initDataLayers on basemap switch
    var _palette, _fOp, _lW, _rSm, _rMd, _rLg;
    var _classFill, _classStroke, _classStrokeW;

    // tryInit: called once at startup when both map and settings are ready.
    function tryInit() {
        if (!_mapReady || !_settingsReady || _layersInitialized) return;
        _layersInitialized = true;
        initLayers(_settings);
        if (_pendingDetailId != null) {
            showDetail(_pendingDetailId);
            _pendingDetailId = null;
            writeHashState();   // strips id= now that the panel is open
        }
    }

    // 'load' fires once on initial render; we use 'once' so setStyle() re-fires don't hit this.
    map.once('load', function () {
        _mapReady = true;
        map.on('moveend', writeHashState);
        tryInit();
    });

    var DEFAULTS = {
        polygon_zoom: '7', circle_sm: '3', circle_md: '5', circle_lg: '7',
        fill_opacity: '0.35', line_width: '1.5', stroke_color: '#ffffff',
        color_geomorph: '#d3e9cf', color_subtle: '#faf075',
        color_obvious: '#f69fa1', color_cat: '#3f67b1'
    };

    fetch(API_BASE + 'api/settings/?v=' + DATA_V)
        .then(function (r) { return r.json(); })
        .then(function (s) { _settings = s; })
        .catch(function () { _settings = DEFAULTS; })
        .then(function () { _settingsReady = true; tryInit(); });

    // ---------------------------------------------------------------------------
    // Layer initialisation (two-phase: parse settings once, re-add layers on basemap switch)
    // ---------------------------------------------------------------------------
    function initLayers(s) {
        POLYGON_ZOOM = parseInt(s.polygon_zoom, 10) || 7;
        // Resolve the shared palette + paint expressions (ls_colors.js) so the
        // main map and the per-record form preview map stay in lock-step.
        _palette = window.LSColors.palette(s);
        _fOp = _palette.fOp; _lW = _palette.lW;
        _rSm = _palette.rSm; _rMd = _palette.rMd; _rLg = _palette.rLg;
        _classFill    = window.LSColors.classFill(_palette);
        _classStroke  = window.LSColors.classStroke(_palette);
        _classStrokeW = window.LSColors.classStrokeWidth();

        initDataLayers();

        // A hash-restored wiper can create the comparison map before settings
        // arrive; its style.load then bails on the missing palette (_swipeAddData
        // guard). Now that the palette exists, build its stack.
        if (_swipe.map && _swipe.map.isStyleLoaded()) _swipeAddData(_swipe.map);

        fetch(API_BASE + 'api/features/?v=' + DATA_V)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                _featuresData = data;
                // Attach sampled susceptibility once the value table is in, then
                // (re)apply the data + filters so the n10/lw sliders work.
                _suscValuesPromise.then(function () {
                    mergeSuscValues(data);
                    if (map.getSource('landslides')) map.getSource('landslides').setData(data);
                    _swipeSetFeatures(data);
                    if (map.getLayer('points')) buildFilter();
                    scatterDrawAll();
                });
                if (map.getSource('landslides')) map.getSource('landslides').setData(data);
                _swipeSetFeatures(data);
            })
            .catch(function (e) { console.error('Feature load failed:', e); });

        map.on('moveend', onMoveEnd);
    }

    // Landslide data layers (points + the two polygon layers), shared by the
    // main map and the swipe comparison map so their symbology is identical.
    // Paint references the module-scope style vars set by initLayers + the
    // shared LSColors expressions. Returns [points, points-patchy, polygon-fill, polygon-outline].
    function _landslideLayerDefs() {
        return [
            {
                id: 'points', type: 'circle', source: 'landslides',
                layout: { 'circle-sort-key': window.LSColors.pointSortKey() },
                paint: {
                    'circle-color': _classFill,
                    'circle-radius': window.LSColors.pointRadius(_palette, 1),
                    'circle-stroke-width': _classStrokeW,
                    'circle-stroke-color': _classStroke,
                    'circle-opacity': 0.9
                }
            },
            // Slow patchy-obvious: a small red center dot over the yellow base
            // (symbolically "partly obvious"). Scaled to sit inside the dot.
            { id: 'points-patchy', type: 'circle', source: 'landslides',
              filter: window.LSColors.PATCHY_FILTER,
              paint: {
                  'circle-color': _palette.patchyDot,
                  'circle-radius': window.LSColors.pointRadius(_palette, 0.45),
                  'circle-stroke-width': 0,
                  'circle-opacity': 0.95
              }
            },
            { id: 'polygon-fill', type: 'fill', source: 'polygons',
              paint: { 'fill-color': window.LSColors.polygonFill(_palette), 'fill-opacity': _fOp } },
            { id: 'polygon-outline', type: 'line', source: 'polygons',
              paint: { 'line-color': window.LSColors.polygonOutline(_palette), 'line-width': _lW } },
        ];
    }

    // Overlay / auxiliary source+layer defs shared verbatim by the main map and
    // the swipe comparison map (same reasoning as _landslideLayerDefs: one def =
    // identical paint on both panes, so nothing can drift). Visibility is read
    // from the sidebar checkboxes at build time; later toggles mirror to both
    // maps via _swipeAlso.
    function _suscSourceDef(s) {
        return {
            type: 'raster',
            tiles: [SUSC_TILE_BASE + s.key + '/{z}/{x}/{y}.png?v=' + SUSC_TILE_V],
            tileSize: 256,
            minzoom: 3,
            maxzoom: 10,
            attribution: s.attr
        };
    }
    function _suscLayerDef(s) {
        var cb = document.getElementById(s.cb);
        return {
            id: 'susc-' + s.key + '-layer',
            type: 'raster',
            source: 'susc-' + s.key,
            layout: { 'visibility': (cb && cb.checked) ? 'visible' : 'none' },
            paint: { 'raster-opacity': 1, 'raster-resampling': 'nearest' }
        };
    }
    function _faultsLayerDef() {
        return {
            id: 'faults-line', type: 'line', source: 'faults',
            layout: { 'visibility': (cbFaults && cbFaults.checked) ? 'visible' : 'none',
                      'line-cap': 'round', 'line-join': 'round' },
            paint: {
                'line-color': '#b5179e',
                'line-width': ['interpolate', ['linear'], ['zoom'], 4, 0.7, 10, 1.6, 14, 2.6],
                'line-opacity': ['match', ['get', 'FTYPE'], 'Inferred', 0.5, 0.85]
            }
        };
    }
    function _polygonHoverDef() {
        return {
            id: 'polygon-hover', type: 'line', source: 'polygons',
            filter: ['==', 'landslide_id', -1],
            paint: { 'line-color': '#fff', 'line-width': 2.5, 'line-opacity': 0.8 }
        };
    }
    function _surveyCircleLayerDefs() {
        var vis = (cbSurveyCircles && cbSurveyCircles.checked) ? 'visible' : 'none';
        return [
            {
                id: 'survey-circles-outline', type: 'line', source: 'survey-circles',
                layout: { 'visibility': vis },
                paint: {
                    'line-color': '#000',
                    'line-opacity': 0.85,
                    'line-width': ['case',
                        ['>', ['coalesce', ['get', 'update_total'], 0], 0], 2.2,
                        0.6
                    ]
                }
            },
            {
                id: 'survey-circles-label', type: 'symbol', source: 'survey-circles',
                filter: ['>', ['coalesce', ['get', 'update_total'], 0], 0],
                layout: {
                    'visibility': vis,
                    'text-field': ['to-string', ['get', 'update_total']],
                    'text-size': 12,
                    'text-allow-overlap': true
                },
                paint: {
                    'text-color': '#000',
                    'text-halo-color': '#fff',
                    'text-halo-width': 1.5
                }
            },
        ];
    }
    function _pendingLayerDefs() {
        return [
            { id: 'pending-poly-fill', type: 'fill', source: 'pending-poly-src',
              paint: { 'fill-color': '#d6219e', 'fill-opacity': 0.12 } },
            { id: 'pending-poly-line', type: 'line', source: 'pending-poly-src',
              paint: { 'line-color': '#d6219e', 'line-width': 2, 'line-dasharray': [3, 1.5] } },
            { id: 'pending-pt', type: 'circle', source: 'pending-pt-src',
              paint: { 'circle-radius': 6, 'circle-color': '#d6219e', 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 } },
        ];
    }

    // Add/re-add data sources and layers (called on initial load and after every basemap switch).
    //
    // Layer-order invariant: the measure-tool layers are kept at the top of the
    // stack. MeasureControl.onAdd hooks `style.load` so its layers are always
    // added before this function runs (`style.load` fires before `load`/`idle`).
    // Every addLayer call below passes beforeId=measure-fill so the new layer
    // is inserted *under* the measure stack. This makes the ordering structural
    // — no moveLayer chasing needed.
    function initDataLayers() {
        var bId = map.getLayer('measure-fill') ? 'measure-fill' : undefined;

        // USGS susceptibility overlays (lw / n10), pre-colored raster tiles.
        // Added first so the inventory data layers stack ON TOP. Initial
        // visibility reflects the toggle state so a basemap switch (which re-runs
        // initDataLayers) preserves the user's choice. Per-pixel alpha is baked
        // into the tiles, so raster-opacity stays at 1. Citation: USGS, Belair
        // et al. 2024 (Slope-Relief Threshold susceptibility, 90 m).
        SUSC_LAYERS.forEach(function (s) {
            map.addSource('susc-' + s.key, _suscSourceDef(s));
            map.addLayer(_suscLayerDef(s), bId);
        });

        // Alaska Quaternary faults & folds (DGGS QFF — Koehler 2013). Reference
        // vector overlay, ON by default. Added here so it sits below the
        // landslide layers (inventory stays on top). Loaded once from a static
        // GeoJSON; inferred traces drawn fainter. Click → fault attributes.
        map.addSource('faults', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        map.addLayer(_faultsLayerDef(), bId);
        if (_faultsData) map.getSource('faults').setData(_faultsData);

        // Editor trace-raster overlays sit here in the stack: above basemap +
        // susceptibility, below faults and all landslide data. Re-added after
        // every basemap switch, like everything else in this function.
        if (window._isInventoryEditor) _traceReplayLayers();

        map.addSource('landslides', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        map.addSource('polygons',   { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });

        var _ldefs = _landslideLayerDefs();
        map.addLayer(_ldefs[0], bId);   // points
        map.addLayer(_ldefs[1], bId);   // points-patchy (red center)
        ensurePinLabel();               // editor-only field labels (re-added with the layers)
        map.addLayer(_ldefs[2], bId);   // polygon-fill
        map.addLayer(_ldefs[3], bId);   // polygon-outline
        map.addLayer(_polygonHoverDef(), bId);

        // Survey-circles layer — black outline only (no fill); thin for
        // circles with no landslides identified (update_total=0), bold for
        // those with hits, plus a numerical label of update_total on the
        // hit circles. Togglable in the legend; visibility on layer creation
        // reflects the current checkbox state (the JS source of truth) so a
        // basemap switch doesn't clobber the user's choice.
        map.addSource('survey-circles',
            { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        _surveyCircleLayerDefs().forEach(function (def) { map.addLayer(def, bId); });
        if (_surveyCirclesData) map.getSource('survey-circles').setData(_surveyCirclesData);

        if (_featuresData) map.getSource('landslides').setData(_featuresData);
        buildFilter();
        onMoveEnd();
    }

    // ---------------------------------------------------------------------------
    // Basemap switching
    // ---------------------------------------------------------------------------
    function setBasemap(id) {
        var bm = findBasemap(id);
        if (!bm || id === _currentBasemap) return;
        // While a polygon is mid-draw, a basemap switch (setStyle) would wipe the
        // in-progress geometry. Staged components survive (re-added on style.load),
        // so switching between polygons is fine — only block during an open draw.
        if (map.__drawPolyOpen) {
            if (window.__drawFlash) window.__drawFlash('Finish or cancel the current polygon before switching imagery.');
            return;
        }
        _currentBasemap = id;
        // Visual selection across the three places it appears.
        document.querySelectorAll('.refmap-option').forEach(function (b) {
            b.classList.toggle('active', b.dataset.id === id);
        });
        var pinned = document.getElementById('pinned-basemap');
        if (pinned && pinned.value !== id) pinned.value = id;
        map.once('idle', function () {
            if (!map.getSource('landslides')) initDataLayers();
        });
        // Force a full reload (diff:false). Without this, MapLibre's default
        // diff between two raster styles (or two vector styles) preserves the
        // user-added landslide layers IN PLACE while inserting the new
        // basemap layers on top of them — clobbering our beforeId='measure-fill'
        // invariant. Full reload wipes sources+layers, then style.load fires
        // _ensureLayers, then idle fires initDataLayers with beforeId — clean.
        map.setStyle(bm.style ? bm.style : buildRasterStyle(bm), { diff: false });
        // Globe is re-asserted by the persistent 'style.load' handler above.
        if (_mapReady) writeHashState();
    }

    // ---------------------------------------------------------------------------
    // Sidebar shell: tabs + Reference-maps panel build-out + pinned basemap
    // ---------------------------------------------------------------------------
    // One basemap card (thumbnail + label; QMS cards get a remove ×).
    function _buildBasemapCard(bm) {
        var card = document.createElement('div');
        card.className = 'refmap-option' + (bm.id === _currentBasemap ? ' active' : '');
        card.dataset.id = bm.id;
        var tipLines = [bm.label];
        if (bm.category === 'Shared') tipLines.push('Shared with: ' + (bm.public ? 'everyone' : 'data admins'));
        if (bm.coverage) tipLines.push('Coverage: ' + bm.coverage);
        if (bm.attr)     tipLines.push(bm.attr);
        card.title = tipLines.join('\n');
        var thumbUrl = basemapThumbnailUrl(bm);
        if (thumbUrl) {
            var img = document.createElement('img');
            img.className = 'refmap-thumb'; img.alt = ''; img.loading = 'lazy'; img.src = thumbUrl;
            img.onerror = function () {
                var ph = document.createElement('div');
                ph.className = 'refmap-thumb-placeholder'; ph.textContent = bm.label;
                img.replaceWith(ph);
            };
            card.appendChild(img);
        } else {
            var ph = document.createElement('div');
            ph.className = 'refmap-thumb-placeholder'; ph.textContent = bm.label;
            card.appendChild(ph);
        }
        var lbl = document.createElement('div');
        lbl.className = 'refmap-label'; lbl.textContent = bm.label;
        card.appendChild(lbl);
        card.addEventListener('click', function () { setBasemap(bm.id); });
        var isSharedEditable = bm.category === 'Shared' && window._isInventoryEditor;
        if (bm._qms || isSharedEditable) {
            card.style.position = 'relative';
            var x = document.createElement('button');
            x.type = 'button'; x.textContent = '×';
            x.title = bm._qms ? 'Remove this QMS layer (just you)' : 'Stop sharing this layer with others';
            x.style.cssText = 'position:absolute;top:2px;right:2px;width:16px;height:16px;line-height:14px;' +
                'padding:0;border:none;border-radius:3px;background:rgba(0,0,0,.55);color:#fff;font-size:12px;cursor:pointer;';
            x.addEventListener('click', function (e) {
                e.stopPropagation();
                if (bm._qms) _removeQmsBasemap(bm.id);
                else _unpromoteQms(bm.qms_id);
            });
            card.appendChild(x);
        }
        // Editor: + on a locally-added QMS card → share it with other users.
        if (bm._qms && window._isInventoryEditor) {
            card.style.position = 'relative';
            var plus = document.createElement('button');
            plus.type = 'button'; plus.textContent = '+';
            plus.title = 'Make this layer available to other users';
            plus.style.cssText = 'position:absolute;top:2px;right:20px;width:16px;height:16px;line-height:14px;' +
                'padding:0;border:none;border-radius:3px;background:rgba(46,125,50,.85);color:#fff;font-size:14px;cursor:pointer;';
            plus.addEventListener('click', function (e) {
                e.stopPropagation();
                _showShareMenu(plus, bm.id.replace('qms-', ''));
            });
            card.appendChild(plus);
        }
        if (bm.category === 'Shared') {   // small scope tag
            var tag = document.createElement('div');
            tag.textContent = bm.public ? 'everyone' : 'admins';
            tag.style.cssText = 'position:absolute;bottom:2px;left:2px;font-size:9px;padding:0 3px;border-radius:2px;' +
                'background:rgba(0,0,0,.6);color:#fff;';
            card.style.position = 'relative';
            card.appendChild(tag);
        }
        return card;
    }

    // Rebuild the Reference-maps cards + the pinned dropdown from BASEMAPS.
    // Called once at startup and again whenever a QMS layer is added/removed.
    function rebuildBasemapUI() {
        var rm = document.getElementById('refmaps-content');
        if (rm) {
            rm.innerHTML = '';
            if (window._isInventoryEditor) rm.appendChild(_buildQmsSearchUI());
            REFMAPS_CATEGORY_ORDER.forEach(function (cat) {
                var inCat = BASEMAPS.filter(function (bm) { return bm.category === cat; });
                if (!inCat.length) return;
                var hdr = document.createElement('div');
                hdr.className = 'refmaps-category'; hdr.textContent = cat;
                rm.appendChild(hdr);
                var grid = document.createElement('div');
                grid.className = 'refmaps-grid';
                inCat.forEach(function (bm) { grid.appendChild(_buildBasemapCard(bm)); });
                rm.appendChild(grid);
            });
            rm.appendChild(_buildSwipeUI());   // swipe/compare controls
            if (window._isInventoryEditor) rm.appendChild(_buildTraceUI());   // GeoTIFF trace overlays
        }
        var pinned = document.getElementById('pinned-basemap');
        if (pinned) {
            pinned.innerHTML = '';
            BASEMAPS.forEach(function (bm) {
                var opt = document.createElement('option');
                opt.value = bm.id; opt.textContent = bm.label;
                if (bm.id === _currentBasemap) opt.selected = true;
                pinned.appendChild(opt);
            });
        }
    }

    // Editor-only QMS catalog search box (added at the top of the refmaps panel).
    // Sidebar entry: a button that opens the floating QMS browser (the sidebar
    // is too narrow for a comfortable results list). Added layers still land as
    // thumbnail cards in the Reference-maps panel.
    function _buildQmsSearchUI() {
        var wrap = document.createElement('div');
        wrap.style.cssText = 'margin-bottom:10px;';
        var hdr = document.createElement('div');
        hdr.className = 'refmaps-category'; hdr.textContent = 'Add layer · QuickMapServices';
        wrap.appendChild(hdr);
        var btn = document.createElement('button');
        btn.type = 'button'; btn.textContent = '🔍 Browse QMS layers…';
        btn.style.cssText = 'width:100%;font-size:12px;padding:6px 8px;border:1px solid #bbb;border-radius:3px;background:#f4f4f4;cursor:pointer;';
        btn.addEventListener('click', function () {
            _ensureQmsBox();
            if (_qmsFP) _qmsFP.open();
            var i = document.getElementById('qms-box-q'); if (i) i.focus();
        });
        wrap.appendChild(btn);
        return wrap;
    }

    // The floating QMS browser (created lazily on first open; reuses the shared
    // float-panel: draggable header + close + resize).
    var _qmsFP = null;
    function _ensureQmsBox() {
        var box = document.getElementById('qms-box');
        if (box) return box;
        box = document.createElement('div');
        box.id = 'qms-box';
        box.className = 'float-panel hidden';
        box.style.cssText = 'left:90px;top:80px;width:440px;height:480px;';
        box.innerHTML =
            '<div class="float-header" id="qms-box-header" style="display:flex;align-items:center;gap:8px;padding:7px 10px;background:#f0ebe9;border-bottom:1px solid #ddd;">' +
                '<span style="font-weight:600;font-size:13px;color:#5D4037;">QuickMapServices — add a layer</span>' +
                '<button id="qms-box-close" title="Close" style="margin-left:auto;border:none;background:none;font-size:18px;line-height:1;color:#666;cursor:pointer;">&times;</button>' +
            '</div>' +
            '<div style="padding:8px 10px 4px;display:flex;gap:6px;">' +
                '<input id="qms-box-q" type="text" placeholder="search… e.g. ESRI Satellite, topo, hillshade" ' +
                    'style="flex:1;font-size:13px;padding:4px 8px;border:1px solid #ccc;border-radius:3px;">' +
                '<button id="qms-box-go" type="button" style="font-size:13px;padding:4px 12px;border:1px solid #bbb;border-radius:3px;background:#f4f4f4;cursor:pointer;">Search</button>' +
            '</div>' +
            '<div style="padding:0 10px 6px;font-size:11px;color:#999;line-height:1.35;">Raster/TMS layers. Only EPSG:3857 aligns on this map (others flagged). Added layers appear in the Reference-maps panel.</div>' +
            '<div id="qms-box-results" style="flex:1;overflow:auto;border-top:1px solid #eee;"></div>';
        var host = (document.getElementById('hist-panel') && document.getElementById('hist-panel').parentNode)
                   || (document.getElementById('map') && document.getElementById('map').parentNode)
                   || document.body;
        host.appendChild(box);
        var q = box.querySelector('#qms-box-q'),
            go = box.querySelector('#qms-box-go'),
            res = box.querySelector('#qms-box-results');
        function run() {
            var query = q.value.trim();
            if (!query) return;
            res.innerHTML = '<div style="color:#888;padding:8px;font-size:12px;">searching…</div>';
            fetch(API_BASE + 'api/qms/?q=' + encodeURIComponent(query))
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    if (d.error) { res.innerHTML = '<div style="color:#c00;padding:8px;font-size:12px;">' + esc(d.error) + '</div>'; return; }
                    if (!d.results || !d.results.length) { res.innerHTML = '<div style="color:#888;padding:8px;font-size:12px;">no raster/TMS results</div>'; return; }
                    res.innerHTML = '';
                    d.results.forEach(function (it) { res.appendChild(_qmsBoxRow(it)); });
                })
                .catch(function () { res.innerHTML = '<div style="color:#c00;padding:8px;font-size:12px;">search failed</div>'; });
        }
        go.addEventListener('click', run);
        q.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); run(); } });
        _qmsFP = makeFloatingPanel(box, { handle: box.querySelector('#qms-box-header'), close: box.querySelector('#qms-box-close') });
        return box;
    }

    // One roomy result row: wrapping name + description/submitter + EPSG + Add.
    function _qmsBoxRow(it) {
        var row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:flex-start;gap:10px;padding:8px;border-bottom:1px solid #eee;';
        var info = document.createElement('div');
        info.style.cssText = 'flex:1;min-width:0;';
        var name = document.createElement('div');
        name.textContent = it.name;
        name.style.cssText = 'font-size:13px;color:#222;font-weight:500;line-height:1.25;word-break:break-word;';
        info.appendChild(name);
        var bits = [];
        if (it.desc) bits.push(it.desc);
        bits.push('QMS #' + it.id);
        if (it.submitter) bits.push('by ' + it.submitter);
        var meta = document.createElement('div');
        meta.textContent = bits.join('  ·  ');
        meta.style.cssText = 'font-size:11px;color:#999;margin-top:2px;line-height:1.3;word-break:break-word;';
        info.appendChild(meta);
        var col = document.createElement('div');
        col.style.cssText = 'display:flex;flex-direction:column;align-items:flex-end;gap:5px;flex:none;';
        var badge = document.createElement('span');
        badge.textContent = 'EPSG:' + it.epsg;
        badge.style.cssText = 'font-size:10px;padding:0 5px;border-radius:3px;white-space:nowrap;' +
            (it.compatible ? 'background:#e3f2e3;color:#2e7d32;' : 'background:#fdeede;color:#a15c00;');
        if (!it.compatible) badge.title = 'Not EPSG:3857 (or not working) — will misalign on this map';
        var add = document.createElement('button');
        add.type = 'button'; add.textContent = 'Add';
        add.style.cssText = 'font-size:12px;padding:2px 12px;border:1px solid #bbb;border-radius:3px;background:#f4f4f4;cursor:pointer;';
        add.addEventListener('click', function () {
            add.disabled = true; add.textContent = '…';
            fetch(API_BASE + 'api/qms/' + it.id + '/')
                .then(function (r) { return r.json(); })
                .then(function (det) {
                    if (det.error || !det.url) { add.textContent = 'err'; return; }
                    if (det.unsupported && det.unsupported.length) {
                        alert('"' + det.name + '" uses tile placeholders MapLibre can’t handle (' +
                              det.unsupported.join(' ') + ') — skipping.');
                        add.disabled = false; add.textContent = 'Add'; return;
                    }
                    if (!det.compatible &&
                        !confirm('"' + det.name + '" is EPSG:' + det.epsg + ', not 3857 — it will misalign ' +
                                 '(worse toward the poles, so noticeably in Alaska). Add anyway?')) {
                        add.disabled = false; add.textContent = 'Add'; return;
                    }
                    _addQmsBasemap(det);
                    add.textContent = '✓ added';
                })
                .catch(function () { add.disabled = false; add.textContent = 'Add'; });
        });
        col.appendChild(badge); col.appendChild(add);
        row.appendChild(info); row.appendChild(col);
        return row;
    }

    function _addQmsBasemap(det) {
        var id = 'qms-' + det.id;
        if (!findBasemap(id)) {
            var bm = { id: id, label: det.name, category: 'QMS',
                       coverage: 'EPSG:' + det.epsg + (det.reproject ? ' (reprojected)' : (det.compatible ? '' : ' — may misalign')),
                       tiles: det.url, attr: det.copyright_text || ('QMS #' + det.id),
                       scheme: det.scheme, reproject: det.reproject,
                       minzoom: det.z_min || 0, maxzoom: det.z_max || 19, _qms: true };
            BASEMAPS.push(bm);
            var saved = _loadQmsBasemaps(); saved.push(bm); _saveQmsBasemaps(saved);
            rebuildBasemapUI();
        }
        setBasemap(id);
    }

    function _removeQmsBasemap(id) {
        for (var i = 0; i < BASEMAPS.length; i++) {
            if (BASEMAPS[i].id === id) { BASEMAPS.splice(i, 1); break; }
        }
        _saveQmsBasemaps(_loadQmsBasemaps().filter(function (b) { return b.id !== id; }));
        if (_currentBasemap === id) setBasemap(DEFAULT_BASEMAP_ID);  // setBasemap re-runs the UI active state
        rebuildBasemapUI();
    }

    // ---- Shared (admin-curated) QMS layers: server-stored, scoped public/editors ----
    function _mergePromotedLayer(layer) {   // add or refresh one shared layer in BASEMAPS
        var i = BASEMAPS.findIndex ? BASEMAPS.findIndex(function (b) { return b.id === layer.id; }) : -1;
        if (i >= 0) BASEMAPS[i] = layer; else BASEMAPS.push(layer);
        rebuildBasemapUI();
    }

    // Load the shared set for the current user (public sees public ones; editors
    // also see editors-only). Public endpoint — called for everyone at startup.
    function _loadPromotedQms() {
        fetch(API_BASE + 'api/qms/promoted/')
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (!d || !d.layers || !d.layers.length) return;
                d.layers.forEach(function (l) { if (!findBasemap(l.id)) BASEMAPS.push(l); });
                rebuildBasemapUI();
            }).catch(function () {})
            .then(function () { _applyPendingSwipe(); });  // hash wiper on a now-merged shared layer
    }

    // Small popup menu off the card's + button: pick who to share with.
    function _showShareMenu(anchor, qmsId) {
        var old = document.getElementById('qms-share-menu');
        if (old) old.remove();
        var menu = document.createElement('div');
        menu.id = 'qms-share-menu';
        menu.style.cssText = 'position:fixed;z-index:30;background:#fff;border:1px solid #bbb;border-radius:4px;' +
            'box-shadow:0 2px 8px rgba(0,0,0,.25);font-size:12px;overflow:hidden;';
        var r = anchor.getBoundingClientRect();
        menu.style.left = Math.round(r.left) + 'px';
        menu.style.top  = Math.round(r.bottom + 3) + 'px';
        [['Data admins', false], ['Everyone', true]].forEach(function (opt) {
            var b = document.createElement('button');
            b.type = 'button'; b.textContent = 'Share → ' + opt[0];
            b.style.cssText = 'display:block;width:100%;text-align:left;padding:6px 14px;border:none;' +
                'background:#fff;cursor:pointer;white-space:nowrap;';
            b.addEventListener('mouseenter', function () { b.style.background = '#f0ebe9'; });
            b.addEventListener('mouseleave', function () { b.style.background = '#fff'; });
            b.addEventListener('click', function () { menu.remove(); _shareLocalQms(qmsId, opt[1]); });
            menu.appendChild(b);
        });
        document.body.appendChild(menu);
        setTimeout(function () {
            document.addEventListener('click', function onDoc(ev) {
                if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener('click', onDoc); }
            });
        }, 0);
    }

    // Promote a locally-added QMS layer, then fold the local copy into the shared
    // one (same tiles → no reload if it was the active basemap).
    function _shareLocalQms(qmsId, isPublic) {
        _promoteQms(qmsId, isPublic, function (err) {
            if (err) { alert(err); return; }
            var localId = 'qms-' + qmsId, sharedId = 'qmsshared-' + qmsId;
            var wasCurrent = _currentBasemap === localId;
            for (var i = 0; i < BASEMAPS.length; i++) { if (BASEMAPS[i].id === localId) { BASEMAPS.splice(i, 1); break; } }
            _saveQmsBasemaps(_loadQmsBasemaps().filter(function (b) { return b.id !== localId; }));
            if (wasCurrent) _currentBasemap = sharedId;   // identical tiles; just move the selection
            rebuildBasemapUI();
        });
    }

    // Promote a QMS service to the shared set (editor-only).
    function _promoteQms(qmsId, isPublic, onDone) {
        fetch(API_BASE + 'api/qms/promote/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': window.CSRF_TOKEN },
            body: JSON.stringify({ qms_id: qmsId, public: !!isPublic })
        }).then(function (r) { return r.json(); })
          .then(function (j) {
            if (j.error || !j.layer) { onDone(j.error || 'failed'); return; }
            _mergePromotedLayer(j.layer);
            onDone(null);
          }).catch(function () { onDone('failed'); });
    }

    function _unpromoteQms(qmsId) {
        if (!confirm('Stop sharing this layer with others?')) return;
        fetch(API_BASE + 'api/qms/' + qmsId + '/unpromote/', {
            method: 'POST', headers: { 'X-CSRFToken': window.CSRF_TOKEN }
        }).then(function (r) { return r.json(); })
          .then(function () {
            var id = 'qmsshared-' + qmsId;
            for (var i = 0; i < BASEMAPS.length; i++) { if (BASEMAPS[i].id === id) { BASEMAPS.splice(i, 1); break; } }
            if (_currentBasemap === id) setBasemap(DEFAULT_BASEMAP_ID);
            rebuildBasemapUI();
          }).catch(function () {});
    }

    // ---------------------------------------------------------------------------
    // Swipe / wipe comparison: a second, view-synced map (basemap B + the same
    // landslide layers) clipped to one side of a draggable divider, stacked over
    // the main map. Data is drawn on BOTH maps so it reads continuous across the
    // divider; the comparison map is pointer-events:none so clicks fall through
    // to the main map. Reuses the shared basemap + color modules and the active
    // filter, so toggling classes/types off gives a clean image-only swipe.
    // ---------------------------------------------------------------------------
    var _swipe = { map: null, container: null, divider: null, basemapId: null, x: 50, on: false };
    var _swipeFilter = null;        // last filter expression (applied to a newly-enabled swipe map)

    function _swipeSetFeatures(data) {
        if (_swipe.map && _swipe.map.getSource('landslides')) _swipe.map.getSource('landslides').setData(data);
    }
    function _swipeSetPolygons(data) {
        if (_swipe.map && _swipe.map.getSource('polygons')) _swipe.map.getSource('polygons').setData(data);
    }
    // Single source of truth for the class/flag-filterable landslide layers, so
    // every filter site (main map, hide-all, swipe mirror) stays in lock-step.
    // A compound-symbol sublayer carries a `base` filter AND-combined with the
    // active user filter — adding a future sublayer here makes it participate in
    // filtering automatically (no hand-maintained id lists to forget).
    function _landslideFilterLayers() {
        return [
            { id: 'points' },
            { id: 'points-patchy', base: window.LSColors.PATCHY_FILTER },
            { id: 'polygon-fill' },
            { id: 'polygon-outline' },
            { id: 'pin-label' },
        ];
    }
    function _applyLandslideFilter(m, userFilter) {
        if (!m) return;
        _landslideFilterLayers().forEach(function (s) {
            if (!m.getLayer(s.id)) return;   // e.g. pin-label when no field is pinned
            m.setFilter(s.id, s.base ? ['all', s.base, userFilter] : userFilter);
        });
    }
    function _swipeSetFilter(f) {
        _applyLandslideFilter(_swipe.map, f);
    }
    // Run fn against the comparison map too (when it exists) — used by every
    // visibility toggle / data load / hover site so both panes stay in step.
    function _swipeAlso(fn) {
        if (_swipe.map) fn(_swipe.map);
    }
    // Build the comparison map's data stack: the SAME defs in the SAME relative
    // order as the main map, so the display reads seamlessly across the divider
    // (same defs = same paint, same order = same occlusion). Main-map order,
    // bottom→top: pending (added at style.load there) → susc rasters → faults →
    // points → points-patchy → polygon-fill → polygon-outline → polygon-hover →
    // survey circles → pin-label. Interaction-only layers (measure, draw draft)
    // are deliberately not mirrored — the basemap locks while they're active.
    function _swipeAddData(cmap) {
        if (!_palette) return;   // initLayers hasn't run yet — re-invoked from there
        if (window._isInventoryEditor) {
            if (!cmap.getSource('pending-poly-src')) cmap.addSource('pending-poly-src', { type: 'geojson', data: _pendingData.polygons });
            if (!cmap.getSource('pending-pt-src'))   cmap.addSource('pending-pt-src',   { type: 'geojson', data: _pendingData.points });
            _pendingLayerDefs().forEach(function (def) { if (!cmap.getLayer(def.id)) cmap.addLayer(def); });
        }
        SUSC_LAYERS.forEach(function (s) {
            if (!cmap.getSource('susc-' + s.key)) cmap.addSource('susc-' + s.key, _suscSourceDef(s));
            if (!cmap.getLayer('susc-' + s.key + '-layer')) cmap.addLayer(_suscLayerDef(s));
        });
        if (!cmap.getSource('faults'))
            cmap.addSource('faults', { type: 'geojson', data: _faultsData || { type: 'FeatureCollection', features: [] } });
        if (!cmap.getLayer('faults-line')) cmap.addLayer(_faultsLayerDef());
        if (!cmap.getSource('landslides'))
            cmap.addSource('landslides', { type: 'geojson', data: _featuresData || { type: 'FeatureCollection', features: [] } });
        if (!cmap.getSource('polygons'))
            cmap.addSource('polygons', { type: 'geojson', data: _polygonsData || { type: 'FeatureCollection', features: [] } });
        _landslideLayerDefs().forEach(function (def) { if (!cmap.getLayer(def.id)) cmap.addLayer(def); });
        if (!cmap.getLayer('polygon-hover')) cmap.addLayer(_polygonHoverDef());
        if (!cmap.getSource('survey-circles'))
            cmap.addSource('survey-circles', { type: 'geojson', data: _surveyCirclesData || { type: 'FeatureCollection', features: [] } });
        _surveyCircleLayerDefs().forEach(function (def) { if (!cmap.getLayer(def.id)) cmap.addLayer(def); });
        _ensurePinLabelOn(cmap);   // editor pinned-field labels (top, like the main map)
        if (_swipeFilter) _swipeSetFilter(_swipeFilter);
    }
    function _swipeSetPending() {
        if (!_swipe.map) return;
        if (_swipe.map.getSource('pending-poly-src')) _swipe.map.getSource('pending-poly-src').setData(_pendingData.polygons);
        if (_swipe.map.getSource('pending-pt-src'))   _swipe.map.getSource('pending-pt-src').setData(_pendingData.points);
    }
    function _swipeSyncView() {
        if (!_swipe.map || !_swipe.on) return;
        _swipe.map.jumpTo({ center: map.getCenter(), zoom: map.getZoom(),
                            bearing: map.getBearing(), pitch: map.getPitch() });
    }
    function _swipeSetX(x) {
        _swipe.x = Math.max(0, Math.min(100, x));
        if (_swipe.container) _swipe.container.style.clipPath = 'inset(0 0 0 ' + _swipe.x + '%)';
        if (_swipe.divider) _swipe.divider.style.left = _swipe.x + '%';
    }
    function _swipeStyleFor(bm) { return bm.style ? bm.style : buildRasterStyle(bm); }

    function _swipeEnsure() {
        if (_swipe.map) return;
        var host = document.getElementById('map').parentNode;
        var cont = document.createElement('div');
        cont.id = 'swipe-map';
        cont.style.cssText = 'position:absolute;top:0;left:0;right:0;bottom:0;z-index:2;pointer-events:none;';
        cont.style.clipPath = 'inset(0 0 0 ' + _swipe.x + '%)';
        host.appendChild(cont);
        _swipe.container = cont;

        var divr = document.createElement('div');
        divr.id = 'swipe-divider';
        divr.style.cssText = 'position:absolute;top:0;bottom:0;left:' + _swipe.x + '%;width:3px;margin-left:-1.5px;' +
            'background:#fff;box-shadow:0 0 3px rgba(0,0,0,.6);z-index:3;cursor:ew-resize;touch-action:none;';
        var handle = document.createElement('div');
        handle.style.cssText = 'position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:26px;height:26px;' +
            'border-radius:50%;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.4);color:#5D4037;font-size:13px;' +
            'display:flex;align-items:center;justify-content:center;';
        handle.textContent = '⇄';
        divr.appendChild(handle);
        host.appendChild(divr);
        _swipe.divider = divr;
        var dragging = false;
        divr.addEventListener('pointerdown', function (e) { dragging = true; divr.setPointerCapture(e.pointerId); e.preventDefault(); });
        divr.addEventListener('pointermove', function (e) {
            if (!dragging) return;
            var r = host.getBoundingClientRect();
            _swipeSetX((e.clientX - r.left) / r.width * 100);
        });
        divr.addEventListener('pointerup', function () {
            dragging = false;
            if (_mapReady) writeHashState();   // capture the divider position (sx=)
        });

        var bm = findBasemap(_swipe.basemapId) || findBasemap(DEFAULT_BASEMAP_ID);
        _swipe.basemapId = bm.id;
        var cmap = new maplibregl.Map({
            container: cont, style: _swipeStyleFor(bm),
            center: map.getCenter(), zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch(),
            interactive: false, attributionControl: false,
            transformRequest: LSBasemaps.transformRequest,
        });
        _swipe.map = cmap;
        cmap.on('style.load', function () {
            if (typeof cmap.setProjection === 'function') { try { cmap.setProjection({ type: 'globe' }); } catch (e) {} }
            _swipeAddData(cmap);
        });
        map.on('move', _swipeSyncView);
        map.on('resize', function () { if (_swipe.map) _swipe.map.resize(); });
    }

    function _swipeEnable(basemapId) {
        var hadMap = !!_swipe.map;
        if (basemapId) _swipe.basemapId = basemapId;
        _swipeEnsure();
        if (hadMap && basemapId) _swipeSetBasemap(basemapId);   // existing map → switch its basemap
        _swipe.on = true;
        if (_swipe.container) _swipe.container.style.display = '';
        if (_swipe.divider) _swipe.divider.style.display = '';
        _swipeSyncView();
        if (_swipe.map) _swipe.map.resize();
        if (_mapReady) writeHashState();
    }
    function _swipeDisable() {
        _swipe.on = false;
        if (_swipe.container) _swipe.container.style.display = 'none';
        if (_swipe.divider) _swipe.divider.style.display = 'none';
        if (_mapReady) writeHashState();
    }
    function _swipeSetBasemap(id) {
        var bm = findBasemap(id);
        _swipe.basemapId = id;
        if (bm && _swipe.map) _swipe.map.setStyle(_swipeStyleFor(bm), { diff: false });  // style.load re-adds data
    }
    // Keep the Compare (swipe) dropdown in step when swipe state changes
    // programmatically (hash restore, default-view apply).
    function _syncSwipeSelect() {
        var sel = document.getElementById('swipe-select');
        if (sel) sel.value = _swipe.on ? (_swipe.basemapId || '') : '';
    }
    // Restore a wiper carried in the URL hash (or the saved localStorage view).
    // Called at startup and again after promoted QMS layers merge — a shared
    // layer referenced by the hash isn't findable until then.
    function _applyPendingSwipe() {
        if (!_pendingSwipe) return;
        var bm = findBasemap(_pendingSwipe.base);
        if (!bm) return;   // maybe a promoted QMS layer still loading — retried later
        var x = _pendingSwipe.x;
        _pendingSwipe = null;
        _swipeSetX(x);
        _swipeEnable(bm.id);
        _syncSwipeSelect();
    }

    // ---------------------------------------------------------------------------
    // View-state strings — the hash format (`map=z/lat/lon&base=…&swipe=…&sx=…`,
    // no leading '#') doubling as a landslide's stored default view.
    // ---------------------------------------------------------------------------
    function _currentViewString() {
        var c = map.getCenter(), z = map.getZoom();
        // Unlike writeHashState, always pin the basemap: a curated view should
        // reproduce its imagery even if the site default changes later.
        var parts = ['map=' + z.toFixed(2) + '/' + c.lat.toFixed(4) + '/' + c.lng.toFixed(4),
                     'base=' + _currentBasemap];
        if (_swipe.on && _swipe.basemapId) {
            parts.push('swipe=' + _swipe.basemapId);
            parts.push('sx=' + Math.round(_swipe.x));
        }
        return parts.join('&');
    }
    // Fully apply a stored view: basemap, wiper (off if the view has none),
    // then fly to its center/zoom.
    function applyViewString(v) {
        var s = parseHashState('#' + v);
        if (s.base && s.base !== _currentBasemap && findBasemap(s.base)) setBasemap(s.base);
        if (s.swipe && findBasemap(s.swipe)) {
            if (s.sx != null) _swipeSetX(s.sx);
            _swipeEnable(s.swipe);
        } else {
            _swipeDisable();
        }
        _syncSwipeSelect();
        if (s.lat != null && s.lon != null && s.zoom != null) {
            map.flyTo({ center: [s.lon, s.lat], zoom: s.zoom });
        }
    }

    function _buildSwipeUI() {
        var wrap = document.createElement('div');
        wrap.style.cssText = 'margin-top:12px;';
        var hdr = document.createElement('div');
        hdr.className = 'refmaps-category'; hdr.textContent = 'Compare (swipe)';
        wrap.appendChild(hdr);
        var hint = document.createElement('div');
        hint.style.cssText = 'font-size:11px;color:#777;margin-bottom:5px;line-height:1.35;';
        hint.textContent = 'Add a wiper-comparison base-map on the right.';
        wrap.appendChild(hint);
        var sel = document.createElement('select');
        sel.id = 'swipe-select';
        sel.style.cssText = 'width:100%;font-size:12px;padding:3px 4px;border:1px solid #ccc;border-radius:3px;';
        var none = document.createElement('option');
        none.value = ''; none.textContent = 'none';
        if (!_swipe.on) none.selected = true;
        sel.appendChild(none);
        BASEMAPS.forEach(function (bm) {
            var opt = document.createElement('option');
            opt.value = bm.id; opt.textContent = bm.label;
            if (_swipe.on && bm.id === _swipe.basemapId) opt.selected = true;
            sel.appendChild(opt);
        });
        wrap.appendChild(sel);
        sel.addEventListener('change', function () {
            if (!sel.value) _swipeDisable(); else _swipeEnable(sel.value);
        });
        return wrap;
    }

    // ---------------------------------------------------------------------------
    // Traced imagery — editor-uploaded GeoTIFF overlays (trace rasters).
    // Upload → the server bakes an XYZ pyramid in the background → the overlay
    // renders under the landslide layers so geometry can be traced in-app with
    // the ✏ draw tool. Editor-only end to end: the registry fetch is gated on
    // _isInventoryEditor, tiles are auth-checked server-side, and none of it
    // exists on the public map or in snapshots. Deliberately NOT mirrored to
    // the swipe pane (unlike inventory data): the overlay is *imagery*, so
    // keeping it main-pane-only makes the wiper a before/after comparison
    // against any basemap.
    // ---------------------------------------------------------------------------
    var _traceRasters = [];   // rows from api/trace_rasters/
    var _traceActive  = {};   // id -> opacity (0..1); presence = overlay enabled
    var _tracePolls   = {};   // id -> setInterval handle while processing
    var _traceListOpen = false;   // uploads list collapsed by default; sticky per session

    function _traceRow(id) {
        for (var i = 0; i < _traceRasters.length; i++)
            if (_traceRasters[i].id === id) return _traceRasters[i];
        return null;
    }
    function _traceAddLayer(id) {
        var r = _traceRow(id);
        if (!r || r.status !== 'ready' || r.bounds_w == null) return;
        var srcId = 'trace-src-' + id, lyrId = 'trace-' + id;
        if (!map.getSource(srcId)) {
            map.addSource(srcId, {
                type: 'raster',
                tiles: [API_BASE + 'tiles/trace/' + id + '/{z}/{x}/{y}.png'],
                tileSize: 256, minzoom: r.min_zoom, maxzoom: r.max_zoom,
                bounds: [r.bounds_w, r.bounds_s, r.bounds_e, r.bounds_n]
            });
        }
        if (!map.getLayer(lyrId)) {
            // The image goes at the very BOTTOM of the data stack — below
            // pending magenta too (pending is the lowest data layer, so a
            // freshly-traced-and-committed record must still draw on top of
            // the image it was traced from), then susc, faults, and every
            // landslide layer above.
            var beforeId;
            ['pending-poly-fill']
                .concat(SUSC_LAYERS.map(function (s) { return 'susc-' + s.key + '-layer'; }))
                .concat(['faults-line', 'points'])
                .some(function (cand) {
                    if (map.getLayer(cand)) { beforeId = cand; return true; }
                    return false;
                });
            map.addLayer({
                id: lyrId, type: 'raster', source: srcId,
                paint: { 'raster-opacity': (_traceActive[id] != null ? _traceActive[id] : 1) }
            }, beforeId);
        }
    }
    function _traceRemoveLayer(id) {
        if (map.getLayer('trace-' + id)) map.removeLayer('trace-' + id);
        if (map.getSource('trace-src-' + id)) map.removeSource('trace-src-' + id);
    }
    // Re-add enabled overlays after a basemap switch (called by initDataLayers,
    // right after faults-line exists so the insertion point is stable).
    function _traceReplayLayers() {
        Object.keys(_traceActive).forEach(function (id) { _traceAddLayer(+id); });
    }
    function _traceZoomTo(r) {
        if (r.bounds_w == null) return;
        map.fitBounds([[r.bounds_w, r.bounds_s], [r.bounds_e, r.bounds_n]],
                      { padding: 60, maxZoom: r.max_zoom || 16 });
    }
    // Thumbnail for a baked raster: its center tile at the zoom where the
    // image roughly fills one tile (slippy math as in basemaps.js). The tile
    // is guaranteed cached-immutable; a missing one (nodata center) just
    // hides the <img> via onerror.
    function _traceThumbUrl(r) {
        if (r.bounds_w == null || r.min_zoom == null) return null;
        var lonSpan = Math.max(1e-6, r.bounds_e - r.bounds_w);
        var z = Math.max(r.min_zoom, Math.min(r.max_zoom, Math.floor(Math.log2(360 / lonSpan))));
        var lon = (r.bounds_w + r.bounds_e) / 2;
        var lat = (r.bounds_s + r.bounds_n) / 2;
        var n = Math.pow(2, z);
        var x = Math.floor((lon + 180) / 360 * n);
        var y = Math.floor((1 - Math.asinh(Math.tan(lat * Math.PI / 180)) / Math.PI) / 2 * n);
        return API_BASE + 'tiles/trace/' + r.id + '/' + z + '/' + x + '/' + y + '.png';
    }
    function _tracePost(url, body) {
        var opts = { method: 'POST', headers: { 'X-CSRFToken': window.CSRF_TOKEN } };
        if (body) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
        return fetch(url, opts).then(function (res) {
            return res.json().then(function (j) { return { ok: res.ok, j: j }; });
        });
    }
    function _traceReplaceRow(row) {
        for (var i = 0; i < _traceRasters.length; i++) {
            if (_traceRasters[i].id === row.id) { _traceRasters[i] = row; return; }
        }
        _traceRasters.unshift(row);
    }
    function _tracePoll(id) {
        if (_tracePolls[id]) return;
        _tracePolls[id] = setInterval(function () {
            fetch(API_BASE + 'api/trace_rasters/' + id + '/status/')
                .then(function (res) { return res.ok ? res.json() : null; })
                .then(function (row) {
                    if (!row || !row.id) return;
                    _traceReplaceRow(row);
                    if (row.status !== 'processing') {
                        clearInterval(_tracePolls[id]);
                        delete _tracePolls[id];
                        if (row.status === 'ready') {
                            _traceActive[id] = 1;   // fresh bake → show it and go there
                            _traceAddLayer(id);
                            _traceZoomTo(row);
                        }
                        _renderTraceRows();
                    }
                }).catch(function () {});
        }, 2500);
    }
    function _traceLoad() {
        if (!window._isInventoryEditor) return;
        fetch(API_BASE + 'api/trace_rasters/')
            .then(function (res) { return res.json(); })
            .then(function (d) {
                _traceRasters = (d && d.rasters) || [];
                _renderTraceRows();
                _traceRasters.forEach(function (r) {
                    if (r.status === 'processing' && !r.stalled) _tracePoll(r.id);
                });
            }).catch(function () {});
    }

    var _TRACE_BTN_CSS = 'font-size:11px;padding:1px 6px;border:1px solid #bbb;' +
                         'border-radius:3px;background:#fff;cursor:pointer;';

    // Summary line on the collapsed uploads list — kept current from every
    // state change (enable/disable, upload, poll completion, delete).
    function _traceUpdateSummary() {
        var sum = document.getElementById('trace-list-summary');
        if (!sum) return;
        if (!_traceRasters.length) { sum.textContent = 'No uploads yet'; return; }
        var shown = Object.keys(_traceActive).length;
        var txt = _traceRasters.length + ' upload' + (_traceRasters.length === 1 ? '' : 's');
        if (shown) txt += ' · ' + shown + ' on map';
        if (_traceRasters.some(function (r) { return r.status === 'processing' && !r.stalled; }))
            txt += ' · processing…';
        sum.textContent = txt;
    }
    function _traceSetListOpen(open) {
        _traceListOpen = open;
        var det = document.getElementById('trace-list-details');
        if (det) det.open = open;
    }

    function _renderTraceRows() {
        _traceUpdateSummary();
        var box = document.getElementById('trace-imagery-rows');
        if (!box) return;
        box.innerHTML = '';
        if (!_traceRasters.length) {
            box.innerHTML = '<div style="font-size:11px;color:#999;">No uploads yet.</div>';
            return;
        }
        _traceRasters.forEach(function (r) {
            var row = document.createElement('div');
            row.style.cssText = 'padding:5px 6px;border:1px solid #e0dcd8;border-radius:4px;' +
                                'margin-bottom:5px;font-size:12px;background:#fff;';
            var top = document.createElement('div');
            top.style.cssText = 'display:flex;align-items:center;gap:5px;';

            var cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = _traceActive[r.id] != null;
            cb.disabled = r.status !== 'ready';
            cb.title = 'Show on map';
            cb.addEventListener('change', function () {
                if (cb.checked) {
                    if (_traceActive[r.id] == null) _traceActive[r.id] = 1;
                    _traceAddLayer(r.id);
                } else {
                    delete _traceActive[r.id];
                    _traceRemoveLayer(r.id);
                }
                _traceUpdateSummary();
            });
            top.appendChild(cb);

            var lbl = document.createElement('span');
            lbl.style.cssText = 'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
            lbl.textContent = r.title + (r.image_date ? ' · ' + r.image_date : '');
            var tipLines = [r.title];
            if (r.image_date)   tipLines.push('Image date: ' + r.image_date);
            if (r.source_note)  tipLines.push(r.source_note);
            if (r.uploaded_by)  tipLines.push('Uploaded by ' + r.uploaded_by + ' ' + r.created_at.slice(0, 10));
            if (r.tile_count)   tipLines.push(r.tile_count + ' tiles, z' + r.min_zoom + '–' + r.max_zoom);
            lbl.title = tipLines.join('\n');
            top.appendChild(lbl);

            if (r.status === 'processing') {
                var st = document.createElement('span');
                st.style.cssText = 'font-size:11px;color:' + (r.stalled ? '#c00' : '#1a73e8') + ';';
                st.textContent = r.stalled ? 'stalled' : 'processing…';
                top.appendChild(st);
            } else if (r.status === 'error') {
                var er = document.createElement('span');
                er.style.cssText = 'font-size:11px;color:#c00;cursor:help;';
                er.textContent = '✖ failed';
                er.title = r.error || 'bake failed';
                top.appendChild(er);
            }

            function btn(txt, title, fn) {
                var b = document.createElement('button');
                b.type = 'button'; b.textContent = txt; b.title = title;
                b.style.cssText = _TRACE_BTN_CSS;
                b.addEventListener('click', fn);
                top.appendChild(b);
                return b;
            }
            if (r.status === 'ready') {
                btn('⌖', 'Zoom to this image', function () { _traceZoomTo(r); });
            }
            if (r.status === 'error' || r.stalled) {
                btn('⟳', 'Re-bake from the uploaded original', function () {
                    _tracePost(API_BASE + 'api/trace_rasters/' + r.id + '/rebuild/')
                        .then(function (res) {
                            if (res.ok && res.j.ok) {
                                r.status = 'processing'; r.stalled = false; r.error = null;
                                _renderTraceRows();
                                _tracePoll(r.id);
                            } else alert((res.j && res.j.error) || 'rebuild failed');
                        });
                });
            }
            btn('×', 'Delete this upload (tiles + original)', function () {
                if (!confirm('Delete "' + r.title + '" — tiles and the uploaded original?')) return;
                _tracePost(API_BASE + 'api/trace_rasters/' + r.id + '/delete/')
                    .then(function (res) {
                        if (res.ok && res.j.ok) {
                            delete _traceActive[r.id];
                            _traceRemoveLayer(r.id);
                            _traceRasters = _traceRasters.filter(function (x) { return x.id !== r.id; });
                            _renderTraceRows();
                        } else alert((res.j && res.j.error) || 'delete failed');
                    });
            });
            row.appendChild(top);

            if (r.status === 'ready') {
                var line2 = document.createElement('div');
                line2.style.cssText = 'display:flex;align-items:center;gap:6px;margin-top:3px;';
                var opLbl = document.createElement('span');
                opLbl.style.cssText = 'font-size:10px;color:#777;';
                opLbl.textContent = 'opacity';
                var op = document.createElement('input');
                op.type = 'range'; op.min = '0'; op.max = '100';
                op.value = String(Math.round((_traceActive[r.id] != null ? _traceActive[r.id] : 1) * 100));
                op.style.cssText = 'flex:1;height:14px;';
                op.title = 'Dim the image against the basemap while tracing';
                op.addEventListener('input', function () {
                    var v = (+op.value) / 100;
                    if (_traceActive[r.id] != null) {
                        _traceActive[r.id] = v;
                        if (map.getLayer('trace-' + r.id))
                            map.setPaintProperty('trace-' + r.id, 'raster-opacity', v);
                    }
                });
                line2.appendChild(opLbl);
                line2.appendChild(op);

                // Provenance link: which landslide this image was traced into.
                var link = document.createElement('span');
                link.style.cssText = 'font-size:10px;white-space:nowrap;';
                function renderLink() {
                    link.innerHTML = '';
                    if (r.landslide_id) {
                        var a = document.createElement('a');
                        a.href = '#id=' + r.landslide_id;
                        a.textContent = '→ #' + r.landslide_id;
                        a.title = 'Traced into landslide #' + r.landslide_id + ' — click to open';
                        a.style.cssText = 'color:#1a5fb4;';
                        var un = document.createElement('button');
                        un.type = 'button'; un.textContent = '✕';
                        un.title = 'Clear this link';
                        un.style.cssText = 'border:none;background:none;color:#999;cursor:pointer;font-size:10px;padding:0 2px;';
                        un.addEventListener('click', function () { saveLink(null); });
                        link.appendChild(a); link.appendChild(un);
                    } else {
                        var lb = document.createElement('button');
                        lb.type = 'button'; lb.textContent = '⚲ link';
                        lb.title = 'Record which landslide this image was traced into '
                                 + '(open that landslide’s info panel first)';
                        lb.style.cssText = 'border:none;background:none;color:#1a5fb4;cursor:pointer;font-size:10px;padding:0;';
                        lb.addEventListener('click', function () {
                            if (!_lastDetail) { alert('Open the landslide’s info panel first, then click link.'); return; }
                            if (!confirm('Link "' + r.title + '" to ' + _lastDetail.name + ' (#' + _lastDetail.id + ')?')) return;
                            saveLink(_lastDetail.id);
                        });
                        link.appendChild(lb);
                    }
                }
                function saveLink(lid) {
                    _tracePost(API_BASE + 'api/trace_rasters/' + r.id + '/link/', { landslide_id: lid })
                        .then(function (res) {
                            if (res.ok && res.j.ok) { r.landslide_id = lid; renderLink(); }
                            else alert((res.j && res.j.error) || 'link failed');
                        });
                }
                renderLink();
                line2.appendChild(link);
                row.appendChild(line2);
            }

            box.appendChild(row);
        });
    }

    function _buildTraceUI() {
        var wrap = document.createElement('div');
        wrap.style.cssText = 'margin-top:12px;';
        var hdr = document.createElement('div');
        hdr.className = 'refmaps-category';
        hdr.textContent = 'Traced imagery (uploads)';
        wrap.appendChild(hdr);
        var hint = document.createElement('div');
        hint.style.cssText = 'font-size:11px;color:#777;margin-bottom:5px;line-height:1.35;';
        hint.textContent = 'Upload a georeferenced GeoTIFF, then trace it with the ✏ draw tool. Data admins only.';
        wrap.appendChild(hint);

        // Uploads list — collapsed by default so a growing library doesn't
        // swamp the panel; the summary line carries the counts. Upload stays
        // visible below regardless.
        var listDet = document.createElement('details');
        listDet.id = 'trace-list-details';
        listDet.open = _traceListOpen;
        var listSum = document.createElement('summary');
        listSum.id = 'trace-list-summary';
        listSum.style.cssText = 'cursor:pointer;font-size:12px;color:#555;margin-bottom:4px;';
        listSum.textContent = 'No uploads yet';
        listDet.appendChild(listSum);
        listDet.addEventListener('toggle', function () { _traceListOpen = listDet.open; });
        var rows = document.createElement('div');
        rows.id = 'trace-imagery-rows';
        listDet.appendChild(rows);
        wrap.appendChild(listDet);

        var det = document.createElement('details');
        var sum = document.createElement('summary');
        sum.textContent = '＋ Upload GeoTIFF…';
        sum.style.cssText = 'cursor:pointer;font-size:12px;color:#1a5fb4;';
        det.appendChild(sum);
        var form = document.createElement('div');
        form.style.cssText = 'display:flex;flex-direction:column;gap:4px;margin-top:5px;';
        var inpCss = 'font-size:12px;padding:3px 4px;border:1px solid #ccc;border-radius:3px;width:100%;box-sizing:border-box;';
        var titleInp = document.createElement('input');
        titleInp.type = 'text'; titleInp.placeholder = 'Title (defaults to filename)';
        titleInp.style.cssText = inpCss;
        var dateInp = document.createElement('input');
        dateInp.type = 'date';
        dateInp.title = 'Image capture date — your date-bracketing evidence';
        dateInp.style.cssText = inpCss;
        var srcInp = document.createElement('input');
        srcInp.type = 'text'; srcInp.placeholder = 'Source note (e.g. PlanetScope scene id)';
        srcInp.style.cssText = inpCss;
        var fileInp = document.createElement('input');
        fileInp.type = 'file'; fileInp.accept = '.tif,.tiff,image/tiff';
        fileInp.style.cssText = 'font-size:11px;';
        var goBtn = document.createElement('button');
        goBtn.type = 'button'; goBtn.textContent = 'Upload';
        goBtn.style.cssText = _TRACE_BTN_CSS + 'align-self:flex-start;padding:3px 12px;';
        var stat = document.createElement('span');
        stat.style.cssText = 'font-size:11px;';
        form.appendChild(titleInp); form.appendChild(dateInp);
        form.appendChild(srcInp); form.appendChild(fileInp);
        form.appendChild(goBtn); form.appendChild(stat);
        det.appendChild(form);
        wrap.appendChild(det);

        goBtn.addEventListener('click', function () {
            var f = fileInp.files && fileInp.files[0];
            if (!f) { stat.style.color = '#c00'; stat.textContent = 'Choose a GeoTIFF first.'; return; }
            var fd = new FormData();
            fd.append('file', f);
            fd.append('title', titleInp.value.trim());
            fd.append('image_date', dateInp.value);
            fd.append('source_note', srcInp.value.trim());
            goBtn.disabled = true;
            stat.style.color = '#1a73e8'; stat.textContent = 'uploading…';
            fetch(API_BASE + 'api/trace_rasters/upload/', {
                method: 'POST', headers: { 'X-CSRFToken': window.CSRF_TOKEN }, body: fd
            }).then(function (res) { return res.json().then(function (j) { return { ok: res.ok, j: j }; }); })
              .then(function (res) {
                goBtn.disabled = false;
                if (res.ok && res.j.ok) {
                    stat.textContent = '';
                    titleInp.value = ''; srcInp.value = ''; dateInp.value = ''; fileInp.value = '';
                    det.open = false;
                    _traceReplaceRow(res.j.raster);
                    _traceSetListOpen(true);   // show the new row's processing status
                    _renderTraceRows();
                    _tracePoll(res.j.id);
                } else {
                    stat.style.color = '#c00';
                    stat.textContent = (res.j && res.j.error) || 'upload failed';
                }
            }).catch(function () {
                goBtn.disabled = false;
                stat.style.color = '#c00'; stat.textContent = 'upload failed';
            });
        });

        _renderTraceRows();   // populate the freshly-created container
        return wrap;
    }

    (function () {
        // Tab switching: one panel visible at a time.
        document.querySelectorAll('.inv-tab').forEach(function (tab) {
            tab.addEventListener('click', function () {
                var key = tab.dataset.tab;
                document.querySelectorAll('.inv-tab').forEach(function (t) {
                    var match = t.dataset.tab === key;
                    t.classList.toggle('active', match);
                    t.setAttribute('aria-selected', match ? 'true' : 'false');
                });
                document.querySelectorAll('.inv-panel').forEach(function (p) {
                    p.classList.toggle('hidden', p.dataset.panel !== key);
                });
            });
        });

        rebuildBasemapUI();
        _applyPendingSwipe(); // wiper from the URL hash / saved view (built-in + local layers)
        _loadPromotedQms();   // merge admin-curated shared layers (public set for everyone)
        _traceLoad();         // editor GeoTIFF overlays (no-op for the public)

        // Pinned basemap quick-select change handler (options (re)built by rebuildBasemapUI).
        var pinned = document.getElementById('pinned-basemap');
        if (pinned) pinned.addEventListener('change', function () { setBasemap(pinned.value); });
    }());

    // ---------------------------------------------------------------------------
    // Polygon loading on zoom / pan
    // ---------------------------------------------------------------------------
    function onMoveEnd() {
        if (!map.getSource('polygons')) return;
        if (map.getZoom() < POLYGON_ZOOM) {
            _polygonsData = { type: 'FeatureCollection', features: [] };
            map.getSource('polygons').setData(_polygonsData);
            _swipeSetPolygons(_polygonsData);
            document.getElementById('zoom-hint').style.display = '';
        } else {
            document.getElementById('zoom-hint').style.display = 'none';
            if (!polygonLoadPending) {
                polygonLoadPending = true;
                var b = map.getBounds();
                var bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].join(',');
                fetch(API_BASE + 'api/polygons/?bbox=' + bbox)
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        // Merge n10/lw so the susceptibility sliders filter
                        // polygons the same way they filter points.
                        _suscValuesPromise.then(function () {
                            mergeSuscValues(data);
                            _polygonsData = data;
                            map.getSource('polygons').setData(data);
                            _swipeSetPolygons(data);
                            buildFilter();
                        });
                    })
                    .catch(function (e) { console.error('Polygon load failed:', e); })
                    .finally(function () { polygonLoadPending = false; });
            }
        }
        updateHistogram();
        updateTimeline();
        updateSidebarCounts();
    }

    // ---------------------------------------------------------------------------
    // Timed events (for seasonal histogram)
    // ---------------------------------------------------------------------------
    var _timedEvents = null;
    fetch(API_BASE + 'api/timed_events/?v=' + DATA_V)
        .then(function (r) { return r.json(); })
        .then(function (d) {
            _timedEvents = d.events;
            if (_urlPanels.openHist && histFP) histFP.open();
            updateHistogram();
        })
        .catch(function (e) { console.error('Timed events load failed:', e); });

    // ---------------------------------------------------------------------------
    // Timeline events
    // ---------------------------------------------------------------------------
    var _timelineEvents = null;
    var CURRENT_YEAR = new Date().getFullYear();
    var TL_HOL_UNITS = 27;  // Holocene bin width in "unit" columns
    var TL_MOD_UNITS = 45;  // Modern pre-2000 bin width in "unit" columns
    var TL_ANN_UNITS = 9;   // Annual bin width (2000-2011) in "unit" columns

    fetch(API_BASE + 'api/timeline_events/?v=' + DATA_V)
        .then(function (r) { return r.json(); })
        .then(function (d) {
            _timelineEvents = d.events;
            if (_urlPanels.openTiming && timingFP) timingFP.open();
            updateTimeline();
        })
        .catch(function (e) { console.error('Timeline events load failed:', e); });

    // ---------------------------------------------------------------------------
    // Slider helpers
    // ---------------------------------------------------------------------------
    var YEAR_LABELS = ['All','Holocene','Modern',
        '2012','2013','2014','2015','2016','2017','2018',
        '2019','2020','2021','2022','2023','2024','2025'];

    function yearPosToMinNum(pos) {
        if (pos <= 0) return null;
        if (pos === 1) return -1;
        if (pos === 2) return 0;
        return 2012 + (pos - 3);
    }

    function fmtAreaLabel(lv) {
        if (lv <= 0) return 'All';
        var v = Math.pow(10, lv + 3);  // slider 0–6 maps to 10^3–10^9 m²
        function s2(n) { return n >= 100 ? String(Math.round(n)) : String(parseFloat(n.toPrecision(2))); }
        if (v >= 1e6) return '\u2265\u202f' + s2(v/1e6) + '\u00a0km\u00b2';
        if (v >= 1e4) return '\u2265\u202f' + s2(v/1e4) + '\u00a0ha';
        return '\u2265\u202f' + Math.round(v) + '\u00a0m\u00b2';
    }

    function fmtVolLabel(lv) {
        if (lv <= 0) return 'All';
        var v = Math.pow(10, lv + 4);  // slider 0–8 maps to 10^4–10^12 m³
        function s2(n) { return n >= 100 ? String(Math.round(n)) : String(parseFloat(n.toPrecision(2))); }
        if (v >= 1e9) return '\u2265\u202f' + s2(v/1e9) + '\u00a0km\u00b3';
        if (v >= 1e6) return '\u2265\u202f' + s2(v/1e6) + '\u00a0M\u00a0m\u00b3';
        if (v >= 1e3) return '\u2265\u202f' + s2(v/1e3) + '\u00a0k\u00a0m\u00b3';
        return '\u2265\u202f' + Math.round(v) + '\u00a0m\u00b3';
    }

    // Dual-handle range bindings. Each filter has a { minEl, maxEl, labelEl,
    // fmtOne, fmtRange } binding object. Position 0 on the min handle and
    // the max-end position on the max handle both mean "no constraint" — the
    // active band visually narrows when handles move inward.
    function fmtAreaRange(loV, hiV, sliderMax) {
        var minActive = loV > 0, maxActive = hiV < sliderMax;
        if (!minActive && !maxActive) return 'All';
        if (minActive && !maxActive) return fmtAreaLabel(loV);
        if (!minActive && maxActive) {
            var v = Math.pow(10, hiV + 3);
            function s2(n) { return n >= 100 ? String(Math.round(n)) : String(parseFloat(n.toPrecision(2))); }
            if (v >= 1e6) return '≤ ' + s2(v/1e6) + ' km²';
            if (v >= 1e4) return '≤ ' + s2(v/1e4) + ' ha';
            return '≤ ' + Math.round(v) + ' m²';
        }
        // both active — show both bounds without the ≥ / ≤ glyphs to save space
        return fmtAreaLabel(loV).replace(/^≥\s?/, '') + ' – '
             + fmtAreaLabel(hiV).replace(/^≥\s?/, '');
    }
    function fmtVolRange(loV, hiV, sliderMax) {
        var minActive = loV > 0, maxActive = hiV < sliderMax;
        if (!minActive && !maxActive) return 'All';
        if (minActive && !maxActive) return fmtVolLabel(loV);
        if (!minActive && maxActive) {
            var v = Math.pow(10, hiV + 4);
            function s2(n) { return n >= 100 ? String(Math.round(n)) : String(parseFloat(n.toPrecision(2))); }
            if (v >= 1e9) return '≤ ' + s2(v/1e9) + ' km³';
            if (v >= 1e6) return '≤ ' + s2(v/1e6) + ' M m³';
            if (v >= 1e3) return '≤ ' + s2(v/1e3) + ' k m³';
            return '≤ ' + Math.round(v) + ' m³';
        }
        return fmtVolLabel(loV).replace(/^≥\s?/, '') + ' – '
             + fmtVolLabel(hiV).replace(/^≥\s?/, '');
    }
    function fmtYearRange(loV, hiV, sliderMax) {
        var minActive = loV > 0, maxActive = hiV < sliderMax;
        if (!minActive && !maxActive) return 'All';
        if (minActive && !maxActive) return YEAR_LABELS[loV];
        if (!minActive && maxActive) {
            // "≤ <year>" — strip the "≥" glyph from the label, prepend ≤.
            return '≤ ' + YEAR_LABELS[hiV].replace(/^≥\s?/, '');
        }
        return YEAR_LABELS[loV].replace(/^≥\s?/, '') + ' – '
             + YEAR_LABELS[hiV].replace(/^≥\s?/, '');
    }
    function fmtSuscRange(loV, hiV, sliderMax) {
        var minActive = loV > 0, maxActive = hiV < sliderMax;
        if (!minActive && !maxActive) return 'All';
        if (minActive && !maxActive) return '≥ ' + loV;
        if (!minActive && maxActive) return '≤ ' + hiV;
        return loV + ' – ' + hiV;
    }

    function _setupDual(slug, fmtRange) {
        var minEl = document.getElementById(slug + '-min');
        var maxEl = document.getElementById(slug + '-max');
        var labelEl = document.getElementById(slug + '-label');
        var container = minEl ? minEl.closest('.dual-range') : null;
        var prog = container ? container.querySelector('.dual-progress') : null;
        if (!minEl || !maxEl) return null;
        var sliderMin = parseFloat(minEl.min);
        var sliderMax = parseFloat(minEl.max);

        function refresh() {
            var lo = parseFloat(minEl.value);
            var hi = parseFloat(maxEl.value);
            // Enforce min ≤ max — push the other handle if crossed.
            if (lo > hi) {
                if (document.activeElement === minEl) maxEl.value = lo;
                else minEl.value = hi;
                lo = parseFloat(minEl.value);
                hi = parseFloat(maxEl.value);
            }
            if (prog) {
                var leftPct  = ((lo - sliderMin) / (sliderMax - sliderMin)) * 100;
                var rightPct = 100 - ((hi - sliderMin) / (sliderMax - sliderMin)) * 100;
                prog.style.left  = leftPct + '%';
                prog.style.right = rightPct + '%';
            }
            if (labelEl) labelEl.textContent = fmtRange(lo, hi, sliderMax);
        }

        minEl.addEventListener('input', function () { refresh(); buildFilter(); });
        maxEl.addEventListener('input', function () { refresh(); buildFilter(); });
        refresh();
        return { minEl: minEl, maxEl: maxEl, sliderMin: sliderMin, sliderMax: sliderMax, refresh: refresh };
    }

    var srcAreaDual = _setupDual('src-area', fmtAreaRange);
    var depAreaDual = _setupDual('dep-area', fmtAreaRange);
    var volDual     = _setupDual('vol',      fmtVolRange);
    var suscN10Dual = _setupDual('susc-n10', fmtSuscRange);
    var suscLwDual  = _setupDual('susc-lw',  fmtSuscRange);
    var yearDual    = _setupDual('year',     fmtYearRange);

    var cbMolards      = document.getElementById('cb-molards');
    var cbStream       = document.getElementById('cb-stream');
    var cbHeadscarp    = document.getElementById('cb-headscarp');
    var cbSiteVolume   = document.getElementById('cb-site-volume');
    var cbSupraglacial = document.getElementById('cb-supraglacial');
    var cbPermafrost   = document.getElementById('cb-permafrost');
    var cbTimed        = document.getElementById('cb-timed');
    var cbSeismic      = document.getElementById('cb-seismic');
    var cbPost2012     = document.getElementById('cb-post2012');
    var cbFlagged      = document.getElementById('cb-flagged');   // editor-only
    var cbLimitView    = document.getElementById('cb-limit-view');
    [cbMolards, cbStream, cbHeadscarp, cbSiteVolume, cbSupraglacial, cbPermafrost, cbTimed, cbSeismic, cbPost2012, cbFlagged].forEach(function (cb) {
        if (cb) cb.addEventListener('change', buildFilter);
    });

    // Susceptibility overlays (lw / n10) — mutually exclusive. Checking one
    // unchecks (and hides) the other; either can be off. Sources/layers are
    // configured at initDataLayers time, so toggling just flips visibility;
    // tiles are fetched on demand by MapLibre on first activation.
    var suscCbs = SUSC_LAYERS.map(function (s) { return document.getElementById(s.cb); });
    function setSuscVis(i, vis) {
        var id = 'susc-' + SUSC_LAYERS[i].key + '-layer';
        if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', vis);
        _swipeAlso(function (m) { if (m.getLayer(id)) m.setLayoutProperty(id, 'visibility', vis); });
    }
    SUSC_LAYERS.forEach(function (s, i) {
        var cb = suscCbs[i];
        if (!cb) return;
        cb.addEventListener('change', function () {
            if (cb.checked) {
                suscCbs.forEach(function (other, j) {
                    if (other && j !== i && other.checked) { other.checked = false; setSuscVis(j, 'none'); }
                });
            }
            setSuscVis(i, cb.checked ? 'visible' : 'none');
        });
    });

    // Survey-circles toggle — lazy-fetches on first activation, then just
    // flips visibility on subsequent toggles.
    var cbSurveyCircles = document.getElementById('cb-survey-circles');
    if (cbSurveyCircles) {
        cbSurveyCircles.addEventListener('change', function () {
            var vis = cbSurveyCircles.checked ? 'visible' : 'none';
            var apply = function () {
                ['survey-circles-outline', 'survey-circles-label'].forEach(function (id) {
                    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', vis);
                    _swipeAlso(function (m) { if (m.getLayer(id)) m.setLayoutProperty(id, 'visibility', vis); });
                });
            };
            if (cbSurveyCircles.checked && !_surveyCirclesData) {
                fetch(API_BASE + 'api/survey_circles/?v=' + DATA_V)
                    .then(function (r) { return r.json(); })
                    .then(function (fc) {
                        _surveyCirclesData = fc;
                        if (map.getSource('survey-circles')) map.getSource('survey-circles').setData(fc);
                        _swipeAlso(function (m) { if (m.getSource('survey-circles')) m.getSource('survey-circles').setData(fc); });
                        apply();
                    })
                    .catch(function (e) { console.error('survey_circles fetch failed:', e); });
            } else {
                apply();
            }
        });
    }

    // Alaska Quaternary faults & folds (DGGS QFF) — reference overlay, ON by
    // default. Unlike survey-circles we fetch eagerly (small vector file) so the
    // layer is populated as soon as the map loads. The source/layer themselves
    // are created in initDataLayers; here we load the data and wire the toggle.
    var cbFaults = document.getElementById('cb-faults');
    function loadFaults() {
        var seed = function () {
            if (map.getSource('faults')) map.getSource('faults').setData(_faultsData);
            _swipeAlso(function (m) { if (m.getSource('faults')) m.getSource('faults').setData(_faultsData); });
        };
        if (_faultsData) { seed(); return; }
        fetch(STATIC_BASE + 'inventory/ak_qff.geojson?v=' + DATA_V)
            .then(function (r) { return r.json(); })
            .then(function (fc) {
                _faultsData = fc;
                seed();
            })
            .catch(function (e) { console.error('faults fetch failed:', e); });
    }
    loadFaults();
    if (cbFaults) {
        cbFaults.addEventListener('change', function () {
            var vis = cbFaults.checked ? 'visible' : 'none';
            if (map.getLayer('faults-line')) map.setLayoutProperty('faults-line', 'visibility', vis);
            _swipeAlso(function (m) { if (m.getLayer('faults-line')) m.setLayoutProperty('faults-line', 'visibility', vis); });
            if (cbFaults.checked) loadFaults();
        });
    }

    var histDaysSlider = document.getElementById('hist-days-slider');
    var histDaysLabel  = document.getElementById('hist-days-label');
    if (histDaysSlider) histDaysSlider.addEventListener('input', function () {
        if (histDaysLabel) histDaysLabel.textContent = this.value;
        updateHistogram();
    });

    // Panel-mode checkboxes (declared early so applyUrlState can hydrate them).
    // Listeners are attached further down, near the panel toggle handlers.
    var cbHistVol    = document.getElementById('hist-volume');
    var cbHistLog    = document.getElementById('hist-log');
    var cbTimingVol  = document.getElementById('timing-volume');
    var cbTimingLog  = document.getElementById('timing-log');

    // Apply URL state to DOM after all elements are referenced
    var _urlPanels = applyUrlState();

    // ---------------------------------------------------------------------------
    // URL state management
    // ---------------------------------------------------------------------------
    function writeUrlState() {
        var params = new URLSearchParams(window.location.search);

        // Types bitmask: bit0=slow, bit1=catastrophic
        // Only write if at least one type is unchecked (default = all checked)
        var t = 0, anyTypeOff = false;
        document.querySelectorAll('.filter-type').forEach(function (cb) {
            if (cb.checked) t |= (cb.value === 'slow' ? 1 : 2);
            else anyTypeOff = true;
        });
        if (anyTypeOff) params.set('t', t); else params.delete('t');

        // Classes bitmask — only write if any present checkbox is unchecked
        var c = 0, anyClassOff = false;
        document.querySelectorAll('.filter-class').forEach(function (cb) {
            if (!cb.checked) { anyClassOff = true; return; }
            var idx = CLASS_ORDER.indexOf(cb.value);
            if (idx >= 0) c |= (1 << idx);
        });
        if (anyClassOff) params.set('c', c); else params.delete('c');

        // Flags bitmask: molards=1 stream=2 supraglacial=4 permafrost=8 timed=16 seismic=32 post2012=64 headscarp=128 site_volume=256
        var f = 0;
        if (cbMolards      && cbMolards.checked)      f |= 1;
        if (cbStream       && cbStream.checked)        f |= 2;
        if (cbSupraglacial && cbSupraglacial.checked)  f |= 4;
        if (cbPermafrost   && cbPermafrost.checked)    f |= 8;
        if (cbTimed        && cbTimed.checked)         f |= 16;
        if (cbSeismic      && cbSeismic.checked)       f |= 32;
        if (cbPost2012     && cbPost2012.checked)      f |= 64;
        if (cbHeadscarp    && cbHeadscarp.checked)     f |= 128;
        if (cbSiteVolume   && cbSiteVolume.checked)    f |= 256;
        if (f !== 0) params.set('f', f); else params.delete('f');

        // Dual-handle sliders: encode "lo,hi" only when off-default.
        // Defaults are min=sliderMin (0), max=sliderMax. Either side
        // moved off its default → both ends written so the URL captures
        // the exact range.
        function encodeDual(dual, paramName) {
            if (!dual) { params.delete(paramName); return; }
            var lo = parseFloat(dual.minEl.value);
            var hi = parseFloat(dual.maxEl.value);
            if (lo === dual.sliderMin && hi === dual.sliderMax) {
                params.delete(paramName);
            } else {
                params.set(paramName, lo + ',' + hi);
            }
        }
        encodeDual(srcAreaDual, 'sa');
        encodeDual(depAreaDual, 'da');
        encodeDual(volDual,     'vol');
        encodeDual(yearDual,    'yr');

        // Limit counts to view
        if (cbLimitView && cbLimitView.checked) params.set('lv', '1'); else params.delete('lv');

        // Panel visibility
        var histOpen   = histPanel   && !histPanel.classList.contains('hidden');
        var timingOpen = timingPanel && !timingPanel.classList.contains('hidden');
        if (histOpen)   params.set('hist',   '1'); else params.delete('hist');
        if (timingOpen) params.set('timing', '1'); else params.delete('timing');

        // Panel modes — bit0=hist-vol, bit1=hist-log, bit2=timing-vol, bit3=timing-log
        var pm = 0;
        if (cbHistVol   && cbHistVol.checked)   pm |= 1;
        if (cbHistLog   && cbHistLog.checked)   pm |= 2;
        if (cbTimingVol && cbTimingVol.checked) pm |= 4;
        if (cbTimingLog && cbTimingLog.checked) pm |= 8;
        if (pm) params.set('pm', pm); else params.delete('pm');

        var qs = params.toString();
        // Build the new URL keeping the hash intact — `pathname` alone would
        // strip `#map=z/lat/lng&...` that writeHashState owns.
        var newUrl = window.location.pathname + (qs ? '?' + qs : '') + window.location.hash;
        if (window.location.search + window.location.hash !==
            (qs ? '?' + qs : '') + window.location.hash) {
            history.replaceState(null, '', newUrl);
        }
    }

    function applyUrlState() {
        var params = new URLSearchParams(window.location.search);
        if (!params.toString()) return { openHist: false, openTiming: false };

        // Types
        var t = params.has('t') ? parseInt(params.get('t')) : 3;
        document.querySelectorAll('.filter-type').forEach(function (cb) {
            cb.checked = cb.value === 'slow' ? !!(t & 1) : !!(t & 2);
        });

        // Classes
        var c = params.has('c') ? parseInt(params.get('c')) : ALL_CLASSES_MASK;
        document.querySelectorAll('.filter-class').forEach(function (cb) {
            var idx = CLASS_ORDER.indexOf(cb.value);
            cb.checked = (idx < 0) ? true : !!(c & (1 << idx));
        });

        // Flags
        var f = params.has('f') ? parseInt(params.get('f')) : 0;
        if (cbMolards)      cbMolards.checked      = !!(f & 1);
        if (cbStream)       cbStream.checked        = !!(f & 2);
        if (cbSupraglacial) cbSupraglacial.checked  = !!(f & 4);
        if (cbPermafrost)   cbPermafrost.checked    = !!(f & 8);
        if (cbTimed)        cbTimed.checked         = !!(f & 16);
        if (cbSeismic)      cbSeismic.checked       = !!(f & 32);
        if (cbPost2012)     cbPost2012.checked      = !!(f & 64);
        if (cbHeadscarp)    cbHeadscarp.checked     = !!(f & 128);
        if (cbSiteVolume)   cbSiteVolume.checked    = !!(f & 256);

        // Sliders
        // Hydrate dual sliders from "lo,hi" param. Backwards compat with the
        // pre-dual form (single value) — interpreted as a min-only restriction.
        function hydrateDual(dual, paramName) {
            if (!dual || !params.has(paramName)) return;
            var raw = params.get(paramName);
            var parts = raw.split(',');
            if (parts.length === 2) {
                dual.minEl.value = parts[0];
                dual.maxEl.value = parts[1];
            } else {
                dual.minEl.value = raw;
            }
            dual.refresh();
        }
        hydrateDual(srcAreaDual, 'sa');
        hydrateDual(depAreaDual, 'da');
        hydrateDual(volDual,     'vol');
        hydrateDual(yearDual,    'yr');

        // Limit counts to view
        if (cbLimitView && params.get('lv') === '1') cbLimitView.checked = true;

        // Panel modes (bit-packed)
        var pm = params.has('pm') ? parseInt(params.get('pm')) : 0;
        if (cbHistVol)   cbHistVol.checked   = !!(pm & 1);
        if (cbHistLog)   cbHistLog.checked   = !!(pm & 2);
        if (cbTimingVol) cbTimingVol.checked = !!(pm & 4);
        if (cbTimingLog) cbTimingLog.checked = !!(pm & 8);

        return {
            openHist:   params.get('hist')   === '1',
            openTiming: params.get('timing') === '1',
        };
    }

    // ---------------------------------------------------------------------------
    // Client-side filtering
    // ---------------------------------------------------------------------------
    function buildFilter() {
        if (!map.getLayer('points')) return;

        var activeTypes = [], activeClasses = [];
        document.querySelectorAll('.filter-type').forEach(function (cb) {
            if (cb.checked) activeTypes.push(cb.value);
        });
        document.querySelectorAll('.filter-class').forEach(function (cb) {
            if (cb.checked) activeClasses.push(cb.value);
        });

        var hideAll = ['==', ['literal', '1'], '0'];
        if (!activeTypes.length || !activeClasses.length) {
            _applyLandslideFilter(map, hideAll);
            _swipeFilter = hideAll;   // so a swipe map enabled later doesn't resurrect the old filter
            _swipeSetFilter(hideAll);
            updateHistogram();
            updateTimeline();
            scheduleSidebarCountUpdate();
            writeUrlState();
            return;
        }

        // Resolve NULL/empty landslide_class to the synthetic value
        // '__unclassified__' so records with no class can be filtered via the
        // "Incomplete classification" checkbox. coalesce handles null;
        // the outer case also converts empty string to the same sentinel.
        var classExpr = ['case',
            ['==', ['coalesce', ['get', 'landslide_class'], ''], ''],
            '__unclassified__',
            ['get', 'landslide_class']
        ];
        var f = ['all',
            ['in', ['get', 'landslide_type'],  ['literal', activeTypes]],
            ['in', classExpr, ['literal', activeClasses]]
        ];

        // Dual-handle filters: each side adds an expression only when its
        // handle has moved off the "no filter" position. Records with NULL
        // on the filtered field drop out as soon as either side activates
        // (coalesce sentinels chosen so the comparison fails for NULLs).
        function addRangeFilter(dual, propName, posToValue, extraAny) {
            if (!dual) return;
            var lo = parseFloat(dual.minEl.value);
            var hi = parseFloat(dual.maxEl.value);
            var minActive = lo > dual.sliderMin;
            var maxActive = hi < dual.sliderMax;
            if (!minActive && !maxActive) return;
            var parts = [];
            if (minActive) {
                parts.push(['>=', ['coalesce', ['get', propName], -1], posToValue(lo)]);
            }
            if (maxActive) {
                parts.push(['<=', ['coalesce', ['get', propName], 1e18], posToValue(hi)]);
            }
            var expr = parts.length === 1 ? parts[0] : ['all'].concat(parts);
            if (extraAny) {
                // Wraps the constraint in 'any' so the bypass case passes
                // through (used by deposit-area to skip slow landslides).
                f.push(['any', extraAny, expr]);
            } else {
                f.push(expr);
            }
        }
        var areaPosToValue = function (p) { return Math.pow(10, p + 3); };
        var volPosToValue  = function (p) { return Math.pow(10, p + 4); };

        addRangeFilter(srcAreaDual, 'area_src',          areaPosToValue);
        addRangeFilter(depAreaDual, 'area_dep',          areaPosToValue,
                        ['==', ['get', 'landslide_type'], 'slow']);
        addRangeFilter(volDual,     'volume_preferred',  volPosToValue);
        // Susceptibility sliders: slider position == raster value (0-81) directly.
        addRangeFilter(suscN10Dual, 'n10', function (p) { return p; });
        addRangeFilter(suscLwDual,  'lw',  function (p) { return p; });
        // Year is a step-1 integer slider mapped through yearPosToMinNum
        // (Holocene = -1, Modern = 0, 2012-2025 = 2012..2025).
        if (yearDual) {
            var yLo = parseInt(yearDual.minEl.value);
            var yHi = parseInt(yearDual.maxEl.value);
            var yMinActive = yLo > yearDual.sliderMin;
            var yMaxActive = yHi < yearDual.sliderMax;
            if (yMinActive || yMaxActive) {
                var yParts = [];
                if (yMinActive) yParts.push(['>=', ['coalesce', ['get', 'year_num'], -1e9], yearPosToMinNum(yLo)]);
                if (yMaxActive) yParts.push(['<=', ['coalesce', ['get', 'year_num'],  1e9], yearPosToMinNum(yHi)]);
                f.push(yParts.length === 1 ? yParts[0] : ['all'].concat(yParts));
            }
        }
        if (cbMolards      && cbMolards.checked)      f.push(['==', ['get', 'molards'], true]);
        if (cbStream       && cbStream.checked)        f.push(['!=', ['coalesce', ['get', 'stream_damming'], ''], '']);
        if (cbHeadscarp    && cbHeadscarp.checked)     f.push(['==', ['get', 'precursory_headscarp'], true]);
        if (cbSiteVolume   && cbSiteVolume.checked)    f.push(['==', ['get', 'has_site_specific_volume'], true]);
        if (cbSupraglacial && cbSupraglacial.checked)  f.push(['==', ['get', 'exclusively_supraglacial'], true]);
        if (cbPermafrost   && cbPermafrost.checked)    f.push(['==', ['get', 'creeping_permafrost_mass'], true]);
        if (cbTimed        && cbTimed.checked)         f.push(['==', ['get', 'has_time_bracket'], true]);
        if (cbSeismic      && cbSeismic.checked)       f.push(['==', ['get', 'has_seismic'], true]);
        if (cbPost2012     && cbPost2012.checked)      f.push(['==', ['get', 'post_2012_activity_increase'], true]);
        if (cbFlagged      && cbFlagged.checked)       f.push(['==', ['get', 'flagged'], true]);

        _applyLandslideFilter(map, f);
        _swipeFilter = f;            // remember so a newly-enabled swipe map can apply it
        _swipeSetFilter(f);
        updateHistogram();
        updateTimeline();
        scheduleSidebarCountUpdate();
        updateSuscCount();
        scatterSyncBox();
        writeUrlState();
    }

    // Live readout for the susceptibility sliders: how many landslides fall in
    // the n10 AND lw boxes (over the whole inventory, independent of the class /
    // type filters — answers "how many sit in this susceptibility range").
    function _suscRange(dual) {
        if (!dual) return null;
        var lo = parseFloat(dual.minEl.value), hi = parseFloat(dual.maxEl.value);
        return { lo: lo, hi: hi, minA: lo > dual.sliderMin, maxA: hi < dual.sliderMax };
    }
    function updateSuscCount() {
        var el = document.getElementById('susc-filter-count');
        if (!el || !_featuresData || !_featuresData.features) return;
        var rn = _suscRange(suscN10Dual), rl = _suscRange(suscLwDual);
        var anyActive = (rn && (rn.minA || rn.maxA)) || (rl && (rl.minA || rl.maxA));
        if (!anyActive) { el.textContent = 'All landslides'; return; }
        var total = _featuresData.features.length, inrange = 0;
        _featuresData.features.forEach(function (ft) {
            var p = ft.properties, ok = true;
            [[rn, p.n10], [rl, p.lw]].forEach(function (pair) {
                var r = pair[0], val = pair[1];
                if (!r || (!r.minA && !r.maxA)) return;
                if (val == null) { ok = false; return; }
                if (r.minA && val < r.lo) ok = false;
                if (r.maxA && val > r.hi) ok = false;
            });
            if (ok) inrange++;
        });
        var txt = inrange + ' of ' + total + ' landslides in range';
        el.textContent = txt;
        var sc = document.getElementById('scatter-count');
        if (sc) sc.textContent = anyActive ? txt : '';
    }

    document.querySelectorAll('.filter-type').forEach(function (cb) { cb.addEventListener('change', buildFilter); });
    document.querySelectorAll('.filter-class').forEach(function (cb) { cb.addEventListener('change', buildFilter); });

    var classAll  = document.getElementById('class-all');
    var classNone = document.getElementById('class-none');
    if (classAll) classAll.addEventListener('click', function (e) {
        e.preventDefault();
        document.querySelectorAll('.filter-class').forEach(function (cb) { cb.checked = true; });
        buildFilter();
    });
    if (classNone) classNone.addEventListener('click', function (e) {
        e.preventDefault();
        document.querySelectorAll('.filter-class').forEach(function (cb) { cb.checked = false; });
        buildFilter();
    });

    document.querySelectorAll('.sg-all').forEach(function (link) {
        link.addEventListener('click', function (e) {
            e.preventDefault();
            document.querySelectorAll('.sg-item[data-sg="' + this.dataset.sg + '"] .filter-class')
                .forEach(function (cb) { cb.checked = true; });
            buildFilter();
        });
    });
    document.querySelectorAll('.sg-none').forEach(function (link) {
        link.addEventListener('click', function (e) {
            e.preventDefault();
            document.querySelectorAll('.sg-item[data-sg="' + this.dataset.sg + '"] .filter-class')
                .forEach(function (cb) { cb.checked = false; });
            buildFilter();
        });
    });

    // ---------------------------------------------------------------------------
    // Sidebar count limiting to map view
    // ---------------------------------------------------------------------------
    var _originalCounts = {};
    var _sidebarCountTimer = null;

    // Snapshot server-rendered counts on first load
    document.querySelectorAll('.filter-class').forEach(function (cb) {
        var countEl = cb.closest('.nav-filter-label').querySelector('.cls-count');
        if (countEl) _originalCounts[cb.value] = countEl.textContent;
    });

    function updateSidebarCounts() {
        if (!cbLimitView || !cbLimitView.checked) {
            // Restore original counts
            document.querySelectorAll('.filter-class').forEach(function (cb) {
                var countEl = cb.closest('.nav-filter-label').querySelector('.cls-count');
                if (countEl && _originalCounts[cb.value] !== undefined) {
                    countEl.textContent = _originalCounts[cb.value];
                }
            });
            return;
        }
        if (!map.getLayer('points')) return;

        // Count visible features per class using queryRenderedFeatures
        var counts = {};
        var features = map.queryRenderedFeatures({ layers: ['points'] });
        // Deduplicate by id (tiles can duplicate features at boundaries)
        var seen = {};
        features.forEach(function (f) {
            var id = f.properties.id;
            if (seen[id]) return;
            seen[id] = true;
            var cls = f.properties.landslide_class || '__unclassified__';
            counts[cls] = (counts[cls] || 0) + 1;
        });

        document.querySelectorAll('.filter-class').forEach(function (cb) {
            var countEl = cb.closest('.nav-filter-label').querySelector('.cls-count');
            if (countEl) countEl.textContent = counts[cb.value] || 0;
        });
    }

    // Schedule update after filter changes (needs render to complete first)
    function scheduleSidebarCountUpdate() {
        if (!cbLimitView || !cbLimitView.checked) return;
        if (_sidebarCountTimer) clearTimeout(_sidebarCountTimer);
        _sidebarCountTimer = setTimeout(updateSidebarCounts, 60);
    }

    if (cbLimitView) cbLimitView.addEventListener('change', function () {
        // Both the sidebar class counts AND the chart panels respect this
        // toggle. When off, histograms summarize the whole inventory;
        // when on, they're clipped to the current map viewport.
        updateSidebarCounts();
        updateHistogram();
        updateTimeline();
        writeUrlState();
    });

    // ---------------------------------------------------------------------------
    // Click / hover interaction
    // ---------------------------------------------------------------------------
    // Right-click (Ctrl-click on Mac) anywhere on the map → copy "lat, lon" to
    // the clipboard for pasting into Planet etc. Skipped while a draw/measure
    // session is active (there, right-click deletes a vertex).
    map.on('contextmenu', function (e) {
        if (map.__measureActive || map.__drawActive) return;
        e.preventDefault();
        var txt = e.lngLat.lat.toFixed(5) + ', ' + e.lngLat.lng.toFixed(5);
        var done = function () { _coordToast(txt + ' copied'); };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(txt).then(done, function () { _coordToast(txt); });
        } else { _coordToast(txt); }
    });
    function _coordToast(msg) {
        var t = document.getElementById('_coord-toast');
        if (!t) {
            t = document.createElement('div');
            t.id = '_coord-toast';
            t.style.cssText = 'position:absolute;bottom:24px;left:50%;transform:translateX(-50%);' +
                'background:rgba(20,20,20,.88);color:#fff;padding:6px 12px;border-radius:4px;' +
                'font-size:13px;z-index:5;pointer-events:none;transition:opacity .3s;opacity:0;';
            (document.getElementById('map') || document.body).appendChild(t);
        }
        t.textContent = msg;
        t.style.opacity = '1';
        clearTimeout(t._h);
        t._h = setTimeout(function () { t.style.opacity = '0'; }, 1600);
    }

    map.on('click', 'points',       function (e) { if (map.__measureActive || map.__drawActive) return; showDetail(e.features[0].properties.id); });
    map.on('click', 'polygon-fill', function (e) { if (map.__measureActive || map.__drawActive) return; showDetail(e.features[0].properties.landslide_id); });
    ['points', 'polygon-fill'].forEach(function (layer) {
        map.on('mouseenter', layer, function () { if (!map.__measureActive && !map.__drawActive) map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', layer, function () { if (!map.__measureActive && !map.__drawActive) map.getCanvas().style.cursor = ''; });
    });
    // Hover highlight is filter-driven, so mirror it to the comparison map —
    // otherwise the white outline cuts off at the wiper divider.
    function _setPolygonHover(landslideId) {
        var f = ['==', 'landslide_id', landslideId];
        if (map.getLayer('polygon-hover')) map.setFilter('polygon-hover', f);
        _swipeAlso(function (m) { if (m.getLayer('polygon-hover')) m.setFilter('polygon-hover', f); });
    }
    map.on('mousemove',  'polygon-fill', function (e) {
        if (map.__measureActive || map.__drawActive) return;
        _setPolygonHover(e.features[0].properties.landslide_id);
    });
    map.on('mouseleave', 'polygon-fill', function () {
        _setPolygonHover(-1);
    });

    // Double-click a landslide → jump to its curated default view (center,
    // zoom, basemap, and wiper state via applyViewString — the wiper turns
    // OFF if the stored view has none), falling back to a plain centroid
    // zoom when no default view is stored. preventDefault suppresses the
    // map's own double-click zoom; the pair of single clicks has already
    // opened the detail panel, so this just re-renders it with fresh data.
    function _dblclickDefaultView(e) {
        if (map.__measureActive || map.__drawActive) return;
        // A dot over its own polygon matches both layers — handle once.
        if (e.originalEvent) {
            if (e.originalEvent._lsDefView) return;
            e.originalEvent._lsDefView = true;
        }
        e.preventDefault();
        var p = e.features[0].properties;
        var id = p.id != null ? p.id : p.landslide_id;
        if (!id) return;
        fetch(API_BASE + 'api/landslide/' + id + '/')
            .then(function (r) { return r.json(); })
            .then(function (d) {
                renderDetail(d);
                if (d.default_map_view) {
                    applyViewString(d.default_map_view);
                } else if (d.centroid_lat != null && d.centroid_lon != null) {
                    map.flyTo({ center: [+d.centroid_lon, +d.centroid_lat], zoom: 13 });
                }
            })
            .catch(function (err) { console.error('Default view failed:', err); });
    }
    map.on('dblclick', 'points',       _dblclickDefaultView);
    map.on('dblclick', 'polygon-fill', _dblclickDefaultView);

    // Quaternary fault trace → popup with name/age/slip attributes (DGGS QFF).
    map.on('click', 'faults-line', function (e) {
        if (map.__measureActive || map.__drawActive) return;
        var p = e.features[0].properties || {};
        function row(lbl, val) {
            if (val === undefined || val === null || val === '' || val === 'Unknown') return '';
            return '<div style="margin:1px 0;"><span style="color:#888;">' + lbl + ':</span> ' + val + '</div>';
        }
        var html = '<div style="font:12px/1.4 system-ui,sans-serif; max-width:240px;">' +
            '<div style="font-weight:600; color:#b5179e; margin-bottom:3px;">' +
                (p.NAME || 'Unnamed fault') + '</div>' +
            row('Age', p.AGE) + row('Type', p.FTYPE) +
            row('Slip rate', p.SLIPRATE) + row('Slip sense', p.SLIPSENSE) +
            row('Dip dir', p.DIPDIRECTI) +
            '<div style="margin-top:4px; color:#aaa; font-size:10px;">DGGS DDS 3 (Koehler, 2013)</div>' +
            '</div>';
        new maplibregl.Popup({ closeButton: true, maxWidth: '260px' })
            .setLngLat(e.lngLat).setHTML(html).addTo(map);
    });
    map.on('mouseenter', 'faults-line', function () { if (!map.__measureActive && !map.__drawActive) map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'faults-line', function () { if (!map.__measureActive && !map.__drawActive) map.getCanvas().style.cursor = ''; });

    // ---------------------------------------------------------------------------
    // Detail panel
    // ---------------------------------------------------------------------------
    var _lastDetail = null;   // {id, name} of the last-opened landslide (trace-overlay linking)

    function showDetail(id) {
        fetch(API_BASE + 'api/landslide/' + id + '/')
            .then(function (r) { return r.json(); })
            .then(renderDetail)
            .catch(function (e) { console.error('Detail failed:', e); });
    }

    function fmtVol(m3) {
        if (!m3) return '\u2014';
        if (m3 >= 1e9) return (m3/1e9).toFixed(2) + '\u00a0km\u00b3';
        if (m3 >= 1e6) return (m3/1e6).toFixed(2) + '\u00a0M\u00a0m\u00b3';
        return (m3/1e3).toFixed(0) + '\u00a0k\u00a0m\u00b3';
    }

    function fmtArea(m2) {
        if (!m2) return '\u2014';
        if (m2 >= 1e6) return (m2/1e6).toFixed(2) + '\u00a0km\u00b2';
        return Math.round(m2).toLocaleString() + '\u00a0m\u00b2';
    }

    function fmtDate(d) { return d ? String(d).slice(0, 10) : null; }
    function flag(val, label) { return val ? label : null; }

    function normUrl(s) {
        if (s == null) return null;
        s = String(s).trim();
        if (!s) return null;
        if (/^[a-z][a-z0-9+.-]*:/i.test(s)) return s;
        return 'https://' + s;
    }

    function planetIsProminent(d) {
        if (d.landslide_type === 'catastrophic')
            return d.landslide_class !== 'Catastrophic Holocene' && d.landslide_class !== 'Catastrophic Modern';
        if (d.landslide_type === 'slow')
            return d.landslide_class === 'Slow Obvious creep' || d.landslide_class === 'Slow Patchy obvious creep';
        return false;
    }

    function renderPlanetStory(s) {
        // Archived timelapse → embedded video player with attribution.
        // Anything else (comparison-type, or timelapse whose MP4 hasn't been
        // archived yet) → external link, same style as before.
        if (s.mp4_url) {
            return '<div class="planet-player">' +
                   '<video class="planet-video" src="' + esc(s.mp4_url) + '" ' +
                   'autoplay muted loop playsinline controls preload="metadata"></video>' +
                   '<div class="planet-caption">Time-lapse imagery © Planet Labs PBC · ' +
                   '<a href="' + esc(s.planet_url) + '" target="_blank" rel="noopener">' +
                   'View on Planet Stories ↗</a></div></div>';
        }
        return '<a class="planet-prominent" href="' + esc(s.planet_url) +
               '" target="_blank" rel="noopener">' +
               '<span class="planet-icon">P</span> View Planet Story</a>';
    }

    // Find an active landslide by its exact unique_name → {id, lat, lon} (names
    // are unique). Lets a flag reason's referenced landslide become a jump link.
    function _featureByName(name) {
        if (!_featuresData || !_featuresData.features) return null;
        var feats = _featuresData.features;
        for (var i = 0; i < feats.length; i++) {
            var p = feats[i].properties;
            if (p && p.unique_name === name) {
                var c = feats[i].geometry && feats[i].geometry.coordinates;
                return { id: p.id, lon: c ? c[0] : null, lat: c ? c[1] : null };
            }
        }
        return null;
    }

    // Render a flag_reason as HTML; turn a "base name of '<NAME>'" reference
    // into a link that flies to + opens that landslide. Only the base-name
    // reason references a real record (the others quote examples), so anything
    // else (or an unresolved name) just renders as escaped text.
    function linkifyFlagReason(reason) {
        var m = reason.match(/^base name of '(.+)' — needs disambiguation/);
        if (m) {
            var refName = m[1];
            var ref = _featureByName(refName);
            if (ref) {
                var rest = reason.slice(("base name of '" + refName + "'").length);
                var data = ' data-id="' + ref.id + '"' +
                    (ref.lat != null && ref.lon != null
                        ? ' data-lat="' + (+ref.lat).toFixed(4) + '" data-lon="' + (+ref.lon).toFixed(4) + '"'
                        : '');
                return "base name of '" +
                    '<a href="#" class="flag-jump"' + data +
                    ' title="Jump to this landslide" style="color:#1a5fb4;text-decoration:underline;">' +
                    esc(refName) + '</a>' + "'" + esc(rest);
            }
        }
        return esc(reason);
    }

    function renderDetail(d) {
        _lastDetail = { id: d.id, name: d.unique_name };   // trace-overlay link target
        var html = '';
        var stories = d.planet_stories || [];
        var prominent = stories.length > 0 && planetIsProminent(d);

        var manageLink = window._isInventoryEditor
            ? ' <a class="manage-gear" href="/inventory/manage/' + d.id + '/" target="_blank" ' +
              'rel="noopener" title="Edit this record in Manage">⚙</a>'
            : '';
        if (d.slug) {
            // Slug-based permalink, identical shape on live and snapshot —
            // `API_BASE` resolves to `/inventory/` on the live site and `./`
            // inside a snapshot bundle. Both forms 302/meta-refresh to a
            // same-page map+id hash, so the URL is shareable while the
            // click handler below intercepts plain left-clicks to do
            // smooth zoom via the hashchange listener (no page reload).
            var permalink = API_BASE + esc(d.slug) + '/';
            var permaData = ' data-id="' + d.id + '"';
            if (d.centroid_lat != null && d.centroid_lon != null) {
                permaData += ' data-lat="' + (+d.centroid_lat).toFixed(4) + '"'
                          +  ' data-lon="' + (+d.centroid_lon).toFixed(4) + '"';
            }
            // In-app permalink clicks reproduce the curated view, matching what
            // a fresh visitor to the slug URL gets from slug_redirect.
            if (d.default_map_view) permaData += ' data-view="' + esc(d.default_map_view) + '"';
            html += '<h3><a class="landslide-permalink" href="' + permalink + '"' +
                    permaData + ' title="Permalink — right-click to copy">' +
                    esc(d.unique_name) + '</a>' + manageLink + '</h3>';
        } else {
            html += '<h3>' + esc(d.unique_name) + manageLink + '</h3>';
        }
        html += '<span class="type-badge ' + d.landslide_type + '">' +
                (d.landslide_type === 'slow' ? 'Slow' : 'Catastrophic') + '</span>';
        if (d.landslide_class) html += ' <span class="class-badge">' + esc(d.landslide_class) + '</span>';

        // Default view — a curated map view (center/zoom/basemap, optionally a
        // wiper) stored per landslide. Anyone can apply it; editors set/clear it
        // from whatever the map currently shows.
        var hasDefView = !!d.default_map_view;
        if (hasDefView || window._isInventoryEditor) {
            var dvBtn = 'font-size:11px;padding:2px 8px;border:1px solid #bbb;border-radius:3px;' +
                        'background:#fff;cursor:pointer;';
            html += '<div id="defview-row" style="margin:8px 0;display:flex;gap:6px;align-items:center;flex-wrap:wrap;">';
            if (hasDefView) {
                html += '<button type="button" id="defview-apply" style="' + dvBtn +
                        'border-color:#5D4037;color:#5D4037;" title="Zoom to this landslide’s preferred view">' +
                        '⌖ Default view</button>';
            }
            if (window._isInventoryEditor) {
                html += '<button type="button" id="defview-set" style="' + dvBtn +
                        '" title="Save the current map view (center, zoom, basemap, wiper) as this landslide’s default">' +
                        (hasDefView ? 'Update' : 'Set') + ' default view</button>';
                if (hasDefView) {
                    html += '<button type="button" id="defview-clear" style="' + dvBtn +
                            '" title="Remove the saved default view">✕</button>';
                }
            }
            html += '<span id="defview-status" style="font-size:11px;"></span></div>';
        }

        // Editor-only: trace imagery linked to this landslide — the record of
        // what it was traced from, one click to bring back. Cards are built
        // with DOM after the innerHTML set (thumbnails + click handlers).
        var linkedTraces = window._isInventoryEditor
            ? _traceRasters.filter(function (r) {
                return r.landslide_id === d.id && r.status === 'ready';
              })
            : [];
        if (linkedTraces.length) html += '<div id="detail-trace-cards"></div>';

        // Editor-only: flag banner with a Clear button. Shown whenever the
        // record is flagged, independent of what's pinned — clearing the flag
        // is a review action, not a field-edit.
        if (window._isInventoryEditor && d.flagged) {
            html += '<div class="flag-banner" data-id="' + d.id + '" ' +
                    'style="margin:8px 0;padding:6px 9px;background:#fff4e5;border:1px solid #f0c98a;' +
                    'border-radius:4px;font-size:12px;color:#8a5a00;">' +
                    '<span style="font-weight:600;">⚑ Flagged for review.</span> ' +
                    (d.flag_reason ? linkifyFlagReason(d.flag_reason) + ' ' : '') +
                    '<button type="button" id="flag-clear-btn" style="margin-left:4px;font-size:11px;' +
                    'padding:1px 8px;border:1px solid #d2a766;border-radius:3px;background:#fff;cursor:pointer;">' +
                    'Clear flag</button> <span id="flag-clear-status" style="font-weight:400;"></span></div>';
        }

        // Editor-only: inline editor for the currently pinned field (rename
        // workflow — see the map label + filter update live on save).
        if (window._isInventoryEditor && _pinField) {
            var pf = _pinField, pv = d[pf], plabel = _PIN_LABELS[pf] || pf;
            html += '<div class="pin-editor" data-id="' + d.id + '" data-field="' + esc(pf) + '" ' +
                    'style="margin:8px 0;padding:6px 8px;background:#f4f6f8;border-radius:4px;">' +
                    '<div style="font-size:11px;font-weight:600;color:#555;margin-bottom:3px;">' + esc(plabel) +
                    ' <span id="pin-edit-status" style="font-weight:400;"></span></div>' +
                    '<input type="text" id="pin-edit-input" value="' + esc(pv == null ? '' : pv) +
                    '" style="width:100%;box-sizing:border-box;"></div>';
        }

        if (prominent) {
            stories.forEach(function (s) { html += renderPlanetStory(s); });
        }

        var imgLinks = [
            { label:'ESRI Wayback',  icon:'W',      url:normUrl(d.esri_wayback_link),  title:'ESRI Wayback historical imagery' },
            { label:'Google Images', icon:'G',      url:normUrl(d.google_images_link), title:'Google Images search' },
            { label:'Sentinel-2',    icon:'S2',     url:normUrl(d.sentinel2_link),     title:'Copernicus Sentinel-2' },
            { label:'Sentinel-1',    icon:'S1',     url:normUrl(d.sentinel1_link),     title:'Copernicus Sentinel-1 SAR' },
            { label:'OPERA Asc',     icon:'↑', url:normUrl(d.opera_asc_link),     title:'OPERA InSAR displacement — ascending' },
            { label:'OPERA Desc',    icon:'↓', url:normUrl(d.opera_desc_link),    title:'OPERA InSAR displacement — descending' },
            { label:'USGS topo',     icon:'⛰', url:normUrl(d.topoview_link),      title:'USGS TopoView — historic topographic maps' }
        ].filter(function (l) { return l.url; });

        if (imgLinks.length) {
            html += '<div class="imagery-links"><div class="detail-section-title" style="margin-bottom:5px;">External map resources</div>';
            imgLinks.forEach(function (l) {
                html += '<a class="imagery-btn" href="' + esc(l.url) + '" target="_blank" rel="noopener" title="' + esc(l.title) + '">' +
                        '<span class="imagery-icon">' + l.icon + '</span>' + esc(l.label) + '</a>';
            });
            html += '</div>';
        }

        if (d.description) html += '<p class="detail-desc">' + richText(d.description) + '</p>';

        var attrs = [];
        if (d.subsets && d.subsets.length) {
            attrs.push(['Subsets', d.subsets.map(esc).join(', ')]);
        }
        if (d.noted_by)          attrs.push(['Noted by',   esc(d.noted_by)]);
        if (d.creep_behavior)    attrs.push(['Creep',      esc(d.creep_behavior)]);
        if (d.stream_damming)    attrs.push(['Stream dam', esc(d.stream_damming)]);

        var dateMin = fmtDate(d.date_min), dateMax = fmtDate(d.date_max);
        if (dateMin && dateMax && dateMin !== dateMax) attrs.push(['Date', dateMin + '\u2013' + dateMax]);
        else if (dateMin) attrs.push(['Date', dateMin]);
        else if (d.year_text) attrs.push(['Date', esc(d.year_text)]);

        if (d.seismic_datetime) attrs.push(['Seismic time', esc(String(d.seismic_datetime).slice(0,16)) + ' UTC']);
        if (d.seismic_note)     attrs.push(['Seismic note', esc(d.seismic_note)]);
        if (d.seismic_credit)   attrs.push(['Seismic src',  esc(d.seismic_credit)]);

        attrs.push(['Est. volume', fmtVol(d.volume_preferred)]);
        if (d.volume_method) attrs.push(['Vol. method', esc(d.volume_method)]);

        var polys = d.polygons || [];
        if (polys.length) {
            if (d.landslide_type === 'catastrophic') {
                var deposit = polys.find(function (p) { return p.role === 'deposit'; });
                var source  = polys.find(function (p) { return p.role === 'source'; });
                if (deposit && deposit.area) attrs.push(['Deposit area', fmtArea(deposit.area)]);
                if (source  && source.area)  attrs.push(['Source area',  fmtArea(source.area)]);
            } else {
                var tot = 0; polys.forEach(function (p) { tot += p.area || 0; });
                if (tot) attrs.push(['Area', fmtArea(tot)]);
            }
        }
        if (d.ongoing_work) attrs.push(['Prior work', esc(d.ongoing_work)]);
        if (d.catastrophic_failure_years) attrs.push(['Cat. failures', esc(d.catastrophic_failure_years)]);

        if (attrs.length) {
            html += '<div class="detail-section"><table class="attr-table"><tbody>';
            attrs.forEach(function (r) { html += '<tr><td>' + r[0] + '</td><td>' + r[1] + '</td></tr>'; });
            html += '</tbody></table></div>';
        }

        var evidence = [
            flag(d.insar_schaefer,'InSAR (Schaefer)'), flag(d.insar_kim,'InSAR (Kim)'),
            flag(d.insar_opera,'InSAR (OPERA)'), flag(d.insar_other,'InSAR (other)'),
            flag(d.planet_labs_creep,'Planet Labs creep'), flag(d.planet_labs_patchy_creep,'Planet patchy creep'),
            flag(d.geomorph_creep,'Geomorphic'), flag(d.insar_creep,'InSAR creep'),
            flag(d.precursory_headscarp,'Precursory headscarp'), flag(d.molards,'Molards'),
            flag(d.exclusively_supraglacial,'Supraglacial runout'),
            flag(d.post_2012_activity_increase,'Post-2012 increase'),
            flag(d.creeping_permafrost_mass,'Creeping permafrost')
        ].filter(Boolean);

        if (evidence.length) {
            html += '<div class="detail-section"><div class="detail-section-title">Detection / evidence</div>' +
                    '<div class="evidence-list">' + evidence.join('<span class="ev-sep">&middot;</span>') + '</div></div>';
        }

        if (d.notes) {
            html += '<div class="detail-section"><div class="detail-section-title">Notes</div>' +
                    '<p class="detail-desc" style="margin:0">' + richText(d.notes) + '</p></div>';
        }

        if (!prominent && stories.length) {
            // Non-prominent records: stash stories inside a collapsed "More"
            // panel so they don't take real estate by default. Players still
            // render in-app when the user expands.
            var inner = stories.map(function (s) {
                if (s.mp4_url) return renderPlanetStory(s);
                return '<a class="imagery-btn" href="' + esc(s.planet_url) +
                       '" target="_blank" rel="noopener" style="margin-top:6px;">' +
                       '<span class="imagery-icon" style="background:#1a73e8">P</span>Planet story</a>';
            }).join('');
            html += '<details class="more-details"><summary>More</summary>' + inner + '</details>';
        }

        document.getElementById('detail-content').innerHTML = html;
        document.getElementById('detail-panel').classList.remove('hidden');

        // Wire the Clear-flag button (if rendered): set flagged=false via
        // manage_edit_field, then live-patch the source so the count/filter drop it.
        var flagBtn = document.getElementById('flag-clear-btn');
        if (flagBtn) {
            var flagWrap = flagBtn.closest('.flag-banner');
            var flagId = +flagWrap.getAttribute('data-id');
            var flagStat = document.getElementById('flag-clear-status');
            flagBtn.addEventListener('click', function () {
                flagBtn.disabled = true;
                flagStat.style.color = '#1a73e8'; flagStat.textContent = 'clearing…';
                fetch('/inventory/manage/' + flagId + '/field/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': window.CSRF_TOKEN },
                    body: JSON.stringify({ name: 'flagged', value: false })
                }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
                  .then(function (res) {
                    if (res.ok && res.j.ok) {
                        _patchFeatureProp(flagId, 'flagged', false);
                        flagWrap.style.display = 'none';
                    } else {
                        flagBtn.disabled = false;
                        flagStat.style.color = '#c00';
                        flagStat.textContent = (res.j && res.j.error) || 'failed';
                    }
                }).catch(function () {
                    flagBtn.disabled = false; flagStat.style.color = '#c00'; flagStat.textContent = 'failed';
                });
            });
        }

        // Wire the inline pinned-field editor (if rendered). Saves per-field to
        // manage_edit_field, then live-patches the source so label + filter update.
        var pinInp = document.getElementById('pin-edit-input');
        if (pinInp) {
            var pinWrap = pinInp.closest('.pin-editor');
            var pinId = +pinWrap.getAttribute('data-id');
            var pinFld = pinWrap.getAttribute('data-field');
            var pinStat = document.getElementById('pin-edit-status');
            var setStat = function (c, t) { pinStat.style.color = c; pinStat.textContent = t; };
            pinInp.addEventListener('change', function () {
                var isBool = !!pinInp.getAttribute('data-bool');
                var value = isBool ? pinInp.checked : pinInp.value;
                setStat('#1a73e8', 'saving…');
                fetch('/inventory/manage/' + pinId + '/field/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': window.CSRF_TOKEN },
                    body: JSON.stringify({ name: pinFld, value: value })
                }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
                  .then(function (res) {
                    if (res.ok && res.j.ok) {
                        var saved = (res.j.value != null) ? res.j.value : value;
                        if (!isBool && res.j.value != null) pinInp.value = res.j.value;
                        setStat('#1a73e8', '✓ saved');
                        setTimeout(function () { pinStat.textContent = ''; }, 1400);
                        _patchFeatureProp(pinId, pinFld, saved);
                    } else {
                        setStat('#c00', (res.j && res.j.error) || 'save failed');
                    }
                }).catch(function () { setStat('#c00', 'save failed'); });
            });
        }

        // Wire the default-view buttons (if rendered). Set/clear save through
        // manage_edit_field (default_map_view is an ordinary landslides column);
        // on success the panel re-renders so the button row reflects the state.
        var dvApply = document.getElementById('defview-apply');
        if (dvApply) dvApply.addEventListener('click', function () {
            applyViewString(d.default_map_view);
        });
        function _saveDefView(value, busyLabel) {
            var stat = document.getElementById('defview-status');
            stat.style.color = '#1a73e8'; stat.textContent = busyLabel;
            fetch('/inventory/manage/' + d.id + '/field/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': window.CSRF_TOKEN },
                body: JSON.stringify({ name: 'default_map_view', value: value })
            }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
              .then(function (res) {
                if (res.ok && res.j.ok) {
                    d.default_map_view = value;
                    renderDetail(d);
                } else {
                    stat.style.color = '#c00';
                    stat.textContent = (res.j && res.j.error) || 'save failed';
                }
            }).catch(function () { stat.style.color = '#c00'; stat.textContent = 'save failed'; });
        }
        var dvSet = document.getElementById('defview-set');
        if (dvSet) dvSet.addEventListener('click', function () {
            _saveDefView(_currentViewString(), 'saving…');
        });
        var dvClear = document.getElementById('defview-clear');
        if (dvClear) dvClear.addEventListener('click', function () {
            _saveDefView(null, 'clearing…');
        });

        // Build the linked trace-imagery cards (thumbnail + title + date;
        // click = show the overlay and zoom to it).
        var tcBox = document.getElementById('detail-trace-cards');
        if (tcBox) {
            var cap = document.createElement('div');
            cap.className = 'detail-section-title';
            cap.style.cssText = 'margin:8px 0 4px;';
            cap.textContent = 'Traced from';
            tcBox.appendChild(cap);
            linkedTraces.forEach(function (r) {
                var card = document.createElement('div');
                card.style.cssText = 'display:flex;align-items:center;gap:8px;padding:5px 7px;' +
                    'margin-bottom:4px;border:1px solid #d8d2cc;border-radius:5px;background:#faf8f6;' +
                    'cursor:pointer;';
                card.title = 'Show this image on the map and zoom to it'
                           + (r.source_note ? '\n' + r.source_note : '');
                var thumbUrl = _traceThumbUrl(r);
                if (thumbUrl) {
                    var img = document.createElement('img');
                    img.src = thumbUrl;
                    img.alt = '';
                    img.style.cssText = 'width:44px;height:44px;object-fit:cover;border-radius:3px;' +
                        'background:#3a3a3a;flex:none;';
                    img.onerror = function () { img.remove(); };
                    card.appendChild(img);
                }
                var txt = document.createElement('div');
                txt.style.cssText = 'min-width:0;font-size:12px;line-height:1.3;';
                var t1 = document.createElement('div');
                t1.style.cssText = 'font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
                t1.textContent = r.title;
                var t2 = document.createElement('div');
                t2.style.cssText = 'color:#777;font-size:11px;';
                t2.textContent = (r.image_date ? 'imaged ' + r.image_date : '')
                               + (_traceActive[r.id] != null ? '  · on map' : '');
                txt.appendChild(t1); txt.appendChild(t2);
                card.appendChild(txt);
                card.addEventListener('click', function () {
                    if (_traceActive[r.id] == null) _traceActive[r.id] = 1;
                    _traceAddLayer(r.id);
                    _traceZoomTo(r);
                    _traceUpdateSummary();
                    _renderTraceRows();
                    t2.textContent = (r.image_date ? 'imaged ' + r.image_date : '') + '  · on map';
                });
                tcBox.appendChild(card);
            });
        }
    }

    function esc(s) {
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    // Linkify for the free-text fields (description / notes): a links-only
    // markdown subset — [text](https://…) plus bare http(s) URLs become
    // anchors; everything else stays literal text, so existing plain-text
    // values render unchanged. Links are tokenized out of the RAW text
    // (placeholders), the remainder is escaped, then anchors are re-inserted
    // with href/label escaped individually — hrefs are scheme-anchored to
    // https?://, so [x](javascript:…) stays literal text.
    function richText(s) {
        var links = [];
        var txt = String(s).replace(/\u0000/g, '').replace(
            /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|(https?:\/\/[^\s<>"']+[^\s<>"'.,;:!?)\]])/g,
            function (m, label, url, bare) {
                links.push({ label: label || bare, href: url || bare });
                return '\u0000' + (links.length - 1) + '\u0000';
            });
        return esc(txt).replace(/\u0000(\d+)\u0000/g, function (m, i) {
            var l = links[+i];
            return '<a href="' + esc(l.href) + '" target="_blank" rel="noopener" ' +
                   'style="color:#1a5fb4;">' + esc(l.label) + '</a>';
        });
    }

    document.getElementById('close-panel').addEventListener('click', function () {
        document.getElementById('detail-panel').classList.add('hidden');
    });

    // ===========================================================================
    // Panel management (both panels live inside a shared flex container)
    // ===========================================================================
    var histPanel      = document.getElementById('hist-panel');
    var timingPanel    = document.getElementById('timing-panel');
    var scatterPanel   = document.getElementById('scatter-float');

    // The three analysis graphs (seasonal histogram, time-series, scatter) are
    // all floating, draggable, resizable panels. Panel objects are assigned at
    // their wiring sites below; declared here so the async URL-state hydration
    // (which runs after init) can open them.
    var histFP, timingFP, scatterFP;

    // Shared floating-panel behavior: toggle/close, jump-free header drag
    // (clamped to the offset parent), and a debounced redraw on resize. opts:
    // {handle, toggle, close, onResize}. Returns {open, close, isOpen}.
    function makeFloatingPanel(panel, opts) {
        if (!panel) return { open: function () {}, close: function () {}, isOpen: function () { return false; } };
        opts = opts || {};
        var handle = opts.handle, toggle = opts.toggle, closeBtn = opts.close,
            onResize = opts.onResize, onChange = opts.onChange;
        function isOpen() { return !panel.classList.contains('hidden'); }
        function open() {
            panel.classList.remove('hidden');
            if (toggle) toggle.classList.add('active');
            if (onResize) setTimeout(onResize, 60);   // after layout settles
            if (onChange) onChange();
        }
        function close() {
            panel.classList.add('hidden');
            if (toggle) toggle.classList.remove('active');
            if (onChange) onChange();
        }
        if (toggle)   toggle.addEventListener('click', function (e) { e.preventDefault(); isOpen() ? close() : open(); });
        if (closeBtn) closeBtn.addEventListener('click', function (e) { e.preventDefault(); close(); });

        if (onResize && window.ResizeObserver) {
            var raf = null;
            new ResizeObserver(function () {
                if (!isOpen()) return;
                if (raf) cancelAnimationFrame(raf);
                raf = requestAnimationFrame(onResize);
            }).observe(panel);
        }
        if (handle) {
            var moving = false, offX = 0, offY = 0, parRect = null;
            handle.addEventListener('pointerdown', function (e) {
                if (e.target.closest('button, a, input, label, select')) return;  // let controls work
                moving = true;
                var r = panel.getBoundingClientRect();
                parRect = (panel.offsetParent || document.body).getBoundingClientRect();
                offX = e.clientX - r.left; offY = e.clientY - r.top;
                // Pin current position as left/top BEFORE clearing right/bottom so
                // the anchor flip doesn't jump.
                panel.style.left = (r.left - parRect.left) + 'px';
                panel.style.top  = (r.top  - parRect.top)  + 'px';
                panel.style.right = 'auto'; panel.style.bottom = 'auto';
                handle.setPointerCapture(e.pointerId);
                e.preventDefault();
            });
            handle.addEventListener('pointermove', function (e) {
                if (!moving) return;
                var w = panel.offsetWidth, h = panel.offsetHeight;
                panel.style.left = Math.max(0, Math.min(parRect.width  - w, e.clientX - offX - parRect.left)) + 'px';
                panel.style.top  = Math.max(0, Math.min(parRect.height - h, e.clientY - offY - parRect.top))  + 'px';
            });
            handle.addEventListener('pointerup', function () { moving = false; });
        }
        return { open: open, close: close, isOpen: isOpen };
    }

    // ---------------------------------------------------------------------------
    // Chart tooltip
    // ---------------------------------------------------------------------------
    var _chartTooltip = (function() {
        var el = document.createElement('div');
        el.style.cssText = 'position:fixed;background:#fff;border:1px solid #ccc;border-radius:3px;' +
            'padding:3px 8px;font-size:11px;color:#333;pointer-events:none;z-index:200;' +
            'display:none;box-shadow:0 1px 4px rgba(0,0,0,0.18);white-space:nowrap;';
        document.body.appendChild(el);
        return el;
    }());
    var _tlHits   = null;
    var _histHits = null;

    function showChartTip(cx, cy, text) {
        _chartTooltip.textContent = text;
        _chartTooltip.style.display = 'block';
        _chartTooltip.style.left = (cx + 14) + 'px';
        _chartTooltip.style.top  = (cy - 24) + 'px';
    }
    function hideChartTip() { _chartTooltip.style.display = 'none'; }

    function fmtEvYr(v) {
        if (v <= 0)   return '0';
        if (v < 0.01) return v.toFixed(4);
        if (v < 0.1)  return v.toFixed(3);
        if (v < 10)   return v.toFixed(2);
        return v.toFixed(1);
    }

    // ===========================================================================
    // Seasonal timing histogram
    // ===========================================================================

    function isLeapYear(y) { return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0; }

    // Distribute weight across 37 bins for a day range [from, to] in a year of yd days.
    // Optional perBin callback receives (binIndex, fraction) for parallel volume tracking.
    function distributeRange(bins, from, to, span, yd, perBin) {
        for (var b = 0; b < 37; b++) {
            var bStart = b * 10 + 1;
            var bEnd   = b < 36 ? b * 10 + 10 : yd;
            var overlap = Math.max(0, Math.min(bEnd, to) - Math.max(bStart, from) + 1);
            if (overlap > 0) {
                var frac = overlap / span;
                bins[b] += frac;
                if (perBin) perBin(b, frac);
            }
        }
    }

    function currentFilterState() {
        var types = [], classes = [];
        document.querySelectorAll('.filter-type').forEach(function (c) { if (c.checked) types.push(c.value); });
        document.querySelectorAll('.filter-class').forEach(function (c) { if (c.checked) classes.push(c.value); });
        function dualLo(dual, toValue) {
            if (!dual) return null;
            var v = parseFloat(dual.minEl.value);
            return v > dual.sliderMin ? toValue(v) : null;
        }
        function dualHi(dual, toValue) {
            if (!dual) return null;
            var v = parseFloat(dual.maxEl.value);
            return v < dual.sliderMax ? toValue(v) : null;
        }
        var areaToValue = function (p) { return Math.pow(10, p + 3); };
        var volToValue  = function (p) { return Math.pow(10, p + 4); };
        function dualYearLo() {
            if (!yearDual) return null;
            var v = parseInt(yearDual.minEl.value);
            return v > yearDual.sliderMin ? yearPosToMinNum(v) : null;
        }
        function dualYearHi() {
            if (!yearDual) return null;
            var v = parseInt(yearDual.maxEl.value);
            return v < yearDual.sliderMax ? yearPosToMinNum(v) : null;
        }
        return {
            types:    types,
            classes:  classes,
            minSrcArea: dualLo(srcAreaDual, areaToValue),
            maxSrcArea: dualHi(srcAreaDual, areaToValue),
            minDepArea: dualLo(depAreaDual, areaToValue),
            maxDepArea: dualHi(depAreaDual, areaToValue),
            minVol:     dualLo(volDual,     volToValue),
            maxVol:     dualHi(volDual,     volToValue),
            minYear:    dualYearLo(),
            maxYear:    dualYearHi(),
            molards:      cbMolards      && cbMolards.checked,
            stream:       cbStream       && cbStream.checked,
            headscarp:    cbHeadscarp    && cbHeadscarp.checked,
            siteVolume:   cbSiteVolume   && cbSiteVolume.checked,
            supraglacial: cbSupraglacial && cbSupraglacial.checked,
            permafrost:   cbPermafrost   && cbPermafrost.checked,
            timed:        cbTimed        && cbTimed.checked,
            seismic:      cbSeismic      && cbSeismic.checked,
            post2012:     cbPost2012     && cbPost2012.checked,
        };
    }

    function computeHistogram() {
        if (!_timedEvents) return null;
        var b = map.getBounds();
        var w = b.getWest(), e = b.getEast(), s = b.getSouth(), n = b.getNorth();
        var fs = currentFilterState();
        var maxDays = histDaysSlider ? parseInt(histDaysSlider.value) : 30;
        var bins      = new Array(37).fill(0);   // count per bin
        var volBins   = new Array(37).fill(0);   // cumulative volume per bin
        var volMaxEv  = new Array(37).fill(0);   // largest single-event contribution per bin
        var count     = 0;
        var unknownVolCount = 0;

        _timedEvents.forEach(function (ev) {
            // Map-view clip only when the pinned "Limit to map view" toggle is on.
            if (cbLimitView && cbLimitView.checked &&
                (ev.lat < s || ev.lat > n || ev.lon < w || ev.lon > e)) return;
            if (fs.types.indexOf(ev.ls_type) < 0) return;
            if (fs.classes.length && fs.classes.indexOf(ev.cls || '__unclassified__') < 0) return;
            // Dual-handle range filters. Each side only applies when the
            // handle has moved off the no-filter position. NULL field
            // values fail the comparison and drop out the moment any
            // side activates.
            if (fs.minVol     !== null && !(ev.vol      >= fs.minVol))     return;
            if (fs.maxVol     !== null && !(ev.vol      <= fs.maxVol))     return;
            if (fs.minSrcArea !== null && !(ev.area_src >= fs.minSrcArea)) return;
            if (fs.maxSrcArea !== null && !(ev.area_src <= fs.maxSrcArea)) return;
            if (fs.minDepArea !== null && ev.ls_type === 'catastrophic' && !(ev.area_dep >= fs.minDepArea)) return;
            if (fs.maxDepArea !== null && ev.ls_type === 'catastrophic' && !(ev.area_dep <= fs.maxDepArea)) return;
            if (fs.minYear !== null || fs.maxYear !== null) {
                var yn = ev.year_num;
                if (yn === null) return;
                if (fs.minYear !== null && yn < fs.minYear) return;
                if (fs.maxYear !== null && yn > fs.maxYear) return;
            }
            if (fs.molards      && !ev.molards)         return;
            if (fs.stream       && !ev.stream_dam)      return;
            if (fs.headscarp    && !ev.headscarp)       return;
            if (fs.siteVolume   && !ev.has_site_volume) return;
            if (fs.supraglacial && !ev.supraglacial)    return;
            if (fs.permafrost   && !ev.permafrost)   return;
            if (fs.seismic && ev.timing !== 'point')  return;
            if (fs.timed   && ev.timing === 'point')  return;

            if (ev.timing === 'range' && ev.span - 1 > maxDays) return;

            count++;
            var hasVol = ev.vol !== null && ev.vol > 0;
            if (!hasVol) unknownVolCount++;
            var v = hasVol ? ev.vol : 0;
            var yd = isLeapYear(ev.year) ? 366 : 365;

            // perBin tallies volume (frac * vol) and per-bin max contribution.
            function tallyVol(bi, frac) {
                if (!hasVol) return;
                var contrib = frac * v;
                volBins[bi] += contrib;
                if (contrib > volMaxEv[bi]) volMaxEv[bi] = contrib;
            }

            if (ev.timing === 'point') {
                var pb = Math.min(36, Math.floor((ev.doy - 1) / 10));
                bins[pb] += 1.0;
                tallyVol(pb, 1.0);
            } else {
                if (ev.doy <= ev.doy_end) {
                    distributeRange(bins, ev.doy, ev.doy_end, ev.span, yd, tallyVol);
                } else {
                    distributeRange(bins, ev.doy,  yd, ev.span, yd, tallyVol);
                    distributeRange(bins, 1, ev.doy_end, ev.span, yd, tallyVol);
                }
            }
        });

        return {
            bins: bins, count: count,
            volBins: volBins, volMaxEv: volMaxEv,
            unknownVolCount: unknownVolCount,
        };
    }

    // Nice y-axis scale: returns {max, step} giving 3-6 ticks
    function niceScale(maxVal) {
        if (maxVal <= 0) return { max: 1, step: 0.5 };
        var steps = [0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 20, 50];
        for (var i = 0; i < steps.length; i++) {
            var ticks = Math.ceil(maxVal / steps[i]);
            if (ticks >= 3 && ticks <= 7) return { max: ticks * steps[i], step: steps[i] };
        }
        return { max: Math.ceil(maxVal), step: 1 };
    }

    // Magnitude-aware version for volume axes (handles 10^3 to 10^12 m³).
    function niceScaleVol(maxVal) {
        if (maxVal <= 0) return { max: 1, step: 1 };
        var mag = Math.pow(10, Math.floor(Math.log10(maxVal)));
        var fracs = [1, 2, 2.5, 5, 10];
        for (var i = 0; i < fracs.length; i++) {
            var step = fracs[i] * mag;
            var ticks = Math.ceil(maxVal / step);
            if (ticks >= 3 && ticks <= 7) return { max: ticks * step, step: step };
        }
        return { max: Math.ceil(maxVal / mag) * mag, step: mag };
    }

    // Volume formatters
    function fmtVolBig(m3) {
        if (!m3) return '0';
        if (m3 >= 1e9)  return (m3 / 1e9 ).toFixed(m3 >= 1e10 ? 1 : 2) + ' km³';
        if (m3 >= 1e6)  return (m3 / 1e6 ).toFixed(m3 >= 1e7  ? 1 : 2) + ' M m³';
        if (m3 >= 1e3)  return Math.round(m3 / 1e3) + ' k m³';
        return Math.round(m3) + ' m³';
    }
    function fmtVolTick(m3) {
        if (m3 <= 0) return '0';
        if (m3 >= 1e9)  return (m3 / 1e9 ) + 'B';   // billions
        if (m3 >= 1e6)  return (m3 / 1e6 ) + 'M';
        if (m3 >= 1e3)  return (m3 / 1e3 ) + 'k';
        return String(Math.round(m3));
    }

    // Bar palette: regular (light) and dominant-event slice (darker).
    var BAR_COLOR       = '#7090c8';
    var BAR_COLOR_DARK  = '#3f67b1';

    var FINAL_BIN_DAYS = 5.25;
    var MONTHS     = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    var MONTH_MID  = [16, 45, 75, 106, 136, 167, 197, 228, 259, 289, 320, 350];
    var MONTH_STRT = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335];

    function renderHistogram(result) {
        var canvas = document.getElementById('hist-canvas');
        var panel  = document.getElementById('hist-panel');
        if (!canvas || !panel) return;

        canvas.width  = panel.clientWidth;
        canvas.height = panel.clientHeight - panel.querySelector('.hist-header').offsetHeight;

        var ctx = canvas.getContext('2d');
        var W = canvas.width, H = canvas.height;
        var ml = 42, mr = 8, mt = 8, mb = 24;
        var cw = W - ml - mr, ch = H - mt - mb;

        var subtitle = document.getElementById('hist-subtitle');
        if (!result || result.count === 0) {
            ctx.clearRect(0, 0, W, H);
            ctx.fillStyle = '#aaa'; ctx.font = '11px sans-serif'; ctx.textAlign = 'center';
            var inView0 = (cbLimitView && cbLimitView.checked) ? ' in view' : '';
            ctx.fillText(result ? ('No timed events' + (inView0 ? ' in current view' : '')) : 'Loading…', W/2, H/2);
            if (subtitle) subtitle.textContent = '0 events with precise timing' + inView0;
            return;
        }
        var maxDays = histDaysSlider ? parseInt(histDaysSlider.value) : 30;
        var useVol = cbHistVol && cbHistVol.checked;
        var useLog = cbHistLog && cbHistLog.checked;

        // Pick which series we're rendering (count or volume) and apply final-bin normalization.
        var normF = 10 / FINAL_BIN_DAYS;
        var bc, bcMax;
        if (useVol) {
            bc    = result.volBins.slice();
            bcMax = result.volMaxEv.slice();
            bc[36]    *= normF;
            bcMax[36] *= normF;
        } else {
            bc    = result.bins.slice();
            bcMax = null;
            bc[36] *= normF;
        }

        var inView = (cbLimitView && cbLimitView.checked) ? ' in view' : '';
        var subtitleText = result.count + ' event' + (result.count === 1 ? '' : 's') +
            inView + ' \u2264\u202f' + maxDays + '\u202fd uncertainty';
        if (useVol && result.unknownVolCount) {
            subtitleText += ' \u00b7 ' + result.unknownVolCount + ' with unknown volume';
        }
        if (subtitle) subtitle.textContent = subtitleText;

        // Y-axis scale (linear or log)
        var maxCombined = 0;
        for (var b = 0; b < 37; b++) maxCombined = Math.max(maxCombined, bc[b]);
        var ymax, ystep, logMin, logMax;
        if (useLog && maxCombined > 0) {
            var minPos = Infinity;
            for (var b2 = 0; b2 < 37; b2++) if (bc[b2] > 0) minPos = Math.min(minPos, bc[b2]);
            logMin = Math.floor(Math.log10(minPos) - 0.01);
            logMax = Math.ceil(Math.log10(maxCombined) + 0.01);
            if (logMax <= logMin) logMax = logMin + 1;
        } else {
            var sc = useVol ? niceScaleVol(maxCombined) : niceScale(maxCombined);
            ymax = sc.max; ystep = sc.step;
        }

        function yPixel(val) {
            if (val <= 0) return mt + ch;
            if (useLog) {
                var lv = Math.log10(val);
                if (lv < logMin) return mt + ch;
                return mt + ch - (lv - logMin) / (logMax - logMin) * ch;
            }
            return mt + ch - (val / ymax) * ch;
        }

        var totalUnits = 36 + FINAL_BIN_DAYS / 10;
        var barUnit = cw / totalUnits;

        ctx.clearRect(0, 0, W, H);

        // Grid lines
        ctx.strokeStyle = '#e8e8e8'; ctx.lineWidth = 0.5;
        if (useLog) {
            for (var exp = logMin + 1; exp <= logMax; exp++) {
                var gyL = mt + ch - (exp - logMin) / (logMax - logMin) * ch;
                ctx.beginPath(); ctx.moveTo(ml, gyL); ctx.lineTo(ml + cw, gyL); ctx.stroke();
            }
        } else {
            for (var v = ystep; v <= ymax + 0.001; v += ystep) {
                var gy = mt + ch - (v / ymax) * ch;
                ctx.beginPath(); ctx.moveTo(ml, gy); ctx.lineTo(ml + cw, gy); ctx.stroke();
            }
        }

        // Month dividers
        ctx.strokeStyle = '#ddd'; ctx.lineWidth = 0.5;
        MONTH_STRT.forEach(function (doy) {
            var mx = ml + (doy - 1) / 10 * barUnit;
            ctx.beginPath(); ctx.moveTo(mx, mt); ctx.lineTo(mx, mt + ch); ctx.stroke();
        });

        // Bars: vol mode draws darker "largest single contributor" slice at the bottom of each bar,
        // with the rest of the bin's volume in the lighter color above.
        for (var b3 = 0; b3 < 37; b3++) {
            var bw = b3 < 36 ? barUnit : barUnit * FINAL_BIN_DAYS / 10;
            var bx = ml + b3 * barUnit;
            var gap = bw > 3 ? 0.8 : 0;
            var bxDraw = bx + gap/2, bwDraw = bw - gap;
            if (bc[b3] <= 0) continue;
            var yTop = yPixel(bc[b3]);
            var yBot = mt + ch;
            if (useVol && bcMax) {
                var maxSeg = Math.min(bcMax[b3], bc[b3]);
                var ySplit = yPixel(bc[b3] - maxSeg);
                if (ySplit > yTop) {
                    ctx.fillStyle = BAR_COLOR;
                    ctx.fillRect(bxDraw, yTop, bwDraw, ySplit - yTop);
                }
                if (yBot > ySplit) {
                    ctx.fillStyle = BAR_COLOR_DARK;
                    ctx.fillRect(bxDraw, ySplit, bwDraw, yBot - ySplit);
                }
            } else {
                ctx.fillStyle = BAR_COLOR;
                ctx.fillRect(bxDraw, yTop, bwDraw, yBot - yTop);
            }
        }

        // Save hit-test data for mouseover
        _histHits = [];
        for (var bh = 0; bh < 37; bh++) {
            var bhw = bh < 36 ? barUnit : barUnit * FINAL_BIN_DAYS / 10;
            _histHits.push({
                b: bh,
                x0: ml + bh * barUnit,
                x1: ml + bh * barUnit + bhw,
                val: bc[bh],
                maxEv: useVol && bcMax ? bcMax[bh] : null,
            });
        }

        ctx.strokeStyle = '#999'; ctx.lineWidth = 0.8;
        ctx.beginPath(); ctx.moveTo(ml, mt); ctx.lineTo(ml, mt + ch); ctx.lineTo(ml + cw, mt + ch); ctx.stroke();

        // Y-axis labels
        ctx.fillStyle = '#666'; ctx.font = '8px sans-serif'; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
        if (useLog) {
            for (var exp2 = logMin; exp2 <= logMax; exp2++) {
                var lvVal = Math.pow(10, exp2);
                var tyL = mt + ch - (exp2 - logMin) / (logMax - logMin) * ch;
                ctx.fillText(useVol ? fmtVolTick(lvVal) : (lvVal >= 1 ? String(lvVal) : lvVal.toFixed(-exp2)), ml - 3, tyL);
            }
        } else {
            for (var v2 = 0; v2 <= ymax + 0.001; v2 += ystep) {
                var ty = mt + ch - (v2 / ymax) * ch;
                ctx.fillText(useVol ? fmtVolTick(v2) : (v2 % 1 === 0 ? v2 : v2.toFixed(1)), ml - 3, ty);
            }
        }

        ctx.save();
        ctx.translate(8, mt + ch / 2); ctx.rotate(-Math.PI / 2);
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = '#888'; ctx.font = '8px sans-serif';
        ctx.fillText(useVol ? 'volume (m\u00b3) / 10 days' : 'events / 10 days', 0, 0);
        ctx.restore();

        ctx.fillStyle = '#777'; ctx.font = '8px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'top';
        MONTHS.forEach(function (m, i) {
            var lx = ml + (MONTH_MID[i] - 1) / 10 * barUnit;
            ctx.fillText(m, lx, mt + ch + 5);
        });

        // Legend swatch (top-right corner of plot area)
        var legendX = ml + cw - 90, legendY = mt + 3;
        if (useVol) {
            ctx.fillStyle = BAR_COLOR_DARK; ctx.fillRect(legendX, legendY, 9, 8);
            ctx.fillStyle = '#555'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.font = '8px sans-serif';
            ctx.fillText('Largest event', legendX + 12, legendY + 4);
            ctx.fillStyle = BAR_COLOR; ctx.fillRect(legendX, legendY + 11, 9, 8);
            ctx.fillStyle = '#555';
            ctx.fillText('Other events', legendX + 12, legendY + 15);
        } else {
            ctx.fillStyle = BAR_COLOR; ctx.fillRect(legendX, legendY, 9, 8);
            ctx.fillStyle = '#555'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.font = '8px sans-serif';
            ctx.fillText('Catastrophic', legendX + 12, legendY + 4);
        }
    }

    function updateHistogram() {
        if (!histPanel || histPanel.classList.contains('hidden')) return;
        renderHistogram(computeHistogram());
    }

    function onHistResize() {
        if (histPanel && !histPanel.classList.contains('hidden')) renderHistogram(computeHistogram());
    }

    // Histogram floating panel
    histFP = makeFloatingPanel(histPanel, {
        handle:   histPanel ? histPanel.querySelector('.hist-header') : null,
        toggle:   document.getElementById('hist-toggle'),
        close:    document.getElementById('hist-close'),
        onResize: updateHistogram,
        onChange: writeUrlState
    });

    // ===========================================================================
    // Timeline histogram
    // ===========================================================================

    // Monthly binning helpers for timeline 2012+
    // Key format for monthly bins: 'YYYY-M' (M = 0-11)
    var TL_MONTHLY_START = 2012;

    function tlParseDate(str) {
        if (!str) return null;
        var p = str.split('-');
        return new Date(parseInt(p[0]), parseInt(p[1]) - 1, parseInt(p[2]));
    }

    // Distribute weight across monthly bins for a date range [d0str, d1str].
    // Optional perBin callback receives (key, fraction) for parallel volume tracking.
    function tlDistributeRange(bins, d0str, d1str, weight, perBin) {
        var d0 = tlParseDate(d0str), d1 = tlParseDate(d1str);
        if (!d0 || !d1 || d1 < d0) return;
        var totalMs = d1 - d0 + 864e5;
        var cur = new Date(d0.getFullYear(), d0.getMonth(), 1);
        while (cur <= d1) {
            var yr = cur.getFullYear(), mo = cur.getMonth();
            if (yr >= TL_MONTHLY_START && yr <= CURRENT_YEAR) {
                var mStart = new Date(yr, mo, 1);
                var mEnd   = new Date(yr, mo + 1, 0);  // last day of month
                var oStart = d0 > mStart ? d0 : mStart;
                var oEnd   = d1 < mEnd   ? d1 : mEnd;
                if (oStart <= oEnd) {
                    var key = yr + '-' + mo;
                    if (bins[key] !== undefined) {
                        var frac = (oEnd - oStart + 864e5) / totalMs;
                        bins[key] += frac * weight;
                        if (perBin) perBin(key, frac);
                    }
                }
            }
            cur = new Date(yr, mo + 1, 1);
        }
    }

    function computeTimeline() {
        if (!_timelineEvents) return null;
        var b = map.getBounds();
        var w = b.getWest(), e = b.getEast(), s = b.getSouth(), n = b.getNorth();
        var fs = currentFilterState();

        // Pre-populate all expected bins with 0
        var bins = {}, volBins = {}, volMaxEv = {};
        function initKey(k) { bins[k] = 0; volBins[k] = 0; volMaxEv[k] = 0; }
        initKey('H');   // Holocene
        initKey('M');   // Modern pre-2000
        for (var y = 2000; y < TL_MONTHLY_START; y++) initKey(String(y));
        for (var y = TL_MONTHLY_START; y <= CURRENT_YEAR; y++) {
            for (var mo = 0; mo < 12; mo++) initKey(y + '-' + mo);
        }

        var total = 0;
        var unknownVolCount = 0;
        _timelineEvents.forEach(function (ev) {
            // Map-view clip only when the pinned "Limit to map view" toggle is on.
            if (cbLimitView && cbLimitView.checked &&
                (ev.lat < s || ev.lat > n || ev.lon < w || ev.lon > e)) return;
            if (fs.types.indexOf(ev.ls_type) < 0) return;
            if (fs.classes.length && fs.classes.indexOf(ev.cls || '__unclassified__') < 0) return;
            // See histogram filter above — dual-handle ranges with NULL exclusion.
            if (fs.minVol     !== null && !(ev.vol      >= fs.minVol))     return;
            if (fs.maxVol     !== null && !(ev.vol      <= fs.maxVol))     return;
            if (fs.minSrcArea !== null && !(ev.area_src >= fs.minSrcArea)) return;
            if (fs.maxSrcArea !== null && !(ev.area_src <= fs.maxSrcArea)) return;
            if (fs.minDepArea !== null && ev.ls_type === 'catastrophic' && !(ev.area_dep >= fs.minDepArea)) return;
            if (fs.maxDepArea !== null && ev.ls_type === 'catastrophic' && !(ev.area_dep <= fs.maxDepArea)) return;
            if (fs.minYear !== null || fs.maxYear !== null) {
                var yn = ev.year_num;
                if (yn === null) return;
                if (fs.minYear !== null && yn < fs.minYear) return;
                if (fs.maxYear !== null && yn > fs.maxYear) return;
            }
            if (fs.molards      && !ev.molards)         return;
            if (fs.stream       && !ev.stream_dam)      return;
            if (fs.headscarp    && !ev.headscarp)       return;
            if (fs.siteVolume   && !ev.has_site_volume) return;
            if (fs.supraglacial && !ev.supraglacial)    return;
            if (fs.permafrost   && !ev.permafrost)   return;
            if (fs.seismic      && !ev.has_seismic)  return;
            if (fs.post2012     && !ev.post_2012)    return;

            total++;
            var hasVol = ev.vol !== null && ev.vol > 0;
            if (!hasVol) unknownVolCount++;
            var v = hasVol ? ev.vol : 0;

            // Tally volume contribution into bin `k` with fraction `frac`.
            function tally(k, frac) {
                if (!hasVol || bins[k] === undefined) return;
                var contrib = frac * v;
                volBins[k] += contrib;
                if (contrib > volMaxEv[k]) volMaxEv[k] = contrib;
            }

            var yn = ev.year_num;

            if (yn === -1)              { bins['H']++; tally('H', 1); return; }
            if (yn === 0 || yn < 2000)  { bins['M']++; tally('M', 1); return; }
            if (yn < TL_MONTHLY_START)  {
                var ak = String(yn);
                if (bins[ak] !== undefined) { bins[ak]++; tally(ak, 1); }
                return;
            }

            // 2012+: use monthly bins with date spreading
            if (yn > CURRENT_YEAR) { total--; if (!hasVol) unknownVolCount--; return; }

            if (ev.tl_pt) {
                // Point event (seismic): assign to one month
                var d = tlParseDate(ev.tl_pt);
                if (d) {
                    var k = d.getFullYear() + '-' + d.getMonth();
                    if (bins[k] !== undefined) { bins[k] += 1; tally(k, 1); }
                }
            } else if (ev.tl_d0 && ev.tl_d1) {
                // Date range: spread proportionally
                tlDistributeRange(bins, ev.tl_d0, ev.tl_d1, 1, tally);
            } else {
                // Year precision only: spread evenly across 12 months
                for (var mo = 0; mo < 12; mo++) {
                    var k = yn + '-' + mo;
                    if (bins[k] !== undefined) { bins[k] += 1/12; tally(k, 1/12); }
                }
            }
        });

        // Normalize raw counts AND volumes to per-year rate using the same scaling.
        function normalize(yearScale) {
            bins['H']    /= yearScale.H;    volBins['H']    /= yearScale.H;    volMaxEv['H']    /= yearScale.H;
            bins['M']    /= yearScale.M;    volBins['M']    /= yearScale.M;    volMaxEv['M']    /= yearScale.M;
            for (var yr2 = TL_MONTHLY_START; yr2 <= CURRENT_YEAR; yr2++) {
                for (var mo2 = 0; mo2 < 12; mo2++) {
                    var kk = yr2 + '-' + mo2;
                    bins[kk]     *= 12;
                    volBins[kk]  *= 12;
                    volMaxEv[kk] *= 12;
                }
            }
        }
        normalize({ H: 10000, M: 150 });

        // Express the rate per MONTH (the finest bin granularity) instead of per
        // year: divide every bin + volume by 12. The chart shape is unchanged —
        // only the y-axis units — but the monthly-resolution tail reads more
        // intuitively (one event in a month → 1 ev/month, not 12 ev/yr).
        Object.keys(bins).forEach(function (k) {
            bins[k]     /= 12;
            volBins[k]  /= 12;
            volMaxEv[k] /= 12;
        });

        return {
            bins: bins, total: total,
            volBins: volBins, volMaxEv: volMaxEv,
            unknownVolCount: unknownVolCount,
        };
    }

    function renderTimeline(result) {
        var canvas = document.getElementById('timing-canvas');
        var panel  = document.getElementById('timing-panel');
        if (!canvas || !panel) return;

        var header = panel.querySelector('.timing-header');
        canvas.width  = panel.clientWidth;
        canvas.height = panel.clientHeight - (header ? header.offsetHeight : 26);

        var ctx = canvas.getContext('2d');
        var W = canvas.width, H = canvas.height;
        var ml = 42, mr = 10, mt = 8, mb = 42;
        var cw = W - ml - mr, ch = H - mt - mb;

        var subtitle = document.getElementById('timing-subtitle');
        if (!result || result.total === 0) {
            ctx.clearRect(0, 0, W, H);
            ctx.fillStyle = '#aaa'; ctx.font = '11px sans-serif'; ctx.textAlign = 'center';
            var inViewTl0 = (cbLimitView && cbLimitView.checked) ? ' in view' : '';
            ctx.fillText(result ? ('No events with temporal data' + (inViewTl0 ? ' in current view' : '')) : 'Loading\u2026', W/2, H/2);
            if (subtitle) subtitle.textContent = '0 events with temporal data' + inViewTl0;
            return;
        }
        var useVolTl = cbTimingVol && cbTimingVol.checked;
        var inViewTl = (cbLimitView && cbLimitView.checked) ? ' in view' : '';
        var subtitleTl = result.total + ' event' + (result.total === 1 ? '' : 's') + ' with temporal data' + inViewTl;
        if (useVolTl && result.unknownVolCount) {
            subtitleTl += ' \u00b7 ' + result.unknownVolCount + ' with unknown volume';
        }
        if (subtitle) subtitle.textContent = subtitleTl;

        // Pick which bin series to render
        var binsR    = useVolTl ? result.volBins  : result.bins;
        var binsRMax = useVolTl ? result.volMaxEv : null;

        // Layout:  Holocene (H_U) | Modern pre-2000 (M_U) | annual 2000-2011 (TL_ANN_UNITS each) | monthly 2012+ (1 per month)
        var H_U = TL_HOL_UNITS, M_U = TL_MOD_UNITS, A_U = TL_ANN_UNITS;
        var annualYears = TL_MONTHLY_START - 2000;           // 12
        var monthlyMonths = (CURRENT_YEAR - TL_MONTHLY_START + 1) * 12;
        var totalUnits = H_U + M_U + annualYears * A_U + monthlyMonths;
        var barUnit = cw / totalUnits;

        // Pixel x for the left edge of a bin key
        function xFor(key) {
            if (key === 'H') return ml;
            if (key === 'M') return ml + H_U * barUnit;
            var dash = key.indexOf('-');
            if (dash < 0) {
                // annual bin 2000-2011
                return ml + (H_U + M_U + (parseInt(key) - 2000) * A_U) * barUnit;
            }
            // monthly bin 'YYYY-M'
            var yr = parseInt(key.slice(0, dash)), mo = parseInt(key.slice(dash + 1));
            return ml + (H_U + M_U + annualYears * A_U + (yr - TL_MONTHLY_START) * 12 + mo) * barUnit;
        }
        function wFor(key) {
            if (key === 'H') return H_U * barUnit;
            if (key === 'M') return M_U * barUnit;
            if (key.indexOf('-') < 0) return A_U * barUnit;  // annual
            return barUnit;  // monthly
        }

        var useLog = cbTimingLog && cbTimingLog.checked;
        var maxVal = 0;
        Object.keys(binsR).forEach(function (k) { maxVal = Math.max(maxVal, binsR[k]); });

        // Y-scale setup
        var ymax, ystep, logMin, logMax;
        if (useLog && maxVal > 0) {
            var minPos = Infinity;
            Object.keys(binsR).forEach(function (k) { var v = binsR[k]; if (v > 0) minPos = Math.min(minPos, v); });
            logMin = Math.floor(Math.log10(minPos) - 0.01);
            logMax = Math.ceil(Math.log10(maxVal)  + 0.01);
            if (logMax <= logMin) logMax = logMin + 1;
            ymax = logMax; ystep = 1;  // unused in log mode but keep refs consistent
        } else {
            var sc = useVolTl ? niceScaleVol(maxVal) : niceScale(maxVal);
            ymax = sc.max; ystep = sc.step;
        }

        function yPixel(val) {
            if (val <= 0) return mt + ch;
            if (useLog) {
                var lv = Math.log10(val);
                if (lv < logMin) return mt + ch;
                return mt + ch - Math.max(0, (lv - logMin) / (logMax - logMin)) * ch;
            }
            return mt + ch - (val / ymax) * ch;
        }

        ctx.clearRect(0, 0, W, H);

        // 2012+ highlight
        var x2012 = xFor(TL_MONTHLY_START + '-0');
        ctx.fillStyle = 'rgba(63,103,177,0.07)';
        ctx.fillRect(x2012, mt, ml + cw - x2012, ch);

        // Grid lines
        ctx.strokeStyle = '#e8e8e8'; ctx.lineWidth = 0.5;
        if (useLog) {
            for (var exp = logMin + 1; exp <= logMax; exp++) {
                var gy = mt + ch - (exp - logMin) / (logMax - logMin) * ch;
                ctx.beginPath(); ctx.moveTo(ml, gy); ctx.lineTo(ml + cw, gy); ctx.stroke();
            }
        } else {
            for (var v = ystep; v <= ymax + 0.001; v += ystep) {
                var gy = mt + ch - (v / ymax) * ch;
                ctx.beginPath(); ctx.moveTo(ml, gy); ctx.lineTo(ml + cw, gy); ctx.stroke();
            }
        }

        // Month dividers (2012+) — very subtle
        ctx.strokeStyle = 'rgba(0,0,0,0.06)'; ctx.lineWidth = 0.4;
        for (var y = TL_MONTHLY_START; y <= CURRENT_YEAR; y++) {
            for (var mo = 1; mo < 12; mo++) {
                var mx = xFor(y + '-' + mo);
                ctx.beginPath(); ctx.moveTo(mx, mt); ctx.lineTo(mx, mt + ch); ctx.stroke();
            }
        }

        // Year dividers (2012+) and section dividers
        var xModStart = xFor('M'), x2000 = xFor('2000');
        ctx.lineWidth = 0.7; ctx.strokeStyle = '#bbb';
        ctx.beginPath(); ctx.moveTo(xModStart, mt); ctx.lineTo(xModStart, mt + ch); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(x2000,     mt); ctx.lineTo(x2000,     mt + ch); ctx.stroke();

        ctx.strokeStyle = 'rgba(63,103,177,0.3)'; ctx.lineWidth = 0.8;
        ctx.beginPath(); ctx.moveTo(x2012, mt); ctx.lineTo(x2012, mt + ch); ctx.stroke();

        ctx.strokeStyle = 'rgba(0,0,0,0.12)'; ctx.lineWidth = 0.5;
        for (var y = TL_MONTHLY_START + 1; y <= CURRENT_YEAR; y++) {
            var yx = xFor(y + '-0');
            ctx.beginPath(); ctx.moveTo(yx, mt); ctx.lineTo(yx, mt + ch); ctx.stroke();
        }

        // Bars: vol mode draws a darker "largest single contributor" slice at the bottom.
        Object.keys(binsR).forEach(function (k) {
            var val = binsR[k];
            if (val <= 0) return;
            var bx = xFor(k), bw = wFor(k);
            var gap = bw > 8 ? 1.5 : bw > 2 ? 0.4 : 0;
            var bxDraw = bx + gap, bwDraw = bw - gap * 2;
            var yTop = yPixel(val);
            var yBot = mt + ch;
            if (useVolTl && binsRMax) {
                var maxSeg = Math.min(binsRMax[k] || 0, val);
                var ySplit = yPixel(val - maxSeg);
                if (ySplit > yTop) {
                    ctx.fillStyle = BAR_COLOR;
                    ctx.fillRect(bxDraw, yTop, bwDraw, ySplit - yTop);
                }
                if (yBot > ySplit) {
                    ctx.fillStyle = BAR_COLOR_DARK;
                    ctx.fillRect(bxDraw, ySplit, bwDraw, yBot - ySplit);
                }
            } else {
                ctx.fillStyle = BAR_COLOR;
                if (yBot > yTop) ctx.fillRect(bxDraw, yTop, bwDraw, yBot - yTop);
            }
        });

        // Save hit-test data for mouseover
        _tlHits = [];
        Object.keys(binsR).forEach(function(k) {
            _tlHits.push({
                key: k,
                x0: xFor(k), x1: xFor(k) + wFor(k),
                val: binsR[k],
                maxEv: useVolTl && binsRMax ? binsRMax[k] : null,
            });
        });

        // Annual average lines across each 2012+ year's monthly columns
        ctx.strokeStyle = 'rgba(30,80,160,0.55)';
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 2]);
        for (var ay = TL_MONTHLY_START; ay <= CURRENT_YEAR; ay++) {
            var sum = 0, cnt = 0;
            for (var am = 0; am < 12; am++) { sum += binsR[ay + '-' + am] || 0; cnt++; }
            var avg = sum / cnt;
            if (avg > 0) {
                var lx0 = xFor(ay + '-0'), lx1 = xFor(ay + '-0') + 12 * barUnit;
                var ly  = yPixel(avg);
                ctx.beginPath(); ctx.moveTo(lx0, ly); ctx.lineTo(lx1, ly); ctx.stroke();
            }
        }
        ctx.setLineDash([]);

        // Axes
        ctx.strokeStyle = '#999'; ctx.lineWidth = 0.8;
        ctx.beginPath(); ctx.moveTo(ml, mt); ctx.lineTo(ml, mt + ch); ctx.lineTo(ml + cw, mt + ch); ctx.stroke();

        // Y-axis labels
        ctx.fillStyle = '#666'; ctx.font = '8px sans-serif'; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
        if (useLog) {
            for (var exp = logMin; exp <= logMax; exp++) {
                var lvVal = Math.pow(10, exp);
                var ty = mt + ch - (exp - logMin) / (logMax - logMin) * ch;
                var lbl;
                if (useVolTl) {
                    lbl = fmtVolTick(lvVal);
                } else {
                    lbl = lvVal >= 1 ? (lvVal >= 100 ? String(Math.round(lvVal)) : String(lvVal)) : lvVal.toFixed(-exp);
                }
                ctx.fillText(lbl, ml - 3, ty);
            }
        } else {
            for (var v = 0; v <= ymax + 0.001; v += ystep) {
                ctx.fillText(useVolTl ? fmtVolTick(v) : (v % 1 === 0 ? v : v.toFixed(1)), ml - 3, mt + ch - (v / ymax) * ch);
            }
        }

        // Y-axis title
        ctx.save();
        ctx.translate(8, mt + ch / 2); ctx.rotate(-Math.PI / 2);
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = '#888'; ctx.font = '8px sans-serif';
        ctx.fillText(useVolTl ? 'volume (m³) / month' : 'events / month', 0, 0);
        ctx.restore();

        // X-axis labels
        var yLbl = mt + ch + 5;
        ctx.textBaseline = 'top'; ctx.textAlign = 'center'; ctx.font = '8px sans-serif';

        ctx.fillStyle = '#555';
        ctx.fillText('Holocene', ml + H_U * barUnit / 2, yLbl);
        ctx.fillStyle = '#666'; ctx.font = '7.5px sans-serif';
        ctx.fillText('pre-2000', xModStart + M_U * barUnit / 2, yLbl);

        // Annual 2000-2011 milestones — center on each A_U-wide bar
        ctx.fillStyle = '#777'; ctx.font = '8px sans-serif';
        [2000, 2002, 2004, 2006, 2008, 2010].forEach(function (yr) {
            ctx.fillText(String(yr), xFor(String(yr)) + A_U * barUnit / 2, yLbl);
        });

        // Monthly-era year labels: centered on each year's 12 bins
        // Show every year if there's room (barUnit*12 wide), else every 2 years
        var yearPx = barUnit * 12;
        var yearStep = yearPx >= 22 ? 1 : yearPx >= 11 ? 2 : 3;
        for (var y = TL_MONTHLY_START; y <= CURRENT_YEAR; y += yearStep) {
            var yCx = xFor(y + '-0') + yearPx / 2;
            ctx.fillText(String(y), yCx, yLbl);
        }

        // "more complete" label
        ctx.fillStyle = 'rgba(63,103,177,0.55)'; ctx.font = '7.5px sans-serif'; ctx.textAlign = 'left';
        ctx.fillText('more complete \u25b8', x2012 + 3, mt + 2);

        // "Modern era" span annotation
        var ySpan = mt + ch + 26, tickH = 3.5, spanLabel = 'Modern era';
        ctx.font = '7.5px sans-serif';
        var lblW = ctx.measureText(spanLabel).width + 8;
        var spanMid = (xModStart + x2012) / 2;

        ctx.strokeStyle = '#999'; ctx.lineWidth = 0.8;
        ctx.beginPath(); ctx.moveTo(xModStart, ySpan); ctx.lineTo(spanMid - lblW/2, ySpan); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(spanMid + lblW/2, ySpan); ctx.lineTo(x2012, ySpan); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(xModStart, ySpan - tickH); ctx.lineTo(xModStart, ySpan + tickH); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(x2012,     ySpan - tickH); ctx.lineTo(x2012,     ySpan + tickH); ctx.stroke();
        ctx.strokeStyle = '#aaa';
        ctx.beginPath(); ctx.moveTo(x2000, ySpan - tickH - 2); ctx.lineTo(x2000, ySpan + tickH + 2); ctx.stroke();

        ctx.fillStyle = '#888'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText(spanLabel, spanMid, ySpan);
    }

    function updateTimeline() {
        if (!timingPanel || timingPanel.classList.contains('hidden')) return;
        renderTimeline(computeTimeline());
    }

    function onTimingResize() {
        if (timingPanel && !timingPanel.classList.contains('hidden')) renderTimeline(computeTimeline());
    }

    // Listeners for panel-mode checkboxes (refs declared near applyUrlState).
    if (cbTimingLog) cbTimingLog.addEventListener('change', function () { updateTimeline(); writeUrlState(); });
    if (cbTimingVol) cbTimingVol.addEventListener('change', function () { updateTimeline(); writeUrlState(); });
    if (cbHistLog)   cbHistLog.addEventListener('change',   function () { updateHistogram(); writeUrlState(); });
    if (cbHistVol)   cbHistVol.addEventListener('change',   function () { updateHistogram(); writeUrlState(); });

    // Timeline floating panel
    timingFP = makeFloatingPanel(timingPanel, {
        handle:   timingPanel ? timingPanel.querySelector('.timing-header') : null,
        toggle:   document.getElementById('timing-toggle'),
        close:    document.getElementById('timing-close'),
        onResize: updateTimeline,
        onChange: writeUrlState
    });

    // ===========================================================================
    // Susceptibility scatter (lw × n10) — brushable, two-way synced to the
    // n10 / lw sliders. Drag a box to set the slider ranges; moving the sliders
    // redraws the box. Dots colored by landslide type. Values from susc_values.
    // ===========================================================================
    var SVGNS = 'http://www.w3.org/2000/svg';
    var SUSC_VMAX = 81;
    var _scDims = null;   // {x0,y0,w,h} plot rect in px; set by scatterDrawAll
    var scatterSvg  = document.getElementById('scatter-svg');
    var scatterHeat = document.getElementById('scatter-heat');

    function _svgEl(name, attrs) {
        var el = document.createElementNS(SVGNS, name);
        for (var k in attrs) el.setAttribute(k, attrs[k]);
        return el;
    }
    function _scVx(lw)  { return _scDims.x0 + (lw  / SUSC_VMAX) * _scDims.w; }
    function _scVy(n10) { return _scDims.y0 + (1 - n10 / SUSC_VMAX) * _scDims.h; }
    function _scPxToLw(px)  { return Math.max(0, Math.min(SUSC_VMAX, Math.round((px - _scDims.x0) / _scDims.w * SUSC_VMAX))); }
    function _scPxToN10(py) { return Math.max(0, Math.min(SUSC_VMAX, Math.round((1 - (py - _scDims.y0) / _scDims.h) * SUSC_VMAX))); }

    function scatterDrawAll() {
        if (!scatterSvg || !scatterPanel || scatterPanel.classList.contains('hidden')) return;
        var W = scatterSvg.clientWidth, H = scatterSvg.clientHeight;
        if (!W || !H) return;
        var padL = 30, padR = 10, padT = 8, padB = 22;
        _scDims = { x0: padL, y0: padT, w: W - padL - padR, h: H - padT - padB };
        while (scatterSvg.firstChild) scatterSvg.removeChild(scatterSvg.firstChild);

        // axes box
        scatterSvg.appendChild(_svgEl('rect', { x: _scDims.x0, y: _scDims.y0, width: _scDims.w, height: _scDims.h,
            fill: 'none', stroke: '#ddd', 'stroke-width': 1 }));
        // ticks + labels
        [0, 20, 40, 60, 81].forEach(function (t) {
            var x = _scVx(t), y = _scVy(t);
            scatterSvg.appendChild(_svgEl('line', { x1: x, y1: _scDims.y0 + _scDims.h, x2: x, y2: _scDims.y0 + _scDims.h + 3, stroke: '#bbb' }));
            var xl = _svgEl('text', { x: x, y: _scDims.y0 + _scDims.h + 13, 'text-anchor': 'middle', 'font-size': 9, fill: '#888' });
            xl.textContent = t; scatterSvg.appendChild(xl);
            scatterSvg.appendChild(_svgEl('line', { x1: _scDims.x0 - 3, y1: y, x2: _scDims.x0, y2: y, stroke: '#bbb' }));
            var yl = _svgEl('text', { x: _scDims.x0 - 5, y: y + 3, 'text-anchor': 'end', 'font-size': 9, fill: '#888' });
            yl.textContent = t; scatterSvg.appendChild(yl);
        });
        var xt = _svgEl('text', { x: _scDims.x0 + _scDims.w / 2, y: H - 1, 'text-anchor': 'middle', 'font-size': 9, fill: '#666' });
        xt.textContent = 'lw →'; scatterSvg.appendChild(xt);
        var yt = _svgEl('text', { x: 9, y: _scDims.y0 + _scDims.h / 2, 'text-anchor': 'middle', 'font-size': 9, fill: '#666',
            transform: 'rotate(-90 9 ' + (_scDims.y0 + _scDims.h / 2) + ')' });
        yt.textContent = 'n10 →'; scatterSvg.appendChild(yt);

        // (landslides are drawn as a grid on the canvas in scatterDrawHeat)
        // selection box + transient drag rect (positioned later)
        scatterSvg.appendChild(_svgEl('rect', { id: 'scatter-box', fill: 'rgba(60,103,177,0.12)',
            stroke: '#3f67b1', 'stroke-width': 1, 'stroke-dasharray': '3 2', display: 'none', 'pointer-events': 'none' }));
        scatterSvg.appendChild(_svgEl('rect', { id: 'scatter-drag', fill: 'rgba(0,0,0,0.06)',
            stroke: '#666', 'stroke-width': 1, display: 'none', 'pointer-events': 'none' }));
        scatterSyncBox();
        scatterDrawHeat();   // terrain backdrop on the canvas behind (drawn last so a
                             // heat error can't abort the interactive svg layer above)
    }

    // Position the dashed selection box from the current slider values.
    function scatterSyncBox() {
        if (!scatterSvg || !_scDims) return;
        var box = document.getElementById('scatter-box');
        if (!box || !suscLwDual || !suscN10Dual) return;
        var lwLo = parseFloat(suscLwDual.minEl.value), lwHi = parseFloat(suscLwDual.maxEl.value);
        var nLo  = parseFloat(suscN10Dual.minEl.value), nHi = parseFloat(suscN10Dual.maxEl.value);
        var active = lwLo > 0 || lwHi < SUSC_VMAX || nLo > 0 || nHi < SUSC_VMAX;
        // "clear box" is only meaningful when a selection exists — dim it otherwise
        // so it's not an enigmatic no-op.
        var clr = document.getElementById('scatter-clear');
        if (clr) clr.classList.toggle('disabled', !active);
        if (!active) { box.setAttribute('display', 'none'); return; }
        var x = _scVx(lwLo), w = _scVx(lwHi) - x;
        var y = _scVy(nHi),  h = _scVy(nLo) - y;
        box.setAttribute('x', x); box.setAttribute('y', y);
        box.setAttribute('width', Math.max(0, w)); box.setAttribute('height', Math.max(0, h));
        box.setAttribute('display', 'block');
    }

    function _scatterSetSliders(lwLo, lwHi, nLo, nHi) {
        suscLwDual.minEl.value = lwLo;  suscLwDual.maxEl.value = lwHi;  suscLwDual.refresh();
        suscN10Dual.minEl.value = nLo;  suscN10Dual.maxEl.value = nHi;  suscN10Dual.refresh();
        buildFilter();   // applies the filter, updates counts, redraws the box
    }

    // ColorBrewer ramps: pale Blues for the terrain backdrop (stays subordinate),
    // dark Reds for the landslide grid drawn on top. Both discretized (binned).
    // Terrain = pale Blues; landslides = dark Reds. Constraint (colorblind-safe):
    // every red is DARKER in value than every blue, so the two layers separate on
    // luminance, not just hue. Darkest blue (#5793c3) ≈ L0.54; lightest red
    // (#d7301f) ≈ L0.32.
    var TERRAIN_RAMP = ['#f7fbff','#d8e7f5','#b3d0e8','#86b3d8','#5793c3'];
    var LS_RAMP      = ['#d7301f','#a50f15','#7a0a10','#560409','#380006'];
    var CELL_KM2 = 0.09 * 0.09;       // one 90 m raster cell = 0.0081 km²
    var P_PER_KKM2 = 1000 / CELL_KM2; // fraction -> landslides per 1000 km²
    var _lsGrid = null;               // landslide centroid counts per (lw,n10) cell

    function scatterComputeLsGrid() {
        var B = SUSC_TERRAIN ? SUSC_TERRAIN.size : 82;
        var g = new Int32Array(B * B);
        if (_featuresData && _featuresData.features) {
            _featuresData.features.forEach(function (ft) {
                var p = ft.properties;
                if (p.n10 == null || p.lw == null) return;
                g[p.n10 * B + p.lw]++;
            });
        }
        _lsGrid = g; return g;
    }
    // Quantile (equal-count) binner: each of n bins holds ~1/n of the supplied
    // nonzero cell values, so the color range is used evenly regardless of the
    // value distribution. edges = n+1 quantile boundaries (the legend labels them
    // under equal-width swatches). Ties push equal values into the higher bin.
    function _quantileBinner(vals, n) {
        var s = vals.slice().sort(function (a, b) { return a - b; });
        var m = s.length, edges = [];
        for (var i = 0; i <= n; i++) edges.push(m ? s[Math.min(m - 1, Math.floor(i / n * m))] : 0);
        if (m) { edges[0] = s[0]; edges[n] = s[m - 1]; }
        return {
            edges: edges,
            bin: function (v) { var b = 0; for (var i = 1; i < n; i++) { if (v >= edges[i]) b = i; } return b; }
        };
    }
    // log-spaced binner over [lo, hi] (edges as an array, same shape as above).
    // Used for the count layer: counts are skewed integers (mostly 1, a few high),
    // where quantile bins collapse on ties — log spreads the high tail across colors.
    function _logBinner(lo, hi, n) {
        var a = Math.log(lo), b = Math.log(hi), d = (b - a) || 1, edges = [];
        for (var i = 0; i <= n; i++) edges.push(Math.exp(a + d * i / n));
        return {
            edges: edges,
            bin: function (v) { return v <= 0 ? 0 : Math.max(0, Math.min(n - 1, Math.floor((Math.log(v) - a) / d * n))); }
        };
    }
    function _fmtNum(v) {
        if (!v) return '0';
        var a = Math.abs(v);
        if (a >= 1e6) return (v / 1e6).toFixed(a >= 1e7 ? 0 : 1) + 'M';
        if (a >= 1e3) return (v / 1e3).toFixed(a >= 1e4 ? 0 : 1) + 'k';
        if (a >= 10)  return v.toFixed(0);
        if (a >= 1)   return v.toFixed(1);
        if (a >= 0.01) return v.toFixed(2);
        return v.toExponential(1);
    }
    function _cellRect(l, n) { var x0 = _scVx(l), y0 = _scVy(n + 1); return [x0, y0, (_scVx(l + 1) - x0) + 0.6, (_scVy(n) - y0) + 0.6]; }

    // Terrain-density backdrop (pale, binned) + landslide grid (dark) on top.
    // Landslide layer = raw centroid count, or (proportion mode) the fraction of
    // terrain cells of that (lw,n10) value that contain a centroid (drawn ‰).
    function scatterDrawHeat() {
        if (!scatterHeat || !_scDims || !SUSC_TERRAIN) return;
        var body = scatterHeat.parentNode;
        var W = body.clientWidth, H = body.clientHeight;
        var dpr = window.devicePixelRatio || 1;
        scatterHeat.width = Math.round(W * dpr); scatterHeat.height = Math.round(H * dpr);
        var ctx = scatterHeat.getContext('2d');
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, W, H);

        var B = SUSC_TERRAIN.size, tgrid = SUSC_TERRAIN.grid;
        scatterComputeLsGrid();
        var pe = document.getElementById('scatter-prop');
        var propMode = pe && pe.checked;

        var i, r;
        // terrain layer (pale, quintile bins over the nonzero cells)
        var tVals = [];
        for (i = 0; i < B * B; i++) if (tgrid[i]) tVals.push(tgrid[i]);
        var tBins = _quantileBinner(tVals, TERRAIN_RAMP.length);
        for (i = 0; i < B * B; i++) {
            if (!tgrid[i]) continue;
            r = _cellRect(i % B, (i / B) | 0);
            ctx.fillStyle = TERRAIN_RAMP[tBins.bin(tgrid[i])];
            ctx.fillRect(r[0], r[1], r[2], r[3]);
        }
        // landslide layer (dark). Proportion = quantile (continuous, even bins);
        // count = log (skewed integers — quantile would collapse on ties).
        var lsVals = new Float64Array(B * B), lsNonzero = [], lsMax = 0, v;
        for (i = 0; i < B * B; i++) {
            if (!_lsGrid[i]) continue;
            v = propMode ? (tgrid[i] > 0 ? _lsGrid[i] / tgrid[i] : 0) : _lsGrid[i];
            lsVals[i] = v;
            if (v > 0) { lsNonzero.push(v); if (v > lsMax) lsMax = v; }
        }
        var lBins = propMode ? _quantileBinner(lsNonzero, LS_RAMP.length)
                             : _logBinner(1, lsMax || 1, LS_RAMP.length);
        for (i = 0; i < B * B; i++) {
            if (!lsVals[i]) continue;
            r = _cellRect(i % B, (i / B) | 0);
            ctx.fillStyle = LS_RAMP[lBins.bin(lsVals[i])];
            ctx.fillRect(r[0], r[1], r[2], r[3]);
        }
        scatterRenderLegend(tBins.edges, lBins.edges, propMode);
    }

    function scatterRenderLegend(tEdges, lEdges, propMode) {
        var el = document.getElementById('scatter-legend');
        if (!el) return;
        function bar(ramp, edges, fmt) {
            return '<div class="leg-bar">' + ramp.map(function (c, i) {
                return '<span style="background:' + c + '" title="' + fmt(edges[i]) + ' – ' + fmt(edges[i + 1]) + '"></span>';
            }).join('') + '</div>';
        }
        function allEdges(edges, fmt) {   // every quintile boundary, under the bar
            return '<div class="leg-edges">' + edges.map(function (v) { return '<span>' + fmt(v) + '</span>'; }).join('') + '</div>';
        }
        var tKm2 = tEdges.map(function (v) { return v * CELL_KM2; });
        var tHtml = '<div class="leg-row"><span class="leg-title">Terrain km²/cell</span>' +
            '<div style="flex:1">' + bar(TERRAIN_RAMP, tKm2, _fmtNum) + allEdges(tKm2, _fmtNum) + '</div></div>';
        var lTitle = propMode ? 'Slides/1000 km²' : 'Slides/cell';
        var lEdgesD = propMode ? lEdges.map(function (v) { return v * P_PER_KKM2; }) : lEdges;
        var lHtml = '<div class="leg-row"><span class="leg-title">' + lTitle + '</span>' +
            '<div style="flex:1">' + bar(LS_RAMP, lEdgesD, _fmtNum) + allEdges(lEdgesD, _fmtNum) + '</div></div>';
        el.innerHTML = tHtml + lHtml;
    }

    // Hover readout: exact numbers for the cell under the cursor.
    function scatterCellTip(e, p) {
        if (!_scDims || !SUSC_TERRAIN ||
            p.x < _scDims.x0 || p.y < _scDims.y0 ||
            p.x > _scDims.x0 + _scDims.w || p.y > _scDims.y0 + _scDims.h) { hideChartTip(); return; }
        var B = SUSC_TERRAIN.size, lw = _scPxToLw(p.x), n10 = _scPxToN10(p.y);
        var tc = SUSC_TERRAIN.grid[n10 * B + lw] || 0;
        var lc = (_lsGrid ? _lsGrid[n10 * B + lw] : 0) || 0;
        var dens = tc > 0 ? (lc / tc * P_PER_KKM2) : 0;   // slides per 1000 km²
        showChartTip(e.clientX, e.clientY,
            'lw ' + lw + ', n10 ' + n10 + ' · ' + _fmtNum(tc * CELL_KM2) + ' km² · ' +
            lc + ' slide' + (lc === 1 ? '' : 's') + (lc ? ' (' + _fmtNum(dens) + '/1000 km²)' : ''));
    }

    // Drag-to-select
    (function () {
        if (!scatterSvg) return;
        var dragging = false, sx = 0, sy = 0;
        function localPt(e) {
            var r = scatterSvg.getBoundingClientRect();
            return { x: e.clientX - r.left, y: e.clientY - r.top };
        }
        scatterSvg.addEventListener('pointerdown', function (e) {
            if (!_scDims) return;
            e.preventDefault();   // stop native selection/drag (was causing a start jump)
            var p = localPt(e);
            dragging = true; sx = p.x; sy = p.y;
            scatterSvg.setPointerCapture(e.pointerId);
        });
        scatterSvg.addEventListener('pointermove', function (e) {
            var p = localPt(e);
            if (dragging) {
                var dr = document.getElementById('scatter-drag');
                if (!dr) return;
                dr.setAttribute('x', Math.min(sx, p.x)); dr.setAttribute('y', Math.min(sy, p.y));
                dr.setAttribute('width', Math.abs(p.x - sx)); dr.setAttribute('height', Math.abs(p.y - sy));
                dr.setAttribute('display', 'block');
                return;
            }
            scatterCellTip(e, p);   // hover readout of exact per-cell numbers
        });
        scatterSvg.addEventListener('pointerleave', hideChartTip);
        scatterSvg.addEventListener('pointerup', function (e) {
            if (!dragging) return;
            dragging = false;
            var dr = document.getElementById('scatter-drag');
            if (dr) dr.setAttribute('display', 'none');
            var p = localPt(e);
            if (Math.abs(p.x - sx) < 4 && Math.abs(p.y - sy) < 4) return;  // treat as a click, not a box
            var lwA = _scPxToLw(sx), lwB = _scPxToLw(p.x);
            var nA  = _scPxToN10(sy), nB = _scPxToN10(p.y);
            _scatterSetSliders(Math.min(lwA, lwB), Math.max(lwA, lwB), Math.min(nA, nB), Math.max(nA, nB));
        });
    }());

    scatterFP = makeFloatingPanel(scatterPanel, {
        handle:   document.getElementById('scatter-handle'),
        toggle:   document.getElementById('scatter-toggle'),
        close:    document.getElementById('scatter-close'),
        onResize: scatterDrawAll
    });
    // Scatter-specific controls (not part of the shared floating behavior).
    var scatterClear = document.getElementById('scatter-clear');
    if (scatterClear) {
        scatterClear.addEventListener('click', function (e) {
            e.preventDefault();
            _scatterSetSliders(0, SUSC_VMAX, 0, SUSC_VMAX);
        });
    }
    var scatterProp = document.getElementById('scatter-prop');
    if (scatterProp) scatterProp.addEventListener('change', scatterDrawAll);

    // ===========================================================================
    // Canvas mouseover tooltips
    // ===========================================================================
    function doyApprox(doy) {
        var m = 0;
        for (var i = 11; i >= 0; i--) { if (doy >= MONTH_STRT[i]) { m = i; break; } }
        return MONTHS[m] + '\u202f' + (doy - MONTH_STRT[m] + 1);
    }

    var timingCanvasEl = document.getElementById('timing-canvas');
    if (timingCanvasEl) {
        timingCanvasEl.addEventListener('mousemove', function(e) {
            if (!_tlHits) { hideChartTip(); return; }
            var ox = e.offsetX, hit = null;
            for (var i = 0; i < _tlHits.length; i++) {
                if (ox >= _tlHits[i].x0 && ox < _tlHits[i].x1) { hit = _tlHits[i]; break; }
            }
            if (!hit) { hideChartTip(); return; }
            var k = hit.key, lbl;
            if      (k === 'H')           lbl = 'Holocene';
            else if (k === 'M')           lbl = 'Modern (pre-2000)';
            else if (k.indexOf('-') < 0)  lbl = k;
            else { var p = k.split('-'); lbl = MONTHS[parseInt(p[1])] + ' ' + p[0]; }
            var tip;
            if (cbTimingVol && cbTimingVol.checked) {
                tip = lbl + ': ' + fmtVolBig(hit.val) + ' / month';
                if (hit.maxEv > 0) tip += ' \u00b7 largest ' + fmtVolBig(hit.maxEv);
            } else {
                tip = lbl + ': ' + fmtEvYr(hit.val) + ' ev/month';
            }
            showChartTip(e.clientX, e.clientY, tip);
        });
        timingCanvasEl.addEventListener('mouseleave', hideChartTip);
    }

    var histCanvasEl = document.getElementById('hist-canvas');
    if (histCanvasEl) {
        histCanvasEl.addEventListener('mousemove', function(e) {
            if (!_histHits) { hideChartTip(); return; }
            var ox = e.offsetX, hit = null;
            for (var i = 0; i < _histHits.length; i++) {
                if (ox >= _histHits[i].x0 && ox < _histHits[i].x1) { hit = _histHits[i]; break; }
            }
            if (!hit) { hideChartTip(); return; }
            var b = hit.b, d0 = b * 10 + 1, d1 = b < 36 ? b * 10 + 10 : 365;
            var rangeLbl = doyApprox(d0) + '\u2013' + doyApprox(d1);
            var tip;
            if (cbHistVol && cbHistVol.checked) {
                tip = rangeLbl + ': ' + fmtVolBig(hit.val) + ' / 10d';
                if (hit.maxEv > 0) tip += ' \u00b7 largest ' + fmtVolBig(hit.maxEv);
            } else {
                tip = rangeLbl + ': ' + (hit.val > 0 ? hit.val.toFixed(2) : '0') + ' ev/10d';
            }
            showChartTip(e.clientX, e.clientY, tip);
        });
        histCanvasEl.addEventListener('mouseleave', hideChartTip);
    }

    // Plain left-click on a slug permalink: intercept the navigation and
    // smooth-zoom via hashchange instead. The href is still the canonical
    // shareable slug URL — right-click "Copy link" gives that form, and
    // modifier-clicks (cmd/ctrl/shift/middle button) honor it normally so
    // "open in new tab" still works.
    document.addEventListener('click', function (e) {
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        var a = e.target.closest && e.target.closest('a.landslide-permalink, a.flag-jump');
        if (!a) return;
        e.preventDefault();
        var lat  = a.getAttribute('data-lat');
        var lon  = a.getAttribute('data-lon');
        var id   = a.getAttribute('data-id');
        var view = a.getAttribute('data-view');   // curated default view, when set
        var newHash = view
            ? '#' + view + '&id=' + id
            : (lat && lon)
                ? '#map=13/' + lat + '/' + lon + '&id=' + id
                : '#id=' + id;
        if (location.hash !== newHash) {
            location.hash = newHash;
        } else {
            // Same hash — listener won't fire. Re-trigger smooth-zoom/detail.
            if (view) applyViewString(view);
            else if (lat && lon) map.flyTo({ center: [+lon, +lat], zoom: 13 });
            if (id) showDetail(+id);
        }
    });

    // Hash-only navigation. Clicking the in-page permalink mutates the
    // URL hash without a page reload, so we fly to the new center/zoom
    // and re-open the detail panel. Browser back/forward through map
    // states also benefits as a side effect.
    var _lastHash = location.hash;
    window.addEventListener('hashchange', function () {
        if (location.hash === _lastHash) return;
        _lastHash = location.hash;
        var s = parseHashState();
        if (s.base && s.base !== _currentBasemap && findBasemap(s.base)) setBasemap(s.base);
        // A wiper in the hash is applied; a hash without one leaves the current
        // wiper alone (permalink clicks shouldn't kill an open comparison).
        if (s.swipe && findBasemap(s.swipe)) {
            if (s.sx != null) _swipeSetX(s.sx);
            _swipeEnable(s.swipe);
            _syncSwipeSelect();
        }
        if (s.lat != null && s.lon != null && s.zoom != null) {
            map.flyTo({ center: [s.lon, s.lat], zoom: s.zoom });
        }
        if (s.id != null) showDetail(s.id);
    });

})();
