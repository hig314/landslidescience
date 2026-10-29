/* ls_water.js — a survey's water surface, drawn over its bed. Global `window.LSWater`.
 *
 * 2026-10-20 (Hig): water, and eventually ice, as a DEM of its own linked to the topobathy DEM,
 * displayed together with the bed, the water see-through with its own opacity and styling (default
 * a blue hillshade), aiming at true see-through water in 3D.
 *
 * A survey links its surfaces in the catalogue: `surfaces: [{kind: 'water'|'sea', url, encoding}]`,
 * each a terrain-RGB PMTiles on the survey's own tile grid. `water` is the inland surface (lakes at
 * their level, rivers at their surface), padded a little past the shore and held below the bed there
 * (tools/lidar/topobathy/water_surface.py), so the shoreline is wherever the surface stands above the
 * bed, at every zoom. `sea` is a mask of ground connected to the sea; it floods to the tide level the
 * page chooses. The older `water_url` / `sea_url` pair (Mapbox-encoded) is still read.
 *
 *   LSWater.has(p)                          // does this survey carry a water surface?
 *   LSWater.levelTile(p, z, x, y, tide)     // Promise<{w, level, bed} | null> (level NaN = no water)
 *   LSWater.register(maplibregl)            // the lswater:// protocol for the 2D drape
 *   LSWater.url2d(p, s)                     // tile URL template for the 2D drape, s = state (below)
 *   LSWater.layer3d(map, getState)          // a custom layer: the water as its own translucent mesh
 *
 * state s = {tide (m), style: 'shade'|'depth'|'flat', az, alt (deg), relief (x), opacity (0..1)}.
 *
 * In 3D the water is NOT draped: MapLibre carries one terrain surface (the bed), and the water is a
 * second mesh in a custom WebGL layer, depth-tested against the terrain and blended over it, so the
 * bed and its shading show through the water at the water's own height. Built against maplibre-gl
 * 5.24.0: a custom layer's defaultProjectionData.mainMatrix takes mercator x, y in [0, 1] and z in
 * mercator units at the centre latitude (worldSize / pixelsPerMeter), which is exactly how terrain
 * places its own vertices, so the water sits on the same vertical scale as the bed.
 */
window.LSWater = (function () {
  'use strict';
  var srcs = {};

  function surf(p, kind) {
    var s = (p.surfaces || []).filter(function (x) { return x.kind === kind; })[0];
    if (s) return s;
    if (kind === 'water' && p.water_url) return { kind: 'water', url: p.water_url, encoding: 'mapbox' };
    if (kind === 'sea' && p.sea_url) return { kind: 'sea', url: p.sea_url, encoding: 'mapbox' };
    return null;
  }
  function has(p) { return !!(p && (surf(p, 'water') || surf(p, 'sea'))); }
  function sources(p) {
    if (!srcs[p.id]) {
      var mk = function (kind) {
        var s = surf(p, kind);
        return s ? { id: p.id + '~' + kind, encoding: s.encoding || 'mapbox', maxzoom: p.max_zoom,
                     pmtiles: LSDemRead.archive(s.url) } : null;
      };
      srcs[p.id] = { bed: LSDemRead.surveySource(p), water: mk('water'), sea: mk('sea') };
    }
    return srcs[p.id];
  }
  // Level per pixel: the inland surface, or the tide over the sea mask where that is higher. The water
  // and sea tiles are read first: most tiles of a survey are dry, and for those the bed is never read.
  // Cached (a mesh reads its neighbours too), a few hundred tiles deep.
  var lvCache = {}, lvOrder = [];
  function levelTile(p, z, x, y, tide) {
    var key = p.id + '/' + z + '/' + x + '/' + y + '/' + tide.toFixed(2);
    if (lvCache[key]) return lvCache[key];
    var S = sources(p);
    var read = function (s) { return s ? LSDemRead.tileFor(s, z, x, y) : Promise.resolve(null); };
    var pr = Promise.all([read(S.water), read(S.sea)]).then(function (t) {
      var wat = t[0], sea = t[1];
      if (!wat && !sea) return null;
      var w = (wat || sea).w, n = w * w, lv = new Float32Array(n), any = false;
      for (var i = 0; i < n; i++) {
        var v = wat ? wat.data[i] : NaN;
        if (sea && !isNaN(sea.data[i]) && !(v >= tide)) v = tide;
        lv[i] = v; if (v === v) any = true;
      }
      if (!any) return null;
      return read(S.bed).then(function (bed) { return { w: w, level: lv, bed: bed ? bed.data : null }; });
    });
    lvCache[key] = pr; lvOrder.push(key);
    if (lvOrder.length > 400) delete lvCache[lvOrder.shift()];
    return pr;
  }

  // ---- colour ---------------------------------------------------------------
  // 'shade': a blue hillshade of the water's own surface; 'depth': shallow cyan to deep navy, alpha
  // rising over the first 2 m (the first water toggle's look); 'flat': one blue.
  var DEEP = [18, 58, 112], LIGHT = [150, 205, 240], FLAT = [52, 128, 196];
  function depthRGB(d) {
    var t = Math.max(0, Math.min(1, Math.log(1 + d) / Math.log(51)));
    return [120 - 110 * t, 205 - 160 * t, 230 - 110 * t, Math.min(1, 0.35 + 0.65 * Math.min(1, d / 2))];
  }
  function sunVec(az, alt) {
    var a = az * Math.PI / 180, e = alt * Math.PI / 180;
    return [Math.sin(a) * Math.cos(e), -Math.cos(a) * Math.cos(e), Math.sin(e)];   // x east, y SOUTH, z up
  }
  function mpp(z, y) {                 // metres per pixel of a 256 px tile at tile row y
    var n = Math.PI - 2 * Math.PI * (y + 0.5) / Math.pow(2, z);
    var lat = Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
    return 2 * Math.PI * 6378137 * Math.cos(lat) / (256 * Math.pow(2, z));
  }

  // ---- 2D: lswater://<id>/<tide cm>/<style>/<az>/<alt>/<relief x10>/<seq>/{z}/{x}/{y} -------------------
  var props = {};
  function url2d(p, s) {
    props[p.id] = p;
    return 'lswater://' + p.id + '/' + Math.round(s.tide * 100) + '/' + s.style + '/' + Math.round(s.az) + '/' +
           Math.round(s.alt) + '/' + Math.round(s.relief * 10) + '/' + (s.seq || 0) + '/{z}/{x}/{y}';
  }
  function register(ml) {
    ml.addProtocol('lswater', function (params) {
      var m = params.url.match(/^lswater:\/\/([^/]+)\/(-?\d+)\/(\w+)\/(\d+)\/(\d+)\/(\d+)\/\d+\/(\d+)\/(\d+)\/(\d+)$/);
      var p = m && props[m[1]];
      if (!p) return Promise.reject(new Error('lswater: bad url'));
      var tide = +m[2] / 100, style = m[3], L = sunVec(+m[4], +m[5]), rel = +m[6] / 10;
      var z = +m[7], x = +m[8], y = +m[9];
      return levelTile(p, z, x, y, tide).then(function (t) {
        var size = t ? t.w : 256;
        var c = document.createElement('canvas'); c.width = c.height = size;
        var ctx = c.getContext('2d'), img = ctx.createImageData(size, size), o = img.data;
        if (t) paint2d(t, o, style, L, rel * 256 / size / mpp(z, y));
        ctx.putImageData(img, 0, 0);
        return new Promise(function (res) { c.toBlob(res, 'image/png'); });
      }).then(function (blob) { return blob.arrayBuffer(); }).then(function (buf) { return { data: buf }; });
    });
  }
  function paint2d(t, o, style, L, k) {   // k = relief / metres per pixel
    var w = t.w, lv = t.level, bed = t.bed;
    for (var r = 0; r < w; r++) {
      for (var c = 0; c < w; c++) {
        var i = r * w + c, v = lv[i];
        if (!(v === v)) continue;
        var b = bed ? bed[i] : NaN;
        if (b === b && !(v > b)) continue;               // dry: the surface is at or below the bed
        var d = b === b ? v - b : 50, j = i * 4, col;
        if (style === 'depth') {
          col = depthRGB(d);
        } else if (style === 'flat') {
          col = [FLAT[0], FLAT[1], FLAT[2], 1];
        } else {
          var l = lv[r * w + Math.max(0, c - 1)], rr = lv[r * w + Math.min(w - 1, c + 1)];
          var u = lv[Math.max(0, r - 1) * w + c], dd = lv[Math.min(w - 1, r + 1) * w + c];
          if (!(l === l)) l = v; if (!(rr === rr)) rr = v; if (!(u === u)) u = v; if (!(dd === dd)) dd = v;
          var gx = (rr - l) * 0.5 * k, gy = (dd - u) * 0.5 * k;        // dz per pixel east, south (exaggerated)
          var nl = Math.sqrt(gx * gx + gy * gy + 1);
          var s = Math.max(0, (-gx * L[0] - gy * L[1] + L[2]) / nl);
          col = [DEEP[0] + (LIGHT[0] - DEEP[0]) * s, DEEP[1] + (LIGHT[1] - DEEP[1]) * s,
                 DEEP[2] + (LIGHT[2] - DEEP[2]) * s, 1];
        }
        o[j] = col[0]; o[j + 1] = col[1]; o[j + 2] = col[2]; o[j + 3] = 255 * col[3];
      }
    }
  }

  // ---- 3D: the water as its own mesh ---------------------------------------------------------------
  var VS = [
    '#version 300 es',
    'precision highp float;',
    'uniform mat4 u_matrix; uniform float u_rel;',
    'in vec3 a_pos; in float a_depth; in vec2 a_grad;',
    'out float v_depth; out vec3 v_n;',
    'void main() {',
    '  v_depth = a_depth;',
    '  v_n = normalize(vec3(-a_grad.x * u_rel, -a_grad.y * u_rel, 1.0));',
    '  gl_Position = u_matrix * vec4(a_pos, 1.0);',
    '}'].join('\n');
  var FS = [
    '#version 300 es',
    'precision highp float;',
    'uniform vec3 u_sun; uniform float u_opacity; uniform int u_style;',
    'uniform vec3 u_deep; uniform vec3 u_light; uniform vec3 u_flat;',
    'in float v_depth; in vec3 v_n; out vec4 fragColor;',
    'void main() {',
    '  if (v_depth <= 0.0) discard;',                        // the shoreline, interpolated between vertices
    '  float s = max(0.0, dot(normalize(v_n), u_sun));',
    '  vec3 c; float a = 1.0;',
    '  if (u_style == 1) {',
    '    float t = clamp(log(1.0 + v_depth) / log(51.0), 0.0, 1.0);',
    '    c = vec3(120.0 - 110.0 * t, 205.0 - 160.0 * t, 230.0 - 110.0 * t) / 255.0 * (0.75 + 0.25 * s);',
    '    a = min(1.0, 0.35 + 0.65 * min(1.0, v_depth / 2.0));',
    '  } else if (u_style == 2) { c = u_flat; }',
    '  else { c = mix(u_deep, u_light, s); }',
    '  a *= u_opacity;',
    '  fragColor = vec4(c * a, a);',                           // MapLibre blends premultiplied
    '}'].join('\n');

  var N = 128;                            // mesh segments per 256 px tile (a vertex every 2 px)
  var MAX_TILES = 64;

  function compile(gl) {
    function sh(type, src) {
      var s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error('lswater shader: ' + gl.getShaderInfoLog(s));
      return s;
    }
    var pr = gl.createProgram();
    gl.attachShader(pr, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(pr);
    if (!gl.getProgramParameter(pr, gl.LINK_STATUS)) throw new Error('lswater link: ' + gl.getProgramInfoLog(pr));
    var loc = {};
    ['u_matrix', 'u_rel', 'u_sun', 'u_opacity', 'u_style', 'u_deep', 'u_light', 'u_flat'].forEach(function (k) {
      loc[k] = gl.getUniformLocation(pr, k);
    });
    ['a_pos', 'a_depth', 'a_grad'].forEach(function (k) { loc[k] = gl.getAttribLocation(pr, k); });
    return { program: pr, loc: loc };
  }

  // Build one tile's mesh from that tile and its east, south and south-east neighbours (their first
  // column / row close the seams).
  function buildMesh(p, z, x, y, tide) {
    var n2 = Math.pow(2, z);
    var get = function (dx, dy) {
      var xx = x + dx, yy = y + dy;
      if (yy >= n2) return Promise.resolve(null);
      return levelTile(p, z, (xx + n2) % n2, yy, tide);
    };
    return Promise.all([get(0, 0), get(1, 0), get(0, 1), get(1, 1)]).then(function (T) {
      if (!T[0]) return null;
      var w = T[0].w, step = w / N, M = N + 1, k = mpp(z, y) * 256 / w;    // metres per pixel
      function at(px, py, which) {          // px, py in 0 .. 2w-1 across the 2 x 2 block
        var tx = px >= w ? 1 : 0, ty = py >= w ? 1 : 0, t = T[ty * 2 + tx];
        if (!t) return NaN;
        var i = (py - ty * w) * w + (px - tx * w);
        return which === 'bed' ? (t.bed ? t.bed[i] : NaN) : t.level[i];
      }
      var nv = M * M, pos = new Float32Array(nv * 3), dep = new Float32Array(nv), grad = new Float32Array(nv * 2);
      var ok = new Uint8Array(nv), cnt = 0;
      for (var r = 0; r < M; r++) {
        for (var c = 0; c < M; c++) {
          var px = Math.min(Math.round(c * step), 2 * w - 1), py = Math.min(Math.round(r * step), 2 * w - 1);
          var v = at(px, py), q = r * M + c;
          pos[q * 3] = c / N; pos[q * 3 + 1] = r / N; pos[q * 3 + 2] = v === v ? v : 0;
          if (!(v === v)) { dep[q] = -1; continue; }
          ok[q] = 1; cnt++;
          var b = at(px, py, 'bed');
          dep[q] = b === b ? v - b : 50;
          var s = Math.max(1, Math.round(step));
          var l = at(Math.max(0, px - s), py), rr = at(Math.min(2 * w - 1, px + s), py);
          var u = at(px, Math.max(0, py - s)), dd = at(px, Math.min(2 * w - 1, py + s));
          var hx = 2 * s, hy = 2 * s;
          if (!(l === l)) { l = v; hx = s; } if (!(rr === rr)) { rr = v; hx = hx === s ? 1e9 : s; }
          if (!(u === u)) { u = v; hy = s; } if (!(dd === dd)) { dd = v; hy = hy === s ? 1e9 : s; }
          grad[q * 2] = (rr - l) / (hx * k); grad[q * 2 + 1] = (dd - u) / (hy * k);
        }
      }
      if (!cnt) return null;
      var idx = [];
      for (var r2 = 0; r2 < N; r2++) {
        for (var c2 = 0; c2 < N; c2++) {
          var a0 = r2 * M + c2, a1 = a0 + 1, a2 = a0 + M, a3 = a2 + 1;
          if (ok[a0] && ok[a1] && ok[a2]) idx.push(a0, a2, a1);
          if (ok[a1] && ok[a2] && ok[a3]) idx.push(a1, a2, a3);
        }
      }
      if (!idx.length) return null;
      return { z: z, x: x, y: y, pos: pos, dep: dep, grad: grad, idx: new Uint32Array(idx) };
    });
  }

  function tileRange(b, z) {             // [[x0, x1], [y0, y1]] for lon/lat bounds b = [w, s, e, n]
    var n = Math.pow(2, z);
    var tx = function (lon) { return Math.floor((lon + 180) / 360 * n); };
    var ty = function (lat) {
      var la = Math.max(-85, Math.min(85, lat)) * Math.PI / 180;
      return Math.floor((1 - Math.log(Math.tan(la) + 1 / Math.cos(la)) / Math.PI) / 2 * n);
    };
    return [[tx(b[0]), tx(b[2])], [ty(b[3]), ty(b[1])]];
  }

  function layer3d(map, getState) {
    var gl = null, prog = null, meshes = {}, inflight = {}, drawn = [], wantKey = '';
    var self = { id: 'lswater3d', type: 'custom', renderingMode: '3d' };

    function release(m) {
      if (m && m.buf && gl) { gl.deleteBuffer(m.buf.pos); gl.deleteBuffer(m.buf.dep); gl.deleteBuffer(m.buf.grad); gl.deleteBuffer(m.buf.idx); }
    }
    function upload(m) {
      var mk = function (target, data) { var b = gl.createBuffer(); gl.bindBuffer(target, b); gl.bufferData(target, data, gl.STATIC_DRAW); return b; };
      m.buf = { pos: mk(gl.ARRAY_BUFFER, m.pos), dep: mk(gl.ARRAY_BUFFER, m.dep), grad: mk(gl.ARRAY_BUFFER, m.grad),
                idx: mk(gl.ELEMENT_ARRAY_BUFFER, m.idx), n: m.idx.length };
      m.pos = m.dep = m.grad = m.idx = null;
    }
    // Which tiles: the survey's bounds inside the view, at the deepest zoom up to the view's that keeps
    // the count under MAX_TILES. Recomputed when the view settles or the state changes.
    function wanted() {
      var s = getState(); if (!s || !s.p) return [];
      var p = s.p, b = map.getBounds(), pb = p.bounds;
      var bb = [Math.max(b.getWest(), pb[0]), Math.max(b.getSouth(), pb[1]), Math.min(b.getEast(), pb[2]), Math.min(b.getNorth(), pb[3])];
      if (bb[0] >= bb[2] || bb[1] >= bb[3]) return [];
      var z = Math.max(p.min_zoom, Math.min(p.max_zoom, Math.floor(map.getZoom()) + 1)), R;
      for (; z >= p.min_zoom; z--) {
        R = tileRange(bb, z);
        if ((R[0][1] - R[0][0] + 1) * (R[1][1] - R[1][0] + 1) <= MAX_TILES) break;
      }
      var out = [];
      for (var x = R[0][0]; x <= R[0][1]; x++) for (var y = R[1][0]; y <= R[1][1]; y++) out.push([z, x, y]);
      return out;
    }
    function refresh() {
      var s = getState(); if (!gl || !s || !s.p) return;
      var W = wanted(), tag = s.p.id + '/' + s.tide.toFixed(2);
      var key = tag + ':' + W.map(function (t) { return t.join('/'); }).join(',');
      if (key === wantKey) return;
      wantKey = key;
      var mine = key, pending = [];
      function current() {
        return W.map(function (t) { return meshes[tag + '/' + t.join('/')]; }).filter(function (m) { return m && m.buf; });
      }
      var now = current();            // tiles kept across a pan show at once; on a zoom change the old set stays until a new one lands
      if (now.length) drawn = now;
      W.forEach(function (t) {
        var k = tag + '/' + t.join('/');
        if (meshes[k] === undefined) {
          meshes[k] = 'loading';
          inflight[k] = buildMesh(s.p, t[0], t[1], t[2], s.tide).then(function (m) {
            if (m && gl) upload(m);
            meshes[k] = m || null; delete inflight[k];
            if (m && mine === wantKey) { drawn = current(); map.triggerRepaint(); }    // show each as it lands
          }, function () { meshes[k] = null; delete inflight[k]; });
        }
        if (inflight[k]) pending.push(inflight[k]);
      });
      Promise.all(pending).then(function () {
        if (mine !== wantKey) return;      // superseded while building
        drawn = current();
        // forget meshes far from the view, so a long session does not pile up GPU buffers
        var keep = {}; W.forEach(function (t) { keep[tag + '/' + t.join('/')] = 1; });
        var ks = Object.keys(meshes);
        if (ks.length > 3 * MAX_TILES) ks.forEach(function (k2) { if (!keep[k2] && meshes[k2] !== 'loading') { release(meshes[k2]); delete meshes[k2]; } });
        map.triggerRepaint();
      });
    }
    function clear() { Object.keys(meshes).forEach(function (k) { if (meshes[k] !== 'loading') release(meshes[k]); }); meshes = {}; drawn = []; wantKey = ''; }

    self.onAdd = function (m, g) { gl = g; prog = compile(gl); map.on('moveend', refresh); refresh(); };
    self.onRemove = function () { map.off('moveend', refresh); clear(); if (gl && prog) gl.deleteProgram(prog.program); gl = null; };
    self.refresh = refresh;
    self.debug = function () { return { drawn: drawn.length, meshes: Object.keys(meshes).length, inflight: Object.keys(inflight).length, want: wantKey.slice(0, 200), renders: renders }; };
    self.reset = function () { clear(); refresh(); map.triggerRepaint(); };
    var renders = 0;
    self.render = function (g, args) {
      renders++;
      var s = getState(); if (!s || !s.p || !drawn.length) return;
      var A = args.defaultProjectionData.mainMatrix, L = prog.loc;
      var ter = map.getTerrain(), ex = ter ? (ter.exaggeration == null ? 1 : ter.exaggeration) : 0;
      var zPerM = maplibregl.MercatorCoordinate.fromLngLat(map.getCenter(), 1).z;
      g.useProgram(prog.program);
      if (g.bindVertexArray) g.bindVertexArray(null);
      var sun = sunVec(s.az, s.alt);
      g.uniform3f(L.u_sun, sun[0], sun[1], sun[2]);
      g.uniform1f(L.u_opacity, s.opacity);
      g.uniform1i(L.u_style, s.style === 'depth' ? 1 : s.style === 'flat' ? 2 : 0);
      g.uniform3f(L.u_deep, DEEP[0] / 255, DEEP[1] / 255, DEEP[2] / 255);
      g.uniform3f(L.u_light, LIGHT[0] / 255, LIGHT[1] / 255, LIGHT[2] / 255);
      g.uniform3f(L.u_flat, FLAT[0] / 255, FLAT[1] / 255, FLAT[2] / 255);
      g.uniform1f(L.u_rel, s.relief * Math.max(ex, 0.0001));
      var Mf = new Float32Array(16);
      drawn.forEach(function (m) {
        var kk = 1 / Math.pow(2, m.z), x0 = m.x * kk, y0 = m.y * kk, zs = ex * zPerM;
        // u_matrix = mainMatrix x translate(x0, y0, 0) x scale(kk, kk, zs), composed in float64
        for (var i = 0; i < 4; i++) {
          Mf[i] = A[i] * kk; Mf[4 + i] = A[4 + i] * kk; Mf[8 + i] = A[8 + i] * zs;
          Mf[12 + i] = x0 * A[i] + y0 * A[4 + i] + A[12 + i];
        }
        g.uniformMatrix4fv(L.u_matrix, false, Mf);
        g.bindBuffer(g.ARRAY_BUFFER, m.buf.pos); g.enableVertexAttribArray(L.a_pos); g.vertexAttribPointer(L.a_pos, 3, g.FLOAT, false, 0, 0);
        g.bindBuffer(g.ARRAY_BUFFER, m.buf.dep); g.enableVertexAttribArray(L.a_depth); g.vertexAttribPointer(L.a_depth, 1, g.FLOAT, false, 0, 0);
        g.bindBuffer(g.ARRAY_BUFFER, m.buf.grad); g.enableVertexAttribArray(L.a_grad); g.vertexAttribPointer(L.a_grad, 2, g.FLOAT, false, 0, 0);
        g.bindBuffer(g.ELEMENT_ARRAY_BUFFER, m.buf.idx);
        g.drawElements(g.TRIANGLES, m.buf.n, g.UNSIGNED_INT, 0);
      });
      g.disableVertexAttribArray(L.a_pos); g.disableVertexAttribArray(L.a_depth); g.disableVertexAttribArray(L.a_grad);
    };
    return self;
  }

  return { has: has, surface: surf, levelTile: levelTile, register: register, url2d: url2d, layer3d: layer3d };
})();
