/* High-resolution PNG export of a MapLibre view — shared by the inventory
 * map and (later) /glaciers, in the same spirit as basemaps.js /
 * ls_overlays.js / ls_proj.js: one implementation, no per-app copies.
 *
 * HOW THE RESOLUTION IS GAINED. Not by upscaling a screenshot. We build a
 * throwaway map offscreen at the same CSS size and camera but a higher
 * `pixelRatio`, so MapLibre allocates a canvas of container × ratio AND
 * requests deeper tiles for it. Vector work (polygons, points, labels, draw
 * shapes) is resolution-independent and comes out genuinely sharper; raster
 * basemaps fetch a deeper zoom. Enlarging the *container* instead would show
 * more map area at the same detail, which is not what "high-res" means here.
 *
 * WHY A CLONE RATHER THAN THE LIVE MAP. `map.getStyle()` returns the fully
 * resolved live style — every source, layer, filter and paint property the
 * user has toggled — so the clone inherits the exact view state with no
 * second copy of that logic to drift. Reading pixels off the live canvas
 * would instead need `preserveDrawingBuffer: true` on it permanently, which
 * costs memory and a copy on every frame of normal panning. Here that flag
 * is set only on the short-lived export map.
 *
 * The honest resolution ceiling: the self-hosted science pyramids are baked
 * to z10 (OPERA z12), so past that they upscale no matter the pixel ratio.
 * Basemap and vector layers keep gaining detail; the rasters do not.
 */
(function () {
    'use strict';

    // Beyond this the GPU refuses the renderbuffer (MAX_RENDERBUFFER_SIZE is
    // commonly 16384, but drivers vary and a 16k×16k RGBA surface is ~1 GB).
    // Checked against the live context and clamped, rather than discovered as
    // a black image.
    var SAFE_DIM = 8192;
    var IDLE_TIMEOUT_MS = 45000;

    function glLimit(map) {
        try {
            var gl = map.painter && map.painter.context && map.painter.context.gl;
            if (gl) {
                return Math.min(gl.getParameter(gl.MAX_RENDERBUFFER_SIZE),
                                gl.getParameter(gl.MAX_TEXTURE_SIZE));
            }
        } catch (e) {}
        return 16384;
    }

    /* Largest whole-number scale that keeps both axes inside the GPU limit. */
    function maxScaleFor(map) {
        var b = map.getContainer().getBoundingClientRect();
        var lim = Math.min(glLimit(map), SAFE_DIM);
        return Math.max(1, Math.floor(lim / Math.max(b.width, b.height)));
    }

    function haversine(a, b) {
        var R = 6371008.8, toRad = Math.PI / 180;
        var dLat = (b.lat - a.lat) * toRad, dLon = (b.lng - a.lng) * toRad;
        var la1 = a.lat * toRad, la2 = b.lat * toRad;
        var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
                Math.cos(la1) * Math.cos(la2) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
        return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
    }

    /* Ground metres per CSS pixel, measured through the map's own projection
     * so it stays right under the globe projection (where a fixed
     * cos(latitude) formula drifts away from the center of the view). */
    function metersPerCssPx(map) {
        var b = map.getContainer().getBoundingClientRect();
        var y = b.height * 0.85, x0 = b.width * 0.1, span = Math.min(200, b.width * 0.3);
        try {
            var p1 = map.unproject([x0, y]), p2 = map.unproject([x0 + span, y]);
            var d = haversine(p1, p2) / span;
            if (isFinite(d) && d > 0) return d;
        } catch (e) {}
        var c = map.getCenter();
        return 156543.03392 * Math.cos(c.lat * Math.PI / 180) / Math.pow(2, map.getZoom());
    }

    /* Screen angle of true north at the view center, radians clockwise from
     * "up". Under the globe projection north is only straight up near the
     * center meridian, so this is measured rather than taken as -bearing. */
    function northAngle(map) {
        try {
            var c = map.getCenter();
            var pc = map.project(c);
            // Sample a point to the NORTH and take the screen direction to it.
            // Only flip to a southward sample within half a degree of the pole,
            // where stepping north would cross it.
            var dLat = (c.lat > 89.4) ? -0.5 : 0.5;
            var pn = map.project([c.lng, c.lat + dLat]);
            // Screen y grows downward, and the arrow is drawn pointing up, so
            // the rotation that carries (0,-1) onto (dx,dy) is atan2(dx, -dy).
            var ang = Math.atan2(pn.x - pc.x, pc.y - pn.y);
            return (dLat < 0) ? ang + Math.PI : ang;
        } catch (e) {
            return -map.getBearing() * Math.PI / 180;
        }
    }

    /* 1 / 2 / 5 × 10^n — the round numbers a scale bar is allowed to be. */
    function niceLength(target) {
        var pow = Math.pow(10, Math.floor(Math.log10(target)));
        var f = target / pow;
        return (f >= 5 ? 5 : f >= 2 ? 2 : 1) * pow;
    }

    function stripTags(s) {
        var d = document.createElement('div');
        d.innerHTML = String(s == null ? '' : s);
        return (d.textContent || '').replace(/\s+/g, ' ').trim();
    }

    /* Attribution for the sources actually VISIBLE in this render.
     *
     * Every overlay stays in the style once added and is toggled with
     * `visibility: none`, so crediting all sources would name datasets the
     * figure does not show — the susceptibility and OPERA citations appearing
     * under a picture with neither layer on. Walk the layers instead, keep the
     * ones that would paint, and credit only their sources. */
    function attributionFor(map) {
        var out = [], seen = {}, live = {};
        var style;
        try { style = map.getStyle(); } catch (e) { return out; }
        (style.layers || []).forEach(function (ly) {
            if (!ly.source) return;
            var vis = (ly.layout && ly.layout.visibility) || 'visible';
            if (vis === 'none') return;
            // A layer turned down to fully transparent contributes no pixels.
            var p = ly.paint || {};
            var op = p['raster-opacity'];
            if (typeof op === 'number' && op <= 0.01) return;
            live[ly.source] = 1;
        });
        var srcs = style.sources || {};
        Object.keys(live).forEach(function (k) {
            var a = stripTags(srcs[k] && srcs[k].attribution);
            if (!a) return;
            a.split('·').forEach(function (part) {
                var t = part.trim();
                if (t && !seen[t]) { seen[t] = 1; out.push(t); }
            });
        });
        return out;
    }

    // -----------------------------------------------------------------------
    // Offscreen clone
    // -----------------------------------------------------------------------
    function cloneOffscreen(src, scale, opts) {
        var b = src.getContainer().getBoundingClientRect();
        var host = document.createElement('div');
        // Off-viewport rather than display:none — a zero-size container gives
        // MapLibre a 0×0 canvas and the export comes back empty.
        host.style.cssText = 'position:fixed;top:0;left:-100000px;pointer-events:none;' +
                             'width:' + Math.round(b.width) + 'px;height:' + Math.round(b.height) + 'px;';
        document.body.appendChild(host);

        var m = new maplibregl.Map({
            container: host,
            style: src.getStyle(),
            center: src.getCenter(),
            zoom: src.getZoom(),
            bearing: src.getBearing(),
            pitch: src.getPitch(),
            pixelRatio: scale,
            // Required to read pixels back: without it the drawing buffer is
            // undefined after compositing and toDataURL yields a blank image.
            canvasContextAttributes: { preserveDrawingBuffer: true, antialias: true },
            maxCanvasSize: [SAFE_DIM, SAFE_DIM],
            interactive: false,
            attributionControl: false,
            // Labels and raster tiles cross-fade in; a capture taken mid-fade
            // shows them half-transparent. Zero makes every frame final.
            fadeDuration: 0,
            transformRequest: opts && opts.transformRequest
        });
        if (opts && opts.globe && typeof m.setProjection === 'function') {
            m.on('style.load', function () {
                try { m.setProjection({ type: 'globe' }); } catch (e) {}
            });
        }
        m.__host = host;
        return m;
    }

    function destroy(m) {
        if (!m) return;
        try { m.remove(); } catch (e) {}
        if (m.__host && m.__host.parentNode) m.__host.parentNode.removeChild(m.__host);
    }

    /* Resolve once the map has nothing left in flight. `idle` already means
     * "style loaded, tiles loaded, nothing animating", but a timeout keeps a
     * single wedged tile request from hanging the export forever — a partial
     * basemap beats a spinner that never ends. */
    function whenIdle(m) {
        return new Promise(function (resolve) {
            var settled = false;
            function finish(reason) {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                // One more frame so the last tile is actually composited.
                requestAnimationFrame(function () { resolve(reason); });
            }
            var timer = setTimeout(function () { finish('timeout'); }, IDLE_TIMEOUT_MS);
            if (m.loaded() && m.areTilesLoaded()) { finish('already'); return; }
            m.once('idle', function () { finish('idle'); });
        });
    }

    // -----------------------------------------------------------------------
    // Decorations — drawn on the 2D canvas after the map pixels, all sized in
    // export pixels so they stay proportionate at any scale.
    // -----------------------------------------------------------------------
    function drawScaleBar(ctx, W, H, s, mppCss) {
        var mppOut = mppCss / s;                       // metres per output px
        var targetPx = W * 0.18;
        var metres = niceLength(targetPx * mppOut);
        var barPx = metres / mppOut;
        var label = metres >= 1000 ? (metres / 1000) + ' km' : metres + ' m';
        var x = 18 * s, y = H - 18 * s, h = 6 * s;

        ctx.save();
        ctx.font = (12 * s) + 'px system-ui, -apple-system, Segoe UI, sans-serif';
        var tw = ctx.measureText(label).width;
        var boxW = Math.max(barPx, tw) + 16 * s, boxH = h + 26 * s;
        ctx.fillStyle = 'rgba(255,255,255,0.82)';
        roundRect(ctx, x - 8 * s, y - boxH + 8 * s, boxW, boxH, 3 * s);
        ctx.fill();

        ctx.fillStyle = '#1a1a1a';
        ctx.fillRect(x, y - h, barPx, h);
        ctx.fillStyle = '#fff';
        // Alternating halves read as a measured bar rather than a plain rule.
        ctx.fillRect(x + barPx / 2, y - h + s, barPx / 2 - s, h - 2 * s);
        ctx.strokeStyle = '#1a1a1a'; ctx.lineWidth = Math.max(1, s * 0.8);
        ctx.strokeRect(x, y - h, barPx, h);

        ctx.fillStyle = '#1a1a1a';
        ctx.textBaseline = 'alphabetic';
        ctx.fillText(label, x, y - h - 5 * s);
        ctx.restore();
    }

    function drawNorthArrow(ctx, W, H, s, ang) {
        var cx = W - 34 * s, cy = 34 * s, r = 16 * s;
        ctx.save();
        ctx.fillStyle = 'rgba(255,255,255,0.82)';
        ctx.beginPath(); ctx.arc(cx, cy, r + 7 * s, 0, Math.PI * 2); ctx.fill();
        ctx.translate(cx, cy);
        ctx.rotate(ang);
        ctx.beginPath();
        ctx.moveTo(0, -r); ctx.lineTo(r * 0.55, r * 0.75);
        ctx.lineTo(0, r * 0.35); ctx.lineTo(-r * 0.55, r * 0.75);
        ctx.closePath();
        ctx.fillStyle = '#1a1a1a'; ctx.fill();
        ctx.restore();

        ctx.save();
        ctx.fillStyle = '#1a1a1a';
        ctx.font = 'bold ' + (11 * s) + 'px system-ui, -apple-system, Segoe UI, sans-serif';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText('N', cx, cy + r + 14 * s);
        ctx.restore();
    }

    function drawAttribution(ctx, W, H, s, lines, reserveLeft) {
        if (!lines.length) return;
        var text = lines.join('  ·  ');
        ctx.save();
        ctx.font = (10 * s) + 'px system-ui, -apple-system, Segoe UI, sans-serif';
        ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
        var tw = ctx.measureText(text).width;
        // Wrap rather than run off the canvas on a busy overlay stack, and
        // keep clear of the scale bar's corner when one is drawn.
        var maxW = W - 24 * s - (reserveLeft || 0);
        var out = [text];
        if (tw > maxW) {
            out = []; var cur = '';
            lines.forEach(function (l) {
                var t = cur ? cur + '  ·  ' + l : l;
                if (ctx.measureText(t).width > maxW && cur) { out.push(cur); cur = l; }
                else cur = t;
            });
            if (cur) out.push(cur);
        }
        var lh = 13 * s;
        var boxH = out.length * lh + 6 * s;
        ctx.fillStyle = 'rgba(255,255,255,0.72)';
        var widest = 0;
        out.forEach(function (l) { widest = Math.max(widest, ctx.measureText(l).width); });
        ctx.fillRect(W - widest - 14 * s, H - boxH, widest + 14 * s, boxH);
        ctx.fillStyle = '#333';
        out.forEach(function (l, i) {
            ctx.fillText(l, W - 7 * s, H - boxH + (i + 1) * lh);
        });
        ctx.restore();
    }

    function drawTitle(ctx, W, H, s, title) {
        if (!title) return;
        ctx.save();
        ctx.font = 'bold ' + (20 * s) + 'px system-ui, -apple-system, Segoe UI, sans-serif';
        ctx.textBaseline = 'top';
        var tw = ctx.measureText(title).width;
        ctx.fillStyle = 'rgba(255,255,255,0.85)';
        roundRect(ctx, 14 * s, 14 * s, tw + 20 * s, 32 * s, 4 * s);
        ctx.fill();
        ctx.fillStyle = '#1a1a1a';
        ctx.fillText(title, 24 * s, 20 * s);
        ctx.restore();
    }

    function roundRect(ctx, x, y, w, h, r) {
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.arcTo(x + w, y, x + w, y + h, r);
        ctx.arcTo(x + w, y + h, x, y + h, r);
        ctx.arcTo(x, y + h, x, y, r);
        ctx.arcTo(x, y, x + w, y, r);
        ctx.closePath();
    }

    function rgba(c) {
        return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + (c[3] / 255) + ')';
    }

    /* Legend blocks for the active overlays. Each entry:
     *   {label, units, kind:'ramp'|'classes', stops:[{v,rgba}], classes:[{label,rgba}]}
     * A 'ramp' is drawn as a gradient with its breakpoints ticked; because the
     * source ramps are log-spaced, ticks are placed at EQUAL SPACING by index
     * rather than by value — that is what the baked tiles actually do between
     * breakpoints, so an evenly-ticked bar is the honest picture of them. */
    function drawLegend(ctx, W, H, s, entries, hasScaleBar) {
        if (!entries || !entries.length) return;
        var pad = 10 * s, bw = 150 * s, bh = 11 * s;
        var rowsH = entries.map(function (e) {
            return (e.kind === 'classes')
                ? 18 * s + e.classes.length * 15 * s
                : 18 * s + bh + 15 * s;
        });
        var boxH = rowsH.reduce(function (a, b) { return a + b; }, 0) + pad * 2;
        var boxW = bw + pad * 2 + 22 * s;
        var x = 18 * s;
        // Sit above the scale bar when both are on, otherwise take its corner.
        var y = H - boxH - (hasScaleBar ? 52 * s : 18 * s);

        ctx.save();
        ctx.fillStyle = 'rgba(255,255,255,0.88)';
        ctx.strokeStyle = 'rgba(0,0,0,0.18)';
        ctx.lineWidth = Math.max(1, s * 0.7);
        roundRect(ctx, x, y, boxW, boxH, 4 * s);
        ctx.fill(); ctx.stroke();

        var cy = y + pad;
        entries.forEach(function (e, i) {
            ctx.fillStyle = '#222';
            ctx.textAlign = 'left'; ctx.textBaseline = 'top';
            ctx.font = 'bold ' + (11 * s) + 'px system-ui, -apple-system, Segoe UI, sans-serif';
            var head = e.label + (e.units ? '  (' + e.units + ')' : '');
            ctx.fillText(head, x + pad, cy);
            cy += 16 * s;

            if (e.kind === 'classes') {
                ctx.font = (10 * s) + 'px system-ui, -apple-system, Segoe UI, sans-serif';
                e.classes.forEach(function (c) {
                    ctx.fillStyle = rgba(c.rgba);
                    ctx.fillRect(x + pad, cy, 14 * s, 10 * s);
                    ctx.strokeStyle = 'rgba(0,0,0,0.25)';
                    ctx.strokeRect(x + pad, cy, 14 * s, 10 * s);
                    ctx.fillStyle = '#333';
                    ctx.fillText(c.label, x + pad + 20 * s, cy);
                    cy += 15 * s;
                });
            } else {
                var g = ctx.createLinearGradient(x + pad, 0, x + pad + bw, 0);
                var n = e.stops.length;
                e.stops.forEach(function (st, k) {
                    g.addColorStop(n === 1 ? 0 : k / (n - 1), rgba(st.rgba));
                });
                ctx.fillStyle = g;
                ctx.fillRect(x + pad, cy, bw, bh);
                ctx.strokeStyle = 'rgba(0,0,0,0.25)';
                ctx.strokeRect(x + pad, cy, bw, bh);
                cy += bh + 2 * s;
                ctx.font = (9 * s) + 'px system-ui, -apple-system, Segoe UI, sans-serif';
                ctx.fillStyle = '#444';
                // Endpoints plus the middle break — a full tick set collides
                // at this width once a ramp has ten breakpoints.
                var picks = [0, Math.floor((n - 1) / 2), n - 1];
                picks.forEach(function (k, j) {
                    var lx = x + pad + (n === 1 ? 0 : bw * k / (n - 1));
                    ctx.textAlign = j === 0 ? 'left' : (j === 2 ? 'right' : 'center');
                    ctx.fillText(fmtVal(e.stops[k].v), lx, cy);
                });
                cy += 13 * s;
            }
        });
        ctx.restore();
    }

    function fmtVal(v) {
        if (v === 0) return '0';
        var a = Math.abs(v);
        if (a >= 1000) return (v / 1000) + 'k';
        if (a < 1) return String(Math.round(v * 100) / 100);
        return String(Math.round(v * 10) / 10);
    }

    // -----------------------------------------------------------------------
    // Public entry point
    // -----------------------------------------------------------------------
    /* render(opts) -> Promise<{blob, width, height, note}>
     *   map            live maplibregl.Map (required)
     *   scale          integer pixel-ratio multiple (2, 3, 4 …)
     *   swipe          {map, xPct} when the wiper is open, else null
     *   deco           {scaleBar, north, attribution, legend, title}
     *   legendEntries  array for drawLegend (caller supplies; see above)
     *   transformRequest, globe
     */
    function render(opts) {
        var src = opts.map;
        var scale = Math.max(1, Math.min(opts.scale || 2, maxScaleFor(src)));
        var note = (scale !== (opts.scale || 2))
            ? 'Clamped to ' + scale + '× — the GPU limits the canvas to ' +
              Math.min(glLimit(src), SAFE_DIM) + ' px on a side.'
            : '';

        var main = cloneOffscreen(src, scale, opts);
        var swipeMap = null;
        if (opts.swipe && opts.swipe.map) {
            swipeMap = cloneOffscreen(opts.swipe.map, scale, opts);
        }

        return Promise.all([whenIdle(main), swipeMap ? whenIdle(swipeMap) : null])
            .then(function (reasons) {
                if (reasons.indexOf('timeout') !== -1) {
                    note = (note ? note + ' ' : '') +
                           'Some tiles were still loading after ' +
                           Math.round(IDLE_TIMEOUT_MS / 1000) + 's — parts of the ' +
                           'basemap may be missing. Try again once the view has settled.';
                }
                var cv = main.getCanvas();
                var W = cv.width, H = cv.height;
                var out = document.createElement('canvas');
                out.width = W; out.height = H;
                var ctx = out.getContext('2d');
                ctx.drawImage(cv, 0, 0);

                if (swipeMap) {
                    // The wiper shows the second basemap from the divider
                    // rightwards (CSS `inset(0 0 0 x%)`), so clip to the same
                    // band and lay it over — matching what is on screen.
                    var cut = Math.round(W * (opts.swipe.xPct || 50) / 100);
                    ctx.save();
                    ctx.beginPath();
                    ctx.rect(cut, 0, W - cut, H);
                    ctx.clip();
                    ctx.drawImage(swipeMap.getCanvas(), 0, 0);
                    ctx.restore();
                    ctx.fillStyle = '#fff';
                    ctx.fillRect(cut - 1.5 * scale, 0, 3 * scale, H);
                }

                var deco = opts.deco || {};
                if (deco.legend) drawLegend(ctx, W, H, scale, opts.legendEntries, !!deco.scaleBar);
                if (deco.scaleBar) drawScaleBar(ctx, W, H, scale, metersPerCssPx(main));
                if (deco.north) drawNorthArrow(ctx, W, H, scale, northAngle(main));
                if (deco.attribution) {
                    // The scale bar owns the bottom-left; measure what it
                    // actually occupies so attribution wraps above it rather
                    // than running through it.
                    drawAttribution(ctx, W, H, scale, attributionFor(main),
                                    deco.scaleBar ? W * 0.22 + 30 * scale : 0);
                }
                if (deco.title) drawTitle(ctx, W, H, scale, deco.title);

                return new Promise(function (resolve, reject) {
                    out.toBlob(function (blob) {
                        if (!blob) { reject(new Error('toBlob returned null')); return; }
                        resolve({ blob: blob, width: W, height: H, note: note });
                    }, 'image/png');
                });
            })
            .finally(function () {
                destroy(main);
                destroy(swipeMap);
            });
    }

    window.LSExport = {
        render: render,
        maxScaleFor: maxScaleFor,
        attributionFor: attributionFor
    };
})();
