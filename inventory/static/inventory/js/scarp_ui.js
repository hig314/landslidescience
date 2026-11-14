/* scarp_ui.js — Landform traces on the map page: the session panel, the click popup, loading the layer.
 *
 * Moved out of map.js on 2026-11-14 without changing its text. map.js calls
 * window.LSScarpUI.init(ctx) once, at the point in its start-up where this code
 * used to sit, and passes in the few things it needs from the map page:
 *   API_BASE, map, _csrf, _drawCtrl, _getCurrentBasemap, _lidarActive, _lidarActiveR, esc.
 * It returns what map.js still uses: _scarpContext, _scarpOpenPanel, _scarpClosePanel, _scarpInit.
 *
 * The tracing engine is LSScarps (scarps.js); this is the map page's side of it.
 */
(function () {
    'use strict';

    window.LSScarpUI = { init: function (ctx) {
        var API_BASE = ctx.API_BASE;
        var map = ctx.map;
        var _csrf = ctx._csrf;
        var _drawCtrl = ctx._drawCtrl;
        var _getCurrentBasemap = ctx._getCurrentBasemap;
        var _lidarActive = ctx._lidarActive;
        var _lidarActiveR = ctx._lidarActiveR;
        var esc = ctx.esc;

        // -----------------------------------------------------------------------
        // Scarp traces. Reads are public, writes editor-only (inventory/scarps.py).
        // Same pattern and placement as the landslide draw tool: the ✏ pencil
        // (DrawModeControl, kind 'scarp') opens a bottom-right panel in the
        // .inv-draw-panel idiom for the session, and closing it ends the session.
        // Outside a session — for everyone, editor or not — clicking a line opens
        // a popup with the note. There is no Analysis-pane entry and no card.
        // -----------------------------------------------------------------------
        var _scarpPanel = null, _scarpStatus = null, _scarpPopup = null;

        function _scarpSay(msg, bad) {
            if (!_scarpStatus) return;
            _scarpStatus.textContent = msg || '';
            _scarpStatus.style.color = bad ? '#b3261e' : '#1b7a3d';
            if (msg) setTimeout(function () {
                if (_scarpStatus && _scarpStatus.textContent === msg) _scarpStatus.textContent = '';
            }, 4000);
        }
        function _scarpFmtLen(len) {
            if (len == null) return '';
            return len >= 1000 ? (len / 1000).toFixed(2) + ' km' : Math.round(len) + ' m';
        }
        // traced_on for a new trace: what the editor was looking at.
        function _scarpContext() {
            var ids = {};
            Object.keys(_lidarActive).concat(Object.keys(_lidarActiveR)).forEach(function (id) { ids[id] = 1; });
            return { page: 'inventory', surveys: Object.keys(ids), basemap: _getCurrentBasemap() };
        }
        function _scarpOpenPanel() {
            if (_scarpPanel) { _scarpPanel.style.display = ''; _scarpSync(); return; }
            var p = document.createElement('div'); p.className = 'inv-draw-panel';
            p.innerHTML =
                '<div class="inv-draw-hd" style="color:#5a2d80;">✏ Trace scarps</div>' +
                '<div style="font-size:11px;color:#666;line-height:1.4;margin-bottom:6px;">' +
                'Click along the scarp, press <b>Enter</b> (or double-click) to finish, ' +
                '<b>Esc</b> cancels. Each line saves straight away — then write what you saw. ' +
                'Click an existing line to edit it: its note here, and on the map drag a vertex, ' +
                'drag a midpoint to add one, right-click a vertex to delete it, Ctrl/⌘-Z undoes. ' +
                'Working observations of scarps of any origin, not a fault map.</div>' +
                '<div id="isq-count" style="font-size:11px;color:#888;"></div>' +
                '<div id="isq-edit" style="display:none;margin-top:6px;border-top:1px solid #eee;padding-top:6px;">' +
                '<div id="isq-who" style="font-size:10px;color:#888;margin-bottom:4px;"></div>' +
                '<textarea id="isq-notes" rows="4" style="width:100%;box-sizing:border-box;font:inherit;' +
                'font-size:12px;border:1px solid #ccc;border-radius:4px;padding:4px 6px;resize:vertical;" ' +
                'placeholder="What made this look like a scarp? Uphill-facing? Cuts drainage? Which survey?"></textarea>' +
                '<div style="display:flex;gap:6px;margin-top:6px;">' +
                '<button id="isq-save" type="button" class="primary" style="background:#7b3fa0;">Save note</button>' +
                '<button id="isq-del" type="button">Delete</button></div></div>' +
                '<div id="isq-status" class="ls-poly-status" style="min-height:14px;font-size:11px;"></div>' +
                '<div style="margin-top:8px;display:flex;gap:6px;">' +
                '<button id="isq-done" type="button" style="margin-left:auto;">Exit</button></div>';
            document.body.appendChild(p);
            _scarpPanel = p; _scarpStatus = p.querySelector('#isq-status');
            p.querySelector('#isq-save').addEventListener('click', function () {
                LSScarps.saveNotes(p.querySelector('#isq-notes').value)
                    .then(function () { _scarpSay('Saved.'); })
                    .catch(function (e) { _scarpSay(e.message, true); });
            });
            p.querySelector('#isq-del').addEventListener('click', function () {
                if (!window.confirm('Delete this scarp? This cannot be undone.')) return;
                LSScarps.remove()
                    .then(function () { _scarpSay('Deleted.'); })
                    .catch(function (e) { _scarpSay(e.message, true); });
            });
            p.querySelector('#isq-done').addEventListener('click', function () {
                if (_drawCtrl) _drawCtrl.deactivate();
            });
            _scarpSync();
        }
        function _scarpClosePanel() { if (_scarpPanel) _scarpPanel.style.display = 'none'; }
        function _scarpSync() {
            if (!_scarpPanel || _scarpPanel.style.display === 'none') return;
            var fc = LSScarps.all(), f = LSScarps.selected();
            _scarpPanel.querySelector('#isq-count').textContent =
                fc.features.length + (fc.features.length === 1 ? ' trace' : ' traces') +
                ' (scarps and other landforms; trace other kinds on /lidar/)';
            var ed = _scarpPanel.querySelector('#isq-edit');
            ed.style.display = f ? '' : 'none';
            if (f) {
                _scarpPanel.querySelector('#isq-notes').value = f.properties.notes || '';
                var len = _scarpFmtLen(f.properties.length_m);
                _scarpPanel.querySelector('#isq-who').textContent =
                    'traced by ' + (f.properties.traced_by || '—') + (len ? ' · ' + len : '');
            }
        }
        // Read-only view of a trace, for anyone, outside a tracing session.
        function _scarpShowPopup(f, lngLat) {
            var pr = f.properties, len = _scarpFmtLen(pr.length_m);
            var k = LSScarps.kindInfo(pr.kind || 'scarp');
            var on = pr.traced_on && pr.traced_on.surveys && pr.traced_on.surveys.length
                ? ' · on ' + pr.traced_on.surveys.join(', ') : '';
            var html = '<div style="font:12px/1.4 system-ui,sans-serif;max-width:240px;">' +
                '<div style="font-weight:600;color:' + esc(k.color) + ';">' + esc(k.label) + ' trace</div>' +
                (pr.group ? '<div><b>' + esc(pr.group) + '</b></div>' : '') +
                '<div style="white-space:pre-wrap;margin:4px 0;">' +
                (pr.notes ? esc(pr.notes) : '<span style="color:#999;">(no note)</span>') + '</div>' +
                '<div style="font-size:10px;color:#888;">traced by ' + esc(pr.traced_by || '—') +
                (len ? ' · ' + len : '') + esc(on) + ' · a working observation, not a published map</div></div>';
            // The one popup slot closes whatever was open, which is what this
            // function used to do for its own predecessor alone.
            _scarpPopup = LSTools.popup.show(new maplibregl.Popup({ closeButton: true, maxWidth: '280px' })
                .setLngLat(lngLat).setHTML(html).addTo(map));
        }
        function _scarpInit() {
            if (typeof LSScarps === 'undefined') return;
            LSScarps.init({
                map: map, api: API_BASE, csrf: _csrf, onChange: _scarpSync,
                // Just the popup. The old select-on-click branch here was dead:
                // LSScarps sets mode='draw' for the whole tracing session and its
                // click handler returns whenever that is so, so this callback
                // never ran with the pencil in scarp mode. Selection while tracing
                // is finish-driven, and stays that way (Hig, 2026-09-24).
                onPick: _scarpShowPopup,
                flash: function (m, bad) { _scarpSay(m, bad); _scarpSync(); }
            }).catch(function (e) { console.warn('scarps: could not load — ' + e.message); });
        }

        return { _scarpContext: _scarpContext, _scarpOpenPanel: _scarpOpenPanel, _scarpClosePanel: _scarpClosePanel, _scarpInit: _scarpInit };
    } };
})();
