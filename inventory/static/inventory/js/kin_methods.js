/* kin_methods.js — the four interactive scenes on the InSAR kinematics
 * methods page. Built on kin3d.js (loaded first). Each scene owns a canvas,
 * some controls, and a readout element; controls mutate a small state object
 * and call scene.refresh().
 */
(function () {
  'use strict';
  if (!window.Kin3D) return;
  var K = window.Kin3D, V = K.v;
  var C = { asc: '#0072B2', desc: '#E69F00', motion: '#CC79A7', axis: '#000',
            blind: '#009E73', grey: '#8d9694', slope: 'rgba(140,150,148,0.20)' };

  function $(id) { return document.getElementById(id); }
  function fmt(x, n) { return (x >= 0 ? '+' : '') + x.toFixed(n === undefined ? 1 : n); }

  /* ===================================================================== */
  /* Scene A — what a radar pass actually measures                          */
  /* ===================================================================== */
  function sceneA() {
    var cv = $('scA'); if (!cv) return;
    var st = { e: 0, n: -6, u: -4, blind: 0 };
    var SC = 0.9;   // display scale: mm/yr -> scene units

    function motion() {
      var b = V.unit(V.cross(K.LOS.asc, K.LOS.desc));
      return V.add([st.e, st.n, st.u], V.mul(b, st.blind));
    }

    var scene = new K.Scene(cv, { scale: 13, yaw: -0.75, pitch: 0.38, build: build });

    function build() {
      var it = [];
      it = it.concat(K.grid(9, 3, 0));
      it = it.concat(K.axes(7));
      var u = motion(), us = V.mul(u, SC);
      // the two look directions, drawn as unit arrows from the ground point
      [['asc', C.asc, 'ascending'], ['desc', C.desc, 'descending']].forEach(function (r) {
        var l = K.LOS[r[0]];
        it.push({ t: 'arrow', a: [0, 0, 0], b: V.mul(l, 8), c: r[1], w: 2,
                  label: r[2] + ' satellite' });
        // projection of the motion onto this look direction
        var p = V.dot(u, l);
        it.push({ t: 'line', a: us, b: V.mul(l, p * SC), c: r[1], w: 1,
                  dash: [3, 3], alpha: 0.85 });
        it.push({ t: 'dot', a: V.mul(l, p * SC), r: 4.5, c: r[1] });
      });
      it.push({ t: 'arrow', a: [0, 0, 0], b: us, c: C.motion, w: 3, head: 11,
                label: 'true motion' });
      it.push({ t: 'dot', a: [0, 0, 0], r: 4, c: '#333' });
      if (Math.abs(st.blind) > 0.01) {
        var b = V.unit(V.cross(K.LOS.asc, K.LOS.desc));
        it.push({ t: 'line', a: V.mul([st.e, st.n, st.u], SC), b: us,
                  c: C.blind, w: 2.5, alpha: 0.9 });
      }
      return it;
    }

    function readout() {
      var u = motion();
      var a = V.dot(u, K.LOS.asc), d = V.dot(u, K.LOS.desc);
      $('roA').innerHTML =
        '<b>True motion</b> (E, N, Up) = (' + fmt(u[0]) + ', ' + fmt(u[1]) + ', ' + fmt(u[2]) +
        ') mm/yr &nbsp;&mdash;&nbsp; <b>three</b> numbers.<br>' +
        '<span style="color:' + C.asc + '"><b>Ascending reads ' + fmt(a, 2) + '</b></span> &nbsp;·&nbsp; ' +
        '<span style="color:' + C.desc + '"><b>Descending reads ' + fmt(d, 2) + '</b></span> mm/yr' +
        ' &nbsp;&mdash;&nbsp; <b>two</b> numbers. That is the whole measurement.';
    }
    function upd() { scene.refresh(); readout(); }
    ['e', 'n', 'u', 'blind'].forEach(function (k) {
      var el = $('scA_' + k); if (!el) return;
      el.addEventListener('input', function () {
        st[k] = +el.value;
        var lab = $('scA_' + k + '_v'); if (lab) lab.textContent = fmt(+el.value);
        upd();
      });
    });
    upd();
    window.addEventListener('resize', function () { scene.draw(); });
  }

  /* ===================================================================== */
  /* Scene B — rotation writes a gradient, translation writes an offset     */
  /* ===================================================================== */
  function sceneB() {
    var cv = $('scB'); if (!cv) return;
    var st = { mode: 'rot', omega: 30, trans: 8 };
    var SL = K.slope(25, 165, 5.2, 3.6);
    var SC = 0.16;                 // mm/yr -> scene units for the arrows
    var pts = [];
    for (var s = -4.2; s <= 4.21; s += 2.1)
      for (var t = -2.6; t <= 2.61; t += 2.6) pts.push([s, t]);

    // velocity of a point on the slope under the current state, in mm/yr
    function vel(s, t) {
      var r = SL.at(s, t);
      var out = [0, 0, 0];
      if (st.mode !== 'trans') {
        // rotation about the expected gravitational pole, Omega in urad/yr;
        // r is in scene units standing for ~100 m, so scale to mm/yr
        out = V.add(out, V.mul(V.cross(SL.pole, r), st.omega * 0.1));
      }
      if (st.mode !== 'rot') out = V.add(out, V.mul(SL.drop, st.trans));
      return out;
    }

    var scene = new K.Scene(cv, { scale: 26, yaw: -0.55, pitch: 0.42, build: build });

    function build() {
      var it = [];
      it.push({ t: 'poly', pts: SL.quad, fill: C.slope, c: '#9aa3a0', w: 1 });
      it.push({ t: 'arrow', a: SL.at(-4.6, 0), b: SL.at(4.6, 0), c: '#6f7a77',
                w: 1.2, head: 8, label: 'downslope' });
      // the expected gravitational pole: horizontal, across the slope
      it.push({ t: 'line', a: V.mul(SL.pole, -4.4), b: V.mul(SL.pole, 4.4),
                c: C.axis, w: 2.4, dash: [7, 4] });
      it.push({ t: 'text', a: V.mul(SL.pole, 4.6), s: 'rotation axis', c: C.axis,
                font: '600 12px system-ui, sans-serif' });
      pts.forEach(function (p) {
        var r = SL.at(p[0], p[1]), u = vel(p[0], p[1]);
        it.push({ t: 'dot', a: r, r: 2.6, c: '#5c6664' });
        if (V.norm(u) > 0.02) {
          it.push({ t: 'arrow', a: r, b: V.add(r, V.mul(u, SC)), c: C.motion,
                    w: 1.8, head: 7 });
        }
      });
      return it;
    }

    // the companion 2-D plot: what each track records along the slope
    function plot() {
      var c = $('scB_plot'); if (!c) return;
      var dpr = window.devicePixelRatio || 1;
      var W = c.clientWidth, H = c.clientHeight;
      if (c.width !== Math.round(W * dpr)) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
      var g = c.getContext('2d');
      g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
      var pad = 34, x0 = pad + 8, x1 = W - 10, y0 = 12, y1 = H - 26;
      var series = ['asc', 'desc'].map(function (k) {
        return { k: k, v: [-4.2, -2.1, 0, 2.1, 4.2].map(function (s) {
          return V.dot(vel(s, 0), K.LOS[k]); }) };
      });
      var m = 1e-9;
      series.forEach(function (S) { S.v.forEach(function (y) { m = Math.max(m, Math.abs(y)); }); });
      m = Math.max(m * 1.25, 1);
      function X(i) { return x0 + (x1 - x0) * i / 4; }
      function Y(y) { return (y0 + y1) / 2 - y / m * (y1 - y0) / 2; }
      g.strokeStyle = '#ddd'; g.lineWidth = 1;
      g.beginPath(); g.moveTo(x0, Y(0)); g.lineTo(x1, Y(0)); g.stroke();
      g.fillStyle = '#777'; g.font = '11px system-ui, sans-serif';
      g.fillText('head', x0 - 4, y1 + 16); g.textAlign = 'right';
      g.fillText('toe', x1, y1 + 16); g.textAlign = 'left';
      g.save(); g.translate(11, (y0 + y1) / 2); g.rotate(-Math.PI / 2);
      g.textAlign = 'center'; g.fillText('LOS rate (mm/yr)', 0, 0); g.restore();
      g.textAlign = 'left';
      series.forEach(function (S) {
        g.strokeStyle = C[S.k]; g.fillStyle = C[S.k]; g.lineWidth = 2.2;
        g.beginPath();
        S.v.forEach(function (y, i) { i ? g.lineTo(X(i), Y(y)) : g.moveTo(X(i), Y(y)); });
        g.stroke();
        S.v.forEach(function (y, i) {
          g.beginPath(); g.arc(X(i), Y(y), 3.6, 0, 6.2832); g.fill();
        });
      });
      var flat = st.mode === 'trans';
      $('roB').innerHTML = flat
        ? 'Pure <b>translation</b>: every point moves identically, so each track records the '
          + 'same value everywhere &mdash; two <b>flat</b> lines. An offset carries no information '
          + 'about position, so one track alone can never separate it into three components.'
        : (st.mode === 'rot'
          ? 'Pure <b>rotation</b>: the velocity grows with distance from the axis, so each track '
            + 'records a <b>tilted</b> line. The tilt is the rotation, and it survives in a '
            + '<i>single</i> track &mdash; which is why &Omega;(t) can span the whole record even '
            + 'where only one look direction exists.'
          : 'Both together: the rotation sets the <b>slope</b> of each line and the translation '
            + 'sets its <b>height</b>. They are separable within one track precisely because one '
            + 'varies with position and the other does not.');
    }

    function upd() { scene.refresh(); plot(); }
    ['omega', 'trans'].forEach(function (k) {
      var el = $('scB_' + k); if (!el) return;
      el.addEventListener('input', function () {
        st[k] = +el.value;
        var lab = $('scB_' + k + '_v'); if (lab) lab.textContent = el.value;
        upd();
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-scb-mode]'), function (b) {
      b.addEventListener('click', function () {
        st.mode = b.getAttribute('data-scb-mode');
        Array.prototype.forEach.call(document.querySelectorAll('[data-scb-mode]'), function (o) {
          o.classList.toggle('on', o === b);
        });
        upd();
      });
    });
    upd();
    window.addEventListener('resize', function () { scene.draw(); plot(); });
  }

  /* ===================================================================== */
  /* Scene C — a slump lowers its centre of mass while its toe rises        */
  /* ===================================================================== */
  function sceneC() {
    var cv = $('scC'); if (!cv) return;
    var st = { ang: 14 };
    var SL = K.slope(25, 165, 4.4, 2.8);

    // The rotation centre sits ABOVE the block, where the centre of curvature of
    // a circular failure surface would be. Placement matters and is checked, not
    // guessed: a block rotating about its own centroid does not lower its centre
    // of mass at all (nothing to demonstrate), and an axis placed upslope of the
    // block lifts the whole thing (head -0.61, toe +1.25, centre of mass +0.32 —
    // an anti-gravitational rotation). Directly overhead gives the real thing:
    // head -1.30, toe +0.55, centre of mass -0.38.
    var CEN = V.mul(SL.normal, 5);

    function rot(p, ang) {
      // Rodrigues rotation of p about the expected pole, through CEN
      var k = SL.pole, th = ang * Math.PI / 180;
      var c = Math.cos(th), s = Math.sin(th);
      var q = V.sub(p, CEN);
      var r = V.add(V.add(V.mul(q, c), V.mul(V.cross(k, q), s)),
                    V.mul(k, V.dot(k, q) * (1 - c)));
      return V.add(r, CEN);
    }

    var scene = new K.Scene(cv, { scale: 30, yaw: -1.15, pitch: 0.18, build: build });

    function build() {
      var it = [];
      it = it.concat(K.grid(7, 2.3, -2.6, '#e6e6e4'));
      // original block, and the same block rotated
      var q = SL.quad;
      it.push({ t: 'poly', pts: q, fill: 'rgba(140,150,148,0.14)', c: '#b3bab8', w: 1, dash: [4, 3] });
      var qr = q.map(function (p) { return rot(p, st.ang); });
      it.push({ t: 'poly', pts: qr, fill: 'rgba(0,114,178,0.20)', c: C.asc, w: 1.6 });
      // head and toe markers, before and after
      [['head', SL.at(-4.0, 0)], ['toe', SL.at(4.0, 0)]].forEach(function (m) {
        var p0 = m[1], p1 = rot(p0, st.ang);
        it.push({ t: 'dot', a: p0, r: 3.4, c: '#b3bab8' });
        it.push({ t: 'arrow', a: p0, b: p1, c: p1[2] > p0[2] ? '#009E73' : '#D55E00',
                  w: 2.6, head: 9, label: m[0] + (p1[2] > p0[2] ? ' rises' : ' drops') });
      });
      // centre of mass
      var c0 = [0, 0, 0], c1 = rot(c0, st.ang);
      it.push({ t: 'dot', a: c0, r: 5.5, c: '#b3bab8', edge: true });
      it.push({ t: 'dot', a: c1, r: 6, c: C.axis, label: 'centre of mass' });
      it.push({ t: 'line', a: V.add(CEN, V.mul(SL.pole, -3.4)),
                b: V.add(CEN, V.mul(SL.pole, 3.4)), c: C.axis, w: 2, dash: [7, 4] });
      it.push({ t: 'dot', a: CEN, r: 3.4, c: C.axis, label: 'rotation axis' });
      return it;
    }

    function readout() {
      var head0 = SL.at(-4.0, 0), toe0 = SL.at(4.0, 0);
      var dh = rot(head0, st.ang)[2] - head0[2];
      var dt = rot(toe0, st.ang)[2] - toe0[2];
      var dc = rot([0, 0, 0], st.ang)[2];   // block centroid is at the origin
      $('roC').innerHTML =
        'head <b style="color:#D55E00">' + fmt(dh, 2) + '</b> &nbsp;·&nbsp; ' +
        'toe <b style="color:#009E73">' + fmt(dt, 2) + '</b> &nbsp;·&nbsp; ' +
        'centre of mass <b>' + fmt(dc, 3) + '</b> (scene units, + = up)<br>' +
        '<span style="color:#555">The toe genuinely rises, and the block is still falling. ' +
        'Upward motion at the toe is the <i>signature</i> of a gravitational slump, not evidence ' +
        'against one &mdash; the test is whether the centre of mass drops.</span>';
    }
    function upd() { scene.refresh(); readout(); }
    var el = $('scC_ang');
    if (el) el.addEventListener('input', function () {
      st.ang = +el.value;
      var lab = $('scC_ang_v'); if (lab) lab.textContent = el.value;
      upd();
    });
    upd();
    window.addEventListener('resize', function () { scene.draw(); });
  }

  /* ===================================================================== */
  /* Scene D — how a stereonet folds directions, and what it hides          */
  /* ===================================================================== */
  function sceneD() {
    var cv = $('scD'); if (!cv) return;
    var SL = K.slope(25, 165, 1, 1);

    function vecs() {
      // head and toe motion under a rotation about the pole: exactly opposite
      var head = V.cross(SL.pole, V.mul(SL.drop, -3));
      var toe = V.cross(SL.pole, V.mul(SL.drop, 3));
      return [{ v: V.unit(head), n: 'head' }, { v: V.unit(toe), n: 'toe' }];
    }

    var scene = new K.Scene(cv, { scale: 62, yaw: -0.7, pitch: 0.3, build: build });

    function build() {
      var it = [];
      // equator + lower hemisphere meridians
      var N = 64, i;
      for (i = 0; i < N; i++) {
        var a0 = i / N * 6.2832, a1 = (i + 1) / N * 6.2832;
        it.push({ t: 'line', a: [Math.cos(a0), Math.sin(a0), 0],
                  b: [Math.cos(a1), Math.sin(a1), 0], c: '#c9d0ce', w: 1 });
      }
      function meridian(az, t) {
        return [Math.cos(az) * Math.sin(t), Math.sin(az) * Math.sin(t),
                -Math.abs(Math.cos(t))];
      }
      for (var m = 0; m < 4; m++) {
        var az = m / 4 * Math.PI;
        for (i = 0; i < N / 2; i++) {
          var t0 = i / (N / 2) * Math.PI, t1 = (i + 1) / (N / 2) * Math.PI;
          it.push({ t: 'line', a: meridian(az, t0), b: meridian(az, t1), c: '#e8ecea', w: 0.8 });
        }
      }
      vecs().forEach(function (r) {
        var up = r.v[2] > 0;
        var plotted = up ? V.mul(r.v, -1) : r.v;   // antipodal fold
        it.push({ t: 'arrow', a: [0, 0, 0], b: r.v, c: up ? '#009E73' : C.asc, w: 2.4,
                  head: 9, label: r.n + (up ? ' (up)' : ' (down)') });
        if (up) {
          it.push({ t: 'line', a: r.v, b: plotted, c: '#009E73', w: 1, dash: [3, 3], alpha: 0.7 });
        }
        it.push({ t: 'dot', a: plotted, r: 6, c: up ? '#fff' : C.asc,
                  hollow: up, edge: true });
        // where it lands on the horizontal disc
        it.push({ t: 'line', a: plotted, b: [plotted[0], plotted[1], 0], c: '#b9c2bf',
                  w: 0.9, dash: [2, 3] });
      });
      return it;
    }

    function readout() {
      $('roD').innerHTML =
        'Both vectors are the same rotation seen at opposite ends of the block, so they point ' +
        '<b>exactly opposite ways</b>. The stereonet only plots the lower half of the sphere, so ' +
        'the upward one is folded through the centre &mdash; and lands <b>on top of</b> its partner, ' +
        'told apart only by being drawn hollow. That is why the head-down/toe-up signature is ' +
        'invisible on a stereonet, and why the methods below plot it as a downslope profile instead.';
    }
    function upd() { scene.refresh(); readout(); }
    upd();
    window.addEventListener('resize', function () { scene.draw(); });
  }

  function init() { sceneA(); sceneB(); sceneC(); sceneD(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
