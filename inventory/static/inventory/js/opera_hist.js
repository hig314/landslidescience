/* opera_hist.js — OPERA velocity view histogram: the asc x desc joint histogram of the ground in view, read from the same value tiles the overlay draws.
 *
 * Moved out of map.js on 2026-11-14 without changing its text. map.js calls
 * window.LSOperaHist.init(ctx) once, at the point in its start-up where this code
 * used to sit, and passes in the few things it needs from the map page:
 *   map, API_BASE, OPERA_TILE_V, makeFloatingPanel.
 * It returns what map.js still uses: operaFP.
 */
(function () {
    'use strict';

    window.LSOperaHist = { init: function (ctx) {
        var map = ctx.map;
        var API_BASE = ctx.API_BASE;
        var OPERA_TILE_V = ctx.OPERA_TILE_V;
        var makeFloatingPanel = ctx.makeFloatingPanel;

        // ===========================================================================
        // OPERA velocity — view-scoped histogram. The tile pyramid IS the
        // pre-aggregation ("pyramiding"): the current viewport is covered by a
        // handful of value tiles at z = min(ceil(zoom), 12), fetched same-origin
        // from the proxy (disk- and browser-cached), canvas-decoded, cropped to
        // the view, and histogrammed client-side in milliseconds. Gray 1–255 maps
        // linearly onto ±30 mm/yr; alpha 0 = no coverage; values AT the ends are
        // clipped (the product saturates there).
        // ===========================================================================
        var operaPanel = document.getElementById('opera-float');
        var _opCache = {};        // 'track/z/x/y' -> {img: ImageData} | {missing:true}
        var _opCacheKeys = [];
        var _opReq = 0;
        var OP_MIN_MM = -30, OP_MAX_MM = 30;

        function _opTileData(track, z, x, y) {
            var key = track + '/' + z + '/' + x + '/' + y;
            if (_opCache[key]) return Promise.resolve(_opCache[key]);
            return fetch(API_BASE + 'tiles/opera/' + track + '/' + z + '/' + x + '/' + y +
                         '.png?v=' + OPERA_TILE_V)
                .then(function (r) {
                    if (r.status === 404) return null;
                    if (!r.ok) throw new Error('opera tile HTTP ' + r.status);
                    return r.blob().then(function (b) { return createImageBitmap(b); });
                })
                .then(function (bmp) {
                    var entry;
                    if (!bmp) {
                        entry = { missing: true };
                    } else {
                        var c = document.createElement('canvas');
                        c.width = c.height = 256;
                        var ctx = c.getContext('2d', { willReadFrequently: true });
                        ctx.drawImage(bmp, 0, 0);
                        entry = { img: ctx.getImageData(0, 0, 256, 256) };
                    }
                    _opCache[key] = entry;
                    _opCacheKeys.push(key);
                    if (_opCacheKeys.length > 96) delete _opCache[_opCacheKeys.shift()];
                    return entry;
                });
        }
        function _opLonPx(lon, z) { return (lon + 180) / 360 * Math.pow(2, z) * 256; }
        function _opLatPx(lat, z) {
            var s = Math.max(-0.9999, Math.min(0.9999, Math.sin(lat * Math.PI / 180)));
            return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * Math.pow(2, z) * 256;
        }
        // Joint (asc, desc) collection: both mosaics share the 3857 tile grid, so
        // the same pixel index is the same ground cell — walk each view tile once
        // with BOTH tracks' ImageData and count pairs where both have coverage.
        // Returns {joint: Uint32Array(255*255) indexed (av-1)+(dv-1)*255,
        //          both, ascOnly, descOnly, totalPx, clipped}.
        function _opCollectJoint() {
            var zv = Math.max(0, Math.min(12, Math.ceil(map.getZoom())));
            var b = map.getBounds();
            var n = Math.pow(2, zv);
            var pxW = _opLonPx(b.getWest(), zv), pxE = _opLonPx(b.getEast(), zv);
            var pxN = Math.max(0, _opLatPx(b.getNorth(), zv));
            var pxS = Math.min(n * 256, _opLatPx(b.getSouth(), zv));
            var jobs = [];
            var tx0 = Math.floor(pxW / 256), tx1 = Math.floor((pxE - 1) / 256);
            var ty0 = Math.floor(pxN / 256), ty1 = Math.min(n - 1, Math.floor((pxS - 1) / 256));
            for (var tx = tx0; tx <= tx1; tx++) {
                for (var ty = Math.max(0, ty0); ty <= ty1; ty++) {
                    (function (tx, ty) {
                        var xw = ((tx % n) + n) % n;   // dateline wrap (Aleutians)
                        var miss = function () { return { missing: true }; };
                        jobs.push(Promise.all([
                            _opTileData('asc', zv, xw, ty).catch(miss),
                            _opTileData('desc', zv, xw, ty).catch(miss)
                        ]).then(function (pair) {
                            return { tx: tx, ty: ty, asc: pair[0], desc: pair[1] };
                        }));
                    })(tx, ty);
                }
            }
            return Promise.all(jobs).then(function (tiles) {
                var joint = new Uint32Array(255 * 255);
                var both = 0, ascOnly = 0, descOnly = 0, totalPx = 0, clipped = 0;
                tiles.forEach(function (t) {
                    var x0 = Math.max(0, Math.round(pxW - t.tx * 256));
                    var x1 = Math.min(256, Math.round(pxE - t.tx * 256));
                    var y0 = Math.max(0, Math.round(pxN - t.ty * 256));
                    var y1 = Math.min(256, Math.round(pxS - t.ty * 256));
                    if (x1 <= x0 || y1 <= y0) return;
                    totalPx += (x1 - x0) * (y1 - y0);
                    var da = (t.asc && t.asc.img) ? t.asc.img.data : null;
                    var dd = (t.desc && t.desc.img) ? t.desc.img.data : null;
                    if (!da && !dd) return;
                    for (var y = y0; y < y1; y++) {
                        var off = y * 1024;
                        for (var x = x0; x < x1; x++) {
                            var i = off + x * 4;
                            var hasA = da && da[i + 3] !== 0;
                            var hasD = dd && dd[i + 3] !== 0;
                            if (hasA && hasD) {
                                var av = da[i], dv = dd[i];
                                joint[(av - 1) + (dv - 1) * 255]++;
                                both++;
                                if (av === 1 || av === 255 || dv === 1 || dv === 255) clipped++;
                            } else if (hasA) {
                                ascOnly++;
                            } else if (hasD) {
                                descOnly++;
                            }
                        }
                    }
                });
                return { joint: joint, both: both, ascOnly: ascOnly, descOnly: descOnly,
                         totalPx: totalPx, clipped: clipped };
            });
        }
        // 2D density heatmap: x = ascending velocity, y = descending velocity
        // (both −30…+30 mm/yr), log-scaled Blues. The diagonals are the physical
        // reading: motion along y=x projects the same into both look directions
        // (vertical / N–S-ish); y=−x is opposite-sign LOS — the signature of
        // east/west aspect-driven downslope motion.
        function _opRender(res) {
            var cv = document.getElementById('opera-hist-canvas');
            if (!cv) return;
            var W = cv.clientWidth || 380, H = cv.clientHeight || 340;
            var dpr = window.devicePixelRatio || 1;
            cv.width = W * dpr; cv.height = H * dpr;
            var ctx = cv.getContext('2d');
            ctx.scale(dpr, dpr);
            ctx.clearRect(0, 0, W, H);
            var L = 40, R = 10, T = 10, B = 30;
            var side = Math.min(W - L - R, H - T - B);   // keep the plot square
            var px = L, py = T;

            // Density image at native 255×255 value resolution.
            var off = document.createElement('canvas');
            off.width = off.height = 255;
            var octx = off.getContext('2d');
            var img = octx.createImageData(255, 255);
            var maxC = 0, j = res.joint;
            for (var k = 0; k < j.length; k++) if (j[k] > maxC) maxC = j[k];
            if (maxC > 0) {
                var logMax = Math.log(1 + maxC);
                for (var dv = 0; dv < 255; dv++) {
                    for (var av = 0; av < 255; av++) {
                        var c = j[av + dv * 255];
                        var p = (av + (254 - dv) * 255) * 4;   // canvas y grows downward
                        if (c > 0) {
                            var t = Math.log(1 + c) / logMax;   // 0..1
                            // white → mid blue → near-navy
                            img.data[p]     = Math.round(255 - t * (255 - 8));
                            img.data[p + 1] = Math.round(255 - t * (255 - 48));
                            img.data[p + 2] = Math.round(255 - t * (255 - 107));
                            img.data[p + 3] = 255;
                        } else {
                            img.data[p + 3] = 0;
                        }
                    }
                }
            }
            octx.putImageData(img, 0, 0);

            // plot background + density
            ctx.fillStyle = '#fff';
            ctx.fillRect(px, py, side, side);
            ctx.imageSmoothingEnabled = false;
            ctx.drawImage(off, px, py, side, side);

            // grid, zero lines, diagonals
            function ax(mm) { return px + (mm - OP_MIN_MM) / (OP_MAX_MM - OP_MIN_MM) * side; }
            function ay(mm) { return py + side - (mm - OP_MIN_MM) / (OP_MAX_MM - OP_MIN_MM) * side; }
            ctx.font = '9px system-ui, sans-serif';
            [-30, -15, 0, 15, 30].forEach(function (mm) {
                ctx.strokeStyle = mm === 0 ? 'rgba(0,0,0,0.28)' : 'rgba(0,0,0,0.07)';
                ctx.beginPath(); ctx.moveTo(ax(mm), py); ctx.lineTo(ax(mm), py + side); ctx.stroke();
                ctx.beginPath(); ctx.moveTo(px, ay(mm)); ctx.lineTo(px + side, ay(mm)); ctx.stroke();
                ctx.fillStyle = '#888'; ctx.textAlign = 'center';
                ctx.fillText((mm > 0 ? '+' : '') + mm, ax(mm), py + side + 12);
                ctx.textAlign = 'right';
                ctx.fillText((mm > 0 ? '+' : '') + mm, px - 3, ay(mm) + 3);
            });
            ctx.strokeStyle = 'rgba(90,60,40,0.30)';
            ctx.setLineDash([4, 3]);
            ctx.beginPath(); ctx.moveTo(ax(-30), ay(-30)); ctx.lineTo(ax(30), ay(30)); ctx.stroke();   // y = x
            ctx.beginPath(); ctx.moveTo(ax(-30), ay(30)); ctx.lineTo(ax(30), ay(-30)); ctx.stroke();   // y = −x
            ctx.setLineDash([]);
            ctx.fillStyle = 'rgba(90,60,40,0.55)';
            ctx.textAlign = 'left';
            ctx.fillText('y=x  vertical-ish', ax(17), ay(20) - 3);
            ctx.fillText('y=−x  E–W aspect', ax(9), ay(-14) - 3);
            // frame + axis titles
            ctx.strokeStyle = '#bbb';
            ctx.strokeRect(px, py, side, side);
            ctx.fillStyle = '#666'; ctx.textAlign = 'center';
            ctx.fillText('ascending LOS velocity (mm/yr)', px + side / 2, py + side + 24);
            ctx.save();
            ctx.translate(10, py + side / 2); ctx.rotate(-Math.PI / 2);
            ctx.fillText('descending LOS velocity (mm/yr)', 0, 0);
            ctx.restore();

            var foot = document.getElementById('opera-hist-foot');
            if (foot) {
                var cov = res.totalPx ? (res.both / res.totalPx * 100).toFixed(0) : '0';
                var clip = res.both ? (res.clipped / res.both * 100).toFixed(1) : '0';
                foot.innerHTML =
                    '<b>' + res.both.toLocaleString() + '</b> px with both tracks (' + cov +
                    '% of view) · asc-only ' + res.ascOnly.toLocaleString() +
                    ' · desc-only ' + res.descOnly.toLocaleString() +
                    ' · ' + clip + '% clipped<br>' +
                    'Log-scaled density. Along <b>y=−x</b>: opposite LOS signs — ' +
                    'east/west aspect-driven motion. Along <b>y=x</b>: same sign in both ' +
                    'looks — vertical-ish motion. 8-bit quantized, clipped at ±30 mm/yr. ' +
                    'OPERA DISP-S1 © NASA/JPL · mosaic ASF.';
            }
        }
        function updateOperaHist() {
            if (!operaPanel || operaPanel.classList.contains('hidden')) return;
            var token = ++_opReq;
            var status = document.getElementById('opera-hist-status');
            if (status) status.textContent = 'computing…';
            _opCollectJoint().then(function (res) {
                if (token !== _opReq) return;   // superseded by a newer view
                if (status) {
                    status.textContent = res.both ? '' : 'no overlapping asc+desc coverage in view';
                }
                _opRender(res);
            }).catch(function (e) {
                if (token !== _opReq) return;
                if (status) status.textContent = 'load failed';
                console.error('opera histogram failed:', e);
            });
        }
        var operaFP = makeFloatingPanel(operaPanel, {
            handle:   operaPanel ? document.getElementById('opera-handle') : null,
            toggle:   document.getElementById('opera-toggle'),
            close:    document.getElementById('opera-close'),
            onResize: updateOperaHist,
            onChange: updateOperaHist
        });
        var _opMoveTimer = null;
        map.on('moveend', function () {
            if (!operaPanel || operaPanel.classList.contains('hidden')) return;
            clearTimeout(_opMoveTimer);
            _opMoveTimer = setTimeout(updateOperaHist, 350);
        });

        return { operaFP: operaFP };
    } };
})();
