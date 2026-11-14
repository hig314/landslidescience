/* external_inv.js — Mirrored third-party landslide inventories: registry, sidebar rows, bbox fetch, popups.
 *
 * Moved out of map.js on 2026-11-14 without changing its text. map.js calls
 * window.LSExternal.init(ctx) once, at the point in its start-up where this code
 * used to sit, and passes in the few things it needs from the map page:
 *   API_BASE, _pendingExt, map, _swipe, _promoteStart, esc.
 * It returns what map.js still uses: _extSpec, _extReplayLayers, _extEnsureRegistry, _extApplyOrDefer, _extWireClicks.
 *
 * _pendingExt is the ext= spec from the page's first URL hash, passed once by value;
 * this file clears its own copy after applying it, and map.js never reads it again.
 */
(function () {
    'use strict';

    window.LSExternal = { init: function (ctx) {
        var API_BASE = ctx.API_BASE;
        var _pendingExt = ctx._pendingExt;
        var map = ctx.map;
        var _swipe = ctx._swipe;
        var _promoteStart = ctx._promoteStart;
        var esc = ctx.esc;

        // -----------------------------------------------------------------------
        // Other people's landslide inventories (inventory/external.py)
        //
        // These are MIRRORS, not our data: each publisher's table keeps their
        // columns and their wording, and nothing here translates a record into
        // our schema. The popup therefore shows whatever fields that row actually
        // populated, labelled the way the publisher labels them -- which is the
        // only thing we can honestly claim to know about their records.
        //
        // WHY THESE ARE FETCHED BY BBOX AND NOT SHIPPED WHOLE
        // Our own inventory is ~1,500 features and ships once. These are 77,142
        // across six layers, and Canada alone is 31,005 points with 28 attributes
        // each. So the endpoint takes a bbox and a cap, and the layer refetches on
        // moveend. A cap that is silently hit would look like sparse data, so the
        // row says when it truncated rather than letting the map imply a gap.
        //
        // WHY A ZOOM FLOOR
        // Below EXT_MIN_ZOOM a continental view would ask for every point in
        // Alaska or Canada, hit the cap, and draw a smear that means nothing. The
        // row stays visible and says to zoom in, because "no data here" and "too
        // far out to ask" are different answers and the map should not conflate
        // them.
        // -----------------------------------------------------------------------
        var EXT_MIN_ZOOM = 7;
        var _extSources = null;     // registry from api/external/
        var _extActive = {};        // "sid/layer" -> true
        var _extData = {};          // "sid/layer" -> last FeatureCollection
        var _extNote = {};          // "sid/layer" -> {n, truncated, far}
        var _extTimer = null;
        var _extSeq = 0;
        var _extPending = null;    // ext= seen before the registry arrived

        function _extKey(sid, layer) { return sid + '/' + layer; }
        function _extSrcId(k) { return 'ext-src-' + k.replace('/', '__'); }
        function _extLyrId(k, kind) { return 'ext-' + kind + '-' + k.replace('/', '__'); }

        // One def builder per geometry kind, so the wiper's right pane can mirror
        // these exactly as it mirrors faults and survey circles.
        function _extLayerDefs(k, geom, colour) {
            var src = _extSrcId(k);
            if (geom === 'POINT') {
                return [{
                    id: _extLyrId(k, 'circle'), type: 'circle', source: src,
                    paint: {
                        'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 2.5, 12, 4.5, 16, 7],
                        'circle-color': colour,
                        'circle-opacity': 0.85,
                        'circle-stroke-width': 1,
                        'circle-stroke-color': '#ffffff',
                        'circle-stroke-opacity': 0.9
                    }
                }];
            }
            if (geom === 'MULTIPOLYGON') {
                return [
                    { id: _extLyrId(k, 'fill'), type: 'fill', source: src,
                      paint: { 'fill-color': colour, 'fill-opacity': 0.18 } },
                    { id: _extLyrId(k, 'line'), type: 'line', source: src,
                      paint: { 'line-color': colour, 'line-width': 1.2, 'line-opacity': 0.9 } }
                ];
            }
            return [{
                id: _extLyrId(k, 'line'), type: 'line', source: src,
                paint: { 'line-color': colour, 'line-width': 2, 'line-opacity': 0.9,
                         'line-dasharray': [3, 1.5] }
            }];
        }

        function _extEnsure(k, geom, colour, m) {
            m = m || map;
            if (!m || !m.getStyle) return;
            // Ticking the box before the style has finished loading used to return
            // here and never come back: the fetch still ran and the row still said
            // "N in view", so the count claimed data the map was not drawing. Wait
            // for the style instead of dropping the request on the floor.
            if (!m.isStyleLoaded || !m.isStyleLoaded()) {
                m.once('idle', function () {
                    if (_extActive[k]) _extEnsure(k, geom, colour, m);
                });
                return;
            }
            var src = _extSrcId(k);
            if (!m.getSource(src)) {
                m.addSource(src, { type: 'geojson',
                    data: _extData[k] || { type: 'FeatureCollection', features: [] } });
            }
            // Below the landslide points so our own records always win a click,
            // and so an external layer can never hide the inventory it sits beside.
            var before = m.getLayer('points') ? 'points' : undefined;
            _extLayerDefs(k, geom, colour).forEach(function (def) {
                if (!m.getLayer(def.id)) m.addLayer(def, before);
            });
        }

        function _extRemove(k, geom, m) {
            m = m || map;
            if (!m || !m.getStyle || !m.getStyle()) return;
            _extLayerDefs(k, geom, '#000').forEach(function (def) {
                if (m.getLayer(def.id)) m.removeLayer(def.id);
            });
            if (m.getSource(_extSrcId(k))) m.removeSource(_extSrcId(k));
        }

        function _extSetData(k, fc) {
            // Cached first: a source created later reads _extData for its initial
            // data, so an answer that arrives before the layer exists is kept
            // rather than lost.
            _extData[k] = fc;
            [map, _swipe && _swipe.map].forEach(function (m) {
                if (m && m.getSource && m.getSource(_extSrcId(k))) m.getSource(_extSrcId(k)).setData(fc);
            });
        }

        function _extRefresh() {
            var keys = Object.keys(_extActive);
            if (!keys.length || !map) return;
            var far = map.getZoom() < EXT_MIN_ZOOM;
            var b = map.getBounds();
            var bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
                       .map(function (v) { return v.toFixed(5); }).join(',');
            var seq = ++_extSeq;
            keys.forEach(function (k) {
                if (far) {
                    _extNote[k] = { far: true };
                    _extSetData(k, { type: 'FeatureCollection', features: [] });
                    _extRenderNotes();
                    return;
                }
                var parts = k.split('/');
                fetch(API_BASE + 'api/external/' + parts[0] + '/' + parts[1] + '/?bbox=' + bbox)
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (fc) {
                        // A slow response from a pan the user has already left
                        // would repaint the previous view's features over the new
                        // one, so late answers are dropped rather than drawn.
                        if (!fc || seq !== _extSeq || !_extActive[k]) return;
                        _extSetData(k, fc);
                        _extNote[k] = { n: fc.features.length, truncated: !!fc.truncated };
                        _extRenderNotes();
                    })
                    .catch(function (e) { console.error('external fetch failed', k, e); });
            });
        }

        function _extRefreshSoon() {
            if (_extTimer) clearTimeout(_extTimer);
            _extTimer = setTimeout(_extRefresh, 250);
        }

        function _extRenderNotes() {
            Object.keys(_extNote).forEach(function (k) {
                var el = document.getElementById('extnote-' + k.replace('/', '__'));
                if (!el) return;
                var s = _extNote[k];
                if (!_extActive[k]) { el.textContent = ''; return; }
                el.textContent = s.far ? 'zoom in to load'
                    : s.truncated ? (s.n + ' shown — zoom in for the rest')
                    : (s.n + ' in view');
            });
        }

        // Encode/apply, mirroring _lidarEncode / the li= applier. Only the layers
        // actually on are listed; an empty set writes nothing, so a reader who has
        // never touched these never sees the parameter.
        function _extSpec() {
            var out = {};
            Object.keys(_extActive).sort().forEach(function (k) { out[k] = true; });
            return out;
        }

        function _extApply(want) {
            if (!want || !_extSources) return;
            // Off first, so a link that names a different set does not leave the
            // previous one drawn underneath it.
            Object.keys(_extActive).forEach(function (k) {
                if (want[k]) return;
                var parts = k.split('/');
                var s0 = _extSources.filter(function (x) { return x.id === parts[0]; })[0];
                var L0 = s0 && s0.layers.filter(function (x) { return x.layer === parts[1]; })[0];
                delete _extActive[k];
                delete _extNote[k];
                if (L0) {
                    _extRemove(k, L0.geom, map);
                    if (_swipe && _swipe.map) _extRemove(k, L0.geom, _swipe.map);
                }
            });
            Object.keys(want).forEach(function (k) {
                if (_extActive[k]) return;
                var parts = k.split('/');
                var s0 = _extSources.filter(function (x) { return x.id === parts[0]; })[0];
                var L0 = s0 && s0.layers.filter(function (x) { return x.layer === parts[1]; })[0];
                if (!L0) return;          // a source that is no longer mirrored
                _extActive[k] = true;
                _extEnsure(k, L0.geom, s0.colour, map);
                if (_swipe && _swipe.map) _extEnsure(k, L0.geom, s0.colour, _swipe.map);
            });
            _extBuildUI();
            _extRefresh();
        }

        function _extReplayLayers(m) {
            if (!_extSources) return;
            Object.keys(_extActive).forEach(function (k) {
                var parts = k.split('/');
                var s = _extSources.filter(function (x) { return x.id === parts[0]; })[0];
                if (!s) return;
                var L = s.layers.filter(function (x) { return x.layer === parts[1]; })[0];
                if (L) _extEnsure(k, L.geom, s.colour, m);
            });
        }

        function _extBuildUI() {
            var host = document.getElementById('ext-inventories');
            if (!host || !_extSources || !_extSources.length) return;
            host.innerHTML = '';
            var h = document.createElement('div');
            h.style.cssText = 'font-size:11px;color:#666;text-transform:uppercase;' +
                              'letter-spacing:0.5px;margin:10px 0 4px;';
            h.textContent = 'Other landslide inventories';
            host.appendChild(h);
            var note = document.createElement('div');
            note.style.cssText = 'font-size:10px;color:#999;margin-bottom:5px;line-height:1.35;';
            note.textContent = 'Published by others and mirrored here unchanged. ' +
                               'Click a feature to see the fields they recorded.';
            host.appendChild(note);

            _extSources.forEach(function (s) {
                var box = document.createElement('div');
                box.style.cssText = 'margin-bottom:6px;';
                var title = document.createElement('div');
                title.style.cssText = 'font-size:11px;font-weight:600;color:#444;display:flex;' +
                                      'align-items:center;gap:5px;';
                // One box for the whole source when it has several layers (DGGS
                // ships five), so turning all of it on is one click, not five.
                // Tri-state: indeterminate while only some of its layers are on.
                var rows = [];
                var all = null;
                if (s.layers.length > 1) {
                    all = document.createElement('input');
                    all.type = 'checkbox';
                    all.title = 'Show or hide every layer of this inventory';
                    all.style.margin = '0';
                    title.appendChild(all);
                }
                function syncAll() {
                    if (!all) return;
                    var on = rows.filter(function (r) { return r.cb.checked; }).length;
                    all.checked = on === rows.length;
                    all.indeterminate = on > 0 && on < rows.length;
                }
                var sw = document.createElement('span');
                sw.style.cssText = 'width:9px;height:9px;border-radius:50%;flex:none;background:' +
                                   s.colour + ';border:1px solid #fff;box-shadow:0 0 0 1px #bbb;';
                title.appendChild(sw);
                var a = document.createElement('a');
                a.href = s.url; a.target = '_blank'; a.rel = 'noopener';
                a.textContent = s.title;
                a.style.cssText = 'color:#444;text-decoration:none;border-bottom:1px dotted #bbb;';
                a.title = s.citation + '\n\n' + s.licence;
                title.appendChild(a);
                box.appendChild(title);

                s.layers.forEach(function (L) {
                    var k = _extKey(s.id, L.layer);
                    var lab = document.createElement('label');
                    lab.className = 'nav-filter-label small';
                    lab.style.cssText = 'margin-left:14px;display:block;';
                    var cb = document.createElement('input');
                    cb.type = 'checkbox';
                    cb.checked = !!_extActive[k];
                    lab.appendChild(cb);
                    lab.appendChild(document.createTextNode(' ' + L.label + ' '));
                    var cnt = document.createElement('span');
                    cnt.style.cssText = 'color:#999;font-size:10px;';
                    cnt.textContent = '(' + L.count.toLocaleString() + ')';
                    lab.appendChild(cnt);
                    var st = document.createElement('span');
                    st.id = 'extnote-' + k.replace('/', '__');
                    st.style.cssText = 'display:block;margin-left:18px;color:#888;font-size:10px;';
                    var row = { k: k, L: L, cb: cb, st: st };
                    rows.push(row);
                    cb.addEventListener('change', function () {
                        _extToggle(row, s, cb.checked);
                        syncAll();
                        _extRefresh();
                        if (window.LSTrack) LSTrack.event('overlay', { which: 'ext:' + k });
                    });
                    lab.appendChild(st);
                    box.appendChild(lab);
                });
                if (all) {
                    syncAll();
                    all.addEventListener('change', function () {
                        var on = all.checked;
                        rows.forEach(function (r) {
                            if (r.cb.checked === on) return;
                            r.cb.checked = on;
                            _extToggle(r, s, on);
                        });
                        syncAll();
                        _extRefresh();   // once, for every layer just switched on
                        if (window.LSTrack) LSTrack.event('overlay', { which: 'ext:' + s.id + '/*' });
                    });
                }
                host.appendChild(box);
            });
        }

        // Switch one mirrored layer on or off on both panes. The caller refreshes,
        // so turning a whole source on fetches once rather than once per layer.
        function _extToggle(r, s, on) {
            if (on) {
                _extActive[r.k] = true;
                _extEnsure(r.k, r.L.geom, s.colour, map);
                if (_swipe && _swipe.map) _extEnsure(r.k, r.L.geom, s.colour, _swipe.map);
            } else {
                delete _extActive[r.k];
                delete _extNote[r.k];
                _extRemove(r.k, r.L.geom, map);
                if (_swipe && _swipe.map) _extRemove(r.k, r.L.geom, _swipe.map);
                r.st.textContent = '';
            }
        }

        function _extFetchRegistry() {
            fetch(API_BASE + 'api/external/')
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (d) {
                    if (!d || !d.sources) return;
                    _extSources = d.sources;
                    _extBuildUI();
                    // A link or restored view may have named layers before the
                    // registry existed; apply it now that the ids mean something.
                    var want = _extPending || _pendingExt;
                    if (want) { _extApply(want); _extPending = null; _pendingExt = null; }
                })
                .catch(function (e) { console.error('external registry failed', e); });
        }
        // The two things the rest of the page asks of the registry, as functions
        // so the registry and the pending spec stay private to this section.
        function _extEnsureRegistry() { if (!_extSources) _extFetchRegistry(); }
        function _extApplyOrDefer(spec) {
            if (_extSources) _extApply(spec); else _extPending = spec;
        }

        // Click: show whatever fields this record actually carries, in the
        // publisher's own words and their own column order. No curation beyond
        // dropping empties -- deciding which of someone else's fields matter is
        // the kind of judgement this whole design exists to postpone.
        function _extPopup(e, s, L) {
            var p = e.features[0].properties || {};
            var rows = '';
            (L.field_order || Object.keys(p)).forEach(function (c) {
                var v = p[c];
                if (v === undefined || v === null || v === '') return;
                if (c === 'id' || c === 'fetched_at') return;
                rows += '<div style="margin:1px 0;display:flex;gap:6px;">' +
                        '<span style="color:#888;flex:none;">' + esc(L.fields[c] || c) + ':</span>' +
                        '<span>' + esc(String(v)) + '</span></div>';
            });
            // Only the FIELD LIST scrolls. The heading says whose data this is and
            // the footer carries the citation both licences require, so neither
            // may scroll out of sight -- and a records-long panel that opened
            // somewhere in its own middle was doing exactly that.
            var html = '<div style="font:12px/1.4 system-ui,sans-serif;max-width:300px;">' +
                '<div style="font-weight:600;color:' + s.colour + ';margin-bottom:3px;">' +
                    esc(s.short) + ' · ' + esc(L.label) + '</div>' +
                '<div class="ext-fields" style="max-height:280px;overflow:auto;">' +
                    rows + '</div>' +
                '<div style="margin-top:5px;color:#aaa;font-size:10px;border-top:1px solid #eee;' +
                'padding-top:4px;">' + esc(s.citation) + ' <a href="' + esc(s.url) +
                '" target="_blank" rel="noopener">source</a><br>Mirrored unmodified; ' +
                'not part of this inventory.</div>' +
                (window._isInventoryEditor
                    ? '<div style="margin-top:7px;border-top:1px solid #eee;padding-top:7px;">' +
                      '<button type="button" class="ext-promote" style="font-size:11px;' +
                      'padding:4px 9px;cursor:pointer;">＋ Promote into the inventory</button>' +
                      '<div style="color:#999;font-size:10px;margin-top:3px;">Traces a new ' +
                      'outline and opens the review form. Nothing is copied automatically.</div>' +
                      '</div>'
                    // Say why the button is absent rather than just omitting it.
                    // A signed-out editor otherwise sees a popup identical to the
                    // one they expect minus the control, with nothing to explain
                    // the difference -- which cost real time the first time it
                    // happened. The same reasoning as the sign-in note in
                    // CLAUDE.md: a missing affordance should account for itself.
                    : '<div style="margin-top:7px;border-top:1px solid #eee;padding-top:7px;' +
                      'color:#999;font-size:10px;">' +
                      '<a href="/inventory/login/">Sign in</a> as an editor to promote ' +
                      'this record into the inventory.</div>') +
                '</div>';
            // focusAfterOpen:false stops MapLibre focusing the close button, which
            // the browser then scrolls into view; resetting scrollTop afterwards
            // covers the case where something else has already moved it.
            var pop = LSTools.popup.show(new maplibregl.Popup({ maxWidth: '330px', focusAfterOpen: false })
                .setLngLat(e.lngLat).setHTML(html).addTo(map));
            var box = pop.getElement().querySelector('.ext-fields');
            if (box) box.scrollTop = 0;
            var pbtn = pop.getElement().querySelector('.ext-promote');
            if (pbtn) pbtn.addEventListener('click', function () {
                _promoteStart(s, L, p);
            });
        }

        // Every active mirror layer with the source/layer it belongs to. `layers`
        // is a FUNCTION on the dispatcher entry because this set changes as the
        // reader ticks boxes.
        function _extActiveLayers() {
            var out = [];
            if (!_extSources) return out;
            Object.keys(_extActive).forEach(function (k) {
                var parts = k.split('/');
                var s = _extSources.filter(function (x) { return x.id === parts[0]; })[0];
                if (!s) return;
                var L = s.layers.filter(function (x) { return x.layer === parts[1]; })[0];
                if (!L) return;
                _extLayerDefs(k, L.geom, s.colour).forEach(function (d) {
                    out.push({ id: d.id, s: s, L: L });
                });
            });
            return out;
        }
        function _extWireClicks() {
            // A REFERENCE entry, so it opens alongside our own record's panel and
            // no longer depends on which of our layers the cursor is over. The old
            // handler deferred to `points` but not to `polygon-fill`, so the same
            // external point showed its popup inside one of our polygons and not
            // on one of our dots -- the asymmetry that prompted this whole review.
            LSTools.clicks.register({
                id: 'external', kind: 'reference', priority: 10,
                layers: function () { return _extActiveLayers().map(function (x) { return x.id; }); },
                handler: function (f, e) {
                    var lid = f.layer && f.layer.id;
                    var m = _extActiveLayers().filter(function (x) { return x.id === lid; })[0];
                    if (m) _extPopup({ features: [f], lngLat: e.lngLat }, m.s, m.L);
                }
            });
            map.on('moveend', _extRefreshSoon);
        }

        return { _extSpec: _extSpec, _extReplayLayers: _extReplayLayers, _extEnsureRegistry: _extEnsureRegistry, _extApplyOrDefer: _extApplyOrDefer, _extWireClicks: _extWireClicks };
    } };
})();
