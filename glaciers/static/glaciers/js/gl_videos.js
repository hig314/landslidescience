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
 *   - GLVideos.open(id, lngLat) for the distance plot's video marks;
 *   - for a video PLACED from its content (v.georef: tools/glaciers/
 *     s1_georef/, docs/GLACIERS.md), a faint box where the frame lies, with
 *     a corner dot; hovering the edge lights it, clicking the edge or dot
 *     offers the video or one of its static products (median, brightness
 *     range, variability, sign persistence, edge strength) drawn in place on
 *     the map, and zooms to it. One overlay at a time, with a small panel
 *     (product, opacity, play/pause and a time slider for the video).
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
        if (!map.getSource('gv-foot')) {
            map.addSource('gv-foot', { type: 'geojson', data: footData() });
            map.addSource('gv-corner', { type: 'geojson', data: cornerData() });
            map.addLayer({ id: 'gv-foot', type: 'line', source: 'gv-foot',
                           paint: { 'line-color': '#00897b', 'line-width': 1.2, 'line-opacity': 0.5, 'line-dasharray': [3, 2] } });
            map.addLayer({ id: 'gv-foot-hi', type: 'line', source: 'gv-foot', filter: ['==', ['get', 'video_id'], -1],
                           paint: { 'line-color': '#00bfa5', 'line-width': 3, 'line-opacity': 0.95 } });
            // the clickable edge: wider than the drawn one, all but invisible
            map.addLayer({ id: 'gv-foot-hit', type: 'line', source: 'gv-foot',
                           paint: { 'line-color': '#000', 'line-width': 12, 'line-opacity': 0.004 } });
            map.addLayer({ id: 'gv-corner', type: 'circle', source: 'gv-corner',
                           paint: { 'circle-radius': 4.5, 'circle-color': '#00897b', 'circle-opacity': 0.8,
                                    'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.5 } });
            // handlers belong to the map and outlive a style change: once only
            if (!hoverWired) ['gv-foot-hit', 'gv-corner'].forEach(function (id) {
                map.on('mousemove', id, function (e) {
                    window.LSTools.cursor.set('hover', 'pointer');
                    hover(e.features && e.features[0] ? e.features[0].properties.video_id : null);
                });
                map.on('mouseleave', id, function () { window.LSTools.cursor.clear('hover'); hover(null); });
            });
            hoverWired = true;
        }
        if (OV.id != null && !map.getSource('gv-ov')) showOverlay(OV.id, OV.product, false);
        if (map.getLayer('gv-pt')) map.moveLayer('gv-pt');
        applyVis();
    }
    function applyVis() {
        var v = visible();
        ['gv-seg', 'gv-pt', 'gv-pick', 'gv-foot', 'gv-foot-hi', 'gv-foot-hit', 'gv-corner', 'gv-ov'].forEach(function (id) {
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
        if (map.getSource('gv-foot')) map.getSource('gv-foot').setData(footData());
        if (map.getSource('gv-corner')) map.getSource('gv-corner').setData(cornerData());
    }

    // ------------------------------------------------------------------
    // Placed videos: the footprint boxes, and one overlay drawn in place
    // ------------------------------------------------------------------
    var OV = { id: null, product: null, opacity: 0.9 }, ovEl = null, hoverId = null, hoverWired = false;
    function placed() { return D.videos.filter(function (v) { return v.georef && v.media === 'video'; }); }
    function corners(b) { return [[b[0], b[3]], [b[2], b[3]], [b[2], b[1]], [b[0], b[1]]]; }
    function footData() {
        return { type: 'FeatureCollection', features: placed().map(function (v) {
            var c = corners(v.georef.bounds);
            return { type: 'Feature', geometry: { type: 'Polygon', coordinates: [c.concat([c[0]])] },
                     properties: { video_id: v.id } };
        }) };
    }
    function cornerData() {
        return { type: 'FeatureCollection', features: placed().map(function (v) {
            var b = v.georef.bounds;
            return { type: 'Feature', geometry: { type: 'Point', coordinates: [b[0], b[3]] }, properties: { video_id: v.id } };
        }) };
    }
    function hover(id) {
        if (id === hoverId) return;
        hoverId = id;
        if (map.getLayer('gv-foot-hi')) map.setFilter('gv-foot-hi', ['==', ['get', 'video_id'], id == null ? -1 : id]);
    }
    function fitTo(v) {
        var b = v.georef.bounds;
        map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, duration: 600 });
    }
    // Directly above the topmost raster (basemap, relief, ice cover and the
    // other overlays, which are rasters with see-through parts), so lines,
    // dots and labels above it (outlines, flow lines, faces) stay on top.
    function beforeVector() {
        var L = map.getStyle().layers, last = -1;
        for (var i = 0; i < L.length; i++) {
            if (L[i].id !== 'gv-ov' && ['raster', 'background', 'hillshade', 'color-relief'].indexOf(L[i].type) >= 0) last = i;
        }
        for (var j = last + 1; j < L.length; j++) if (L[j].id !== 'gv-ov') return L[j].id;
        return undefined;
    }
    function clearOverlay() {
        if (map.getLayer('gv-ov')) map.removeLayer('gv-ov');
        if (map.getSource('gv-ov')) map.removeSource('gv-ov');
        OV.id = null; OV.product = null;
        ovPanel();
    }
    // product: 'video' or a georef product id
    function showOverlay(id, product, fit) {
        var v = byId[id];
        if (!v || !v.georef) return;
        if (map.getLayer('gv-ov')) map.removeLayer('gv-ov');
        if (map.getSource('gv-ov')) map.removeSource('gv-ov');
        OV.id = id; OV.product = product;
        var coords = corners(v.georef.bounds);
        if (product === 'video') {
            map.addSource('gv-ov', { type: 'video', urls: [v.src], coordinates: coords });
        } else {
            var p = v.georef.products.filter(function (x) { return x.id === product; })[0];
            if (!p) { clearOverlay(); return; }
            map.addSource('gv-ov', { type: 'image', url: p.src, coordinates: coords });
        }
        map.addLayer({ id: 'gv-ov', type: 'raster', source: 'gv-ov',
                       layout: { visibility: visible() ? 'visible' : 'none' },
                       paint: { 'raster-opacity': OV.opacity, 'raster-fade-duration': 0 } }, beforeVector());
        if (fit) fitTo(v);
        ovPanel();
    }
    function ovVideo() {
        var src = map.getSource('gv-ov');
        return src && src.getVideo ? src.getVideo() : null;
    }
    function ovPanel() {
        if (!ovEl) {
            ovEl = document.createElement('div');
            ovEl.className = 'gv-ovp';
            map.getContainer().appendChild(ovEl);
        }
        var v = OV.id != null ? byId[OV.id] : null;
        if (!v || !visible()) { ovEl.style.display = 'none'; ovEl.innerHTML = ''; return; }
        ovEl.style.display = '';
        var opts = [{ id: 'video', label: 'Video', title: 'The timelapse itself, playing in place' }].concat(v.georef.products);
        ovEl.innerHTML =
            '<div class="gv-ovh"><b title="' + esc(v.caption || '') + '">' + esc((v.caption || v.kind_label).slice(0, 40)) + '</b>' +
            '<button type="button" data-a="close" title="Remove from the map">×</button></div>' +
            '<div class="gv-ovb">' + opts.map(function (o) {
                return '<button type="button" data-p="' + o.id + '" title="' + esc(o.title || '') + '"' +
                    (o.id === OV.product ? ' class="on"' : '') + '>' + esc(o.label) + '</button>';
            }).join('') + '</div>' +
            (OV.product === 'video'
                ? '<div class="gv-ovv"><button type="button" data-a="play" title="Play or pause">❚❚</button>' +
                  '<input type="range" data-a="seek" min="0" max="1" step="0.001" value="0" title="Position in the series"></div>'
                : '') +
            '<div class="gv-ovv"><span>Opacity</span><input type="range" data-a="op" min="0.1" max="1" step="0.05" value="' + OV.opacity + '">' +
            '<button type="button" data-a="zoom" title="Zoom to the video">Zoom</button></div>' +
            (v.georef.grade && v.georef.grade !== 'good'
                ? '<div class="rt-sub">Placed from its content, ' + esc(v.georef.grade) + ' match: check the fit against the outlines.</div>' : '');
        Array.prototype.forEach.call(ovEl.querySelectorAll('[data-p]'), function (b) {
            b.addEventListener('click', function () { showOverlay(OV.id, b.getAttribute('data-p'), false); });
        });
        ovEl.querySelector('[data-a=close]').addEventListener('click', clearOverlay);
        ovEl.querySelector('[data-a=zoom]').addEventListener('click', function () { fitTo(v); });
        ovEl.querySelector('[data-a=op]').addEventListener('input', function (e) {
            OV.opacity = +e.target.value;
            if (map.getLayer('gv-ov')) map.setPaintProperty('gv-ov', 'raster-opacity', OV.opacity);
        });
        var play = ovEl.querySelector('[data-a=play]'), seek = ovEl.querySelector('[data-a=seek]');
        if (play) {
            play.addEventListener('click', function () {
                var vid = ovVideo(); if (!vid) return;
                if (vid.paused) { vid.play(); } else { vid.pause(); }
            });
            seek.addEventListener('input', function () {
                var vid = ovVideo(); if (!vid || !vid.duration) return;
                vid.pause(); vid.currentTime = (+seek.value) * vid.duration;
                map.triggerRepaint();
            });
            // the video element exists once the source has loaded it
            (function hook(tries) {
                var vid = ovVideo();
                if (!vid) { if (tries < 50 && OV.product === 'video') setTimeout(function () { hook(tries + 1); }, 200); return; }
                vid.muted = true; vid.loop = true;
                var p = vid.play(); if (p && p.catch) p.catch(function () {});
                function upd() {
                    if (!ovEl.contains(play)) return;
                    play.textContent = vid.paused ? '▶' : '❚❚';
                    if (vid.duration && !vid.paused) seek.value = vid.currentTime / vid.duration;
                }
                vid.addEventListener('timeupdate', upd); vid.addEventListener('play', upd); vid.addEventListener('pause', upd);
                upd();
            })(0);
        }
    }
    // The menu a footprint's edge or corner opens: the video, or a product.
    function menu(ids, lngLat) {
        ids = ids.filter(function (i) { return byId[i] && byId[i].georef; });
        if (!ids.length) return;
        var box = document.createElement('div');
        box.className = 'gv-pop';
        box.innerHTML = ids.map(function (id) {
            var v = byId[id];
            return '<div class="gv-menu"><b>' + esc(v.caption || v.kind_label) + '</b>' +
                '<div class="rt-sub">' + esc(v.kind_label) + (v.date ? ' · ' + esc(v.date) : '') + '</div>' +
                '<div class="gv-ovb">' + [{ id: 'video', label: '▶ Video', title: 'Play it in place on the map' }].concat(v.georef.products).map(function (o) {
                    return '<button type="button" data-v="' + id + '" data-p="' + o.id + '" title="' + esc(o.title || '') + '">' + esc(o.label) + '</button>';
                }).join('') + '<button type="button" data-v="' + id + '" data-p="box" title="Play it in this box instead">In a box</button></div></div>';
        }).join('');
        var pop = new maplibregl.Popup({ maxWidth: '360px' }).setLngLat(lngLat).setDOMContent(box).addTo(map);
        Array.prototype.forEach.call(box.querySelectorAll('[data-p]'), function (b) {
            b.addEventListener('click', function () {
                var id = +b.getAttribute('data-v'), p = b.getAttribute('data-p');
                pop.remove();
                if (p === 'box') open([id], lngLat);
                else showOverlay(id, p, true);
            });
        });
        window.LSTools.popup.show(pop);
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
        if (v.georef && v.media === 'video') {
            h += '<div class="gv-ovb"><span class="rt-sub">On the map:</span>' +
                [{ id: 'video', label: 'Video' }].concat(v.georef.products).map(function (o) {
                    return '<button type="button" class="gv-onmap" data-v="' + v.id + '" data-p="' + o.id + '" title="' + esc(o.title || 'Play it in place on the map') + '">' + esc(o.label) + '</button>';
                }).join('') + '</div>';
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
            Array.prototype.forEach.call(box.querySelectorAll('.gv-onmap'), function (b) {
                b.addEventListener('click', function () {
                    window.LSTools.popup.close();
                    showOverlay(+b.getAttribute('data-v'), b.getAttribute('data-p'), true);
                });
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
        GL.onTab(function () { if (S.open && GL.tab() === 'flow') stop(); applyVis(); ovPanel(); });
        window.LSTools.clicks.register({
            id: 'gl-video', kind: 'reference', layers: ['gv-pt'], priority: 50,
            handler: function (f, e, hits) {
                var ids = [];
                (hits || []).forEach(function (h) { if (ids.indexOf(h.properties.video_id) < 0) ids.push(h.properties.video_id); });
                open(ids, e.lngLat);
            }
        });
        // a footprint's edge or corner dot; below the segment markers (50),
        // above the network's own lines (40)
        window.LSTools.clicks.register({
            id: 'gl-video-foot', kind: 'reference', layers: ['gv-corner', 'gv-foot-hit'], priority: 45,
            handler: function (f, e, hits) {
                var ids = [];
                (hits || []).forEach(function (h) { if (ids.indexOf(h.properties.video_id) < 0) ids.push(h.properties.video_id); });
                menu(ids, e.lngLat);
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

    return { init: init, open: open, reload: load, overlay: showOverlay, clearOverlay: clearOverlay };
})();
