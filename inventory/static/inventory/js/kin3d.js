/* kin3d.js — a very small orthographic 3-D canvas engine for the InSAR
 * kinematics methods page.
 *
 * Deliberately not three.js: the scenes here are a few dozen line segments,
 * arrows and translucent quads, the site has no bundler, and a vendored 3-D
 * library would be ~600 kB to draw what fits in 200 lines. Everything is
 * orthographic with painter's-algorithm depth sorting, which is exactly right
 * for diagrams where parallel really should look parallel.
 *
 * Coordinates are the same ones the analysis uses throughout:
 *   x = East, y = North, z = Up, all in metres.
 *
 * Scene objects are plain data pushed onto scene.items:
 *   {t:'line',  a:[x,y,z], b:[x,y,z], c:'#rgb', w:1, dash:[4,3]}
 *   {t:'arrow', a:[..], b:[..], c:'#rgb', w:2, head:8, label:'u'}
 *   {t:'poly',  pts:[[..],..], fill:'rgba()', c:'#rgb', w:1}
 *   {t:'dot',   a:[..], r:4, c:'#rgb', hollow:false, label:''}
 *   {t:'text',  a:[..], s:'label', c:'#rgb', dx:0, dy:0}
 * Drag to rotate. Scenes rebuild themselves through a build() callback so
 * sliders can mutate state and just call scene.refresh().
 */
(function () {
  'use strict';

  function v3(a) { return [a[0], a[1], a[2]]; }
  function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
  function add(a, b) { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; }
  function mul(a, s) { return [a[0] * s, a[1] * s, a[2] * s]; }
  function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
  function cross(a, b) {
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]];
  }
  function norm(a) { return Math.sqrt(dot(a, a)); }
  function unit(a) { var n = norm(a); return n < 1e-12 ? [0, 0, 0] : mul(a, 1 / n); }

  function Scene(canvas, opts) {
    opts = opts || {};
    this.cv = canvas;
    this.ctx = canvas.getContext('2d');
    this.yaw = opts.yaw === undefined ? -0.6 : opts.yaw;
    this.pitch = opts.pitch === undefined ? 0.45 : opts.pitch;
    this.scale = opts.scale || 1;
    this.centre = opts.centre || [0, 0, 0];
    this.build = opts.build || function () { return []; };
    this.items = [];
    var self = this;

    var dragging = false, lx = 0, ly = 0;
    function down(e) {
      dragging = true;
      var p = pt(e); lx = p.x; ly = p.y;
      canvas.style.cursor = 'grabbing';
      e.preventDefault();
    }
    function move(e) {
      if (!dragging) return;
      var p = pt(e);
      self.yaw -= (p.x - lx) * 0.012;
      self.pitch += (p.y - ly) * 0.012;
      // clamp so the scene never flips through the poles
      self.pitch = Math.max(-1.45, Math.min(1.45, self.pitch));
      lx = p.x; ly = p.y;
      self.draw();
      e.preventDefault();
    }
    function up() { dragging = false; canvas.style.cursor = 'grab'; }
    function pt(e) {
      var r = canvas.getBoundingClientRect();
      var t = e.touches ? e.touches[0] : e;
      return { x: t.clientX - r.left, y: t.clientY - r.top };
    }
    canvas.addEventListener('mousedown', down);
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
    canvas.addEventListener('touchstart', down, { passive: false });
    canvas.addEventListener('touchmove', move, { passive: false });
    canvas.addEventListener('touchend', up);
    canvas.style.cursor = 'grab';
    canvas.style.touchAction = 'none';
  }

  Scene.prototype.basis = function () {
    var cy = Math.cos(this.yaw), sy = Math.sin(this.yaw);
    var cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
    var e = [cy, -sy, 0];                       // screen right
    var u = [sy * sp, cy * sp, cp];             // screen up
    return { e: e, u: u, f: cross(e, u) };      // f = depth axis
  };

  Scene.prototype.project = function (p) {
    var b = this._b, q = sub(p, this.centre);
    return { x: this.w2 + dot(q, b.e) * this.scale,
             y: this.h2 - dot(q, b.u) * this.scale,
             d: dot(q, b.f) };
  };

  Scene.prototype.refresh = function () { this.items = this.build(this) || []; this.draw(); };

  Scene.prototype.draw = function () {
    var cv = this.cv, ctx = this.ctx;
    var dpr = window.devicePixelRatio || 1;
    var cssW = cv.clientWidth, cssH = cv.clientHeight;
    if (cv.width !== Math.round(cssW * dpr) || cv.height !== Math.round(cssH * dpr)) {
      cv.width = Math.round(cssW * dpr); cv.height = Math.round(cssH * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    this.w2 = cssW / 2; this.h2 = cssH / 2;
    this._b = this.basis();

    // painter's algorithm: farthest first. Depth key is the mean vertex depth,
    // which is fine for diagrams with no interpenetrating surfaces.
    var self = this;
    var drawList = this.items.map(function (it, i) {
      var ps = it.t === 'poly' ? it.pts : (it.t === 'dot' || it.t === 'text' ? [it.a] : [it.a, it.b]);
      var d = 0;
      ps.forEach(function (p) { d += self.project(p).d; });
      return { it: it, d: d / ps.length, i: i };
    });
    drawList.sort(function (a, b) { return a.d - b.d || a.i - b.i; });
    drawList.forEach(function (r) { self.item(r.it); });
  };

  Scene.prototype.item = function (it) {
    var ctx = this.ctx;
    ctx.save();
    ctx.lineWidth = it.w || 1.4;
    ctx.strokeStyle = it.c || '#444';
    ctx.setLineDash(it.dash || []);
    if (it.alpha !== undefined) ctx.globalAlpha = it.alpha;
    if (it.t === 'line' || it.t === 'arrow') {
      var A = this.project(it.a), B = this.project(it.b);
      ctx.beginPath(); ctx.moveTo(A.x, A.y); ctx.lineTo(B.x, B.y); ctx.stroke();
      if (it.t === 'arrow') {
        var dx = B.x - A.x, dy = B.y - A.y, L = Math.hypot(dx, dy) || 1;
        var h = it.head || 9, wgt = h * 0.45;
        var ux = dx / L, uy = dy / L;
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.moveTo(B.x, B.y);
        ctx.lineTo(B.x - ux * h - uy * wgt, B.y - uy * h + ux * wgt);
        ctx.lineTo(B.x - ux * h + uy * wgt, B.y - uy * h - ux * wgt);
        ctx.closePath();
        ctx.fillStyle = it.c || '#444';
        ctx.fill();
      }
      if (it.label) {
        ctx.fillStyle = it.c || '#444';
        ctx.font = (it.font || '600 12px system-ui, sans-serif');
        ctx.fillText(it.label, B.x + (it.dx || 8), B.y + (it.dy || -6));
      }
    } else if (it.t === 'poly') {
      var self = this;
      ctx.beginPath();
      it.pts.forEach(function (p, i) {
        var P = self.project(p);
        if (i === 0) ctx.moveTo(P.x, P.y); else ctx.lineTo(P.x, P.y);
      });
      ctx.closePath();
      if (it.fill) { ctx.fillStyle = it.fill; ctx.fill(); }
      if (it.c) ctx.stroke();
    } else if (it.t === 'dot') {
      var D = this.project(it.a);
      ctx.beginPath(); ctx.arc(D.x, D.y, it.r || 4, 0, 6.2832);
      if (it.hollow) { ctx.fillStyle = '#fff'; ctx.fill(); ctx.stroke(); }
      else { ctx.fillStyle = it.c || '#444'; ctx.fill(); if (it.edge) ctx.stroke(); }
      if (it.label) {
        ctx.fillStyle = it.lc || it.c || '#444';
        ctx.font = (it.font || '600 12px system-ui, sans-serif');
        ctx.fillText(it.label, D.x + (it.dx || 7), D.y + (it.dy || -5));
      }
    } else if (it.t === 'text') {
      var T = this.project(it.a);
      ctx.fillStyle = it.c || '#444';
      ctx.font = (it.font || '12px system-ui, sans-serif');
      ctx.textAlign = it.align || 'left';
      ctx.fillText(it.s, T.x + (it.dx || 0), T.y + (it.dy || 0));
    }
    ctx.restore();
  };

  /* ---- shared scene furniture ------------------------------------------ */

  // Ground grid on z = 0 (or on a plane through `at` with normal +z).
  function grid(half, step, z, colour) {
    var out = [], c = colour || '#dcdcda';
    for (var v = -half; v <= half + 1e-9; v += step) {
      out.push({ t: 'line', a: [-half, v, z], b: [half, v, z], c: c, w: 0.7 });
      out.push({ t: 'line', a: [v, -half, z], b: [v, half, z], c: c, w: 0.7 });
    }
    return out;
  }

  function axes(L) {
    return [
      { t: 'arrow', a: [0, 0, 0], b: [L, 0, 0], c: '#9aa3a0', w: 1.2, head: 7, label: 'E' },
      { t: 'arrow', a: [0, 0, 0], b: [0, L, 0], c: '#9aa3a0', w: 1.2, head: 7, label: 'N' },
      { t: 'arrow', a: [0, 0, 0], b: [0, 0, L], c: '#9aa3a0', w: 1.2, head: 7, label: 'Up' }
    ];
  }

  // A tilted slope surface: returns {quad, dropline, pole, normal, pointAt(s,t)}
  // s runs downslope (positive = toward the toe), t runs cross-slope.
  function slope(dipDeg, azDeg, halfS, halfT) {
    var az = azDeg * Math.PI / 180, dip = dipDeg * Math.PI / 180;
    var dh = [Math.sin(az), Math.cos(az), 0];                 // horizontal downhill
    var drop = unit([dh[0] * Math.cos(dip), dh[1] * Math.cos(dip), -Math.sin(dip)]);
    // Horizontal cross-slope axis, signed so that a POSITIVE rotation about it
    // lowers the head and raises the toe — the mass-lowering convention the
    // analysis uses. The other sign is an anti-gravitational rotation, which
    // would make these figures teach the reverse of the physics.
    var pole = unit([dh[1], -dh[0], 0]);
    var n = cross(drop, pole);                                // outward slope normal
    if (n[2] < 0) n = mul(n, -1);
    function at(s, t) { return add(mul(drop, s), mul(pole, t)); }
    return {
      drop: drop, pole: pole, normal: n, at: at,
      quad: [at(-halfS, -halfT), at(halfS, -halfT), at(halfS, halfT), at(-halfS, halfT)]
    };
  }

  window.Kin3D = {
    Scene: Scene, grid: grid, axes: axes, slope: slope,
    v: { add: add, sub: sub, mul: mul, dot: dot, cross: cross, norm: norm, unit: unit, v3: v3 },
    // Sentinel-1 look vectors, ground -> satellite, +LOS = toward the satellite.
    LOS: {
      asc: [-0.613, -0.142, 0.777],
      desc: [0.613, -0.142, 0.777]
    }
  };
})();
