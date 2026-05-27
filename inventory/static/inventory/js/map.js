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
    function parseHashState() {
        var h = location.hash || '';
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
            } else if (k === 'id') {
                var n = parseInt(v, 10);
                if (n > 0) out.id = n;
            }
        });
        return out;
    }
    var _initialHash = parseHashState();
    var _pendingDetailId = _initialHash.id || null;

    function writeHashState() {
        var c = map.getCenter(), z = map.getZoom();
        var parts = ['map=' + z.toFixed(2) + '/' + c.lat.toFixed(4) + '/' + c.lng.toFixed(4)];
        if (_currentBasemap && _currentBasemap !== DEFAULT_BASEMAP_ID) parts.push('base=' + _currentBasemap);
        var newHash = '#' + parts.join('&');
        if (location.hash !== newHash) history.replaceState(null, '', newHash);
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
    var BASEMAPS = CFG.basemaps || [
        { id: 'streets',   label: 'Streets',
          style: 'https://tiles.openfreemap.org/styles/liberty' },
        { id: 'esri-img',  label: 'ESRI Imagery',
          tiles: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
          labelTiles: 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
          attr: '© Esri, Maxar, Earthstar Geographics' },
        { id: 's2-cloudless', label: 'Sentinel-2 cloudless',
          tiles: 'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg',
          attr: 'Sentinel-2 cloudless 2024 by <a href="https://s2maps.eu/">EOX</a> (Contains modified Copernicus Sentinel data 2024)' },
        { id: 'esri-topo', label: 'ESRI Topo',
          tiles: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
          attr: '© Esri, USGS, NOAA' },
        { id: 'usgs-topo', label: 'USGS Topo',
          tiles: 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}',
          attr: 'USGS National Map' },
        { id: 'usgs-img',  label: 'USGS Imagery',
          tiles: 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}',
          attr: 'USGS National Map' },
        { id: 'usgs-hist', label: 'USGS Hist. Topo',
          tiles: 'https://server.arcgisonline.com/ArcGIS/rest/services/USA_Topo_Maps/MapServer/tile/{z}/{y}/{x}',
          attr: '© Esri, USGS' },
    ];

    function buildRasterStyle(bm) {
        var sources = { basemap: { type: 'raster', tiles: [bm.tiles], tileSize: 256, attribution: bm.attr || '' } };
        var layers  = [{ id: 'basemap', type: 'raster', source: 'basemap' }];
        if (bm.labelTiles) {
            sources.labels = { type: 'raster', tiles: [bm.labelTiles], tileSize: 256 };
            layers.push({ id: 'labels', type: 'raster', source: 'labels' });
        }
        return { version: 8, sources: sources, layers: layers,
                 projection: { type: 'globe' },
                 glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf' };
    }

    function findBasemap(id) {
        for (var i = 0; i < BASEMAPS.length; i++) if (BASEMAPS[i].id === id) return BASEMAPS[i];
        return null;
    }

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
        zoom: (_initialHash.zoom != null) ? _initialHash.zoom : 4
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
    // Load settings + map ready — both must complete before adding layers
    // ---------------------------------------------------------------------------
    var POLYGON_ZOOM       = 7;
    var polygonLoadPending = false;
    var _settings = null, _mapReady = false, _settingsReady = false, _layersInitialized = false;
    var _featuresData = null;      // cached GeoJSON so re-init after basemap switch is instant
    var _surveyCirclesData = null; // cached survey-circles GeoJSON (fetched on first toggle)
    var _currentBasemap = _initialBasemapId;

    // Layer style variables set by initLayers, reused by initDataLayers on basemap switch
    var _cG, _cS, _cO, _cC, _cCPale, _sk, _fOp, _lW, _rSm, _rMd, _rLg;
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
        _cG     = s.color_geomorph || '#d3e9cf';
        _cS     = s.color_subtle   || '#faf075';
        _cO     = s.color_obvious  || '#f69fa1';
        _cC     = s.color_cat      || '#3f67b1';
        _cCPale = '#96b8df';  // de-emphasized catastrophic (Holocene, Modern, Small)
        _sk  = s.stroke_color   || '#ffffff';
        _fOp = parseFloat(s.fill_opacity) || 0.35;
        _lW  = parseFloat(s.line_width)   || 1.5;
        _rSm = parseFloat(s.circle_sm)    || 3;
        _rMd = parseFloat(s.circle_md)    || 5;
        _rLg = parseFloat(s.circle_lg)    || 7;

        _classFill = [
            'match', ['get', 'landslide_class'],
            'Slow Obvious creep', _cO, 'Slow Patchy obvious creep', _cO,
            'Slow Subtle creep', _cS,
            'Slow Geomorph creep', _cG, 'Small slow landslide', _cG,
            'Catastrophic Cryptic', _cC,
            'Catastrophic Obvious creep', _cC, 'Catastrophic Patchy obvious creep', _cC,
            'Catastrophic Subtle creep', _cC, 'Catastrophic Geomorph creep', _cC,
            'Catastrophic Modern', _cCPale, 'Catastrophic Holocene', _cCPale,
            'Small catastrophic landslide', _cCPale,
            _cG
        ];
        _classStroke = [
            'match', ['get', 'landslide_class'],
            'Catastrophic Obvious creep', _cO, 'Catastrophic Patchy obvious creep', _cO,
            'Catastrophic Subtle creep', _cS, 'Catastrophic Geomorph creep', _cG,
            _sk
        ];
        _classStrokeW = [
            'match', ['get', 'landslide_class'],
            'Catastrophic Obvious creep', 3, 'Catastrophic Patchy obvious creep', 3,
            'Catastrophic Subtle creep', 3, 'Catastrophic Geomorph creep', 3,
            1
        ];

        initDataLayers();

        fetch(API_BASE + 'api/features/?v=' + DATA_V)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                _featuresData = data;
                if (map.getSource('landslides')) map.getSource('landslides').setData(data);
            })
            .catch(function (e) { console.error('Feature load failed:', e); });

        map.on('moveend', onMoveEnd);
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

        map.addSource('landslides', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        map.addSource('polygons',   { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });

        map.addLayer({
            id: 'points', type: 'circle', source: 'landslides',
            layout: {
                'circle-sort-key': ['match', ['get', 'landslide_class'],
                    // Top: post-2012 catastrophic (dark blue, no precursor)
                    'Catastrophic Cryptic', 50,
                    // Obviously creeping (red halo or red dot)
                    'Slow Obvious creep', 40, 'Slow Patchy obvious creep', 40,
                    'Catastrophic Obvious creep', 40, 'Catastrophic Patchy obvious creep', 40,
                    // Subtle (yellow)
                    'Slow Subtle creep', 30, 'Catastrophic Subtle creep', 30,
                    // Geomorphic (green)
                    'Slow Geomorph creep', 20, 'Small slow landslide', 20, 'Catastrophic Geomorph creep', 20,
                    // Bottom: de-emphasized pale blue
                    10
                ]
            },
            paint: {
                'circle-color': _classFill,
                'circle-radius': ['interpolate', ['linear'], ['zoom'],
                    4,  ['match', ['get', 'landslide_class'], ['Small slow landslide', 'Small catastrophic landslide'], _rSm * 0.6, _rSm],
                    8,  ['match', ['get', 'landslide_class'], ['Small slow landslide', 'Small catastrophic landslide'], _rMd * 0.6, _rMd],
                    12, ['match', ['get', 'landslide_class'], ['Small slow landslide', 'Small catastrophic landslide'], _rLg * 0.6, _rLg]
                ],
                'circle-stroke-width': _classStrokeW,
                'circle-stroke-color': _classStroke,
                'circle-opacity': 0.9
            }
        }, bId);
        map.addLayer({
            id: 'polygon-fill', type: 'fill', source: 'polygons',
            paint: {
                'fill-color': ['case',
                    ['==', ['get', 'role'], 'deposit'], _cC,
                    ['==', ['get', 'role'], 'source'],  _cC,
                    _classFill],
                'fill-opacity': _fOp
            }
        }, bId);
        map.addLayer({
            id: 'polygon-outline', type: 'line', source: 'polygons',
            paint: {
                'line-color': ['case',
                    ['==', ['get', 'role'], 'deposit'], '#1a3f80',
                    ['==', ['get', 'role'], 'source'],  '#1a3f80',
                    _sk],
                'line-width': _lW
            }
        }, bId);
        map.addLayer({
            id: 'polygon-hover', type: 'line', source: 'polygons',
            filter: ['==', 'landslide_id', -1],
            paint: { 'line-color': '#fff', 'line-width': 2.5, 'line-opacity': 0.8 }
        }, bId);

        // Survey-circles layer — black outline only (no fill); thin for
        // circles with no landslides identified (update_total=0), bold for
        // those with hits, plus a numerical label of update_total on the
        // hit circles. Togglable in the legend; visibility on layer creation
        // reflects the current checkbox state (the JS source of truth) so a
        // basemap switch doesn't clobber the user's choice.
        var circlesVis = (cbSurveyCircles && cbSurveyCircles.checked) ? 'visible' : 'none';
        map.addSource('survey-circles',
            { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        map.addLayer({
            id: 'survey-circles-outline', type: 'line', source: 'survey-circles',
            layout: { 'visibility': circlesVis },
            paint: {
                'line-color': '#000',
                'line-opacity': 0.85,
                'line-width': ['case',
                    ['>', ['coalesce', ['get', 'update_total'], 0], 0], 2.2,
                    0.6
                ]
            }
        }, bId);
        map.addLayer({
            id: 'survey-circles-label', type: 'symbol', source: 'survey-circles',
            filter: ['>', ['coalesce', ['get', 'update_total'], 0], 0],
            layout: {
                'visibility': circlesVis,
                'text-field': ['to-string', ['get', 'update_total']],
                'text-size': 12,
                'text-allow-overlap': true
            },
            paint: {
                'text-color': '#000',
                'text-halo-color': '#fff',
                'text-halo-width': 1.5
            }
        }, bId);
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
        _currentBasemap = id;
        document.querySelectorAll('.bm-btn').forEach(function (b) {
            b.classList.toggle('active', b.dataset.id === id);
        });
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

    // Build basemap picker buttons
    (function () {
        var picker = document.getElementById('basemap-picker');
        if (!picker) return;
        BASEMAPS.forEach(function (bm) {
            var btn = document.createElement('button');
            btn.className = 'bm-btn' + (bm.id === _currentBasemap ? ' active' : '');
            btn.dataset.id = bm.id;
            btn.textContent = bm.label;
            btn.addEventListener('click', function () { setBasemap(bm.id); });
            picker.appendChild(btn);
        });
    }());

    // ---------------------------------------------------------------------------
    // Polygon loading on zoom / pan
    // ---------------------------------------------------------------------------
    function onMoveEnd() {
        if (!map.getSource('polygons')) return;
        if (map.getZoom() < POLYGON_ZOOM) {
            map.getSource('polygons').setData({ type: 'FeatureCollection', features: [] });
            document.getElementById('zoom-hint').style.display = '';
        } else {
            document.getElementById('zoom-hint').style.display = 'none';
            if (!polygonLoadPending) {
                polygonLoadPending = true;
                var b = map.getBounds();
                var bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].join(',');
                fetch(API_BASE + 'api/polygons/?bbox=' + bbox)
                    .then(function (r) { return r.json(); })
                    .then(function (data) { map.getSource('polygons').setData(data); })
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
            if (_urlPanels.openHist && histPanel) {
                histPanel.classList.remove('hidden');
                updateChartsContainer();
            }
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
            if (_urlPanels.openTiming && timingPanel) {
                timingPanel.classList.remove('hidden');
                updateChartsContainer();
            }
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

    var srcAreaSlider = document.getElementById('src-area-slider');
    var srcAreaLabel  = document.getElementById('src-area-label');
    var depAreaSlider = document.getElementById('dep-area-slider');
    var depAreaLabel  = document.getElementById('dep-area-label');
    var volSlider     = document.getElementById('vol-slider');
    var volLabel      = document.getElementById('vol-label');
    var yearSlider    = document.getElementById('year-slider');
    var yearLabel     = document.getElementById('year-label');

    if (srcAreaSlider) srcAreaSlider.addEventListener('input', function () {
        srcAreaLabel.textContent = fmtAreaLabel(parseFloat(this.value));
        buildFilter();
    });
    if (depAreaSlider) depAreaSlider.addEventListener('input', function () {
        depAreaLabel.textContent = fmtAreaLabel(parseFloat(this.value));
        buildFilter();
    });
    if (volSlider) volSlider.addEventListener('input', function () {
        volLabel.textContent = fmtVolLabel(parseFloat(this.value));
        buildFilter();
    });
    if (yearSlider) yearSlider.addEventListener('input', function () {
        yearLabel.textContent = YEAR_LABELS[parseInt(this.value)];
        buildFilter();
    });

    var cbMolards      = document.getElementById('cb-molards');
    var cbStream       = document.getElementById('cb-stream');
    var cbHeadscarp    = document.getElementById('cb-headscarp');
    var cbSiteVolume   = document.getElementById('cb-site-volume');
    var cbSupraglacial = document.getElementById('cb-supraglacial');
    var cbPermafrost   = document.getElementById('cb-permafrost');
    var cbTimed        = document.getElementById('cb-timed');
    var cbSeismic      = document.getElementById('cb-seismic');
    var cbPost2012     = document.getElementById('cb-post2012');
    var cbLimitView    = document.getElementById('cb-limit-view');
    [cbMolards, cbStream, cbHeadscarp, cbSiteVolume, cbSupraglacial, cbPermafrost, cbTimed, cbSeismic, cbPost2012].forEach(function (cb) {
        if (cb) cb.addEventListener('change', buildFilter);
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
                });
            };
            if (cbSurveyCircles.checked && !_surveyCirclesData) {
                fetch(API_BASE + 'api/survey_circles/?v=' + DATA_V)
                    .then(function (r) { return r.json(); })
                    .then(function (fc) {
                        _surveyCirclesData = fc;
                        if (map.getSource('survey-circles')) map.getSource('survey-circles').setData(fc);
                        apply();
                    })
                    .catch(function (e) { console.error('survey_circles fetch failed:', e); });
            } else {
                apply();
            }
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

        // Sliders
        var sa  = srcAreaSlider ? parseFloat(srcAreaSlider.value) : 0;
        var da  = depAreaSlider ? parseFloat(depAreaSlider.value) : 0;
        var vol = volSlider ? parseFloat(volSlider.value) : 0;
        var yr  = yearSlider ? parseInt(yearSlider.value) : 0;
        if (sa  !== 0) params.set('sa',  sa);  else params.delete('sa');
        if (da  !== 0) params.set('da',  da);  else params.delete('da');
        if (vol !== 0) params.set('vol', vol); else params.delete('vol');
        if (yr  !== 0) params.set('yr',  yr);  else params.delete('yr');

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
        if (srcAreaSlider && params.has('sa')) {
            srcAreaSlider.value = params.get('sa');
            if (srcAreaLabel) srcAreaLabel.textContent = fmtAreaLabel(parseFloat(srcAreaSlider.value));
        }
        if (depAreaSlider && params.has('da')) {
            depAreaSlider.value = params.get('da');
            if (depAreaLabel) depAreaLabel.textContent = fmtAreaLabel(parseFloat(depAreaSlider.value));
        }
        if (volSlider && params.has('vol')) {
            volSlider.value = params.get('vol');
            if (volLabel) volLabel.textContent = fmtVolLabel(parseFloat(volSlider.value));
        }
        if (yearSlider && params.has('yr')) {
            yearSlider.value = params.get('yr');
            if (yearLabel) yearLabel.textContent = YEAR_LABELS[parseInt(yearSlider.value)];
        }

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
            map.setFilter('points', hideAll);
            map.setFilter('polygon-fill', hideAll);
            map.setFilter('polygon-outline', hideAll);
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

        if (srcAreaSlider) {
            var la = parseFloat(srcAreaSlider.value);
            if (la > 0) f.push(['>=', ['coalesce', ['get', 'area_src'], 1e15], Math.pow(10, la + 3)]);
        }
        if (depAreaSlider) {
            var ld = parseFloat(depAreaSlider.value);
            if (ld > 0) f.push(['any',
                ['==', ['get', 'landslide_type'], 'slow'],
                ['>=', ['coalesce', ['get', 'area_dep'], 1e15], Math.pow(10, ld + 3)]
            ]);
        }
        if (volSlider) {
            var lv = parseFloat(volSlider.value);
            if (lv > 0) f.push(['>=', ['coalesce', ['get', 'volume_preferred'], 1e15], Math.pow(10, lv + 4)]);
        }
        if (yearSlider) {
            var yp = parseInt(yearSlider.value);
            if (yp > 0) f.push(['>=', ['coalesce', ['get', 'year_num'], 9999], yearPosToMinNum(yp)]);
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

        map.setFilter('points', f);
        map.setFilter('polygon-fill', f);
        map.setFilter('polygon-outline', f);
        updateHistogram();
        updateTimeline();
        scheduleSidebarCountUpdate();
        writeUrlState();
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
        updateSidebarCounts();
        writeUrlState();
    });

    // ---------------------------------------------------------------------------
    // Click / hover interaction
    // ---------------------------------------------------------------------------
    map.on('click', 'points',       function (e) { if (map.__measureActive) return; showDetail(e.features[0].properties.id); });
    map.on('click', 'polygon-fill', function (e) { if (map.__measureActive) return; showDetail(e.features[0].properties.landslide_id); });
    ['points', 'polygon-fill'].forEach(function (layer) {
        map.on('mouseenter', layer, function () { if (!map.__measureActive) map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', layer, function () { if (!map.__measureActive) map.getCanvas().style.cursor = ''; });
    });
    map.on('mousemove',  'polygon-fill', function (e) {
        map.setFilter('polygon-hover', ['==', 'landslide_id', e.features[0].properties.landslide_id]);
    });
    map.on('mouseleave', 'polygon-fill', function () {
        map.setFilter('polygon-hover', ['==', 'landslide_id', -1]);
    });

    // ---------------------------------------------------------------------------
    // Detail panel
    // ---------------------------------------------------------------------------
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

    function renderDetail(d) {
        var html = '';
        var stories = d.planet_stories || [];
        var prominent = stories.length > 0 && planetIsProminent(d);

        var manageLink = window._isInventoryEditor
            ? ' <a class="manage-gear" href="/inventory/manage/' + d.id + '/" target="_blank" ' +
              'rel="noopener" title="Edit this record in Manage">⚙</a>'
            : '';
        if (d.slug) {
            // Live: permalink is the slug deep-link (server redirects to a
            // map+id hash with zoom=13 at the landslide centroid). Snapshot:
            // that slug deep-link doesn't exist in the bundle, so we build
            // the equivalent hash directly — same zoom + format as the live
            // server-side redirect, so the user gets the same focus
            // behavior without leaving the archived view.
            var permalink;
            if (CFG.snapshotMode) {
                if (d.centroid_lat != null && d.centroid_lon != null) {
                    permalink = '#map=13/' + (+d.centroid_lat).toFixed(4)
                              + '/' + (+d.centroid_lon).toFixed(4)
                              + '&id=' + d.id;
                } else {
                    permalink = '#id=' + d.id;
                }
            } else {
                permalink = '/inventory/' + esc(d.slug) + '/';
            }
            html += '<h3><a class="landslide-permalink" href="' + permalink +
                    '" title="Permalink — right-click to copy">' + esc(d.unique_name) + '</a>' +
                    manageLink + '</h3>';
        } else {
            html += '<h3>' + esc(d.unique_name) + manageLink + '</h3>';
        }
        html += '<span class="type-badge ' + d.landslide_type + '">' +
                (d.landslide_type === 'slow' ? 'Slow' : 'Catastrophic') + '</span>';
        if (d.landslide_class) html += ' <span class="class-badge">' + esc(d.landslide_class) + '</span>';

        if (prominent) {
            stories.forEach(function (s) { html += renderPlanetStory(s); });
        }

        var imgLinks = [
            { label:'ESRI Wayback',  icon:'W',      url:normUrl(d.esri_wayback_link),  title:'ESRI Wayback historical imagery' },
            { label:'Google Images', icon:'G',      url:normUrl(d.google_images_link), title:'Google Images search' },
            { label:'Sentinel-2',    icon:'S2',     url:normUrl(d.sentinel2_link),     title:'Copernicus Sentinel-2' },
            { label:'Sentinel-1',    icon:'S1',     url:normUrl(d.sentinel1_link),     title:'Copernicus Sentinel-1 SAR' },
            { label:'OPERA Asc',     icon:'↑', url:normUrl(d.opera_asc_link),     title:'OPERA InSAR displacement — ascending' },
            { label:'OPERA Desc',    icon:'↓', url:normUrl(d.opera_desc_link),    title:'OPERA InSAR displacement — descending' }
        ].filter(function (l) { return l.url; });

        if (imgLinks.length) {
            html += '<div class="imagery-links"><div class="detail-section-title" style="margin-bottom:5px;">Imagery</div>';
            imgLinks.forEach(function (l) {
                html += '<a class="imagery-btn" href="' + esc(l.url) + '" target="_blank" rel="noopener" title="' + esc(l.title) + '">' +
                        '<span class="imagery-icon">' + l.icon + '</span>' + esc(l.label) + '</a>';
            });
            html += '</div>';
        }

        if (d.description) html += '<p class="detail-desc">' + esc(d.description) + '</p>';

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
                    '<p class="detail-desc" style="margin:0">' + esc(d.notes) + '</p></div>';
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
    }

    function esc(s) {
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    document.getElementById('close-panel').addEventListener('click', function () {
        document.getElementById('detail-panel').classList.add('hidden');
    });

    // ===========================================================================
    // Panel management (both panels live inside a shared flex container)
    // ===========================================================================
    var histPanel      = document.getElementById('hist-panel');
    var timingPanel    = document.getElementById('timing-panel');
    var chartsContainer = document.getElementById('charts-container');

    var mapLegend     = document.getElementById('map-legend');
    var basemapPicker = document.getElementById('basemap-picker');

    function updateChartsContainer() {
        if (!chartsContainer) return;
        var histOpen   = histPanel   && !histPanel.classList.contains('hidden');
        var timingOpen = timingPanel && !timingPanel.classList.contains('hidden');
        var chartsOpen = histOpen || timingOpen;
        if (chartsOpen) chartsContainer.classList.remove('hidden');
        else            chartsContainer.classList.add('hidden');
        // Lift legend and basemap picker above chart panel so toggles stay clickable
        var liftPx = chartsOpen ? 208 : 32;
        if (mapLegend)     mapLegend.style.bottom     = liftPx + 'px';
        if (basemapPicker) basemapPicker.style.bottom = liftPx + 'px';
        // Re-render open panels after flex layout reflows (fixes canvas width when partner panel closes)
        setTimeout(function() { updateHistogram(); updateTimeline(); }, 50);
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
        return {
            types:    types,
            classes:  classes,
            minSrcArea: srcAreaSlider && parseFloat(srcAreaSlider.value) > 0 ? Math.pow(10, parseFloat(srcAreaSlider.value) + 3) : null,
            minDepArea: depAreaSlider && parseFloat(depAreaSlider.value) > 0 ? Math.pow(10, parseFloat(depAreaSlider.value) + 3) : null,
            minVol:     volSlider     && parseFloat(volSlider.value)     > 0 ? Math.pow(10, parseFloat(volSlider.value)     + 4) : null,
            minYear:  yearSlider && parseInt(yearSlider.value) > 0  ? yearPosToMinNum(parseInt(yearSlider.value)) : null,
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
            if (ev.lat < s || ev.lat > n || ev.lon < w || ev.lon > e) return;
            if (fs.types.indexOf(ev.ls_type) < 0) return;
            if (fs.classes.length && fs.classes.indexOf(ev.cls || '__unclassified__') < 0) return;
            if (fs.minVol     !== null && ev.vol      !== null && ev.vol      < fs.minVol)     return;
            if (fs.minSrcArea !== null && ev.area_src !== null && ev.area_src < fs.minSrcArea) return;
            if (fs.minDepArea !== null && ev.ls_type === 'catastrophic' && ev.area_dep !== null && ev.area_dep < fs.minDepArea) return;
            if (fs.minYear !== null) {
                var yn = ev.year_num !== null ? ev.year_num : 9999;
                if (yn < fs.minYear) return;
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
            ctx.fillText(result ? 'No timed events in current view' : 'Loading…', W/2, H/2);
            if (subtitle) subtitle.textContent = '0 events with precise timing in view';
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

        var subtitleText = result.count + ' event' + (result.count === 1 ? '' : 's') +
            ' in view \u2264\u202f' + maxDays + '\u202fd uncertainty';
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

    // Histogram toggle
    var histToggle = document.getElementById('hist-toggle');
    var histClose  = document.getElementById('hist-close');

    if (histToggle && histPanel) {
        histToggle.addEventListener('click', function (e) {
            e.preventDefault();
            histPanel.classList.toggle('hidden');
            updateChartsContainer();
            if (!histPanel.classList.contains('hidden')) {
                setTimeout(updateHistogram, 50);
            }
            writeUrlState();
        });
    }
    if (histClose) {
        histClose.addEventListener('click', function () {
            histPanel.classList.add('hidden');
            updateChartsContainer();
            writeUrlState();
        });
    }

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
            if (ev.lat < s || ev.lat > n || ev.lon < w || ev.lon > e) return;
            if (fs.types.indexOf(ev.ls_type) < 0) return;
            if (fs.classes.length && fs.classes.indexOf(ev.cls || '__unclassified__') < 0) return;
            if (fs.minVol     !== null && ev.vol      !== null && ev.vol      < fs.minVol)     return;
            if (fs.minSrcArea !== null && ev.area_src !== null && ev.area_src < fs.minSrcArea) return;
            if (fs.minDepArea !== null && ev.ls_type === 'catastrophic' && ev.area_dep !== null && ev.area_dep < fs.minDepArea) return;
            if (fs.minYear !== null) {
                var yn = ev.year_num !== null ? ev.year_num : 9999;
                if (yn < fs.minYear) return;
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
            ctx.fillText(result ? 'No events with temporal data in current view' : 'Loading\u2026', W/2, H/2);
            if (subtitle) subtitle.textContent = '0 events with temporal data in view';
            return;
        }
        var useVolTl = cbTimingVol && cbTimingVol.checked;
        var subtitleTl = result.total + ' event' + (result.total === 1 ? '' : 's') + ' with temporal data in view';
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
        ctx.fillText(useVolTl ? 'volume (m³) / yr' : 'events / yr', 0, 0);
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

    // Timing panel toggle
    var timingToggle = document.getElementById('timing-toggle');
    var timingClose  = document.getElementById('timing-close');

    if (timingToggle && timingPanel) {
        timingToggle.addEventListener('click', function (e) {
            e.preventDefault();
            timingPanel.classList.toggle('hidden');
            updateChartsContainer();
            if (!timingPanel.classList.contains('hidden')) {
                setTimeout(updateTimeline, 50);
            }
            writeUrlState();
        });
    }
    if (timingClose) {
        timingClose.addEventListener('click', function () {
            timingPanel.classList.add('hidden');
            updateChartsContainer();
            writeUrlState();
        });
    }

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
                tip = lbl + ': ' + fmtVolBig(hit.val) + ' / yr';
                if (hit.maxEv > 0) tip += ' \u00b7 largest ' + fmtVolBig(hit.maxEv);
            } else {
                tip = lbl + ': ' + fmtEvYr(hit.val) + ' ev/yr';
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

    // Hash-only navigation. The live `/inventory/<slug>/` route reloads the
    // page (server 302 to a map+id hash), so the initial-hash parser at
    // boot handles it. Inside a snapshot — where no slug route exists —
    // the in-bundle permalink writes the hash directly without reloading,
    // so we need to fly to the new map state and re-open the detail panel.
    var _lastHash = location.hash;
    window.addEventListener('hashchange', function () {
        if (location.hash === _lastHash) return;
        _lastHash = location.hash;
        var s = parseHashState();
        if (s.lat != null && s.lon != null && s.zoom != null) {
            map.flyTo({ center: [s.lon, s.lat], zoom: s.zoom });
        }
        if (s.id != null) showDetail(s.id);
    });

})();
