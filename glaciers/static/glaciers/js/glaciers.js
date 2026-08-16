/* /glaciers — Lagrangian glacier-flow visualization.
 *
 * A deliberately small sibling of the inventory map: basemaps come from the
 * shared LSBasemaps module, the ITS_LIVE/Hugonnet overlays from the shared
 * LSOverlays module (single source of truth with the inventory), and the new
 * code here is the tracer engine: particles advected through the
 * TIME-VARYING ITS_LIVE velocity field —
 *     v(x, t) = v_annual(x, yearblend(t)) + amp(x)·cos(2π(doy − phase(x))/365.25)
 * per component — so particle paths curve when the field changes; unlike
 * constant-velocity streakline animations (Windy-style), displacement is the
 * time-integral of the data. Artifacts in the data (mosaic seams, gap years)
 * are deliberately visible: this doubles as a data-inspection tool.
 *
 * Density-managed respawn (spec'd by Hig): min/max particles per management
 * cell — under-filled cells spawn, over-filled cells retire their oldest
 * particles by FADING over several steps (no instant kills). Particles that
 * exit the ice or hit NoData also fade out.
 *
 * Tracer bundles (annual vx/vy + seasonal amp/phase + landice mask on the
 * 120 m EPSG:3413 grid) are built by tools/build_glacier_tracers.py and
 * served at /glaciers/data/ (immutable; bump TRACER_DATA_V on rebuild).
 */
(function () {
    'use strict';

    var CFG = window.GL_CONFIG || {};
    var TRACER_DATA_V = '1';

    // ---------------------------------------------------------------------
    // EPSG:3413 (WGS84 north polar stereographic, lat_ts=70, lon_0=-45) ↔
    // lon/lat. Snyder (1987) ellipsoidal formulas — ~30 lines beats a
    // dependency; matches proj to sub-mm at these latitudes.
    // ---------------------------------------------------------------------
    var A = 6378137.0, E = 0.081819190842622, LON0 = -45 * Math.PI / 180;
    var E2 = E / 2;
    function _tOf(phi) {
        return Math.tan(Math.PI / 4 - phi / 2) /
               Math.pow((1 - E * Math.sin(phi)) / (1 + E * Math.sin(phi)), E2);
    }
    var PHI_TS = 70 * Math.PI / 180;
    var M_TS = Math.cos(PHI_TS) / Math.sqrt(1 - E * E * Math.sin(PHI_TS) * Math.sin(PHI_TS));
    var T_TS = _tOf(PHI_TS);

    function lonLatTo3413(lon, lat) {
        var phi = lat * Math.PI / 180, lam = lon * Math.PI / 180;
        var rho = A * M_TS * _tOf(phi) / T_TS;
        return [rho * Math.sin(lam - LON0), -rho * Math.cos(lam - LON0)];
    }
    function ll3413ToLonLat(x, y) {
        var rho = Math.hypot(x, y);
        var t = rho * T_TS / (A * M_TS);
        var chi = Math.PI / 2 - 2 * Math.atan(t);
        var e2 = E * E, e4 = e2 * e2, e6 = e4 * e2, e8 = e4 * e4;
        var phi = chi +
            (e2 / 2 + 5 * e4 / 24 + e6 / 12 + 13 * e8 / 360) * Math.sin(2 * chi) +
            (7 * e4 / 48 + 29 * e6 / 240 + 811 * e8 / 11520) * Math.sin(4 * chi) +
            (7 * e6 / 120 + 81 * e8 / 1120) * Math.sin(6 * chi) +
            (4279 * e8 / 161280) * Math.sin(8 * chi);
        var lam = LON0 + Math.atan2(x, -y);
        return [lam * 180 / Math.PI, phi * 180 / Math.PI];
    }

    // ---------------------------------------------------------------------
    // Map + shared layers
    // ---------------------------------------------------------------------
    var DEFAULT_BASEMAP = 'esri-img';
    var basemaps = window.LSBasemaps.DEFAULTS.slice();
    function findBasemap(id) {
        for (var i = 0; i < basemaps.length; i++) if (basemaps[i].id === id) return basemaps[i];
        return null;
    }
    if (window.LSBasemaps.registerProtocols) window.LSBasemaps.registerProtocols();

    var map = new maplibregl.Map({
        container: 'gl-map',
        style: window.LSBasemaps.buildRasterStyle(findBasemap(DEFAULT_BASEMAP)),
        center: (CFG.catalog[0] && CFG.catalog[0].center) || [-147.07, 61.15],
        zoom: (CFG.catalog[0] && CFG.catalog[0].zoom) || 10,
        transformRequest: window.LSBasemaps.transformRequest,
        attributionControl: { compact: true }
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');

    // Overlays: the shared glacier set, one simple row each (checkbox +
    // opacity + the thinning variant toggle). No wiper here — the inventory
    // map is the comparison tool; this app spends its complexity on tracers.
    var OVERLAYS = window.LSOverlays.glacierOverlays({});
    var _ovState = {};
    OVERLAYS.forEach(function (ov) { _ovState[ov.id] = { on: false, op: ov.defOpacity }; });

    function ensureOverlays() {
        OVERLAYS.forEach(function (ov) {
            if (!map.getSource(ov.sourceId)) map.addSource(ov.sourceId, ov.sourceDef());
            if (!map.getLayer(ov.layerId)) {
                map.addLayer({
                    id: ov.layerId, type: 'raster', source: ov.sourceId,
                    layout: { 'visibility': 'none' },
                    paint: { 'raster-opacity': 1, 'raster-resampling': 'nearest' }
                });
            }
        });
        applyOverlays();
    }
    function applyOverlays() {
        OVERLAYS.forEach(function (ov) {
            if (!map.getLayer(ov.layerId)) return;
            var st = _ovState[ov.id];
            map.setLayoutProperty(ov.layerId, 'visibility', st.on ? 'visible' : 'none');
            map.setPaintProperty(ov.layerId, 'raster-opacity', st.op);
        });
    }
    function swapOverlaySource(ov) {
        if (!map.getLayer(ov.layerId)) return;
        map.removeLayer(ov.layerId);
        map.removeSource(ov.sourceId);
        ensureOverlays();
    }
    map.on('style.load', function () { ensureOverlays(); });

    // ---------------------------------------------------------------------
    // Minimal panel UI: site select, basemap select, overlay rows
    // ---------------------------------------------------------------------
    var siteSel = document.getElementById('gl-site');
    (CFG.catalog || []).forEach(function (c) {
        var o = document.createElement('option');
        o.value = c.slug; o.textContent = c.name;
        siteSel.appendChild(o);
    });

    var bmRow = document.getElementById('gl-basemap-row');
    var bmSel = document.createElement('select');
    basemaps.forEach(function (bm) {
        var o = document.createElement('option');
        o.value = bm.id; o.textContent = bm.label;
        if (bm.id === DEFAULT_BASEMAP) o.selected = true;
        bmSel.appendChild(o);
    });
    bmSel.addEventListener('change', function () {
        var bm = findBasemap(bmSel.value);
        if (bm) map.setStyle(window.LSBasemaps.buildRasterStyle(bm));
        // overlays re-added on style.load
    });
    bmRow.appendChild(bmSel);

    var ovWrap = document.getElementById('gl-overlays');
    OVERLAYS.forEach(function (ov) {
        var st = _ovState[ov.id];
        var row = document.createElement('div');
        row.className = 'gl-ov-row';
        var lab = document.createElement('label');
        var cb = document.createElement('input');
        cb.type = 'checkbox';
        var span = document.createElement('span');
        span.textContent = ov.label;
        span.title = ov.sub;
        lab.appendChild(cb); lab.appendChild(span);
        row.appendChild(lab);
        var op = document.createElement('input');
        op.type = 'range'; op.min = '10'; op.max = '100';
        op.value = String(Math.round(st.op * 100));
        op.style.display = 'none';
        op.addEventListener('input', function () { st.op = (+op.value) / 100; applyOverlays(); });
        row.appendChild(op);
        var vlab = null, vcb = null;
        if (ov.variant) {
            vlab = document.createElement('label');
            vlab.style.cssText = 'font-size:11px;color:#555;';
            if (ov.variant.title) vlab.title = ov.variant.title;
            vcb = document.createElement('input');
            vcb.type = 'checkbox';
            vcb.checked = ov.variant.get();
            vcb.addEventListener('change', function () {
                ov.variant.set(vcb.checked);
                swapOverlaySource(ov);
            });
            vlab.appendChild(vcb);
            vlab.appendChild(document.createTextNode(ov.variant.label));
            vlab.style.display = 'none';
            row.appendChild(vlab);
        }
        cb.addEventListener('change', function () {
            st.on = cb.checked;
            op.style.display = st.on ? '' : 'none';
            if (vlab) vlab.style.display = st.on ? 'flex' : 'none';
            ensureOverlays();
        });
        ovWrap.appendChild(row);
    });

    // Tracer controls
    var trWrap = document.getElementById('gl-tracer-controls');
    trWrap.innerHTML = '';
    function ctlRow(labelText, input) {
        var row = document.createElement('div');
        row.className = 'gl-ov-row';
        var lab = document.createElement('label');
        lab.appendChild(input);
        lab.appendChild(document.createTextNode(labelText));
        row.appendChild(lab);
        trWrap.appendChild(row);
        return input;
    }
    var cbTracers = ctlRow('Flow tracers', document.createElement('input'));
    cbTracers.type = 'checkbox'; cbTracers.checked = true;
    var cbTrails = ctlRow('Trails', document.createElement('input'));
    cbTrails.type = 'checkbox'; cbTrails.checked = true;
    var densSel = document.createElement('select');
    ['sparse', 'normal', 'dense'].forEach(function (d) {
        var o = document.createElement('option');
        o.value = d; o.textContent = 'Density: ' + d;
        if (d === 'normal') o.selected = true;
        densSel.appendChild(o);
    });
    var densRow = document.createElement('div');
    densRow.className = 'gl-ov-row';
    densRow.appendChild(densSel);
    trWrap.appendChild(densRow);

    // ---------------------------------------------------------------------
    // Tracer bundle loading
    // ---------------------------------------------------------------------
    var B = null;    // active bundle {hdr, vx, vy, vxAmp, vyAmp, vxPh, vyPh, ice}

    function loadBundle(slug) {
        return fetch(CFG.dataBase + slug + '.json?v=' + TRACER_DATA_V)
            .then(function (r) {
                if (!r.ok) throw new Error('bundle header HTTP ' + r.status);
                return r.json();
            })
            .then(function (hdr) {
                return fetch(CFG.dataBase + hdr.bin + '?v=' + TRACER_DATA_V)
                    .then(function (r) {
                        if (!r.ok) throw new Error('bundle bin HTTP ' + r.status);
                        return r.arrayBuffer();
                    })
                    .then(function (buf) {
                        // Bundles are int16 (1 m/yr resolution, nodata
                        // sentinel) — decode once into Float32 with NaN.
                        var nod = hdr.nodata != null ? hdr.nodata : -32768;
                        var scale = hdr.scale || 1;
                        function view(name) {
                            var o = hdr.offsets[name];
                            if (hdr.dtype !== 'int16') {
                                return new Float32Array(buf, o[0] * 4, o[1]);
                            }
                            var raw = new Int16Array(buf, o[0] * 2, o[1]);
                            var out = new Float32Array(o[1]);
                            for (var i = 0; i < o[1]; i++) {
                                out[i] = raw[i] === nod ? NaN : raw[i] * scale;
                            }
                            return out;
                        }
                        return {
                            hdr: hdr,
                            vx: view('vx'), vy: view('vy'),
                            vxAmp: view('vx_amp'), vyAmp: view('vy_amp'),
                            vxPh: view('vx_phase'), vyPh: view('vy_phase'),
                            ice: view('landice')
                        };
                    });
            });
    }

    // ---------------------------------------------------------------------
    // Field sampling — bilinear in space (NaN-corner aware), linear in time
    // between annual fields, climatological seasonal cycle superposed.
    // ---------------------------------------------------------------------
    function sampleGrid2(arr, nx, ny, fx, fy) {
        // arr is a [ny][nx] plane starting at some base offset handled by the
        // caller passing a subarray view; fx/fy fractional cell coords.
        var j0 = Math.floor(fx), i0 = Math.floor(fy);
        var tx = fx - j0, ty = fy - i0;
        var sum = 0, wsum = 0;
        for (var di = 0; di <= 1; di++) {
            for (var dj = 0; dj <= 1; dj++) {
                var i = i0 + di, j = j0 + dj;
                if (i < 0 || i >= ny || j < 0 || j >= nx) continue;
                var v = arr[i * nx + j];
                if (v !== v) continue;   // NaN
                var w = (di ? ty : 1 - ty) * (dj ? tx : 1 - tx);
                sum += w * v; wsum += w;
            }
        }
        return wsum > 0.05 ? sum / wsum : NaN;
    }

    function sampleVelocity(x, y, t) {
        // t in fractional years (e.g. 2017.53). Returns {vx, vy} m/yr or null.
        var g = B.hdr.grid;
        var fx = (x - g.x0) / g.dx - 0.5;
        var fy = (g.y0_north - y) / g.dx - 0.5;
        if (fx < -1 || fy < -1 || fx > g.nx || fy > g.ny) return null;
        var years = B.hdr.years, nY = years.length;
        var ty = t - years[0];                       // years since first field
        var y0 = Math.floor(ty), frac = ty - y0;
        if (y0 < 0) { y0 = 0; frac = 0; }
        if (y0 >= nY - 1) { y0 = nY - 1; frac = 0; }
        var plane = g.nx * g.ny;
        var vx0 = sampleGrid2(B.vx.subarray(y0 * plane, (y0 + 1) * plane), g.nx, g.ny, fx, fy);
        var vy0 = sampleGrid2(B.vy.subarray(y0 * plane, (y0 + 1) * plane), g.nx, g.ny, fx, fy);
        var vx = vx0, vy = vy0;
        if (frac > 0) {
            var vx1 = sampleGrid2(B.vx.subarray((y0 + 1) * plane, (y0 + 2) * plane), g.nx, g.ny, fx, fy);
            var vy1 = sampleGrid2(B.vy.subarray((y0 + 1) * plane, (y0 + 2) * plane), g.nx, g.ny, fx, fy);
            if (vx1 === vx1 && vy1 === vy1 && vx0 === vx0 && vy0 === vy0) {
                vx = vx0 + frac * (vx1 - vx0);
                vy = vy0 + frac * (vy1 - vy0);
            }
        }
        if (vx !== vx || vy !== vy) return null;
        // Sanity clamp: nothing on Earth flows 25 km/yr — a value like this
        // is an unmasked fill or corrupt cell; treat as no-data.
        if (Math.abs(vx) > 25000 || Math.abs(vy) > 25000) return null;
        // Seasonal superposition (climatological amp + day-of-peak phase).
        var doy = (t - Math.floor(t)) * 365.25;
        var ax = sampleGrid2(B.vxAmp, g.nx, g.ny, fx, fy);
        var ay = sampleGrid2(B.vyAmp, g.nx, g.ny, fx, fy);
        var px = sampleGrid2(B.vxPh, g.nx, g.ny, fx, fy);
        var py = sampleGrid2(B.vyPh, g.nx, g.ny, fx, fy);
        if (ax === ax && px === px) vx += ax * Math.cos(2 * Math.PI * (doy - px) / 365.25);
        if (ay === ay && py === py) vy += ay * Math.cos(2 * Math.PI * (doy - py) / 365.25);
        return { vx: vx, vy: vy };
    }

    function onIce(x, y) {
        var g = B.hdr.grid;
        var j = Math.round((x - g.x0) / g.dx - 0.5);
        var i = Math.round((g.y0_north - y) / g.dx - 0.5);
        if (i < 0 || i >= g.ny || j < 0 || j >= g.nx) return false;
        var v = B.ice[i * g.nx + j];
        return v === v && v > 0;
    }

    // ---------------------------------------------------------------------
    // Particles + density-managed respawn
    // ---------------------------------------------------------------------
    var DENSITY = {                      // management-cell targets
        sparse: { cell: 3000, min: 1, max: 1, cap: 1500 },
        normal: { cell: 1800, min: 1, max: 2, cap: 4000 },
        dense:  { cell: 1200, min: 1, max: 3, cap: 8000 }
    };
    var FADE_STEPS = 30;                 // fade-out length (steps)
    var particles = [];
    var simT = null;                     // current sim time (fractional years)
    var playing = false;

    function densityCfg() { return DENSITY[densSel.value] || DENSITY.normal; }

    function manageDensity() {
        if (!B) return;
        var cfg = densityCfg();
        var g = B.hdr.grid;
        var cell = cfg.cell;
        var ncx = Math.ceil(g.nx * g.dx / cell), ncy = Math.ceil(g.ny * g.dx / cell);
        var counts = new Int16Array(ncx * ncy);
        var byCell = {};
        particles.forEach(function (p) {
            if (p.fade < 1) return;      // dying particles don't hold territory
            var cx = Math.floor((p.x - g.x0) / cell);
            var cy = Math.floor((g.y0_north - p.y) / cell);
            if (cx < 0 || cy < 0 || cx >= ncx || cy >= ncy) return;
            var k = cy * ncx + cx;
            counts[k]++;
            (byCell[k] = byCell[k] || []).push(p);
        });
        // Over-max: fade the oldest surplus.
        Object.keys(byCell).forEach(function (k) {
            var list = byCell[k];
            if (list.length > cfg.max) {
                list.sort(function (a, b) { return b.age - a.age; });
                for (var i = 0; i < list.length - cfg.max; i++) list[i].fade -= 1 / FADE_STEPS;
            }
        });
        // Under-min: spawn in open ice cells (respect the global cap).
        if (particles.length >= cfg.cap) return;
        for (var cy = 0; cy < ncy; cy++) {
            for (var cx = 0; cx < ncx; cx++) {
                if (counts[cy * ncx + cx] >= cfg.min) continue;
                // random point in the cell; keep only if on ice w/ velocity
                for (var attempt = 0; attempt < 3; attempt++) {
                    var x = g.x0 + cx * cell + Math.random() * cell;
                    var y = g.y0_north - cy * cell - Math.random() * cell;
                    if (!onIce(x, y)) continue;
                    if (!sampleVelocity(x, y, simT)) continue;
                    particles.push({ x: x, y: y, age: 0, fade: 1 });
                    break;
                }
                if (particles.length >= cfg.cap) return;
            }
        }
    }

    function stepParticles(dt) {
        // RK2 midpoint; dt in years.
        for (var n = particles.length - 1; n >= 0; n--) {
            var p = particles[n];
            p.age += dt;
            if (p.fade < 1) {
                p.fade -= 1 / FADE_STEPS;
                if (p.fade <= 0) { particles.splice(n, 1); continue; }
            }
            var v1 = sampleVelocity(p.x, p.y, simT);
            if (!v1) { p.fade = Math.min(p.fade, 1 - 1 / FADE_STEPS); continue; }
            var mx = p.x + v1.vx * dt / 2, my = p.y + v1.vy * dt / 2;
            var v2 = sampleVelocity(mx, my, simT + dt / 2) || v1;
            p.x += v2.vx * dt;
            p.y += v2.vy * dt;
            p.speed = Math.hypot(v2.vx, v2.vy);
            if (!onIce(p.x, p.y)) p.fade = Math.min(p.fade, 1 - 1 / FADE_STEPS);
        }
    }

    // ---------------------------------------------------------------------
    // Rendering — canvas over the map; trails via destination-out fade.
    // ---------------------------------------------------------------------
    var canvas = document.getElementById('tracer-canvas');
    var ctx = canvas.getContext('2d');

    function sizeCanvas() {
        var r = canvas.parentNode.getBoundingClientRect();
        var dpr = window.devicePixelRatio || 1;
        canvas.width = Math.round(r.width * dpr);
        canvas.height = Math.round(r.height * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    sizeCanvas();
    window.addEventListener('resize', sizeCanvas);
    map.on('resize', sizeCanvas);
    // Trails smear under pan/zoom — clear on any camera move.
    map.on('move', function () { ctx.clearRect(0, 0, canvas.width, canvas.height); });

    // Speed color: same log ramp as the ice-v tiles (itslive_color_v.txt).
    var SPEED_RAMP = [
        [0, 8, 29, 88], [1, 34, 94, 168], [3, 29, 145, 192], [10, 65, 182, 196],
        [30, 127, 205, 187], [100, 199, 233, 180], [300, 255, 237, 160],
        [1000, 254, 178, 76], [3000, 240, 59, 32], [19200, 189, 0, 38]
    ];
    function speedColor(v) {
        var lo = SPEED_RAMP[0], hi = SPEED_RAMP[SPEED_RAMP.length - 1], i;
        for (i = 0; i < SPEED_RAMP.length - 1; i++) {
            if (v >= SPEED_RAMP[i][0] && v <= SPEED_RAMP[i + 1][0]) {
                lo = SPEED_RAMP[i]; hi = SPEED_RAMP[i + 1]; break;
            }
        }
        var t = hi[0] === lo[0] ? 0 :
            (Math.log(Math.max(v, 0.01) + 1) - Math.log(lo[0] + 1)) /
            (Math.log(hi[0] + 1) - Math.log(lo[0] + 1));
        t = Math.min(1, Math.max(0, t));
        return 'rgb(' + Math.round(lo[1] + t * (hi[1] - lo[1])) + ',' +
                        Math.round(lo[2] + t * (hi[2] - lo[2])) + ',' +
                        Math.round(lo[3] + t * (hi[3] - lo[3])) + ')';
    }

    function draw() {
        var w = canvas.getBoundingClientRect().width, h = canvas.getBoundingClientRect().height;
        if (cbTrails.checked) {
            ctx.globalCompositeOperation = 'destination-out';
            ctx.fillStyle = 'rgba(0,0,0,0.045)';
            ctx.fillRect(0, 0, w, h);
            ctx.globalCompositeOperation = 'source-over';
        } else {
            ctx.clearRect(0, 0, w, h);
        }
        if (!cbTracers.checked || !B) return;
        for (var n = 0; n < particles.length; n++) {
            var p = particles[n];
            var ll = ll3413ToLonLat(p.x, p.y);
            var pt = map.project(ll);
            if (pt.x < -10 || pt.y < -10 || pt.x > w + 10 || pt.y > h + 10) continue;
            ctx.globalAlpha = Math.max(0, Math.min(1, p.fade));
            ctx.fillStyle = speedColor(p.speed || 0);
            ctx.fillRect(pt.x - 1, pt.y - 1, 2.6, 2.6);
        }
        ctx.globalAlpha = 1;
    }

    // ---------------------------------------------------------------------
    // Time control + animation loop
    // ---------------------------------------------------------------------
    var slider = document.getElementById('gl-time');
    var dateEl = document.getElementById('gl-date');
    var playBtn = document.getElementById('gl-play');
    var YEARS_PER_SEC = 1.25;
    var MAX_STEP = 0.03;                 // yr — sub-steps keep RK2 honest
    var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    function tRange() {
        var ys = B.hdr.years;
        return [ys[0], ys[ys.length - 1] + 1];
    }
    function setSimT(t, fromSlider) {
        var r = tRange();
        simT = Math.min(r[1] - 0.001, Math.max(r[0], t));
        if (!fromSlider) slider.value = String((simT - r[0]) / (r[1] - r[0]));
        var yr = Math.floor(simT);
        var mo = Math.min(11, Math.floor((simT - yr) * 12));
        dateEl.textContent = MONTHS[mo] + ' ' + yr;
    }
    slider.addEventListener('input', function () {
        if (!B) return;
        var r = tRange();
        setSimT(r[0] + (+slider.value) * (r[1] - r[0]), true);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
    });
    playBtn.addEventListener('click', function () {
        playing = !playing;
        playBtn.textContent = playing ? '❚❚' : '▶';
    });

    var lastFrame = null, stepsSinceManage = 0;
    function frame(ts) {
        requestAnimationFrame(frame);
        if (!B) return;
        var dtReal = lastFrame == null ? 0 : Math.min(0.1, (ts - lastFrame) / 1000);
        lastFrame = ts;
        if (playing && cbTracers.checked) {
            var dtSim = dtReal * YEARS_PER_SEC;
            var r = tRange();
            if (simT + dtSim >= r[1]) {        // loop back to the start
                setSimT(r[0]);
                particles = [];
                ctx.clearRect(0, 0, canvas.width, canvas.height);
            }
            while (dtSim > 0) {
                var dt = Math.min(dtSim, MAX_STEP);
                stepParticles(dt);
                setSimT(simT + dt);
                dtSim -= dt;
                if (++stepsSinceManage >= 4) { manageDensity(); stepsSinceManage = 0; }
            }
        } else if (cbTracers.checked && B && particles.length === 0 && simT != null) {
            manageDensity();               // paused with no particles → seed
        }
        draw();
    }
    requestAnimationFrame(frame);

    // ---------------------------------------------------------------------
    // Site switching
    // ---------------------------------------------------------------------
    function activateSite(slug) {
        var entry = (CFG.catalog || []).filter(function (c) { return c.slug === slug; })[0];
        B = null; particles = [];
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        dateEl.textContent = 'loading…';
        loadBundle(slug).then(function (bundle) {
            B = bundle;
            setSimT(tRange()[0]);
            particles = [];
            manageDensity();
            if (entry && entry.center) {
                map.jumpTo({ center: entry.center, zoom: entry.zoom || 10 });
            }
        }).catch(function (e) {
            dateEl.textContent = 'no data';
            console.error('tracer bundle load failed:', e);
        });
    }
    siteSel.addEventListener('change', function () { activateSite(siteSel.value); });
    if (CFG.catalog && CFG.catalog.length) activateSite(CFG.catalog[0].slug);
})();
