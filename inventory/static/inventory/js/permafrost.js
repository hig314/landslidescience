/* Permafrost patch detail: the four plots of tools/permafrost/patch.py for
 * the sample circle, in the browser.
 *
 * Opened from the analysis panel (analysis.js, "Permafrost detail") when
 * both axes are permafrost products and a sample circle exists. The circle
 * is LSSample's; this reads it two ways -- the published 1 km cells from
 * api/permafrost/patch/ (inventory/permafrost.py, memory-mapped grids, with
 * the kernel lapse and the PZI curve) and the 60 m downscale through
 * LSSample.fine() -- and draws: MAAT and PZI against elevation (Gruber
 * cells, the 60 m cloud, the kernel lapse and the continuous curve), MAGT
 * against elevation coloured by northness (Obu cells, hollow where Obu had
 * no value and the kernel fit stands in), and probability. CSV and PNG
 * downloads. Until 2026-10-10 this file also held the Obu x Gruber density
 * panel, which the analysis panel generalised.
 *
 * Global `window.LSPermafrost`; map.js calls init() once the map exists.
 */
window.LSPermafrost = (function () {
  'use strict';

  var FINE = ['elev60', 'north60', 'maat60', 'pzi60', 'magt60', 'prob60'];
  var map = null, apiBase = '', panelB = null, el = {};
  var patch = null, seq = 0;    // {lon, lat, r, cells, p60, lapse, curve}

  function init(opts) {
    map = opts.map; apiBase = opts.apiBase || '';
    var B = document.getElementById('pf-patch-float'); if (!B) return;
    el.B = B; el.canvas = B.querySelector('#pf-patch-canvas'); el.title = B.querySelector('#pf-patch-title'); el.body = B.querySelector('#pf-patch-body');
    panelB = opts.makePanel(B, { handle: B.querySelector('.pf-header'), close: B.querySelector('#pf-patch-close'), onResize: drawB });
    B.querySelector('#pf-patch-csv').addEventListener('click', downloadCsv);
    B.querySelector('#pf-patch-png').addEventListener('click', downloadPng);
    // The circle moved or went: redraw for it if open, close with it.
    LSSample.on(function (c) {
      if (!panelB || !panelB.isOpen()) return;
      if (c) show(c); else { patch = null; panelB.close(); }
    });
  }

  function show(c) {
    var my = ++seq;
    patch = { lon: c.lon, lat: c.lat, r: c.r, cells: null, p60: null };
    if (el.title) el.title.textContent = 'Reading ' + c.r + ' km patch at ' + c.lat.toFixed(3) + ' N ' + (-c.lon).toFixed(3) + ' W …';
    if (panelB) panelB.open();
    window.LSTrack && LSTrack.event('map_tool', { tool: 'pf_patch' });
    var api = fetch(apiBase + 'api/permafrost/patch/?lon=' + c.lon + '&lat=' + c.lat + '&r=' + c.r).then(function (res) { return res.json(); });
    Promise.all([api, LSSample.fine(FINE)]).then(function (out) {
      if (my !== seq) return;
      var j = out[0];
      if (j.error) { if (el.title) el.title.textContent = j.error; return; }
      patch.cells = j.cells; patch.lapse = j.lapse; patch.curve = j.curve; patch.p60 = out[1];
      if (el.title) el.title.textContent = c.r + ' km patch at ' + c.lat.toFixed(3) + ' N ' + (-c.lon).toFixed(3) + ' W — ' +
        j.cells.length + ' published cells, ' + (out[1] ? out[1].n : 0) + ' cells at 60 m';
      drawB();
    }).catch(function (err) { if (el.title) el.title.textContent = 'Patch read failed: ' + err; });
  }

  // ---- the plots -------------------------------------------------------------------------
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
  function northColor(nv) {   // red south-facing .. blue north-facing (+1 = north, as the rasters)
    var t = Math.max(-0.6, Math.min(0.6, nv || 0)) / 0.6;
    return t >= 0 ? 'rgb(' + Math.round(230 - 180 * t) + ',' + Math.round(230 - 120 * t) + ',' + Math.round(230 + 25 * t) + ')'
                  : 'rgb(' + Math.round(230 + 25 * -t) + ',' + Math.round(230 - 150 * -t) + ',' + Math.round(230 - 180 * -t) + ')';
  }
  function drawB() {
    if (!el.canvas || !panelB || !panelB.isOpen() || !patch || !patch.cells) return;
    var cv = el.canvas, W = el.body.clientWidth, H = el.body.clientHeight;
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
    if (P.cloud) {   // the 60 m cloud
      for (var i = 0; i < P.cloud.length; i++) {
        var cv = P.cloud[i], ce = e60[i]; if (isNaN(cv) || isNaN(ce)) continue;
        ctx.fillStyle = P.cloudColor ? northColor(P.cloudColor[i]) : 'rgba(158,202,225,0.55)';
        ctx.fillRect(sx(ce) - 0.75, sy(cv) - 0.75, 1.5, 1.5);
      }
    }
    if (P.line) {    // the fitted line / curve
      ctx.strokeStyle = '#222'; ctx.setLineDash([5, 3]); ctx.lineWidth = 1.2; ctx.beginPath();
      for (var k = 0; k <= 60; k++) { var ee = emin + (emax - emin) * k / 60, yy = P.line(ee); if (k === 0) ctx.moveTo(sx(ee), sy(yy)); else ctx.lineTo(sx(ee), sy(yy)); }
      ctx.stroke(); ctx.setLineDash([]);
    }
    cells.forEach(function (c) {   // the published cells
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

  // ---- downloads ---------------------------------------------------------------------------
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
    e.preventDefault(); if (!el.canvas || !patch) return;
    el.canvas.toBlob(function (b) { if (b) save(b, 'permafrost_patch_' + patch.lat.toFixed(3) + 'N_' + (-patch.lon).toFixed(3) + 'W.png'); });
  }
  function save(blob, name) {
    var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  return { init: init, show: show };
})();
