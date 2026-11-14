/* export_ui.js — High-resolution PNG export controls for the inventory map (the engine is ls_export.js): scales offered, which overlays are painted, the legend.
 *
 * Moved out of map.js on 2026-11-14 without changing its text. map.js calls
 * window.LSExportUI.init(ctx) once, at the point in its start-up where this code
 * used to sit, and passes in the few things it needs from the map page:
 *   map, OVERLAYS, _ovVisible, _swipe, _loadRamps, _rampKey.
 * It returns what map.js still uses: nothing.
 */
(function () {
    'use strict';

    window.LSExportUI = { init: function (ctx) {
        var map = ctx.map;
        var OVERLAYS = ctx.OVERLAYS;
        var _ovVisible = ctx._ovVisible;
        var _swipe = ctx._swipe;
        var _loadRamps = ctx._loadRamps;
        var _rampKey = ctx._rampKey;

        // -----------------------------------------------------------------------
        // High-resolution PNG export (engine in ls_export.js)
        //
        // Only the app-specific parts live here: which scales are offered (bounded
        // by the GPU), which overlays are currently painted (so the legend matches
        // the picture), and handing over the wiper's second map. The rendering,
        // compositing and decoration drawing are shared.
        // -----------------------------------------------------------------------
        (function () {
            var goBtn = document.getElementById('exp-go');
            if (!goBtn || !window.LSExport) return;
            goBtn.addEventListener('click', function () {
                window.LSTrack && LSTrack.event('download', { kind: 'map_png' });
            });
            var panel = document.getElementById('export-panel');
            var closeBtn = document.getElementById('export-close');
            if (closeBtn) closeBtn.addEventListener('click', function () {
                panel.classList.add('hidden');
            });
            // Opened from the header Download menu (base.html). A map control was
            // tried first and proved undiscoverable; "Download" is where someone
            // looking for an image export actually looks.
            var dlItem = document.getElementById('dl-map-image');
            if (dlItem) dlItem.addEventListener('click', function () {
                panel.classList.remove('hidden');
                fillScales();
            });
            var scaleSel = document.getElementById('exp-scale');
            var statusEl = document.getElementById('exp-status');
            var _ramps = null;

            function fillScales() {
                var b = map.getContainer().getBoundingClientRect();
                var max = LSExport.maxScaleFor(map);
                scaleSel.innerHTML = '';
                [1, 2, 3, 4, 6, 8].forEach(function (n) {
                    if (n > max) return;
                    var o = document.createElement('option');
                    o.value = n;
                    o.textContent = n + '\u00d7  \u2014  ' + Math.round(b.width * n) + ' \u00d7 ' +
                                    Math.round(b.height * n) + ' px';
                    scaleSel.appendChild(o);
                });
                if (!scaleSel.options.length) {
                    var o1 = document.createElement('option');
                    o1.value = 1; o1.textContent = '1\u00d7 (window too large to scale)';
                    scaleSel.appendChild(o1);
                }
                // 2x by default: a clear step up from the screen without the
                // tile-fetch cost of the higher multiples.
                scaleSel.value = scaleSel.querySelector('option[value="2"]') ? '2'
                               : scaleSel.options[scaleSel.options.length - 1].value;
            }
            fillScales();
            window.addEventListener('resize', fillScales);

            /* Legend entries for the overlays actually painted on the LEFT pane
             * (the base image); wiper-right-only overlays would describe pixels
             * that only occupy part of the frame. */
            function legendEntries() {
                if (!_ramps) return [];
                var out = [];
                OVERLAYS.forEach(function (ov) {
                    if (!_ovVisible(ov, false)) return;
                    // The thinning layer's smoothed build has its own ramp; the
                    // hash encodes that variant as a `~s` suffix, and so does the
                    // ramp registry.
                    var r = _ramps[_rampKey(ov, _ramps)];
                    if (r) out.push(r);
                });
                return out;
            }

            function setStatus(msg, kind) {
                statusEl.textContent = msg || '';
                statusEl.style.color = kind === 'error' ? '#b00020'
                                     : kind === 'ok' ? '#2a7' : '#888';
            }

            function stamp() {
                var d = new Date(), p = function (n) { return (n < 10 ? '0' : '') + n; };
                return d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + '-' +
                       p(d.getHours()) + p(d.getMinutes());
            }

            goBtn.addEventListener('click', function () {
                goBtn.disabled = true;
                setStatus('Loading tiles at full resolution\u2026');
                var scale = parseInt(scaleSel.value, 10) || 2;

                // Ramps are small and rarely change — one shared fetch per session
                // (_loadRamps), the same object the in-panel colour keys read.
                var pre = _loadRamps();

                pre.then(function (r) {
                    _ramps = r;
                    return LSExport.render({
                        map: map,
                        scale: scale,
                        swipe: (_swipe.on && _swipe.map) ? { map: _swipe.map, xPct: _swipe.x } : null,
                        globe: true,
                        transformRequest: LSBasemaps.transformRequest,
                        legendEntries: legendEntries(),
                        deco: {
                            scaleBar:    document.getElementById('exp-scalebar').checked,
                            north:       document.getElementById('exp-north').checked,
                            legend:      document.getElementById('exp-legend').checked,
                            attribution: document.getElementById('exp-attrib').checked,
                            title:       (document.getElementById('exp-title').value || '').trim()
                        }
                    });
                }).then(function (res) {
                    var url = URL.createObjectURL(res.blob);
                    var a = document.createElement('a');
                    a.href = url;
                    // Title names the file too (slugified); the timestamp stays as
                    // a suffix so repeated exports of the same view never collide.
                    var t = (document.getElementById('exp-title').value || '').trim()
                        .toLowerCase().replace(/[^a-z0-9]+/g, '-')
                        .replace(/^-+|-+$/g, '').slice(0, 60);
                    a.download = (t || 'landslidescience') + '-' + stamp() + '.png';
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    // Revoke late: Safari cancels an in-flight download otherwise.
                    setTimeout(function () { URL.revokeObjectURL(url); }, 30000);
                    setStatus((res.note ? res.note + ' ' : '') +
                              'Saved ' + res.width + ' \u00d7 ' + res.height + ' px.', 'ok');
                }).catch(function (e) {
                    setStatus('Export failed: ' + (e && e.message ? e.message : e), 'error');
                }).finally(function () {
                    goBtn.disabled = false;
                });
            });
        })();

        return {  };
    } };
})();
