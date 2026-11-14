/* trace_rasters.js — Traced imagery: editor GeoTIFF uploads and Sentinel fetches (registry, layers, im= state, upload UI, rows).
 *
 * Moved out of map.js on 2026-11-14 without changing its text. map.js calls
 * window.LSTraceRasters.init(ctx) once, at the point in its start-up where this code
 * used to sit, and passes in the few things it needs from the map page:
 *   API_BASE, SUSC_LAYERS, _pendingIm, map, RASTER_UI_ZOOM, _traceReseat, _bboxInView, _footprintsSet, _getLastDetail.
 * It returns what map.js still uses: _traceActive, _traceRows, _traceLoadAtStart, _traceRow, _traceAddLayer, _traceReplayLayers, _imSpec, _imApplyHashSpec, _traceZoomTo, _traceThumbUrl, _traceUpdateSummary, _renderTraceRows, _buildTraceUI.
 *
 * _pendingIm is the im= spec from the page's first URL hash, passed once by value;
 * this file owns it from then on. _traceActive is one object shared with map.js
 * (never reassigned). The upload UI used to sit further down map.js, after the
 * promotion code; it was moved up beside the rest before the section left.
 */
(function () {
    'use strict';

    window.LSTraceRasters = { init: function (ctx) {
        var API_BASE = ctx.API_BASE;
        var SUSC_LAYERS = ctx.SUSC_LAYERS;
        var _pendingIm = ctx._pendingIm;
        var map = ctx.map;
        var RASTER_UI_ZOOM = ctx.RASTER_UI_ZOOM;
        var _traceReseat = ctx._traceReseat;
        var _bboxInView = ctx._bboxInView;
        var _footprintsSet = ctx._footprintsSet;
        var _getLastDetail = ctx._getLastDetail;

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
        // What the rest of the page asks of this section without touching its
        // state: the current rows, and the start-up fetch (forced when the first
        // URL named imagery, so a visitor's im= resolves too).
        function _traceRows() { return _traceRasters; }
        function _traceLoadAtStart() { _traceLoad(!!_pendingIm); }

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
                // The server says where this mode's tiles live: a pre-baked
                // PMTiles archive (one ranged URL) or the baked XYZ pyramid.
                // ?v= is the bake time: tiles are served immutable for a year,
                // so without it a re-bake in a new render mode never shows.
                var t = r.tiles || { kind: 'xyz', url: API_BASE + 'tiles/trace/' + id + '/{z}/{x}/{y}.png' };
                var url = t.kind === 'pmtiles'
                    ? 'pmtiles://' + location.origin + t.url + '/{z}/{x}/{y}'
                    : t.url + '?v=' + (r.baked_at || 0);
                map.addSource(srcId, {
                    type: 'raster', tiles: [url],
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
            _traceReseat();
        }
        function _traceRemoveLayer(id) {
            if (map.getLayer('trace-' + id)) map.removeLayer('trace-' + id);
            if (map.getSource('trace-src-' + id)) map.removeSource('trace-src-' + id);
        }
        // Show/hide WITHOUT removing: a removed source drops its tiles and a
        // quick off-on refetches every one. Parked at opacity 0 the source stays
        // in use and its tiles stay warm — the same rule the lidar overlays use.
        // _traceRemoveLayer is for delete and re-bake, where the tiles must go.
        function _traceSetVisible(id, on) {
            var lyr = 'trace-' + id;
            if (on) {
                if (_traceActive[id] == null) _traceActive[id] = 1;
                if (!map.getLayer(lyr)) { _traceAddLayer(id); return; }
                map.setPaintProperty(lyr, 'raster-opacity', _traceActive[id]);
            } else {
                delete _traceActive[id];
                if (map.getLayer(lyr)) map.setPaintProperty(lyr, 'raster-opacity', 0);
            }
        }
        // Re-add enabled overlays after a basemap switch (called by initDataLayers,
        // right after faults-line exists so the insertion point is stable).
        function _traceReplayLayers() {
            Object.keys(_traceActive).forEach(function (id) { _traceAddLayer(+id); });
        }
        // --- imagery overlays in the shareable view state (`im=`) ----------------
        function _imSpec() {
            var out = {};
            Object.keys(_traceActive).sort(function (a, b) { return a - b; })
                .forEach(function (id) { out[id] = _traceActive[id]; });
            return out;
        }
        // Overwrite imagery visibility from a parsed `im` spec: listed rows go on at
        // the given opacity, unlisted ones go off. Rows the viewer cannot see are
        // skipped rather than failing the whole view -- a default view naming an
        // editor-only upload must still open for a visitor, just without it.
        function _imApplyHashSpec(spec) {
            if (!_traceRasters.length) {
                // An empty spec with no list loaded has nothing to turn off, and
                // is no reason to fetch the list for a visitor.
                if (!spec || !Object.keys(spec).length) { _pendingIm = null; return; }
                // Not in yet. For an editor the boot load is already on its way;
                // for a visitor nothing would ever fetch it, so ask now.
                _pendingIm = spec;
                _traceLoad(true);
                return;
            }
            var known = {};
            _traceRasters.forEach(function (r) { known[r.id] = r; });
            Object.keys(_traceActive).forEach(function (id) {
                if (!spec || spec[id] == null) _traceSetVisible(+id, false);
            });
            Object.keys(spec || {}).forEach(function (id) {
                var r = known[id];
                if (!r || r.status !== 'ready') return;
                _traceActive[id] = spec[id];
                _traceSetVisible(+id, true);
            });
            if (typeof _renderTraceRows === 'function') _renderTraceRows();
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
                                _traceRemoveLayer(id);  // a re-bake changes the tile URL (?v=)
                                _traceAddLayer(id);
                                _traceZoomTo(row);
                            }
                            _renderTraceRows();
                            _traceShowCue();
                        }
                    }).catch(function () {});
            }, 2500);
        }
        // `force` loads the list for a visitor who is not an editor. The endpoint
        // answers them with the public rows only, so this is exactly what a view
        // naming a Sentinel-2 image needs to resolve it -- and nothing more. It is
        // not done on every public page load, only when a view actually asks for
        // imagery, which is why the flag exists rather than dropping the gate.
        function _traceLoad(force) {
            if (!window._isInventoryEditor && !force) return;
            fetch(API_BASE + 'api/trace_rasters/')
                .then(function (res) { return res.json(); })
                .then(function (d) {
                    _traceRasters = (d && d.rasters) || [];
                    if (_pendingIm) { var sp = _pendingIm; _pendingIm = null; _imApplyHashSpec(sp); }
                    if (!document.getElementById('trace-imagery-rows')) return;
                    _renderTraceRows();
                    _traceRasters.forEach(function (r) {
                        if (r.status === 'processing' && !r.stalled) _tracePoll(r.id);
                    });
                }).catch(function () {});
        }

        var _TRACE_BTN_CSS = 'font-size:11px;padding:1px 6px;border:1px solid #bbb;' +
                             'border-radius:3px;background:#fff;cursor:pointer;';

        // How the server composes the PNG. 'auto' picks false colour when the file
        // has a 4th (NIR) band — on a Planet scene that turns ice bright cyan and
        // rubble dark, with the equalised stretch keeping detail at both ends.
        var _TRACE_RENDERS = [
            ['auto', 'Render: auto (false colour if NIR)'],
            ['nrg',  'False colour NIR–R–G'],
            ['rgb',  'Natural colour'],
            ['gray', 'Greyscale (first band)']
        ];
        function _traceRenderSelect(value) {
            var sel = document.createElement('select');
            sel.title = 'How the image is composed into a picture';
            _TRACE_RENDERS.forEach(function (o) {
                var op = document.createElement('option');
                op.value = o[0]; op.textContent = o[1];
                sel.appendChild(op);
            });
            sel.value = value || 'auto';
            return sel;
        }
        function _tracePostForm(url, fields) {
            var fd = new FormData();
            Object.keys(fields).forEach(function (k) { fd.append(k, fields[k]); });
            return fetch(url, { method: 'POST', headers: { 'X-CSRFToken': window.CSRF_TOKEN }, body: fd })
                .then(function (res) { return res.json().then(function (j) { return { ok: res.ok, j: j }; }); });
        }

        // Summary line on the collapsed uploads list — kept current from every
        // state change (enable/disable, upload, poll completion, delete).
        // Same standard as the lidar list: only uploads that touch the current
        // view are offered. Everything else is reachable by turning the footprints
        // on and going there.
        function _traceInView(r) {
            return r.bounds_w != null &&
                   _bboxInView([r.bounds_w, r.bounds_s, r.bounds_e, r.bounds_n]);
        }
        function _traceUpdateSummary() {
            var sum = document.getElementById('trace-list-summary');
            if (!sum) return;
            if (!_traceRasters.length) { sum.textContent = 'No uploads yet'; return; }
            var inView = _traceRasters.filter(_traceInView).length;
            var shown = Object.keys(_traceActive).length;
            var txt = _traceRasters.length + ' upload' + (_traceRasters.length === 1 ? '' : 's') +
                      ' · ' + inView + ' in view';
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
            if (map && map.getSource('fp-trace-src')) _footprintsSet('trace', true);
            var box = document.getElementById('trace-imagery-rows');
            if (!box) return;
            box.innerHTML = '';
            if (!_traceRasters.length) {
                box.innerHTML = '<div style="font-size:11px;color:#999;">No uploads yet.</div>';
                return;
            }
            var rowsInView = _traceRasters.filter(_traceInView);
            if (!rowsInView.length) {
                box.innerHTML = '<div style="font-size:11px;color:#999;">No uploads in this view — ' +
                                'turn on the footprints to see where they are.</div>';
                return;
            }
            rowsInView.forEach(function (r) {
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
                    _traceSetVisible(r.id, cb.checked);
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
                // Re-bake from the uploaded original. Used by the ⟳ button (recover a
                // failed/stalled bake) and by the render selector on the second line,
                // which re-bakes the moment it is changed — there is nothing else a
                // changed composition could mean.
                function rebake(render) {
                    _tracePostForm(API_BASE + 'api/trace_rasters/' + r.id + '/rebuild/',
                                   { render: render || r.render || 'auto' })
                        .then(function (res) {
                            if (res.ok && res.j.ok && res.j.reused) {
                                // That mode was baked earlier: swap pyramids, no wait.
                                _traceReplaceRow(res.j.raster);
                                if (_traceActive[r.id] != null) { _traceRemoveLayer(r.id); _traceAddLayer(r.id); }
                                _traceLastUploaded = null;
                                _renderTraceRows(); _traceShowCue();
                            } else if (res.ok && res.j.ok) {
                                r.status = 'processing'; r.stalled = false; r.error = null;
                                if (render) r.render = render;
                                _traceLastUploaded = { id: r.id, title: r.title };
                                _renderTraceRows(); _traceShowCue();
                                _tracePoll(r.id);
                            } else alert((res.j && res.j.error) || 'rebuild failed');
                        });
                }
                if (r.status !== 'processing' || r.stalled) {
                    btn('⟳', 'Re-bake from the uploaded original', function () { rebake(null); });
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
                    var rsel = _traceRenderSelect(r.render || 'auto');
                    rsel.style.cssText = 'font-size:10px;max-width:120px;';
                    rsel.title = 'Composition — changing it re-bakes the image (a few minutes)';
                    rsel.addEventListener('change', function () { rebake(rsel.value); });
                    line2.appendChild(rsel);

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
                                var _lastDetail = _getLastDetail();
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

        // Planet scene ids lead with the capture timestamp: 20240712_115100_43_2459_3B_...
        // Most other providers' exports (Maxar, Sentinel via EO Browser) also carry a
        // YYYYMMDD or YYYY-MM-DD somewhere in the name. Only a plausible date wins.
        function _traceDateFromName(name) {
            var m = /(^|[^\d])((19|20)\d{2})[-_]?(0[1-9]|1[0-2])[-_]?(0[1-9]|[12]\d|3[01])(?![\d])/.exec(name || '');
            return m ? m[2] + '-' + m[4] + '-' + m[5] : '';
        }
        function _traceSceneIdFromName(name) {
            // Planet: the first four underscore fields are the scene id.
            var m = /^(\d{8}_\d{6}_\d{2}_[0-9a-f]{4})/i.exec(name || '');
            return m ? 'PlanetScope ' + m[1] : '';
        }
        var _traceLastUploaded = null;   // {id, title} — drives the success cue across re-renders

        // Sentinel-2 by date and place. Cloud cover comes from the scene's own
        // metadata, so a date can be judged before anything is fetched: that is
        // the whole point of listing candidates rather than just taking the
        // nearest. It is the granule's cover, not this window's, so a 60% scene
        // can still be clear over one valley — the form says so rather than
        // hiding the high numbers.
        function _sentinelCloudSpan(cc) {
            var el = document.createElement('span');
            if (cc == null) { el.textContent = 'cloud ?'; el.style.color = '#888'; return el; }
            el.textContent = 'cloud ' + Math.round(cc) + '%';
            el.style.color = cc < 10 ? '#1b7a3d' : (cc < 35 ? '#b26a00' : '#999');
            if (cc < 10) el.style.fontWeight = '600';
            return el;
        }

        function _fillSentinelForm(det) {
            var inpCss = 'font-size:12px;padding:3px 4px;border:1px solid #ccc;border-radius:3px;width:100%;box-sizing:border-box;';
            var form = document.createElement('div');
            form.style.cssText = 'display:flex;flex-direction:column;gap:4px;margin-top:5px;';

            var where = document.createElement('div');
            where.style.cssText = 'font-size:11px;color:#777;line-height:1.35;';
            function setWhere() {
                var c = map.getCenter();
                where.textContent = 'Cut around the map centre: ' + c.lat.toFixed(4) + ', ' +
                                    c.lng.toFixed(4) + ' — pan before searching to move it.';
            }
            setWhere();
            det.addEventListener('toggle', function () { if (det.open) setWhere(); });

            var dateInp = document.createElement('input');
            dateInp.type = 'date';
            dateInp.title = 'The date you are looking for; nearby passes are listed too';
            dateInp.style.cssText = inpCss;

            var daysSel = document.createElement('select');
            daysSel.style.cssText = inpCss;
            [[3, '± 3 days'], [7, '± 7 days'], [14, '± 2 weeks'], [30, '± 1 month']]
                .forEach(function (o) {
                    var op = document.createElement('option');
                    op.value = o[0]; op.textContent = o[1];
                    daysSel.appendChild(op);
                });
            daysSel.value = '7';

            var opt = document.createElement('div');
            opt.style.cssText = 'display:flex;gap:4px;';
            var halfSel = document.createElement('select');
            halfSel.style.cssText = inpCss;
            [[3000, '6 km across'], [6000, '12 km across'], [12000, '24 km across']]
                .forEach(function (o) {
                    var op = document.createElement('option');
                    op.value = o[0]; op.textContent = o[1];
                    halfSel.appendChild(op);
                });
            halfSel.value = '6000';
            var rendSel = document.createElement('select');
            rendSel.style.cssText = inpCss;
            [['nrg', 'False colour'], ['rgb', 'Natural colour']].forEach(function (o) {
                var op = document.createElement('option');
                op.value = o[0]; op.textContent = o[1];
                rendSel.appendChild(op);
            });
            // L1C first, and the default. The atmospheric correction behind L2A
            // misfires in steep shadowed terrain, leaving blotches where a scar
            // would be; L1C is what the sensor saw.
            var lvlSel = document.createElement('select');
            lvlSel.style.cssText = inpCss;
            lvlSel.title = 'L1C is top-of-atmosphere — no correction to misread. ' +
                           'L2A is surface reflectance, which can blotch in deep shade.';
            [['l1c', 'L1C (as seen)'], ['l2a', 'L2A (corrected)']].forEach(function (o) {
                var op = document.createElement('option');
                op.value = o[0]; op.textContent = o[1];
                lvlSel.appendChild(op);
            });
            opt.appendChild(halfSel); opt.appendChild(rendSel); opt.appendChild(lvlSel);

            var goBtn = document.createElement('button');
            goBtn.type = 'button'; goBtn.textContent = 'Find scenes';
            goBtn.style.cssText = _TRACE_BTN_CSS + 'align-self:flex-start;padding:3px 12px;';
            var stat = document.createElement('span');
            stat.style.cssText = 'font-size:11px;';
            var out = document.createElement('div');
            out.style.cssText = 'display:flex;flex-direction:column;gap:2px;';

            form.appendChild(where); form.appendChild(dateInp); form.appendChild(daysSel);
            form.appendChild(opt); form.appendChild(goBtn); form.appendChild(stat);
            form.appendChild(out);
            det.appendChild(form);

            function add(scene, centre) {
                stat.style.color = '#1a73e8';
                stat.textContent = 'fetching the window and baking…';
                fetch(API_BASE + 'api/sentinel/add/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': window.CSRF_TOKEN },
                    body: JSON.stringify({ lat: centre.lat, lon: centre.lon, scene: scene.scene,
                                           date: scene.date, half: +halfSel.value,
                                           render: rendSel.value, level: lvlSel.value })
                }).then(function (res) { return res.json().then(function (j) { return { ok: res.ok, j: j }; }); })
                  .then(function (res) {
                    if (!(res.ok && res.j.ok)) {
                        stat.style.color = '#c00';
                        stat.textContent = (res.j && res.j.error) || 'could not add that scene';
                        return;
                    }
                    stat.textContent = ''; out.innerHTML = ''; det.open = false;
                    _traceReplaceRow(res.j.raster);
                    _traceLastUploaded = { id: res.j.id, title: res.j.raster.title };
                    _traceGoTo(res.j.raster);
                    _traceShowCue();
                    _traceSetListOpen(true);
                    _renderTraceRows();
                    _tracePoll(res.j.id);
                }).catch(function () {
                    stat.style.color = '#c00'; stat.textContent = 'could not add that scene';
                });
            }

            goBtn.addEventListener('click', function () {
                if (!dateInp.value) {
                    stat.style.color = '#c00'; stat.textContent = 'Pick a date first.'; return;
                }
                var c = map.getCenter();
                var centre = { lat: c.lat, lon: c.lng };
                out.innerHTML = '';
                goBtn.disabled = true;
                stat.style.color = '#1a73e8'; stat.textContent = 'searching…';
                fetch(API_BASE + 'api/sentinel/search/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': window.CSRF_TOKEN },
                    body: JSON.stringify({ lat: centre.lat, lon: centre.lon,
                                           date: dateInp.value, days: +daysSel.value,
                                           level: lvlSel.value })
                }).then(function (res) { return res.json().then(function (j) { return { ok: res.ok, j: j }; }); })
                  .then(function (res) {
                    goBtn.disabled = false;
                    if (!(res.ok && res.j.ok)) {
                        stat.style.color = '#c00';
                        stat.textContent = (res.j && res.j.error) || 'search failed';
                        return;
                    }
                    var scenes = res.j.scenes || [];
                    if (!scenes.length) {
                        stat.style.color = '#c00';
                        stat.textContent = 'No Sentinel-2 pass covers this point in that window.';
                        return;
                    }
                    // Date order reads like a calendar; the clearest pass is
                    // marked so the eye finds it without re-sorting the list.
                    var best = null;
                    scenes.forEach(function (s) {
                        if (s.cloud_cover == null) return;
                        if (!best || s.cloud_cover < best.cloud_cover) best = s;
                    });
                    scenes.slice().sort(function (a, b) {
                        return a.date < b.date ? -1 : (a.date > b.date ? 1 : 0);
                    }).forEach(function (s) {
                        var row = document.createElement('div');
                        row.style.cssText = 'display:flex;align-items:center;gap:6px;font-size:12px;';
                        var d = document.createElement('span');
                        d.textContent = (best && s.scene === best.scene ? '★ ' : '') + s.date;
                        d.style.cssText = 'font-variant-numeric:tabular-nums;min-width:86px;';
                        var btn = document.createElement('button');
                        btn.type = 'button'; btn.textContent = 'Add';
                        btn.style.cssText = _TRACE_BTN_CSS + 'padding:1px 8px;font-size:11px;';
                        btn.addEventListener('click', function () { add(s, centre); });
                        row.appendChild(d);
                        row.appendChild(_sentinelCloudSpan(s.cloud_cover));
                        row.appendChild(btn);
                        row.title = s.scene;
                        out.appendChild(row);
                    });
                    stat.style.color = '#777';
                    stat.textContent = scenes.length + ' pass' + (scenes.length > 1 ? 'es' : '') +
                        '; cloud is the whole granule’s, so a high figure can still be clear here.';
                }).catch(function () {
                    goBtn.disabled = false;
                    stat.style.color = '#c00'; stat.textContent = 'search failed';
                });
            });
            return det;
        }

        function _buildTraceUI(opts) {
            opts = opts || {};
            var wrap = document.createElement('div');
            wrap.style.cssText = 'margin-top:12px;';
            var hdr = document.createElement('div');
            hdr.className = 'refmaps-category';
            hdr.textContent = 'Traced imagery';
            wrap.appendChild(hdr);
            var hint = document.createElement('div');
            hint.style.cssText = 'font-size:11px;color:#777;margin-bottom:5px;line-height:1.35;';
            hint.textContent = 'Fetch a Sentinel-2 scene by date, or upload a georeferenced GeoTIFF, then trace it with the ✏ draw tool. Data admins only; Sentinel-2 scenes are shown to everyone.';
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
            if (!opts.far) wrap.appendChild(listDet);

            // Success cue. The form collapses after a good upload, which used to
            // take its status line with it, so the only sign anything happened was
            // a new row in a list that is collapsed by default. This line lives
            // outside both and follows the bake through to "ready".
            var cue = document.createElement('div');
            cue.id = 'trace-upload-cue';
            cue.style.cssText = 'font-size:11px;color:#1b7a3d;margin:4px 0;line-height:1.35;display:none;';
            wrap.appendChild(cue);

            // Sentinel-2 becomes available only when the view is close enough
            // for the cut to mean anything: the window is taken around the map
            // centre and is 12 km across by default, so from a statewide view you
            // would be asking for a scene of somewhere you cannot see. Below the
            // threshold the control stays visible but inert, and says why.
            var s2 = document.createElement('details');
            var s2sum = document.createElement('summary');
            s2sum.textContent = '＋ Fetch Sentinel-2 scene…';
            s2sum.style.cssText = 'cursor:pointer;font-size:12px;color:#1a5fb4;';
            s2.appendChild(s2sum);
            if (opts.far) {
                s2sum.style.color = '#999';
                s2sum.style.cursor = 'default';
                s2.addEventListener('click', function (e) { e.preventDefault(); });
                var s2far = document.createElement('div');
                s2far.style.cssText = 'font-size:11px;color:#777;margin:3px 0 0 14px;line-height:1.35;';
                s2far.textContent = 'Zoom to z' + RASTER_UI_ZOOM + ' first — the scene is cut around the map centre.';
                s2.appendChild(s2far);
                wrap.appendChild(s2);
            } else {
                wrap.appendChild(_fillSentinelForm(s2));
            }

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
            var renderSel = _traceRenderSelect('auto');
            renderSel.style.cssText = inpCss;
            var fileInp = document.createElement('input');
            fileInp.type = 'file'; fileInp.accept = '.tif,.tiff,image/tiff,.pmtiles';
            fileInp.style.cssText = 'font-size:11px;';
            // Fill what the filename already says, without overwriting what the
            // user typed: Planet encodes the capture date and scene id in the name.
            function fromFilename() {
                var f = fileInp.files && fileInp.files[0];
                if (!f) return;
                // A pre-baked archive carries its mode in the name (<stem>.nrg.pmtiles)
                // and needs no bake: the render selector does not apply.
                var pre = /\.(nrg|rgb|gray)\.pmtiles$/i.exec(f.name);
                renderSel.disabled = !!pre;
                if (pre) renderSel.value = pre[1].toLowerCase();
                if (!dateInp.value) dateInp.value = _traceDateFromName(f.name);
                if (!srcInp.value) srcInp.value = _traceSceneIdFromName(f.name);
                if (!titleInp.value) titleInp.placeholder = f.name.replace(/\.tiff?$/i, '');
            }
            fileInp.addEventListener('change', fromFilename);
            // The whole form is a drop target, not just the 30 px file control.
            form.addEventListener('dragover', function (e) {
                e.preventDefault(); e.dataTransfer.dropEffect = 'copy';
                form.style.outline = '2px dashed #1a5fb4';
            });
            form.addEventListener('dragleave', function () { form.style.outline = ''; });
            form.addEventListener('drop', function (e) {
                e.preventDefault(); form.style.outline = '';
                var files = e.dataTransfer && e.dataTransfer.files;
                if (!files || !files.length) return;
                if (!/\.(tiff?|pmtiles)$/i.test(files[0].name)) {
                    stat.style.color = '#c00'; stat.textContent = 'Drop a GeoTIFF (.tif) or a pre-baked .pmtiles.'; return;
                }
                fileInp.files = files;       // DataTransfer.files is assignable to a file input
                stat.textContent = '';
                fromFilename();
                det.open = true;
            });
            var goBtn = document.createElement('button');
            goBtn.type = 'button'; goBtn.textContent = 'Upload';
            goBtn.style.cssText = _TRACE_BTN_CSS + 'align-self:flex-start;padding:3px 12px;';
            var stat = document.createElement('span');
            stat.style.cssText = 'font-size:11px;';
            form.appendChild(titleInp); form.appendChild(dateInp);
            form.appendChild(srcInp); form.appendChild(renderSel); form.appendChild(fileInp);
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
                fd.append('render', renderSel.value);
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
                        _traceLastUploaded = { id: res.j.id, title: res.j.raster.title };
                        _traceGoTo(res.j.raster);
                        _traceShowCue();
                        _traceSetListOpen(true);   // show the new row's status
                        _renderTraceRows();
                        if (res.j.raster.status === 'ready') {
                            // Pre-baked: nothing to wait for — show it now.
                            _traceActive[res.j.id] = 1;
                            _traceAddLayer(res.j.id);
                            _traceZoomTo(res.j.raster);
                            _renderTraceRows(); _traceShowCue();
                        } else {
                            _tracePoll(res.j.id);
                        }
                    } else {
                        stat.style.color = '#c00';
                        stat.textContent = (res.j && res.j.error) || 'upload failed';
                    }
                }).catch(function () {
                    goBtn.disabled = false;
                    stat.style.color = '#c00'; stat.textContent = 'upload failed';
                });
            });

            // Populate AFTER the caller has attached this subtree: both fills find
            // their targets by id, and before attachment getElementById returns
            // nothing (or the previous section's copy), which left the summary on
            // its "No uploads yet" placeholder with an image plainly on the map.
            setTimeout(function () { _renderTraceRows(); _traceShowCue(); }, 0);
            return wrap;
        }

        // Straight after an upload: centre on the image at z10 and switch the
        // upload footprints on, so the blue dashed outline shows where the image
        // is and that it is still baking, before a single tile exists.
        function _traceGoTo(r) {
            if (r.bounds_w == null) return;
            map.easeTo({ center: [(r.bounds_w + r.bounds_e) / 2, (r.bounds_s + r.bounds_n) / 2],
                         zoom: Math.max(map.getZoom(), RASTER_UI_ZOOM), duration: 900 });
            _footprintsSet('trace', true);
            var cb = document.getElementById('fp-cb-trace');
            if (cb) cb.checked = true;
        }

        function _traceShowCue() {
            var cue = document.getElementById('trace-upload-cue');
            if (!cue) return;
            var u = _traceLastUploaded;
            if (!u) { cue.style.display = 'none'; return; }
            var r = _traceRow(u.id);
            cue.style.display = 'block';
            if (!r || r.status === 'processing') {
                cue.style.color = '#1b7a3d';
                cue.textContent = '✓ “' + u.title + '” is baking' + (r && r.render ? ' as ' + r.render : '') +
                                  ' — footprint outlined in blue on the map meanwhile (a few minutes for a Planet scene). ' +
                                  'The image appears there when ready.';
            } else if (r.status === 'ready') {
                cue.style.color = '#1b7a3d';
                cue.textContent = '✓ “' + u.title + '” is ready and shown on the map.';
                setTimeout(function () { if (_traceLastUploaded === u) { _traceLastUploaded = null; _traceShowCue(); } }, 12000);
            } else {
                cue.style.color = '#c00';
                cue.textContent = '✖ “' + u.title + '” failed to bake: ' + (r.error || 'see the row for details.');
            }
        }

        return { _traceActive: _traceActive, _traceRows: _traceRows, _traceLoadAtStart: _traceLoadAtStart, _traceRow: _traceRow, _traceAddLayer: _traceAddLayer, _traceReplayLayers: _traceReplayLayers, _imSpec: _imSpec, _imApplyHashSpec: _imApplyHashSpec, _traceZoomTo: _traceZoomTo, _traceThumbUrl: _traceThumbUrl, _traceUpdateSummary: _traceUpdateSummary, _renderTraceRows: _renderTraceRows, _buildTraceUI: _buildTraceUI };
    } };
})();
