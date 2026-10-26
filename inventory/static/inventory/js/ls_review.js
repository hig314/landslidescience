/* Imagery review history on /inventory/ — the recorder and the overlay's
 * controls (server: inventory/review.py, whose docstring is the design note).
 *
 * Recorder. A VIEW starts when the map is at rest with its tiles drawn
 * (`idle`, or loaded() when input resumes) and ends at the next movestart, a
 * basemap or wiper change (map.js calls layoutChanged()), the tab hiding, or
 * IDLE_CAP without input -- then the dwell is cut back to the last input plus
 * IDLE_CAP, so a tab left open is not a review. With the wiper on, each pane
 * is its own record (its own basemap, its own share of the screen). Records
 * are batched and posted every FLUSH_MS and when the page goes away. Only
 * signed-in users: map.js does not call init() otherwise, and the server
 * refuses anonymous posts. Below MIN_ZOOM (10, where the lidar list starts)
 * it is navigation, not review, and nothing is recorded; the server caps
 * the dwell a single view counts toward density (review.DWELL_CAP_S).
 *
 * What was looked at. A high-res layer switched on in a pane and inside its
 * frame -- a lidar survey (by its footprint) or an editor's uploaded image
 * (by its bounds), host.layers(side) -- is taken to be what is being looked
 * at, and the view is credited to it (`lidar:<id>`, `trace:<id>`) instead of
 * the basemap beneath. Several in one frame share the view (k). Low-res
 * overlays are ignored: the basemap is still what is being read under them.
 * layoutChanged() (also called from writeHashState, so any toggle reaches
 * it) ends the view only when that attribution or the frame has changed.
 *
 * Overlay. One OVERLAYS entry in map.js ('review'); its tiles come from
 * tileUrl(m), which reads the reviewer, the imagery choice and the since
 * date kept here. "This pane's basemap" (the default) gives each pane the
 * history of the imagery it is showing. extraUI(side) builds the row's
 * controls; every copy (sidebar, wiper panel, left-pane panel) stays in step.
 *
 *   LSReview.init(host)   host = { map, base, basemap(), swipe(), label(id),
 *                                  visible(side), csrf }
 */
(function () {
    var IDLE_CAP = 120000;     // ms without input before a view stops counting
    var FLUSH_MS = 30000;
    var MIN_DWELL = 0.1;       // s
    var MIN_ZOOM = 10;         // below this it is navigation, not review
    var R_KM = 6371.0088;

    var host = null, cur = null, queue = [], lastAct = Date.now();
    var meta = null, metaP = null;
    var state = { u: '', b: '*', since: '', v: 1 };
    // The imagery choices besides 'all' and 'this pane': the two sources most
    // worth tracking (Google is a shared QMS layer; its QMS id is the same in
    // every environment).
    var SOURCES = [['esri-img', 'ESRI imagery'], ['qmsshared-678', 'Google imagery'],
                   ['lidar:*', 'all lidar']];
    var lastBump = 0;
    var syncs = [];            // repaint callbacks, one per extraUI copy

    function now() { return Date.now(); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function areaKm2(w, s, e, n) {
        var rad = Math.PI / 180;
        return R_KM * R_KM * (e - w) * rad * Math.abs(Math.sin(n * rad) - Math.sin(s * rad));
    }

    // ---- recorder ---------------------------------------------------------
    function panes() {
        var m = host.map, c = m.getContainer();
        var W = c.clientWidth, H = c.clientHeight;
        var sw = host.swipe();
        var list = [];
        if (sw.on && sw.map) {
            var xs = W * sw.x / 100;
            list.push({ p: 'l', b: host.basemap(), x0: 0, x1: xs });
            list.push({ p: 'r', b: sw.basemapId, x0: xs, x1: W });
        } else {
            list.push({ p: '', b: host.basemap(), x0: 0, x1: W });
        }
        var out = [];
        list.filter(function (q) { return q.x1 - q.x0 >= 20; }).forEach(function (q) {
            var a = m.unproject([q.x0, 0]), b = m.unproject([q.x1, H]);
            var w = a.lng, e = b.lng;
            if (e < w) e += 360;
            var n = Math.max(a.lat, b.lat), s = Math.min(a.lat, b.lat);
            if (!(isFinite(w) && isFinite(e) && isFinite(n) && isFinite(s) &&
                  e - w > 0 && e - w <= 180 && n > s)) return;
            var hi = host.layers(q.p === 'r' ? 'right' : 'left').filter(function (L) {
                return L.covers(w, s, e, n);
            });
            var keys = hi.length ? hi.map(function (L) { return L.key; }) : [q.b];
            keys.forEach(function (k) {
                if (!k || k === 'blank') return;
                out.push({ b: k, p: q.p, w: w, e: e, n: n, s: s, k: keys.length });
            });
        });
        return out;
    }
    function signature(ps) {
        return ps.map(function (q) {
            return [q.b, q.p, q.w.toFixed(5), q.s.toFixed(5), q.e.toFixed(5), q.n.toFixed(5)].join(',');
        }).join('|');
    }
    function canOpen() {
        if (document.hidden || now() - lastAct > IDLE_CAP) return false;
        var m = host.map;
        if (m.isMoving() || !m.loaded() || m.getZoom() < MIN_ZOOM) return false;
        var sw = host.swipe();
        if (sw.on && sw.map && !sw.map.loaded()) return false;
        return true;
    }
    function open() {
        if (cur || !canOpen()) return;
        var ps = panes();
        if (!ps.length) return;
        cur = { t: now(), z: host.map.getZoom(), panes: ps, sig: signature(ps) };
    }
    function close() {
        if (!cur) return;
        var end = Math.min(now(), lastAct + IDLE_CAP);
        var d = (end - cur.t) / 1000;
        if (d >= MIN_DWELL) {
            cur.panes.forEach(function (q) {
                queue.push({ b: q.b, p: q.p, z: +cur.z.toFixed(2),
                             w: +q.w.toFixed(6), s: +q.s.toFixed(6), e: +q.e.toFixed(6), n: +q.n.toFixed(6),
                             t: cur.t, d: +d.toFixed(2), k: q.k });
            });
        }
        cur = null;
    }
    function flush(leaving) {
        if (!queue.length) return;
        var batch = queue.splice(0, 500);
        fetch(host.base + 'api/review/log/', {
            method: 'POST', keepalive: !!leaving, credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': host.csrf },
            body: JSON.stringify({ views: batch })
        }).then(function (r) {
            if (!r.ok && r.status >= 500) queue = batch.concat(queue);   // retry later
            else if (r.ok) afterSaved();
        }).catch(function () { if (!leaving) queue = batch.concat(queue); });
    }
    // The overlay fills in behind the reviewer, at most once a minute.
    function afterSaved() {
        if (!(host.visible('left') || host.visible('right'))) return;
        if (now() - lastBump < 60000) return;
        bump();
    }
    function activity() {
        var idleBefore = now() - lastAct > IDLE_CAP;
        lastAct = now();
        if (idleBefore || !cur) open();
    }

    // ---- overlay state ------------------------------------------------------
    function loadMeta(force) {
        if (metaP && !force) return metaP;
        var q = state.u ? '?u=' + encodeURIComponent(state.u) : '';
        metaP = fetch(host.base + 'api/review/meta/' + q, { credentials: 'same-origin' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (j) { meta = j && j.ok ? j : null; repaint(); return meta; })
            .catch(function () { return null; });
        return metaP;
    }
    function basemapFor(m) {
        if (state.b !== '@pane') return state.b;
        var sw = host.swipe();
        return (sw.map && m === sw.map) ? sw.basemapId : host.basemap();
    }
    function query(b) {
        var q = ['b=' + encodeURIComponent(b || '*')];
        if (state.u) q.push('u=' + encodeURIComponent(state.u));
        if (state.since) q.push('since=' + state.since);
        return q.join('&');
    }
    function tileUrl(m) {
        return location.origin + host.base + 'tiles/review/{z}/{x}/{y}.png?' +
               query(basemapFor(m)) + '&v=' + state.v;
    }
    function bump() {
        lastBump = now();
        state.v++;
        var sw = host.swipe();
        [host.map, sw.map].forEach(function (m) {
            var src = m && m.getSource('ov-review-src');
            if (src && src.setTiles) src.setTiles([tileUrl(m)]);
        });
    }
    function repaint() { syncs.forEach(function (f) { f(); }); }
    function setState(k, v) {
        state[k] = v;
        if (k === 'u') loadMeta(true);
        bump();
        repaint();
    }
    function share(on) {
        return fetch(host.base + 'api/review/share/', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': host.csrf },
            body: JSON.stringify({ share: !!on })
        }).then(function () { return loadMeta(true); });
    }

    // What one second at this view adds, and how long each band takes.
    function calibration() {
        var bd = host.map.getBounds();
        var a = areaKm2(bd.getWest(), bd.getSouth(), bd.getEast(), bd.getNorth());
        if (!(a > 0)) return '';
        var per = 100 / a;   // s/100 km²
        var fmt = function (v) { return v >= 10 ? v.toFixed(0) : v >= 1 ? v.toFixed(1) : v.toPrecision(2); };
        var s = 'Here, 1 s on this screen adds ' + fmt(per) + ' s/100 km²';
        if (meta && meta.breaks) {
            var b = meta.breaks.filter(function (x) { return x / per >= 0.5 && x / per <= 600; })[0];
            if (b) s += ' · ' + fmt(b / per) + ' s reaches ' + b;
            if (host.map.getZoom() < MIN_ZOOM) s = 'Not recorded below zoom ' + MIN_ZOOM + '.';
        }
        return s + '.';
    }

    // ---- the row's controls ---------------------------------------------------
    function extraUI(side) {
        var box = document.createElement('div');
        box.style.cssText = 'margin-top:5px;font-size:10px;color:#666;line-height:1.5;';
        var line = function () {
            var d = document.createElement('div');
            d.style.cssText = 'display:flex;align-items:center;gap:4px;margin-top:2px;flex-wrap:wrap;';
            box.appendChild(d);
            return d;
        };
        var lab = function (parent, t) {
            var s = document.createElement('span'); s.textContent = t; parent.appendChild(s); return s;
        };
        var selCss = 'font-size:10px;padding:1px 2px;max-width:150px;';

        var l1 = line(); lab(l1, 'Reviewer');
        var uSel = document.createElement('select'); uSel.style.cssText = selCss; l1.appendChild(uSel);
        uSel.addEventListener('change', function () { setState('u', uSel.value); });

        var l2 = line(); lab(l2, 'Imagery');
        var bSel = document.createElement('select'); bSel.style.cssText = selCss; l2.appendChild(bSel);
        bSel.title = '"This pane\'s basemap" shows, in each pane, the history of the basemap it is showing. ' +
                     '"All imagery" (basemaps and uploaded images) and "all lidar" never overlap.';
        bSel.addEventListener('change', function () { setState('b', bSel.value); });

        var l3 = line(); lab(l3, 'Since');
        var dIn = document.createElement('input'); dIn.type = 'date';
        dIn.style.cssText = 'font-size:10px;padding:0 2px;';
        dIn.title = 'Only views on or after this date — e.g. since the provider last changed its imagery.';
        l3.appendChild(dIn);
        var clr = document.createElement('button'); clr.type = 'button'; clr.textContent = 'all time';
        clr.style.cssText = 'font-size:10px;padding:0 4px;';
        l3.appendChild(clr);
        dIn.addEventListener('change', function () { setState('since', dIn.value || ''); });
        clr.addEventListener('click', function () { dIn.value = ''; setState('since', ''); });

        var key = document.createElement('div');
        key.style.cssText = 'display:flex;flex-wrap:wrap;gap:2px 8px;margin-top:4px;';
        box.appendChild(key);
        var cal = document.createElement('div');
        cal.style.cssText = 'margin-top:3px;color:#888;';
        box.appendChild(cal);

        var l4 = document.createElement('label');
        l4.style.cssText = 'display:flex;align-items:center;gap:4px;margin-top:4px;cursor:pointer;';
        var shCb = document.createElement('input'); shCb.type = 'checkbox';
        l4.appendChild(shCb);
        lab(l4, 'Let other signed-in users see my review history');
        box.appendChild(l4);
        shCb.addEventListener('change', function () { share(shCb.checked); });

        function paint() {
            if (!meta) { cal.textContent = 'Loading…'; return; }
            var rs = meta.reviewers || [];
            uSel.innerHTML = '';
            rs.forEach(function (r) {
                var o = document.createElement('option');
                o.value = r.me ? '' : r.username;
                o.textContent = (r.me ? 'me' : r.username) + ' (' + (r.views || 0) + ')';
                uSel.appendChild(o);
            });
            uSel.value = state.u;
            l1.style.display = rs.length > 1 ? '' : 'none';

            bSel.innerHTML = '';
            var opts = [['*', 'all imagery'], ['@pane', 'this pane\'s basemap']].concat(SOURCES);
            opts.forEach(function (o) {
                var el = document.createElement('option'); el.value = o[0]; el.textContent = o[1];
                bSel.appendChild(el);
            });
            bSel.value = state.b;
            dIn.value = state.since;

            key.innerHTML = '';
            var br = meta.breaks || [], cs = meta.colors || [];
            cs.forEach(function (c, i) {
                var t = i === 0 ? '< ' + br[0] : i === cs.length - 1 ? '≥ ' + br[br.length - 1] : br[i - 1] + '–' + br[i];
                var s = document.createElement('span');
                s.style.cssText = 'display:inline-flex;align-items:center;gap:3px;';
                s.innerHTML = '<span style="width:10px;height:10px;border-radius:2px;border:1px solid rgba(0,0,0,.2);background:' +
                              c + ';"></span>' + esc(t);
                key.appendChild(s);
            });
            var u = document.createElement('span'); u.textContent = meta.units || ''; u.style.color = '#999';
            key.appendChild(u);
            cal.textContent = calibration();
            shCb.checked = meta.sharing === true;
        }
        syncs.push(paint);
        paint();
        loadMeta();
        return box;
    }

    // ---- the one-time notice --------------------------------------------------
    function notice() {
        loadMeta().then(function (m) {
            if (!m || m.sharing !== null) return;
            var c = host.map.getContainer().parentNode;
            var d = document.createElement('div');
            d.style.cssText = 'position:absolute;left:12px;bottom:28px;z-index:12;max-width:360px;' +
                'background:#fff;border:1px solid #d7ccc8;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,.25);' +
                'padding:10px 12px;font:12px/1.45 system-ui,sans-serif;color:#444;';
            d.innerHTML = '<div style="font-weight:600;color:#5d4037;margin-bottom:4px;">Imagery review history</div>' +
                'While you are signed in, this map records which imagery you look at, where, and for how long, ' +
                'so you can see what you have already reviewed (Reference maps → Overlays → Review history). ' +
                'It is private to you unless you share it. Anonymous visitors are never recorded.' +
                '<div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap;">' +
                '<button type="button" data-a="1">Share with other signed-in users</button>' +
                '<button type="button" data-a="0">Keep it private</button>' +
                '<button type="button" data-a="" style="margin-left:auto;">Later</button></div>';
            c.appendChild(d);
            d.addEventListener('click', function (ev) {
                var a = ev.target && ev.target.getAttribute('data-a');
                if (a === null) return;
                d.remove();
                if (a !== '') share(a === '1');
            });
        });
    }

    // ---- the click --------------------------------------------------------------
    function pointHtml(lngLat, side) {
        var sw = host.swipe();
        var m = side === 'right' && sw.map ? sw.map : host.map;
        var url = host.base + 'api/review/point/?lon=' + lngLat.lng.toFixed(6) + '&lat=' +
                  lngLat.lat.toFixed(6) + '&' + query(basemapFor(m));
        return fetch(url, { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (j) {
            var box = '<div style="font:12px/1.4 system-ui,sans-serif;max-width:300px;">';
            if (!j.ok) return box + esc(j.error || 'Could not read the history.') + '</div>';
            var who = j.user === (meta && meta.reviewers && meta.reviewers[0].username) ? 'You' : esc(j.user);
            if (!j.views) return box + who + ' have no recorded views here' +
                (state.b === '*' ? '' : ' on this imagery') + (state.since ? ' since ' + state.since : '') + '.</div>';
            var h = box + '<div><b>' + who + '</b>: ' + j.views + ' view' + (j.views === 1 ? '' : 's') +
                    ' here, ' + j.density.toPrecision(2) + ' ' + esc(j.units) + ' in all</div>' +
                    '<table style="margin-top:4px;font-size:11px;border-collapse:collapse;">' +
                    '<tr style="color:#999;"><td>date</td><td style="padding-left:8px;">imagery</td>' +
                    '<td style="padding-left:8px;">zoom</td><td style="padding-left:8px;">s</td></tr>';
            j.recent.forEach(function (v) {
                h += '<tr><td>' + esc(v.date) + '</td><td style="padding-left:8px;">' + esc(host.label(v.basemap)) +
                     '</td><td style="padding-left:8px;">' + v.zoom + '</td><td style="padding-left:8px;">' + v.dwell + '</td></tr>';
            });
            if (j.views > j.recent.length) h += '<tr><td colspan="4" style="color:#999;">… and ' +
                (j.views - j.recent.length) + ' earlier</td></tr>';
            return h + '</table></div>';
        });
    }

    function init(h) {
        host = h;
        var m = host.map;
        m.on('movestart', close);
        m.on('idle', open);
        m.on('moveend', function () { if (syncs.length && meta) repaint(); });
        ['pointerdown', 'pointermove', 'wheel', 'keydown', 'touchstart'].forEach(function (t) {
            window.addEventListener(t, activity, { passive: true, capture: true });
        });
        document.addEventListener('visibilitychange', function () {
            if (document.hidden) { close(); flush(true); }
            else { lastAct = now(); open(); }
        });
        window.addEventListener('pagehide', function () { close(); flush(true); });
        setInterval(function () {
            if (cur && now() - lastAct > IDLE_CAP) close();
            flush(false);
        }, FLUSH_MS);
        notice();
    }
    // A basemap or wiper change: the view so far was of the old layout. The
    // next one opens when both panes are drawn again.
    var hooked = null;
    function layoutChanged() {
        if (!host) return;
        if (cur) {
            var ps = canOpen() ? panes() : [];
            if (ps.length && signature(ps) === cur.sig) return;   // same frame, same imagery
        }
        close();
        var sw = host && host.swipe();
        if (sw && sw.map && hooked !== sw.map) { hooked = sw.map; sw.map.on('idle', open); }
        setTimeout(open, 0);
    }

    window.LSReview = {
        init: init, layoutChanged: layoutChanged,
        tileUrl: tileUrl, extraUI: extraUI, pointHtml: pointHtml,
        _test: { panes: panes, queue: function () { return queue; }, state: state, flush: flush, close: close }
    };
})();
