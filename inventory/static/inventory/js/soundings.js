/* soundings.js — bathymetric soundings and depth contours as a map layer.
 * Global `window.LSSoundings`. Used by /inventory/ (a reference layer) and
 * /lidar/ (beside the surveys); the data are inventory/soundings.py's.
 *
 *   LSSoundings.init({map, api: '/inventory/', bars?}) -> Promise   fetch + add layers (idempotent);
 *        bars: true adds the 3D layer (/lidar/): with terrain on, every sounding is a marker AT ITS OWN
 *        ELEVATION (a disc; a lower bound a small down-pointing arrow; rejected a grey ring) instead of a
 *        dot draped on the surface, and the bars (surface to bed) appear as the lidar is faded
 *   LSSoundings.setSeeThrough(0..1)   the bars' opacity (/lidar/: 1 - lidar opacity)
 *   LSSoundings.setVisible(bool) / visible()
 *   LSSoundings.onChange(fn)   called when the data arrive or visibility changes
 *   LSSoundings.all() -> the FeatureCollection (with .surveys, .levels)
 *   LSSoundings.near(coords [[lon,lat],...], maxM) -> [{d, off, z, sigma, f}]
 *        soundings with an elevation within maxM of the line, d = metres along it
 *        (lower bounds included: f.properties.depth_bound === 'min')
 *
 * Points are coloured by DEPTH (pale shallow → dark deep, one fixed ramp so
 * two lakes read alike); a rejected sounding is a hollow grey ring, kept on the
 * map because the record of what was measured outlives the judgement on it.
 * A LOWER BOUND (depth_bound 'min': a lead line that ran out, ">120") sits on
 * the same ramp at its threshold with a heavy dark ring, and labels and the
 * popup say "≥": the bed is somewhere below it.
 * Contours (digitised old lake maps) are dashed lines. A click opens a popup
 * that cites the survey and says how the elevation was made (level − depth,
 * and whose level). Clicks go through LSTools as a REFERENCE (priority 35,
 * under the landform traces) where the page has it.
 *
 * Layers survive a basemap switch: they are re-added on style.load.
 */
window.LSSoundings = (function () {
  'use strict';
  var SRC = 'soundings', PT = 'soundings-pt', REJ = 'soundings-rej', HIT = 'soundings-hit',
      CON = 'soundings-con', LBL = 'soundings-lbl', BARS = 'soundings-3d';
  var map = null, api = '/inventory/', data = null, shown = false, loading = null, wired = false;
  var bars = false, seeThrough = 0;          // 3D bars (opts.bars, /lidar/) and their opacity
  var listeners = [];                         // onChange(fn): data loaded or visibility changed (the profile redraws)
  function changed() { listeners.forEach(function (fn) { try { fn(); } catch (e) { console.error(e); } }); }
  var surveys = {}, levels = {};

  // Depth ramp (m): pale blue at the shore to near-black navy at 100 m.
  var RAMP = ['interpolate', ['linear'], ['get', 'depth_m'],
              0, '#c6e6f5', 5, '#7fc4e8', 15, '#3b8fd0', 30, '#1f5fa8', 60, '#14336e', 100, '#0a1838'];

  function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'); }
  function vis() { return shown ? 'visible' : 'none'; }
  // In 3D (the bars layer, terrain on) the soundings are drawn at their own elevation by the custom layer, so the
  // dots draped on the terrain -- the modelled bed, not the sounding -- are hidden (Hig, 2026-10-21: "the soundings
  // should be positioned at their location in space rather than pinned to the bottom"). The hit layer stays.
  function in3d() { return !!(bars && map && map.getTerrain && map.getTerrain()); }
  function visDrape() { return shown && !in3d() ? 'visible' : 'none'; }
  function syncDrape() {
    [PT, REJ, LBL].forEach(function (id) {
      if (map && map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visDrape());
    });
  }

  function addLayers() {
    if (!map || !data || map.getSource(SRC)) return;
    // A link's ref= can arrive before the style has (isStyleLoaded() is also false
    // while tiles load, so it cannot be the test): try, and wait for the style if refused.
    try { map.addSource(SRC, { type: 'geojson', data: data }); }
    catch (e) { map.once('style.load', addLayers); return; }
    map.addLayer({ id: CON, type: 'line', source: SRC, layout: { visibility: vis() },
      filter: ['==', ['get', 'kind'], 'contour'],
      paint: { 'line-color': RAMP, 'line-width': 1.6, 'line-dasharray': [3, 2] } });
    map.addLayer({ id: PT, type: 'circle', source: SRC, layout: { visibility: visDrape() },
      filter: ['all', ['==', ['get', 'kind'], 'sounding'], ['!=', ['get', 'flag'], 'rejected']],
      paint: { 'circle-color': RAMP,
               'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 1.5, 13, 3, 16, 5.5],
               'circle-stroke-color': ['case', ['==', ['get', 'flag'], 'suspect'], '#e65100',
                                       ['==', ['get', 'depth_bound'], 'min'], '#111111', '#ffffff'],
               // ['zoom'] is legal only as the input of a TOP-LEVEL interpolate/step, so the
               // bound test goes inside the stops (nested the other way, the layer is refused).
               'circle-stroke-width': ['interpolate', ['linear'], ['zoom'],
                                       9, ['case', ['==', ['get', 'depth_bound'], 'min'], 2.5, 0.3],
                                       14, ['case', ['==', ['get', 'depth_bound'], 'min'], 2.5, 1]] } });
    map.addLayer({ id: REJ, type: 'circle', source: SRC, layout: { visibility: visDrape() },
      filter: ['all', ['==', ['get', 'kind'], 'sounding'], ['==', ['get', 'flag'], 'rejected']],
      paint: { 'circle-color': 'rgba(0,0,0,0)', 'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 2, 16, 6],
               'circle-stroke-color': '#888', 'circle-stroke-width': 1.5 } });
    // Depth labels close in, only where the style can draw text.
    var st = map.getStyle && map.getStyle();
    if (st && st.glyphs) {
      map.addLayer({ id: LBL, type: 'symbol', source: SRC, minzoom: 15.5, layout: { visibility: visDrape(),
          'text-field': ['concat', ['case', ['==', ['get', 'depth_bound'], 'min'], '≥', ''],
                         ['to-string', ['round', ['get', 'depth_m']]], ' m'],
          'text-font': ['Noto Sans Regular'], 'text-size': 10, 'text-offset': [0, 1.0], 'text-anchor': 'top',
          'text-allow-overlap': false },
        filter: ['==', ['get', 'kind'], 'sounding'],
        paint: { 'text-color': '#0a1838', 'text-halo-color': '#fff', 'text-halo-width': 1.2 } });
    }
    map.addLayer({ id: HIT, type: 'circle', source: SRC, layout: { visibility: vis() },
      paint: { 'circle-color': '#000', 'circle-opacity': 0, 'circle-radius': 8 } });
    if (bars) map.addLayer(barLayer());
    wire();
  }

  // ---- 3D: each sounding as a bar from its lake's surface down to the bed --
  // (Hig, 2026-10-12, for /lidar/: "show in their appropriate point in space --
  // a bar down from the lake surface, so the dots appear even at 100% lidar
  // opacity, but as vertical traces you can see through if the opacity is
  // reduced".)  The dot at the top is the circle layer, draped on the terrain
  // at the water surface.  The bars are a custom WebGL layer drawn WITHOUT the
  // depth test -- they are under the terrain, so with it they could never show
  // -- at an opacity the host sets from the lidar opacity (setSeeThrough), so
  // they appear as the surface is faded and are gone when it is opaque (the markers below, since 2026-10-21,
  // are drawn always).  A bar
  // runs from level x exaggeration to bed x exaggeration: MapLibre's terrain
  // height is the DEM value times the exaggeration, no offset (5.24,
  // Terrain.getElevation), and custom layers share its projection.  Only with
  // terrain on; in 2D the dots are the whole story.
  function hexRgb(h) { return [parseInt(h.slice(1, 3), 16) / 255, parseInt(h.slice(3, 5), 16) / 255, parseInt(h.slice(5, 7), 16) / 255]; }
  var STOPS = []; for (var si = 3; si < RAMP.length; si += 2) STOPS.push([RAMP[si], hexRgb(RAMP[si + 1])]);
  function rampRgb(d) {
    if (d <= STOPS[0][0]) return STOPS[0][1];
    for (var i = 1; i < STOPS.length; i++) {
      if (d <= STOPS[i][0]) {
        var t = (d - STOPS[i - 1][0]) / (STOPS[i][0] - STOPS[i - 1][0]), a = STOPS[i - 1][1], b = STOPS[i][1];
        return [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), a[2] + t * (b[2] - a[2])];
      }
    }
    return STOPS[STOPS.length - 1][1];
  }
  // The markers (2026-10-21): one point sprite per sounding at its own elevation, drawn without the depth test like
  // the bars (a sounding below the modelled bed would otherwise vanish, and one above it -- a lower bound the fill
  // went deeper than -- is exactly what should be seen), always while the layer is on. Kind: 0 a sounding (disc in
  // the depth ramp, white rim), 1 a LOWER BOUND (a down-pointing arrow, dark rim: the bed is somewhere below),
  // 2 rejected (a grey ring). Size follows the zoom like the 2D circles.
  function barLayer() {
    var prog = null, buf = null, n = 0, loc = {};
    var VS = 'attribute vec3 a_pos; attribute vec3 a_col; attribute float a_kind; uniform mat4 u_matrix; uniform float u_ex;' +
             'uniform float u_size; varying vec3 v_col; varying float v_kind;' +
             'void main() { gl_Position = u_matrix * vec4(a_pos.xy, a_pos.z * u_ex, 1.0);' +
             ' gl_PointSize = a_kind > 0.5 && a_kind < 1.5 ? u_size * 1.5 : u_size; v_col = a_col; v_kind = a_kind; }';
    var FS = 'precision mediump float; uniform float u_alpha; uniform float u_dot; varying vec3 v_col; varying float v_kind;' +
             'void main() { vec3 col = v_col;' +
             ' if (u_dot > 0.5) { vec2 c = gl_PointCoord - 0.5; float r = length(c);' +
             '  if (v_kind > 1.5) { if (r > 0.5 || r < 0.32) discard; col = vec3(0.55); }' +
             '  else if (v_kind > 0.5) { float y = gl_PointCoord.y, x = abs(gl_PointCoord.x - 0.5);' +
             '   float w = 0.48 * (0.95 - y) / 0.85; if (y < 0.1 || y > 0.95 || x > w) discard;' +
             '   float wi = 0.48 * (0.95 - 0.16 - y) / 0.85; if (y < 0.2 || x > wi) col = vec3(0.07); }' +
             '  else { if (r > 0.5) discard; if (r > 0.36) col = vec3(1.0); } }' +
             ' gl_FragColor = vec4(col * u_alpha, u_alpha); }';
    function build(gl) {
      // two vertices per sounding (surface, bed) for the bars, then every sounding again as a marker
      var top = [], mk = [];
      data.features.forEach(function (f) {
        var p = f.properties;
        if (p.kind !== 'sounding' || p.z == null) return;
        var ll = f.geometry.coordinates, c = rampRgb(p.depth_m), lv = levels[p.level_id];
        var kind = p.flag === 'rejected' ? 2 : (p.depth_bound === 'min' ? 1 : 0);
        var b = maplibregl.MercatorCoordinate.fromLngLat(ll, p.z);
        if (p.flag === 'rejected') c = [0.55, 0.55, 0.55];
        if (lv && lv.level_m != null) {
          var a = maplibregl.MercatorCoordinate.fromLngLat(ll, lv.level_m);
          top.push(a.x, a.y, a.z, c[0], c[1], c[2], kind, b.x, b.y, b.z, c[0], c[1], c[2], kind);
        }
        mk.push(b.x, b.y, b.z, c[0], c[1], c[2], kind);
      });
      n = top.length / 14; nm = mk.length / 7;
      buf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(top.concat(mk)), gl.STATIC_DRAW);
    }
    var nm = 0;
    function sh(gl, type, src) {
      var o = gl.createShader(type); gl.shaderSource(o, src); gl.compileShader(o);
      if (!gl.getShaderParameter(o, gl.COMPILE_STATUS)) console.error('soundings 3D shader:', gl.getShaderInfoLog(o));
      return o;
    }
    function sizeFor(zm) { return zm <= 9 ? 3 : zm >= 16 ? 12 : zm <= 13 ? 3 + (zm - 9) * 0.9 : 6.6 + (zm - 13) * 1.8; }
    return {
      id: BARS, type: 'custom', renderingMode: '3d',
      onAdd: function (m, gl) {
        prog = gl.createProgram();
        gl.attachShader(prog, sh(gl, gl.VERTEX_SHADER, VS)); gl.attachShader(prog, sh(gl, gl.FRAGMENT_SHADER, FS));
        gl.linkProgram(prog);
        ['a_pos', 'a_col', 'a_kind'].forEach(function (k) { loc[k] = gl.getAttribLocation(prog, k); });
        ['u_matrix', 'u_ex', 'u_alpha', 'u_dot', 'u_size'].forEach(function (k) { loc[k] = gl.getUniformLocation(prog, k); });
        build(gl);
        syncDrape();
      },
      render: function (gl, args) {
        var t = map.getTerrain && map.getTerrain();
        if (!shown || !t || !nm) return;
        var M = (args && args.defaultProjectionData && args.defaultProjectionData.mainMatrix) || args.modelViewProjectionMatrix || args;
        gl.useProgram(prog);
        gl.uniformMatrix4fv(loc.u_matrix, false, new Float32Array(M));   // MapLibre hands over a Float64Array
        gl.uniform1f(loc.u_ex, t.exaggeration || 1);
        gl.uniform1f(loc.u_size, sizeFor(map.getZoom()) * (window.devicePixelRatio || 1));
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.enableVertexAttribArray(loc.a_pos); gl.enableVertexAttribArray(loc.a_col); gl.enableVertexAttribArray(loc.a_kind);
        gl.vertexAttribPointer(loc.a_pos, 3, gl.FLOAT, false, 28, 0);
        gl.vertexAttribPointer(loc.a_col, 3, gl.FLOAT, false, 28, 12);
        gl.vertexAttribPointer(loc.a_kind, 1, gl.FLOAT, false, 28, 24);
        gl.disable(gl.DEPTH_TEST);
        gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
        if (n && seeThrough >= 0.02) {
          gl.uniform1f(loc.u_alpha, seeThrough);
          gl.uniform1f(loc.u_dot, 0); gl.drawArrays(gl.LINES, 0, 2 * n);
        }
        gl.uniform1f(loc.u_alpha, 1);
        gl.uniform1f(loc.u_dot, 1); gl.drawArrays(gl.POINTS, 2 * n, nm);
        gl.disableVertexAttribArray(loc.a_kind);
      },
      onRemove: function (m, gl) { if (buf) gl.deleteBuffer(buf); if (prog) gl.deleteProgram(prog); buf = prog = null; }
    };
  }
  // 0 = bars hidden (the surface is opaque), 1 = fully drawn. /lidar/ passes 1 - lidar opacity.
  function setSeeThrough(a) {
    a = Math.max(0, Math.min(1, +a || 0));
    if (a === seeThrough) return;
    seeThrough = a;
    if (map && map.getLayer(BARS)) map.triggerRepaint();
    changed();
  }

  function wire() {
    if (wired) return;
    wired = true;
    if (window.LSTools && LSTools.clicks) {
      LSTools.clicks.register({ id: 'soundings', kind: 'reference', priority: 35, layers: [HIT, CON],
                                handler: function (f, e) { popup(f, e.lngLat); } });
    } else {
      map.on('click', HIT, function (e) { popup(e.features[0], e.lngLat); });
    }
    [HIT, CON].forEach(function (id) {
      map.on('mouseenter', id, function () {
        if (window.LSTools) LSTools.cursor.set('hover', 'pointer'); else map.getCanvas().style.cursor = 'pointer';
      });
      map.on('mouseleave', id, function () {
        if (window.LSTools) LSTools.cursor.clear('hover'); else map.getCanvas().style.cursor = '';
      });
    });
    map.on('style.load', function () { if (data) addLayers(); });
    map.on('terrain', syncDrape);              // 2D <-> 3D: draped dots or the markers in space
  }

  function fmtWhen(s) { return s ? s.replace('T', ' ').replace(/:\d\d(\+00:00|Z)$/, ' UTC') : ''; }

  function popup(f, lngLat) {
    if (!f) return;
    var p = f.properties, sv = surveys[p.survey_id] || {}, lv = levels[p.level_id] || null;
    var orig = (p.depth_unit && p.depth_unit !== 'm' && p.depth_orig != null) ? ' (' + p.depth_orig + ' ' + esc(p.depth_unit) + ')' : '';
    var head = p.kind === 'contour' ? 'Depth contour' : 'Sounding';
    var html = '<div class="snd-pop" style="font-size:12px;line-height:1.4;max-width:270px">' +
      '<div style="font-weight:600">' + head + (lv && lv.water_body ? ' · ' + esc(lv.water_body) : '') + '</div>' +
      '<div>' + (p.depth_bound === 'min' ? 'at least ' : '') + '<b>' + Number(p.depth_m).toFixed(1) + ' m</b> deep' + orig +
      (p.sigma_m != null ? ' <span style="color:#777">±' + Number(p.sigma_m).toFixed(1) + ' m</span>' : '') + '</div>';
    if (p.depth_bound === 'min')
      html += '<div style="color:#666;font-size:11px">A lower bound: the bed was not reached at this depth.</div>';
    if (p.z != null && lv) {
      html += '<div>bed ' + (p.depth_bound === 'min' ? 'below ' : '') + Number(p.z).toFixed(1) + ' m ' + esc(lv.datum) +
        (p.sigma_z != null ? ' <span style="color:#777">±' + Number(p.sigma_z).toFixed(1) + '</span>' : '') + '</div>' +
        '<div style="color:#666;font-size:11px">= level ' + Number(lv.level_m).toFixed(2) + ' m − depth; level: ' +
        esc(lv.source) + '</div>';
    } else {
      html += '<div style="color:#666;font-size:11px">no elevation: the water level for this depth is not known yet</div>';
    }
    if (p.flag) html += '<div style="color:' + (p.flag === 'rejected' ? '#b00020' : '#e65100') + '">' + esc(p.flag) +
      (p.flag_reason ? ': ' + esc(p.flag_reason) : '') + '</div>';
    if (p.notes) html += '<div style="white-space:pre-wrap">' + esc(p.notes) + '</div>';
    html += '<div style="color:#555;font-size:11px;margin-top:4px;border-top:1px solid #eee;padding-top:3px">' +
      esc(sv.title || '') + (p.observed_at ? '<br>' + esc(fmtWhen(p.observed_at)) : '') +
      (sv.collector ? '<br>by ' + esc(sv.collector) : '') + (sv.method ? ' · ' + esc(sv.method) : '') +
      (sv.licence ? '<br>' + esc(sv.licence) : '') + '</div></div>';
    var pop = new maplibregl.Popup({ closeButton: true, maxWidth: '290px' }).setLngLat(lngLat).setHTML(html);
    if (window.LSTools && LSTools.popup) LSTools.popup.show(pop.addTo(map)); else pop.addTo(map);
  }

  function load() {
    if (data) return Promise.resolve(data);
    if (loading) return loading;
    loading = fetch(api + 'api/soundings/', { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error('soundings: HTTP ' + r.status); return r.json(); })
      .then(function (fc) {
        data = fc; surveys = {}; levels = {};
        (fc.surveys || []).forEach(function (s) { surveys[s.id] = s; });
        (fc.levels || []).forEach(function (l) { levels[l.id] = l; });
        // MapLibre reads feature-state ids and expressions from properties; sounding ids
        // are numbers, contour ids strings -- keep them out of the top-level id.
        fc.features.forEach(function (f) { delete f.id; });
        addLayers();
        changed();
        return data;
      });
    return loading;
  }

  function init(opts) {
    map = opts.map; api = opts.api || api;
    if (opts.bars) bars = true;
    return load();
  }

  function setVisible(on) {
    shown = !!on;
    if (shown && !data && map) load();
    [CON, HIT].forEach(function (id) {
      if (map && map.getLayer(id)) map.setLayoutProperty(id, 'visibility', vis());
    });
    syncDrape();
    if (map && map.getLayer(BARS)) map.triggerRepaint();
  }

  // Soundings within maxM of a polyline, projected onto it: d along, off across.
  // Local equirectangular metres (a profile is a few km at most).
  function near(coords, maxM) {
    if (!data || !coords || coords.length < 2) return [];
    var lat0 = coords[0][1] * Math.PI / 180, kx = 111320 * Math.cos(lat0), ky = 110574;
    var P = coords.map(function (c) { return [c[0] * kx, c[1] * ky]; }), cum = [0], i;
    for (i = 1; i < P.length; i++) cum.push(cum[i - 1] + Math.hypot(P[i][0] - P[i - 1][0], P[i][1] - P[i - 1][1]));
    var out = [];
    data.features.forEach(function (f) {
      var p = f.properties;
      if (p.kind !== 'sounding' || p.z == null || p.flag === 'rejected') return;
      var q = [f.geometry.coordinates[0] * kx, f.geometry.coordinates[1] * ky], best = null;
      for (var k = 1; k < P.length; k++) {
        var ax = P[k - 1][0], ay = P[k - 1][1], bx = P[k][0] - ax, by = P[k][1] - ay, L2 = bx * bx + by * by;
        var t = L2 > 0 ? Math.max(0, Math.min(1, ((q[0] - ax) * bx + (q[1] - ay) * by) / L2)) : 0;
        var dx = q[0] - (ax + t * bx), dy = q[1] - (ay + t * by), off = Math.hypot(dx, dy);
        if (!best || off < best.off) best = { off: off, d: cum[k - 1] + t * Math.sqrt(L2) };
      }
      if (best && best.off <= maxM) out.push({ d: best.d, off: best.off, z: p.z, sigma: p.sigma_z, f: f });
    });
    return out.sort(function (a, b) { return a.d - b.d; });
  }

  return { init: init, setVisible: setVisible, setSeeThrough: setSeeThrough,
           onChange: function (fn) { listeners.push(fn); }, visible: function () { return shown; },
           all: function () { return data; }, near: near, survey: function (id) { return surveys[id]; },
           level: function (id) { return levels[id]; } };
})();
