/* external_inv.js — Mirrored third-party landslide inventories: registry, sidebar rows, bbox fetch, popups.
 *
 * Moved out of map.js on 2026-11-14 without changing its text. map.js calls
 * window.LSExternal.init(ctx) once, at the point in its start-up where this code
 * used to sit, and passes in the few things it needs from the map page:
 *   API_BASE, _pendingExt, map, _swipe, _promoteStart, esc.
 * It returns what map.js still uses: _extShowRecord (for ext_explore.js), _extSpec, _extReplayLayers, _extEnsureRegistry, _extApplyOrDefer, _extWireClicks.
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

        // -----------------------------------------------------------------------
        // Review (editors only; inventory/external.py REVIEW). One category per
        // record from a shared list the editor grows with `+`, and an optional
        // note. No category = unreviewed. "Promoted" is not a category: the
        // server reads it from our own records and sends it as _promoted, so it
        // is shown here as a fact, not offered as a choice.
        // -----------------------------------------------------------------------
        var _revCats = null;          // [{id, label, count}], shared by every source
        var _revCatsWait = null;

        function _revFetchCats(force) {
            if (_revCats && !force) return Promise.resolve(_revCats);
            if (_revCatsWait && !force) return _revCatsWait;
            _revCatsWait = fetch(API_BASE + 'api/external/review/categories/', { credentials: 'same-origin' })
                .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || r.status); return d; }); })
                .then(function (d) { _revCats = d.categories || []; _revCatsWait = null; return _revCats; })
                .catch(function (e) { _revCatsWait = null; throw e; });
            return _revCatsWait;
        }

        function _revPost(url, body) {
            return fetch(url, {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': window.CSRF_TOKEN },
                body: JSON.stringify(body)
            }).then(function (r) {
                return r.json().catch(function () { return {}; }).then(function (d) {
                    if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
                    return d;
                });
            });
        }

        // Write a saved review into every cached copy of that record's
        // properties, so the next click on it (either pane) shows what was saved
        // without waiting for a refetch.
        function _revStore(s, L, extId, rev) {
            var k = _extKey(s.id, L.layer), fc = _extData[k];
            var apply = function (props) {
                ['_review_id', '_review', '_review_note', '_reviewed_by', '_reviewed_at'].forEach(function (f) {
                    if (rev[f] === undefined) delete props[f]; else props[f] = rev[f];
                });
            };
            if (fc && fc.features) fc.features.forEach(function (f) {
                if (f.properties && String(f.properties.ext_id) === String(extId)) apply(f.properties);
            });
        }

        function _extReviewUI(box, s, L, p) {
            var extId = p.ext_id;
            if (extId === undefined || extId === null) return;
            box.style.cssText = 'margin:0 0 6px;padding:5px 6px;background:#f6f6f2;border:1px solid #e4e4dc;' +
                                'border-radius:4px;font-size:11px;';
            var url = API_BASE + 'api/external/' + s.id + '/' + L.layer + '/review/' + encodeURIComponent(extId) + '/';
            var promoted = '';
            (p._promoted || []).forEach(function (pr) {
                promoted += (promoted ? ', ' : '') + '<a href="#id=' + pr[0] + '">' + esc(pr[1] || ('#' + pr[0])) + '</a>';
            });
            box.innerHTML =
                (promoted ? '<div style="margin-bottom:4px;color:#2e7d32;"><b>Promoted</b> as ' + promoted + '</div>' : '') +
                '<div style="display:flex;gap:4px;align-items:center;">' +
                  '<span style="color:#666;flex:none;">Review</span>' +
                  '<select class="rv-cat" style="flex:1;min-width:0;font-size:11px;"><option value="">Unreviewed</option></select>' +
                  '<button type="button" class="rv-add" title="Add a review category" ' +
                  'style="flex:none;font-size:11px;padding:1px 6px;cursor:pointer;">+</button>' +
                '</div>' +
                '<div class="rv-new" style="display:none;margin-top:4px;gap:4px;">' +
                  '<input type="text" class="rv-new-label" maxlength="80" placeholder="New category" ' +
                  'style="flex:1;min-width:0;font-size:11px;">' +
                  '<button type="button" class="rv-new-ok" style="font-size:11px;cursor:pointer;">Add</button>' +
                '</div>' +
                '<input type="text" class="rv-note" maxlength="2000" placeholder="Note (optional)" ' +
                'style="display:block;width:100%;box-sizing:border-box;margin-top:4px;font-size:11px;">' +
                '<div class="rv-status" style="color:#999;font-size:10px;margin-top:2px;min-height:12px;"></div>';
            var sel = box.querySelector('.rv-cat'), note = box.querySelector('.rv-note'),
                st = box.querySelector('.rv-status'), addBtn = box.querySelector('.rv-add'),
                newRow = box.querySelector('.rv-new'), newLab = box.querySelector('.rv-new-label'),
                newOk = box.querySelector('.rv-new-ok');
            note.value = p._review_note || '';

            function stamp() {
                st.style.color = '#999';
                st.textContent = p._review ? ('by ' + (p._reviewed_by || '?') + ', ' +
                    String(p._reviewed_at || '').slice(0, 10)) : '';
                note.disabled = !sel.value;
                note.title = sel.value ? '' : 'Choose a category first';
            }
            function fill(cats) {
                var keep = sel.value || (p._review_id != null ? String(p._review_id) : '');
                sel.length = 1;
                cats.forEach(function (c) {
                    var o = document.createElement('option');
                    o.value = String(c.id); o.textContent = c.label;
                    sel.appendChild(o);
                });
                sel.value = keep;
                stamp();
            }
            function save() {
                st.style.color = '#999'; st.textContent = 'Saving…';
                var cid = sel.value ? parseInt(sel.value, 10) : null;
                _revPost(url, { category_id: cid, note: cid ? note.value : '' })
                    .then(function (d) {
                        var rev = d.review || {};
                        _revStore(s, L, extId, rev);
                        // The Inventory-tab explorer (ext_explore.js) keeps its own copy of
                        // who is reviewed as what, for its Review filter.
                        window.dispatchEvent(new CustomEvent('ls:ext-review', { detail:
                            { sid: s.id, layer: L.layer, ext_id: String(extId), review: rev } }));
                        ['_review_id', '_review', '_review_note', '_reviewed_by', '_reviewed_at'].forEach(function (f) {
                            if (rev[f] === undefined) delete p[f]; else p[f] = rev[f];
                        });
                        if (!cid) note.value = '';
                        stamp();
                        st.textContent = (cid ? 'Saved' : 'Set back to unreviewed') + (st.textContent ? ' · ' + st.textContent : '');
                        _revFetchCats(true).then(fill).catch(function () {});
                    })
                    .catch(function (e) { st.style.color = '#c0392b'; st.textContent = 'Not saved: ' + e.message; });
            }
            function addCategory() {
                var label = newLab.value.replace(/\s+/g, ' ').trim();
                if (!label) { newLab.focus(); return; }
                newOk.disabled = true;
                _revPost(API_BASE + 'api/external/review/categories/', { label: label })
                    .then(function (d) {
                        _revCats = d.categories || _revCats;
                        newLab.value = ''; newRow.style.display = 'none';
                        sel.value = '';
                        fill(_revCats);
                        sel.value = String(d.id);
                        save();          // adding a category from a record files that record under it
                    })
                    .catch(function (e) { st.style.color = '#c0392b'; st.textContent = 'Category not added: ' + e.message; })
                    .then(function () { newOk.disabled = false; });
            }

            sel.addEventListener('change', save);
            note.addEventListener('change', function () { if (sel.value) save(); });
            note.addEventListener('keydown', function (e) { if (e.key === 'Enter') note.blur(); });
            addBtn.addEventListener('click', function () {
                var open = newRow.style.display === 'none';
                newRow.style.display = open ? 'flex' : 'none';
                if (open) newLab.focus();
            });
            newOk.addEventListener('click', addCategory);
            newLab.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') { e.preventDefault(); addCategory(); }
                // Escape here closes the little row, not the popup or a tool.
                if (e.key === 'Escape') { e.stopPropagation(); newRow.style.display = 'none'; }
            });

            // The record's own category is shown at once (it rides on the feature),
            // before the list arrives, so a slow list never shows "Unreviewed".
            if (p._review_id != null) {
                var o0 = document.createElement('option');
                o0.value = String(p._review_id); o0.textContent = p._review;
                sel.appendChild(o0); sel.value = o0.value;
            }
            stamp();
            _revFetchCats().then(fill).catch(function (e) {
                st.style.color = '#c0392b';
                st.textContent = 'Review unavailable: ' + e.message;
                sel.disabled = true; addBtn.disabled = true; note.disabled = true;
            });
        }

        // Link: the same provenance as a promotion, onto a record of ours that
        // already exists (Hig, 2026-11-24: promoting a throwaway record to delete
        // it and copy its text over by hand lost the stamp). Suggests our nearest
        // records, nearest first, or takes an id; two clicks to link, no dialog.
        // The server (api_external_link) writes the stamp and merges the
        // provenance sentence into ongoing_work; noted_by is only filled if blank.
        function _extLinkUI(box, s, L, p, rbox) {
            if (!box) return;
            var extId = p.ext_id;
            if (extId === undefined || extId === null) return;
            if (box.style.display !== 'none') { box.style.display = 'none'; return; }
            box.style.display = 'block';
            box.style.cssText += ';font-size:11px;background:#f6f6f2;border:1px solid #e4e4dc;' +
                                 'border-radius:4px;padding:5px 6px;';
            var base = API_BASE + 'api/external/' + s.id + '/' + L.layer + '/';
            box.innerHTML = '<div style="color:#666;margin-bottom:3px;">Link one of our records to this one:</div>' +
                '<div class="lk-list" style="color:#999;">Finding our nearest records…</div>' +
                '<div style="display:flex;gap:4px;margin-top:4px;">' +
                  '<input type="text" class="lk-id" inputmode="numeric" placeholder="or record id" ' +
                  'style="flex:1;min-width:0;font-size:11px;">' +
                  '<button type="button" class="lk-id-go" style="font-size:11px;cursor:pointer;">Link</button>' +
                '</div>' +
                '<div class="lk-status" style="font-size:10px;margin-top:3px;min-height:12px;color:#999;"></div>';
            var list = box.querySelector('.lk-list'), st = box.querySelector('.lk-status'),
                idIn = box.querySelector('.lk-id'), idGo = box.querySelector('.lk-id-go');
            var armed = null;   // the button waiting for its confirming second click

            function link(lid, btn, label) {
                if (armed !== btn) {
                    if (armed) armed.textContent = armed._label;
                    armed = btn; btn._label = btn.textContent;
                    btn.textContent = 'Confirm';
                    st.style.color = '#999';
                    st.textContent = 'Click Confirm to stamp ' + label + ' as ' + s.short + ' ' + extId +
                                     ' and add the provenance sentence to its ongoing work.';
                    return;
                }
                armed = null; btn.disabled = true;
                st.style.color = '#999'; st.textContent = 'Linking…';
                _revPost(base + 'link/' + encodeURIComponent(extId) + '/', { landslide_id: lid })
                    .then(function (d) {
                        var a = d.applied || {}, pv = a.previous || {};
                        btn.textContent = 'Linked';
                        st.style.color = '#2e7d32';
                        st.innerHTML = 'Linked <a href="/inventory/manage/' + lid + '/">' +
                            esc(a.name || ('#' + lid)) + '</a>' +
                            (pv.external_id && (pv.external_source !== a.external_source || pv.external_id !== a.external_id)
                                ? ' (was ' + esc(pv.external_source + ' ' + pv.external_id) + ')' : '') +
                            '. Ongoing work: ' + esc(a.ongoing_work || '');
                        p._promoted = (p._promoted || []).filter(function (x) { return x[0] !== lid; })
                                                          .concat([[lid, a.name]]);
                        if (rbox) _extReviewUI(rbox, s, L, p);
                    })
                    .catch(function (e) {
                        btn.disabled = false; btn.textContent = btn._label || 'Link';
                        st.style.color = '#c0392b'; st.textContent = 'Not linked: ' + e.message;
                    });
            }

            fetch(base + 'nearby/' + encodeURIComponent(extId) + '/', { credentials: 'same-origin' })
                .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || r.status); return d; }); })
                .then(function (d) {
                    var recs = d.records || [];
                    if (!recs.length) { list.textContent = 'None of our records nearby.'; return; }
                    list.innerHTML = '';
                    list.style.color = '';
                    recs.forEach(function (r) {
                        var km = r.distance_m >= 1000 ? (r.distance_m / 1000).toFixed(1) + ' km' : r.distance_m + ' m';
                        var tag = r.external_id ? ' · <span style="color:#a0522d;" title="Already linked">' +
                                  (r.external_source === s.id && r.external_id === String(extId)
                                      ? 'linked to this' : 'linked to ' + esc(r.external_source + ' ' + r.external_id)) + '</span>' : '';
                        var row = document.createElement('div');
                        row.style.cssText = 'display:flex;gap:4px;align-items:center;margin:2px 0;';
                        row.innerHTML = '<span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;">' +
                            '<a href="/inventory/manage/' + r.id + '/">' + esc(r.name || ('#' + r.id)) + '</a>' +
                            ' <span style="color:#999;">#' + r.id + ' · ' + km + (r.pending ? ' · pending' : '') + '</span>' +
                            tag + '</span>' +
                            '<button type="button" style="flex:none;font-size:11px;cursor:pointer;">Link</button>';
                        var b = row.querySelector('button');
                        b.addEventListener('click', function () { link(r.id, b, r.name || ('#' + r.id)); });
                        list.appendChild(row);
                    });
                })
                .catch(function (e) { list.style.color = '#c0392b'; list.textContent = 'Nearby records unavailable: ' + e.message; });

            function byId() {
                var lid = parseInt(String(idIn.value).replace(/^#/, ''), 10);
                if (!lid) { idIn.focus(); return; }
                link(lid, idGo, '#' + lid);
            }
            idGo.addEventListener('click', byId);
            idIn.addEventListener('input', function () {
                if (armed === idGo) { idGo.textContent = 'Link'; armed = null; st.textContent = ''; }
            });
            idIn.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') { e.preventDefault(); byId(); }
                if (e.key === 'Escape') { e.stopPropagation(); box.style.display = 'none'; }
            });
        }

        // Click: show whatever fields this record actually carries, in the
        // publisher's own words and their own column order. No curation beyond
        // dropping empties -- deciding which of someone else's fields matter is
        // the kind of judgement this whole design exists to postpone.
        // Display only: a shapefile's doubles arrive as 12.801463569999999.
        // Round what is shown; the mirror and the record endpoint keep it all.
        function _extShowVal(v) {
            if (typeof v !== 'number' || Number.isInteger(v)) return String(v);
            var a = Math.abs(v);
            return String(a >= 1000 ? Math.round(v) : a >= 1 ? Number(v.toFixed(2)) : Number(v.toPrecision(3)));
        }

        function _extPopup(e, s, L) {
            var p = e.features[0].properties || {};
            var rows = '';
            (L.field_order || Object.keys(p)).forEach(function (c) {
                var v = p[c];
                if (v === undefined || v === null || v === '') return;
                if (c === 'id' || c === 'fetched_at' || c.charAt(0) === '_') return;
                rows += '<div style="margin:1px 0;display:flex;gap:6px;">' +
                        '<span style="color:#888;flex:none;">' + esc(L.fields[c] || c) + ':</span>' +
                        '<span>' + esc(_extShowVal(v)) + '</span></div>';
            });
            // Only the FIELD LIST scrolls. The heading says whose data this is and
            // the footer carries the citation both licences require, so neither
            // may scroll out of sight -- and a records-long panel that opened
            // somewhere in its own middle was doing exactly that.
            var html = '<div style="font:12px/1.4 system-ui,sans-serif;max-width:300px;">' +
                '<div style="font-weight:600;color:' + s.colour + ';margin-bottom:3px;">' +
                    esc(s.short) + ' · ' + esc(L.label) + '</div>' +
                (window._isInventoryEditor ? '<div class="ext-review"></div>' : '') +
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
                      '<button type="button" class="ext-link" style="font-size:11px;margin-top:6px;' +
                      'padding:4px 9px;cursor:pointer;">⇄ Link to an existing record…</button>' +
                      '<div class="ext-link-box" style="display:none;margin-top:5px;"></div>' +
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
            var rbox = pop.getElement().querySelector('.ext-review');
            if (rbox) _extReviewUI(rbox, s, L, p);
            var lbtn = pop.getElement().querySelector('.ext-link');
            if (lbtn) lbtn.addEventListener('click', function () {
                _extLinkUI(pop.getElement().querySelector('.ext-link-box'), s, L, p, rbox);
            });
            var pbtn = pop.getElement().querySelector('.ext-promote');
            if (pbtn) pbtn.addEventListener('click', function () {
                // The explorer hands the Inventory tab back to our own records
                // before the outline is drawn (Hig, 2026-11-16).
                window.dispatchEvent(new CustomEvent('ls:ext-promote', { detail: { sid: s.id, layer: L.layer } }));
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

        // Open the same popup for a record found some other way (the explorer
        // fetches the full record by id and calls this).
        function _extShowRecord(lngLat, s, L, p) {
            _extPopup({ features: [{ properties: p }], lngLat: lngLat }, s, L);
        }

        return { _extShowRecord: _extShowRecord, _extSpec: _extSpec, _extReplayLayers: _extReplayLayers, _extEnsureRegistry: _extEnsureRegistry, _extApplyOrDefer: _extApplyOrDefer, _extWireClicks: _extWireClicks };
    } };
})();
