/* measure.js — MeasureControl: the distance / area measuring tool, a MapLibre control.
 *
 * Moved out of map.js on 2026-11-14 without changing its text. map.js calls
 * window.LSMeasure.init(ctx) once, at the point in its start-up where this code
 * used to sit, and passes in the few things it needs from the map page:
 *   .
 * It returns what map.js still uses: MeasureControl, fmtArea.
 */
(function () {
    'use strict';

    window.LSMeasure = { init: function (ctx) {

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

            // Both measure modes share ONE slot in LSTools ('measure'), because
            // switching line <-> area is not a tool change — it stays the measure
            // tool and keeps its finalized shapes. Only leaving for Draw or InSAR
            // is a release.
            this._btnLine = LSTools.mode({
                id: 'measure-line', order: 10, label: '━',
                title: 'Measure distance (Esc cancels, Esc again exits)',
                onSelect: function () { self._setMode('line'); },
                onRelease: function () { self._setMode('idle'); },
                cancel: function () { self._cancel(); }
            });
            this._btnPoly = LSTools.mode({
                id: 'measure-area', order: 20, label: '▱',
                title: 'Measure area (Esc cancels, Esc again exits)',
                onSelect: function () { self._setMode('polygon'); },
                onRelease: function () { self._setMode('idle'); },
                cancel: function () { self._cancel(); }
            });
            this._btnClear = LSTools.action({
                id: 'measure-clear', order: 90, label: '✕',
                className: 'tool-action',
                title: 'Clear measurements and exit the measure tool',
                onClick: function () { self._clearAll(); }
            });
            var el = document.createElement('div');   // IControl must return a node;
            el.style.display = 'none';                // the buttons live in LSTools

            this._readout = document.getElementById('measure-readout');
            this._tooltip = document.createElement('div');
            this._tooltip.className = 'inv-measure-tooltip';
            this._map.getContainer().appendChild(this._tooltip);

            // Bind handlers once so we can in principle remove them.
            this._onClick   = this._onClick.bind(this);
            this._onMove    = this._onMove.bind(this);
            this._onLeave   = this._onLeave.bind(this);
            // An ACTION entry: the dispatcher runs it only while one of these
            // holders owns the map, and nothing else runs then. Replaces a global
            // map.on('click') that fired on every click and returned when idle.
            LSTools.clicks.register({
                id: 'measure', kind: 'action', holders: ['measure-line', 'measure-area'],
                handler: function (_f, e) { self._onClick(e); }
            });
            this._map.on('mousemove', this._onMove);
            this._map.getContainer().addEventListener('mouseleave', this._onLeave);

            // Layers must be re-created on every style.load (basemap switch wipes them).
            this._ensureLayers = this._ensureLayers.bind(this);
            this._map.on('style.load', this._ensureLayers);
            if (this._map.isStyleLoaded()) this._ensureLayers();

            return el;
        };
        MeasureControl.prototype.onRemove = function () { /* not used */ };

        MeasureControl.prototype._setMode = function (mode) {
            if (mode !== 'idle') {
                window.LSTrack && LSTrack.event('map_tool', { tool: 'measure_' + mode });
                // Exclusion is the coordinator's job now: this releases Draw or
                // InSAR if either holds the map, and only fails if the outgoing
                // tool objected (an open polygon ring).
                if (!LSTools.claim('measure-' + (mode === 'line' ? 'line' : 'area'))) return;
            } else {
                LSTools.release('measure-line');
                LSTools.release('measure-area');
            }
            if (this._mode === mode) return;
            // Switching out of an in-progress shape commits whatever is valid so far.
            if (this._mode !== 'idle') this._finalize();
            this._mode = mode;
            var drawing = mode !== 'idle';
            if (drawing) LSTools.cursor.set('tool', 'crosshair'); else LSTools.cursor.clear('tool');
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

        // Escape, routed here by LSTools (Hig, 2026-09-24: two-step). With a shape
        // in progress, Escape discards it and stays in the tool -- the old meaning.
        // With nothing in progress, Escape exits the tool, which used to require
        // the button and left Escape doing visibly nothing. InSAR already meant
        // "exit"; now the two agree.
        MeasureControl.prototype._cancel = function () {
            if (this._mode === 'idle') return;
            if (this._active.length) {
                this._active = [];
                this._setPreview([]);
                this._tooltip.style.display = 'none';
                this._render();
                return;
            }
            this._setMode('idle');
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
                LSTools.release('measure-line');
                LSTools.release('measure-area');
                LSTools.cursor.clear('tool');
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

        return { MeasureControl: MeasureControl, fmtArea: fmtArea };
    } };
})();
