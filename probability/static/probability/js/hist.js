/* hist.js: two distributions on one set of bins, and their ratio.
 * Global `PH.Hist`. Plain SVG, no library.
 *
 *   PH.Hist.draw(el, {edges, log, units, a, b, aLabel, bLabel, mark, sel}, onBrush)
 *
 * `a` and `b` are cell counts per bin (the reference terrain and a set of
 * landslides). Each is drawn as its SHARE per bin, so the two curves have the
 * same area whatever the two totals are. Under them, the frequency ratio
 * share(b) / share(a) on a log scale: 1 means landslides take that kind of
 * ground in proportion to how much of it there is. A ratio is drawn only
 * where both bins hold enough cells to mean something (MIN_CELLS).
 * The first and last bins also count every cell beyond the range; the
 * vertical scale ignores them, so they may be cut off at the top.
 * Drag across the plot to choose a range (onBrush([lo, hi])); double-click
 * clears it (onBrush(null)). `mark` draws a vertical line at one value.
 */
window.PH = window.PH || {};
PH.Hist = (function () {
  var W = 340, H = 190, M = {l: 34, r: 8, t: 8, b: 26}, H2 = 54, GAP = 8, MIN_CELLS = 20;
  var NS = 'http://www.w3.org/2000/svg';

  function el(tag, attrs, parent, text) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    if (parent) parent.appendChild(n);
    return n;
  }
  function fmt(v) {
    var a = Math.abs(v);
    if (a === 0) return '0';
    if (a >= 1000) return (v / 1000).toPrecision(2).replace(/\.0$/, '') + 'k';
    return String(+v.toPrecision(a < 1 ? 2 : 3));
  }
  function steps(share, x, y) {
    var d = '';
    for (var i = 0; i < share.length; i++) d += (i ? 'L' : 'M') + x(i) + ',' + y(share[i]) + 'L' + x(i + 1) + ',' + y(share[i]);
    return d;
  }

  function draw(host, o, onBrush) {
    host.innerHTML = '';
    var n = o.edges.length - 1, sa = 0, sb = 0, i;
    for (i = 0; i < n; i++) { sa += o.a[i]; sb += o.b[i]; }
    var A = o.a.map(function (c) { return sa ? c / sa : 0; }), B = o.b.map(function (c) { return sb ? c / sb : 0; });
    // The end bins also hold everything beyond the range, so they can tower over the rest.
    // Scale to the bins between them and let the end bins run off the top.
    var inner = A.slice(1, n - 1).concat(B.slice(1, n - 1));
    var top = Math.max.apply(null, inner.length ? inner : A.concat(B)) || 1;
    var pw = W - M.l - M.r, ph = H - M.t - M.b - H2 - GAP;
    var svg = el('svg', {viewBox: '0 0 ' + W + ' ' + H, width: '100%', role: 'img'}, host);
    var x = function (i) { return M.l + i / n * pw; };
    var y = function (v) { return M.t + ph - Math.min(1, v / top) * ph; };
    var tx = o.log ? Math.log10 : function (v) { return v; };
    var lo = tx(o.edges[0]), hi = tx(o.edges[n]);
    var xv = function (v) { return M.l + (tx(v) - lo) / (hi - lo) * pw; };
    var vx = function (px) { var t = lo + (px - M.l) / pw * (hi - lo); return o.log ? Math.pow(10, t) : t; };

    if (o.sel) el('rect', {x: xv(o.sel[0]), y: M.t, width: Math.max(1, xv(o.sel[1]) - xv(o.sel[0])), height: H - M.t - M.b, class: 'ph-sel'}, svg);
    el('path', {d: steps(A, x, y) + 'L' + x(n) + ',' + y(0) + 'L' + x(0) + ',' + y(0) + 'Z', class: 'ph-a'}, svg);
    el('path', {d: steps(B, x, y), class: 'ph-b'}, svg);
    el('line', {x1: M.l, x2: M.l + pw, y1: y(0), y2: y(0), class: 'ph-axis'}, svg);
    el('text', {x: M.l - 4, y: M.t + 8, class: 'ph-tick', 'text-anchor': 'end'}, svg, (top * 100).toPrecision(2) + '%');
    el('text', {x: 8, y: M.t + ph / 2, class: 'ph-tick', transform: 'rotate(-90 8 ' + (M.t + ph / 2) + ')', 'text-anchor': 'middle'}, svg, 'share of cells');

    // frequency ratio, log2 scale from 1/32 to 32
    var y0 = M.t + ph + GAP, R = 5;
    var yr = function (r) { return y0 + H2 / 2 - Math.max(-R, Math.min(R, Math.log2(r))) / R * (H2 / 2); };
    el('line', {x1: M.l, x2: M.l + pw, y1: yr(1), y2: yr(1), class: 'ph-axis'}, svg);
    [[32, '32×'], [1, '1×'], [1 / 32, '1/32']].forEach(function (t) {
      el('text', {x: M.l - 4, y: yr(t[0]) + 3, class: 'ph-tick', 'text-anchor': 'end'}, svg, t[1]);
    });
    for (i = 0; i < n; i++) {
      if (o.a[i] < MIN_CELLS || o.b[i] < MIN_CELLS) continue;
      var r = B[i] / A[i];
      el('rect', {x: x(i) + 0.5, width: Math.max(1, pw / n - 1), y: Math.min(yr(r), yr(1)), height: Math.max(1, Math.abs(yr(r) - yr(1))),
                  class: r >= 1 ? 'ph-fr-hi' : 'ph-fr-lo'}, svg);
    }
    var ticks = o.log ? [] : [0, 0.25, 0.5, 0.75, 1].map(function (t) { return o.edges[0] + t * (o.edges[n] - o.edges[0]); });
    if (o.log) for (var p = Math.ceil(lo); p <= Math.floor(hi); p++) ticks.push(Math.pow(10, p));
    ticks.forEach(function (v) {
      el('text', {x: xv(v), y: H - M.b + 12, class: 'ph-tick', 'text-anchor': 'middle'}, svg, fmt(v));
    });
    el('text', {x: M.l + pw / 2, y: H - 2, class: 'ph-tick', 'text-anchor': 'middle'}, svg, o.units);
    if (o.mark != null && isFinite(o.mark)) el('line', {x1: xv(o.mark), x2: xv(o.mark), y1: M.t, y2: H - M.b, class: 'ph-mark'}, svg);

    var hit = el('rect', {x: M.l, y: M.t, width: pw, height: H - M.t - M.b, fill: 'transparent', style: 'cursor:crosshair'}, svg);
    var drag = null, band = null;
    function px(ev) {
      var b = svg.getBoundingClientRect();
      return Math.max(M.l, Math.min(M.l + pw, (ev.clientX - b.left) / b.width * W));
    }
    hit.addEventListener('pointerdown', function (ev) {
      drag = px(ev); hit.setPointerCapture(ev.pointerId);
      band = el('rect', {y: M.t, height: H - M.t - M.b, class: 'ph-sel', x: drag, width: 0}, svg);
    });
    hit.addEventListener('pointermove', function (ev) {
      if (drag == null) return;
      var p = px(ev);
      band.setAttribute('x', Math.min(drag, p)); band.setAttribute('width', Math.abs(p - drag));
    });
    hit.addEventListener('pointerup', function (ev) {
      if (drag == null) return;
      var p = px(ev), a = Math.min(drag, p), b = Math.max(drag, p);
      drag = null;
      if (b - a < 3) { band.remove(); return; }
      onBrush([vx(a), vx(b)]);
    });
    hit.addEventListener('dblclick', function () { onBrush(null); });
  }

  return {draw: draw, fmt: fmt};
})();
