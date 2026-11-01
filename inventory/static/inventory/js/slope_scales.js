/* slope_scales.js — the two finest slope rows at z12, computed in the page.
 * Global `window.LSSlope`.
 *
 * The slope overlays (tools/terrain/bake_slope.py) are baked to z11. Baking
 * z12 too would add ~25 GB for two rows, so `local` and `60` read the archive
 * to z11 and at z12 run the same calculation here, on the same Mapterhorn z12
 * tiles the bake read. Deeper zooms overzoom z12. tools/terrain/chord_slope.py
 * defines the measure; `kernel` below is its diameter_slope_tile and horn.
 * If you change one, change the other, and re-check (LSSlope._compare): four
 * z12 tiles averaged here must match the baked z11 tile.
 *
 * The water and coverage mask comes from the z11 tile underneath: alpha 0 there
 * means no colour here, and a missing z11 tile means nothing to fetch at all.
 * So only the bake knows the coverage polygon and the flat-water rule, and open
 * sea and Canada cost no Mapterhorn requests.
 *
 *   lsslope://<local|60>/{z}/{x}/{y}     LSSlope.register(maplibregl)
 */
window.LSSlope = (function () {
  'use strict';

  var TILE = 512, Z_TOP = 11, Z_CALC = 12;
  var LENGTH = { local: null, '60': 60 };
  // tools/permafrost/sp_color_slope.txt, the one ramp: deg, r, g, b, a.
  var STOPS = [[0, 0, 151, 8, 70], [10, 255, 255, 255, 35], [32, 235, 236, 226, 90],
               [40, 255, 236, 0, 225], [45, 240, 0, 0, 255], [50, 184, 0, 255, 255], [90, 0, 0, 0, 255]];
  var WORLD = 2 * Math.PI * 6378137;

  function buildLut() {
    var lut = new Uint8ClampedArray(901 * 4);
    for (var i = 0; i <= 900; i++) {
      var d = i / 10, k = 0;
      while (k < STOPS.length - 2 && d > STOPS[k + 1][0]) k++;
      var a = STOPS[k], b = STOPS[k + 1], t = (d - a[0]) / (b[0] - a[0]);
      for (var c = 0; c < 4; c++) lut[i * 4 + c] = a[c + 1] + (b[c + 1] - a[c + 1]) * t + 0.5;
    }
    return lut;
  }

  // Runs in a worker (stringified) or, without Worker support, on the page.
  // m: {z: Float32Array padded by pad, n, pad, L (null = Horn), r, rowM, mask, lut}
  // -> straight RGBA, n x n.
  function kernel(m) {
    var z = m.z, n = m.n, pad = m.pad, w = n + 2 * pad, rowM = m.rowM;
    var out = new Uint8ClampedArray(n * n * 4), s = new Float32Array(n * n);
    var x, y, i;
    if (m.L === null) {
      for (y = 0; y < n; y++) {
        var px = rowM[y];
        for (x = 0; x < n; x++) {
          i = (y + pad) * w + x + pad;
          var a = z[i - w - 1], b = z[i - w], c = z[i - w + 1], d = z[i - 1], f = z[i + 1],
              g = z[i + w - 1], h = z[i + w], k = z[i + w + 1];
          var dx = ((c + 2 * f + k) - (a + 2 * d + g)) / (8 * px);
          var dy = ((g + 2 * h + k) - (a + 2 * b + c)) / (8 * px);
          s[y * n + x] = Math.sqrt(dx * dx + dy * dy);
        }
      }
    } else {
      var best = new Float32Array(n * n), NDIR = 24, r = m.r;
      for (var t = 0; t < NDIR; t++) {
        var th = Math.PI * t / NDIR, ddy = r * Math.sin(th), ddx = r * Math.cos(th);
        var iy = Math.floor(ddy), ix = Math.floor(ddx), fy = ddy - iy, fx = ddx - ix;
        var jy = Math.floor(-ddy), jx = Math.floor(-ddx), gy = -ddy - jy, gx = -ddx - jx;
        var a00 = (1 - fy) * (1 - fx), a01 = (1 - fy) * fx, a10 = fy * (1 - fx), a11 = fy * fx;
        var b00 = (1 - gy) * (1 - gx), b01 = (1 - gy) * gx, b10 = gy * (1 - gx), b11 = gy * gx;
        var oa = iy * w + ix, ob = jy * w + jx;
        for (y = 0; y < n; y++) {
          i = (y + pad) * w + pad;
          var e = y * n;
          for (x = 0; x < n; x++, i++, e++) {
            var p = i + oa, q = i + ob;
            var dd = z[p] * a00 + z[p + 1] * a01 + z[p + w] * a10 + z[p + w + 1] * a11 -
                     (z[q] * b00 + z[q + 1] * b01 + z[q + w] * b10 + z[q + w + 1] * b11);
            if (dd < 0) dd = -dd;
            if (dd > best[e]) best[e] = dd;
          }
        }
      }
      for (y = 0; y < n; y++) {
        var len = 2 * r * rowM[y];
        for (x = 0; x < n; x++) s[y * n + x] = best[y * n + x] / len;
      }
    }
    var lut = m.lut, mask = m.mask, DEG10 = 1800 / Math.PI;
    for (y = 0; y < n; y++) {
      i = (y + pad) * w + pad;
      for (x = 0; x < n; x++, i++) {
        var e2 = y * n + x;
        if (z[i] <= 0 || !mask[(y >> 1) * (n >> 1) + (x >> 1)]) continue;   // clear
        var v = Math.floor(Math.atan(s[e2]) * DEG10 + 0.5);
        if (v > 900) v = 900;
        var o = e2 * 4, l = v * 4;
        out[o] = lut[l]; out[o + 1] = lut[l + 1]; out[o + 2] = lut[l + 2]; out[o + 3] = lut[l + 3];
      }
    }
    return out;
  }

  // ---- a small worker pool, so panning at z12 does not stall the page --------
  var pool = null, waiting = [], jobs = {}, jobId = 0;
  function workers() {
    if (pool !== null) return pool;
    pool = [];
    try {
      var src = 'var kernel=' + kernel.toString() + ';' +
        'onmessage=function(e){var o=kernel(e.data.m);postMessage({id:e.data.id,o:o},[o.buffer]);};';
      var url = URL.createObjectURL(new Blob([src], { type: 'text/javascript' }));
      var n = Math.max(1, Math.min(3, (navigator.hardwareConcurrency || 2) - 1));
      for (var i = 0; i < n; i++) {
        var wk = new Worker(url);
        wk.idle = true;
        wk.onmessage = (function (w) {
          return function (e) {
            w.idle = true;
            var res = jobs[e.data.id]; delete jobs[e.data.id];
            if (res) res(e.data.o);
            next();
          };
        })(wk);
        pool.push(wk);
      }
    } catch (err) { pool = []; }
    return pool;
  }
  function next() {
    var free = pool.filter(function (w) { return w.idle; })[0];
    if (!free || !waiting.length) return;
    var job = waiting.shift();
    if (job.dead && job.dead()) { job.res(null); next(); return; }
    free.idle = false;
    jobs[job.id] = job.res;
    free.postMessage({ id: job.id, m: job.m }, [job.m.z.buffer]);
    next();
  }
  function run(m, dead) {
    if (!workers().length) return Promise.resolve(kernel(m));
    return new Promise(function (res) {
      waiting.push({ id: ++jobId, m: m, res: res, dead: dead });
      next();
    });
  }

  // ---- tiles ---------------------------------------------------------------
  function lruGet(c, k) {
    if (!c.has(k)) return undefined;
    var v = c.get(k); c.delete(k); c.set(k, v); return v;
  }
  function lruSet(c, k, v, max) {
    c.set(k, v);
    while (c.size > max) c.delete(c.keys().next().value);
  }
  function pixels(blob) {
    return createImageBitmap(blob, { premultiplyAlpha: 'none', colorSpaceConversion: 'none' }).then(function (bm) {
      var c = document.createElement('canvas');
      c.width = bm.width; c.height = bm.height;
      var ctx = c.getContext('2d', { willReadFrequently: true });
      ctx.drawImage(bm, 0, 0);
      var d = ctx.getImageData(0, 0, c.width, c.height).data;
      bm.close && bm.close();
      return d;
    });
  }
  var heights = new Map();                  // z12 tile -> Promise<Float32Array | null>
  function heightTile(x, y) {
    var n = Math.pow(2, Z_CALC);
    x = ((x % n) + n) % n;
    var k = x + '/' + y, p = lruGet(heights, k);
    if (p) return p;
    p = (y < 0 || y >= n) ? Promise.resolve(null) :
      fetch('https://tiles.mapterhorn.com/' + Z_CALC + '/' + x + '/' + y + '.webp')
        .then(function (r) { return r.ok ? r.blob().then(pixels) : null; })
        .then(function (d) {
          if (!d) return null;
          var o = new Float32Array(TILE * TILE);
          for (var i = 0, j = 0; j < o.length; i += 4, j++) o[j] = d[i] * 256 + d[i + 1] + d[i + 2] / 256 - 32768;
          return o;
        }).catch(function () { return null; });
    lruSet(heights, k, p, 80);
    return p;
  }
  var archives = {};
  function archive(sid) {
    return archives[sid] || (archives[sid] =
      new pmtiles.PMTiles(location.origin + '/overlays/sp_slope_' + sid + '.pmtiles'));
  }
  var parents = new Map();                  // sid/z11 tile -> Promise<Uint8Array alpha | null>
  function parentAlpha(sid, x, y) {
    var k = sid + '/' + x + '/' + y, p = lruGet(parents, k);
    if (p) return p;
    p = archive(sid).getZxy(Z_TOP, x, y).then(function (res) {
      if (!res || !res.data) return null;
      return pixels(new Blob([res.data], { type: 'image/webp' })).then(function (d) {
        var a = new Uint8Array(TILE * TILE);
        for (var i = 3, j = 0; j < a.length; i += 4, j++) a[j] = d[i];
        return a;
      });
    }).catch(function () { return null; });
    lruSet(parents, k, p, 32);
    return p;
  }

  function rowMetres(y) {
    var n = Math.pow(2, Z_CALC) * TILE, res = WORLD / n, out = new Float64Array(TILE);
    for (var i = 0; i < TILE; i++) {
      var lat = Math.atan(Math.sinh(Math.PI * (1 - 2 * (y * TILE + i + 0.5) / n)));
      out[i] = res * Math.cos(lat);
    }
    return out;
  }

  // The tile's RGBA, or null when nothing shows there.
  var LUT = null;
  function computeTile(sid, x, y, dead) {
    var L = LENGTH[sid];
    return parentAlpha(sid, x >> 1, y >> 1).then(function (pa) {
      if (!pa) return null;
      var hx = (x & 1) * (TILE >> 1), hy = (y & 1) * (TILE >> 1), half = TILE >> 1;
      var mask = new Uint8Array(half * half), any = false;
      for (var j = 0; j < half; j++) for (var i = 0; i < half; i++) {
        if (pa[(j + hy) * TILE + i + hx] > 0) { mask[j * half + i] = 1; any = true; }
      }
      if (!any) return null;
      var rowM = rowMetres(y), r = L === null ? 0 : (L / 2) / rowM[TILE >> 1];
      var pad = L === null ? 1 : Math.ceil(r) + 1;
      var want = [];
      for (var dy = -1; dy <= 1; dy++) for (var dx = -1; dx <= 1; dx++) want.push(heightTile(x + dx, y + dy));
      return Promise.all(want).then(function (t) {
        if (!t[4]) return null;
        var w = TILE + 2 * pad, z = new Float32Array(w * w);    // absent tile = open sea = 0 m
        for (var row = 0; row < w; row++) {
          var sy = row - pad, ti = sy < 0 ? 0 : sy >= TILE ? 2 : 1, ly = sy - (ti - 1) * TILE;
          for (var part = 0; part < 3; part++) {
            var src = t[ti * 3 + part];
            var x0 = part === 0 ? 0 : part === 1 ? pad : pad + TILE;
            var cnt = part === 1 ? TILE : pad;
            var sx = part === 0 ? TILE - pad : 0;
            if (src) z.set(src.subarray(ly * TILE + sx, ly * TILE + sx + cnt), row * w + x0);
          }
        }
        return run({ z: z, n: TILE, pad: pad, L: L, r: r, rowM: rowM, mask: mask,
                     lut: LUT || (LUT = buildLut()) }, dead);
      });
    });
  }

  function encode(rgba) {
    var c = document.createElement('canvas');
    c.width = c.height = TILE;
    c.getContext('2d').putImageData(new ImageData(rgba, TILE, TILE), 0, 0);
    return new Promise(function (resolve, reject) {
      c.toBlob(function (b) { b ? b.arrayBuffer().then(resolve, reject) : reject(new Error('lsslope toBlob')); }, 'image/png');
    });
  }

  function loader(params, abortController) {
    var p = params.url.replace('lsslope://', '').split('/');
    var sid = p[0], z = +p[1], x = +p[2], y = +p[3];
    var dead = function () { return abortController && abortController.signal && abortController.signal.aborted; };
    if (!(sid in LENGTH)) return Promise.reject(new Error('lsslope: unknown scale ' + sid));
    if (z <= Z_TOP) {
      return archive(sid).getZxy(z, x, y).then(function (res) {
        return { data: res && res.data ? res.data : null };
      });
    }
    return computeTile(sid, x, y, dead).then(function (rgba) {
      if (!rgba) return { data: null };
      return encode(rgba).then(function (buf) { return { data: buf }; });
    });
  }

  function register(gl) { if (gl.addProtocol) gl.addProtocol('lsslope', loader); }

  // Check: the four z12 tiles under z11 tile (x, y), averaged as the bake does
  // (premultiplied), against the baked tile. Land pixels only. Console or probe.
  function _compare(sid, x, y) {
    var kids = [[0, 0], [1, 0], [0, 1], [1, 1]];
    return Promise.all(kids.map(function (k) {
      return computeTile(sid, x * 2 + k[0], y * 2 + k[1]);
    })).then(function (ts) {
      return archive(sid).getZxy(Z_TOP, x, y).then(function (res) {
        return pixels(new Blob([res.data], { type: 'image/webp' }));
      }).then(function (d) {
        var se = 0, n = 0, maxd = 0, big = 0;
        for (var j = 0; j < TILE; j++) for (var i = 0; i < TILE; i++) {
          var o = d[(j * TILE + i) * 4 + 3], sum = 0, bx = i >> 8, by = j >> 8;
          var t = ts[by * 2 + bx];
          if (!t) continue;
          var ly = (j % 256) * 2, lx = (i % 256) * 2;
          for (var a = 0; a < 2; a++) for (var b = 0; b < 2; b++) sum += t[((ly + a) * TILE + lx + b) * 4 + 3];
          var dd = sum / 4 - o;
          se += dd * dd; n++;
          if (Math.abs(dd) > maxd) maxd = Math.abs(dd);
          if (Math.abs(dd) > 8) big++;
        }
        return { pixels: n, alpha_rms: Math.sqrt(se / n), alpha_max: maxd, alpha_over_8: big };
      });
    });
  }

  return { register: register, computeTile: computeTile, _compare: _compare, _kernel: kernel, _lut: buildLut };
})();
