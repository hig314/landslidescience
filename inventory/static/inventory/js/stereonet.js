/* The slope-aspect stereonet: the analysis panel's two plots, on a circle.
 *
 * Aspect is cyclic, so a linear axis cuts the compass at north and draws 359°
 * and 1° at opposite ends. Here the plot is a domed hill seen from above (Hig,
 * 2026-11-10): aspect is the direction from the centre, slope the distance
 * out, so a south-facing 45° slope sits below the centre, halfway to a 90°
 * rim. The radius is linear in slope (a polar plot, not an equal-area net).
 *
 * The same two plots as analysis.js, from the same data:
 *   TERRAIN    the density of every land cell of the reviewed area, by aspect
 *              and slope (api/analysis/density/), km² per polar cell.
 *   LANDSLIDES each at its own values (ls_quantities.json), as points, a
 *              count per cell, or landslides per 1000 km² of that terrain.
 * Subsets (map view, sample), the map's filters, the fixed rate scale and the
 * brush's rings and paint are analysis.js's, through LSAnalysis.shared.
 *
 * ASPECT IS ROTATED TO TRUE NORTH. The cube's aspect is measured on the Alaska
 * Albers grid, whose north leans from true north by n * (lon + 154)°: +18° in
 * the western Aleutians, -21° at the southern tip of the panhandle. The server
 * rotates the terrain (density ?true_north=1) and rotateLs() below the
 * landslides. The map paint needs no rotation: its aspect is derived from
 * north-up Web Mercator tiles (ls_sample.js).
 *
 * THE CELLS ARE EQUAL-AREA ON THE HEMISPHERE OF SLOPE NORMALS (Hig,
 * 2026-11-11). A point here is the pole to a slope, so "how far apart are two
 * slopes" is the angle between their normals, and near-flat ground facing
 * north and facing south are neighbours. The grid is a cap on the vertical,
 * then K rings of 6, 12, 18 … cells out to 60° (1 + 3K(K+1) cells; the
 * zonal construction of Leopardi 2006). Ring radii are equal-area: with the
 * Lambert radius r = 2 sin(slope/2), ring k ends at r0·sqrt(1 + 3k(k+1)).
 * Every cell has the same solid angle, so gentle slopes get few aspect
 * divisions and steep ones many; consecutive rings never share an aspect
 * boundary (one has 6k cells, the next 6k+6), so the cells sit like bricks.
 * Each ring centres a cell on north. The 288 x 60 server histogram (1.25° x
 * 1°) is split 4 x 8 and each piece goes to the cell its centre falls in, so
 * cell edges are exact to 0.3° of aspect and 0.125° of slope. Sparse cells are drawn as they are:
 * the cure for noise is fewer, bigger cells (the "cells" select).
 *
 * The page is still linear in slope, so an outer cell looks up to ~20 %
 * larger than the cap at 60°; it is not more ground.
 *
 * ASPECT EFFECT (mode 'effect'): landslides observed in a cell / expected from
 * slope alone, where "expected" is the cell's terrain times the rate at its
 * slope, smoothed over slope (Gaussian, SLOPE_H = 2°) from the same terrain
 * and landslides. 1 = what slope predicts. The key is symmetric about 1 in
 * powers of two, wide enough for the data: the low end rounded down to 1/2^s,
 * the high end up to 2^s. A cell with terrain and no landslides is 0, in the
 * lowest class.
 *
 * URL: an=stereo; sn=<slope key>,e<K>,<rim°>,<points|count|prop|effect>;
 * sb=a0,a1,s0,s1 the brush (a0 > a1 crosses north); spt=0 paint off. The old
 * sn=<slope>,<sectors>,<ring°>,<rim°>,<mode> (2026-11-10) is still read, at
 * the default cells.
 *
 * Global `window.LSStereonet`; map.js calls init() after LSAnalysis.init().
 */
window.LSStereonet = (function () {
  'use strict';

  var BASE = 288, BASE_DEG = 1.25, ASPECT = 'aspect';
  var CONE = 0.8627, LON0 = -154;     // EPSG:3338: the cone constant, the central meridian (analysis.py has the same)
  var COLOR = { slow: '#d6604d', catastrophic: '#3f67b1', ring: '#111', brush: '#3f67b1' };
  var S_LABEL = { slope: '60 m grid', slope_local: 'local', slope_200: '200 m', slope_500: '500 m', slope_1k: '1 km', slope_2k: '2 km' };
  var FINE = { slope: 'slope60' };    // the slope axes with a 60 m value archive, so the brush can paint
  var WIND = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];

  var map = null, o = {}, el = {}, fp = null, sh = null;
  var KS = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12], SUBX = 4, SUBY = 8, SLOPE_H = 2, SMAX = 60;
  var EFFECT_BLUE = ['#d1e5f0', '#92c5de', '#4393c3', '#2166ac', '#053061'];   // RdBu, from 1 outwards
  var EFFECT_RED = ['#fddbc7', '#f4a582', '#d6604d', '#b2182b', '#67001f'];
  var S = 'slope', K = 6, rim = 90, lsMode = 'points', paintOn = true;
  var brush = null;                              // {a0, a1, s0, s1}; a0 > a1 crosses north
  var lsv = null, kidx = {}, outside = null;
  var dens = null, densCache = {}, densSeq = 0;
  var sub = null, subSeq = 0, subTimer = null;
  var P = null, pendingState = null, cubeError = null;

  // ---- init --------------------------------------------------------------------------
  function init(opts) {
    o = opts; map = opts.map; sh = LSAnalysis.shared;
    var panel = document.getElementById('sn-float'); if (!panel) return;
    ['slope', 'cells', 'rim', 'ls', 'sample', 'radius', 'limit', 'paint', 'count', 'clear', 'close', 'body', 'heat', 'svg', 'foot', 'note'].forEach(function (k) {
      el[k] = panel.querySelector('#sn-' + k);
    });
    el.tip = document.createElement('div'); el.tip.className = 'pf-tip'; el.body.appendChild(el.tip);
    fp = o.makePanel(panel, { handle: panel.querySelector('.an-header'), toggle: document.getElementById('sn-toggle'),
                              close: el.close, onResize: draw, onChange: onOpenClose });
    el.slope.addEventListener('change', function () { S = el.slope.value; brush = null; reload(); o.onChange(); });
    el.cells.addEventListener('change', function () { K = +el.cells.value; draw(); o.onChange(); });
    el.rim.addEventListener('change', function () { rim = +el.rim.value; draw(); o.onChange(); });
    el.ls.addEventListener('change', function () { lsMode = el.ls.value; draw(); o.onChange(); });
    el.sample.addEventListener('click', function (e) {
      e.preventDefault();
      if (LSSample.isArmed()) LSSample.disarm(); else LSSample.arm(radius());
    });
    el.radius.addEventListener('change', function () {
      var r = radius(); LSSample.setRadius(r);
      var c = LSSample.get(); if (c) LSSample.set(c.lon, c.lat, r);
    });
    if (el.limit) el.limit.addEventListener('change', function () { o.limitView.set(el.limit.checked); });
    el.paint.addEventListener('change', function () { paintOn = el.paint.checked; updatePaint(); o.onChange(); });
    el.clear.addEventListener('click', function (e) { e.preventDefault(); setBrush(null); });
    el.svg.addEventListener('pointermove', onHover);
    el.svg.addEventListener('pointerleave', function () { el.tip.style.display = 'none'; });
    el.svg.addEventListener('click', function (e) { var p = nearest(e); if (p && o.openLandslide) o.openLandslide(p.id, p.ft); });
    wireBrush();
    LSSample.on(onSample);
    LSSample.onArmChange(function (on) { el.sample.classList.toggle('active', on); });
    map.on('moveend', function () {
      if (subsetKind() !== 'view' || !fp.isOpen()) return;
      clearTimeout(subTimer); subTimer = setTimeout(loadSubset, 250);
    });
    fetch(o.apiBase + 'api/analysis/reviewed/').then(function (r) { return r.json(); })
      .then(function (j) { if (j.error) return; outside = {}; (j.outside || []).forEach(function (id) { outside[id] = true; }); draw(); })
      .catch(function () {});
    o.values().then(function (j) { if (!j) return; lsv = j; j.keys.forEach(function (k, i) { kidx[k] = i; }); draw(); }).catch(function () {});
    if (pendingState) { var s = pendingState; pendingState = null; setState(s); }
  }
  function panel() { return fp; }
  function radius() { return Math.max(0.5, Math.min(50, (parseFloat(el.radius.value) || 20) / 2)); }
  function reload() { sub = null; loadDensity(); loadSubset(); draw(); updatePaint(); }

  function onOpenClose() {
    if (!fp) return;
    if (fp.isOpen()) { syncLimit(); loadDensity(); loadSubset(); draw(); }
    else {
      var other = LSAnalysis.panel();
      if (!(other && other.isOpen())) { LSSample.disarm(); sh.highlight([]); sh.outline(null); }
    }
    updatePaint();
    o.onChange();
  }

  // ---- data -----------------------------------------------------------------------------
  function densUrl(extra) {
    return o.apiBase + 'api/analysis/density/?x=' + ASPECT + '&y=' + S + '&xbins=' + BASE + '&true_north=1' + (extra ? '&' + extra : '');
  }
  function loadDensity() {
    var key = S, my = ++densSeq;
    if (densCache[key]) { dens = densCache[key]; return; }
    dens = null;
    fetch(densUrl()).then(function (r) { return r.json(); }).then(function (j) {
      if (j.error) { cubeError = j.error; draw(); return; }
      densCache[key] = j;
      if (my === densSeq) { dens = j; draw(); }
    }).catch(function () {});
  }
  function limitOn() { return !!(o.limitView && o.limitView.get()); }
  function subsetKind() { return LSSample.get() ? 'sample' : limitOn() ? 'view' : null; }
  function syncLimit() {
    var on = limitOn();
    if (el.limit) el.limit.checked = on;
    el.sample.classList.toggle('an-dim', on);
  }
  function onLimitView() {
    if (!fp) return;
    syncLimit();
    if (!fp.isOpen()) return;
    if (limitOn()) LSSample.disarm();
    loadSubset(); draw(); o.onChange();
  }
  function loadSubset() {
    var k = subsetKind(), my = ++subSeq;
    if (sub && sub.subset && sub.subset.kind !== (k === 'sample' ? 'circle' : k)) sub = null;
    if (!k || !fp || !fp.isOpen()) { sub = null; if (fp && fp.isOpen()) sh.outline(null); return; }
    var arg;
    if (k === 'view') { var b = map.getBounds(); arg = 'view=' + [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map(function (v) { return v.toFixed(4); }).join(','); }
    else { var c = LSSample.get(); arg = 'circle=' + c.lon.toFixed(5) + ',' + c.lat.toFixed(5) + ',' + c.r; }
    fetch(densUrl(arg)).then(function (r) { return r.json(); }).then(function (j) {
      if (my !== subSeq) return;
      if (j.error) { cubeError = j.error; draw(); return; }
      j.idset = {}; j.subset.ids.forEach(function (id) { j.idset[id] = true; });
      sub = j; sh.outline(k === 'sample' ? j.subset.outline : null); draw();
    }).catch(function () {});
  }
  function onSample() {
    if (!fp || !fp.isOpen()) return;
    var c = LSSample.get();
    if (c && Math.abs(parseFloat(el.radius.value) - 2 * c.r) > 0.01) el.radius.value = 2 * c.r;
    loadSubset(); draw(); o.onChange();
  }
  function lsVal(id, key) {
    if (!lsv) return null;
    var row = lsv.values[String(id)], i = kidx[key];
    return (row && i != null) ? row[i] : null;
  }
  // Grid azimuth -> true azimuth at this longitude (the terrain's rotation is
  // the server's, the same formula).
  function rotateLs(aspect, lon) {
    var dl = ((lon - LON0 + 540) % 360) - 180;
    return (((aspect + CONE * dl) % 360) + 360) % 360;
  }

  // ---- equal-area cells -----------------------------------------------------------------
  var geoCache = {};
  function geo(k) {
    if (geoCache[k]) return geoCache[k];
    var n = 1 + 3 * k * (k + 1), r0 = 2 * Math.sin(rad(SMAX / 2)) / Math.sqrt(n), edge = [], i;
    for (i = 0; i <= k; i++) edge.push(2 * Math.asin(Math.min(1, r0 * Math.sqrt(1 + 3 * i * (i + 1)) / 2)) * 180 / Math.PI);
    edge[k] = SMAX;
    var g = { K: k, n: n, edge: edge, maps: {} };
    g.cellOf = function (az, sl) {
      sl = Math.min(sl, SMAX - 1e-9);
      var ring = 0; while (sl >= edge[ring]) ring++;
      if (ring === 0) return 0;
      var m = 6 * ring;
      return 1 + 3 * ring * (ring - 1) + Math.floor(((az + 180 / m) % 360) / (360 / m)) % m;
    };
    // id -> {ring, a0, a1, s0, s1, c}: c the centre azimuth
    g.span = function (id) {
      if (id === 0) return { ring: 0, a0: 0, a1: 360, s0: 0, s1: edge[0], c: null };
      var ring = 1; while (1 + 3 * (ring + 1) * ring <= id) ring++;
      var m = 6 * ring, j = id - (1 + 3 * ring * (ring - 1)), w = 360 / m, c = j * w;
      return { ring: ring, a0: (c - w / 2 + 360) % 360, a1: (c + w / 2) % 360, s0: edge[ring - 1], s1: edge[ring], c: c };
    };
    geoCache[k] = g; return g;
  }
  // The cell of each quarter-bin piece of the server histogram (nx x ny over
  // 360° x 0..SMAX°), cached per grid and histogram shape.
  function pieceMap(g, nx, ny) {
    var key = nx + 'x' + ny; if (g.maps[key]) return g.maps[key];
    var out = new Int32Array(nx * ny * SUBX * SUBY), dx = 360 / nx, dy = SMAX / ny, q = 0;
    for (var yi = 0; yi < ny; yi++) for (var xi = 0; xi < nx; xi++)
      for (var sy = 0; sy < SUBY; sy++) for (var sx = 0; sx < SUBX; sx++)
        out[q++] = g.cellOf((xi + (sx + 0.5) / SUBX) * dx, (yi + (sy + 0.5) / SUBY) * dy);
    g.maps[key] = out; return out;
  }
  // Histogram -> km² per cell, and (with rowW, a weight per slope row) the sum of km² x weight.
  function merge(g, src, nx, ny, rowW) {
    var pm = pieceMap(g, nx, ny), out = new Float64Array(g.n), wout = rowW ? new Float64Array(g.n) : null;
    var pp = SUBX * SUBY, f = 1 / pp, q = 0;
    for (var yi = 0; yi < ny; yi++) {
      var w = rowW ? rowW[yi] : 0;
      for (var xi = 0; xi < nx; xi++) {
        var v = src[yi * nx + xi] * f;
        if (!v) { q += pp; continue; }
        for (var k = 0; k < pp; k++, q++) { out[pm[q]] += v; if (wout) wout[pm[q]] += v * w; }
      }
    }
    return { a: out, e: wout };
  }
  // Landslides per km² at each slope row's centre, smoothed over slope: the
  // "expected from slope alone" of the aspect effect.
  function slopeRate(src, nx, ny, slopes) {
    var dy = SMAX / ny, rowT = new Float64Array(ny), lam = new Float64Array(ny), y, x, i;
    for (y = 0; y < ny; y++) for (x = 0; x < nx; x++) rowT[y] += src[y * nx + x];
    for (y = 0; y < ny; y++) {
      var c = (y + 0.5) * dy, num = 0, den = 0, t;
      for (i = 0; i < slopes.length; i++) { t = (slopes[i] - c) / SLOPE_H; if (t > -5 && t < 5) num += Math.exp(-0.5 * t * t); }
      for (i = 0; i < ny; i++) { t = ((i + 0.5) * dy - c) / SLOPE_H; if (t > -5 && t < 5) den += Math.exp(-0.5 * t * t) * rowT[i]; }
      lam[y] = den > 0 ? num / den : 0;
    }
    return lam;
  }
  // The aspect effect's classes: s per side of 1, in powers of two.
  function effectScale(vals, counts, exps) {
    var lo = Infinity, hi = 0;
    for (var i = 0; i < vals.length; i++) if (exps[i] > 0 && counts[i] > 0) { lo = Math.min(lo, vals[i]); hi = Math.max(hi, vals[i]); }
    var span = hi > 0 ? Math.max(hi, 1 / lo, 1.0001) : 2;
    var sd = Math.max(1, Math.min(5, Math.ceil(Math.log(span) / Math.LN2 - 1e-9)));
    var colors = EFFECT_BLUE.slice(0, sd).reverse().concat(EFFECT_RED.slice(0, sd));
    return { s: sd, colors: colors,
             bin: function (v) { if (!(v > 0)) return 0; var b = Math.floor(Math.log(v) / Math.LN2) + sd; return Math.max(0, Math.min(2 * sd - 1, b)); } };
  }
  function fracLabel(e) { return e === 0 ? '1' : e > 0 ? String(Math.pow(2, e)) : '1/' + Math.pow(2, -e); }
  function inArc(a, a0, a1) { return a0 <= a1 ? (a >= a0 && a <= a1) : (a >= a0 || a <= a1); }

  function dims() {
    var W = el.svg.clientWidth, H = el.svg.clientHeight;
    var top = 32, bot = 30, side = 24;
    var R = Math.floor(Math.min((W - side * 4) / 4, (H - top - bot) / 2));
    return { W: W, H: H, R: R, panes: [{ cx: Math.round(W / 4), cy: top + R }, { cx: Math.round(3 * W / 4), cy: top + R }] };
  }
  function rad(d) { return d * Math.PI / 180; }
  function toXY(p, R, az, s) { var r = R * Math.min(s, rim) / rim; return { x: p.cx + r * Math.sin(rad(az)), y: p.cy - r * Math.cos(rad(az)) }; }
  function wedge(ctx, p, R, a0, a1, s0, s1) {
    if (s0 >= rim) return;
    if (a1 <= a0) a1 += 360;
    var r0 = R * s0 / rim, r1 = R * Math.min(s1, rim) / rim;
    // canvas angle 0 is east and runs clockwise; aspect 0 is north and runs clockwise
    // Close the antialiasing seam with a constant 0.3 px of overlap: a fixed
    // angle would be a large share of a narrow sector.
    var pad = 0.3 / Math.max(r1, 1);
    var t0 = rad(a0 - 90) - pad, t1 = rad(a1 - 90) + pad;
    ctx.beginPath(); ctx.arc(p.cx, p.cy, r1 + 0.3, t0, t1); ctx.arc(p.cx, p.cy, Math.max(0, r0 - 0.3), t1, t0, true); ctx.closePath(); ctx.fill();
  }
  function wedgePath(p, R, a0, a1, s0, s1) {
    if (a0 === 0 && a1 === 360) {     // the cap: a disc (or a full ring)
      var ro = R * Math.min(s1, rim) / rim, ri = R * Math.min(s0, rim) / rim;
      var circ = function (r, sw) { return 'M' + (p.cx - r) + ' ' + p.cy + 'a' + r + ' ' + r + ' 0 1 ' + sw + ' ' + (2 * r) + ' 0a' + r + ' ' + r + ' 0 1 ' + sw + ' ' + (-2 * r) + ' 0Z'; };
      return circ(ro, 1) + (ri > 0 ? circ(ri, 0) : '');
    }
    var A = toXY(p, R, a0, s1), B = toXY(p, R, a1, s1), C = toXY(p, R, a1, s0), D = toXY(p, R, a0, s0);
    var span = ((a1 - a0) + 360) % 360 || 360, large = span > 180 ? 1 : 0;
    var r1 = R * Math.min(s1, rim) / rim, r0 = R * Math.min(s0, rim) / rim;
    return 'M' + A.x + ' ' + A.y + 'A' + r1 + ' ' + r1 + ' 0 ' + large + ' 1 ' + B.x + ' ' + B.y +
           'L' + C.x + ' ' + C.y + 'A' + r0 + ' ' + r0 + ' 0 ' + large + ' 0 ' + D.x + ' ' + D.y + 'Z';
  }

  // ---- draw -----------------------------------------------------------------------------
  function draw(settled) {
    if (!el.svg || !fp || !fp.isOpen()) return;
    var d = dims(); if (d.R <= 20) return;
    var R = d.R, g = geo(K), T = sh;
    var cv = el.heat, dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(d.W * dpr); cv.height = Math.round(d.H * dpr);
    var ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, d.W, d.H);

    var kind = subsetKind(), subOk = !kind || !!(sub && sub.idset);
    var pts = [], fd = o.features ? o.features() : null, lsg = new Float64Array(g.n);
    var nOut = 0, nFilt = 0, nFlat = 0, nMissing = 0, B = d.panes[1];
    if (lsv && fd && fd.features && subOk) {
      fd.features.forEach(function (ft) {
        var id = ft.properties.landslide_id != null ? ft.properties.landslide_id : ft.properties.id;
        if (kind ? !sub.idset[id] : (outside && outside[id])) { if (!kind) nOut++; return; }
        if (o.passes && !o.passes(ft)) { nFilt++; return; }
        var va = lsVal(id, ASPECT), vs = lsVal(id, S), c = ft.geometry && ft.geometry.coordinates;
        if (!lsv.values[String(id)] || vs == null) { nMissing++; return; }
        if (va == null || !c) { nFlat++; return; }
        var az = rotateLs(va, c[0]);
        lsg[g.cellOf(az, vs)]++;
        var xy = toXY(B, R, az, vs);
        var type = (ft.properties.landslide_type || '').toLowerCase().indexOf('cat') === 0 ? 'catastrophic' : 'slow';
        pts.push({ x: xy.x, y: xy.y, id: id, name: ft.properties.unique_name, ft: ft, az: az, s: vs, type: type, lon: c[0], lat: c[1] });
      });
    }
    var source = kind ? (sub && sub.xsize === BASE ? sub : null) : (dens && dens.xsize === BASE ? dens : null);
    var effect = lsMode === 'effect', lam = null, mg = null;
    if (source && effect) lam = slopeRate(source.grid, source.xsize, source.ysize, pts.map(function (p) { return Math.min(p.s, SMAX - 1e-9); }));
    if (source) mg = merge(g, source.grid, source.xsize, source.ysize, lam);
    var tg = mg ? mg.a : null, eg = mg ? mg.e : null;
    var grid = lsMode !== 'points', i, sp;

    // pane 0 the terrain; a faint copy under the landslides, as in the x-y panel
    var tMax = 0, tBins = null;
    if (tg) {
      for (i = 0; i < tg.length; i++) if (tg[i] > tMax) tMax = tg[i];
      tBins = T.logRangeBinner(1, Math.max(2, tMax), T.T_RAMP.length);
      for (i = 0; i < g.n; i++) {
        var tv = tg[i]; if (!tv) continue;
        sp = g.span(i);
        ctx.fillStyle = T.T_RAMP[Math.max(1, tBins.bin(tv)) - 1];
        wedge(ctx, d.panes[0], R, sp.a0, sp.a1, sp.s0, sp.s1);
        if (!effect) {
          ctx.globalAlpha = 0.28;
          wedge(ctx, d.panes[1], R, sp.a0, sp.a1, sp.s0, sp.s1);
          ctx.globalAlpha = 1;
        }
      }
    }
    // pane 1 the landslide grid
    var lBins = null, noTerrain = 0, eff = null;
    if (effect && eg) {
      var ev = new Float64Array(g.n);
      for (i = 0; i < g.n; i++) ev[i] = eg[i] > 0 ? lsg[i] / eg[i] : 0;
      eff = effectScale(ev, lsg, eg);
      for (i = 0; i < g.n; i++) {
        if (!(eg[i] > 0) && !lsg[i]) continue;
        if (!(eg[i] > 0)) { noTerrain++; ctx.fillStyle = T.LS_CATCH; }
        else ctx.fillStyle = eff.colors[eff.bin(ev[i])];
        sp = g.span(i);
        wedge(ctx, B, R, sp.a0, sp.a1, sp.s0, sp.s1);
      }
    } else if (grid && !effect) {
      var vals = new Float64Array(lsg.length), vmax = 0;
      for (i = 0; i < lsg.length; i++) {
        if (!lsg[i]) continue;
        var tc = tg ? tg[i] : 0, v = lsMode === 'prop' ? (tc > 0 ? lsg[i] / tc * 1000 : -1) : lsg[i];
        vals[i] = v; if (v > vmax) vmax = v;
      }
      lBins = lsMode === 'prop' ? T.logRangeBinner(T.RATE_LO, T.RATE_HI, T.LS_RAMP.length) : T.logCatchBinner(vmax, T.LS_RAMP.length);
      for (i = 0; i < g.n; i++) {
        if (!vals[i]) continue;
        var b = vals[i] < 0 ? 0 : lBins.bin(vals[i]);
        if (vals[i] < 0) noTerrain++;
        ctx.fillStyle = b === 0 ? T.LS_CATCH : T.LS_RAMP[b - 1];
        sp = g.span(i);
        wedge(ctx, B, R, sp.a0, sp.a1, sp.s0, sp.s1);
      }
    }

    // svg: the net, labels, points, brush
    var svg = el.svg; while (svg.firstChild) svg.removeChild(svg.firstChild);
    var where = kind === 'sample' ? ' · in the sample' : kind ? ' · in the map view' : '';
    var titles = ['Terrain · km² per cell' + where,
                  'Landslides · ' + (lsMode === 'points' ? 'points' : lsMode === 'count' ? 'count per cell' : lsMode === 'prop' ? 'per 1000 km²' : 'observed / expected from slope') + where];
    d.panes.forEach(function (p, pi) { cellLines(svg, p, R, g, pi === 1 && !grid); net(svg, p, R); txt(svg, titles[pi], p.cx, 11, 'middle', '#555'); });
    txt(svg, 'centre = flat, rim = ' + rim + '° · ' + g.n + ' equal-area cells to 60° · aspect from true north · click a cell for a brush, drag for any sector', d.W / 2, d.H - 4, 'middle', '#bbb');

    var hl = [], drawn = [], sampled = kind === 'sample';
    pts.forEach(function (p) {
      var inB = !!brush && inArc(p.az, brush.a0, brush.a1) && p.s >= brush.s0 && p.s <= brush.s1;
      if (inB || sampled) hl.push(p);
      if (!grid) {
        svg.appendChild(svgEl('circle', { cx: p.x, cy: p.y, r: (inB || sampled) ? 4 : 3, fill: COLOR[p.type], 'fill-opacity': 0.8,
                                          stroke: (inB || sampled) ? COLOR.ring : '#fff', 'stroke-width': (inB || sampled) ? 1.5 : 0.6 }));
        drawn.push(p);
      } else if (inB) {
        svg.appendChild(svgEl('circle', { cx: p.x, cy: p.y, r: 3.5, fill: 'none', stroke: COLOR.ring, 'stroke-width': 1.2 }));
      }
    });
    if (brush) d.panes.forEach(function (p) {
      svg.appendChild(svgEl('path', { d: wedgePath(p, R, brush.a0, brush.a1, brush.s0, brush.s1), fill: 'rgba(60,103,177,0.12)', stroke: COLOR.brush,
                                      'stroke-width': 1, 'stroke-dasharray': '3 2', 'pointer-events': 'none' }));
    });
    svg.appendChild(svgEl('path', { id: 'sn-drag', fill: 'rgba(0,0,0,0.06)', stroke: '#666', 'stroke-width': 1, display: 'none', 'pointer-events': 'none' }));
    P = { d: d, pts: pts, drawn: drawn, tg: tg, eg: eg, lsg: lsg, g: g };
    sh.highlight(hl);
    el.clear.classList.toggle('disabled', !brush);

    var msg = (kind && !source) ? 'reading…' : pts.length ? pts.length + ' landslides' : (lsv ? (kind ? 'no landslides' : 'no values') : 'loading…');
    if (hl.length) msg += ' · ' + hl.length + ' selected';
    var star = [];
    if (nMissing) star.push(nMissing + ' with no sampled values');
    if (nFlat) star.push(nFlat + ' on flat ground (no aspect)');
    if (nOut) star.push(nOut + ' outside the reviewed area');
    if (nFilt) star.push(nFilt + ' hidden by the map’s filters');
    if (star.length && pts.length) msg = msg.replace(/^(\d+ landslides)/, '$1*');
    el.count.textContent = msg;
    var lines = [];
    if (star.length) lines.push('<div>' + (pts.length ? '* not plotted: ' : '') + esc(star.join(' · ')) + '</div>');
    if (cubeError) lines.push('<div class="an-err">' + esc(cubeError) + '</div>');
    if (brush && !FINE[S]) lines.push('<div>The brush paints the map only with the 60 m slope grid, which has a value archive.</div>');
    el.note.innerHTML = lines.join(''); el.note.style.display = lines.length ? '' : 'none';
    foot(tBins, lBins, noTerrain, kind, source, pts.length, eff);
    // The legend and notes just written can change the plot's height, and the
    // panel's ResizeObserver does not see that (the panel itself did not
    // resize). The canvas would then be stretched against the net: draw once
    // more at the real size. The second pass writes the same legend, so it stops.
    if (settled !== true && (el.svg.clientWidth !== d.W || el.svg.clientHeight !== d.H)) draw(true);
  }
  // Faint cell edges, so the grid reads where cells are empty; skipped under points.
  function cellLines(svg, p, R, g, skip) {
    if (skip) return;
    var dd = '', k, j, e;
    for (k = 0; k < g.K; k++) {
      e = g.edge[k]; if (e >= rim) break;
      var r = R * e / rim; dd += 'M' + (p.cx - r) + ' ' + p.cy + 'a' + r + ' ' + r + ' 0 1 0 ' + (2 * r) + ' 0a' + r + ' ' + r + ' 0 1 0 ' + (-2 * r) + ' 0';
    }
    for (k = 1; k <= g.K; k++) {
      var m = 6 * k, s0 = g.edge[k - 1], s1 = g.edge[k];
      if (s0 >= rim) break;
      for (j = 0; j < m; j++) { var a = j * 360 / m + 180 / m, A = toXY(p, R, a, s0), Bq = toXY(p, R, a, s1); dd += 'M' + A.x.toFixed(1) + ' ' + A.y.toFixed(1) + 'L' + Bq.x.toFixed(1) + ' ' + Bq.y.toFixed(1); }
    }
    svg.appendChild(svgEl('path', { d: dd, fill: 'none', stroke: 'rgba(255,255,255,0.7)', 'stroke-width': 0.6, 'pointer-events': 'none' }));
  }
  function net(svg, p, R) {
    var line = '#000', step = rim >= 90 ? 30 : 15, s, a;
    for (s = step; s <= rim; s += step) {
      var r = R * s / rim;
      svg.appendChild(svgEl('circle', { cx: p.cx, cy: p.cy, r: r, fill: 'none', stroke: line, 'stroke-width': s === rim ? 0.6 : 0.35, 'pointer-events': 'none' }));
      var lab = txt(svg, s + '°', p.cx + 2, p.cy - r + 9, 'start', '#000');
      lab.setAttribute('paint-order', 'stroke'); lab.setAttribute('stroke', '#fff'); lab.setAttribute('stroke-width', 2.5);
    }
    for (a = 0; a < 180; a += 45) {
      var q = toXY(p, R, a, rim), q2 = toXY(p, R, a + 180, rim);
      svg.appendChild(svgEl('line', { x1: q.x, y1: q.y, x2: q2.x, y2: q2.y, stroke: line, 'stroke-width': 0.35, 'pointer-events': 'none' }));
    }
    [['N', 0], ['E', 90], ['S', 180], ['W', 270]].forEach(function (t) {
      var at = { x: p.cx + (R + 9) * Math.sin(rad(t[1])), y: p.cy - (R + 9) * Math.cos(rad(t[1])) };
      var c = txt(svg, t[0], at.x, at.y + 3, 'middle', '#000'); c.setAttribute('font-weight', '600');
    });
  }
  function foot(tBins, lBins, noTerrain, kind, source, nLs, eff) {
    var T = sh, html = '', fmt = T.fmtNum, i;
    if (tBins) {
      var tbar = '<div class="leg-bar">' + T.T_RAMP.map(function (c, i) { return '<span style="background:' + c + '" title="' + fmt(tBins.edges[i + 1]) + ' – ' + fmt(tBins.edges[i + 2]) + ' km²"></span>'; }).join('') + '</div>';
      var tt = []; for (i = 1; i < tBins.edges.length; i++) tt.push({ at: (i - 1) / T.T_RAMP.length, label: fmt(tBins.edges[i]) });
      html += '<div class="leg-row"><span class="leg-title" title="Scaled to this plot’s fullest cell.">Terrain km²/cell</span>' + T.legendBar(tbar, tt) + '</div>';
    }
    if (lBins) {
      var cells = T.LS_RAMP.length + 1;
      var bar = '<div class="leg-bar"><span style="background:' + T.LS_CATCH + '" title="above zero, below ' + fmt(lBins.edges[1]) + (noTerrain ? '; or landslides in a cell with no sampled terrain (' + noTerrain + ')' : '') + '"></span>' +
                T.LS_RAMP.map(function (c, i) { return '<span style="background:' + c + '" title="' + fmt(lBins.edges[i + 1]) + ' – ' + fmt(lBins.edges[i + 2]) + '"></span>'; }).join('') + '</div>';
      var lt = [{ at: 0, label: '0' }]; for (i = 1; i < lBins.edges.length; i++) lt.push({ at: i / cells, label: fmt(lBins.edges[i]) });
      html += '<div class="leg-row"><span class="leg-title" title="' + (lsMode === 'prop' ? 'Fixed scale, the same as the x-y panel’s.' : 'Scaled to this plot’s fullest cell.') + '">' + (lsMode === 'prop' ? 'Slides/1000 km² (fixed)' : 'Slides/cell') + '</span>' + T.legendBar(bar, lt) + '</div>';
    }
    if (eff) {
      var ebar = '<div class="leg-bar">' + eff.colors.map(function (c, i) {
        return '<span style="background:' + c + '" title="' + fracLabel(i - eff.s) + ' – ' + fracLabel(i - eff.s + 1) + (i === 0 ? ' (and cells with none observed)' : '') + '"></span>';
      }).join('') + '</div>';
      var et = []; for (i = 0; i <= 2 * eff.s; i++) et.push({ at: i / (2 * eff.s), label: fracLabel(i - eff.s) });
      html += '<div class="leg-row"><span class="leg-title" title="Landslides in the cell ÷ the number its terrain would hold at the rate for its slope (smoothed over ' + SLOPE_H + '° of slope). 1 = what slope alone predicts. The scale widens in powers of two to fit the data; a cell with terrain and no landslides is 0, in the lowest class.' +
              (noTerrain ? ' Grey: landslides in a cell with no sampled terrain (' + noTerrain + ').' : '') + '">Observed / expected</span>' + T.legendBar(ebar, et) + '</div>';
    }
    if (brush) {
      html += '<div class="an-sample">Box: aspect ' + deg(brush.a0) + '–' + deg(brush.a1) + '° · slope ' + Math.round(brush.s0) + '–' + Math.round(brush.s1) + '°' +
              (paintOn && FINE[S] ? ' · painted on the map from zoom ' + T.PAINT_MINZ : '') + '</div>';
    }
    if (kind) {
      var c = LSSample.get(), s = kind === 'sample' ? 'Sample ⌀ ' + (2 * c.r) + ' km at ' + c.lat.toFixed(3) + ' N ' + (-c.lon).toFixed(3) + ' W' : 'Map view';
      if (source && source.subset) {
        var st = source.subset.stats.y;
        s += ' · ' + source.subset.cells.toLocaleString() + ' km² of land (1 km cells' + (kind === 'sample' ? ', outlined on the map' : '') + ')' +
             (st ? ' · slope median ' + Math.round(st.p50) + '° (' + Math.round(st.p10) + '–' + Math.round(st.p90) + '°)' : '') +
             ' · ' + nLs + ' landslide' + (nLs === 1 ? '' : 's');
      } else s += ' · reading…';
      html += '<div class="an-sample">' + s + '</div>';
    }
    el.foot.innerHTML = html; el.foot.style.display = html ? '' : 'none';
  }

  // ---- brush, hover --------------------------------------------------------------------
  function local(e) { var r = el.svg.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; }
  // A panel-local point -> {pane, az, s} inside one of the nets, or null.
  function polar(l) {
    if (!P) return null;
    for (var i = 0; i < 2; i++) {
      var p = P.d.panes[i], dx = l.x - p.cx, dy = l.y - p.cy, r = Math.hypot(dx, dy);
      if (r <= P.d.R) return { pane: i, az: (Math.atan2(dx, -dy) * 180 / Math.PI + 360) % 360, s: r / P.d.R * rim };
    }
    return null;
  }
  // Two azimuths -> the shorter arc between them, in clockwise order.
  function arc(a, b) { return ((b - a + 540) % 360) - 180 >= 0 ? [a, b] : [b, a]; }
  function wireBrush() {
    var start = null;
    LSTools.drag(el.svg, {
      onStart: function (e) { start = polar(local(e)); if (!start) return false; start.l = local(e); },
      onMove: function (e) {
        var dr = el.svg.querySelector('#sn-drag'), c = polar(local(e)); if (!dr || !c || c.pane !== start.pane) return;
        var ar = arc(start.az, c.az);
        dr.setAttribute('d', wedgePath(P.d.panes[start.pane], P.d.R, ar[0], ar[1], Math.min(start.s, c.s), Math.max(start.s, c.s)));
        dr.setAttribute('display', 'block');
      },
      onEnd: function (e, info) {
        var dr = el.svg.querySelector('#sn-drag'); if (dr) dr.setAttribute('display', 'none');
        if (info.cancelled || !e || !start) return;
        var l = local(e);
        if (Math.abs(l.x - start.l.x) < 4 && Math.abs(l.y - start.l.y) < 4) {
          if (nearest(e)) return;                          // a click on a point opens it (the click handler)
          if (start.s >= 60) return;                       // the cube's slope axis ends at 60°
          var sp = P.g.span(P.g.cellOf(start.az, start.s));
          setBrush({ a0: +sp.a0.toFixed(2), a1: +sp.a1.toFixed(2), s0: +sp.s0.toFixed(2), s1: +sp.s1.toFixed(2) });
          return;
        }
        var c = polar(l); if (!c || c.pane !== start.pane) return;
        var ar = arc(start.az, c.az);
        setBrush({ a0: +ar[0].toFixed(1), a1: +ar[1].toFixed(1), s0: +Math.min(start.s, c.s).toFixed(1), s1: +Math.max(start.s, c.s).toFixed(1) });
      }
    });
  }
  function setBrush(b) { brush = b; draw(); updatePaint(); o.onChange(); }
  function updatePaint() {
    var on = fp && fp.isOpen() && paintOn && brush && FINE[S];
    sh.setExternalPaint(on ? { fx: 'aspect60', fy: FINE[S], box: { x0: brush.a0, x1: brush.a1, y0: brush.s0, y1: brush.s1 } } : null);
  }
  function nearest(e) {
    if (!P) return null;
    var l = local(e), best = null, bd = 64;
    P.drawn.forEach(function (p) { var dd = (p.x - l.x) * (p.x - l.x) + (p.y - l.y) * (p.y - l.y); if (dd < bd) { bd = dd; best = p; } });
    return best;
  }
  function deg(v) { return String(+v.toFixed(2)); }     // 178.75, but 180
  function windName(a) { return WIND[Math.round(a / 22.5) % 16]; }
  function onHover(e) {
    if (!P) return;
    var l = local(e), html = null, c = polar(l), p = c && c.pane === 1 ? nearest(e) : null;
    if (p) html = '<b>' + esc(p.name || ('#' + p.id)) + '</b><br>' + windName(p.az) + ' (' + Math.round(p.az) + '°) · slope ' + Math.round(p.s) + '°';
    else if (c && c.s < SMAX) {
      var i = P.g.cellOf(c.az, c.s), sp = P.g.span(i);
      var tv = P.tg ? P.tg[i] || 0 : 0, lv = P.lsg[i] || 0, ev = P.eg ? P.eg[i] || 0 : 0;
      html = (sp.c == null ? 'Any aspect' : windName(sp.c) + ' (' + deg(sp.a0) + '–' + deg(sp.a1) + '°)') + ' · slope ' + sp.s0.toFixed(1) + '–' + sp.s1.toFixed(1) + '°<br>' +
             sh.fmtNum(tv) + ' km² · ' + lv + ' slide' + (lv === 1 ? '' : 's') + (lv && tv ? ' (' + sh.fmtNum(lv / tv * 1000) + '/1000 km²)' : lv ? ' (no sampled terrain)' : '') +
             (P.eg ? '<br>expected from slope ' + (ev >= 10 ? ev.toFixed(0) : ev.toFixed(1)) + (ev > 0 ? ' · observed / expected ' + (lv / ev).toFixed(2) : '') : '');
    }
    if (!html) { el.tip.style.display = 'none'; return; }
    el.tip.innerHTML = html; el.tip.style.display = 'block';
    var br = el.body.getBoundingClientRect();
    el.tip.style.left = Math.min(br.width - 200, e.clientX - br.left + 12) + 'px';
    el.tip.style.top = (e.clientY - br.top + 12) + 'px';
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function svgEl(n, a) { var e = document.createElementNS('http://www.w3.org/2000/svg', n); for (var k in a) e.setAttribute(k, a[k]); return e; }
  function txt(svg, s, x, y, anchor, fill) {
    var t = svgEl('text', { x: x, y: y, 'text-anchor': anchor || 'start', 'font-size': 9, fill: fill || '#888', 'pointer-events': 'none' });
    t.textContent = s; svg.appendChild(t); return t;
  }

  // ---- URL state (map.js _VIEW_PARAMS row 'sn') -----------------------------------------
  function state() { return { S: S, K: K, rim: rim, ls: lsMode, brush: brush, paint: paintOn }; }
  function setState(s) {
    if (!el.svg) { pendingState = s; return; }
    if (S_LABEL[s.S]) { S = s.S; el.slope.value = S; }
    if (KS.indexOf(s.K) >= 0) { K = s.K; el.cells.value = K; }
    if (s.rim === 60 || s.rim === 90) { rim = s.rim; el.rim.value = rim; }
    if (['points', 'count', 'prop', 'effect'].indexOf(s.ls) >= 0) { lsMode = s.ls; el.ls.value = lsMode; }
    if (s.paint !== undefined) { paintOn = s.paint !== false; el.paint.checked = paintOn; }
    if (s.brush !== undefined) brush = s.brush;
    if (fp && fp.isOpen()) reload();
  }

  return { init: init, panel: panel, redraw: draw, onFilter: draw, onLimitView: onLimitView, state: state, setState: setState };
})();
