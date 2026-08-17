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

    // ---- controls -------------------------------------------------------
    var elDtMin = document.getElementById('pv-dtmin'),
        elDtMax = document.getElementById('pv-dtmax'),
        elDtVal = document.getElementById('pv-dtval'),
        elMode = document.getElementById('pv-mode'),
        elColor = document.getElementById('pv-color'),
        elTrails = document.getElementById('pv-trails'),
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
        if (elTrails.checked) {
            ctx.globalCompositeOperation = 'destination-out';
            ctx.fillStyle = 'rgba(0,0,0,0.10)';
            ctx.fillRect(0, 0, w, h);
            ctx.globalCompositeOperation = 'source-over';
        } else {
            ctx.clearRect(0, 0, w, h);
        }
        if (!D || simT == null) return;

        var lo = dtLo(), hi = dtHi();
        var mode = elMode.value, cmode = elColor.value;
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
                ctx.globalAlpha = 0.28;
                ctx.beginPath(); ctx.moveTo(q0.x, q0.y); ctx.lineTo(q1.x, q1.y); ctx.stroke();
            }
            if (drawSwarm) {
                ctx.globalAlpha = 0.85;
                ctx.fillStyle = col;
                ctx.fillRect(p.x - 1.2, p.y - 1.2, 2.4, 2.4);
            }
        }
        ctx.globalAlpha = 1;
        lastCount = n;
    }

    map.on('render', function () { if (D && map.isMoving()) draw(); });
    map.on('moveend', writeHash);

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
        if (D && elStats.dataset.base == null) elStats.dataset.base = elStats.textContent;
        if (D) {
            elStats.textContent = (elStats.dataset.base || '') +
                ' · showing ' + lastCount.toLocaleString() + ' now';
        }
    }
    requestAnimationFrame(frame);

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
