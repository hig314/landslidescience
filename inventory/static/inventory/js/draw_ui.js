/* draw_ui.js — Editor drawing on the map page: New button, draw control and session panel, provisional and pending layers, pinned labels, promotion of a mirrored record.
 *
 * Moved out of map.js on 2026-11-14 without changing its text. map.js calls
 * window.LSDrawUI.init(ctx) once, at the point in its start-up where this code
 * used to sit, and passes in the few things it needs from the map page:
 *   API_BASE, writeHashState, map, _getFeaturesData, _pendingLayerDefs, _swipe, _swipeSetFeatures, _swipeAlso, _swipeSetPending, _scarpsOn, _scarpsSet, buildFilter, esc, _scarpContext, _scarpOpenPanel, _scarpClosePanel.
 * It returns what map.js still uses: _csrf, _provLayerDefs, ensureProvLayers, refreshProvData, _swipeDrawSync, _getPendingData, _getPinField, _ensurePinLabelOn, ensurePinLabel, _patchFeatureProp, _PIN_LABELS, _drawCtrl, _promoteStart.
 *
 * The drawing engine is terra_draw.js. The three _scarp* functions come from
 * scarp_ui.js, which starts later, so they are passed as wrappers that look
 * the function up when called. The promotion code used to sit further down
 * map.js; it was moved up beside the draw tool before the section left.
 */
(function () {
    'use strict';

    window.LSDrawUI = { init: function (ctx) {
        var API_BASE = ctx.API_BASE;
        var writeHashState = ctx.writeHashState;
        var map = ctx.map;
        var _getFeaturesData = ctx._getFeaturesData;
        var _pendingLayerDefs = ctx._pendingLayerDefs;
        var _swipe = ctx._swipe;
        var _swipeSetFeatures = ctx._swipeSetFeatures;
        var _swipeAlso = ctx._swipeAlso;
        var _swipeSetPending = ctx._swipeSetPending;
        var _scarpsOn = ctx._scarpsOn;
        var _scarpsSet = ctx._scarpsSet;
        var buildFilter = ctx.buildFilter;
        var esc = ctx.esc;
        var _scarpContext = ctx._scarpContext;
        var _scarpOpenPanel = ctx._scarpOpenPanel;
        var _scarpClosePanel = ctx._scarpClosePanel;

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
        // Defs shared with the swipe map's mirror (see _swipeAddData) so staged
        // components read identically across the wiper divider.
        function _provLayerDefs() {
            return [
                // Teal = UNCOMMITTED (staged, still yours to name/commit).
                // Magenta #d6219e = committed but incomplete (pending review).
                // Two different states, deliberately two different colours —
                // don't unify them. Dashed outline = not yet in the inventory.
                { id: 'prov-fill', type: 'fill', source: 'prov-src',
                  paint: { 'fill-color': '#00b3a4', 'fill-opacity': 0.25 } },
                { id: 'prov-line', type: 'line', source: 'prov-src',
                  paint: { 'line-color': '#00897b', 'line-width': 2, 'line-dasharray': [2, 1.5] } },
            ];
        }
        function ensureProvLayers() {
            if (!map.getSource('prov-src')) {
                map.addSource('prov-src', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
            }
            var bId = map.getLayer('measure-fill') ? 'measure-fill' : undefined;
            _provLayerDefs().forEach(function (def) {
                if (!map.getLayer(def.id)) map.addLayer(def, bId);
                // Re-seat on every call: this runs on style.load BEFORE
                // initDataLayers, so the first add lands at the bottom of the
                // stack and every raster inserted "below the data" (lidar, trace
                // imagery: _rasterBeforeId) then covers the staged polygons. The
                // staged work is the thing being drawn; it sits on top of all of
                // it (Hig, 2026-09-26). initDataLayers calls back here for that.
                map.moveLayer(def.id, bId);
            });
            // (Name labels intentionally omitted — symbol layers depend on a glyph
            // server that varies by basemap and 404s; the queue panel shows names.)
        }
        function refreshProvData() {
            ensureProvLayers();   // guarantee the source + layers exist first
            var src = map.getSource('prov-src');
            if (!src) { console.warn('refreshProvData: prov-src missing'); return; }
            // Staged components are ALWAYS drawn for their editor, not only while
            // the draw tool happens to be open. They are real pending work that
            // lives server-side and survives sessions — hiding them made a
            // detached polygon (or anything staged last week) silently invisible
            // until you thought to click Draw, and left the map disagreeing with
            // itself depending on how you arrived. They are visually distinct
            // (dashed amber) and only ever visible to the editor who staged them.
            var feats = _prov.map(function (c) {
                return { type: 'Feature', geometry: c.geometry,
                         properties: { label: c.unique_name + ' (' + c.role + ')' } };
            });
            var fc = { type: 'FeatureCollection', features: feats };
            src.setData(fc);
            // Mirror to the wiper's comparison map — the swipe container covers
            // the right pane, so main-map-only staged polygons vanish under it.
            _swipeAlso(function (m) {
                var s = m.getSource('prov-src');
                if (s) s.setData(fc);
            });
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
            _swipeDrawSync();   // clear the wiper-pane draft mirror
        }
        function onTDChange(ids, type) {
            // A non-empty snapshot means a polygon is mid-draw → lock the basemap.
            map.__drawPolyOpen = !!(_td && _td.getSnapshot().length);
            _swipeDrawSync();
        }

        // Mirror the in-progress Terra Draw draft onto the wiper's comparison map.
        // The swipe container sits ABOVE the main map (clipped to the right pane),
        // so TD's own layers — main-map only — vanish under it, even though clicks
        // pass through (the container is pointer-events:none) and drawing over the
        // right pane WORKS. This restores the visuals: full-snapshot setData per
        // change event (drafts are a handful of features — cheap). Styling matches
        // Terra Draw 1.0's polygon-mode defaults.
        function _swipeDrawSync() {
            var cmap = _swipe.map;
            if (!cmap || !cmap.__lsStyleReady) return;
            var feats = (map.__drawActive && _td) ? _td.getSnapshot() : [];
            if (!cmap.getSource('td-mirror-src')) {
                if (!feats.length) return;   // nothing to show → nothing to build
                cmap.addSource('td-mirror-src',
                    { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
                cmap.addLayer({ id: 'td-mirror-fill', type: 'fill', source: 'td-mirror-src',
                    filter: ['==', ['geometry-type'], 'Polygon'],
                    paint: { 'fill-color': '#3f97e0', 'fill-opacity': 0.3 } });
                cmap.addLayer({ id: 'td-mirror-line', type: 'line', source: 'td-mirror-src',
                    filter: ['==', ['geometry-type'], 'Polygon'],
                    paint: { 'line-color': '#3f97e0', 'line-width': 2 } });
                cmap.addLayer({ id: 'td-mirror-pt', type: 'circle', source: 'td-mirror-src',
                    filter: ['==', ['geometry-type'], 'Point'],
                    paint: { 'circle-radius': 4, 'circle-color': '#ffffff',
                             'circle-stroke-color': '#3f97e0', 'circle-stroke-width': 2 } });
            }
            cmap.getSource('td-mirror-src').setData({ type: 'FeatureCollection', features: feats });
        }
        function onTDFinish(id) {
            openNamePopup(id);
        }

        // `given`: a geometry to stage instead of a Terra Draw feature -- the
        // outline of the outside record being promoted (see _promoteStart).
        function openNamePopup(fid, given) {
            var feat = given ? { geometry: given }
                : _td && _td.getSnapshot().filter(function (f) { return f.id === fid; })[0];
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
            // Promotion seeds the name from their record, as a starting point and
            // not an answer -- their databases are not named to the standard at
            // /inventory/naming/, so the editor is expected to edit this.
            if (_promote) {
                if (_promote.name) nameEl.value = _promote.name;
                warnEl.style.color = '#666';
                warnEl.textContent = 'From ' + _promote.short + ' · their id ' +
                                     (_promote.extId || '—') + '. Rename to suit the standard.' +
                                     (given ? ' Their outline is staged as drawn; refine it after ' +
                                      'committing with Edit outline, or Cancel to trace your own.' : '');
            }
            nameEl.focus();
            nameEl.select();
            // Enter anywhere in the popup is Add (Hig, 2026-09-26): the hands are
            // already on the keyboard from finishing the polygon. Stopped at the
            // overlay so neither Terra Draw's Enter-to-finish nor the tool
            // Escape/Enter policy on document sees a keystroke meant for the form.
            ov.addEventListener('keydown', function (e) {
                if (e.key !== 'Enter' || e.target.tagName === 'BUTTON') return;
                e.preventDefault(); e.stopPropagation();
                ov.querySelector('#idp-ok').click();
            });
            function close() { if (ov.parentNode) ov.parentNode.removeChild(ov); }
            function discard() {
                if (_td && !given) { _td.removeFeatures([fid]); _td.setMode('polygon'); }
                map.__drawPolyOpen = false; close();
            }
            ov.querySelector('#idp-cancel').addEventListener('click', discard);
            ov.querySelector('#idp-ok').addEventListener('click', function () {
                var nm = (nameEl.value || '').trim(), role = roleEl.value;
                if (!nm) { warnEl.textContent = 'Enter a name.'; return; }
                _drawPost('stage/', { unique_name: nm, role: role, geometry: feat.geometry }).then(function (res) {
                    if (res.ok && res.j.ok) {
                        _prov.push(res.j.component);
                        refreshProvData(); renderQueue();
                        if (_td && !given) { _td.removeFeatures([fid]); _td.setMode('polygon'); }
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
                // Rename the whole group — the recovery path when the preview flags
                // a name collision (or the editor just mistyped). Renaming onto
                // another staged name merges the groups (same name = same landslide).
                var ren = document.createElement('button'); ren.type = 'button'; ren.className = 'idq-del';
                ren.textContent = '✎ rename'; ren.title = 'Rename this landslide (all its components)';
                ren.addEventListener('click', function () {
                    var nm2 = window.prompt('New name for "' + nm + '":', nm);
                    if (nm2 == null) return;
                    nm2 = nm2.trim();
                    if (!nm2 || nm2 === nm) return;
                    _drawPost('rename/', { from_name: nm, to_name: nm2 }).then(function (res) {
                        if (res.ok && res.j.ok) {
                            _prov.forEach(function (p) { if (p.unique_name === nm) p.unique_name = nm2; });
                            refreshProvData(); renderQueue();
                        } else {
                            window.__drawFlash((res.j && res.j.error) || 'Rename failed.');
                        }
                    }).catch(function (e) {
                        if (e && e.authExpired) _drawAuthFlash();
                        else window.__drawFlash('Rename failed: ' + (e && e.message ? e.message : e));
                    });
                });
                row.appendChild(ren);
                var warn = document.createElement('div'); warn.className = 'idq-warn'; warn.dataset.name = nm;
                row.appendChild(warn);
                q.appendChild(row);
            });
            // Server-side warnings + commit-block.
            var commitBtn = _drawPanel.querySelector('#idq-commit');
            if (!names.length) { if (commitBtn) commitBtn.disabled = true; return; }
            fetch(DRAW_BASE + 'preview/').then(function (r) { return r.json(); }).then(function (pv) {
                if (!pv || !pv.ok) return;
                // Names that would be ABSORBED by an existing landslide. Drop
                // confirmations for names no longer staged/attaching, so a rename
                // correctly re-arms the gate.
                var stillAttaching = {};
                (pv.groups || []).forEach(function (g) {
                    var el = _drawPanel.querySelector('.idq-warn[data-name="' + (CSS && CSS.escape ? CSS.escape(g.unique_name) : g.unique_name) + '"]');
                    if (!el) return;
                    if (g.warnings && g.warnings.length) {
                        // "→ adds to existing" is informational, not a warning.
                        el.textContent = g.warnings.map(function (w) {
                            return w.charAt(0) === '→' ? w : '⚠ ' + w;
                        }).join('  ·  ');
                    } else {
                        el.textContent = '';
                    }
                    if (!g.attach_to) return;
                    stillAttaching[g.unique_name] = true;
                    // Merging into an existing landslide is a legitimate move (a
                    // source drawn for a committed deposit) AND what an accidental
                    // name reuse looks like. Make it a deliberate, unchecked
                    // opt-in right where the name was typed — the alternative is
                    // the ✎ rename beside it.
                    var lab = document.createElement('label');
                    lab.className = 'idq-attach';
                    lab.style.cssText = 'display:block;margin-top:3px;font-size:11px;' +
                        'color:#7a4b00;background:#fff6e5;border:1px solid #f0d9a8;' +
                        'border-radius:3px;padding:3px 5px;cursor:pointer;';
                    var cb = document.createElement('input');
                    cb.type = 'checkbox';
                    cb.checked = !!_attachOk[g.unique_name];
                    cb.style.cssText = 'margin-right:5px;vertical-align:middle;';
                    cb.addEventListener('change', function () {
                        if (cb.checked) _attachOk[g.unique_name] = true;
                        else delete _attachOk[g.unique_name];
                        updateCommitGate(pv);
                    });
                    lab.appendChild(cb);
                    lab.appendChild(document.createTextNode(
                        'Add to existing landslide #' + g.attach_to +
                        (g.landslide_type ? ' (' + g.landslide_type + ')' : '') +
                        (g.attach_distance_km ? ', ' + g.attach_distance_km + ' km away' : '') +
                        ' — otherwise rename ✎'));
                    el.appendChild(lab);
                });
                Object.keys(_attachOk).forEach(function (n) {
                    if (!stillAttaching[n]) delete _attachOk[n];
                });
                _attachPending = Object.keys(stillAttaching);
                updateCommitGate(pv);
            }).catch(function () {});
        }

        // Names that would be absorbed by an existing landslide, and the ones the
        // editor has explicitly opted in for (checkbox in the queue row).
        var _attachPending = [], _attachOk = {};

        // Commit stays disabled while any attach is unconfirmed, so the merge is
        // never something that just happens on the way past.
        function updateCommitGate(pv) {
            var btn = _drawPanel && _drawPanel.querySelector('#idq-commit');
            if (!btn) return;
            var unconfirmed = _attachPending.filter(function (n) { return !_attachOk[n]; });
            btn.disabled = !!(pv && pv.has_block) || unconfirmed.length > 0;
            btn.title = unconfirmed.length
                ? 'Tick the "add to existing landslide" box, or rename ✎, for: ' + unconfirmed.join(', ')
                : '';
        }

        function doCommit() {
            window.__drawFlash('Committing…');
            // The server enforces the same opt-in independently (409 with
            // needs_confirm) — the checkbox is the affordance, not the guard.
            _drawPost('commit/', { confirm_attach: Object.keys(_attachOk) })
                .then(function (res) {
                    if (res.ok && res.j.ok) {
                        if (_promote) {
                            _promoteStamp(res.j.redirect).then(function () {
                                window.location.href = res.j.redirect;
                            });
                        } else {
                            window.location.href = res.j.redirect;
                        }
                        return;
                    }
                    if (res.j && res.j.needs_confirm === 'attach') {
                        window.__drawFlash('Tick the "add to existing landslide" box, or rename ✎, first.');
                        renderQueue();
                        return;
                    }
                    window.__drawFlash((res.j && res.j.error) || 'Commit failed.');
                    renderQueue();
                }).catch(function (e) {
                    if (e && e.authExpired) _drawAuthFlash();
                    else window.__drawFlash('Commit failed: ' + (e && e.message ? e.message : e));
                });
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
                doCommit();
            });
            p.querySelector('#idq-discard').addEventListener('click', function () {
                if (!window.confirm('Discard all staged components?')) return;
                _drawPost('delete/', { all: true }).then(function () { _prov = []; refreshProvData(); renderQueue(); })
                    .catch(function (e) { if (e && e.authExpired) _drawAuthFlash(); });
            });
            p.querySelector('#idq-done').addEventListener('click', function () {
                // Exiting with staged work is fine (it's saved server-side), but say
                // so — a silently vanishing polygon reads as data loss or a ghost.
                if (_prov.length &&
                    !window.confirm(_prov.length + ' staged component(s) are saved and will '
                        + 'reappear when you reopen ✏ draw. (Use "Discard all" to delete '
                        + 'them.)\n\nExit the draw tool?')) return;
                _drawCtrl.deactivate();
            });
        }
        function closePanel() { if (_drawPanel) _drawPanel.style.display = 'none'; }

        function DrawModeControl() {}
        DrawModeControl.prototype.onAdd = function (mapArg) {
            var self = this; this._map = mapArg; this._on = false;
            // Joins the shared tool group. `tool-editor` keeps the brown cue the
            // editor-only controls use without putting this button outside the
            // set of things that compete for a map click.
            this._btn = LSTools.mode({
                id: 'draw', order: 30, label: '✏',
                className: 'tool-editor',
                title: 'Draw: landslide polygons or scarp traces',
                aria: 'Draw a new landslide or trace a scarp',
                // The pencil asks WHAT to draw rather than assuming polygons: the
                // scarp tracer used to live under Analysis as its own tool, which
                // let both editors' Terra Draw sessions be open at once. One mode,
                // two kinds, so the tool group's mutual exclusion covers both.
                onSelect: function () { self.choose(); },
                onRelease: function () { self.deactivate(); },
                // Escape is Terra Draw's here (keyEvents.cancel discards the ring),
                // and map.__drawPolyOpen updates synchronously inside its change
                // event -- so a second listener racing it on the same keypress is
                // exactly the bug the router exists to remove. No-op on purpose;
                // exiting the tool stays on the button.
                cancel: function () {},
                // The one switch that should ask rather than just happen: staged
                // components are held server-side and come back, but an unclosed
                // ring only exists inside Terra Draw and dies with it.
                canRelease: function () {
                    if (!map.__drawPolyOpen) return true;
                    return window.confirm('A polygon is still open and will be discarded.'
                                          + '\n\nSwitch tools anyway?');
                }
            });
            var el = document.createElement('div');
            el.style.display = 'none';
            // Provisional layers + staged data survive basemap switches (re-added on
            // every style.load, MeasureControl pattern).
            map.on('style.load', refreshProvData);
            if (map.isStyleLoaded()) refreshProvData();
            return el;
        };
        DrawModeControl.prototype.onRemove = function () {};
        DrawModeControl.prototype.toggle = function () { this._on ? this.deactivate() : this.choose(); };

        // A two-item menu beside the pencil. Click away or Escape closes it;
        // clicking the pencil again while it is open closes it too.
        DrawModeControl.prototype.choose = function () {
            // Menu icons as inline SVG, not Unicode shapes: U+25B0 (parallelogram) and
            // U+2571 (diagonal) are missing from some system fonts and showed as '?'
            // on production (Hig, 2026-09-29). Colours match the layers they draw.
            var _DRAW_ICON_POLY = '<svg width="14" height="12" viewBox="0 0 14 12" aria-hidden="true" ' +
                'style="vertical-align:-1px"><polygon points="1,11 4,1 13,3 11,10" fill="#00b3a4" fill-opacity=".45" ' +
                'stroke="#00897b" stroke-width="1.4" stroke-linejoin="round"/></svg>';
            var _DRAW_ICON_SCARP = '<svg width="14" height="12" viewBox="0 0 14 12" aria-hidden="true" ' +
                'style="vertical-align:-1px"><path d="M1 10 L6 5 L13 2" fill="none" stroke="#7b3fa0" ' +
                'stroke-width="1.8" stroke-dasharray="3 2" stroke-linecap="round"/></svg>';
            var self = this;
            if (this._menu) { this._closeMenu(); return; }
            var m = document.createElement('div');
            m.className = 'inv-draw-choice';
            m.setAttribute('role', 'menu');
            m.innerHTML =
                '<div class="inv-draw-choice-hd">Draw</div>' +
                '<button type="button" data-kind="poly">' + _DRAW_ICON_POLY + ' Landslide polygons</button>' +
                (typeof LSScarps !== 'undefined'
                    ? '<button type="button" data-kind="scarp">' + _DRAW_ICON_SCARP + ' Scarp traces</button>' : '');
            var r = this._btn.getBoundingClientRect();
            m.style.left = Math.round(r.right + 8) + 'px';
            m.style.top = Math.round(r.top - 4) + 'px';
            document.body.appendChild(m);
            m.addEventListener('click', function (e) {
                var b = e.target.closest('button[data-kind]');
                if (!b) return;
                var kind = b.dataset.kind;
                self._closeMenu();
                self.activate(kind);
            });
            this._menu = m;
            this._menuAway = function (e) {
                if (m.contains(e.target) || self._btn.contains(e.target)) return;
                self._closeMenu();
            };
            this._menuKey = function (e) { if (e.key === 'Escape') self._closeMenu(); };
            // Next tick, or the click that opened the menu closes it.
            setTimeout(function () {
                if (self._menu !== m) return;
                document.addEventListener('mousedown', self._menuAway);
                document.addEventListener('keydown', self._menuKey);
            }, 0);
        };
        DrawModeControl.prototype._closeMenu = function () {
            if (!this._menu) return;
            this._menu.remove(); this._menu = null;
            document.removeEventListener('mousedown', this._menuAway);
            document.removeEventListener('keydown', this._menuKey);
        };

        // kind: 'poly' (new landslide, the staged-components panel) or 'scarp'
        // (fault-scarp tracing through LSScarps). Both are the one LSTools mode
        // 'draw', so measure/InSAR/the other kind are released on the way in.
        DrawModeControl.prototype.activate = function (kind) {
            kind = kind === 'scarp' ? 'scarp' : 'poly';
            if (typeof terraDraw === 'undefined' || typeof terraDrawMaplibreGlAdapter === 'undefined') {
                alert('Drawing library failed to load — check your connection and reload.');
                return;
            }
            if (kind === 'scarp' && typeof LSScarps === 'undefined') {
                alert('Scarp tracing is not available on this page.');
                return;
            }
            if (this._on && this._kind === kind) return;
            if (this._on) {
                // Switching kinds inside the mode: same question canRelease asks.
                if (this._kind === 'poly' && map.__drawPolyOpen &&
                    !window.confirm('A polygon is still open and will be discarded.'
                                    + '\n\nSwitch to scarp traces anyway?')) return;
                this._teardown();
            } else if (!LSTools.claim('draw')) {
                // Takes the map from measure/InSAR if either has it; false only if
                // the outgoing tool objected.
                return;
            }
            map.__drawActive = true; this._on = true; this._kind = kind;
            LSTools.cursor.set('tool', 'crosshair');
            var self = this;
            // Open the panel first so any Terra Draw init error is visible (and the
            // catch below fully deactivates, so a failure never locks the map up).
            try {
                if (kind === 'poly') {
                    ensureProvLayers();
                    openPanel();
                    startTD();
                    loadProvisional();
                    // Basemap switch (setStyle) wipes Terra Draw's layers — rebuild it on
                    // the new style so drawing keeps working. (Switching is blocked while
                    // a polygon is open, so there's no in-progress ring to lose here.)
                    this._styleReload = function () {
                        if (self._on && self._kind === 'poly') { stopTD(); startTD(); }
                    };
                } else {
                    _scarpOpenPanel();
                    if (!_scarpsOn()) { _scarpsSet(true); writeHashState(); }
                    LSScarps.startDraw({ terraDraw: window.terraDraw,
                                         adapter: window.terraDrawMaplibreGlAdapter,
                                         kind: 'scarp', context: _scarpContext });
                    this._styleReload = function () {
                        if (self._on && self._kind === 'scarp') {
                            LSScarps.stopDraw();
                            LSScarps.startDraw({ terraDraw: window.terraDraw,
                                                 adapter: window.terraDrawMaplibreGlAdapter });
                        }
                    };
                }
                map.on('style.load', this._styleReload);
            } catch (e) {
                console.error('draw activate failed:', e);
                this.deactivate();
                alert('Could not start the draw tool: ' + (e && e.message ? e.message : e));
            }
        };
        // Undo whatever the current kind set up, without touching the tool group.
        DrawModeControl.prototype._teardown = function () {
            LSTools.cursor.clear('tool');
            if (this._styleReload) { map.off('style.load', this._styleReload); this._styleReload = null; }
            if (this._kind === 'scarp') {
                if (typeof LSScarps !== 'undefined') { LSScarps.stopDraw(); LSScarps.select(null); }
                _scarpClosePanel();
            } else {
                stopTD(); closePanel();
                refreshProvData();   // hide staged polygons (kept server-side) — no ghosts
            }
            map.__drawActive = false; this._on = false; this._kind = null;
        };
        DrawModeControl.prototype.deactivate = function () {
            this._closeMenu();
            this._teardown();
            LSTools.release('draw');
        };
        DrawModeControl.prototype.kind = function () { return this._on ? this._kind : null; };

        // --- Editor-only: provisional (pending) landslides, shown in magenta. ---
        // Pending records are hidden from the public map; editors see them so they
        // can tell what they've already added while mapping a series. Click one to
        // open its review form. Survives basemap switches (style.load), like measure.
        var _pendingData = { points: { type: 'FeatureCollection', features: [] },
                             polygons: { type: 'FeatureCollection', features: [] } };
        function _getPendingData() { return _pendingData; }   // for the wiper's second map: reassigned on each load
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
        function _getPinField() { return _pinField; }   // for the detail panel: it changes with the picker
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
            var _featuresData = _getFeaturesData();
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
            // Staged (uncommitted) components belong on the map from the start,
            // not only once the draw tool is opened — otherwise work staged
            // earlier (or a just-detached polygon) is invisible until you happen
            // to click Draw. Same refresh-on-return treatment as pending records.
            loadProvisional();
            map.on('style.load', function () { ensureProvLayers(); refreshProvData(); });
            document.addEventListener('visibilitychange', function () {
                if (!document.hidden) { loadPending(); loadProvisional(); }
            });

            // Staged components are now always on the map, so they need to answer
            // "what is this?" when clicked — otherwise they're an inert magenta
            // blob. Identify it and point at the tool that manages it. Skipped
            // while draw/measure own the cursor.
            // Reference, above external mirrors and below faults and scarps:
            // staged work is ours, but it is not yet a record.
            LSTools.clicks.register({ id: 'staged', kind: 'reference', priority: 20,
                                      layers: ['prov-fill'], handler: function (f, e) {
                var label = (f.properties && f.properties.label) || 'staged component';
                LSTools.popup.show(new maplibregl.Popup({ closeButton: true })
                    .setLngLat(e.lngLat)
                    .setHTML('<div style="font-size:12px;line-height:1.45;">' +
                             '<b>Staged (not committed)</b><br>' +
                             String(label).replace(/[<>&]/g, '') +
                             '<br><span style="color:#666;">Open <b>✏ Draw</b> to name, ' +
                             'rename or commit it.</span></div>')
                    .addTo(map));
            } });
            map.on('mouseenter', 'prov-fill', function () {
                LSTools.cursor.set('hover', 'pointer');
            });
            map.on('mouseleave', 'prov-fill', function () {
                LSTools.cursor.clear('hover');
            });

            function _openPending(id) { if (id) window.location.href = '/inventory/manage/' + id + '/review/'; }
            // 'navigate' outranks everything: a click that leaves the page must
            // not also open a panel it will never show. Point over its own fill
            // is one record, so one entry.
            LSTools.clicks.register({
                id: 'pending', kind: 'navigate', priority: 40,
                layers: ['pending-pt', 'pending-poly-fill'],
                handler: function (f) {
                    var p = f.properties || {};
                    _openPending(f.layer && f.layer.id === 'pending-pt' ? p.id : p.landslide_id);
                }
            });
            ['pending-pt', 'pending-poly-fill'].forEach(function (lyr) {
                map.on('mouseenter', lyr, function () { LSTools.cursor.set('hover', 'pointer'); });
                map.on('mouseleave', lyr, function () { LSTools.cursor.clear('hover'); });
            });

            // Pin-field dropdown: restore the saved choice, relabel on change.
            var pinSel = document.getElementById('pin-field');
            if (pinSel) {
                if (_pinField) pinSel.value = _pinField;
                pinSel.addEventListener('change', function () { setPinField(pinSel.value); });
            }
        }

        // -----------------------------------------------------------------------
        // Promoting one mirrored record into this inventory.
        //
        // A promotion is NOT a copy. Their point says "something happened near
        // here" and carries their reading of what; our inventory wants an outline
        // and our own classification. So the flow is: their record supplies the
        // prompt and the provenance, the editor supplies the geometry and every
        // field, and the record lands in the review queue like any other new one.
        //
        // It deliberately reuses the existing draw tool rather than inventing a
        // second creation path. Trace, name, commit, review is the flow editors
        // already know, and everything hanging off it -- the name-collision block,
        // the 5 km attach guard, the rule cascade, the audit log -- applies here
        // for free. The only additions are a pre-filled name and a note of where
        // the idea came from.
        // -----------------------------------------------------------------------
        var _promote = null;   // {sid, short, layer, extId, name, colour, props}

        function _promoteSuggestName(s, L, p) {
            // Their name if they gave one. Their databases are not named to our
            // standard, so this is a starting point for the editor, never an
            // answer: the naming rules at /inventory/naming/ still apply and the
            // review form is where the name gets settled.
            var nameCol = null;
            Object.keys(L.fields || {}).forEach(function (c) {
                if (!nameCol && /^name$/i.test(L.fields[c])) nameCol = c;
            });
            var n = nameCol ? p[nameCol] : null;
            return (n && String(n).trim()) || '';
        }

        function _promoteIdOf(s, L, p) {
            return p.ext_id || p.points_id || p.ls_id || p.objectid || '';
        }

        function _promoteStart(s, L, p) {
            if (!window._isInventoryEditor) return;
            _promote = {
                sid: s.id, short: s.short, layer: L.layer, colour: s.colour,
                extId: String(_promoteIdOf(s, L, p) || ''),
                name: _promoteSuggestName(s, L, p),
                props: p, fields: L.fields || {}, order: L.field_order || [],
                layerGeom: L.geom
            };
            // Close any popup so the map is clear to trace on.
            var pops = document.querySelectorAll('.maplibregl-popup');
            for (var i = 0; i < pops.length; i++) pops[i].remove();
            if (_drawCtrl) _drawCtrl.activate('poly');
            _promoteBanner();
            // A source that publishes outlines: start from THEIR outline (Hig,
            // 2026-11-24: "default to using their outline"). Fetched whole from
            // the server -- the map's copy is tile-clipped and simplified -- and
            // offered straight to the name box; Cancel there leaves the tool
            // ready to trace by hand instead.
            if (/POLYGON/.test(L.geom || '') && _promote.extId) {
                var pr = _promote;
                fetch('/inventory/api/external/' + s.id + '/' + L.layer + '/record/' +
                      encodeURIComponent(pr.extId) + '/?geom=1', { credentials: 'same-origin' })
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (rec) {
                        if (_promote !== pr || !rec || !rec.geometry) return;
                        openNamePopup(null, rec.geometry);
                    })
                    .catch(function (e) { console.error('their outline did not load', e); });
            }
        }

        function _promoteCancel() {
            _promote = null;
            var b = document.getElementById('promote-banner');
            if (b && b.parentNode) b.parentNode.removeChild(b);
        }

        function _promoteBanner() {
            if (!_promote) return;
            var old = document.getElementById('promote-banner');
            if (old && old.parentNode) old.parentNode.removeChild(old);
            var b = document.createElement('div');
            b.id = 'promote-banner';
            b.style.cssText = 'position:absolute;top:10px;left:50%;transform:translateX(-50%);' +
                'z-index:12;background:#fff;border:1px solid ' + _promote.colour + ';' +
                'border-left:5px solid ' + _promote.colour + ';border-radius:4px;' +
                'padding:7px 11px;font:12px/1.4 system-ui,sans-serif;box-shadow:0 2px 8px rgba(0,0,0,.18);' +
                'max-width:440px;';
            b.innerHTML =
                '<div style="font-weight:600;">Promoting from ' + esc(_promote.short) + '</div>' +
                '<div style="color:#555;margin-top:2px;">' +
                  (_promote.name ? '“' + esc(_promote.name) + '” · ' : '') +
                  'their id ' + esc(_promote.extId || '—') + '</div>' +
                '<div style="color:#666;margin-top:4px;">' +
                  (/POLYGON/.test((_promote.layerGeom) || '') ? 'Their outline is offered first; Cancel it to trace your own. ' : '') +
                  'Trace the outline, press <b>Enter</b>, ' +
                  'then name it. Their fields travel with it for reference — none are copied.</div>' +
                '<div style="text-align:right;margin-top:5px;">' +
                  '<button id="promote-cancel" type="button" style="font-size:11px;">Cancel promotion</button></div>';
            map.getContainer().appendChild(b);
            var c = document.getElementById('promote-cancel');
            if (c) c.addEventListener('click', function () {
                _promoteCancel();
                if (_drawCtrl) _drawCtrl.deactivate();
            });
        }

        // After the draw tool commits, the new landslide exists and we are about
        // to land on its review form. Stamp the provenance first, through the same
        // per-field endpoint the form's own autosave uses -- so there is exactly
        // one way a column on `landslides` gets written from the browser.
        function _promoteStamp(redirect) {
            var m = /\/manage\/(?:review\/)?(\d+)/.exec(redirect || '');
            if (!m || !_promote) return Promise.resolve();
            var pr = _promote;
            // One server call, not a field at a time. The rules live in the
            // registry and one of them CLEARS a column -- the draw commit has
            // already stamped this editor into noted_by, and a promotion has to
            // undo that, because somebody else made the observation. Doing it
            // field by field from here would leave the editor's name on another
            // person's record whenever one call failed.
            return fetch('/inventory/api/external/' + pr.sid + '/' + pr.layer +
                         '/promote/' + encodeURIComponent(pr.extId) + '/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': window.CSRF_TOKEN },
                body: JSON.stringify({ landslide_id: parseInt(m[1], 10) })
            }).then(function (r) {
                // The server refuses an id its mirror does not hold (404); say so
                // rather than land on a form that silently lacks the provenance.
                if (!r.ok) return r.json().catch(function () { return {}; }).then(function (d) {
                    throw new Error(d.error || ('HTTP ' + r.status));
                });
            }).catch(function (e) {
                // A failed stamp must not lose the landslide the editor just drew:
                // the record is already committed and the redirect still happens.
                // Provenance can be set by hand on the form.
                console.error('promotion rules failed to apply', e);
            });
        }

        return { _csrf: _csrf, _provLayerDefs: _provLayerDefs, ensureProvLayers: ensureProvLayers, refreshProvData: refreshProvData, _swipeDrawSync: _swipeDrawSync, _getPendingData: _getPendingData, _getPinField: _getPinField, _ensurePinLabelOn: _ensurePinLabelOn, ensurePinLabel: ensurePinLabel, _patchFeatureProp: _patchFeatureProp, _PIN_LABELS: _PIN_LABELS, _drawCtrl: _drawCtrl, _promoteStart: _promoteStart };
    } };
})();
