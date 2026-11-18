/* gl_videos.js — timelapse videos linked to segments of the flow network.
 *
 * A video (a Planet story, or an uploaded Sentinel-1 animation) is attached
 * to one or more SEGMENTS, the stretch of a flow line between two junctions;
 * the server works the segments out from stored anchor points
 * (glaciers/videos.py, which says why). This module:
 *   - draws each linked segment as a coloured band with a round marker at
 *     its middle, and plays the video in a popup when one is clicked;
 *   - for editors, the "Link a video" panel: choose the kind, give the link
 *     or the file, then click every segment the video shows. Editing an
 *     existing video adds or removes segments without the file being sent
 *     again;
 *   - GLVideos.open(id, lngLat) for the distance plot's video marks.
 *
 * Clicks go through LSTools: a 'reference' entry above the network's own
 * (so a marker click plays the video rather than plotting the path), and,
 * while picking, an 'action' entry owned by the 'glvideo' hold.
 */
window.GLVideos = (function () {
    'use strict';

    var GL, map, CFG, API = '/glaciers/lines/';
    var D = { videos: [], kinds: [], segments: null, points: null }, byId = {};
    var S = { open: false, editId: null, picks: [] };       // picks: [{key, anchor, name, length_m, geometry}]
    var el = {};
    var EMPTY = { type: 'FeatureCollection', features: [] };

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function visible() { return GL.tab() !== 'flow'; }
    function say(msg, bad) {
        if (!el.msg) return;
        el.msg.textContent = msg || '';
        el.msg.style.color = bad ? '#b71c1c' : '#2e7d32';
    }

    // ------------------------------------------------------------------
    // Map layers
    // ------------------------------------------------------------------
    function addLayers() {
        if (!map.getSource('gv-seg')) {
            map.addSource('gv-seg', { type: 'geojson', data: D.segments || EMPTY });
            map.addLayer({ id: 'gv-seg', type: 'line', source: 'gv-seg',
                           layout: { 'line-cap': 'round', 'line-join': 'round' },
                           paint: { 'line-color': ['get', 'color'], 'line-width': 11, 'line-opacity': 0.32 } });
        }
        if (!map.getSource('gv-pick')) {
            map.addSource('gv-pick', { type: 'geojson', data: pickData() });
            map.addLayer({ id: 'gv-pick', type: 'line', source: 'gv-pick',
                           layout: { 'line-cap': 'round', 'line-join': 'round' },
                           paint: { 'line-color': '#ff4081', 'line-width': 9, 'line-opacity': 0.75 } });
        }
        if (!map.getSource('gv-pt')) {
            map.addSource('gv-pt', { type: 'geojson', data: D.points || EMPTY });
            map.addLayer({ id: 'gv-pt', type: 'circle', source: 'gv-pt',
                           paint: { 'circle-radius': 7, 'circle-color': ['get', 'color'],
                                    'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2 } });
            map.on('mouseenter', 'gv-pt', function () { window.LSTools.cursor.set('hover', 'pointer'); });
            map.on('mouseleave', 'gv-pt', function () { window.LSTools.cursor.clear('hover'); });
        }
        if (map.getLayer('gv-pt')) map.moveLayer('gv-pt');
        applyVis();
    }
    function applyVis() {
        var v = visible();
        ['gv-seg', 'gv-pt', 'gv-pick'].forEach(function (id) {
            if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', v ? 'visible' : 'none');
        });
    }
    function pickData() {
        return { type: 'FeatureCollection', features: S.picks.map(function (p) {
            return { type: 'Feature', geometry: p.geometry, properties: {} };
        }) };
    }
    function redraw() {
        if (map.getSource('gv-seg')) map.getSource('gv-seg').setData(D.segments || EMPTY);
        if (map.getSource('gv-pt')) map.getSource('gv-pt').setData(D.points || EMPTY);
        if (map.getSource('gv-pick')) map.getSource('gv-pick').setData(pickData());
    }
    function load() {
        return fetch(API + 'api/videos/').then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        }).then(function (d) {
            var was = {};
            Object.keys(byId).forEach(function (k) { was[k] = byId[k].state; });
            D = d; byId = {};
            d.videos.forEach(function (v) { byId[v.id] = v; });
            redraw();
            // A video that was still being copied here has arrived (or
            // failed): show it in the popup that was waiting for it.
            if (shown && byId[shown.id] && was[shown.id] === 'archiving' && byId[shown.id].state !== 'archiving') {
                open(shown.ids, shown.lngLat, shown.id);
            }
            watch();
            return d;
        }).catch(function (e) { console.error('videos failed to load:', e); });
    }

    // While any video is still being copied here (a Planet story being
    // archived, a Drive link being fetched), ask again every few seconds, so
    // it becomes playable without a reload. Gives up after ten minutes.
    var shown = null, watchT = null, watchUntil = 0;
    function watch() {
        var pending = D.videos.some(function (v) { return v.state === 'archiving'; });
        if (!pending) { watchUntil = 0; return; }
        if (!watchUntil) watchUntil = Date.now() + 600000;
        if (watchT || Date.now() > watchUntil) return;
        watchT = setTimeout(function () { watchT = null; load(); }, 4000);
    }

    // ------------------------------------------------------------------
    // Playing one
    // ------------------------------------------------------------------
    function player(v) {
        var h = '<div class="gv-pop"><b>' + esc(v.caption || v.kind_label) + '</b>' +
            '<div class="rt-sub">' + esc(v.kind_label) + (v.date ? ' · ' + esc(v.date) : '') + '</div>';
        if (v.media === 'video') {
            h += '<video src="' + esc(v.src) + '" controls loop muted autoplay playsinline></video>';
        } else if (v.media === 'image') {
            h += '<img src="' + esc(v.src) + '" alt="">';
        } else if (v.state === 'comparison') {
            h += '<div class="rt-sub">A before/after comparison, which Planet does not offer as a file.</div>';
        } else if (v.state === 'failed') {
            h += '<div class="rt-sub" style="color:#b71c1c">The video could not be copied here: ' + esc(v.error) +
                ' Delete this entry and link it again.</div>';
        } else {
            h += '<div class="rt-sub">The video is being copied here; it will appear in this box when it is ready.</div>';
        }
        if (v.link) h += '<div><a href="' + esc(v.link) + '" target="_blank" rel="noopener">Open on planet.com</a></div>';
        h += '<div class="rt-sub">Shows: ' + v.segments.map(function (s) {
            return esc(s.name || ('line ' + s.line_id)) + ' (' + (s.length_m / 1000).toFixed(1) + ' km)';
        }).join(', ') + '</div>';
        if (CFG.editor) h += '<div><button type="button" class="gv-edit" data-v="' + v.id + '">Edit segments or details</button></div>';
        return h + '</div>';
    }
    function open(ids, lngLat, first) {
        ids = [].concat(ids).filter(function (i) { return byId[i]; });
        if (!ids.length) return;
        var box = document.createElement('div');
        function show(id) {
            if (shown) shown.id = id;
            var tabs = ids.length > 1 ? '<div class="gv-tabs">' + ids.map(function (i) {
                return '<button type="button" data-t="' + i + '"' + (i === id ? ' class="on"' : '') + '>' +
                    esc((byId[i].caption || byId[i].kind_label).slice(0, 28)) + '</button>';
            }).join('') + '</div>' : '';
            box.innerHTML = tabs + player(byId[id]);
            Array.prototype.forEach.call(box.querySelectorAll('[data-t]'), function (b) {
                b.addEventListener('click', function () { show(+b.getAttribute('data-t')); });
            });
            var ed = box.querySelector('.gv-edit');
            if (ed) ed.addEventListener('click', function () { window.LSTools.popup.close(); start(id); });
        }
        if (!lngLat) { var m = byId[ids[0]].segments[0].mid; lngLat = { lng: m[0], lat: m[1] }; }
        var me = shown = { ids: ids, lngLat: lngLat, id: null };
        show(first != null && ids.indexOf(first) >= 0 ? first : ids[0]);
        var pop = new maplibregl.Popup({ maxWidth: '440px' }).setLngLat(lngLat).setDOMContent(box).addTo(map);
        pop.on('close', function () { if (shown === me) shown = null; });
        window.LSTools.popup.show(pop);
    }

    // ------------------------------------------------------------------
    // The editor's panel: one video, and the segments it shows
    // ------------------------------------------------------------------
    function kind() {
        var id = S.editId ? byId[S.editId].kind : el.kind.value;
        return D.kinds.filter(function (k) { return k.id === id; })[0] || { upload: false };
    }
    function sync() {
        if (!el.panel) return;
        el.panel.style.display = S.open ? '' : 'none';
        if (!S.open) return;
        var editing = S.editId != null, k = kind();
        el.title.textContent = editing ? 'Video #' + S.editId + ' (' + byId[S.editId].kind_label + ')' : 'Link a video to the network';
        el.kindRow.style.display = editing ? 'none' : '';
        el.urlRow.style.display = (!editing && !k.upload) ? '' : 'none';
        el.fileRow.style.display = (!editing && k.upload) ? '' : 'none';
        el.del.style.display = editing ? '' : 'none';
        el.segs.innerHTML = S.picks.length
            ? S.picks.map(function (p, i) {
                return '<div class="gv-seg"><span>' + esc(p.name || 'flow line') + ' · ' + (p.length_m / 1000).toFixed(1) +
                    ' km</span><button type="button" data-i="' + i + '" title="Remove this segment">×</button></div>';
            }).join('')
            : '<div class="rt-none">No segment chosen yet.</div>';
        Array.prototype.forEach.call(el.segs.querySelectorAll('button'), function (b) {
            b.addEventListener('click', function () { S.picks.splice(+b.getAttribute('data-i'), 1); sync(); redraw(); });
        });
    }
    function segmentAt(lngLat) {
        return fetch(API + 'api/segment/?lon=' + lngLat.lng.toFixed(6) + '&lat=' + lngLat.lat.toFixed(6))
            .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || 'HTTP ' + r.status); return d; }); });
    }
    function onPickClick(e) {
        if (!S.open) return;
        segmentAt(e.lngLat).then(function (s) {
            var at = -1;
            S.picks.forEach(function (p, i) { if (p.key === s.key) at = i; });
            if (at >= 0) { S.picks.splice(at, 1); say('Removed ' + (s.name || 'that segment') + '.'); }
            else { S.picks.push(s); say('Added ' + (s.name || 'a segment') + ', ' + (s.length_m / 1000).toFixed(1) + ' km between junctions.'); }
            sync(); redraw();
        }).catch(function (err) { say(err.message + '. Click on a flow line.', true); });
    }
    function start(id) {
        if (!CFG.editor || !el.panel) return;
        if (!window.LSTools.claim('glvideo')) return;
        if (GL.tab() === 'flow') GL.showTab('retreat', true);
        S.open = true; S.editId = id || null; S.picks = [];
        el.url.value = ''; el.file.value = ''; el.drive.value = ''; el.cap.value = ''; el.date.value = '';
        el.del.textContent = 'Delete video'; el.del.dataset.sure = '';
        say('Click each segment of the network this video shows; click one again to remove it. Esc cancels.');
        if (id) {
            var v = byId[id];
            el.cap.value = v.caption; el.date.value = v.date;
            // the stored segments, with their geometry, from the anchors
            Promise.all(v.segments.map(function (s) { return segmentAt({ lng: s.anchor[0], lat: s.anchor[1] }); }))
                .then(function (segs) { if (S.editId === id) { S.picks = segs; sync(); redraw(); } })
                .catch(function (e) { say(e.message, true); });
        }
        window.LSTools.cursor.set('tool', 'crosshair');
        sync(); redraw();
    }
    function stop() {
        if (!S.open) return;
        S.open = false; S.editId = null; S.picks = [];
        window.LSTools.cursor.clear('tool');
        window.LSTools.release('glvideo');
        sync(); redraw();
    }
    function send(fd) {
        return fetch(API + 'api/videos/save/', { method: 'POST', headers: { 'X-CSRFToken': CFG.csrf }, body: fd })
            .then(function (r) {
                return r.json().catch(function () { throw new Error('HTTP ' + r.status + ' (signed in? file too large?)'); })
                    .then(function (d) { if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status)); return d; });
            });
    }
    function changed() {
        return load().then(function () { if (window.GLNetwork && window.GLNetwork.refresh) window.GLNetwork.refresh(); });
    }
    function save() {
        if (!S.picks.length) { say('Choose at least one segment: click a flow line on the map.', true); return; }
        var fd = new FormData(), k = kind();
        if (S.editId) fd.append('id', S.editId);
        else {
            fd.append('kind', el.kind.value);
            if (k.upload) {
                if (el.file.files.length) fd.append('file', el.file.files[0]);
                else if (el.drive.value.trim()) fd.append('url', el.drive.value.trim());
                else { say('Choose the video file, or paste its Google Drive share link.', true); return; }
            } else fd.append('url', el.url.value.trim());
        }
        fd.append('caption', el.cap.value.trim());
        fd.append('date', el.date.value.trim());
        fd.append('points', JSON.stringify(S.picks.map(function (p) { return p.anchor; })));
        el.save.disabled = true;
        say(k.upload && !S.editId ? 'Uploading…' : 'Saving…');
        send(fd).then(function (d) {
            el.save.disabled = false;
            var id = d.id;
            stop();
            return changed().then(function () { open(id); });
        }).catch(function (e) { el.save.disabled = false; say(e.message, true); });
    }
    function del() {
        if (!S.editId) return;
        if (!el.del.dataset.sure) {                 // two presses, no browser dialog
            el.del.dataset.sure = '1'; el.del.textContent = 'Press again to delete for good';
            return;
        }
        var fd = new FormData();
        fd.append('id', S.editId); fd.append('delete', '1');
        send(fd).then(function () { stop(); return changed(); }).catch(function (e) { say(e.message, true); });
    }
    function wireEditor() {
        ['panel', 'title', 'kind', 'kindRow', 'url', 'urlRow', 'file', 'drive', 'fileRow', 'cap', 'date', 'segs', 'msg', 'save', 'cancel', 'del']
            .forEach(function (k) { el[k] = $('nv-' + k); });
        window.LSTools.hold({ id: 'glvideo', onRelease: stop, cancel: stop });
        window.LSTools.clicks.register({ id: 'gl-video-pick', kind: 'action', holders: ['glvideo'],
                                         handler: function (f, e) { onPickClick(e); } });
        el.kind.addEventListener('change', sync);
        el.save.addEventListener('click', save);
        el.cancel.addEventListener('click', stop);
        el.del.addEventListener('click', del);
        Array.prototype.forEach.call(document.querySelectorAll('.nv-open'), function (b) {
            b.addEventListener('click', function () { start(null); });
        });
    }

    function init(gl) {
        GL = gl; map = gl.map; CFG = gl.CFG;
        (GL.layerHooks = GL.layerHooks || []).push(addLayers);
        GL.onTab(function () { if (S.open && GL.tab() === 'flow') stop(); applyVis(); });
        window.LSTools.clicks.register({
            id: 'gl-video', kind: 'reference', layers: ['gv-pt'], priority: 50,
            handler: function (f, e, hits) {
                var ids = [];
                (hits || []).forEach(function (h) { if (ids.indexOf(h.properties.video_id) < 0) ids.push(h.properties.video_id); });
                open(ids, e.lngLat);
            }
        });
        if (CFG.editor && $('nv-panel')) wireEditor();
        load().then(function (d) {
            if (d && el.kind) {
                el.kind.innerHTML = d.kinds.map(function (k) { return '<option value="' + k.id + '">' + esc(k.label) + '</option>'; }).join('');
            }
            if (map.isStyleLoaded()) addLayers();
        });
    }

    return { init: init, open: open, reload: load };
})();
