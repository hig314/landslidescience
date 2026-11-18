/* /glaciers — the flow network, face lines, and the distance plot.
 *
 * Server side and the reasoning: glaciers/network.py, GLACIERS_PLAN.md §0.
 *
 * WHAT IS ON THE MAP
 *   RGI centrelines   reference only (tile layer `lines`), thin and pale.
 *   the network       our flow lines (adopted from RGI, drafted, or drawn)
 *                     and our face lines, in the landform-trace editor's
 *                     layers. That editor (scarps.js) is mounted here on
 *                     /glaciers/lines/, which answers its API contract, so
 *                     selecting, dragging vertices, inserting at midpoints
 *                     and undo are the same code as on /lidar/.
 *   end dots          the downstream end of every flow line: a ring where it
 *                     joins another line, a solid dot where nothing is below
 *                     it (an outlet). Lines run down-flow and joins are found
 *                     from geometry, so the dots are how direction and a
 *                     missed join are seen.
 *
 * CLICK A FLOW LINE (outside an editing session) to plot the path from it
 * down to its outlet: one point per ice face, time across and distance from
 * the head of the line up by default (the other way round is a menu choice, kept per
 * browser). URL: np=<line id>.
 *
 * Clicks go through LSTools; the editing session is an LSTools mode.
 */
window.GLNetwork = (function () {
    'use strict';

    var GL, map, CFG;
    var API = '/glaciers/lines/';
    var SRC_COLOR = { nsidc0634: '#d62728', usgs_glacier_bay: '#9467bd', ours: '#ff4081' };
    var SRC_LABEL = { nsidc0634: 'McNabb & Hock', usgs_glacier_bay: 'USGS Glacier Bay', ours: 'Ours' };
    var S = { path: null, pathId: null, method: 'x_m', axes: 'tx', editing: false, refOn: true };
    try { if (localStorage.getItem('gl_nw_axes') === 'dx') S.axes = 'dx'; } catch (e) {}
    var el = {};

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function csrf() { return CFG.csrf; }
    function post(url, body) {
        return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
                            body: JSON.stringify(body) })
            .then(function (r) {
                return r.json().catch(function () { throw new Error('HTTP ' + r.status + ' (signed in?)'); })
                    .then(function (d) { if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status)); return d; });
            });
    }
    function visible() { return GL.tab() !== 'flow'; }

    // ------------------------------------------------------------------
    // Layers of our own (the editor adds the network's)
    // ------------------------------------------------------------------
    function addLayers() {
        var before = map.getLayer('gx-obs-line') ? 'gx-obs-line' : undefined;
        if (map.getSource('gx') && !map.getLayer('gx-cl')) {
            map.addLayer({ id: 'gx-cl', type: 'line', source: 'gx', 'source-layer': 'lines',
                           paint: { 'line-color': '#b3e5fc', 'line-opacity': 0.55,
                                    'line-width': ['case', ['get', 'main'], 1, 0.5] } }, before);
        }
        if (!map.getSource('gn-path')) {
            map.addSource('gn-path', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
            map.addLayer({ id: 'gn-path', type: 'line', source: 'gn-path',
                           layout: { 'line-cap': 'round', 'line-join': 'round' },
                           paint: { 'line-color': '#ffee58', 'line-opacity': 0.55, 'line-width': 9 } });
        }
        if (!map.getSource('gn-nodes')) {
            map.addSource('gn-nodes', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
            map.addLayer({ id: 'gn-nodes', type: 'circle', source: 'gn-nodes',
                           // outlet = solid blue; join = white ring; fork (where a
                           // distributary leaves its parent) = orange
                           paint: { 'circle-radius': ['case', ['get', 'outlet'], 5.5, ['get', 'fork'], 4.5, 3],
                                    'circle-color': ['case', ['get', 'outlet'], '#00b0ff', ['get', 'fork'], '#ff9800', '#ffffff'],
                                    'circle-stroke-color': ['case', ['get', 'outlet'], '#ffffff', ['get', 'fork'], '#ffffff', '#00b0ff'],
                                    'circle-stroke-width': 1.5 } });
        }
        if (!map.getLayer('gn-hi') && map.getSource('gx')) {
            map.addLayer({ id: 'gn-hi', type: 'line', source: 'gx', 'source-layer': 'obs',
                           filter: ['==', ['get', 'id'], -1],
                           paint: { 'line-color': '#ffffff', 'line-width': 5, 'line-opacity': 0.9 } });
        }
        if (window.LSScarps && S.ready) window.LSScarps.raise();
        ['gn-nodes'].forEach(function (id) { if (map.getLayer(id)) map.moveLayer(id); });
        applyVis();
        if (S.path) drawPathOnMap();
        refreshNodes();
    }
    function applyVis() {
        var v = visible();
        [['gx-cl', v && S.refOn], ['gn-path', v], ['gn-nodes', v], ['gn-hi', v]].forEach(function (p) {
            if (map.getLayer(p[0])) map.setLayoutProperty(p[0], 'visibility', p[1] ? 'visible' : 'none');
        });
        if (window.LSScarps && S.ready) window.LSScarps.setVisible(v);
        if (el.chart) el.chart.style.display = (v && S.path) ? '' : 'none';
    }
    var _nodesT = null;
    function refreshNodes() {
        clearTimeout(_nodesT);
        _nodesT = setTimeout(function () {
            fetch(API + 'api/nodes/').then(function (r) { return r.json(); }).then(function (fc) {
                if (map.getSource('gn-nodes')) map.getSource('gn-nodes').setData(fc);
            }).catch(function () {});
        }, 400);
    }

    // ------------------------------------------------------------------
    // The path and its plot
    // ------------------------------------------------------------------
    function showPath(id) {
        S.pathId = id;
        if (id == null) { S.path = null; drawPathOnMap(); renderChart(); GL.writeHash(); return; }
        fetch(API + 'api/path/' + id + '/').then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        }).then(function (d) {
            if (S.pathId !== id) return;
            S.path = d; drawPathOnMap(); renderChart(); GL.writeHash();
        }).catch(function () {
            if (S.pathId === id) { S.pathId = null; S.path = null; drawPathOnMap(); renderChart(); }
        });
    }
    function drawPathOnMap() {
        if (!map.getSource('gn-path')) return;
        map.getSource('gn-path').setData(S.path && S.path.geometry
            ? { type: 'Feature', properties: {}, geometry: S.path.geometry }
            : { type: 'FeatureCollection', features: [] });
    }
    function niceStep(span, target) {
        var raw = span / target, p = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / p;
        return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p;
    }
    function hi(fid) {
        if (!map.getLayer('gn-hi')) return;
        var n = (fid && fid.charAt(0) === 'o') ? +fid.slice(1) : -1;
        map.setFilter('gn-hi', ['==', ['get', 'id'], n]);
    }
    function renderChart() {
        var c = el.chart;
        if (!S.path) { c.style.display = 'none'; c.innerHTML = ''; return; }
        c.style.display = visible() ? '' : 'none';
        var P = S.path, F = P.faces.filter(function (f) { return f.t0 != null; });
        var W = Math.max(420, Math.min(900, el.chart.parentNode.clientWidth - 40)), H = 270;
        var m = { l: 46, r: 14, t: 30, b: 34 };
        // The distance axis covers where the faces are, not the whole line:
        // 40 km of glacier above a 9 km retreat left the data in a thin strip.
        // Distance is measured DOWN-FLOW FROM THE HEAD of the line (Hig,
        // 2026-11-17), so a retreat reads as falling numbers, like a glacier
        // length. The API's x_m / cross_m / end_m count up from the outlet.
        function DK(m_) { return (P.total_m - m_) / 1000; }
        var dmax = P.total_m / 1000, d0 = 0, d1 = dmax;
        if (F.length) {
            var lo = Infinity, hi = -Infinity;
            F.forEach(function (f) { var d = DK(f[S.method]); lo = Math.min(lo, d); hi = Math.max(hi, d); });
            var dp = Math.max(0.5, (hi - lo) * 0.12);
            d0 = Math.max(0, lo - dp); d1 = Math.min(dmax, hi + dp);
            if (d1 - d0 < 1) { d0 = Math.max(0, d0 - 0.5); d1 = Math.min(dmax, d1 + 0.5); }
        }
        var t0 = Infinity, t1 = -Infinity;
        F.forEach(function (f) { t0 = Math.min(t0, f.t0); t1 = Math.max(t1, f.t1 || f.t0); });
        if (!F.length) { t0 = 1900; t1 = 2025; }
        var pad = Math.max(2, (t1 - t0) * 0.05); t0 = Math.floor(t0 - pad); t1 = Math.ceil(t1 + pad);
        // Two ways round (Hig, 2026-11-16): time across and distance up is
        // the default, the way a retreat is usually read; distance across
        // is the one that lines up with elevation profiles later.
        var tx = S.axes !== 'dx';
        var pw = W - m.l - m.r, ph = H - m.t - m.b;
        function PX(d, t) { return tx ? m.l + (t - t0) / (t1 - t0) * pw : m.l + (d - d0) / (d1 - d0) * pw; }
        function PY(d, t) { return tx ? H - m.b - (d - d0) / (d1 - d0) * ph : H - m.b - (t - t0) / (t1 - t0) * ph; }
        var s = '<svg width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '">';
        // reaches: a band per reach, named at its edge
        P.reaches.forEach(function (r, i) {
            var a = Math.max(d0, DK(r.end_m + r.length_m)), b = Math.min(d1, DK(r.end_m));
            if (b <= a) return;                      // this reach is off the axis
            var fill = r.origin === 'rgi7' ? '#e3f2fd' : '#fff8e1', op = i % 2 ? 0.55 : 0.9;
            // a line the path joins part-way down is on it only below the join
            var nm = esc((r.name || ('line ' + r.id)).slice(0, 40) + (r.to_frac < 1 ? ', above the fork' : (r.from_frac > 0 ? ', below the join' : '')));
            if (tx) {
                s += '<rect x="' + m.l + '" y="' + PY(b, t0) + '" width="' + pw + '" height="' + (PY(a, t0) - PY(b, t0)) +
                    '" fill="' + fill + '" opacity="' + op + '"/>' +
                    '<text x="' + (m.l + 5) + '" y="' + (PY(b, t0) + 12) + '" font-size="10" fill="#5d4037">' + nm + '</text>' +
                    '<line x1="' + m.l + '" x2="' + (W - m.r) + '" y1="' + PY(b, t0) + '" y2="' + PY(b, t0) + '" stroke="#bbb" stroke-dasharray="3 3"/>';
            } else {
                s += '<rect x="' + PX(a, t0) + '" y="' + m.t + '" width="' + (PX(b, t0) - PX(a, t0)) + '" height="' + ph +
                    '" fill="' + fill + '" opacity="' + op + '"/>' +
                    '<text x="' + (PX(a, t0) + 4) + '" y="' + (m.t - 6) + '" font-size="10" fill="#5d4037">' + nm + '</text>' +
                    '<line x1="' + PX(b, t0) + '" x2="' + PX(b, t0) + '" y1="' + m.t + '" y2="' + (H - m.b) + '" stroke="#bbb" stroke-dasharray="3 3"/>';
            }
        });
        var ds = niceStep(d1 - d0, tx ? 6 : 8), ts = niceStep(t1 - t0, tx ? 9 : 7);
        function xtick(px, label) {
            return '<line x1="' + px + '" x2="' + px + '" y1="' + (H - m.b) + '" y2="' + (H - m.b + 4) + '" stroke="#666"/>' +
                '<text x="' + px + '" y="' + (H - m.b + 15) + '" font-size="10" text-anchor="middle" fill="#444">' + label + '</text>';
        }
        function ytick(py, label) {
            return '<line x1="' + m.l + '" x2="' + (W - m.r) + '" y1="' + py + '" y2="' + py + '" stroke="#ddd"/>' +
                '<text x="' + (m.l - 5) + '" y="' + (py + 3) + '" font-size="10" text-anchor="end" fill="#444">' + label + '</text>';
        }
        for (var k = Math.ceil(d0 / ds) * ds; k <= d1 + 1e-9; k += ds) {
            s += tx ? ytick(PY(k, t0), +k.toFixed(2)) : xtick(PX(k, t0), +k.toFixed(2));
        }
        for (var t = Math.ceil(t0 / ts) * ts; t <= t1; t += ts) {
            s += tx ? xtick(PX(0, t), t) : ytick(PY(0, t), t);
        }
        s += '<rect x="' + m.l + '" y="' + m.t + '" width="' + pw + '" height="' + ph + '" fill="none" stroke="#888"/>';
        var dlab = 'distance down-flow from the head of the line (km)';
        s += '<text x="' + (m.l + pw / 2) + '" y="' + (H - 4) + '" font-size="10.5" text-anchor="middle" fill="#444">' +
            (tx ? 'year' : dlab + '  →  down-glacier') + '</text>';
        if (tx) {
            s += '<text transform="rotate(-90 11 ' + (m.t + ph / 2) + ')" x="11" y="' + (m.t + ph / 2) +
                '" font-size="10.5" text-anchor="middle" fill="#444">' + dlab + '</text>';
        }
        // Videos linked to segments of this path: a box over the stretch of
        // line and the years each one shows; with no dates, a bar at the
        // edge of the distance axis.
        (P.videos || []).forEach(function (v) {
            v.spans.forEach(function (sp) {
                var a = Math.max(d0, DK(sp.b_m)), b = Math.min(d1, DK(sp.a_m));
                if (b <= a) return;
                var ta = v.t0 != null ? Math.max(t0, v.t0) : null, tb = v.t1 != null ? Math.min(t1, v.t1) : null;
                var x, y, w, h;
                if (ta != null && tb != null && tb > ta) {
                    x = Math.min(PX(a, ta), PX(b, tb)); w = Math.abs(PX(b, tb) - PX(a, ta));
                    y = Math.min(PY(a, ta), PY(b, tb)); h = Math.abs(PY(b, tb) - PY(a, ta));
                } else if (tx) {
                    x = W - m.r - 7; w = 7; y = Math.min(PY(a, t0), PY(b, t0)); h = Math.abs(PY(b, t0) - PY(a, t0));
                } else {
                    y = m.t; h = 7; x = Math.min(PX(a, t0), PX(b, t0)); w = Math.abs(PX(b, t0) - PX(a, t0));
                }
                s += '<rect class="nw-vid" data-v="' + v.id + '" data-m="' + sp.mid.join(',') + '" x="' + x + '" y="' + y +
                    '" width="' + Math.max(w, 2) + '" height="' + Math.max(h, 2) + '" fill="' + v.color + '" fill-opacity="0.13" stroke="' +
                    v.color + '" stroke-width="1.2"><title>' + esc((v.caption || v.kind_label) + (v.date ? ' · ' + v.date : '') + ' (click to play)') +
                    '</title></rect>';
            });
        });
        F.forEach(function (f, i) {
            var d = DK(f[S.method]), col = SRC_COLOR[f.source] || '#555';
            if (f.t1 != null && f.t1 - f.t0 > 0.02) {          // the date bracket, as a whisker along the time axis
                s += '<line x1="' + PX(d, f.t0) + '" x2="' + PX(d, f.t1) + '" y1="' + PY(d, f.t0) + '" y2="' + PY(d, f.t1) +
                    '" stroke="' + col + '" stroke-width="1.5" opacity="0.6"/>';
            }
            var tm = (f.t0 + (f.t1 || f.t0)) / 2, x = PX(d, tm), y = PY(d, tm);
            s += f.how === 'pinned'
                ? '<rect class="nw-pt" data-i="' + i + '" x="' + (x - 3.5) + '" y="' + (y - 3.5) + '" width="7" height="7" transform="rotate(45 ' + x + ' ' + y + ')" fill="' + col + '" stroke="#fff"/>'
                : '<circle class="nw-pt" data-i="' + i + '" cx="' + x + '" cy="' + y + '" r="3.2" fill="' +
                  (f.how === 'projected' ? '#fff' : col) + '" stroke="' + (f.how === 'projected' ? col : '#fff') + '" stroke-width="' + (f.how === 'projected' ? 1.5 : 0.6) + '"/>';
        });
        s += '</svg>';
        var srcs = {};
        F.forEach(function (f) { srcs[f.source] = (srcs[f.source] || 0) + 1; });
        var head = '<div class="nw-head"><b>' + esc(P.reaches[0].name || 'Flow line ' + P.line_id) + '</b> to the outlet · ' +
            (P.total_m / 1000).toFixed(1) + ' km · ' + F.length + ' ice faces ' +
            Object.keys(srcs).map(function (k) {
                return '<span class="nw-key"><i style="background:' + (SRC_COLOR[k] || '#555') + '"></i>' + esc(SRC_LABEL[k] || k) + ' ' + srcs[k] + '</span>';
            }).join('') +
            '<span class="nw-tools"><select id="nw-axes" title="Which way round the plot is drawn">' +
            '<option value="tx">time across, distance up</option><option value="dx">distance across, time up</option></select>' +
            '<select id="nw-method" title="How a face becomes one position on the line">' +
            '<option value="x_m">width-averaged position</option><option value="cross_m">centreline crossing</option></select>' +
            '<button type="button" id="nw-csv" title="Download this series">CSV</button>' +
            '<button type="button" id="nw-close" title="Close the plot">×</button></span></div>';
        var vids = (P.videos || []).length ? '<div class="nw-vids"><span class="rt-sub">Videos on this path:</span>' +
            P.videos.map(function (v) {
                return '<button type="button" data-v="' + v.id + '" data-m="' + v.spans[0].mid.join(',') + '" style="border-color:' + v.color +
                    '; color:' + v.color + '" title="' + esc(v.kind_label) + '">▶ ' + esc((v.caption || v.kind_label).slice(0, 34)) +
                    (v.date ? ' · ' + esc(v.date) : '') + '</button>';
            }).join('') + '</div>' : '';
        c.innerHTML = head + s + vids + '<div class="nw-tip" id="nw-tip"></div>' +
            '<div class="nw-foot">Filled = the face crosses the line; ring = it stops short and is projected onto it; ' +
            'diamond = pinned by hand. Click a point to set the year bar.</div>';
        $('nw-axes').value = S.axes;
        $('nw-axes').addEventListener('change', function () {
            S.axes = this.value;
            try { localStorage.setItem('gl_nw_axes', S.axes); } catch (e) {}
            renderChart();
        });
        $('nw-method').value = S.method;
        $('nw-method').addEventListener('change', function () { S.method = this.value; renderChart(); });
        $('nw-close').addEventListener('click', function () { showPath(null); });
        $('nw-csv').addEventListener('click', function () {
            var rows = ['id,source,how,date_min,date_max,width_averaged_km_from_head,crossing_km_from_head,reach_id,label'];
            F.forEach(function (f) {
                rows.push([f.id, f.source, f.how, f.date_min, f.date_max, DK(f.x_m).toFixed(3),
                           DK(f.cross_m).toFixed(3), f.reach, '"' + String(f.label).replace(/"/g, '""') + '"'].join(','));
            });
            var a = document.createElement('a');
            a.href = URL.createObjectURL(new Blob([rows.join('\n')], { type: 'text/csv' }));
            a.download = 'ice_faces_line_' + P.line_id + '.csv'; a.click();
        });
        Array.prototype.forEach.call(c.querySelectorAll('[data-v]'), function (b) {
            b.addEventListener('click', function () {
                var mm = b.getAttribute('data-m').split(',');
                if (window.GLVideos) window.GLVideos.open(+b.getAttribute('data-v'), { lng: +mm[0], lat: +mm[1] });
            });
        });
        var tip = $('nw-tip');
        Array.prototype.forEach.call(c.querySelectorAll('.nw-pt'), function (p) {
            var f = F[+p.getAttribute('data-i')];
            p.addEventListener('mouseenter', function () {
                tip.textContent = (f.date_min === f.date_max ? f.date_min : f.date_min + ' to ' + f.date_max) + ' · ' +
                    DK(f[S.method]).toFixed(2) + ' km · ' + (SRC_LABEL[f.source] || f.source) +
                    (f.label ? ' · ' + f.label : '') + (f.how !== 'crossing' ? ' · ' + f.how : '');
                hi(f.id);
            });
            p.addEventListener('mouseleave', function () { tip.textContent = ''; hi(null); });
            p.addEventListener('click', function () {
                if (window.GLRetreat && window.GLRetreat.setYear) window.GLRetreat.setYear(Math.floor(f.t0));
            });
        });
    }

    // ------------------------------------------------------------------
    // Editing (editors only): an LSTools mode around the trace editor
    // ------------------------------------------------------------------
    function say(msg, bad) {
        if (!el.msg) return;
        el.msg.textContent = msg || '';
        el.msg.style.color = bad ? '#b71c1c' : '#2e7d32';
    }
    function afterChange() {
        refreshNodes();
        if (S.pathId != null) showPath(S.pathId);
    }
    function sel() { return window.LSScarps.selected(); }
    function syncPanel() {
        if (!el.panel) return;
        el.panel.style.display = S.editing ? '' : 'none';
        Array.prototype.forEach.call(document.querySelectorAll('.nw-openRow'), function (r) {
            r.style.display = S.editing ? 'none' : '';
        });
        if (!S.editing) return;
        var f = sel(), p = f && f.properties;
        el.selBox.style.display = f ? '' : 'none';
        // One state at a time: a selected line shows its fields, none shows
        // the tools for drawing a new one.
        if (el.create) el.create.style.display = f ? 'none' : '';
        var drawing = window.LSScarps.drawArmed();
        el.newLine.disabled = el.newFace.disabled = drawing;
        if (!f) return;
        var isFace = p.kind === 'face';
        el.selTitle.textContent = (isFace ? 'Ice face' : 'Flow line') + ' #' + p.id +
            (p.length_m ? ' · ' + (p.length_m / 1000).toFixed(2) + ' km' : '') +
            (!isFace && p.origin !== 'manual' ? ' · ' + ({ rgi7: 'from RGI', draft: 'draft', mixed: 'RGI + drawn beyond it' }[p.origin] || p.origin) : '');
        el.faceRows.style.display = isFace ? '' : 'none';
        el.lineRows.style.display = isFace ? 'none' : '';
        if (document.activeElement !== el.name) el.name.value = p.name || '';
        if (document.activeElement !== el.date) el.date.value = p.date || '';
        if (document.activeElement !== el.srcNote) el.srcNote.value = p.source_note || '';
        if (document.activeElement !== el.notes) el.notes.value = p.notes || '';
        el.basis.value = p.basis || '';
        el.kindSel.value = p.kind;
    }
    function saveField(fields) {
        if (!sel()) return;
        window.LSScarps.save(fields).then(function () { say('Saved.'); afterChange(); })
            .catch(function (e) { say(e.message, true); });
    }
    function startEdit() {
        if (S.editing) return;
        if (typeof terraDraw === 'undefined' || typeof terraDrawMaplibreGlAdapter === 'undefined') {
            say('The drawing library did not load; check the connection and reload.', true); return;
        }
        if (!window.LSTools.claim('glnet')) return;
        S.editing = true;
        if (GL.tab() === 'flow') GL.showTab('retreat', true);
        try {
            window.LSScarps.startDraw({ terraDraw: window.terraDraw, adapter: window.terraDrawMaplibreGlAdapter,
                                        // explicit: nothing is drawn until a
                                        // "new line" button arms it, so a
                                        // missed click cannot start a line
                                        explicit: true,
                                        // ends of flow lines snap onto other flow lines
                                        snapKinds: ['flowline'],
                                        context: function () { return { page: 'glaciers', basemap: GL.basemap ? GL.basemap() : null }; } });
        } catch (e) {
            console.error('edit start failed:', e); stopEdit(); say('Could not start: ' + e.message, true); return;
        }
        say('Click a line to edit it. Use the buttons above to draw a new one.');
        if (S.fillFrom) S.fillFrom();
        if (S.autoSrc) S.autoSrc();
        syncPanel();
    }
    function stopEdit() {
        if (!S.editing) return;
        S.editing = false;
        window.LSScarps.stopDraw(); window.LSScarps.select(null);
        window.LSTools.cursor.clear('tool');
        window.LSTools.release('glnet');
        syncPanel();
    }
    function wireEditor() {
        ['panel', 'msg', 'selBox', 'none', 'create', 'selTitle', 'faceRows', 'lineRows', 'name', 'date', 'basis',
         'srcNote', 'notes', 'kindSel', 'newLine', 'newFace', 'extend', 'reverse', 'del', 'done', 'file', 'plot',
         'fFrom', 'fDate', 'fBasis', 'fSrc', 'fKeep']
            .forEach(function (k) { el[k] = $('nw-' + k); });
        window.LSTools.mode({
            id: 'glnet', order: 30, label: '✎',
            title: 'Edit the flow network and ice faces: draw flow lines (down-flow) and face lines, drag vertices, join feeders',
            onSelect: startEdit, onRelease: stopEdit,
            canRelease: function () { return !window.LSScarps.lineInProgress(); },
            // Escape is Terra Draw's (it drops the line in progress).
            cancel: function () {}
        });
        Array.prototype.forEach.call(document.querySelectorAll('.nw-open'), function (b) {
            b.addEventListener('click', startEdit);
        });
        function arm(kind) {
            window.LSScarps.setDrawKind(kind);
            window.LSScarps.armDraw(true);
            say('Drawing one ' + (kind === 'face' ? 'ice face' : 'flow line (start up-flow, end down-flow)') +
                ': click along it; right-click or Enter ends it; Esc cancels.');
        }
        el.newLine.addEventListener('click', function () { arm('flowline'); });
        el.newFace.addEventListener('click', function () {
            autoSrc();
            arm('face');
            var d = el.fDate.value.trim();
            say('Tracing one ice face' + (d ? ' dated ' + d : ' with NO DATE (it will not plot until it has one)') +
                ': click along the front; right-click or Enter ends it; Esc cancels.', !d);
        });
        // Source: one pull-down, the same "＋ New …" pattern as trace groups.
        // It lists what is switched on to trace from (choosing one also fills
        // the date and basis), then every source already used on a face, then
        // "＋ New source…", which opens a text box. el.fSrc holds the value
        // that is saved.
        var MAP_BASIS = { 'usgs-hist': 'map', 'usgs-topo': 'map', 'esri-topo': 'map', 'streets': 'map', 'noaa-chart': 'chart' };
        var NEWSRC = '__new__', srcBox = $('nw-fSrcNew');
        S.srcAdded = []; S.srcBasis = {};
        function srcOf(it) { return it.title + (it.note ? ' — ' + it.note : ''); }
        function basisOf(it) { return it.kind === 'survey' ? 'survey' : (it.kind === 'basemap' ? (MAP_BASIS[it.id] || 'image') : 'image'); }
        function opt(v, label) { return '<option value="' + v + '">' + esc(label) + '</option>'; }
        function fillFrom() {
            var items = GL.imagery ? GL.imagery() : [], cur = el.fSrc.value.trim();
            var typing = srcBox.style.display !== 'none';
            var seen = {}, used = [];
            items.forEach(function (it) { seen[srcOf(it)] = 1; });
            var all = S.srcAdded.slice();
            try {
                window.LSScarps.all().features.forEach(function (f) {
                    if (f.properties.kind === 'face' && f.properties.source_note) {
                        all.push(f.properties.source_note);
                        // what that source was read as, to offer with it
                        if (f.properties.basis) S.srcBasis[f.properties.source_note] = f.properties.basis;
                    }
                });
            } catch (e) { /* the network has not loaded yet */ }
            all.forEach(function (t) { if (!seen[t]) { seen[t] = 1; used.push(t); } });
            used.sort(function (x, y) { return x.localeCompare(y); });
            S.fromItems = items; S.usedSrc = used;
            el.fFrom.innerHTML = opt('', '(not said)') +
                (items.length ? '<optgroup label="On the map now">' + items.map(function (it, i) {
                    return opt('m' + i, it.title + (it.date ? ' · ' + it.date : '')); }).join('') + '</optgroup>' : '') +
                (used.length ? '<optgroup label="Used on other faces">' + used.map(function (t, i) {
                    return opt('u' + i, t.length > 70 ? t.slice(0, 68) + '…' : t); }).join('') + '</optgroup>' : '') +
                opt(NEWSRC, '＋ New source…');
            var v = '';
            if (typing) v = NEWSRC;
            else if (cur) {
                items.forEach(function (it, i) { if (!v && srcOf(it) === cur) v = 'm' + i; });
                if (!v && used.indexOf(cur) >= 0) v = 'u' + used.indexOf(cur);
            }
            el.fFrom.value = v;
        }
        // Until the editor chooses or types a source, it follows what is
        // being looked at: the topmost thing on the map (a dated image, a
        // lidar survey, else the basemap). Choosing or typing anything ends
        // that, e.g. a face read from a paper while a basemap is up.
        S.srcAuto = true;
        function autoSrc() {
            if (!S.srcAuto || srcBox.style.display !== 'none') return;
            var it = (GL.imagery ? GL.imagery() : [])[0];
            if (!it) return;
            var was = S.autoItem;
            el.fSrc.value = srcOf(it);
            el.fBasis.value = basisOf(it);
            // the date follows too, unless one has been typed
            if (!el.fDate.value.trim() || (was && el.fDate.value === (was.date || ''))) el.fDate.value = it.date || '';
            S.autoItem = it;
            fillFrom();
        }
        S.autoSrc = autoSrc;
        el.fBasis.addEventListener('change', function () { S.srcAuto = false; });
        el.fFrom.addEventListener('mousedown', fillFrom);
        el.fFrom.addEventListener('focus', fillFrom);
        el.fFrom.addEventListener('change', function () {
            var v = el.fFrom.value;
            S.srcAuto = false;
            if (v === NEWSRC) {
                srcBox.style.display = ''; el.fSrc.value = ''; el.fSrc.focus(); return;
            }
            srcBox.style.display = 'none';
            if (v === '') { el.fSrc.value = ''; return; }
            if (v.charAt(0) === 'u') {
                el.fSrc.value = S.usedSrc[+v.slice(1)] || '';
                if (S.srcBasis[el.fSrc.value]) el.fBasis.value = S.srcBasis[el.fSrc.value];
                return;
            }
            var it = S.fromItems[+v.slice(1)];
            if (!it) return;
            el.fDate.value = it.date || '';
            el.fBasis.value = basisOf(it);
            el.fSrc.value = srcOf(it);
            if (!it.date) say('That layer carries no date: type the date of what you are tracing.', true);
        });
        // The typed source joins the list and becomes the choice.
        function commitSrc() {
            if (srcBox.style.display === 'none') return;
            var t = el.fSrc.value.trim().slice(0, 300);
            el.fSrc.value = t;
            srcBox.style.display = 'none';
            if (t && S.srcAdded.indexOf(t) < 0) S.srcAdded.push(t);
            fillFrom();
        }
        el.fSrc.addEventListener('change', commitSrc);
        el.fSrc.addEventListener('blur', commitSrc);
        el.fSrc.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); commitSrc(); }
            e.stopPropagation();
        });
        S.fillFrom = fillFrom;
        el.extend.addEventListener('click', function () {
            if (window.LSScarps.extendSelected()) {
                say('Extending: click onward from the end of the line (the dot); right-click or Enter ends it.');
            }
        });
        el.done.addEventListener('click', stopEdit);
        el.name.addEventListener('change', function () { saveField({ name: el.name.value }); });
        el.date.addEventListener('change', function () { saveField({ date: el.date.value }); });
        el.basis.addEventListener('change', function () { saveField({ basis: el.basis.value }); });
        el.srcNote.addEventListener('change', function () { saveField({ source_note: el.srcNote.value }); });
        el.notes.addEventListener('change', function () { saveField({ notes: el.notes.value }); });
        el.kindSel.addEventListener('change', function () { saveField({ kind: el.kindSel.value }); });
        el.reverse.addEventListener('click', function () { saveField({ reverse: true }); });
        el.plot.addEventListener('click', function () { var f = sel(); if (f) showPath(f.properties.id); });
        el.del.addEventListener('click', function () {
            var f = sel();
            if (!f) return;
            // Two-step, in the page: no browser confirm dialog.
            if (el.del.getAttribute('data-arm') !== String(f.properties.id)) {
                el.del.setAttribute('data-arm', f.properties.id);
                el.del.textContent = 'Click again to delete line #' + f.properties.id;
                setTimeout(function () { el.del.removeAttribute('data-arm'); el.del.textContent = 'Delete whole line'; }, 4000);
                return;
            }
            el.del.removeAttribute('data-arm'); el.del.textContent = 'Delete whole line';
            window.LSScarps.remove().then(function () { say('Deleted.'); afterChange(); })
                .catch(function (e) { say(e.message, true); });
        });
        el.file.addEventListener('change', function () {
            var f = el.file.files && el.file.files[0];
            if (!f) return;
            f.text().then(function (txt) {
                var body;
                try { body = JSON.parse(txt); } catch (e) { throw new Error('that file is not GeoJSON'); }
                return post(API + 'api/import/', body);
            }).then(function (d) {
                say(d.created + ' face line' + (d.created === 1 ? '' : 's') + ' imported' +
                    (d.skipped.length ? '; skipped: ' + d.skipped.slice(0, 3).join('; ') : '.'), d.skipped.length > 0);
                return window.LSScarps.load();
            }).then(afterChange).catch(function (e) { say(e.message, true); });
            el.file.value = '';
        });
    }

    // Buttons on the selected glacier's card (editors).
    function cardHook(card, selData) {
        if (!CFG.editor || !selData) return;
        var g = selData.glacier;
        var lines = window.LSScarps.all().features.filter(function (f) {
            return f.properties.glacier_id === g.id && f.properties.kind === 'flowline';
        });
        var box = document.createElement('div');
        box.className = 'nw-card';
        var main = lines.filter(function (f) { return f.properties.is_main; })[0];
        box.innerHTML = lines.length
            ? '<span>' + lines.length + ' flow line' + (lines.length === 1 ? '' : 's') + ' in the network</span>' +
              (main ? '<button type="button" data-a="plot">Plot faces along it</button>' : '') +
              '<button type="button" data-a="draft" title="A first guess at the line beyond today\'s terminus, from the midpoints of the mirrored faces. To be corrected by hand.">Draft the former bed</button>'
            : '<button type="button" data-a="adopt" title="Copy this glacier\'s RGI 7.0 centrelines into the network, where they can be edited, joined and extended">Add its centrelines to the network</button>';
        var out = document.createElement('div');
        out.className = 'rt-sub';
        box.appendChild(out);
        Array.prototype.forEach.call(box.querySelectorAll('button'), function (b) {
            b.addEventListener('click', function () {
                var a = b.getAttribute('data-a');
                if (a === 'plot') { showPath(main.properties.id); return; }
                b.disabled = true;
                post(API + 'api/' + a + '/', { glacier_id: g.id }).then(function (d) {
                    return window.LSScarps.load().then(function () {
                        afterChange();
                        cardHook.rerender();
                        if (a === 'draft' && d.id) showPath(lines.filter(function (f) { return f.properties.is_main; })[0].properties.id);
                    });
                }).catch(function (e) { out.textContent = e.message; out.style.color = '#b71c1c'; b.disabled = false; });
            });
        });
        var head = card.querySelector('.rt-ids');
        if (head) head.parentNode.insertBefore(box, head.nextSibling);
    }

    function init(gl) {
        GL = gl; map = gl.map; CFG = gl.CFG;
        el.chart = $('nw-chart');
        var X = GL.hash.extras || {};
        // Until the network has loaded, a link's np= is carried, not dropped.
        var np0 = (X.np != null && isFinite(+X.np)) ? String(+X.np) : null;
        GL.addHashExtras(function () {
            return { np: S.pathId != null ? String(S.pathId) : (S.ready ? null : np0) };
        }, ['np']);
        GL.onTab(function () {
            if (S.editing && GL.tab() === 'flow') stopEdit();
            if (S.editing && S.autoSrc) S.autoSrc();     // back from Reference maps: the basemap may have changed
            applyVis();
        });
        (GL.layerHooks = GL.layerHooks || []).push(addLayers);
        (GL.cardHooks = GL.cardHooks || []).push(cardHook);
        cardHook.rerender = function () { if (window.GLRetreat && window.GLRetreat.rerenderCard) window.GLRetreat.rerenderCard(); };
        var refCb = $('nw-ref');
        if (refCb) refCb.addEventListener('change', function () { S.refOn = refCb.checked; applyVis(); });
        // Not before the style has loaded: the editor adds its layers as soon
        // as its list arrives, and on a fast answer that beat the style and
        // threw, leaving this module half started.
        function boot() {
          window.LSScarps.init({
            map: map, api: API, csrf: csrf,
            // Fires on every redraw, including after a dragged vertex is
            // saved, which is the one edit that reports nowhere else: so
            // while editing, the end dots and the open plot follow it.
            onChange: function () {
                syncPanel();
                if (S.editing) { clearTimeout(S._chg); S._chg = setTimeout(afterChange, 700); }
            },
            // Outside a session a click on a flow line plots its path; on a
            // face it says what the face is.
            onPick: function (f, lngLat) {
                var p = f.properties;
                if (p.kind === 'flowline') { showPath(p.id); return; }
                window.LSTools.popup.show(new maplibregl.Popup({ maxWidth: '300px' }).setLngLat(lngLat).setHTML(
                    '<div class="rt-pop"><b>Ice face</b> (ours)<div>' + esc(p.date || 'undated') +
                    (p.basis ? ' · from ' + esc(p.basis) : '') + '</div>' +
                    (p.source_note ? '<div>' + esc(p.source_note) + '</div>' : '') +
                    (p.notes ? '<div class="rt-sub">' + esc(p.notes) + '</div>' : '') +
                    '<div class="rt-sub">traced by ' + esc(p.traced_by) + '</div></div>').addTo(map));
            },
            flash: function (msg, bad) { say(msg, bad); afterChange(); },
            // A face traced with the tracing block is saved with its date,
            // basis and source in the same request.
            drawFields: function (kind) {
                if (kind !== 'face' || !el.fDate) return null;
                return { date: el.fDate.value.trim(), basis: el.fBasis.value, source_note: el.fSrc.value.trim() };
            },
            // Say where the new face landed (the same rule the plot uses),
            // and stay armed for the next one if asked.
            onCreated: function (res, kind) {
                if (kind !== 'face') return;
                var pl = res.placement, d = el.fDate.value.trim(), bad = false, msg = 'Face #' + res.id + ' saved';
                if (res.warning) { msg += ', ' + res.warning; bad = true; }
                else if (!d) { msg += ' with no date (it will not plot until it has one)'; bad = true; }
                else msg += ', dated ' + d;
                var nm = pl && (pl.name || ('flow line #' + pl.line_id));
                if (!pl) { msg += '. No flow line is near it, so it is on no plot: draw or extend a flow line through it.'; bad = true; }
                else if (pl.how === 'crossing') msg += '. It crosses ' + nm + ', ' + pl.km_from_head.toFixed(2) + ' km from the head of that line.';
                else if (pl.how === 'projected') msg += '. It stops ' + pl.dist_m + ' m short of ' + nm + ' and is projected onto it, ' + pl.km_from_head.toFixed(2) + ' km from its head.';
                else { msg += '. The nearest flow line (' + nm + ') is ' + (pl.dist_m / 1000).toFixed(1) + ' km away or the face lies beyond its end, so it is on no plot: extend the flow line through it.'; bad = true; }
                var again = el.fKeep && el.fKeep.checked && S.editing;
                say(msg + (again ? ' Ready for the next face (Esc stops).' : ''), bad);
                if (again) {
                    window.LSScarps.setDrawKind('face');
                    window.LSScarps.armDraw(true);
                }
            }
        }).then(function () {
            S.ready = true;
            if (map.getSource('gx')) addLayers();
            applyVis();
            cardHook.rerender();
            if (X.np != null && isFinite(+X.np)) showPath(+X.np);
          }).catch(function (e) { console.warn('flow network unavailable:', e); });
        }
        if (map.isStyleLoaded()) boot();
        else {
            var done = false, once = function () { if (!done) { done = true; boot(); } };
            map.once('load', once); map.once('idle', once);
        }
        if (CFG.editor && $('nw-panel')) wireEditor();
    }

    return { init: init, showPath: showPath,
             // redraw the open plot (a video was linked or changed)
             refresh: function () { if (S.pathId != null) showPath(S.pathId); } };
})();
