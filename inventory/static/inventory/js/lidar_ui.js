/* lidar_ui.js — Lidar surveys and the raster section of the sidebar: catalogue, layers, li= state, footprints, zoom-gated lists for both panes.
 *
 * Moved out of map.js on 2026-11-14 without changing its text. map.js calls
 * window.LSLidarUI.init(ctx) once, at the point in its start-up where this code
 * used to sit, and passes in the few things it needs from the map page:
 *   SUSC_LAYERS, _pendingLi, writeHashState, map, _isMapReady, _swipe, _wiperPanelRefresh, RASTER_UI_ZOOM, _traceRows, _buildTraceUI.
 * Optional: floorLayer(map) -> the layer id rasters are inserted beneath (another host page; see glaciers.js).
 * It returns what map.js still uses: _lidarCat, _lidarActive, _lidarActiveR, LIDAR_PRESETS, _lidarFetch, _bboxInView, _lidarReplayLayers, _liSpec, _liApplyHashSpec, _footprintsSet, _buildRasterUI, _rasterPanelRefresh, _buildRasterUIRight.
 *
 * _pendingLi is the li= spec from the page's first URL hash, passed once by value;
 * this file owns it from then on. _lidarActive / _lidarActiveR are objects
 * shared with map.js (never reassigned). The uploads part of the raster
 * section is trace_rasters.js, reached through _traceRows and _buildTraceUI.
 */
(function () {
    'use strict';

    window.LSLidarUI = { init: function (ctx) {
        var SUSC_LAYERS = ctx.SUSC_LAYERS;
        var _pendingLi = ctx._pendingLi;
        var writeHashState = ctx.writeHashState;
        var map = ctx.map;
        var _isMapReady = ctx._isMapReady;
        var _swipe = ctx._swipe;
        var _wiperPanelRefresh = ctx._wiperPanelRefresh;
        var RASTER_UI_ZOOM = ctx.RASTER_UI_ZOOM;
        var _traceRows = ctx._traceRows;
        var _buildTraceUI = ctx._buildTraceUI;

        // ---------------------------------------------------------------------------
        // Georeferenced raster overlays — self-hosted lidar DEMs + editor uploads.
        //
        // Both are "a raster pinned to the ground that you trace landslides on", so
        // they share one panel section. They differ in two ways that the UI has to
        // respect: lidar is public and uploads are editor-only, and lidar arrives as
        // one PMTiles archive per survey shaded client-side (see dem_shade.js) while
        // uploads are pre-baked PNG pyramids.
        //
        // Zoom gating: below RASTER_UI_ZOOM a DEM overlay is useless — a 0.5 m survey
        // at z8 is a smudge, and listing every survey in Alaska is noise. So far out,
        // the panel offers only footprints (where does coverage exist?); zoomed in, it
        // lists the rasters that actually cover the view.
        // ---------------------------------------------------------------------------
        var _lidarCatalog = null;      // FeatureCollection from /lidar/catalog.geojson
        function _lidarCat() { return _lidarCatalog; }   // for the rest of the page: it arrives after start-up
        var _lidarActive = {};         // id -> {preset, opacity}   (main map)
        var _lidarActiveR = {};        // id -> {preset, opacity}   (wiper right pane)
        var _lidarBbox = {};           // id -> [w, s, e, n]
        var _rasterUiKey = '';         // last-rendered signature, to avoid churn

        // No sliders here by design — tracing wants a stable, repeatable render, not
        // a lighting studio. Values mirror the /lidar/ preview's presets.
        var LIDAR_PRESETS = {
            hillshade: { label: 'Hillshade',
                         opts: { hs: 1, sl: 0, md: 0, as: 0, az: 315, alt: 45, ve: 1, bl: 0 } },
            // The same view /lidar/ calls "Preset", to the parameter: mod-5 base at
            // full strength, slope at 0.6, hillshade over the top in HARD LIGHT.
            // It was 'KBSP (mod5+slope)' here with bl:0 (Overlay), which lets the
            // ramp decide and washes the shading out wherever the ramp is pale —
            // which is most of a mod-5 base. Converged 2026-09-20 so the two
            // viewers mean the same thing by the same word.
            preset:    { label: 'Preset',
                         opts: { hs: 1, sl: 0.6, md: 1, as: 0, az: 315, alt: 45, ve: 1, bl: 2 } },
            // Not a shading at all: the orthomosaic from the same flight, drawn
            // as a plain raster. It lives in this table and in the same dropdown
            // because that is what it is to the person using it -- another way to
            // look at one survey -- and keeping it here buys one layer per survey,
            // one state field and one hash code instead of a parallel set of each.
            // Offered only where the catalogue advertises ortho_url, which today
            // means the SfM flights; a lidar DTM has no imagery to show.
            ortho:     { label: 'Ortho (imagery)', ortho: true }
        };

        function _lidarFetch() {
            if (_lidarCatalog || !window.DemShade) return;
            // Restricted surveys live in a second, auth-only catalogue. Fetch both
            // and merge: the public one must keep working if the gated one 403s or
            // does not exist, which is the normal case for almost every visitor.
            var pub = fetch('/lidar/catalog.geojson')
                .then(function (r) { return r.ok ? r.json() : null; });
            var gated = window._canViewRestricted
                ? fetch('/lidar/catalog-gated.geojson')
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .catch(function () { return null; })
                : Promise.resolve(null);
            Promise.all([pub, gated])
                .then(function (both) {
                    var fc = both[0];
                    if (!fc) return;
                    if (both[1] && both[1].features && both[1].features.length) {
                        fc = { type: 'FeatureCollection',
                               features: fc.features.concat(both[1].features) };
                    }
                    _lidarCatalog = fc;
                    fc.features.forEach(function (f) {
                        var p = f.properties, b = _geomBounds(f.geometry);
                        _lidarBbox[p.id] = b;
                        // Tile-Worker URLs (edge-cached per tile) when the catalog
                        // carries them, else the archive itself; slope pyramid if built.
                        DemShade.addDataset(p.id, p.pmtiles_url,
                            DemShade.catalogOpts ? DemShade.catalogOpts(p)
                                : (p.slope_url ? { slope: p.slope_url, slopeStep: p.slope_step } : undefined));
                    });
                    // Lidar carried in the URL / saved view (li=): the ids are
                    // resolvable only now. Unknown ids (a gated or retired survey)
                    // are simply skipped.
                    if (_pendingLi) { var pl = _pendingLi; _pendingLi = null; _liApplyHashSpec(pl); }
                    if (typeof LSProfile !== 'undefined') LSProfile.onCatalog();   // a profile from the URL waits for this
                    _rasterUiKey = '';
                    _rasterPanelRefresh();
                })
                .catch(function () { /* lidar simply absent — panel still works */ });
        }

        function _geomBounds(geom) {
            var w = 180, s = 90, e = -180, n = -90;
            var polys = geom.type === 'Polygon' ? [geom.coordinates] : geom.coordinates;
            polys.forEach(function (poly) {
                poly.forEach(function (ring) {
                    ring.forEach(function (pt) {
                        if (pt[0] < w) w = pt[0];
                        if (pt[0] > e) e = pt[0];
                        if (pt[1] < s) s = pt[1];
                        if (pt[1] > n) n = pt[1];
                    });
                });
            });
            return [w, s, e, n];
        }

        function _bboxInView(b) {
            if (!b) return false;
            var m = map.getBounds();
            return !(b[2] < m.getWest() || b[0] > m.getEast() ||
                     b[3] < m.getSouth() || b[1] > m.getNorth());
        }

        // Where raster overlays sit in the stack: below every data layer, same
        // insertion chain the trace overlays use, so a traced polygon always draws
        // on top of the image it came from.
        function _rasterBeforeId(m) {
            // A host other than the inventory map (/glaciers/) names its own
            // lowest data layer.
            if (ctx.floorLayer) return ctx.floorLayer(m);
            var beforeId;
            ['pending-poly-fill']
                .concat(SUSC_LAYERS.map(function (s) { return 'susc-' + s.key + '-layer'; }))
                .concat(['faults-line', 'points'])
                .some(function (cand) {
                    if (m.getLayer(cand)) { beforeId = cand; return true; }
                    return false;
                });
            return beforeId;
        }

        function _lidarLayerId(id) { return 'lidar-' + id; }

        // Target map is a parameter so the wiper's right pane gets the same
        // overlays: comparing two surveys of the same slope across the divider is
        // the whole reason to have several DEMs of one place.
        function _lidarAddLayer(id, m, state) {
            m = m || map; state = state || _lidarActive;
            if (!window.DemShade || !_lidarCatalog || !m) return;
            var f = _lidarCatalog.features.filter(function (x) {
                return x.properties.id === id;
            })[0];
            if (!f) return;
            var p = f.properties;
            var st = state[id] || (state[id] = { preset: 'hillshade', opacity: 1 });
            // A shared link or a stored default view can name a rendering this
            // survey cannot do. Fall back rather than adding a source whose every
            // tile 404s, which looks like a broken map rather than a missing product.
            if (st.preset === 'ortho' && !p.ortho_url) st.preset = 'hillshade';
            var srcId = 'lidar-src-' + id, lyrId = _lidarLayerId(id);
            // The ortho is a photograph, not a surface, so it is a plain pmtiles
            // raster with no shading pipeline. Same layer id and same slot in the
            // stack as the shaded DEM: one overlay per survey that can draw
            // different things, not a second overlay competing for position.
            var url = st.preset === 'ortho'
                ? 'pmtiles://' + new URL(p.ortho_url, location.href).href + '/{z}/{x}/{y}'
                : DemShade.url(id, LIDAR_PRESETS[st.preset].opts);
            if (!m.getSource(srcId)) {
                m.addSource(srcId, {
                    type: 'raster', tiles: [url], tileSize: 256,
                    minzoom: p.min_zoom, maxzoom: p.max_zoom,
                    bounds: _lidarBbox[id], attribution: p.title
                });
            } else {
                m.getSource(srcId).setTiles([url]);
            }
            if (!m.getLayer(lyrId)) {
                m.addLayer({
                    id: lyrId, type: 'raster', source: srcId,
                    paint: {
                        'raster-opacity': st.opacity,
                        // Zero both, or switching between two surveys cross-fades
                        // through the basemap. Same fix as the /lidar/ preview.
                        'raster-opacity-transition': { duration: 0, delay: 0 },
                        'raster-fade-duration': 0
                    }
                }, _rasterBeforeId(m));
            } else {
                m.setPaintProperty(lyrId, 'raster-opacity', st.opacity);
            }
        }

        function _lidarRemoveLayer(id, m) {
            m = m || map;
            if (!m) return;
            if (m.getLayer(_lidarLayerId(id))) m.removeLayer(_lidarLayerId(id));
            if (m.getSource('lidar-src-' + id)) m.removeSource('lidar-src-' + id);
        }

        function _lidarReplayLayers() {
            Object.keys(_lidarActive).forEach(function (id) { _lidarAddLayer(id, map, _lidarActive); });
            if (_swipe.map && _swipe.map.__lsStyleReady) {
                Object.keys(_lidarActiveR).forEach(function (id) {
                    _lidarAddLayer(id, _swipe.map, _lidarActiveR);
                });
            }
        }

        // Encode the visible lidar surveys for the URL hash / a stored view:
        // `<id>.<preset>l<pct>r<pct>` per survey (see the hash grammar comment at
        // the top). One preset per survey: the main pane's if it is on there,
        // else the wiper pane's.
        function _liSpec() {
            var ids = {};
            Object.keys(_lidarActive).forEach(function (id) { ids[id] = 1; });
            Object.keys(_lidarActiveR).forEach(function (id) { ids[id] = 1; });
            var out = {};
            Object.keys(ids).sort().forEach(function (id) {
                var L = _lidarActive[id], R = _lidarActiveR[id];
                out[id] = { preset: (L || R).preset, presetR: R ? R.preset : null,
                            left: !!L, right: !!R,
                            opLeft: L ? L.opacity : null, opRight: R ? R.opacity : null };
            });
            return out;
        }
        // Overwrite lidar visibility from a parsed `li` spec: listed surveys go on
        // with the given preset/opacity per pane, unlisted ones go off. Layers are
        // (re)built only when the maps can take them; otherwise the state waits for
        // initLayers / _swipeAddData to replay it.
        function _liApplyHashSpec(spec) {
            var known = Object.keys(_lidarBbox);
            if (!known.length) { _pendingLi = spec; return; }   // catalog not in yet
            var ids = {};
            known.forEach(function (id) { ids[id] = 1; });
            Object.keys(_lidarActive).concat(Object.keys(_lidarActiveR)).forEach(function (id) { ids[id] = 1; });
            Object.keys(ids).forEach(function (id) {
                var e = spec[id];
                if (e && e.left) {
                    _lidarActive[id] = { preset: e.preset || 'hillshade',
                                         opacity: e.opLeft != null ? e.opLeft : 1 };
                } else if (_lidarActive[id]) {
                    if (_isMapReady()) _lidarRemoveLayer(id, map);
                    delete _lidarActive[id];
                }
                if (e && e.right) {
                    _lidarActiveR[id] = { preset: e.presetR || e.preset || 'hillshade',
                                          opacity: e.opRight != null ? e.opRight : 1 };
                } else if (_lidarActiveR[id]) {
                    if (_swipe.map && _swipe.map.__lsStyleReady) _lidarRemoveLayer(id, _swipe.map);
                    delete _lidarActiveR[id];
                }
            });
            if (_isMapReady()) _lidarReplayLayers();
            _rasterUiKey = '';
            _rasterPanelRefresh();
        }

        // ---- footprints (the zoomed-out view of "where is there data?") ----------
        function _footprintFC(kind) {
            if (kind === 'lidar') {
                return _lidarCatalog || { type: 'FeatureCollection', features: [] };
            }
            return {
                type: 'FeatureCollection',
                features: _traceRows().filter(function (r) {
                    return r.bounds_w != null;     // bounds are known from upload, before the bake
                }).map(function (r) {
                    return {
                        type: 'Feature',
                        properties: { id: r.id, title: r.title, status: r.status },
                        geometry: { type: 'Polygon', coordinates: [[
                            [r.bounds_w, r.bounds_s], [r.bounds_e, r.bounds_s],
                            [r.bounds_e, r.bounds_n], [r.bounds_w, r.bounds_n],
                            [r.bounds_w, r.bounds_s]]] }
                    };
                })
            };
        }

        var FOOTPRINT_STYLE = {
            lidar: { color: '#0a7', id: 'fp-lidar' },
            trace: { color: '#b5651d', id: 'fp-trace' }
        };

        // Footprints stay on at every zoom once enabled. Zoomed out they answer
        // "where is there data?"; zoomed in they answer the different but equally
        // useful "am I about to trace off the edge of the survey?" — so rather than
        // dropping them, the fill fades away and the outline thins to a quiet edge
        // line that never competes with the imagery underneath it.
        function _footprintsSet(kind, on, m) {
            m = m || map;
            if (!m) return;
            var st = FOOTPRINT_STYLE[kind], srcId = st.id + '-src';
            if (!on) {
                ['-line', '-fill', '-line-baking', '-fill-baking'].forEach(function (sfx) {
                    if (m.getLayer(st.id + sfx)) m.removeLayer(st.id + sfx);
                });
                if (m.getSource(srcId)) m.removeSource(srcId);
                return;
            }
            var fc = _footprintFC(kind);
            if (m.getSource(srcId)) { m.getSource(srcId).setData(fc); return; }
            m.addSource(srcId, { type: 'geojson', data: fc });
            // A still-baking upload gets its own two layers (blue, tight dash, a
            // fill that stays visible zoomed in): the "your image is coming" cue.
            // Separate layers because zoom interpolation must be the outermost
            // expression and line-dasharray cannot be data-driven at all — a
            // 'case' around either is rejected and the whole add fails silently.
            var notBaking = ['!=', ['get', 'status'], 'processing'];
            var baking = ['==', ['get', 'status'], 'processing'];
            m.addLayer({ id: st.id + '-fill-baking', type: 'fill', source: srcId, filter: baking,
                         paint: { 'fill-color': '#1a73e8', 'fill-opacity': 0.15 } },
                       _rasterBeforeId(m));
            m.addLayer({ id: st.id + '-line-baking', type: 'line', source: srcId, filter: baking,
                         paint: { 'line-color': '#1a73e8', 'line-width': 2, 'line-dasharray': [1, 1] } },
                       _rasterBeforeId(m));
            var color = ['match', ['get', 'status'], 'error', '#c00', st.color];
            m.addLayer({ id: st.id + '-fill', type: 'fill', source: srcId, filter: notBaking,
                         paint: {
                             'fill-color': color,
                             'fill-opacity': ['interpolate', ['linear'], ['zoom'],
                                              6, 0.12, 10, 0.05, 12, 0]
                         } },
                       _rasterBeforeId(m));
            m.addLayer({ id: st.id + '-line', type: 'line', source: srcId, filter: notBaking,
                         paint: {
                             'line-color': color,
                             'line-dasharray': [2, 1],
                             'line-width': ['interpolate', ['linear'], ['zoom'],
                                            6, 1.5, 12, 1, 16, 0.8],
                             'line-opacity': ['interpolate', ['linear'], ['zoom'],
                                              6, 1, 12, 0.7, 16, 0.45]
                         } },
                       _rasterBeforeId(m));
        }

        // The merged section. Rebuilt in place (stable id) whenever the zoom band or
        // the set of in-view rasters changes, so it always describes what is actually
        // reachable from here rather than a static catalogue.
        function _buildRasterUI() {
            var wrap = document.createElement('div');
            wrap.id = 'raster-overlays-ui';
            wrap.style.cssText = 'margin-top:12px;';
            _fillRasterUI(wrap);
            return wrap;
        }

        function _rasterUiSignature() {
            var z = map.getZoom();
            if (z < RASTER_UI_ZOOM) return 'far';
            var ids = [];
            Object.keys(_lidarBbox).forEach(function (id) {
                if (_bboxInView(_lidarBbox[id])) ids.push('L' + id);
            });
            if (window._isInventoryEditor) {
                _traceRows().forEach(function (r) {
                    if (r.bounds_w != null && _bboxInView(
                            [r.bounds_w, r.bounds_s, r.bounds_e, r.bounds_n]))
                        ids.push('T' + r.id + r.status.charAt(0));
                });
            }
            return 'near:' + ids.sort().join(',');
        }

        function _rasterPanelRefresh() {
            var wrap = document.getElementById('raster-overlays-ui');
            if (!wrap) return;
            var key = _rasterUiSignature();
            if (key === _rasterUiKey) return;      // nothing changed — don't churn the DOM
            _rasterUiKey = key;
            wrap.innerHTML = '';
            _fillRasterUI(wrap);
            // The right pane lists the same in-view surveys, so it has to follow.
            if (_swipe.on) _wiperPanelRefresh();
        }

        function _fillRasterUI(wrap) {
            var hdr = document.createElement('div');
            hdr.className = 'refmaps-category';
            hdr.textContent = 'Elevation & imagery overlays';
            wrap.appendChild(hdr);

            var far = map.getZoom() < RASTER_UI_ZOOM;
            var hint = document.createElement('div');
            hint.style.cssText = 'font-size:11px;color:#777;margin-bottom:6px;line-height:1.35;';

            // Footprint toggles live at EVERY zoom — zoomed in they mark the survey
            // edge, which matters while tracing near it.
            wrap.appendChild(_fpCheckbox('lidar', 'Lidar footprints'));
            if (window._isInventoryEditor) {
                wrap.appendChild(_fpCheckbox('trace', 'Uploaded GeoTIFF footprints'));
            }

            if (far) {
                // Too far out for the rasters themselves to mean anything — but
                // uploading one is not a display act, so the form stays available;
                // only the overlay list is withheld until the view is close enough.
                //
                // EXCEPT anything already switched ON. Its layer keeps drawing at
                // every zoom, so withholding the only control that turns it off
                // strands the user with a survey they cannot reach — which is
                // exactly what happens when you turn one on and then zoom out to
                // see it in context. Show those rows and nothing else.
                var onNow = Object.keys(_lidarActive);
                hint.textContent = onNow.length
                    ? 'Zoom to z' + RASTER_UI_ZOOM + ' to add more. Showing what is on:'
                    : 'Zoom to z' + RASTER_UI_ZOOM + ' to load lidar or uploaded imagery.';
                wrap.appendChild(hint);
                onNow.forEach(function (id) {
                    var f = _lidarCatalog && _lidarCatalog.features.filter(function (x) {
                        return x.properties.id === id;
                    })[0];
                    if (f) wrap.appendChild(_lidarRow(f.properties, map, _lidarActive));
                });
                if (window._isInventoryEditor) wrap.appendChild(_buildTraceUI({ far: true }));
                return;
            }

            var lidarIn = Object.keys(_lidarBbox).filter(function (id) {
                return _bboxInView(_lidarBbox[id]);
            });
            if (!_lidarCatalog) {
                hint.textContent = 'Loading lidar catalog…';
            } else if (!lidarIn.length) {
                hint.textContent = 'No lidar coverage in this view.';
            } else {
                hint.textContent = 'Lidar DEMs covering this view. Overlays draw beneath all landslide data.';
            }
            wrap.appendChild(hint);

            lidarIn.forEach(function (id) {
                var f = _lidarCatalog.features.filter(function (x) {
                    return x.properties.id === id;
                })[0];
                if (f) wrap.appendChild(_lidarRow(f.properties, map, _lidarActive));
            });

            if (window._isInventoryEditor) wrap.appendChild(_buildTraceUI());
        }

        // Same section for the wiper's right pane. Lidar only: the upload overlays
        // remain main-pane-only, which is a deliberate existing choice (see the
        // trace-raster header comment) rather than an oversight here.
        function _buildRasterUIRight() {
            var wrap = document.createElement('div');
            wrap.style.cssText = 'margin-top:12px;';
            var cmap = _swipe.map;
            if (!cmap) return wrap;

            var hdr = document.createElement('div');
            hdr.className = 'refmaps-category';
            hdr.textContent = 'Elevation overlays';
            wrap.appendChild(hdr);

            wrap.appendChild(_fpCheckbox('lidar', 'Lidar footprints', cmap));

            var hint = document.createElement('div');
            hint.style.cssText = 'font-size:11px;color:#777;margin-bottom:6px;line-height:1.35;';
            if (map.getZoom() < RASTER_UI_ZOOM) {
                // Same rule as the main pane: whatever is on stays reachable.
                var onR = Object.keys(_lidarActiveR);
                hint.textContent = onR.length
                    ? 'Zoom to z' + RASTER_UI_ZOOM + ' to add more. Showing what is on:'
                    : 'Zoom to z' + RASTER_UI_ZOOM + ' to load lidar here.';
                wrap.appendChild(hint);
                onR.forEach(function (id) {
                    var f = _lidarCatalog && _lidarCatalog.features.filter(function (x) {
                        return x.properties.id === id;
                    })[0];
                    if (f) wrap.appendChild(_lidarRow(f.properties, cmap, _lidarActiveR));
                });
                return wrap;
            }
            var ids = Object.keys(_lidarBbox).filter(function (id) {
                return _bboxInView(_lidarBbox[id]);
            });
            hint.textContent = ids.length
                ? 'Put a different survey on this side to compare across the divider.'
                : 'No lidar coverage in this view.';
            wrap.appendChild(hint);
            ids.forEach(function (id) {
                var f = _lidarCatalog && _lidarCatalog.features.filter(function (x) {
                    return x.properties.id === id;
                })[0];
                if (f) wrap.appendChild(_lidarRow(f.properties, cmap, _lidarActiveR));
            });
            return wrap;
        }

        function _fpCheckbox(kind, label, m) {
            m = m || map;
            var lab = document.createElement('label');
            lab.style.cssText = 'display:block;font-size:12px;margin:3px 0;cursor:pointer;';
            var cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.id = 'fp-cb-' + kind + (m === map ? '' : '-r');
            cb.checked = !!m.getSource(FOOTPRINT_STYLE[kind].id + '-src');
            cb.addEventListener('change', function () { _footprintsSet(kind, cb.checked, m); });
            lab.appendChild(cb);
            lab.appendChild(document.createTextNode(' ' + label));
            return lab;
        }

        function _lidarRow(p, m, state) {
            m = m || map; state = state || _lidarActive;
            var st = state[p.id];
            var row = document.createElement('div');
            row.style.cssText = 'border-top:1px solid #e6e6e6;padding:5px 0;';

            var lab = document.createElement('label');
            lab.style.cssText = 'display:block;font-size:12px;cursor:pointer;';
            var cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = !!st;
            lab.appendChild(cb);
            lab.appendChild(document.createTextNode(' ' + p.title));
            row.appendChild(lab);

            var meta = document.createElement('div');
            meta.style.cssText = 'font-size:11px;color:#777;margin:1px 0 3px 18px;';
            meta.textContent = (p.year || 'year n/a') + ' · ' + p.native_res_m + ' m · ' +
                               p.coverage_km2 + ' km²';
            row.appendChild(meta);

            var ctl = document.createElement('div');
            ctl.style.cssText = 'margin-left:18px;display:' + (st ? 'block' : 'none') + ';';

            var sel = document.createElement('select');
            sel.style.cssText = 'font-size:11px;width:100%;margin-bottom:3px;';
            Object.keys(LIDAR_PRESETS).forEach(function (k) {
                // The shadings are computed from tiles every survey has; the
                // orthomosaic is a separate product only some flights produced.
                if (k === 'ortho' && !p.ortho_url) return;
                var o = document.createElement('option');
                o.value = k; o.textContent = LIDAR_PRESETS[k].label;
                if (st && st.preset === k) o.selected = true;
                sel.appendChild(o);
            });
            sel.addEventListener('change', function () {
                if (state[p.id]) {
                    state[p.id].preset = sel.value;
                    _lidarAddLayer(p.id, m, state);
                    writeHashState();
                }
            });
            ctl.appendChild(sel);

            var op = document.createElement('input');
            op.type = 'range'; op.min = 0; op.max = 100;
            op.value = st ? Math.round(st.opacity * 100) : 100;
            op.style.cssText = 'width:100%;';
            op.addEventListener('input', function () {
                if (state[p.id]) {
                    state[p.id].opacity = op.value / 100;
                    if (m.getLayer(_lidarLayerId(p.id)))
                        m.setPaintProperty(_lidarLayerId(p.id), 'raster-opacity', op.value / 100);
                }
            });
            op.addEventListener('change', function () { writeHashState(); });   // once per drag, not per pixel
            ctl.appendChild(op);
            row.appendChild(ctl);

            cb.addEventListener('change', function () {
                if (cb.checked) {
                    state[p.id] = { preset: sel.value, opacity: op.value / 100 };
                    _lidarAddLayer(p.id, m, state);
                    ctl.style.display = 'block';
                } else {
                    _lidarRemoveLayer(p.id, m);
                    delete state[p.id];
                    ctl.style.display = 'none';
                }
                writeHashState();   // li= must track what is actually on
            });
            return row;
        }

        return { _lidarCat: _lidarCat, _lidarActive: _lidarActive, _lidarActiveR: _lidarActiveR, LIDAR_PRESETS: LIDAR_PRESETS, _lidarFetch: _lidarFetch, _bboxInView: _bboxInView, _lidarReplayLayers: _lidarReplayLayers, _liSpec: _liSpec, _liApplyHashSpec: _liApplyHashSpec, _footprintsSet: _footprintsSet, _buildRasterUI: _buildRasterUI, _rasterPanelRefresh: _rasterPanelRefresh, _buildRasterUIRight: _buildRasterUIRight };
    } };
})();
