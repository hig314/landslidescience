/* /glaciers/pairs/ — the LITERAL view of ITS_LIVE Level-2 data.
 *
 * Every mark on screen is one measured displacement over its own explicit
 * time interval. No gridding, no fitting, no integration, no model. The
 * counterpart to the tracer app: where that one shows a smooth field and
 * hides the sampling, this shows the sampling and hides nothing.
 *
 * Registration (autoRIFT source; Lei 2021, Gardner 2025): the map grid
 * node is the centre of the SEARCH window in image 2, with the image-1
 * template offset upstream by the a-priori reference displacement, which
 * is not published. So each measurement is drawn ARRIVING at its node and
 * reaching back v·dt upstream — approximate, with error (v − v_ref)·dt.
 *
 * A measurement is "active" at time t when t1 ≤ t ≤ t2. In swarm mode its
 * mark sits at node − v·(t2 − t): departing at t1, arriving at t2. What
 * you see is the data's own structure — dense in summer, thin in winter,
 * tight where correlation is good, and, at long separations, the
 * near-stationary haze of documented skipping/locking.
 */
(function () {
    'use strict';

    var CFG = window.PV_CONFIG || {};
    var DATA_V = '1';

    // ---- map (shared modules, same conventions as /glaciers/) -----------
    var DEFAULT_BASEMAP = 'esri-img';
    var basemaps = window.LSBasemaps.DEFAULTS.slice();
    function findBasemap(id) {
        for (var i = 0; i < basemaps.length; i++) if (basemaps[i].id === id) return basemaps[i];
        return null;
    }
    if (window.LSBasemaps.registerProtocols) window.LSBasemaps.registerProtocols();

    var _hash = window.LSHash ? window.LSHash.parse(location.hash) : { extras: {} };
    var _basemap = (_hash.base && findBasemap(_hash.base)) ? _hash.base : DEFAULT_BASEMAP;
    var _hadView = _hash.zoom != null;

    var map = new maplibregl.Map({
        container: 'pv-map',
        style: window.LSBasemaps.styleFor(findBasemap(_basemap)),
        center: _hadView ? [_hash.lon, _hash.lat] : [-146.98, 61.16],
        zoom: _hadView ? _hash.zoom : 11.4,
        transformRequest: window.LSBasemaps.transformRequest,
        attributionControl: { compact: true }
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');

    var bmSel = document.createElement('select');
    basemaps.forEach(function (bm) {
        var o = document.createElement('option');
        o.value = bm.id; o.textContent = bm.label;
        bmSel.appendChild(o);
    });
    bmSel.value = _basemap;
    bmSel.addEventListener('change', function () {
        var bm = findBasemap(bmSel.value);
        if (!bm) return;
        _basemap = bm.id;
        map.setStyle(window.LSBasemaps.styleFor(bm), { diff: false });
        writeHash();
    });
    document.getElementById('pv-basemap-row').appendChild(bmSel);

    // Swept-area selector. Each area is a separate raw sweep with its own
    // monthly series and harmonics, so switching reloads with ?bundle= —
    // the map view travels along in the hash.
    var siteSel = document.getElementById('pv-site');
    (CFG.bundles || [CFG.bundle]).forEach(function (b) {
        var o = document.createElement('option');
        o.value = b;
        o.textContent = b.replace('_pairs', '').replace(/(^|-)([a-z])/g,
            function (m, a, c) { return a + c.toUpperCase(); });
        if (b === CFG.bundle) o.selected = true;
        siteSel.appendChild(o);
    });
    siteSel.addEventListener('change', function () {
        location.href = location.pathname + '?bundle=' +
            encodeURIComponent(siteSel.value) + location.hash;
    });

    // ---- controls -------------------------------------------------------
    var elDtMin = document.getElementById('pv-dtmin'),
        elDtMax = document.getElementById('pv-dtmax'),
        elDtVal = document.getElementById('pv-dtval'),
        elMode = document.getElementById('pv-mode'),
        elColor = document.getElementById('pv-color'),
        elDecay = document.getElementById('pv-decay'),
        elSource = document.getElementById('pv-source'),
        elTime = document.getElementById('pv-time'),
        elDate = document.getElementById('pv-date'),
        elPlay = document.getElementById('pv-play'),
        elSpeed = document.getElementById('pv-speed'),
        elStats = document.getElementById('pv-stats');

    [['0.25', '¼×'], ['0.5', '½×'], ['1', '1×'], ['2', '2×'], ['4', '4×']]
        .forEach(function (o) {
            var e = document.createElement('option');
            e.value = o[0]; e.textContent = o[1];
            if (o[0] === '1') e.selected = true;
            elSpeed.appendChild(e);
        });

    // dt sliders: keep min <= max without fighting the user's drag.
    function dtLo() { return Math.min(+elDtMin.value, +elDtMax.value); }
    function dtHi() { return Math.max(+elDtMin.value, +elDtMax.value); }
    function syncDtLabel() {
        elDtVal.textContent = dtLo() + ' – ' + dtHi() + ' d';
    }
    elDtMin.addEventListener('input', function () { syncDtLabel(); writeHash(); });
    elDtMax.addEventListener('input', function () { syncDtLabel(); writeHash(); });
    syncDtLabel();

    // ---- data -----------------------------------------------------------
    var D = null;   // {i,j,vx,vy,t1,t2,dt,err,n,grid}
    var simT = null, playing = false, tRange = [0, 1];

    function load() {
        var b = CFG.bundle;
        return fetch(CFG.dataBase + b + '_vectors.json?v=' + DATA_V)
            .then(function (r) { if (!r.ok) throw new Error('header ' + r.status); return r.json(); })
            .then(function (hdr) {
                return fetch(CFG.dataBase + hdr.bin + '?v=' + DATA_V)
                    .then(function (r) { if (!r.ok) throw new Error('bin ' + r.status); return r.arrayBuffer(); })
                    .then(function (buf) {
                        function view(name, Ctor, bytes) {
                            var o = hdr.offsets[name];
                            return new Ctor(buf, o[0], o[1]);
                        }
                        var n = hdr.n;
                        var dtd = view('dt', Uint16Array);
                        var tm = view('t_mid', Float32Array);
                        var t1 = new Float32Array(n), t2 = new Float32Array(n);
                        for (var k = 0; k < n; k++) {
                            var h = dtd[k] / 2 / 365.25;
                            t1[k] = tm[k] - h; t2[k] = tm[k] + h;
                        }
                        D = {
                            n: n, grid: hdr.grid,
                            i: view('i', Int16Array), j: view('j', Int16Array),
                            vx: view('vx', Int16Array), vy: view('vy', Int16Array),
                            dt: dtd, err: view('v_error', Uint16Array),
                            t1: t1, t2: t2,
                            dtMaxYr: hdr.dt_range[1] / 365.25
                        };
                        tRange = hdr.t_range;
                        loadFitExtras(b);
                        elDtMin.max = elDtMax.max = String(Math.ceil(hdr.dt_range[1]));
                        if (_hash.extras.dtlo) elDtMin.value = _hash.extras.dtlo;
                        if (_hash.extras.dthi) elDtMax.value = _hash.extras.dthi;
                        syncDtLabel();
                        setT(_hash.extras.t != null ? parseFloat(_hash.extras.t) : tRange[0] + 0.6 * (tRange[1] - tRange[0]));
                        elStats.textContent = hdr.n.toLocaleString() + ' measurements loaded · ' +
                            'grid stride ' + hdr.stride + ' · ' +
                            hdr.t_range[0].toFixed(1) + '–' + hdr.t_range[1].toFixed(1);
                    });
            });
    }

    // Verdicts (per-record keep/reject from the robust fit) and the model
    // coefficients, loaded lazily — the raw view works without them.
    var V = null, M = null;
    function loadFitExtras(b) {
        fetch(CFG.dataBase + b + '_model.json?v=' + DATA_V)
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (mh) {
                if (!mh) return;
                return Promise.all([
                    fetch(CFG.dataBase + mh.verdict_bin + '?v=' + DATA_V)
                        .then(function (r) { return r.ok ? r.arrayBuffer() : null; }),
                    fetch(CFG.dataBase + mh.bin + '?v=' + DATA_V)
                        .then(function (r) { return r.ok ? r.arrayBuffer() : null; })
                ]).then(function (bufs) {
                    if (bufs[0]) V = new Uint8Array(bufs[0]);
                    if (bufs[1]) {
                        var supOff = mh.coef_count * 4 + mh.source_count;
                        M = {
                            grid: mh.grid, tRef: mh.t_ref,
                            tLo: mh.t_window ? mh.t_window[0] : 2015.0,
                            tHi: mh.t_window ? mh.t_window[1] : mh.t_ref + 1.0,
                            coef: new Float32Array(bufs[1], 0, mh.coef_count),
                            source: new Uint8Array(bufs[1], mh.coef_count * 4, mh.source_count),
                            tFirst: mh.support_count
                                ? new Float32Array(bufs[1].slice(supOff, supOff + mh.support_count * 4))
                                : null,
                            tLast: mh.support_count
                                ? new Float32Array(bufs[1].slice(supOff + mh.support_count * 4,
                                                                 supOff + mh.support_count * 8))
                                : null
                        };
                    }
                });
            })
            .catch(function (e) { console.warn('fit extras unavailable', e); });
    }

    // Model velocity at time t for grid cell (i, j); null where unfitted.
    function modelAt(i, j, t) {
        if (!M) return null;
        t = Math.min(M.tHi != null ? M.tHi : M.tRef + 1, Math.max(M.tLo != null ? M.tLo : 2015, t));
        var g = M.grid;
        if (i < 0 || j < 0 || i >= g.ny || j >= g.nx) return null;
        var kk = i * g.nx + j;
        var src = M.source[kk];
        if (!src) return null;
        if (M.tLast && (t > M.tLast[kk] + 0.5 || t < M.tFirst[kk] - 0.5)) return null;
        var o = (i * g.nx + j) * 8;
        var tp = 2 * Math.PI, dtr = t - M.tRef;
        var co = Math.cos(tp * t), si = Math.sin(tp * t);
        return {
            vx: M.coef[o] + M.coef[o+1] * dtr + M.coef[o+2] * co + M.coef[o+3] * si,
            vy: M.coef[o+4] + M.coef[o+5] * dtr + M.coef[o+6] * co + M.coef[o+7] * si,
            src: src
        };
    }

    // ---- time -----------------------------------------------------------
    var MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    function setT(t, fromSlider) {
        simT = Math.min(tRange[1], Math.max(tRange[0], t));
        if (!fromSlider) {
            elTime.value = String((simT - tRange[0]) / (tRange[1] - tRange[0]));
        }
        var yr = Math.floor(simT);
        var mo = Math.min(11, Math.floor((simT - yr) * 12));
        var day = Math.max(1, Math.round(((simT - yr) * 12 - mo) * 30.4) + 1);
        elDate.textContent = day + ' ' + MONTHS[mo] + ' ' + yr;
    }
    elTime.addEventListener('input', function () {
        if (!D) return;
        setT(tRange[0] + (+elTime.value) * (tRange[1] - tRange[0]), true);
    });
    elTime.addEventListener('change', writeHash);
    elPlay.addEventListener('click', function () {
        if (!playing && simT >= tRange[1] - 1e-4) setT(tRange[0]);
        playing = !playing;
        elPlay.textContent = playing ? '❚❚' : '▶';
        if (!playing) writeHash();
    });
    document.addEventListener('keydown', function (e) {
        if (!D) return;
        var tag = (e.target && e.target.tagName) || '';
        if (tag === 'INPUT' || tag === 'SELECT') return;
        var step = e.shiftKey ? 1 / 12 : 1 / 365.25;
        if (e.key === 'ArrowRight') { setT(simT + step); e.preventDefault(); }
        else if (e.key === 'ArrowLeft') { setT(simT - step); e.preventDefault(); }
        else if (e.key === ' ') { elPlay.click(); e.preventDefault(); }
    });

    // ---- colour ---------------------------------------------------------
    var SPEED_RAMP = [[0,8,29,88],[1,34,94,168],[3,29,145,192],[10,65,182,196],
        [30,127,205,187],[100,199,233,180],[300,255,237,160],[1000,254,178,76],
        [3000,240,59,32],[19200,189,0,38]];
    function rampColor(v) {
        var lo = SPEED_RAMP[0], hi = SPEED_RAMP[SPEED_RAMP.length - 1], i;
        for (i = 0; i < SPEED_RAMP.length - 1; i++) {
            if (v >= SPEED_RAMP[i][0] && v <= SPEED_RAMP[i+1][0]) {
                lo = SPEED_RAMP[i]; hi = SPEED_RAMP[i+1]; break;
            }
        }
        var t = hi[0] === lo[0] ? 0 :
            (Math.log(Math.max(v,0.01)+1) - Math.log(lo[0]+1)) /
            (Math.log(hi[0]+1) - Math.log(lo[0]+1));
        t = Math.min(1, Math.max(0, t));
        return [Math.round(lo[1]+t*(hi[1]-lo[1])), Math.round(lo[2]+t*(hi[2]-lo[2])),
                Math.round(lo[3]+t*(hi[3]-lo[3]))];
    }
    var _lut = [];
    (function () {
        for (var k = 0; k < 64; k++) {
            var v = Math.exp(Math.log(0.5) + (k/63)*(Math.log(20000)-Math.log(0.5)));
            var c = rampColor(v);
            _lut.push('rgb(' + c[0] + ',' + c[1] + ',' + c[2] + ')');
        }
    })();
    function speedCol(v) {
        var k = Math.round((Math.log(Math.max(v,0.5))-Math.log(0.5)) /
                           (Math.log(20000)-Math.log(0.5)) * 63);
        return _lut[Math.min(63, Math.max(0, k))];
    }
    // Separation colour: short = teal, long = magenta (so the skip/lock
    // haze is instantly identifiable as "these are the long pairs").
    function dtCol(d) {
        var t = Math.min(1, Math.log(1 + d) / Math.log(1 + 550));
        return 'rgb(' + Math.round(20 + 200*t) + ',' + Math.round(170 - 120*t) + ',' +
               Math.round(150 + 40*t) + ')';
    }
    function dirCol(vx, vy) {
        var a = (Math.atan2(vy, vx) * 180 / Math.PI + 360) % 360;
        return 'hsl(' + a.toFixed(0) + ',75%,55%)';
    }

    // ---- render ---------------------------------------------------------
    var canvas = document.getElementById('pv-canvas');
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

    function nodeXY(k) {
        var g = D.grid;
        return [g.x0 + (D.j[k] + 0.5) * g.dx, g.y0_north - (D.i[k] + 0.5) * g.dx];
    }

    // First index with t1 >= value (D.t1 is sorted ascending).
    function lowerBound(val) {
        var lo = 0, hi = D.n;
        while (lo < hi) {
            var mid = (lo + hi) >> 1;
            if (D.t1[mid] < val) lo = mid + 1; else hi = mid;
        }
        return lo;
    }

    var lastCount = 0;
    function draw() {
        var w = canvas.getBoundingClientRect().width,
            h = canvas.getBoundingClientRect().height;
        var decay = elDecay.value;
        if (decay === 'trails') {
            ctx.globalCompositeOperation = 'destination-out';
            ctx.fillStyle = 'rgba(0,0,0,0.10)';
            ctx.fillRect(0, 0, w, h);
            ctx.globalCompositeOperation = 'source-over';
        } else if (decay === 'off') {
            ctx.clearRect(0, 0, w, h);
        }
        // 'accum': never erase — measurements pile up so a whole season's
        // (or decade's) sampling builds into one picture. Marks are drawn
        // fainter so density, not saturation, carries the signal.
        var accum = decay === 'accum';
        if (!D || simT == null) return;

        var lo = dtLo(), hi = dtHi();
        var mode = elMode.value, cmode = elColor.value;
        var smode = elSource.value;
        if (smode === 'fitted') { drawFitted(w, h, cmode, accum); lastCount = fittedCount; return; }
        var drawSwarm = mode === 'swarm' || mode === 'both';
        var drawVec = mode === 'vector' || mode === 'both';
        // Candidates: intervals starting within dtMax before now.
        var kStart = lowerBound(simT - D.dtMaxYr);
        var kEnd = lowerBound(simT + 1e-6);
        var n = 0;
        ctx.lineWidth = 1.1;
        for (var k = kStart; k < kEnd; k++) {
            if (D.t2[k] < simT) continue;
            var d = D.dt[k];
            if (d < lo || d > hi) continue;
            if (V) {
                var vd = V[k];
                if (smode === 'kept' && vd !== 1) continue;
                if (smode === 'rejected' && vd !== 0) continue;
            } else if (smode === 'kept' || smode === 'rejected') {
                continue;   // verdicts not loaded yet
            }
            var xy = nodeXY(k);
            var vx = D.vx[k], vy = D.vy[k];
            var yrs = d / 365.25;
            // departure = arrival - v*dt ; position at t = arrival - v*(t2 - t)
            var backA = D.t2[k] - simT;
            var pxA = xy[0] - vx * backA, pyA = xy[1] - vy * backA;
            var ll = window.LSProj.toLonLat(pxA, pyA);
            var p = map.project(ll);
            if (p.x < -40 || p.y < -40 || p.x > w + 40 || p.y > h + 40) continue;
            n++;
            var spd = Math.hypot(vx, vy);
            var col = cmode === 'speed' ? speedCol(spd)
                    : cmode === 'dt' ? dtCol(d) : dirCol(vx, vy);
            if (drawVec) {
                var l0 = window.LSProj.toLonLat(xy[0] - vx * yrs, xy[1] - vy * yrs);
                var q0 = map.project(l0), q1 = map.project(window.LSProj.toLonLat(xy[0], xy[1]));
                ctx.strokeStyle = col;
                ctx.globalAlpha = accum ? 0.10 : 0.28;
                ctx.beginPath(); ctx.moveTo(q0.x, q0.y); ctx.lineTo(q1.x, q1.y); ctx.stroke();
            }
            if (drawSwarm) {
                ctx.globalAlpha = accum ? 0.30 : 0.85;
                ctx.fillStyle = col;
                ctx.fillRect(p.x - 1.2, p.y - 1.2, 2.4, 2.4);
            }
        }
        ctx.globalAlpha = 1;
        lastCount = n;
    }

    // Fitted mode: sample the model on the grid and advect each sample over a
    // nominal short window, so it reads in the same visual language as the
    // measurements it is meant to be compared against. Cells that exist only
    // by extrapolation or spatial fill are drawn dimmer — interpolated
    // coverage should never look as solid as measured coverage.
    var fittedCount = 0;
    function drawFitted(w, h, cmode, accum) {
        fittedCount = 0;
        if (!M) return;
        var g = M.grid;
        var c = map.getCenter();
        var mpp = 156543.03392 * Math.cos(c.lat * Math.PI / 180) / Math.pow(2, map.getZoom());
        var step = Math.max(1, Math.round(9 * mpp / g.dx));
        var NOM = 16 / 365.25;                     // nominal 16-day window
        var frac = ((simT / NOM) % 1 + 1) % 1;     // phase within that window
        for (var i = 0; i < g.ny; i += step) {
            for (var j = 0; j < g.nx; j += step) {
                var m = modelAt(i, j, simT);
                if (!m) continue;
                var x = g.x0 + (j + 0.5) * g.dx, y = g.y0_north - (i + 0.5) * g.dx;
                var back = (1 - frac) * NOM;
                var ll = window.LSProj.toLonLat(x - m.vx * back, y - m.vy * back);
                var p = map.project(ll);
                if (p.x < -20 || p.y < -20 || p.x > w + 20 || p.y > h + 20) continue;
                fittedCount++;
                var spd = Math.hypot(m.vx, m.vy);
                ctx.fillStyle = cmode === 'dir' ? dirCol(m.vx, m.vy) : speedCol(spd);
                ctx.globalAlpha = (m.src === 1 ? 0.9 : m.src === 2 ? 0.55 : 0.3) * (accum ? 0.4 : 1);
                ctx.fillRect(p.x - 1.2, p.y - 1.2, 2.4, 2.4);
            }
        }
        ctx.globalAlpha = 1;
    }

    map.on('render', function () { if (D && map.isMoving()) draw(); });
    // A pan/zoom invalidates accumulated pixels (they were drawn in the old
    // screen frame), so clear and let the picture rebuild.
    map.on('movestart', function () {
        if (elDecay.value === 'accum') ctx.clearRect(0, 0, canvas.width, canvas.height);
    });
    map.on('moveend', writeHash);
    // Changing what's drawn should also start the accumulation over.
    [elDecay, elMode, elColor, elDtMin, elDtMax, elSource].forEach(function (el) {
        el.addEventListener('input', function () {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
        });
    });

    var last = null;
    function frame(ts) {
        requestAnimationFrame(frame);
        if (!D) return;
        var dtReal = last == null ? 0 : Math.min(0.1, (ts - last) / 1000);
        last = ts;
        if (playing) {
            if (simT >= tRange[1] - 1e-4) { playing = false; elPlay.textContent = '▶'; }
            else setT(simT + dtReal * parseFloat(elSpeed.value));
        }
        if (!map.isMoving()) draw();
        if (insCell && playing && (ts % 500) < 20) drawInspector();
        if (D && elStats.dataset.base == null) elStats.dataset.base = elStats.textContent;
        if (D) {
            elStats.textContent = (elStats.dataset.base || '') +
                ' · showing ' + lastCount.toLocaleString() + ' now';
        }
    }
    requestAnimationFrame(frame);

    // ---------------------------------------------------------------------
    // POINT INSPECTOR — click a cell, see every measurement it ever made.
    // Some calls in this data cannot be made statistically (blunders in fast
    // areas look exactly like real slow motion in slow areas), so the tool
    // exists to put a human in the loop rather than to hide the ambiguity.
    // ---------------------------------------------------------------------
    var insEl = document.getElementById('pv-inspect');
    var insCv = document.getElementById('pv-ins-canvas');
    var insCx = insCv.getContext('2d');
    var insTitle = document.getElementById('pv-ins-title');
    var insLegend = document.getElementById('pv-ins-legend');
    var insCell = null;
    var insView = null;     // {t0,t1,v0,v1} or null = auto-fit
    var insDrag = null;     // in-progress zoom box

    // The assumption-free monthly series (tools/fit_monthly_tv.py). 7 MB, so
    // it loads lazily on the first inspector open rather than on page load.
    var MON = null, monPending = false;
    function loadMonthly(then) {
        if (MON) { then && then(); return; }
        if (monPending) return;
        monPending = true;
        var site = (CFG.bundle || 'columbia_pairs').replace('_pairs', '');
        fetch(CFG.dataBase + site + '_monthly.json?v=' + DATA_V)
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (mh) {
                if (!mh) throw new Error('no monthly header');
                return fetch(CFG.dataBase + mh.bin + '?v=' + DATA_V)
                    .then(function (r) { return r.arrayBuffer(); })
                    .then(function (buf) {
                        var g = mh.grid, n = mh.n_months * g.ny * g.nx;
                        MON = {
                            hdr: mh, g: g, nm: mh.n_months, nod: mh.nodata,
                            vx: new Int16Array(buf, 0, n),
                            vy: new Int16Array(buf, n * 2, n)
                        };
                        then && then();
                    });
            })
            .catch(function (e) { console.warn('monthly series unavailable', e); })
            .then(function () { monPending = false; });
    }

    function monthlySeries(i, j) {
        if (!MON) return null;
        var g = MON.g, out = [], plane = g.ny * g.nx, k = i * g.nx + j;
        for (var m = 0; m < MON.nm; m++) {
            var a = MON.vx[m * plane + k], b = MON.vy[m * plane + k];
            out.push((a === MON.nod || b === MON.nod) ? null
                     : { t: MON.hdr.t0 + (m + 0.5) / 12, vx: a, vy: b });
        }
        return out;
    }

    // Harmonic decomposition of THIS cell, computed in the browser so the
    // analysis is inspectable rather than baked into a file: remove the
    // secular part (centred 13-month mean), project onto harmonics of one
    // year, and keep each only if odd and even years agree — the same
    // replication rule the offline tool applies.
    // Harmonic gate. A truncated Fourier series can describe any repeatable
    // shape, which is the point — but on slow cells the anomaly is mostly
    // noise, and unconstrained higher harmonics fit it as high-frequency
    // spikes. (Measured: h3 was retained in 66% of slow cells, MORE than h2,
    // which is backwards for a physical cycle.) Four constraints, chosen to
    // encode what a real seasonal cycle looks like rather than to tune an
    // outcome:
    //   1. HIERARCHY  - h_k only if h_(k-1) survived. A third harmonic
    //                   without a first is a shape no seasonal cycle has.
    //   2. DECAY      - amp_k < 0.7 * amp_(k-1). Real cycles put most of
    //                   their power in the fundamental.
    //   3. AGREEMENT  - the odd/even-year replication bar RISES with k,
    //                   since higher frequencies replicate by chance more
    //                   easily against temporally-correlated residuals.
    //   4. PLAUSIBILITY - total harmonic amplitude capped against the cell's
    //                   own mean speed; beyond that the reconstruction
    //                   implies part-year reversal.
    var H_AGREE = [0.60, 0.78, 0.88];
    var H_DECAY = 0.7;
    var H_MIN_AMP = 3;
    var H_TOTAL_FRAC = 0.6;

    function harmonicFit(series) {
        var n = series.length;
        var ok = series.map(function (p) { return !!p; });
        if (ok.filter(Boolean).length < 60) return null;
        var vx = series.map(function (p) { return p ? p.vx : 0; });
        var vy = series.map(function (p) { return p ? p.vy : 0; });
        var t = series.map(function (p, m) { return p ? p.t : MON.hdr.t0 + (m + 0.5) / 12; });
        var meanSpd = 0, cnt = 0;
        for (var q = 0; q < n; q++) if (ok[q]) { meanSpd += Math.hypot(vx[q], vy[q]); cnt++; }
        meanSpd = cnt ? meanSpd / cnt : 0;

        var W = 13, half = 6;
        function smooth(a) {
            var o = [];
            for (var m = 0; m < n; m++) {
                var s = 0, c = 0;
                for (var d = -half; d <= half; d++) {
                    var k = Math.min(n - 1, Math.max(0, m + d));
                    if (ok[k]) { s += a[k]; c++; }
                }
                o.push(c ? s / c : 0);
            }
            return o;
        }
        var sx = smooth(vx), sy = smooth(vy);
        var ax = vx.map(function (v, m) { return v - sx[m]; });
        var ay = vy.map(function (v, m) { return v - sy[m]; });
        var fitx = new Array(n).fill(0), fity = new Array(n).fill(0);
        var kept = [], amps = [], prevAmp = Infinity, totalAmp = 0;

        for (var h = 1; h <= 3; h++) {
            function proj(res, mask) {
                var cc = 0, ss = 0, nn = 0;
                for (var m = 0; m < n; m++) {
                    if (!ok[m] || (mask && !mask(m))) continue;
                    var c = Math.cos(2 * Math.PI * h * t[m]), si = Math.sin(2 * Math.PI * h * t[m]);
                    cc += res[m] * c; ss += res[m] * si; nn += c * c;
                }
                return nn > 0 ? [cc / nn, ss / nn] : [0, 0];
            }
            var rx = ax.map(function (v, m) { return v - fitx[m]; });
            var ry = ay.map(function (v, m) { return v - fity[m]; });
            var px_ = proj(rx), py_ = proj(ry);
            var even = function (m) { return Math.floor(t[m]) % 2 === 0; };
            var odd = function (m) { return Math.floor(t[m]) % 2 !== 0; };
            var A = proj(rx, even).concat(proj(ry, even));
            var B = proj(rx, odd).concat(proj(ry, odd));
            var nA = Math.hypot(A[0], A[1], A[2], A[3]), nB = Math.hypot(B[0], B[1], B[2], B[3]);
            var dot = A[0]*B[0] + A[1]*B[1] + A[2]*B[2] + A[3]*B[3];
            var cosv = (nA > 0 && nB > 0) ? dot / (nA * nB) : 0;
            var ratio = Math.max(nA, nB) / Math.max(Math.min(nA, nB), 1e-9);
            var amp = Math.hypot(px_[0], px_[1], py_[0], py_[1]);

            // Extra constraints apply to HIGHER harmonics only — the
            // fundamental carries most of the real signal and gating it the
            // same way discarded the part that works.
            var hierarchyOK = (h === 1) || (kept.indexOf(h - 1) >= 0);
            var decayOK = (h === 1) || (amp < H_DECAY * prevAmp);
            var plausibleOK = (h === 1) ||
                ((totalAmp + amp) < Math.max(H_MIN_AMP * 2, H_TOTAL_FRAC * meanSpd));
            if (hierarchyOK && decayOK && plausibleOK &&
                cosv > H_AGREE[h - 1] && ratio < 2.5 && amp > H_MIN_AMP) {
                kept.push(h); amps.push(amp);
                prevAmp = amp; totalAmp += amp;
                for (var m = 0; m < n; m++) {
                    var c2 = Math.cos(2 * Math.PI * h * t[m]), s2 = Math.sin(2 * Math.PI * h * t[m]);
                    fitx[m] += px_[0] * c2 + px_[1] * s2;
                    fity[m] += py_[0] * c2 + py_[1] * s2;
                }
            }
        }
        return { t: t, ok: ok, sx: sx, sy: sy, fx: fitx, fy: fity,
                 kept: kept, meanSpd: meanSpd };
    }

    // ---- inspector chrome + zoom -----------------------------------------
    var insLast = null;     // pixel->data inverses from the last draw
    var insDrag = null;     // in-progress zoom box
    var insFull = null;     // full extent of data + fits = reset view AND
                            // the zoom-out bound
    function clampView(v) {
        if (!insFull) return v;
        var t0 = Math.max(insFull.t0, v.t0), t1 = Math.min(insFull.t1, v.t1);
        var v0 = Math.max(insFull.v0, v.v0), v1 = Math.min(insFull.v1, v.v1);
        if (t1 - t0 < 1e-3 || v1 - v0 < 1e-6) return null;   // degenerate -> reset
        // Fully zoomed out on both axes is the same as no view at all.
        if (t0 <= insFull.t0 + 1e-9 && t1 >= insFull.t1 - 1e-9 &&
            v0 <= insFull.v0 + 1e-9 && v1 >= insFull.v1 - 1e-9) return null;
        return { t0: t0, t1: t1, v0: v0, v1: v1 };
    }

    document.getElementById('pv-ins-close').addEventListener('click', function () {
        insEl.style.display = 'none'; insCell = null;
    });
    document.getElementById('pv-ins-reset').addEventListener('click', function () {
        insView = null; drawInspector();
    });
    // Panel is CSS-resizable; keep the canvas filling whatever height is left.
    if (window.ResizeObserver) {
        new ResizeObserver(function () { if (insCell) drawInspector(); }).observe(insEl);
    }

    function insCoords(ev) {
        var r = insCv.getBoundingClientRect();
        return [ev.clientX - r.left, ev.clientY - r.top];
    }
    insCv.addEventListener('pointerdown', function (ev) {
        if (!insCell) return;
        var c = insCoords(ev);
        insDrag = { x0: c[0], y0: c[1], x1: c[0], y1: c[1] };
        insCv.setPointerCapture(ev.pointerId);
        ev.preventDefault();
    });
    insCv.addEventListener('pointermove', function (ev) {
        if (!insDrag) return;
        var c = insCoords(ev);
        insDrag.x1 = c[0]; insDrag.y1 = c[1];
        drawInspector();
    });
    insCv.addEventListener('pointerup', function (ev) {
        if (!insDrag) return;
        var d = insDrag; insDrag = null;
        if (Math.abs(d.x1 - d.x0) > 6 && Math.abs(d.y1 - d.y0) > 6 && insLast) {
            var f = insLast;
            var ta = f.invx(Math.min(d.x0, d.x1)), tb = f.invx(Math.max(d.x0, d.x1));
            var vb = f.invy(Math.min(d.y0, d.y1)), va = f.invy(Math.max(d.y0, d.y1));
            insView = clampView({ t0: ta, t1: tb, v0: Math.max(0, va), v1: vb });
        }
        drawInspector();
    });
    insCv.addEventListener('dblclick', function () { insView = null; drawInspector(); });
    // Wheel zoom about the cursor (shift = time axis only). Anchoring on the
    // pointer keeps whatever you are looking at fixed while the scale changes.
    insCv.addEventListener('wheel', function (ev) {
        if (!insCell || !insLast) return;
        ev.preventDefault();
        var c = insCoords(ev);
        var tAt = insLast.invx(c[0]), vAt = insLast.invy(c[1]);
        var fz = Math.exp((ev.deltaY > 0 ? 1 : -1) * -0.18);
        var cur = insView || { t0: insLast.invx(insLast.L0), t1: insLast.invx(insLast.W0),
                               v0: insLast.invy(insLast.H0), v1: insLast.invy(insLast.T0) };
        var nt0 = tAt - (tAt - cur.t0) / fz, nt1 = tAt + (cur.t1 - tAt) / fz;
        var nv0 = cur.v0, nv1 = cur.v1;
        if (!ev.shiftKey) {
            nv0 = vAt - (vAt - cur.v0) / fz;
            nv1 = vAt + (cur.v1 - vAt) / fz;
        }
        if (nt1 - nt0 < 0.05) return;
        insView = clampView({ t0: nt0, t1: nt1, v0: Math.max(0, nv0),
                              v1: Math.max(nv0 + 1, nv1) });
        drawInspector();
    }, { passive: false });

    map.on('click', function (e) {
        if (!D) return;
        var g = D.grid;
        var xy = window.LSProj.fromLonLat(e.lngLat.lng, e.lngLat.lat);
        var j = Math.round((xy[0] - g.x0) / g.dx - 0.5);
        var i = Math.round((g.y0_north - xy[1]) / g.dx - 0.5);
        if (i < 0 || j < 0 || i >= g.ny || j >= g.nx) return;
        insCell = [i, j];
        loadMonthly(function () { drawInspector(); });
        drawInspector();
        insEl.style.display = 'block';
    });

    function drawInspector() {
        if (!insCell) return;
        var i = insCell[0], j = insCell[1];
        // Collect this cell's records. The bundle is strided, so snap to the
        // nearest sampled node rather than silently showing nothing.
        var idx = [];
        for (var k = 0; k < D.n; k++) {
            if (Math.abs(D.i[k] - i) <= 1 && Math.abs(D.j[k] - j) <= 1) idx.push(k);
        }
        var w = insCv.clientWidth;
        var h = Math.max(120, insEl.clientHeight - 62);
        insCv.style.height = h + 'px';
        var dpr = window.devicePixelRatio || 1;
        insCv.width = w * dpr; insCv.height = h * dpr;
        insCx.setTransform(dpr, 0, 0, dpr, 0, 0);
        insCx.clearRect(0, 0, w, h);
        if (!idx.length) {
            insTitle.textContent = 'Point history — no measurements here';
            insLegend.textContent = '';
            return;
        }
        var spd = idx.map(function (k) { return Math.hypot(D.vx[k], D.vy[k]); });
        // Full extent: every measurement AND every curve we draw over them.
        // This is both the default view and the zoom-out bound, so nothing is
        // ever off-screen at reset and you cannot zoom past "everything".
        var smax = 50, tlo = Infinity, thi = -Infinity;
        idx.forEach(function (k, q) {
            if (spd[q] > smax) smax = spd[q];
            if (D.t1[k] < tlo) tlo = D.t1[k];
            if (D.t2[k] > thi) thi = D.t2[k];
        });
        if (!isFinite(tlo)) { tlo = tRange[0]; thi = tRange[1]; }
        var serFull = MON ? monthlySeries(i, j) : null;
        if (serFull) {
            serFull.forEach(function (pt) {
                if (!pt) return;
                var v = Math.hypot(pt.vx, pt.vy);
                if (v > smax) smax = v;
                if (pt.t < tlo) tlo = pt.t;
                if (pt.t > thi) thi = pt.t;
            });
            var hkFull = harmonicFit(serFull);
            if (hkFull) {
                for (var q2 = 0; q2 < hkFull.t.length; q2++) {
                    if (!hkFull.ok[q2]) continue;
                    var vf = Math.hypot(hkFull.sx[q2] + hkFull.fx[q2],
                                        hkFull.sy[q2] + hkFull.fy[q2]);
                    if (vf > smax) smax = vf;
                }
            }
        }
        smax *= 1.04;
        insFull = { t0: tlo - 0.05, t1: thi + 0.05, v0: 0, v1: smax };
        var L = 44, R = 8, T = 8, B2 = 18;
        var t0 = insView ? insView.t0 : insFull.t0;
        var t1 = insView ? insView.t1 : insFull.t1;
        var v0 = insView ? insView.v0 : insFull.v0;
        var v1 = insView ? insView.v1 : insFull.v1;
        function px(t) { return L + (t - t0) / (t1 - t0) * (w - L - R); }
        function py(v) { return h - B2 - (v - v0) / Math.max(v1 - v0, 1e-6) * (h - T - B2); }
        insLast = {
            invx: function (X) { return t0 + (X - L) / (w - L - R) * (t1 - t0); },
            invy: function (Y) { return v0 + (h - B2 - Y) / (h - T - B2) * (v1 - v0); },
            L0: L, W0: w - R, T0: T, H0: h - B2
        };
        insCx.save();
        insCx.beginPath(); insCx.rect(L, T, w - L - R, h - T - B2); insCx.clip();
        // axes
        insCx.strokeStyle = '#ddd'; insCx.lineWidth = 1;
        insCx.beginPath();
        insCx.moveTo(L, T); insCx.lineTo(L, h - B2); insCx.lineTo(w - R, h - B2);
        insCx.stroke();
        insCx.fillStyle = '#999'; insCx.font = '9px sans-serif';
        for (var g = 0; g <= 4; g++) {
            var vv = v0 + (v1 - v0) * g / 4;
            insCx.fillText(vv >= 100 ? vv.toFixed(0) : vv.toFixed(1), 2, py(vv) + 3);
            insCx.strokeStyle = '#f4f4f4';
            insCx.beginPath(); insCx.moveTo(L, py(vv)); insCx.lineTo(w - R, py(vv)); insCx.stroke();
        }
        insCx.fillText('m/yr', 2, T + 8);
        var span = t1 - t0;
        // Every year boundary gets a mark, always — seasonality is only
        // readable against the annual grid. Labels thin out when they would
        // collide; months appear once there is room for them.
        var labelEvery = span > 24 ? 5 : span > 10 ? 2 : 1;
        for (var y = Math.ceil(t0); y < t1; y += 1) {
            insCx.strokeStyle = '#dcdcdc';
            insCx.beginPath(); insCx.moveTo(px(y), T); insCx.lineTo(px(y), h - B2); insCx.stroke();
            if (Math.round(y) % labelEvery === 0) {
                insCx.fillStyle = '#777';
                insCx.fillText(String(Math.round(y)), px(y) - 12, h - 5);
            }
        }
        if (span < 6) {                       // month marks, unlabelled below 3 yr
            for (var ym = Math.floor(t0); ym < t1 + 1; ym += 1) {
                for (var mo = 1; mo < 12; mo++) {
                    var tm2 = ym + mo / 12;
                    if (tm2 < t0 || tm2 > t1) continue;
                    insCx.strokeStyle = '#f4f4f4';
                    insCx.beginPath(); insCx.moveTo(px(tm2), T); insCx.lineTo(px(tm2), h - B2); insCx.stroke();
                    if (span < 3 && mo % 3 === 0) {
                        insCx.fillStyle = '#bbb';
                        insCx.fillText(MONTHS[mo], px(tm2) - 8, h - 5);
                    }
                }
            }
        }
        // each measurement: a horizontal bar spanning its own interval
        var nk = 0, nr = 0;
        idx.forEach(function (k, q) {
            var v = spd[q];
            var t1m = D.t1[k], t2m = D.t2[k];
            if (t2m < t0 || t1m > t1) return;
            var verdict = V ? V[k] : 2;
            if (verdict === 1) { insCx.strokeStyle = 'rgba(30,120,200,0.55)'; nk++; }
            else if (verdict === 0) { insCx.strokeStyle = 'rgba(210,70,50,0.5)'; nr++; }
            else insCx.strokeStyle = 'rgba(150,150,150,0.4)';
            var yy = py(v);
            if (yy < T - 40 || yy > h - B2 + 40) return;
            insCx.lineWidth = 1.4;
            insCx.beginPath();
            insCx.moveTo(px(Math.max(t1m, t0)), yy);
            insCx.lineTo(px(Math.min(t2m, t1)), yy);
            insCx.stroke();
            insCx.lineWidth = 1;
        });
        // (1) assumption-free monthly series, (2) secular + retained
        // harmonics. Drawn before the parametric model so the reader sees
        // increasing assumption from bottom to top of the legend.
        var hk = null;
        if (MON) {
            var ser = monthlySeries(i, j);
            if (ser) {
                // Monthly series is the reference curve: drawn PALE and THICK
                // so it reads as a band behind everything else, rather than
                // competing with the thin model lines drawn over it.
                insCx.strokeStyle = 'rgba(20,150,90,0.30)'; insCx.lineWidth = 5;
                insCx.beginPath();
                var st = false;
                ser.forEach(function (pt) {
                    if (!pt) { st = false; return; }
                    var X = px(pt.t), Y = py(Math.hypot(pt.vx, pt.vy));
                    if (!st) { insCx.moveTo(X, Y); st = true; } else insCx.lineTo(X, Y);
                });
                insCx.stroke();
                hk = harmonicFit(ser);
                if (hk) {
                    insCx.strokeStyle = 'rgba(150,60,190,0.95)';
                    insCx.lineWidth = 2;
                    insCx.beginPath(); st = false;
                    for (var m2 = 0; m2 < hk.t.length; m2++) {
                        if (!hk.ok[m2]) { st = false; continue; }
                        var vX = hk.sx[m2] + hk.fx[m2], vY = hk.sy[m2] + hk.fy[m2];
                        var X2 = px(hk.t[m2]), Y2 = py(Math.hypot(vX, vY));
                        if (!st) { insCx.moveTo(X2, Y2); st = true; } else insCx.lineTo(X2, Y2);
                    }
                    insCx.stroke();
                }
                insCx.lineWidth = 1;
            }
        }
        // the fitted model over the same window
        if (M) {
            insCx.strokeStyle = 'rgba(15,15,15,0.95)'; insCx.lineWidth = 1.3;
            insCx.setLineDash([5, 3]);
            insCx.beginPath();
            var started = false;
            for (var t = t0; t <= t1; t += 1 / 48) {
                var m = modelAt(i, j, t);
                if (!m) { started = false; continue; }
                var pv = Math.hypot(m.vx, m.vy);
                if (!started) { insCx.moveTo(px(t), py(pv)); started = true; }
                else insCx.lineTo(px(t), py(pv));
            }
            insCx.stroke();
            insCx.setLineDash([]);
            insCx.lineWidth = 1;
        }
        // current time marker
        insCx.strokeStyle = '#c9971c';
        insCx.beginPath(); insCx.moveTo(px(simT), T); insCx.lineTo(px(simT), h - B2); insCx.stroke();
        insCx.restore();
        if (insDrag) {
            insCx.strokeStyle = 'rgba(60,60,60,0.9)';
            insCx.setLineDash([4, 3]);
            insCx.strokeRect(Math.min(insDrag.x0, insDrag.x1), Math.min(insDrag.y0, insDrag.y1),
                             Math.abs(insDrag.x1 - insDrag.x0), Math.abs(insDrag.y1 - insDrag.y0));
            insCx.setLineDash([]);
        }
        var ll = window.LSProj.toLonLat(D.grid.x0 + (j + 0.5) * D.grid.dx,
                                        D.grid.y0_north - (i + 0.5) * D.grid.dx);
        insTitle.textContent = 'Point history — ' + ll[1].toFixed(4) + ', ' + ll[0].toFixed(4);
        insLegend.innerHTML =
            (V
                ? '<span class="pv-sw" style="background:rgb(30,120,200)"></span>kept ' + nk +
                  '<span class="pv-sw" style="background:rgb(210,70,50)"></span>rejected ' + nr
                : '<span class="pv-sw" style="background:#999"></span>' +
                  idx.length + ' pairs — filter verdicts not built for this area') +
            '<span class="pv-sw" style="background:rgba(20,150,90,0.45)"></span>monthly (no cycle assumed)' +
            '<span class="pv-sw" style="background:rgb(150,60,190)"></span>secular + harmonics' +
            (hk ? ' [' + (hk.kept.length ? 'h' + hk.kept.join(',h') : 'none kept') + ']' : '') +
            '<span class="pv-sw" style="background:#111"></span>' +
            (M ? 'parametric: mean+trend+1 annual term' : 'parametric: not built for this area') +
            '<span class="pv-sw" style="background:#c9971c"></span>now' +
            ' · bar length = pair separation' +
            (MON ? '' : ' · loading monthly series…');
    }
    map.on('moveend', function () { if (insCell) drawInspector(); });

    // ---- hash -----------------------------------------------------------
    function writeHash() {
        if (!window.LSHash || !map) return;
        var c = map.getCenter();
        var h = window.LSHash.encode({
            zoom: map.getZoom(), lat: c.lat, lon: c.lng,
            base: _basemap !== DEFAULT_BASEMAP ? _basemap : null,
            extras: {
                t: simT != null && !playing ? simT.toFixed(3) : null,
                dtlo: dtLo(), dthi: dtHi()
            }
        });
        if (location.hash !== h) history.replaceState(null, '', h);
    }

    load().catch(function (e) {
        elStats.textContent = 'bundle unavailable — build it with tools/build_pair_vectors.py';
        console.error(e);
    });
})();
