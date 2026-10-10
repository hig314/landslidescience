/* The analysis panel: two overlays as axes, the map as the sampler.
 *
 * Pick two quantities (any two overlays that carry `values`, map.js
 * OVERLAYS) and the panel draws the terrain's joint density of the pair
 * (api/analysis/density/, the quantity cube in inventory/analysis.py: every
 * land cell of a 1 km EPSG:3338 lattice, point-sampled from the native
 * product) as a log backdrop, with every landslide at its own values
 * (ls_quantities.json, tools/analysis/sample_quantities.py) as points, or
 * as a count / per-1000-km² grid like the old susceptibility scatter.
 *
 * THE AXES ARE THE WIPER PANES (Hig, 2026-10-09). Open the panel over a
 * wiper showing two different overlays and they are x (left) and y
 * (right); pick axes in the panel and the wiper is created or re-pointed
 * so the map always shows what the plot is crossing. map.js owns the
 * wiper; this module only reads and asks (opts.wiper.get / set), and
 * map.js calls onOverlays() whenever overlay state changes.
 *
 * SAMPLE: the ◎ button arms LSSample; click the map and the cells inside
 * the circle light up on the plot -- the 60 m value archives where both
 * axes have them, else the cube's lattice cells -- with a median / range
 * summary, and the landslides inside the circle ringed on the plot and on
 * the map. With permafrost products on both axes a "Permafrost detail"
 * button opens the four-plot patch panel (permafrost.js).
 *
 * BRUSH: drag a box on the plot and the landslides inside it are ringed on
 * the map (client side, from the values file). With lw and n10 on the
 * axes the box also drives those two filter sliders, which exist for
 * exactly this, and the sliders move the box back.
 *
 * URL: ax=<x>|<y>, bx=x0,x1,y0,y1 (the brush), ls=count|prop (the
 * landslide layer when not points), an=analysis; the sample is LSSample's
 * smp=. The old an=scatter and an=pf open this panel on lw|n10 and
 * pzi|prob (map.js).
 *
 * Global `window.LSAnalysis`; map.js calls init() once the map exists.
 */
window.LSAnalysis = (function () {
  'use strict';

  var COLOR = { slow: '#d6604d', catastrophic: '#3f67b1', patch: 'rgba(255,143,0,0.45)', patchDot: 'rgba(255,143,0,0.7)',
                ring: '#111', brush: '#3f67b1' };
  // Terrain = pale Blues under everything; landslide grid = YlOrRd with an
  // off-ramp CATCH colour for "above zero, below the bottom of the scale"
  // (the susceptibility scatter's scheme; see its history in git).
  var TERRAIN_RAMP = ['#f7fbff', '#d8e7f5', '#b3d0e8', '#86b3d8', '#5793c3'];
  var LS_RAMP = ['#ffeda0', '#fed976', '#feb24c', '#fd8d3c', '#fc4e2a', '#e31a1c', '#b10026'];
  var LS_CATCH = '#bcbddc', LOG_DECADES = 3;
  var PF_KEYS = ['maat', 'pzi', 'magt', 'prob', 'maat60', 'pzi60', 'magt60', 'prob60'];
  var SLIDER_KEYS = ['lw', 'n10'];
  var DEFAULT = ['lw', 'n10'];
  var HL = 'an-highlight';

  var map = null, o = {}, el = {}, fp = null;
  var quantities = null, cubeError = null;      // the server registry
  var axes = [], byKey = {}, byOv = {};         // the overlays that can be axes
  var lsv = null, kidx = {};                    // ls_quantities.json
  var X = null, Y = null, lsMode = 'points';
  var densCache = {}, dens = null, densSeq = 0;
  var brush = null;                             // {x0, x1, y0, y1} in value space
  var blotch = null, blotchSeq = 0;             // the sample on the current axes
  var P = null;                                 // the drawn plot: {d, qx, qy, sx, sy, pts, lsGrid, tBins, lBins}
  var syncing = false, pendingState = null, _hl = [];

  // ---- init --------------------------------------------------------------------------
  function init(opts) {
    o = opts; map = opts.map;
    var panel = document.getElementById('an-float'); if (!panel) return;
    el.panel = panel;
    ['x', 'y', 'ls', 'sample', 'radius', 'count', 'pf', 'clear', 'close', 'body', 'heat', 'svg', 'foot', 'note'].forEach(function (k) {
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
    el.sample.addEventListener('click', function (e) {
      e.preventDefault();
      if (LSSample.isArmed()) LSSample.disarm(); else LSSample.arm(parseFloat(el.radius.value) || 10);
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
      .then(function (j) { quantities = j.quantities || {}; if (j.error) cubeError = j.error; fillPickers(); loadDensity(); draw(); })
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
    loadDensity(); refreshBlotch(); draw(); syncSlidersToBrush();
    if (how.push) pushWiper();
    o.onChange();
    window.LSTrack && LSTrack.event('explore_chart', { chart: 'analysis', x: X, y: Y });
  }
  function openWith(x, y) {
    if (x && y && x !== y) setAxes(x, y, { push: false });
    if (fp && !fp.isOpen()) fp.open(); else { pushWiper(); draw(); }
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
      LSSample.disarm();
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
  function dims() {
    var W = el.svg.clientWidth, H = el.svg.clientHeight;
    return { W: W, H: H, x0: 42, y0: 8, w: W - 52, h: H - 34 };
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

  // ---- binners (the scatter's) -------------------------------------------------------------
  function quantileBinner(vals, n) {
    var s = vals.slice().sort(function (a, b) { return a - b; }), m = s.length, edges = [];
    for (var i = 0; i <= n; i++) edges.push(m ? s[Math.min(m - 1, Math.floor(i / n * m))] : 0);
    if (m) { edges[0] = s[0]; edges[n] = s[m - 1]; }
    return { edges: edges, bin: function (v) { var b = 0; for (var i = 1; i < n; i++) { if (v >= edges[i]) b = i; } return b; } };
  }
  function logCatchBinner(hi, n) {
    var top = hi > 0 ? hi : 1, lo = top / Math.pow(10, LOG_DECADES);
    var a = Math.log(lo), b = Math.log(top), d = (b - a) || 1, edges = [0, lo];
    for (var i = 1; i <= n; i++) edges.push(Math.exp(a + d * i / n));
    return { edges: edges, bin: function (v) {
      if (v <= 0 || v < lo) return 0;
      return Math.max(1, Math.min(n, Math.floor((Math.log(v) - a) / d * n) + 1));
    } };
  }
  function blues(t) {   // white -> #08306b
    var s = [[247, 251, 255], [198, 219, 239], [107, 174, 214], [33, 113, 181], [8, 48, 107]];
    var k = Math.min(3.999, t * 4), i = Math.floor(k), f = k - i, a = s[i], b = s[i + 1];
    return 'rgb(' + Math.round(a[0] + (b[0] - a[0]) * f) + ',' + Math.round(a[1] + (b[1] - a[1]) * f) + ',' + Math.round(a[2] + (b[2] - a[2]) * f) + ')';
  }

  // ---- draw --------------------------------------------------------------------------------
  function draw() {
    if (!el.svg || !fp || !fp.isOpen() || !X || !Y) return;
    var d = dims(); if (d.w <= 0 || d.h <= 0) return;
    var qx = axisOf(X), qy = axisOf(Y);
    var sx = function (v) { return d.x0 + (v - qx.min) / (qx.max - qx.min) * d.w; };
    var sy = function (v) { return d.y0 + d.h - (v - qy.min) / (qy.max - qy.min) * d.h; };
    var cw = d.w / qx.bins, ch = d.h / qy.bins;
    var cv = el.heat, dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(d.W * dpr); cv.height = Math.round(d.H * dpr);
    var ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, d.W, d.H);
    var cellRect = function (xi, yi) { return [d.x0 + xi * cw, d.y0 + d.h - (yi + 1) * ch, cw + 0.6, ch + 0.6]; };

    // the landslides on this plane
    var pts = [], fd = o.features ? o.features() : null, test = o.inView ? o.inView() : null;
    var lsGrid = new Int32Array(qx.bins * qy.bins);
    if (lsv && fd && fd.features) {
      fd.features.forEach(function (ft) {
        var id = ft.properties.landslide_id != null ? ft.properties.landslide_id : ft.properties.id;
        var vx = lsVal(id, X), vy = lsVal(id, Y);
        if (vx == null || vy == null) return;
        var c = ft.geometry && ft.geometry.coordinates;
        if (test && c && !test(c[0], c[1])) return;
        var px = pos(qx, vx), py = pos(qy, vy);
        if (px == null || py == null) return;
        var xi = Math.min(qx.bins - 1, Math.max(0, Math.floor((px - qx.min) / (qx.max - qx.min) * qx.bins)));
        var yi = Math.min(qy.bins - 1, Math.max(0, Math.floor((py - qy.min) / (qy.max - qy.min) * qy.bins)));
        lsGrid[yi * qx.bins + xi]++;
        var type = (ft.properties.landslide_type || '').toLowerCase().indexOf('cat') === 0 ? 'catastrophic' : 'slow';
        pts.push({ x: sx(px), y: sy(py), id: id, name: ft.properties.unique_name, ft: ft, vx: vx, vy: vy, type: type,
                   lon: c ? c[0] : null, lat: c ? c[1] : null, xi: xi, yi: yi });
      });
    }
    // terrain density (log, blues) -- or, in grid modes, the scatter's quantile pale blues
    var tBins = null, lBins = null, tv, i, xi, yi;
    var grid = lsMode !== 'points';
    if (dens && dens.xsize === qx.bins && dens.ysize === qy.bins) {
      if (grid) {
        var tVals = [];
        for (i = 0; i < dens.grid.length; i++) if (dens.grid[i]) tVals.push(dens.grid[i]);
        tBins = quantileBinner(tVals, TERRAIN_RAMP.length);
      }
      var lmax = Math.log(dens.max + 1);
      for (yi = 0; yi < qy.bins; yi++) for (xi = 0; xi < qx.bins; xi++) {
        tv = dens.grid[yi * qx.bins + xi]; if (!tv) continue;
        var r = cellRect(xi, yi);
        ctx.fillStyle = grid ? TERRAIN_RAMP[tBins.bin(tv)] : blues(Math.log(tv + 1) / lmax);
        ctx.fillRect(r[0], r[1], r[2], r[3]);
      }
    }
    if (grid) {
      var lsVals = new Float64Array(lsGrid.length), lsMax = 0;
      for (i = 0; i < lsGrid.length; i++) {
        if (!lsGrid[i]) continue;
        tv = dens ? dens.grid[i] : 0;
        var v = lsMode === 'prop' ? (tv > 0 ? lsGrid[i] / tv * 1000 : 0) : lsGrid[i];   // per 1000 km² (cells are 1 km²)
        lsVals[i] = v; if (v > lsMax) lsMax = v;
      }
      lBins = logCatchBinner(lsMax, LS_RAMP.length);
      for (yi = 0; yi < qy.bins; yi++) for (xi = 0; xi < qx.bins; xi++) {
        i = yi * qx.bins + xi; if (!lsVals[i]) continue;
        var rr = cellRect(xi, yi), b = lBins.bin(lsVals[i]);
        ctx.fillStyle = b === 0 ? LS_CATCH : LS_RAMP[b - 1];
        ctx.fillRect(rr[0], rr[1], rr[2], rr[3]);
      }
    }
    // the sample, on this plane
    if (blotch && blotch.x) {
      ctx.fillStyle = blotch.fine ? COLOR.patch : COLOR.patchDot;
      for (i = 0; i < blotch.x.length; i++) {
        var bx = blotch.x[i], by = blotch.y[i];
        if (bx == null || by == null || isNaN(bx) || isNaN(by)) continue;
        var ppx = pos(qx, bx), ppy = pos(qy, by); if (ppx == null || ppy == null) continue;
        if (blotch.fine) ctx.fillRect(sx(ppx) - 1, sy(ppy) - 1, 2, 2);
        else { ctx.beginPath(); ctx.arc(sx(ppx), sy(ppy), 2.5, 0, 6.283); ctx.fill(); }
      }
    }

    // svg: frame, ticks, labels, points, brush
    var svg = el.svg; while (svg.firstChild) svg.removeChild(svg.firstChild);
    svg.appendChild(svgEl('rect', { x: d.x0, y: d.y0, width: d.w, height: d.h, fill: 'none', stroke: '#ddd' }));
    ticks(qx).forEach(function (t) {
      svg.appendChild(svgEl('line', { x1: sx(t.p), y1: d.y0 + d.h, x2: sx(t.p), y2: d.y0 + d.h + 3, stroke: '#bbb' }));
      txt(svg, t.l, sx(t.p), d.y0 + d.h + 13, 'middle');
    });
    ticks(qy).forEach(function (t) {
      svg.appendChild(svgEl('line', { x1: d.x0 - 3, y1: sy(t.p), x2: d.x0, y2: sy(t.p), stroke: '#bbb' }));
      txt(svg, t.l, d.x0 - 5, sy(t.p) + 3, 'end');
    });
    txt(svg, qx.label + ' →', d.x0 + d.w / 2, d.H - 2, 'middle', '#666');
    var yl = txt(svg, qy.label + ' →', 10, d.y0 + d.h / 2, 'middle', '#666');
    yl.setAttribute('transform', 'rotate(-90 10 ' + (d.y0 + d.h / 2) + ')');
    var hl = [], inSample = 0;
    pts.forEach(function (p) {
      var inB = brush && p.vx >= brush.x0 && p.vx <= brush.x1 && p.vy >= brush.y0 && p.vy <= brush.y1;
      var inS = p.lon != null && LSSample.inside(p.lon, p.lat);
      if (inS) inSample++;
      if (inB || inS) hl.push(p);
      if (!grid) {
        svg.appendChild(svgEl('circle', { cx: p.x, cy: p.y, r: (inB || inS) ? 4 : 3, fill: COLOR[p.type], 'fill-opacity': 0.8,
                                          stroke: (inB || inS) ? COLOR.ring : '#fff', 'stroke-width': (inB || inS) ? 1.5 : 0.6 }));
      } else if (inB || inS) {
        svg.appendChild(svgEl('circle', { cx: p.x, cy: p.y, r: 3.5, fill: 'none', stroke: COLOR.ring, 'stroke-width': 1.2 }));
      }
    });
    if (brush) {
      var bx0 = sx(pos(qx, brush.x0) != null ? brush.x0 : qx.min), bx1 = sx(brush.x1), by0 = sy(brush.y0), by1 = sy(brush.y1);
      svg.appendChild(svgEl('rect', { x: Math.min(bx0, bx1), y: Math.min(by0, by1), width: Math.abs(bx1 - bx0), height: Math.abs(by0 - by1),
                                      fill: 'rgba(60,103,177,0.12)', stroke: COLOR.brush, 'stroke-width': 1, 'stroke-dasharray': '3 2', 'pointer-events': 'none' }));
    }
    svg.appendChild(svgEl('rect', { id: 'an-drag', fill: 'rgba(0,0,0,0.06)', stroke: '#666', 'stroke-width': 1, display: 'none', 'pointer-events': 'none' }));
    P = { d: d, qx: qx, qy: qy, sx: sx, sy: sy, pts: pts, lsGrid: lsGrid, cw: cw, ch: ch };
    highlight(hl);
    el.clear.classList.toggle('disabled', !brush);
    el.pf.style.display = (LSSample.get() && PF_KEYS.indexOf(X) >= 0 && PF_KEYS.indexOf(Y) >= 0) ? '' : 'none';
    // count + notes
    var msg = pts.length ? pts.length + ' landslides' : (lsv ? 'no values' : 'loading…');
    if (hl.length) msg += ' · ' + hl.length + ' selected';
    el.count.textContent = msg;
    el.note.textContent = cubeError ? cubeError : (dens ? '' : (quantities && !q(X) || quantities && !q(Y) ? 'no terrain density for this pair on this server' : ''));
    foot(qx, qy, tBins, lBins, inSample);
  }
  function ticks(ax) {
    if (ax.classes) return ax.classes.map(function (c, i) { return { p: i + 0.5, l: String(c) }; });
    var st = niceStep(ax.max - ax.min, 6), out = [];
    for (var v = Math.ceil(ax.min / st) * st; v <= ax.max + 1e-9; v += st) out.push({ p: v, l: fmtV(ax, v) });
    return out;
  }
  function foot(qx, qy, tBins, lBins, inSample) {
    var html = '';
    if (tBins && lBins) {
      var bar = function (ramp, edges, catchC) {
        var s = catchC ? '<span style="background:' + catchC + '" title="above zero, below ' + fmtNum(edges[1]) + '"></span>' : '';
        var off = catchC ? 1 : 0;
        return '<div class="leg-bar">' + s + ramp.map(function (c, i) {
          return '<span style="background:' + c + '" title="' + fmtNum(edges[i + off]) + ' – ' + fmtNum(edges[i + off + 1]) + '"></span>';
        }).join('') + '</div>';
      };
      var edges = function (e) { return '<div class="leg-edges">' + e.map(function (v) { return '<span>' + fmtNum(v) + '</span>'; }).join('') + '</div>'; };
      html += '<div class="leg-row"><span class="leg-title">Terrain km²/cell</span><div style="flex:1">' + bar(TERRAIN_RAMP, tBins.edges) + edges(tBins.edges) + '</div></div>';
      html += '<div class="leg-row"><span class="leg-title">' + (lsMode === 'prop' ? 'Slides/1000 km²' : 'Slides/cell') + '</span><div style="flex:1">' +
              bar(LS_RAMP, lBins.edges, LS_CATCH) + edges(lBins.edges.slice(1)) + '</div></div>';
    }
    var c = LSSample.get();
    if (c) {
      var s = 'Sample ' + c.r + ' km at ' + c.lat.toFixed(3) + ' N ' + (-c.lon).toFixed(3) + ' W';
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
  function wireBrush() {
    var sx0 = 0, sy0 = 0;
    function local(e) { var r = el.svg.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; }
    LSTools.drag(el.svg, {
      onStart: function (e) { if (!P) return false; var p = local(e); sx0 = p.x; sy0 = p.y; },
      onMove: function (e) {
        var p = local(e), dr = el.svg.querySelector('#an-drag'); if (!dr) return;
        dr.setAttribute('x', Math.min(sx0, p.x)); dr.setAttribute('y', Math.min(sy0, p.y));
        dr.setAttribute('width', Math.abs(p.x - sx0)); dr.setAttribute('height', Math.abs(p.y - sy0));
        dr.setAttribute('display', 'block');
      },
      onEnd: function (e, info) {
        var dr = el.svg.querySelector('#an-drag'); if (dr) dr.setAttribute('display', 'none');
        if (info.cancelled || !e || !P) return;
        var p = local(e);
        if (Math.abs(p.x - sx0) < 4 && Math.abs(p.y - sy0) < 4) return;   // a click, not a box
        var vx = function (px) { return P.qx.min + (px - P.d.x0) / P.d.w * (P.qx.max - P.qx.min); };
        var vy = function (py) { return P.qy.min + (P.d.y0 + P.d.h - py) / P.d.h * (P.qy.max - P.qy.min); };
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
  function nearest(e) {
    if (!P) return null;
    var r = el.svg.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top, best = null, bd = 64;
    P.pts.forEach(function (p) { var dd = (p.x - x) * (p.x - x) + (p.y - y) * (p.y - y); if (dd < bd) { bd = dd; best = p; } });
    return best;
  }
  function onHover(e) {
    if (!P) return;
    var r = el.svg.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top, html = null;
    if (lsMode === 'points') {
      var p = nearest(e);
      if (p) html = '<b>' + esc(p.name || ('#' + p.id)) + '</b><br>' + P.qx.label + ' ' + fmtV(P.qx, p.vx) + ' · ' + P.qy.label + ' ' + fmtV(P.qy, p.vy);
    } else if (x >= P.d.x0 && x <= P.d.x0 + P.d.w && y >= P.d.y0 && y <= P.d.y0 + P.d.h) {
      var xi = Math.min(P.qx.bins - 1, Math.floor((x - P.d.x0) / P.cw)), yi = Math.min(P.qy.bins - 1, Math.floor((P.d.y0 + P.d.h - y) / P.ch));
      var tc = dens ? dens.grid[yi * P.qx.bins + xi] || 0 : 0, lc = P.lsGrid[yi * P.qx.bins + xi] || 0;
      var xv = P.qx.classes ? P.qx.classes[xi] : P.qx.min + (xi + 0.5) * (P.qx.max - P.qx.min) / P.qx.bins;
      var yv = P.qy.classes ? P.qy.classes[yi] : P.qy.min + (yi + 0.5) * (P.qy.max - P.qy.min) / P.qy.bins;
      html = P.qx.label + ' ' + fmtV(P.qx, xv) + ', ' + P.qy.label + ' ' + fmtV(P.qy, yv) + ' · ' + fmtNum(tc) + ' km² · ' +
             lc + ' slide' + (lc === 1 ? '' : 's') + (lc && tc ? ' (' + fmtNum(lc / tc * 1000) + '/1000 km²)' : '');
    }
    if (!html) { el.tip.style.display = 'none'; return; }
    el.tip.innerHTML = html; el.tip.style.display = 'block';
    var br = el.body.getBoundingClientRect();
    el.tip.style.left = Math.min(br.width - 200, e.clientX - br.left + 12) + 'px';
    el.tip.style.top = (e.clientY - br.top + 12) + 'px';
  }
  function onClick(e) { if (lsMode !== 'points') return; var p = nearest(e); if (p && o.openLandslide) o.openLandslide(p.id, p.ft); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function svgEl(n, a) { var e = document.createElementNS('http://www.w3.org/2000/svg', n); for (var k in a) e.setAttribute(k, a[k]); return e; }
  function txt(svg, s, x, y, anchor, fill) {
    var t = svgEl('text', { x: x, y: y, 'text-anchor': anchor || 'start', 'font-size': 9, fill: fill || '#888' });
    t.textContent = s; svg.appendChild(t); return t;
  }

  // ---- URL state (map.js _VIEW_PARAMS row 'ax') ------------------------------------------------
  function state() {
    if (!X || !Y) return null;
    return { x: X, y: Y, brush: brush, ls: lsMode === 'points' ? null : lsMode };
  }
  function setState(s) {
    if (!el.panel) { pendingState = s; return; }
    if (s.ls && (s.ls === 'count' || s.ls === 'prop')) { lsMode = s.ls; if (el.ls) el.ls.value = lsMode; }
    else if (s.ls === null) { lsMode = 'points'; if (el.ls) el.ls.value = lsMode; }
    if (s.x && s.y && s.x !== s.y) setAxes(s.x, s.y, { push: false });
    if (s.brush !== undefined) { brush = s.brush; syncSlidersToBrush(); }
    draw();
  }
  function keys() { return X && Y ? [X, Y] : null; }

  return { init: init, panel: panel, openWith: openWith, onOverlays: onOverlays, onFilter: onFilter,
           redraw: draw, state: state, setState: setState, keys: keys };
})();
