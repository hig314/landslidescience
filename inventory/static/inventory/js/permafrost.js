/* Permafrost analysis: Obu x Gruber joint density with landslides as points,
 * and a patch sampler.
 *
 * Panel A (#pf-float, Analysis tab -> "Permafrost: Obu x Gruber"): the
 * terrain joint density of two permafrost products (pf_density.json, built
 * by tools/permafrost/export_web.py) as a log-scaled backdrop, with every
 * landslide drawn as a point at its own sampled values (pf_values.json).
 * Pairs: Gruber PZI x Obu probability, Gruber MAAT x Obu MAGT; resolution:
 * the published 1 km products or our 60 m downscale. Same idea as the
 * susceptibility scatter: "landslides are here MORE than terrain is".
 *
 * Sample patch: arm the tool (panel button or the toolbar), click the map,
 * and a circle of the chosen radius is read two ways -- the published 1 km
 * cells from api/permafrost/patch/ (inventory/permafrost.py, memory-mapped
 * grids) and the 60 m downscale from value-encoded PMTiles
 * (/overlays/pfv_<field>.pmtiles, tools/permafrost/bake_values.py; the
 * colour overlays are lossy WebP and cannot be read back). Panel B
 * (#pf-patch-float) draws the four plots of tools/permafrost/patch.py:
 * MAAT and PZI against elevation (Gruber cells, 60 m cloud, the kernel lapse
 * and the continuous curve), MAGT against elevation coloured by northness
 * (Obu cells, hollow where Obu had no value and the kernel fit stands in),
 * and probability. The patch's points also light up on Panel A. The circle
 * rides the URL as pp=lat,lon,r (map.js _VIEW_PARAMS).
 *
 * Global `window.LSPermafrost`; map.js calls init() once the map exists.
 */
window.LSPermafrost = (function () {
  'use strict';

  var PAIRS = {
    frac: { x: 'pzi', y: 'prob', xl: 'Gruber PZI', yl: 'Obu permafrost probability', step: 0.2, fmt: function (v) { return v.toFixed(2); } },
    temp: { x: 'maat', y: 'magt', xl: 'Gruber MAAT (°C)', yl: 'Obu MAGT (°C)', step: 4, fmt: function (v) { return v.toFixed(1); } }
  };
  // terrain-RGB scale per value archive (bake_values.py)
  // Northness is not an archive: at 60 m it is high-frequency by nature and
  // did not compress (1 GB at 0.01 precision), so it is derived here from the
  // elevation tile with a 3x3 gradient -- it only colours points.
  var SCALE = { elev60: 0.1, maat60: 10, pzi60: 1000, magt60: 10, prob60: 1000 };
  var Z = 10;                               // the value archives' one zoom
  var COLOR = { slow: '#d6604d', catastrophic: '#3f67b1', patch: '#ff8f00', cloud: 'rgba(255,143,0,0.35)' };
  var SRC = 'pf-patch';

  var map = null, features = function () { return null; }, openLandslide = function () {};
  var onChange = function () {}, makePanel = null, staticBase = '', dataV = '';
  var density = null, values = null;
  var pair = 'frac', res = '1km';
  var el = {}, panelA = null, panelB = null;
  var patch = null;            // {lon, lat, r, cells, p60: {elev, north, maat, pzi, magt, prob}, lapse, curve}
  var pending = null;          // a circle from the URL waiting for init
  var armed = false, seq = 0;
  var archives = {}, tileCache = {};

  // ---- init ----------------------------------------------------------------
  function init(opts) {
    map = opts.map; features = opts.features || features; openLandslide = opts.openLandslide || openLandslide;
    onChange = opts.onChange || onChange; makePanel = opts.makePanel; staticBase = opts.staticBase || ''; dataV = opts.dataV || '';
    var A = document.getElementById('pf-float'), B = document.getElementById('pf-patch-float');
    if (!A || !B) return;
    el.A = A; el.B = B;
    el.heat = A.querySelector('#pf-heat'); el.svg = A.querySelector('#pf-svg'); el.body = A.querySelector('#pf-body');
    el.pair = A.querySelector('#pf-pair'); el.res = A.querySelector('#pf-res'); el.count = A.querySelector('#pf-count');
    el.radius = A.querySelector('#pf-radius'); el.sample = A.querySelector('#pf-sample');
    el.tip = document.createElement('div'); el.tip.className = 'pf-tip'; el.body.appendChild(el.tip);
    el.Bcanvas = B.querySelector('#pf-patch-canvas'); el.Btitle = B.querySelector('#pf-patch-title'); el.Bbody = B.querySelector('#pf-patch-body');
    panelA = makePanel(A, { handle: A.querySelector('.pf-header'), toggle: document.getElementById('pf-toggle'),
                            close: A.querySelector('#pf-close'), onResize: drawA });
    panelB = makePanel(B, { handle: B.querySelector('.pf-header'), close: B.querySelector('#pf-patch-close'), onResize: drawB });
    el.pair.addEventListener('change', function () { pair = el.pair.value; drawA(); });
    el.res.addEventListener('change', function () { res = el.res.value; drawA(); });
    el.sample.addEventListener('click', function (e) { e.preventDefault(); arm(); });
    B.querySelector('#pf-patch-csv').addEventListener('click', downloadCsv);
    B.querySelector('#pf-patch-png').addEventListener('click', downloadPng);
    B.querySelector('#pf-patch-clear').addEventListener('click', function (e) { e.preventDefault(); clearPatch(); });
    el.svg.addEventListener('mousemove', onHover); el.svg.addEventListener('mouseleave', function () { el.tip.style.display = 'none'; });
    el.svg.addEventListener('click', onPointClick);

    fetch(staticBase + 'inventory/pf_density.json?v=' + dataV).then(function (r) { return r.json(); })
      .then(function (j) { density = j; drawA(); }).catch(function () {});
    fetch(staticBase + 'inventory/pf_values.json?v=' + dataV).then(function (r) { return r.json(); })
      .then(function (j) { values = j; drawA(); }).catch(function () {});

    // Registered as a mode so LSTools owns the claim and Escape, but with no
    // toolbar button: the sampler belongs to this panel (Hig, 2026-10-09; a
    // map-wide sample tool is the generalisation sketched in
    // ANALYSIS_TOOL_PLAN.md, not this one).
    LSTools.mode({
      id: 'pfpatch', order: 26, label: '◎', className: 'ls-tool-hidden',
      title: 'Permafrost patch: click the map to read the published 1 km cells and the 60 m downscale inside a circle (Esc exits)',
      onSelect: function () { if (LSTools.claim('pfpatch')) setArmed(true); },
      onRelease: function () { LSTools.release('pfpatch'); setArmed(false); },
      cancel: function () { LSTools.release('pfpatch'); setArmed(false); }
    });
    LSTools.clicks.register({ id: 'pfpatch', kind: 'action', holders: ['pfpatch'],
                              handler: function (_f, e) { onMapClick(e); } });
    map.on('style.load', ensureLayers);
    if (map.isStyleLoaded()) ensureLayers();
    if (pending) { var p = pending; pending = null; runPatch(p.lon, p.lat, p.r); }
  }
  function panel() { return panelA; }

  // ---- map circle ------------------------------------------------------------
  function fc(f) { return { type: 'FeatureCollection', features: f }; }
  function ensureLayers() {
    if (!map.getSource(SRC)) map.addSource(SRC, { type: 'geojson', data: fc([]) });
    if (!map.getLayer(SRC + '-fill')) {
      map.addLayer({ id: SRC + '-fill', type: 'fill', source: SRC, paint: { 'fill-color': COLOR.patch, 'fill-opacity': 0.08 } });
      map.addLayer({ id: SRC + '-line', type: 'line', source: SRC, paint: { 'line-color': COLOR.patch, 'line-width': 2, 'line-dasharray': [3, 2] } });
    }
    renderCircle();
  }
  function renderCircle() {
    if (!map.getSource(SRC)) return;
    map.getSource(SRC).setData(fc(patch ? [turf.circle([patch.lon, patch.lat], patch.r, { units: 'kilometers', steps: 72 })] : []));
  }

  // ---- the sampler -----------------------------------------------------------
  function setArmed(on) {
    armed = on;
    if (on) LSTools.cursor.set('tool', 'crosshair'); else LSTools.cursor.clear('tool');
    if (el.sample) el.sample.classList.toggle('active', on);
  }
  function arm() { if (LSTools.claim('pfpatch')) setArmed(true); }
  function onMapClick(e) {
    if (!armed) return;
    var r = parseFloat(el.radius && el.radius.value) || 10;
    runPatch(e.lngLat.lng, e.lngLat.lat, Math.max(0.5, Math.min(30, r)));
    LSTools.release('pfpatch'); setArmed(false);
  }
  function runPatch(lon, lat, r) {
    var my = ++seq;
    patch = { lon: lon, lat: lat, r: r, cells: null, p60: null };
    renderCircle();
    if (el.Btitle) el.Btitle.textContent = 'Reading ' + r + ' km patch at ' + lat.toFixed(3) + ' N ' + (-lon).toFixed(3) + ' W …';
    if (panelB) panelB.open();
    window.LSTrack && LSTrack.event('map_tool', { tool: 'pf_patch' });
    onChange();
    var api = fetch('api/permafrost/patch/?lon=' + lon + '&lat=' + lat + '&r=' + r).then(function (res) { return res.json(); });
    Promise.all([api, read60(lon, lat, r)]).then(function (out) {
      if (my !== seq) return;
      var j = out[0];
      if (j.error) { if (el.Btitle) el.Btitle.textContent = j.error; return; }
      patch.cells = j.cells; patch.lapse = j.lapse; patch.curve = j.curve; patch.p60 = out[1];
      if (el.Btitle) el.Btitle.textContent = r + ' km patch at ' + lat.toFixed(3) + ' N ' + (-lon).toFixed(3) + ' W — ' +
        j.cells.length + ' published cells, ' + out[1].n + ' cells at 60 m';
      drawB(); drawA();
    }).catch(function (err) { if (el.Btitle) el.Btitle.textContent = 'Patch read failed: ' + err; });
  }
  function clearPatch() {
    patch = null; renderCircle(); if (panelB) panelB.close(); drawA(); onChange();
  }

  // ---- reading the 60 m value archives ----------------------------------------
  function archive(f) {
    if (!archives[f]) archives[f] = new pmtiles.PMTiles(location.origin + '/overlays/pfv_' + f + '.pmtiles');
    return archives[f];
  }
  function tileXY(lon, lat, z) {
    var n = Math.pow(2, z), la = lat * Math.PI / 180;
    return [(lon + 180) / 360 * n, (1 - Math.log(Math.tan(la) + 1 / Math.cos(la)) / Math.PI) / 2 * n];
  }
  function decode(bitmap, scale) {
    var c = document.createElement('canvas'); c.width = bitmap.width; c.height = bitmap.height;
    var ctx = c.getContext('2d'); ctx.drawImage(bitmap, 0, 0);
    var img = ctx.getImageData(0, 0, c.width, c.height).data, out = new Float32Array(c.width * c.height);
    for (var i = 0, j = 0; i < img.length; i += 4, j++) {
      out[j] = img[i + 3] < 255 ? NaN : (-10000 + (img[i] * 65536 + img[i + 1] * 256 + img[i + 2]) * 0.1) / scale;
    }
    return { w: c.width, h: c.height, data: out };
  }
  function tile(f, x, y) {
    var key = f + '/' + x + '/' + y;
    if (!tileCache[key]) {
      tileCache[key] = archive(f).getZxy(Z, x, y).then(function (r) {
        return (r && r.data) ? createImageBitmap(new Blob([r.data])).then(function (bm) { return decode(bm, SCALE[f]); }) : null;
      }).catch(function () { return null; });
    }
    return tileCache[key];
  }
  // Every 60 m cell inside the circle, as parallel arrays; subsampled to
  // keep the plots and Panel A light.
  // cos(aspect) * sin(slope) for every pixel of an elevation tile (Horn
  // gradient, one-sided at the tile edge), +1 a steep north face.
  function northness(tile, lat) {
    var w = tile.w, h = tile.h, d = tile.data, out = new Float32Array(w * h);
    var px = 40075016.686 * Math.cos(lat * Math.PI / 180) / (w * Math.pow(2, Z));
    for (var y = 0; y < h; y++) for (var x = 0; x < w; x++) {
      var x0 = Math.max(0, x - 1), x1 = Math.min(w - 1, x + 1), y0 = Math.max(0, y - 1), y1 = Math.min(h - 1, y + 1);
      var dzdx = (d[y * w + x1] - d[y * w + x0]) / ((x1 - x0) * px);
      var dzdy = (d[y0 * w + x] - d[y1 * w + x]) / ((y1 - y0) * px);     // north up
      if (isNaN(dzdx) || isNaN(dzdy)) { out[y * w + x] = NaN; continue; }
      var slope = Math.atan(Math.hypot(dzdx, dzdy));
      var aspect = Math.atan2(-dzdx, dzdy);            // 0 = north-facing, measured from north
      out[y * w + x] = Math.cos(aspect) * Math.sin(slope);
    }
    return { w: w, h: h, data: out };
  }
  function read60(lon, lat, r) {
    var n = Math.pow(2, Z), fields = Object.keys(SCALE);
    var dlat = r / 111.32, dlon = r / (111.32 * Math.cos(lat * Math.PI / 180));
    var t0 = tileXY(lon - dlon, lat + dlat, Z), t1 = tileXY(lon + dlon, lat - dlat, Z);
    var jobs = [];
    for (var tx = Math.floor(t0[0]); tx <= Math.floor(t1[0]); tx++)
      for (var ty = Math.floor(t0[1]); ty <= Math.floor(t1[1]); ty++)
        (function (tx, ty) {
          jobs.push(Promise.all(fields.map(function (f) { return tile(f, tx, ty); })).then(function (ts) { return { tx: tx, ty: ty, ts: ts }; }));
        })(tx, ty);
    return Promise.all(jobs).then(function (tiles) {
      var out = {}; fields.forEach(function (f) { out[f] = []; }); out.north60 = []; out.lon = []; out.lat = [];
      var cosl = Math.cos(lat * Math.PI / 180), rm = r * 1000;
      tiles.forEach(function (t) {
        if (!t.ts[0]) return;
        var w = t.ts[0].w, h = t.ts[0].h, nt = northness(t.ts[0], lat);
        for (var py = 0; py < h; py++) {
          var ylat = (2 * Math.atan(Math.exp(Math.PI * (1 - 2 * (t.ty + (py + 0.5) / h) / n))) - Math.PI / 2) * 180 / Math.PI;
          var dy = (ylat - lat) * 111320;
          if (Math.abs(dy) > rm) continue;
          for (var px = 0; px < w; px++) {
            var xlon = (t.tx + (px + 0.5) / w) / n * 360 - 180;
            var dx = (xlon - lon) * 111320 * cosl;
            if (dx * dx + dy * dy > rm * rm) continue;
            var i = py * w + px, e = t.ts[0].data[i];
            if (isNaN(e)) continue;
            fields.forEach(function (f, k) { out[f].push(t.ts[k] ? t.ts[k].data[i] : NaN); });
            out.north60.push(nt.data[i]); out.lon.push(xlon); out.lat.push(ylat);
          }
        }
      });
      out.n = out.lon.length;
      var keep = 9000;
      if (out.n > keep) {
        var step = out.n / keep, idx = [];
        for (var s = 0; s < out.n; s += step) idx.push(Math.floor(s));
        Object.keys(out).forEach(function (k) { if (Array.isArray(out[k])) out[k] = idx.map(function (i) { return out[k][i]; }); });
      }
      return out;
    });
  }

  // ---- Panel A: density + landslides -------------------------------------------
  function pairKey() { var p = PAIRS[pair]; return res === '60m' ? p.x + '60|' + p.y + '60' : p.x + '|' + p.y; }
  function dims() {
    var W = el.svg.clientWidth, H = el.svg.clientHeight;
    return { W: W, H: H, x0: 38, y0: 8, w: W - 48, h: H - 34 };
  }
  var A = null;   // current plot: {d, ax, pts: [{x,y,id,name,ft}]}
  function drawA() {
    if (!el.svg || !panelA || !panelA.isOpen()) return;
    var d = dims(); if (d.w <= 0 || d.h <= 0) return;
    var p = PAIRS[pair], key = pairKey(), dens = density && density.pairs[key];
    var ax = dens ? { xmin: dens.xmin, xmax: dens.xmax, ymin: dens.ymin, ymax: dens.ymax }
                  : (pair === 'frac' ? { xmin: 0, xmax: 1, ymin: 0, ymax: 1 } : { xmin: -24, xmax: 8, ymin: -16, ymax: 8 });
    var sx = function (v) { return d.x0 + (v - ax.xmin) / (ax.xmax - ax.xmin) * d.w; };
    var sy = function (v) { return d.y0 + d.h - (v - ax.ymin) / (ax.ymax - ax.ymin) * d.h; };
    // heat
    var cv = el.heat; cv.width = d.W; cv.height = d.H;
    var ctx = cv.getContext('2d'); ctx.clearRect(0, 0, d.W, d.H);
    if (dens) {
      var cw = d.w / dens.xsize, ch = d.h / dens.ysize, lmax = Math.log(dens.max + 1);
      for (var yi = 0; yi < dens.ysize; yi++) for (var xi = 0; xi < dens.xsize; xi++) {
        var v = dens.grid[yi * dens.xsize + xi]; if (!v) continue;
        var t = Math.log(v + 1) / lmax;
        ctx.fillStyle = blues(t);
        ctx.fillRect(d.x0 + xi * cw, d.y0 + d.h - (yi + 1) * ch, cw + 0.5, ch + 0.5);
      }
    }
    // the patch, on this plane
    if (patch && patch.cells) {
      ctx.fillStyle = COLOR.cloud;
      if (res === '60m' && patch.p60) {
        var X = patch.p60[p.x + '60'], Y = patch.p60[p.y + '60'];
        for (var i = 0; i < X.length; i++) if (!isNaN(X[i]) && !isNaN(Y[i])) ctx.fillRect(sx(X[i]) - 1, sy(Y[i]) - 1, 2, 2);
      } else {
        patch.cells.forEach(function (c) {
          var xv = c[p.x], yv = pair === 'temp' ? (c.magt != null ? c.magt : c.magt_filled) : c[p.y];
          if (xv == null || yv == null) return;
          ctx.beginPath(); ctx.arc(sx(xv), sy(yv), 2.5, 0, 6.283); ctx.fill();
        });
      }
    }
    // svg: axes + points
    var svg = el.svg; while (svg.firstChild) svg.removeChild(svg.firstChild);
    svg.appendChild(svgEl('rect', { x: d.x0, y: d.y0, width: d.w, height: d.h, fill: 'none', stroke: '#ddd' }));
    for (var tv = ax.xmin; tv <= ax.xmax + 1e-9; tv += p.step) {
      svg.appendChild(svgEl('line', { x1: sx(tv), y1: d.y0 + d.h, x2: sx(tv), y2: d.y0 + d.h + 3, stroke: '#bbb' }));
      txt(svg, p.fmt(tv), sx(tv), d.y0 + d.h + 13, 'middle');
    }
    for (var tv2 = ax.ymin; tv2 <= ax.ymax + 1e-9; tv2 += p.step) {
      svg.appendChild(svgEl('line', { x1: d.x0 - 3, y1: sy(tv2), x2: d.x0, y2: sy(tv2), stroke: '#bbb' }));
      txt(svg, p.fmt(tv2), d.x0 - 5, sy(tv2) + 3, 'end');
    }
    txt(svg, p.xl + (res === '60m' ? ', 60 m' : ', 1 km') + ' →', d.x0 + d.w / 2, d.H - 2, 'middle', '#666');
    var yl = txt(svg, p.yl + ' →', 10, d.y0 + d.h / 2, 'middle', '#666');
    yl.setAttribute('transform', 'rotate(-90 10 ' + (d.y0 + d.h / 2) + ')');
    var pts = [], n = 0, fd = features();
    var kx = res === '60m' ? p.x + '60' : p.x, ky = res === '60m' ? p.y + '60' : p.y;
    if (values && fd && fd.features) {
      fd.features.forEach(function (ft) {
        var id = ft.properties.landslide_id != null ? ft.properties.landslide_id : ft.properties.id;
        var v = values[String(id)]; if (!v || v[kx] == null || v[ky] == null) return;
        var type = (ft.properties.landslide_type || '').toLowerCase().indexOf('cat') === 0 ? 'catastrophic' : 'slow';
        var c = svgEl('circle', { cx: sx(v[kx]), cy: sy(v[ky]), r: 3, fill: COLOR[type], 'fill-opacity': 0.8, stroke: '#fff', 'stroke-width': 0.6 });
        svg.appendChild(c); n++;
        pts.push({ x: sx(v[kx]), y: sy(v[ky]), id: id, name: ft.properties.unique_name, ft: ft, vx: v[kx], vy: v[ky] });
      });
    }
    A = { d: d, ax: ax, pts: pts, p: p };
    if (el.count) el.count.textContent = n ? n + ' landslides' : (values ? 'no values' : 'loading…');
  }
  function blues(t) {   // white -> #08306b, perceptual-ish
    var s = [[247, 251, 255], [198, 219, 239], [107, 174, 214], [33, 113, 181], [8, 48, 107]];
    var k = Math.min(3.999, t * 4), i = Math.floor(k), f = k - i;
    var a = s[i], b = s[i + 1];
    return 'rgb(' + Math.round(a[0] + (b[0] - a[0]) * f) + ',' + Math.round(a[1] + (b[1] - a[1]) * f) + ',' + Math.round(a[2] + (b[2] - a[2]) * f) + ')';
  }
  function nearest(e) {
    if (!A) return null;
    var r = el.svg.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top, best = null, bd = 64;
    A.pts.forEach(function (q) { var dd = (q.x - x) * (q.x - x) + (q.y - y) * (q.y - y); if (dd < bd) { bd = dd; best = q; } });
    return best;
  }
  function onHover(e) {
    var q = nearest(e);
    if (!q) { el.tip.style.display = 'none'; return; }
    el.tip.innerHTML = '<b>' + esc(q.name || ('#' + q.id)) + '</b><br>' + A.p.xl + ' ' + A.p.fmt(q.vx) + ' · ' + A.p.yl + ' ' + A.p.fmt(q.vy);
    el.tip.style.display = 'block';
    var r = el.body.getBoundingClientRect();
    el.tip.style.left = Math.min(r.width - 180, e.clientX - r.left + 12) + 'px';
    el.tip.style.top = (e.clientY - r.top + 12) + 'px';
  }
  function onPointClick(e) { var q = nearest(e); if (q) openLandslide(q.id, q.ft); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function svgEl(n, a) { var e = document.createElementNS('http://www.w3.org/2000/svg', n); for (var k in a) e.setAttribute(k, a[k]); return e; }
  function txt(svg, s, x, y, anchor, fill) {
    var t = svgEl('text', { x: x, y: y, 'text-anchor': anchor || 'start', 'font-size': 9, fill: fill || '#888' });
    t.textContent = s; svg.appendChild(t); return t;
  }

  // ---- Panel B: the patch plots ----------------------------------------------------
  function curveFn(c) {
    var p0 = Phi((c.T0 - c.T_zero) / c.sigma);
    return function (t) { return Math.max(0, Math.min(1, (Phi((c.T0 - t) / c.sigma) - p0) / (1 - p0))); };
  }
  function Phi(z) { return 0.5 * (1 + erf(z / Math.SQRT2)); }
  function erf(x) {   // Abramowitz-Stegun 7.1.26
    var s = x < 0 ? -1 : 1; x = Math.abs(x);
    var t = 1 / (1 + 0.3275911 * x);
    var y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
    return s * y;
  }
  function northColor(nv) {   // red south-facing .. blue north-facing
    var t = Math.max(-0.6, Math.min(0.6, nv || 0)) / 0.6;
    return t >= 0 ? 'rgb(' + Math.round(230 - 180 * t) + ',' + Math.round(230 - 120 * t) + ',' + Math.round(230 + 25 * t) + ')'
                  : 'rgb(' + Math.round(230 + 25 * -t) + ',' + Math.round(230 - 150 * -t) + ',' + Math.round(230 - 180 * -t) + ')';
  }
  function drawB() {
    if (!el.Bcanvas || !panelB || !panelB.isOpen() || !patch || !patch.cells) return;
    var cv = el.Bcanvas, W = el.Bbody.clientWidth, H = el.Bbody.clientHeight;
    if (!W || !H) return;
    var dpr = window.devicePixelRatio || 1;
    cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + 'px'; cv.style.height = H + 'px';
    var ctx = cv.getContext('2d'); ctx.scale(dpr, dpr); ctx.clearRect(0, 0, W, H);
    var cells = patch.cells, p60 = patch.p60 || {}, cur = curveFn(patch.curve), L = patch.lapse || {};
    var e60 = p60.elev60 || [];
    var emin = Infinity, emax = -Infinity;
    cells.forEach(function (c) { if (c.elev != null) { emin = Math.min(emin, c.elev); emax = Math.max(emax, c.elev); } });
    e60.forEach(function (v) { if (!isNaN(v)) { emin = Math.min(emin, v); emax = Math.max(emax, v); } });
    if (!isFinite(emin)) { emin = 0; emax = 1000; }
    var pad = (emax - emin) * 0.04; emin -= pad; emax += pad;
    var g1 = mean(cells.map(function (c) { return c.maat; })), ge = mean(cells.map(function (c) { return c.elev; }));
    var obuHas = cells.filter(function (c) { return !c.filled; });
    var o1 = mean(obuHas.map(function (c) { return c.magt; })), oe = mean(obuHas.map(function (c) { return c.elev; }));
    if (o1 == null) { o1 = mean(cells.map(function (c) { return c.magt_filled; })); oe = ge; }
    var panels = [
      { title: 'Gruber MAAT (°C)', lo: null, hi: null, cloud: p60.maat60, cell: function (c) { return c.maat; },
        line: g1 != null && L.gruber_b != null ? function (e) { return g1 + L.gruber_b * (e - ge); } : null,
        note: L.gruber_b != null ? 'kernel lapse ' + (L.gruber_b * 1000).toFixed(2) + ' °C/km' : '' },
      { title: 'Gruber PZI', lo: 0, hi: 1, cloud: p60.pzi60, cell: function (c) { return c.pzi; },
        line: g1 != null && L.gruber_b != null ? function (e) { return cur(g1 + L.gruber_b * (e - ge)); } : null,
        note: 'published cells (0.01 floor) vs the continuous curve' },
      { title: 'Obu MAGT (°C)', lo: null, hi: null, cloud: p60.magt60, cloudColor: p60.north60,
        cell: function (c) { return c.filled ? c.magt_filled : c.magt; }, hollow: function (c) { return c.filled; }, cellColor: function (c) { return c.north; },
        line: o1 != null && L.obu_b != null ? function (e) { return o1 + L.obu_b * (e - oe); } : null,
        note: (L.obu_b != null ? 'lapse ' + (L.obu_b * 1000).toFixed(2) + ' °C/km' : '') + (L.obu_c != null ? ', northness ' + L.obu_c.toFixed(2) + ' °C' : '') + ' · hollow = no Obu value, kernel fit' },
      { title: 'Obu permafrost probability', lo: 0, hi: 1, cloud: p60.prob60, cell: function (c) { return c.filled ? null : c.prob; }, note: '' }
    ];
    var cols = W >= 560 ? 2 : 1, rows = Math.ceil(4 / cols), pw = W / cols, ph = H / rows;
    panels.forEach(function (P, i) {
      var rx = (i % cols) * pw, ry = Math.floor(i / cols) * ph;
      plot(ctx, { x: rx + 44, y: ry + 18, w: pw - 56, h: ph - 44 }, P, e60, cells, emin, emax);
    });
  }
  function mean(a) { var s = 0, n = 0; a.forEach(function (v) { if (v != null && !isNaN(v)) { s += v; n++; } }); return n ? s / n : null; }
  function plot(ctx, r, P, e60, cells, emin, emax) {
    var vals = [];
    (P.cloud || []).forEach(function (v) { if (!isNaN(v)) vals.push(v); });
    cells.forEach(function (c) { var v = P.cell(c); if (v != null) vals.push(v); });
    var lo = P.lo != null ? P.lo : Math.min.apply(null, vals), hi = P.hi != null ? P.hi : Math.max.apply(null, vals);
    if (!isFinite(lo) || !isFinite(hi)) { lo = 0; hi = 1; }
    if (P.lo == null) { var pd = (hi - lo) * 0.06 || 0.5; lo -= pd; hi += pd; }
    var sx = function (e) { return r.x + (e - emin) / (emax - emin) * r.w; };
    var sy = function (v) { return r.y + r.h - (v - lo) / (hi - lo) * r.h; };
    ctx.strokeStyle = '#ddd'; ctx.lineWidth = 1; ctx.strokeRect(r.x, r.y, r.w, r.h);
    ctx.fillStyle = '#444'; ctx.font = '600 11px sans-serif'; ctx.textAlign = 'left'; ctx.fillText(P.title, r.x, r.y - 5);
    ctx.font = '9px sans-serif'; ctx.fillStyle = '#888'; ctx.textAlign = 'center';
    var es = niceStep(emax - emin, 5);
    for (var e = Math.ceil(emin / es) * es; e <= emax; e += es) { ctx.fillText(Math.round(e), sx(e), r.y + r.h + 11); }
    ctx.fillText('elevation (m)', r.x + r.w / 2, r.y + r.h + 22);
    ctx.textAlign = 'right';
    var vs = niceStep(hi - lo, 5);
    for (var v = Math.ceil(lo / vs) * vs; v <= hi + 1e-9; v += vs) { ctx.fillText(fmtTick(v, vs), r.x - 4, sy(v) + 3); }
    // 60 m cloud
    if (P.cloud) {
      for (var i = 0; i < P.cloud.length; i++) {
        var cv = P.cloud[i], ce = e60[i]; if (isNaN(cv) || isNaN(ce)) continue;
        ctx.fillStyle = P.cloudColor ? northColor(P.cloudColor[i]) : 'rgba(158,202,225,0.55)';
        ctx.fillRect(sx(ce) - 0.75, sy(cv) - 0.75, 1.5, 1.5);
      }
    }
    // the fitted line / curve
    if (P.line) {
      ctx.strokeStyle = '#222'; ctx.setLineDash([5, 3]); ctx.lineWidth = 1.2; ctx.beginPath();
      for (var k = 0; k <= 60; k++) { var ee = emin + (emax - emin) * k / 60, yy = P.line(ee); if (k === 0) ctx.moveTo(sx(ee), sy(yy)); else ctx.lineTo(sx(ee), sy(yy)); }
      ctx.stroke(); ctx.setLineDash([]);
    }
    // published cells
    cells.forEach(function (c) {
      var v = P.cell(c); if (v == null || c.elev == null) return;
      var hollow = P.hollow && P.hollow(c);
      ctx.beginPath(); ctx.arc(sx(c.elev), sy(v), 3, 0, 6.283);
      if (hollow) { ctx.strokeStyle = '#222'; ctx.lineWidth = 1; ctx.stroke(); }
      else { ctx.fillStyle = P.cellColor ? northColor(P.cellColor(c)) : '#08519c'; ctx.fill(); ctx.strokeStyle = '#222'; ctx.lineWidth = 0.5; ctx.stroke(); }
    });
    if (P.note) { ctx.fillStyle = '#777'; ctx.font = '9px sans-serif'; ctx.textAlign = 'right'; ctx.fillText(P.note, r.x + r.w - 2, r.y + 10); }
  }
  function niceStep(span, n) { var raw = span / n, p = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / p; return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p; }
  function fmtTick(v, step) { return step >= 1 ? String(Math.round(v)) : v.toFixed(step >= 0.1 ? 1 : 2); }

  // ---- downloads ---------------------------------------------------------------------
  function downloadCsv(e) {
    e.preventDefault(); if (!patch || !patch.cells) return;
    var rows = ['set,lon,lat,elev_m,northness,maat_c,pzi,magt_c,magt_std,prob,magt_inpainted'];
    patch.cells.forEach(function (c) {
      rows.push(['1km', c.lon, c.lat, c.elev, c.north, c.maat, c.pzi, c.magt, c.std, c.prob, c.filled ? c.magt_filled : ''].map(cs).join(','));
    });
    var p = patch.p60;
    if (p) for (var i = 0; i < p.lon.length; i++)
      rows.push(['60m', p.lon[i].toFixed(5), p.lat[i].toFixed(5), p.elev60[i], p.north60[i], p.maat60[i], p.pzi60[i], p.magt60[i], '', p.prob60[i], ''].map(cs).join(','));
    rows.push('# ' + patch.r + ' km patch at ' + patch.lat.toFixed(4) + ' N ' + patch.lon.toFixed(4) + ' E; 60 m set subsampled to ' + (p ? p.lon.length : 0) + ' of ' + (p ? p.n : 0) + ' cells; kernel lapse: ' + JSON.stringify(patch.lapse) + '; PZI curve: ' + JSON.stringify(patch.curve));
    save(new Blob([rows.join('\n')], { type: 'text/csv' }), 'permafrost_patch_' + patch.lat.toFixed(3) + 'N_' + (-patch.lon).toFixed(3) + 'W.csv');
  }
  function cs(v) { return (v == null || (typeof v === 'number' && isNaN(v))) ? '' : (typeof v === 'number' ? +v.toFixed(4) : v); }
  function downloadPng(e) {
    e.preventDefault(); if (!el.Bcanvas || !patch) return;
    el.Bcanvas.toBlob(function (b) { if (b) save(b, 'permafrost_patch_' + patch.lat.toFixed(3) + 'N_' + (-patch.lon).toFixed(3) + 'W.png'); });
  }
  function save(blob, name) {
    var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  // ---- URL state (map.js _VIEW_PARAMS row 'pp') ------------------------------------------
  function state() { return patch ? { lon: patch.lon, lat: patch.lat, r: patch.r } : null; }
  function setState(st) {
    if (!st) { if (patch) clearPatch(); return; }
    if (!map) { pending = st; return; }
    if (el.radius) el.radius.value = st.r;
    runPatch(st.lon, st.lat, st.r);
  }

  return { init: init, panel: panel, state: state, setState: setState, redraw: drawA };
})();
