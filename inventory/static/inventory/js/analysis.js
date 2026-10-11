/* The analysis panel: two overlays as axes, the map as the sampler.
 *
 * Pick two quantities (any two overlays that carry `values`, map.js
 * OVERLAYS) and the panel draws two plots on the same axes, side by side:
 *
 *   TERRAIN    the joint density of the pair over every land cell of a 1 km
 *              EPSG:3338 lattice, point-sampled from the native product
 *              (api/analysis/density/, the quantity cube in
 *              inventory/analysis.py), as a log backdrop -- with the sample's
 *              cells on it as the orange blotch.
 *   LANDSLIDES every landslide at its own values (ls_quantities.json,
 *              tools/analysis/sample_quantities.py) as points, or per cell
 *              as a count or as landslides per 1000 km² of terrain with those
 *              values (the old susceptibility scatter's two grid modes).
 *
 * The GRID slider coarsens both plots together in factor-of-2 steps: level k
 * merges 2^k x 2^k of the cube's bins (a plain sum of four cells per step,
 * so it is instant), and the landslide grid and the per-1000-km² rate are
 * built on the same merged cells -- the two plots are always on one scale.
 * The rate's colour scale is fixed (RATE_LO..RATE_HI), so coarsening shows
 * rates falling as the landslides are spread over more terrain.
 *
 * THE AXES ARE THE WIPER PANES (Hig, 2026-10-09). Open the panel over a
 * wiper showing two different overlays and they are x (left) and y
 * (right); pick axes in the panel and the wiper is created or re-pointed
 * so the map always shows what the plot is crossing. map.js owns the
 * wiper; this module only reads and asks (opts.wiper.get / set), and
 * map.js calls onOverlays() whenever overlay state changes. While the panel
 * is open map.js also shows a left-pane control panel beside the wiper's
 * right-pane one, so both axes have their basemap and overlay controls on
 * the map, and it holds the panes to EXACTLY ONE AXIS-CAPABLE OVERLAY
 * EACH: non-axis overlays go off when the panel opens, extras are pruned
 * to the first, and an overlay turned on replaces the pane's previous one
 * (Hig, 2026-10-10).
 *
 * SAMPLE: the ◎ button is a tool. While it is on, a ghost of the circle
 * follows the cursor over the map at the chosen radius; every click sets
 * the sample there (LSSample), and the tool stays on so the next click
 * moves it; turning the tool off (or closing the panel) removes the sample
 * and its highlights. The circle's cells light up on the terrain plot -- the 60 m
 * value archives where both axes have them, else the cube's lattice
 * cells -- with a median / range summary in the footer, and the landslides
 * inside the circle are drawn on the landslide plot as points whatever the
 * landslide layer mode (over the grid, with hover and click), and ringed on
 * the map. With permafrost products on both axes a "Permafrost detail"
 * button opens the four-plot patch panel (permafrost.js).
 *
 * BRUSH: drag a box on either plot and the landslides inside it are ringed
 * on the map (client side, from the values file). With lw and n10 on the
 * axes the box also drives those two filter sliders, which exist for
 * exactly this, and the sliders move the box back.
 *
 * URL: ax=<x>|<y>, bx=x0,x1,y0,y1 (the brush), ls=count|prop (the
 * landslide layer when not points), gr=<level> (the grid slider when not
 * 0), an=analysis; the sample is LSSample's smp=. The old an=scatter and
 * an=pf open this panel on lw|n10 and pzi|prob (map.js).
 *
 * Global `window.LSAnalysis`; map.js calls init() once the map exists.
 */
window.LSAnalysis = (function () {
  'use strict';

  var COLOR = { slow: '#d6604d', catastrophic: '#3f67b1', patch: 'rgba(255,143,0,0.45)', patchDot: 'rgba(255,143,0,0.7)',
                ring: '#111', brush: '#3f67b1' };
  // Landslide grid = YlOrRd with an off-ramp CATCH colour for "above zero,
  // below the bottom of the scale" (the susceptibility scatter's scheme).
  var LS_RAMP = ['#ffeda0', '#fed976', '#feb24c', '#fd8d3c', '#fc4e2a', '#e31a1c', '#b10026'];
  var LS_CATCH = '#bcbddc', LOG_DECADES = 3;
  // The rate scale is ABSOLUTE and the same at every grid level: a scale
  // re-normalised to each level's maximum made coarsening look like rates
  // rising, when the maximum was falling (Hig, 2026-10-10 — slope x
  // elevation: 1000 -> 91 -> 29 -> 20 -> 6 per 1000 km² from 1x to 16x).
  // 0.1 is well under the statewide mean (0.63); 1000 is one landslide per
  // km², the most a single lattice cell with one landslide can read.
  var RATE_LO = 0.1, RATE_HI = 1000;
  var PF_KEYS = ['maat', 'pzi', 'magt', 'prob', 'maat60', 'pzi60', 'magt60', 'prob60'];
  var SLIDER_KEYS = ['lw', 'n10'];
  var DEFAULT = ['lw', 'n10'];
  var HL = 'an-highlight', MIN_BINS = 4;

  var map = null, o = {}, el = {}, fp = null;
  var quantities = null, cubeError = null;      // the server registry
  var axes = [], byKey = {}, byOv = {};         // the overlays that can be axes
  var lsv = null, kidx = {};                    // ls_quantities.json
  var X = null, Y = null, lsMode = 'points', level = 0;
  var densCache = {}, dens = null, densSeq = 0;
  var brush = null;                             // {x0, x1, y0, y1} in value space
  var blotch = null, blotchSeq = 0;             // the sample on the current axes
  var P = null;                                 // the drawn plot: {d, qx, qy, panes:[{sx,sy,...}], pts, tg, lg, f}
  var syncing = false, pendingState = null, _hl = [];

  // ---- init --------------------------------------------------------------------------
  function init(opts) {
    o = opts; map = opts.map;
    var panel = document.getElementById('an-float'); if (!panel) return;
    el.panel = panel;
    ['x', 'y', 'ls', 'grid', 'gridv', 'sample', 'radius', 'count', 'pf', 'clear', 'close', 'body', 'heat', 'svg', 'foot', 'note'].forEach(function (k) {
      el[k] = panel.querySelector('#an-' + k);
    });
    el.tip = document.createElement('div'); el.tip.className = 'pf-tip'; el.body.appendChild(el.tip);
    axes = (o.axes ? o.axes() : []).filter(function (a) { return a && a.key; });
    axes.forEach(function (a) { byKey[a.key] = a; byOv[a.ov] = a; });
    fillPickers();
    fp = o.makePanel(panel, { handle: panel.querySelector('.an-header'), toggle: document.getElementById('an-toggle'),
                              close: el.close, onResize: draw, onChange: onOpenClose });
    el.x.addEventListener('change', function () { pick('x'); });
    el.y.addEventListener('change', function () { pick('y'); });
    el.ls.addEventListener('change', function () { lsMode = el.ls.value; draw(); o.onChange(); });
    el.grid.addEventListener('input', function () { setLevel(+el.grid.value, true); });
    el.sample.addEventListener('click', function (e) {
      e.preventDefault();
      if (LSSample.isArmed()) LSSample.disarm(); else LSSample.arm(radius());
    });
    // A new radius re-reads an existing sample in place and sizes the ghost.
    el.radius.addEventListener('change', function () {
      var r = radius(); LSSample.setRadius(r);
      var c = LSSample.get(); if (c) LSSample.set(c.lon, c.lat, r);
    });
    el.clear.addEventListener('click', function (e) { e.preventDefault(); clearBrush(); });
    el.pf.addEventListener('click', function (e) { e.preventDefault(); if (o.onPermafrost) o.onPermafrost(LSSample.get()); });
    el.svg.addEventListener('pointermove', onHover);
    el.svg.addEventListener('pointerleave', function () { el.tip.style.display = 'none'; });
    el.svg.addEventListener('click', onClick);
    wireBrush();
    // Presets in the Analysis tab: data-an-preset="x|y".
    Array.prototype.forEach.call(document.querySelectorAll('[data-an-preset]'), function (b) {
      b.addEventListener('click', function (e) {
        e.preventDefault();
        var p = b.getAttribute('data-an-preset').split('|');
        openWith(p[0], p[1]);
      });
    });
    LSSample.on(onSample);
    LSSample.init({ map: map, apiBase: o.apiBase, onArm: function (on) { el.sample.classList.toggle('active', on); } });
    map.on('style.load', ensureLayers);
    if (map.isStyleLoaded()) ensureLayers();

    fetch(o.apiBase + 'api/analysis/quantities/').then(function (r) { return r.json(); })
      .then(function (j) { quantities = j.quantities || {}; if (j.error) cubeError = j.error; fillPickers(); loadDensity(); syncGrid(); draw(); })
      .catch(function () { cubeError = 'quantity registry unavailable'; draw(); });
    // ls_quantities.json, through map.js's own fetch of it (one request).
    o.values().then(function (j) {
      if (!j) return;
      lsv = j; j.keys.forEach(function (k, i) { kidx[k] = i; }); draw();
    }).catch(function () {});
    if (pendingState) { var s = pendingState; pendingState = null; setState(s); }
  }
  function panel() { return fp; }
  function q(k) { return quantities && quantities[k]; }
  function available(k) { return !quantities || !!quantities[k]; }   // before the registry arrives, assume yes
  // The header shows the circle's DIAMETER ("a 20 km sample" is 20 km across); LSSample works in radius.
  function radius() { return Math.max(0.5, Math.min(50, (parseFloat(el.radius.value) || 20) / 2)); }

  // ---- pickers ------------------------------------------------------------------------
  function fillPickers() {
    ['x', 'y'].forEach(function (s) {
      var sel = el[s]; if (!sel) return;
      var cur = s === 'x' ? X : Y;
      sel.innerHTML = '';
      var groups = {}, order = [];
      axes.forEach(function (a) {
        if (quantities && !quantities[a.key]) return;
        if (!groups[a.cat]) { groups[a.cat] = []; order.push(a.cat); }
        groups[a.cat].push(a);
      });
      order.forEach(function (c) {
        var g = document.createElement('optgroup'); g.label = c;
        groups[c].forEach(function (a) {
          var op = document.createElement('option'); op.value = a.key; op.textContent = a.short || a.label;
          g.appendChild(op);
        });
        sel.appendChild(g);
      });
      if (cur) sel.value = cur;
    });
  }
  function pick(which) {
    var x = el.x.value, y = el.y.value;
    if (x === y) {   // crossing a quantity with itself only draws its diagonal
      var alt = axes.filter(function (a) { return a.key !== x && available(a.key); })[0];
      if (!alt) return;
      if (which === 'x') { y = alt.key; el.y.value = y; } else { x = alt.key; el.x.value = x; }
    }
    setAxes(x, y, { push: true });
  }
  function setAxes(x, y, how) {
    how = how || {};
    if (x === X && y === Y) { if (how.push) pushWiper(); return; }
    X = x; Y = y;
    if (el.x) { el.x.value = X; el.y.value = Y; }
    brush = null; blotch = null;
    loadDensity(); refreshBlotch(); syncGrid(); draw(); syncSlidersToBrush();
    if (how.push) pushWiper();
    o.onChange();
    window.LSTrack && LSTrack.event('explore_chart', { chart: 'analysis', x: X, y: Y });
  }
  function openWith(x, y) {
    if (x && y && x !== y) setAxes(x, y, { push: false });
    if (fp && !fp.isOpen()) fp.open(); else { pushWiper(); draw(); }
  }

  // ---- the grid slider --------------------------------------------------------------------
  // Level k merges 2^k x 2^k bins. The top of the slider is where the
  // shorter axis would drop below MIN_BINS cells.
  function maxLevel() {
    if (!X || !Y) return 0;
    var n = Math.min(axisOf(X).bins, axisOf(Y).bins), k = 0;
    while (Math.ceil(n / (2 << k)) >= MIN_BINS) k++;
    return k;
  }
  function syncGrid() {
    if (!el.grid) return;
    var mx = maxLevel();
    el.grid.max = mx; el.grid.disabled = mx === 0;
    if (level > mx) level = mx;
    el.grid.value = level;
    if (el.gridv) el.gridv.textContent = (1 << level) + '×';
  }
  function setLevel(k, user) {
    k = Math.max(0, Math.min(maxLevel(), k | 0));
    if (k === level && !user) return;
    level = k; syncGrid(); draw();
    if (user) o.onChange();
  }
  function coarsen(src, nx, ny, k) {
    if (!k) return { g: src, nx: nx, ny: ny };
    var f = 1 << k, cx = Math.ceil(nx / f), cy = Math.ceil(ny / f), out = new Float64Array(cx * cy);
    for (var yi = 0; yi < ny; yi++) for (var xi = 0; xi < nx; xi++) out[(yi >> k) * cx + (xi >> k)] += src[yi * nx + xi];
    return { g: out, nx: cx, ny: cy };
  }

  // ---- the wiper binding --------------------------------------------------------------
  // The panes, as axes: the first axis-capable overlay shown on each side.
  function adoptWiper() {
    if (!o.wiper) return false;
    var w = o.wiper.get(); if (!w || !w.on) return false;
    var xs = w.left.filter(function (id) { return byOv[id] && available(byOv[id].key); });
    var ys = w.right.filter(function (id) { return byOv[id] && available(byOv[id].key); });
    if (!xs.length || !ys.length) return false;
    var xo = xs[0], yo = ys[0];
    if (xo === yo) { yo = ys.filter(function (id) { return id !== xo; })[0] || xs.filter(function (id) { return id !== yo; })[0]; if (!yo) return false; if (xs.indexOf(yo) >= 0 && ys.indexOf(xo) >= 0 && ys[0] === xo) { xo = yo; yo = xs[0]; } }
    setAxes(byOv[xo].key, byOv[yo].key, { push: false });
    return true;
  }
  function pushWiper() {
    if (!o.wiper || !X || !Y || syncing) return;
    syncing = true;
    try { o.wiper.set(byKey[X] ? byKey[X].ov : null, byKey[Y] ? byKey[Y].ov : null); } finally { syncing = false; }
  }
  // map.js: overlay or wiper state changed. While the panel is open, panes
  // that unambiguously name two axes become the axes.
  function onOverlays() {
    if (syncing || !fp || !fp.isOpen()) return;
    adoptWiper();
  }
  function onOpenClose() {
    if (!fp) return;
    if (fp.isOpen()) {
      if (!adoptWiper() && !X) setAxes(DEFAULT[0], DEFAULT[1], {});
      pushWiper();           // show the two quantities on the map (creates the wiper if needed)
      draw();
    } else {
      LSSample.disarm();     // takes the sample with it
      highlight([]);         // and the brush's rings on the map
    }
    o.onChange();
  }

  // ---- data ----------------------------------------------------------------------------
  function loadDensity() {
    if (!X || !Y) return;
    var key = X + '|' + Y, my = ++densSeq;
    if (densCache[key]) { dens = densCache[key]; return; }
    dens = null;
    fetch(o.apiBase + 'api/analysis/density/?x=' + X + '&y=' + Y).then(function (r) { return r.json(); })
      .then(function (j) {
        if (j.error) { cubeError = j.error; return; }
        densCache[key] = j;
        if (my === densSeq) { dens = j; draw(); }
      }).catch(function () {});
  }
  function lsVal(id, key) {
    if (!lsv) return null;
    var row = lsv.values[String(id)], i = kidx[key];
    return (row && i != null) ? row[i] : null;
  }
  // The sample's values on the current axes.
  function refreshBlotch() {
    var c = LSSample.get(), my = ++blotchSeq;
    blotch = null;
    if (!c || !X || !Y) return;
    var fx = byKey[X] && byKey[X].fine, fy = byKey[Y] && byKey[Y].fine;
    var p = (fx && fy)
      ? LSSample.fine([fx, fy]).then(function (r) { return r && { x: r[fx], y: r[fy], n: r.n, fine: true }; })
      : LSSample.cells([X, Y]).then(function (r) { return r && !r.error && { x: r[X], y: r[Y], n: r.n, fine: false }; });
    p.then(function (b) { if (my !== blotchSeq) return; blotch = b; draw(); }).catch(function () {});
  }
  function onSample() {
    var c = LSSample.get();
    if (c && el.radius && Math.abs(parseFloat(el.radius.value) - 2 * c.r) > 0.01) el.radius.value = 2 * c.r;   // a sample from the URL
    refreshBlotch(); draw(); o.onChange();
  }

  // ---- geometry ------------------------------------------------------------------------
  function axisOf(k) {
    var m = q(k);
    if (m) return { min: m.min, max: m.max, bins: m.bins, classes: m.classes || null, integer: !!m.integer,
                    label: m.label + (m.units ? ' (' + m.units + ')' : ''), units: m.units };
    var a = byKey[k] || {};
    return { min: 0, max: 1, bins: 50, classes: null, label: a.label || k, units: '' };
  }
  function pos(ax, v) {   // value -> axis coordinate (class position for an ordinal quantity)
    if (ax.classes) { var i = ax.classes.indexOf(v); return i < 0 ? null : i + 0.5; }
    return v;
  }
  // Two panes side by side on the same axes: [0] terrain, [1] landslides.
  function dims() {
    var W = el.svg.clientWidth, H = el.svg.clientHeight;
    var top = 18, bot = 34, ml = 42, gap = 48, mr = 10;
    var w = Math.floor((W - ml - gap - mr) / 2), h = H - top - bot;
    return { W: W, H: H, w: w, h: h, panes: [{ x0: ml, y0: top }, { x0: ml + w + gap, y0: top }] };
  }
  function niceStep(span, n) { var raw = span / n, p = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / p; return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p; }
  function fmtV(ax, v) {
    if (ax.classes) return String(v);
    var span = ax.max - ax.min;
    return span >= 50 ? String(Math.round(v)) : span >= 5 ? v.toFixed(1) : v.toFixed(2);
  }
  function fmtNum(v) {
    if (!v) return '0';
    var a = Math.abs(v);
    if (a >= 1e6) return (v / 1e6).toFixed(a >= 1e7 ? 0 : 1) + 'M';
    if (a >= 1e3) return (v / 1e3).toFixed(a >= 1e4 ? 0 : 1) + 'k';
    if (a >= 10) return v.toFixed(0);
    if (a >= 1) return v.toFixed(1);
    if (a >= 0.01) return v.toFixed(2);
    return v.toExponential(1);
  }

  // ---- colour scales -------------------------------------------------------------------------
  function logCatchBinner(hi, n) {
    var top = hi > 0 ? hi : 1, lo = top / Math.pow(10, LOG_DECADES);
    var a = Math.log(lo), b = Math.log(top), d = (b - a) || 1, edges = [0, lo];
    for (var i = 1; i <= n; i++) edges.push(Math.exp(a + d * i / n));
    return { edges: edges, bin: function (v) {
      if (v <= 0 || v < lo) return 0;
      return Math.max(1, Math.min(n, Math.floor((Math.log(v) - a) / d * n) + 1));
    } };
  }
  // A fixed log scale from lo to hi; below lo is the catch bin, above hi the top bin.
  function logRangeBinner(lo, hi, n) {
    var a = Math.log(lo), d = Math.log(hi) - a, edges = [0, lo];
    for (var i = 1; i <= n; i++) edges.push(Math.exp(a + d * i / n));
    return { edges: edges, bin: function (v) {
      if (v <= 0 || v < lo) return 0;
      return Math.max(1, Math.min(n, Math.floor((Math.log(v) - a) / d * n) + 1));
    } };
  }
  function blues(t) {   // white -> #08306b
    var s = [[247, 251, 255], [198, 219, 239], [107, 174, 214], [33, 113, 181], [8, 48, 107]];
    var k = Math.min(3.999, Math.max(0, t) * 4), i = Math.floor(k), f = k - i, a = s[i], b = s[i + 1];
    return 'rgb(' + Math.round(a[0] + (b[0] - a[0]) * f) + ',' + Math.round(a[1] + (b[1] - a[1]) * f) + ',' + Math.round(a[2] + (b[2] - a[2]) * f) + ')';
  }

  // ---- draw --------------------------------------------------------------------------------
  function draw() {
    if (!el.svg || !fp || !fp.isOpen() || !X || !Y) return;
    var d = dims(); if (d.w <= 20 || d.h <= 20) return;
    var qx = axisOf(X), qy = axisOf(Y), f = 1 << level;
    var nx = qx.bins, ny = qy.bins, cnx = Math.ceil(nx / f), cny = Math.ceil(ny / f);
    var cw = d.w / nx, ch = d.h / ny;      // one raw bin, in pixels
    var panes = d.panes.map(function (p) {
      var sx = function (v) { return p.x0 + (v - qx.min) / (qx.max - qx.min) * d.w; };
      var sy = function (v) { return p.y0 + d.h - (v - qy.min) / (qy.max - qy.min) * d.h; };
      // the merged cell (cxi, cyi): raw bins [cxi*f, min(nx, (cxi+1)*f))
      var cell = function (cxi, cyi) {
        var xa = cxi * f, xb = Math.min(nx, xa + f), ya = cyi * f, yb = Math.min(ny, ya + f);
        return [p.x0 + xa * cw, p.y0 + d.h - yb * ch, (xb - xa) * cw + 0.6, (yb - ya) * ch + 0.6];
      };
      return { x0: p.x0, y0: p.y0, sx: sx, sy: sy, cell: cell };
    });
    var cv = el.heat, dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(d.W * dpr); cv.height = Math.round(d.H * dpr);
    var ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, d.W, d.H);

    // the landslides on this plane (pixel positions in the landslide pane)
    var pts = [], fd = o.features ? o.features() : null, test = o.inView ? o.inView() : null;
    var lsRaw = new Float64Array(nx * ny), B = panes[1];
    if (lsv && fd && fd.features) {
      fd.features.forEach(function (ft) {
        var id = ft.properties.landslide_id != null ? ft.properties.landslide_id : ft.properties.id;
        var vx = lsVal(id, X), vy = lsVal(id, Y);
        if (vx == null || vy == null) return;
        var c = ft.geometry && ft.geometry.coordinates;
        if (test && c && !test(c[0], c[1])) return;
        var px = pos(qx, vx), py = pos(qy, vy);
        if (px == null || py == null) return;
        var xi = Math.min(nx - 1, Math.max(0, Math.floor((px - qx.min) / (qx.max - qx.min) * nx)));
        var yi = Math.min(ny - 1, Math.max(0, Math.floor((py - qy.min) / (qy.max - qy.min) * ny)));
        lsRaw[yi * nx + xi]++;
        var type = (ft.properties.landslide_type || '').toLowerCase().indexOf('cat') === 0 ? 'catastrophic' : 'slow';
        pts.push({ x: B.sx(px), y: B.sy(py), id: id, name: ft.properties.unique_name, ft: ft, vx: vx, vy: vy, type: type,
                   lon: c ? c[0] : null, lat: c ? c[1] : null });
      });
    }
    var haveDens = dens && dens.xsize === nx && dens.ysize === ny;
    var tg = haveDens ? coarsen(dens.grid, nx, ny, level) : null;      // terrain, merged
    var lg = coarsen(lsRaw, nx, ny, level);                              // landslides, merged
    var grid = lsMode !== 'points', i, xi, yi, r;

    // pane 0: the terrain density, log blues -- and a faint copy of it under
    // the landslide pane, so the two read against the same ground.
    var tMax = 0;
    if (tg) {
      for (i = 0; i < tg.g.length; i++) if (tg.g[i] > tMax) tMax = tg.g[i];
      var lmax = Math.log(tMax + 1);
      for (yi = 0; yi < cny; yi++) for (xi = 0; xi < cnx; xi++) {
        var tv = tg.g[yi * cnx + xi]; if (!tv) continue;
        ctx.fillStyle = blues(Math.log(tv + 1) / lmax);
        r = panes[0].cell(xi, yi); ctx.fillRect(r[0], r[1], r[2], r[3]);
        ctx.globalAlpha = 0.28;
        r = panes[1].cell(xi, yi); ctx.fillRect(r[0], r[1], r[2], r[3]);
        ctx.globalAlpha = 1;
      }
    }
    // pane 1: the landslide grid. A merged cell's count is the sum of its
    // raw cells' counts and its terrain the sum of their km², so the rate
    // (count / km² x 1000) is the area-weighted mean of the raw rates with
    // the empty raw cells weighing in -- not a mean of rates. A cell with
    // landslides but no sampled terrain has no rate; it is drawn in the
    // catch colour rather than dropped.
    var lBins = null, noTerrain = 0;
    if (grid) {
      var lsVals = new Float64Array(lg.g.length), lsMax = 0;
      for (i = 0; i < lg.g.length; i++) {
        if (!lg.g[i]) continue;
        var tc = tg ? tg.g[i] : 0;
        var v = lsMode === 'prop' ? (tc > 0 ? lg.g[i] / tc * 1000 : -1) : lg.g[i];   // cells are 1 km²; -1 = no terrain
        lsVals[i] = v; if (v > lsMax) lsMax = v;
      }
      lBins = lsMode === 'prop' ? logRangeBinner(RATE_LO, RATE_HI, LS_RAMP.length) : logCatchBinner(lsMax, LS_RAMP.length);
      for (yi = 0; yi < cny; yi++) for (xi = 0; xi < cnx; xi++) {
        i = yi * cnx + xi; if (!lsVals[i]) continue;
        r = panes[1].cell(xi, yi);
        var b = lsVals[i] < 0 ? 0 : lBins.bin(lsVals[i]);
        if (lsVals[i] < 0) noTerrain++;
        ctx.fillStyle = b === 0 ? LS_CATCH : LS_RAMP[b - 1];
        ctx.fillRect(r[0], r[1], r[2], r[3]);
      }
    }
    // the sample, on the terrain pane
    if (blotch && blotch.x) {
      var A = panes[0];
      ctx.fillStyle = blotch.fine ? COLOR.patch : COLOR.patchDot;
      for (i = 0; i < blotch.x.length; i++) {
        var bx = blotch.x[i], by = blotch.y[i];
        if (bx == null || by == null || isNaN(bx) || isNaN(by)) continue;
        var ppx = pos(qx, bx), ppy = pos(qy, by); if (ppx == null || ppy == null) continue;
        if (blotch.fine) ctx.fillRect(A.sx(ppx) - 1, A.sy(ppy) - 1, 2, 2);
        else { ctx.beginPath(); ctx.arc(A.sx(ppx), A.sy(ppy), 2.5, 0, 6.283); ctx.fill(); }
      }
    }

    // svg: frames, ticks, labels, points, brush
    var svg = el.svg; while (svg.firstChild) svg.removeChild(svg.firstChild);
    var sampled = !!LSSample.get();
    var titles = ['Terrain · km² per cell', 'Landslides · ' + (lsMode === 'points' ? 'points' : lsMode === 'count' ? 'count per cell' : 'per 1000 km²') +
                  (grid && sampled ? ' · sample as points' : '')];
    panes.forEach(function (p, pi) {
      svg.appendChild(svgEl('rect', { x: p.x0, y: p.y0, width: d.w, height: d.h, fill: 'none', stroke: '#ddd' }));
      txt(svg, titles[pi], p.x0, p.y0 - 6, 'start', '#555');
      ticks(qx).forEach(function (t) {
        svg.appendChild(svgEl('line', { x1: p.sx(t.p), y1: p.y0 + d.h, x2: p.sx(t.p), y2: p.y0 + d.h + 3, stroke: '#bbb' }));
        txt(svg, t.l, p.sx(t.p), p.y0 + d.h + 13, 'middle');
      });
      ticks(qy).forEach(function (t) {
        svg.appendChild(svgEl('line', { x1: p.x0 - 3, y1: p.sy(t.p), x2: p.x0, y2: p.sy(t.p), stroke: '#bbb' }));
        txt(svg, t.l, p.x0 - 5, p.sy(t.p) + 3, 'end');
      });
      txt(svg, qx.label + ' →', p.x0 + d.w / 2, d.H - 2, 'middle', '#666');
    });
    var yl = txt(svg, qy.label + ' →', 10, d.panes[0].y0 + d.h / 2, 'middle', '#666');
    yl.setAttribute('transform', 'rotate(-90 10 ' + (d.panes[0].y0 + d.h / 2) + ')');

    // points: all of them in points mode; in the grid modes only the sample's,
    // as points over the grid (Hig, 2026-10-10) -- the grid itself is unchanged.
    var hl = [], inSample = 0, drawn = [];
    pts.forEach(function (p) {
      var inB = brush && p.vx >= brush.x0 && p.vx <= brush.x1 && p.vy >= brush.y0 && p.vy <= brush.y1;
      var inS = p.lon != null && LSSample.inside(p.lon, p.lat);
      if (inS) inSample++;
      if (inB || inS) hl.push(p);
      if (!grid || inS) {
        svg.appendChild(svgEl('circle', { cx: p.x, cy: p.y, r: (inB || inS) ? 4 : 3, fill: COLOR[p.type], 'fill-opacity': grid ? 0.95 : 0.8,
                                          stroke: (inB || inS) ? COLOR.ring : '#fff', 'stroke-width': (inB || inS) ? 1.5 : 0.6 }));
        drawn.push(p);
      } else if (inB) {
        svg.appendChild(svgEl('circle', { cx: p.x, cy: p.y, r: 3.5, fill: 'none', stroke: COLOR.ring, 'stroke-width': 1.2 }));
      }
    });
    if (brush) {
      panes.forEach(function (p) {
        var bx0 = p.sx(brush.x0), bx1 = p.sx(brush.x1), by0 = p.sy(brush.y0), by1 = p.sy(brush.y1);
        svg.appendChild(svgEl('rect', { x: Math.min(bx0, bx1), y: Math.min(by0, by1), width: Math.abs(bx1 - bx0), height: Math.abs(by0 - by1),
                                        fill: 'rgba(60,103,177,0.12)', stroke: COLOR.brush, 'stroke-width': 1, 'stroke-dasharray': '3 2', 'pointer-events': 'none' }));
      });
    }
    svg.appendChild(svgEl('rect', { id: 'an-drag', fill: 'rgba(0,0,0,0.06)', stroke: '#666', 'stroke-width': 1, display: 'none', 'pointer-events': 'none' }));
    P = { d: d, qx: qx, qy: qy, panes: panes, pts: pts, drawn: drawn, tg: tg, lg: lg, f: f, cnx: cnx, cny: cny, cw: cw, ch: ch, grid: grid };
    highlight(hl);
    el.clear.classList.toggle('disabled', !brush);
    el.pf.style.display = (sampled && PF_KEYS.indexOf(X) >= 0 && PF_KEYS.indexOf(Y) >= 0) ? '' : 'none';
    // count + notes
    var msg = pts.length ? pts.length + ' landslides' : (lsv ? 'no values' : 'loading…');
    if (hl.length) msg += ' · ' + hl.length + ' selected';
    el.count.textContent = msg;
    el.note.textContent = cubeError ? cubeError : (dens ? '' : (quantities && !q(X) || quantities && !q(Y) ? 'no terrain density for this pair on this server' : ''));
    foot(qx, qy, tMax, lBins, inSample, noTerrain);
  }
  function ticks(ax) {
    if (ax.classes) return ax.classes.map(function (c, i) { return { p: i + 0.5, l: String(c) }; });
    var st = niceStep(ax.max - ax.min, 5), out = [];
    for (var v = Math.ceil(ax.min / st) * st; v <= ax.max + 1e-9; v += st) out.push({ p: v, l: fmtV(ax, v) });
    return out;
  }
  // A legend bar with its numbers AT the colour boundaries (a tick each),
  // not spread evenly beneath it: ticks = [{at: 0..1, label}].
  function legendBar(barHtml, ticks) {
    return '<div class="leg-scale">' + barHtml + '<div class="leg-ticks">' + ticks.map(function (t) {
      return '<span class="leg-tick" style="left:' + (t.at * 100).toFixed(2) + '%"><i></i>' + t.label + '</span>';
    }).join('') + '</div></div>';
  }
  function foot(qx, qy, tMax, lBins, inSample, noTerrain) {
    var html = '';
    if (tMax) {   // the terrain's log scale, as a gradient from 1 km² to the fullest cell; ticks at the decades
      var n = 6, stops = [], ticks = [], lm = Math.log(tMax + 1);
      for (var i = 0; i <= n; i++) stops.push(blues(i / n));
      for (var v = 1; v <= tMax; v *= 10) ticks.push({ at: Math.log(v + 1) / lm, label: fmtNum(v) });
      if (ticks.length && ticks[ticks.length - 1].at < 0.9) ticks.push({ at: 1, label: fmtNum(tMax) });
      html += '<div class="leg-row"><span class="leg-title">Terrain km²/cell</span>' +
              legendBar('<div class="leg-bar" style="background:linear-gradient(to right,' + stops.join(',') + ')"></div>', ticks) + '</div>';
    }
    if (lBins) {
      var cells = LS_RAMP.length + 1;   // the catch swatch, then the ramp
      var catchTitle = 'above zero, below ' + fmtNum(lBins.edges[1]) + (noTerrain ? '; or landslides in a cell with no sampled terrain (' + noTerrain + ')' : '');
      var bar = '<div class="leg-bar"><span style="background:' + LS_CATCH + '" title="' + catchTitle + '"></span>' +
                LS_RAMP.map(function (c, i) {
                  return '<span style="background:' + c + '" title="' + fmtNum(lBins.edges[i + 1]) + ' – ' + fmtNum(lBins.edges[i + 2]) + '"></span>';
                }).join('') + '</div>';
      var lt = [{ at: 0, label: '0' }];
      for (var j = 1; j < lBins.edges.length; j++) lt.push({ at: j / cells, label: fmtNum(lBins.edges[j]) });
      html += '<div class="leg-row"><span class="leg-title" title="' + (lsMode === 'prop' ? 'Fixed scale: the same colour is the same rate at every grid step and on every pair of axes.' : 'Scaled to this plot’s fullest cell.') + '">' +
              (lsMode === 'prop' ? 'Slides/1000 km² (fixed)' : 'Slides/cell') + '</span>' + legendBar(bar, lt) + '</div>';
    }
    var c = LSSample.get();
    if (c) {
      var s = 'Sample ⌀ ' + (2 * c.r) + ' km at ' + c.lat.toFixed(3) + ' N ' + (-c.lon).toFixed(3) + ' W';
      if (blotch && blotch.x) {
        s += ' · ' + blotch.n + (blotch.fine ? ' cells at 60 m' : ' cells at 1 km') +
             ' · ' + summary(qx, blotch.x) + ' · ' + summary(qy, blotch.y) + ' · ' + inSample + ' landslide' + (inSample === 1 ? '' : 's') + ' inside';
      } else s += ' · reading…';
      html += '<div class="an-sample">' + s + '</div>';
    }
    el.foot.innerHTML = html;
    el.foot.style.display = html ? '' : 'none';
  }
  function summary(ax, vals) {
    var v = []; for (var i = 0; i < vals.length; i++) { var t = vals[i]; if (t != null && !isNaN(t)) v.push(t); }
    if (!v.length) return ax.label + ': none';
    v.sort(function (a, b) { return a - b; });
    var at = function (f) { return v[Math.min(v.length - 1, Math.floor(f * v.length))]; };
    if (ax.classes) return ax.label + ' mode ' + fmtV(ax, mode(v));
    return ax.label.replace(/ \(.*\)$/, '') + ' median ' + fmtV(ax, at(0.5)) + ' (' + fmtV(ax, at(0.1)) + '–' + fmtV(ax, at(0.9)) + ')' + (ax.units ? ' ' + ax.units : '');
  }
  function mode(v) { var c = {}, best = v[0], bn = 0; v.forEach(function (t) { c[t] = (c[t] || 0) + 1; if (c[t] > bn) { bn = c[t]; best = t; } }); return best; }

  // ---- highlight on the map -------------------------------------------------------------------
  function ensureLayers() {
    if (!map.getSource(HL)) map.addSource(HL, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    if (!map.getLayer(HL)) {
      map.addLayer({ id: HL, type: 'circle', source: HL,
                     paint: { 'circle-radius': 9, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': COLOR.ring, 'circle-stroke-width': 2 } });
    }
    highlight(_hl);
  }
  function highlight(pts) {
    _hl = pts;
    if (!map.getSource(HL)) return;
    map.getSource(HL).setData({ type: 'FeatureCollection', features: pts.filter(function (p) { return p.lon != null; }).map(function (p) {
      return { type: 'Feature', geometry: { type: 'Point', coordinates: [p.lon, p.lat] }, properties: { id: p.id } };
    }) });
  }

  // ---- brush --------------------------------------------------------------------------------
  function local(e) { var r = el.svg.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; }
  // Which pane a panel-local point is in, or -1.
  function paneAt(x, y) {
    if (!P) return -1;
    for (var i = 0; i < P.panes.length; i++) {
      var p = P.panes[i];
      if (x >= p.x0 && x <= p.x0 + P.d.w && y >= p.y0 && y <= p.y0 + P.d.h) return i;
    }
    return -1;
  }
  function wireBrush() {
    var sx0 = 0, sy0 = 0, pane = -1;
    LSTools.drag(el.svg, {
      onStart: function (e) {
        if (!P) return false;
        var p = local(e); pane = paneAt(p.x, p.y); if (pane < 0) return false;
        sx0 = p.x; sy0 = p.y;
      },
      onMove: function (e) {
        var p = local(e), dr = el.svg.querySelector('#an-drag'); if (!dr) return;
        dr.setAttribute('x', Math.min(sx0, p.x)); dr.setAttribute('y', Math.min(sy0, p.y));
        dr.setAttribute('width', Math.abs(p.x - sx0)); dr.setAttribute('height', Math.abs(p.y - sy0));
        dr.setAttribute('display', 'block');
      },
      onEnd: function (e, info) {
        var dr = el.svg.querySelector('#an-drag'); if (dr) dr.setAttribute('display', 'none');
        if (info.cancelled || !e || !P || pane < 0) return;
        var p = local(e), pn = P.panes[pane];
        if (Math.abs(p.x - sx0) < 4 && Math.abs(p.y - sy0) < 4) return;   // a click, not a box
        var vx = function (px) { return P.qx.min + (px - pn.x0) / P.d.w * (P.qx.max - P.qx.min); };
        var vy = function (py) { return P.qy.min + (pn.y0 + P.d.h - py) / P.d.h * (P.qy.max - P.qy.min); };
        var x0 = clampV(P.qx, Math.min(vx(sx0), vx(p.x))), x1 = clampV(P.qx, Math.max(vx(sx0), vx(p.x)));
        var y0 = clampV(P.qy, Math.min(vy(sy0), vy(p.y))), y1 = clampV(P.qy, Math.max(vy(sy0), vy(p.y)));
        setBrush({ x0: x0, x1: x1, y0: y0, y1: y1 });
      }
    });
  }
  function clampV(ax, v) {
    v = Math.max(ax.min, Math.min(ax.max, v));
    if (ax.classes) return ax.classes[Math.min(ax.classes.length - 1, Math.max(0, Math.floor(v)))];
    if (ax.integer) return Math.round(v);
    return +v.toFixed(3);
  }
  function setBrush(b) {
    brush = b; draw(); syncSlidersToBrush(); o.onChange();
    window.LSTrack && LSTrack.event('explore_filter', { chart: 'analysis', x: X, y: Y });
  }
  function clearBrush() { if (!brush) return; brush = null; draw(); syncSlidersToBrush(); o.onChange(); }
  function sliderAxes() { return SLIDER_KEYS.indexOf(X) >= 0 && SLIDER_KEYS.indexOf(Y) >= 0 && o.sliders; }
  // lw x n10: the box IS the two sliders (the only quantities with filters).
  function syncSlidersToBrush() {
    if (!sliderAxes() || syncing) return;
    syncing = true;
    try {
      var r = {}; r[X] = brush ? [brush.x0, brush.x1] : null; r[Y] = brush ? [brush.y0, brush.y1] : null;
      o.sliders.set(r);
    } finally { syncing = false; }
  }
  // map.js buildFilter: the sliders moved; mirror them into the box.
  function onFilter() {
    if (!sliderAxes() || syncing || !fp || !fp.isOpen()) return;
    var s = o.sliders.get(); if (!s) return;
    var lx = s[X], ly = s[Y];
    var active = lx.lo > lx.min || lx.hi < lx.max || ly.lo > ly.min || ly.hi < ly.max;
    var nb = active ? { x0: lx.lo, x1: lx.hi, y0: ly.lo, y1: ly.hi } : null;
    if (JSON.stringify(nb) !== JSON.stringify(brush)) { brush = nb; draw(); o.onChange(); }
    else draw();
  }

  // ---- hover / click ------------------------------------------------------------------------
  // The nearest DRAWN point (all of them in points mode, the sample's in the grid modes).
  function nearest(e) {
    if (!P) return null;
    var l = local(e), best = null, bd = 64;
    P.drawn.forEach(function (p) { var dd = (p.x - l.x) * (p.x - l.x) + (p.y - l.y) * (p.y - l.y); if (dd < bd) { bd = dd; best = p; } });
    return best;
  }
  function onHover(e) {
    if (!P) return;
    var l = local(e), html = null, pi = paneAt(l.x, l.y);
    var p = pi === 1 ? nearest(e) : null;
    if (p) html = '<b>' + esc(p.name || ('#' + p.id)) + '</b><br>' + P.qx.label + ' ' + fmtV(P.qx, p.vx) + ' · ' + P.qy.label + ' ' + fmtV(P.qy, p.vy);
    else if (pi >= 0) {
      var pn = P.panes[pi];
      var cxi = Math.min(P.cnx - 1, Math.floor((l.x - pn.x0) / (P.cw * P.f))), cyi = Math.min(P.cny - 1, Math.floor((pn.y0 + P.d.h - l.y) / (P.ch * P.f)));
      var tc = P.tg ? P.tg.g[cyi * P.cnx + cxi] || 0 : 0, lc = P.lg.g[cyi * P.cnx + cxi] || 0;
      html = cellLabel(P.qx, cxi, P.qx.bins) + ', ' + cellLabel(P.qy, cyi, P.qy.bins) + ' · ' + fmtNum(tc) + ' km² · ' +
             lc + ' slide' + (lc === 1 ? '' : 's') + (lc && tc ? ' (' + fmtNum(lc / tc * 1000) + '/1000 km²)' : lc ? ' (no sampled terrain)' : '');
    }
    if (!html) { el.tip.style.display = 'none'; return; }
    el.tip.innerHTML = html; el.tip.style.display = 'block';
    var br = el.body.getBoundingClientRect();
    el.tip.style.left = Math.min(br.width - 200, e.clientX - br.left + 12) + 'px';
    el.tip.style.top = (e.clientY - br.top + 12) + 'px';
  }
  // A merged cell's extent on an axis: one class, or a value range.
  function cellLabel(ax, ci, n) {
    var a = ci * P.f, b = Math.min(n, a + P.f);
    if (ax.classes) return ax.classes.slice(a, b).join('/');
    var va = ax.min + a / n * (ax.max - ax.min), vb = ax.min + b / n * (ax.max - ax.min);
    return fmtV(ax, va) + '–' + fmtV(ax, vb) + (ax.units ? ' ' + ax.units : '');
  }
  function onClick(e) { var p = nearest(e); if (p && o.openLandslide) o.openLandslide(p.id, p.ft); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function svgEl(n, a) { var e = document.createElementNS('http://www.w3.org/2000/svg', n); for (var k in a) e.setAttribute(k, a[k]); return e; }
  function txt(svg, s, x, y, anchor, fill) {
    var t = svgEl('text', { x: x, y: y, 'text-anchor': anchor || 'start', 'font-size': 9, fill: fill || '#888' });
    t.textContent = s; svg.appendChild(t); return t;
  }

  // ---- URL state (map.js _VIEW_PARAMS row 'ax') ------------------------------------------------
  function state() {
    if (!X || !Y) return null;
    return { x: X, y: Y, brush: brush, ls: lsMode === 'points' ? null : lsMode, gr: level || null };
  }
  function setState(s) {
    if (!el.panel) { pendingState = s; return; }
    if (s.ls && (s.ls === 'count' || s.ls === 'prop')) { lsMode = s.ls; if (el.ls) el.ls.value = lsMode; }
    else if (s.ls === null) { lsMode = 'points'; if (el.ls) el.ls.value = lsMode; }
    if (s.x && s.y && s.x !== s.y) setAxes(s.x, s.y, { push: false });
    if (s.gr !== undefined) { level = s.gr || 0; syncGrid(); }
    if (s.brush !== undefined) { brush = s.brush; syncSlidersToBrush(); }
    draw();
  }
  function keys() { return X && Y ? [X, Y] : null; }

  return { init: init, panel: panel, openWith: openWith, onOverlays: onOverlays, onFilter: onFilter,
           redraw: draw, state: state, setState: setState, keys: keys };
})();
