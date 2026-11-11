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
 * SECTORS ARE CENTRED ON THE COMPASS POINTS, so "south" is a wedge straddling
 * 180°, not one starting there. The server bins aspect at 1.25° (?xbins=288):
 * every sector width offered here is an EVEN number of those bins, which is
 * what lets a sector straddle north.
 *
 * Polar cells grow with radius on the page, but each covers the same range of
 * aspect and slope, and colour is per cell, so a large outer cell is not more
 * ground. Rate is a ratio and is unaffected either way.
 *
 * URL: an=stereo; sn=<slope key>,<sectors>,<ring°>,<rim°>,<points|count|prop>;
 * sb=a0,a1,s0,s1 the brush (a0 > a1 crosses north); spt=0 paint off.
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
  var S = 'slope', sectors = 24, ringW = 5, rim = 90, lsMode = 'points', paintOn = true;
  var brush = null;                              // {a0, a1, s0, s1}; a0 > a1 crosses north
  var lsv = null, kidx = {}, outside = null;
  var dens = null, densCache = {}, densSeq = 0;
  var sub = null, subSeq = 0, subTimer = null;
  var P = null, pendingState = null, cubeError = null;

  // ---- init --------------------------------------------------------------------------
  function init(opts) {
    o = opts; map = opts.map; sh = LSAnalysis.shared;
    var panel = document.getElementById('sn-float'); if (!panel) return;
    ['slope', 'sectors', 'rings', 'rim', 'ls', 'sample', 'radius', 'limit', 'paint', 'count', 'clear', 'close', 'body', 'heat', 'svg', 'foot', 'note'].forEach(function (k) {
      el[k] = panel.querySelector('#sn-' + k);
    });
    el.tip = document.createElement('div'); el.tip.className = 'pf-tip'; el.body.appendChild(el.tip);
    fp = o.makePanel(panel, { handle: panel.querySelector('.an-header'), toggle: document.getElementById('sn-toggle'),
                              close: el.close, onResize: draw, onChange: onOpenClose });
    el.slope.addEventListener('change', function () { S = el.slope.value; brush = null; reload(); o.onChange(); });
    el.sectors.addEventListener('change', function () { sectors = +el.sectors.value; draw(); o.onChange(); });
    el.rings.addEventListener('change', function () { ringW = +el.rings.value; draw(); o.onChange(); });
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

  // ---- polar cells ----------------------------------------------------------------------
  // A sector is centred on its compass point, so the first runs from -w/2 to +w/2.
  function nRings() { return Math.ceil(60 / ringW); }
  function sectorOfBase(b) { var n = BASE / sectors; return Math.floor(((b + n / 2) % BASE) / n); }
  function merge(src, nx, ny) {
    var nr = nRings(), out = new Float64Array(nr * sectors);
    for (var yi = 0; yi < ny; yi++) {
      var ring = Math.min(nr - 1, Math.floor(yi / ringW));
      for (var xi = 0; xi < nx; xi++) out[ring * sectors + sectorOfBase(xi)] += src[yi * nx + xi];
    }
    return out;
  }
  function sectorSpan(i) { var w = 360 / sectors, c = i * w; return [(c - w / 2 + 360) % 360, (c + w / 2) % 360]; }
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
    var R = d.R, nr = nRings(), T = sh;
    var cv = el.heat, dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(d.W * dpr); cv.height = Math.round(d.H * dpr);
    var ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, d.W, d.H);

    var kind = subsetKind(), subOk = !kind || !!(sub && sub.idset);
    var pts = [], fd = o.features ? o.features() : null, lsg = new Float64Array(nr * sectors);
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
        var sec = sectorOfBase(Math.min(BASE - 1, Math.floor(az / BASE_DEG)));
        lsg[Math.min(nr - 1, Math.floor(Math.min(vs, 59.999) / ringW)) * sectors + sec]++;
        var xy = toXY(B, R, az, vs);
        var type = (ft.properties.landslide_type || '').toLowerCase().indexOf('cat') === 0 ? 'catastrophic' : 'slow';
        pts.push({ x: xy.x, y: xy.y, id: id, name: ft.properties.unique_name, ft: ft, az: az, s: vs, type: type, lon: c[0], lat: c[1] });
      });
    }
    var source = kind ? (sub && sub.xsize === BASE ? sub : null) : (dens && dens.xsize === BASE ? dens : null);
    var tg = source ? merge(source.grid, source.xsize, source.ysize) : null;
    var grid = lsMode !== 'points', i, ring, sec, span;

    // pane 0 the terrain; a faint copy under the landslides, as in the x-y panel
    var tMax = 0, tBins = null;
    if (tg) {
      for (i = 0; i < tg.length; i++) if (tg[i] > tMax) tMax = tg[i];
      tBins = T.logRangeBinner(1, Math.max(2, tMax), T.T_RAMP.length);
      for (ring = 0; ring < nr; ring++) for (sec = 0; sec < sectors; sec++) {
        var tv = tg[ring * sectors + sec]; if (!tv) continue;
        span = sectorSpan(sec);
        ctx.fillStyle = T.T_RAMP[Math.max(1, tBins.bin(tv)) - 1];
        wedge(ctx, d.panes[0], R, span[0], span[1], ring * ringW, (ring + 1) * ringW);
        ctx.globalAlpha = 0.28;
        wedge(ctx, d.panes[1], R, span[0], span[1], ring * ringW, (ring + 1) * ringW);
        ctx.globalAlpha = 1;
      }
    }
    // pane 1 the landslide grid
    var lBins = null, noTerrain = 0;
    if (grid) {
      var vals = new Float64Array(lsg.length), vmax = 0;
      for (i = 0; i < lsg.length; i++) {
        if (!lsg[i]) continue;
        var tc = tg ? tg[i] : 0, v = lsMode === 'prop' ? (tc > 0 ? lsg[i] / tc * 1000 : -1) : lsg[i];
        vals[i] = v; if (v > vmax) vmax = v;
      }
      lBins = lsMode === 'prop' ? T.logRangeBinner(T.RATE_LO, T.RATE_HI, T.LS_RAMP.length) : T.logCatchBinner(vmax, T.LS_RAMP.length);
      for (ring = 0; ring < nr; ring++) for (sec = 0; sec < sectors; sec++) {
        i = ring * sectors + sec; if (!vals[i]) continue;
        var b = vals[i] < 0 ? 0 : lBins.bin(vals[i]);
        if (vals[i] < 0) noTerrain++;
        ctx.fillStyle = b === 0 ? T.LS_CATCH : T.LS_RAMP[b - 1];
        span = sectorSpan(sec);
        wedge(ctx, B, R, span[0], span[1], ring * ringW, (ring + 1) * ringW);
      }
    }

    // svg: the net, labels, points, brush
    var svg = el.svg; while (svg.firstChild) svg.removeChild(svg.firstChild);
    var where = kind === 'sample' ? ' · in the sample' : kind ? ' · in the map view' : '';
    var titles = ['Terrain · km² per cell' + where,
                  'Landslides · ' + (lsMode === 'points' ? 'points' : lsMode === 'count' ? 'count per cell' : 'per 1000 km²') + where];
    d.panes.forEach(function (p, pi) { net(svg, p, R); txt(svg, titles[pi], p.cx, 11, 'middle', '#555'); });
    txt(svg, 'centre = flat, rim = ' + rim + '° · aspect from true north · click a cell for a brush, drag for any sector', d.W / 2, d.H - 4, 'middle', '#bbb');

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
    P = { d: d, pts: pts, drawn: drawn, tg: tg, lsg: lsg, nr: nr };
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
    foot(tBins, lBins, noTerrain, kind, source, pts.length);
    // The legend and notes just written can change the plot's height, and the
    // panel's ResizeObserver does not see that (the panel itself did not
    // resize). The canvas would then be stretched against the net: draw once
    // more at the real size. The second pass writes the same legend, so it stops.
    if (settled !== true && (el.svg.clientWidth !== d.W || el.svg.clientHeight !== d.H)) draw(true);
  }
  function net(svg, p, R) {
    var line = '#d4d4d4', step = rim >= 90 ? 30 : 15, s, a;
    for (s = step; s <= rim; s += step) {
      var r = R * s / rim;
      svg.appendChild(svgEl('circle', { cx: p.cx, cy: p.cy, r: r, fill: 'none', stroke: s === rim ? '#999' : line, 'pointer-events': 'none' }));
      var lab = txt(svg, s + '°', p.cx + 2, p.cy - r + 9, 'start', '#777');
      lab.setAttribute('paint-order', 'stroke'); lab.setAttribute('stroke', '#fff'); lab.setAttribute('stroke-width', 2.5);
    }
    for (a = 0; a < 180; a += 45) {
      var q = toXY(p, R, a, rim), q2 = toXY(p, R, a + 180, rim);
      svg.appendChild(svgEl('line', { x1: q.x, y1: q.y, x2: q2.x, y2: q2.y, stroke: line, 'pointer-events': 'none' }));
    }
    [['N', 0], ['E', 90], ['S', 180], ['W', 270]].forEach(function (t) {
      var at = { x: p.cx + (R + 9) * Math.sin(rad(t[1])), y: p.cy - (R + 9) * Math.cos(rad(t[1])) };
      var c = txt(svg, t[0], at.x, at.y + 3, 'middle', '#555'); c.setAttribute('font-weight', '600');
    });
  }
  function foot(tBins, lBins, noTerrain, kind, source, nLs) {
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
  function cellAt(az, s) {
    var w = 360 / sectors;
    return { sec: Math.floor(((az + w / 2) % 360) / w) % sectors, ring: Math.min(P.nr - 1, Math.floor(s / ringW)) };
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
          var cc = cellAt(start.az, start.s), span = sectorSpan(cc.sec);
          setBrush({ a0: span[0], a1: span[1], s0: cc.ring * ringW, s1: (cc.ring + 1) * ringW });
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
    else if (c && c.s < 60) {
      var cc = cellAt(c.az, c.s), i = cc.ring * sectors + cc.sec, span = sectorSpan(cc.sec);
      var tv = P.tg ? P.tg[i] || 0 : 0, lv = P.lsg[i] || 0;
      html = windName(cc.sec * 360 / sectors) + ' (' + deg(span[0]) + '–' + deg(span[1]) + '°) · slope ' + cc.ring * ringW + '–' + (cc.ring + 1) * ringW + '°<br>' +
             sh.fmtNum(tv) + ' km² · ' + lv + ' slide' + (lv === 1 ? '' : 's') + (lv && tv ? ' (' + sh.fmtNum(lv / tv * 1000) + '/1000 km²)' : lv ? ' (no sampled terrain)' : '');
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
  function state() { return { S: S, sectors: sectors, ring: ringW, rim: rim, ls: lsMode, brush: brush, paint: paintOn }; }
  function setState(s) {
    if (!el.svg) { pendingState = s; return; }
    if (S_LABEL[s.S]) { S = s.S; el.slope.value = S; }
    if ([144, 72, 48, 24, 12, 8, 4].indexOf(s.sectors) >= 0) { sectors = s.sectors; el.sectors.value = sectors; }
    if ([2, 5, 10, 15].indexOf(s.ring) >= 0) { ringW = s.ring; el.rings.value = ringW; }
    if (s.rim === 60 || s.rim === 90) { rim = s.rim; el.rim.value = rim; }
    if (['points', 'count', 'prop'].indexOf(s.ls) >= 0) { lsMode = s.ls; el.ls.value = lsMode; }
    if (s.paint !== undefined) { paintOn = s.paint !== false; el.paint.checked = paintOn; }
    if (s.brush !== undefined) brush = s.brush;
    if (fp && fp.isOpen()) reload();
  }

  return { init: init, panel: panel, redraw: draw, onFilter: draw, onLimitView: onLimitView, state: state, setState: setState };
})();
