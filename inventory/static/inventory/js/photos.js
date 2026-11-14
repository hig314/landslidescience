/* photos.js — Field photos: detail-panel strip, lightbox, and map dots.
 *
 * Moved out of map.js on 2026-11-14 without changing its text. map.js calls
 * window.LSPhotos.init(ctx) once, at the point in its start-up where this code
 * used to sit, and passes in the few things it needs from the map page:
 *   map, esc.
 * It returns what map.js still uses: _photoShow, _photoOpenAt, photoStripHtml, _photoPtsSet.
 */
(function () {
    'use strict';

    window.LSPhotos = { init: function (ctx) {
        var map = ctx.map;
        var esc = ctx.esc;

        // ---------------------------------------------------------------------------
        // Field photos: detail-panel strip, lightbox, and map dots.
        // api_detail orders photos featured-first (editor-curated sort_order),
        // then uncurated chronologically — the strip and lightbox keep that order.
        // ---------------------------------------------------------------------------
        var _detailPhotos = [];        // photos of the currently-open landslide
        var PHOTO_STRIP_MAX = 8;       // thumbs shown before "Show all N"
        // What the detail panel asks of this section: show a record's photos
        // (strip index target + map dots), and open the lightbox at one of them.
        function _photoShow(pics) { _detailPhotos = pics; _photoPtsSet(pics); }
        function _photoOpenAt(i) { _photoLightbox.open(_detailPhotos, i); }

        function photoStripHtml(pics) {
            var html = '<div class="detail-section"><div class="detail-section-title">Field photos</div>' +
                       '<div class="photo-strip" style="display:flex;flex-wrap:wrap;gap:6px;">';
            pics.forEach(function (p, i) {
                var hidden = pics.length > PHOTO_STRIP_MAX && i >= PHOTO_STRIP_MAX;
                html += '<a href="#" class="photo-thumb" data-idx="' + i + '"' +
                        (hidden ? ' style="display:none;"' : '') +
                        ' title="' + esc(p.caption || 'Field photo') + '">' +
                        '<img src="' + esc(p.thumb_url) + '" loading="lazy" alt="' +
                        esc(p.caption || 'field photo') + '" style="width:72px;height:72px;' +
                        'object-fit:cover;border-radius:3px;display:block;background:#e8e4de;"></a>';
            });
            html += '</div>';
            if (pics.length > PHOTO_STRIP_MAX) {
                html += '<a href="#" class="photo-show-all" style="display:inline-block;' +
                        'font-size:11px;color:#1a5fb4;margin-top:5px;">Show all ' +
                        pics.length + ' photos</a>';
            }
            return html + '</div>';
        }

        // Fullscreen lightbox (built lazily, one instance). Arrows / swipe walk
        // the photo list in strip order; Esc or backdrop click closes.
        var _photoLightbox = (function () {
            var el, img, cap, credit, counter, fullLink, pics = [], idx = 0, touchX = null;

            function build() {
                el = document.createElement('div');
                el.style.cssText = 'position:fixed;inset:0;z-index:3000;display:none;' +
                    'flex-direction:column;align-items:center;justify-content:center;' +
                    'background:rgba(20,16,12,0.88);cursor:zoom-out;';
                img = document.createElement('img');
                img.style.cssText = 'max-width:92vw;max-height:78vh;object-fit:contain;' +
                    'border-radius:3px;box-shadow:0 4px 30px rgba(0,0,0,0.5);cursor:default;background:#222;';
                var meta = document.createElement('div');
                meta.style.cssText = 'max-width:92vw;color:#eee;text-align:center;' +
                    'font-size:13px;margin-top:10px;cursor:default;';
                cap = document.createElement('div');
                credit = document.createElement('div');
                credit.style.cssText = 'font-size:11px;color:#bbb;margin-top:3px;';
                meta.appendChild(cap); meta.appendChild(credit);

                function navBtn(label, dir, side) {
                    var b = document.createElement('button');
                    b.textContent = label;
                    b.title = dir > 0 ? 'Next photo' : 'Previous photo';
                    b.style.cssText = 'position:absolute;top:50%;transform:translateY(-50%);' +
                        side + ':10px;border:none;border-radius:50%;width:40px;height:40px;' +
                        'font-size:20px;background:rgba(255,255,255,0.15);color:#fff;cursor:pointer;';
                    b.addEventListener('click', function (e) { e.stopPropagation(); show(idx + dir); });
                    return b;
                }
                var prevBtn = navBtn('‹', -1, 'left'), nextBtn = navBtn('›', 1, 'right');

                var bar = document.createElement('div');
                bar.style.cssText = 'position:absolute;top:10px;right:14px;display:flex;' +
                    'gap:14px;align-items:center;font-size:12px;cursor:default;';
                counter = document.createElement('span');
                counter.style.color = '#ccc';
                fullLink = document.createElement('a');
                fullLink.textContent = 'full size ↗';
                fullLink.target = '_blank';
                fullLink.rel = 'noopener';
                fullLink.style.cssText = 'color:#9cc3ef;';
                var closeBtn = document.createElement('button');
                closeBtn.textContent = '✕';
                closeBtn.title = 'Close (Esc)';
                closeBtn.style.cssText = 'border:none;background:none;color:#fff;' +
                    'font-size:18px;cursor:pointer;padding:2px 6px;';
                closeBtn.addEventListener('click', close);
                bar.appendChild(counter); bar.appendChild(fullLink); bar.appendChild(closeBtn);

                el.appendChild(img); el.appendChild(meta);
                el.appendChild(prevBtn); el.appendChild(nextBtn); el.appendChild(bar);
                // Backdrop click closes; clicks on content don't bubble to it.
                el.addEventListener('click', function (e) { if (e.target === el) close(); });
                [img, meta, bar].forEach(function (n) {
                    n.addEventListener('click', function (e) { e.stopPropagation(); });
                });
                el.addEventListener('touchstart', function (e) {
                    touchX = e.touches.length === 1 ? e.touches[0].clientX : null;
                }, { passive: true });
                el.addEventListener('touchend', function (e) {
                    if (touchX == null) return;
                    var dx = e.changedTouches[0].clientX - touchX;
                    touchX = null;
                    if (Math.abs(dx) > 40) show(idx + (dx < 0 ? 1 : -1));
                }, { passive: true });
                document.body.appendChild(el);
            }

            function creditLine(p) {
                var bits = [];
                if (p.photographer) bits.push('Photo: ' + p.photographer);
                if (p.taken_at) bits.push(String(p.taken_at).slice(0, 10));
                if (p.license) bits.push(p.license);
                return bits.join(' · ');
            }

            function show(i) {
                idx = ((i % pics.length) + pics.length) % pics.length;
                var p = pics[idx];
                img.src = p.web_url;
                img.alt = p.caption || 'field photo';
                cap.textContent = p.caption || '';
                credit.textContent = creditLine(p);
                counter.textContent = (idx + 1) + ' / ' + pics.length;
                fullLink.href = p.orig_url;
                // Preload neighbors so arrowing feels instant.
                [idx + 1, idx - 1].forEach(function (n) {
                    var q = pics[((n % pics.length) + pics.length) % pics.length];
                    if (q && q !== p) { var pre = new Image(); pre.src = q.web_url; }
                });
            }

            function onKey(e) {
                if (e.key === 'Escape') close();
                else if (e.key === 'ArrowRight') show(idx + 1);
                else if (e.key === 'ArrowLeft') show(idx - 1);
            }

            function open(list, i) {
                if (!list || !list.length) return;
                if (!el) build();
                pics = list;
                el.style.display = 'flex';
                document.addEventListener('keydown', onKey);
                show(i || 0);
            }
            function close() {
                if (el) el.style.display = 'none';
                document.removeEventListener('keydown', onKey);
            }
            return { open: open, close: close };
        })();

        // Geotagged photos as map dots while their landslide's panel is open.
        // White camera-dots, clustered when co-located (a burst from one outcrop
        // stacks on one pixel); click → lightbox. Never moves the camera — a
        // helicopter photo km from the polygon is signal, not a framing target.
        var _photoPtsFeats = [];   // survives style.load (basemap switch wipes layers)

        function ensurePhotoLayers() {
            if (!map.getSource('photo-pts')) {
                map.addSource('photo-pts', {
                    type: 'geojson',
                    data: { type: 'FeatureCollection', features: [] },
                    cluster: true, clusterRadius: 26
                });
            }
            if (!map.getLayer('photo-pt')) {
                map.addLayer({ id: 'photo-pt', type: 'circle', source: 'photo-pts',
                    filter: ['!', ['has', 'point_count']],
                    paint: { 'circle-radius': 6, 'circle-color': '#ffffff',
                             'circle-stroke-color': '#3a3630', 'circle-stroke-width': 2 } });
            }
            if (!map.getLayer('photo-pt-cluster')) {
                map.addLayer({ id: 'photo-pt-cluster', type: 'circle', source: 'photo-pts',
                    filter: ['has', 'point_count'],
                    paint: { 'circle-radius': 10, 'circle-color': '#ffffff',
                             'circle-stroke-color': '#3a3630', 'circle-stroke-width': 2 } });
            }
            if (!map.getLayer('photo-pt-count')) {
                map.addLayer({ id: 'photo-pt-count', type: 'symbol', source: 'photo-pts',
                    filter: ['has', 'point_count'],
                    layout: { 'text-field': ['get', 'point_count_abbreviated'],
                              'text-font': ['Noto Sans Regular'], 'text-size': 10,
                              'text-allow-overlap': true, 'text-optional': true },
                    paint: { 'text-color': '#3a3630' } });
            }
        }

        function refreshPhotoPts() {
            ensurePhotoLayers();
            var src = map.getSource('photo-pts');
            if (src) src.setData({ type: 'FeatureCollection', features: _photoPtsFeats });
        }

        function _photoPtsSet(pics) {
            _photoPtsFeats = [];
            (pics || []).forEach(function (p, i) {
                if (p.lat == null || p.lon == null) return;
                _photoPtsFeats.push({ type: 'Feature',
                    geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
                    properties: { idx: i } });
            });
            refreshPhotoPts();
        }

        map.on('style.load', function () {
            if (_photoPtsFeats.length) refreshPhotoPts();
        });

        // 'navigate': the lightbox is modal, so nothing else should react to the
        // same click -- the panel used to re-render underneath it.
        LSTools.clicks.register({
            id: 'photos', kind: 'navigate', priority: 60,
            layers: ['photo-pt', 'photo-pt-cluster'],
            handler: function (f) {
                if (f.layer && f.layer.id === 'photo-pt') {
                    _photoLightbox.open(_detailPhotos, f.properties.idx);
                    return;
                }
                // Open the lightbox at the cluster's first photo — the viewer arrows
                // through the rest; no camera movement.
                map.getSource('photo-pts').getClusterLeaves(f.properties.cluster_id, 100, 0,
                    function (err, leaves) {
                        if (!err && leaves && leaves.length) {
                            _photoLightbox.open(_detailPhotos, leaves[0].properties.idx);
                        }
                    });
            }
        });
        ['photo-pt', 'photo-pt-cluster'].forEach(function (layer) {
            map.on('mouseenter', layer, function () {
                LSTools.cursor.set('hover', 'pointer');
            });
            map.on('mouseleave', layer, function () {
                LSTools.cursor.clear('hover');
            });
        });

        return { _photoShow: _photoShow, _photoOpenAt: _photoOpenAt, photoStripHtml: photoStripHtml, _photoPtsSet: _photoPtsSet };
    } };
})();
