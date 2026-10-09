/* profile.js — elevation profile tool for the inventory map (mock-up, 2026-10-08).
 *
 * A mode in the LSTools group (beside measure and draw): click vertices to
 * draw a polyline, double-click or Enter to finish, Esc cancels the line in
 * progress and Esc again leaves the tool. On finish the line is sampled at a
 * regular spacing and every DEM that covers it is read at those points:
 *
 *   - every lidar / bathymetry survey in the catalogue whose footprint the
 *     line crosses (terrain-RGB PMTiles, read directly with pmtiles.js at the
 *     survey's deepest zoom, decoded here -- no server, no shading pipeline);
 *   - Mapterhorn's global terrain as the reference everywhere (3DEP 1/3
 *     arc-second in Alaska; terrarium WebP, z13).
 *
 * The result is a floating panel with one line per DEM: a "1:1" button locks
 * the vertical scale to the horizontal one (true aspect, the profile fits
 * the panel at equal scales), unlocking lets the profile fill the panel and
 * shows the vertical exaggeration it took. SVG and CSV downloads of what is
 * drawn. Hovering the chart marks the position on the map.
 *
 * Global `window.LSProfile`; map.js calls init() once the map exists and
 * hands it the lidar catalogue (which arrives later) through a getter.
 */
window.LSProfile = (function () {
  'use strict';

  var SRC = 'profile-src', PREVIEW = 'profile-preview', CURSOR = 'profile-cursor';
  var COLOR = '#00897b';
  var MAPTERHORN = 'https://tiles.mapterhorn.com/{z}/{x}/{y}.webp';
  var PALETTE = ['#d81b60', '#1e88e5', '#43a047', '#fb8c00', '#8e24aa', '#00acc1',
                 '#6d4c41', '#c0ca33', '#3949ab', '#e53935'];

  var map = null, catalog = function () { return null; }, panelApi = null;
  var mode = 'idle';            // 'idle' | 'draw'
  var active = [];              // vertices of the line in progress
  var line = null;              // finished line, [[lng, lat], …]
  var result = null;            // {points: [{d, lon, lat}], series: [{id, title, color, z: []}]}
  var locked = true;            // 1:1 aspect
  var lastClick = 0, lastX = 0, lastY = 0;
  var el = {};                  // panel elements
  var tileCache = {};           // key -> Promise<{w, h, data: Float32Array}>

  // ---- map layers --------------------------------------------------------
  function ensureLayers() {
    [SRC, PREVIEW, CURSOR].forEach(function (id) {
      if (!map.getSource(id)) map.addSource(id, { type: 'geojson', data: fc([]) });
    });
    if (!map.getLayer(PREVIEW + '-line')) {
      map.addLayer({ id: PREVIEW + '-line', type: 'line', source: PREVIEW,
        paint: { 'line-color': COLOR, 'line-width': 2, 'line-dasharray': [2, 2] } });
    }
    if (!map.getLayer(SRC + '-line')) {
      map.addLayer({ id: SRC + '-line', type: 'line', source: SRC,
        filter: ['==', ['geometry-type'], 'LineString'],
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': COLOR, 'line-width': 3 } });
    }
    if (!map.getLayer(SRC + '-pts')) {
      map.addLayer({ id: SRC + '-pts', type: 'circle', source: SRC,
        filter: ['==', ['geometry-type'], 'Point'],
        paint: { 'circle-radius': 4, 'circle-color': '#fff',
                 'circle-stroke-color': COLOR, 'circle-stroke-width': 2 } });
    }
    if (!map.getLayer(CURSOR + '-pt')) {
      map.addLayer({ id: CURSOR + '-pt', type: 'circle', source: CURSOR,
        paint: { 'circle-radius': 6, 'circle-color': '#ffeb3b',
                 'circle-stroke-color': '#333', 'circle-stroke-width': 2 } });
    }
    render();
  }
  function fc(features) { return { type: 'FeatureCollection', features: features }; }
  function render() {
    var feats = [];
    var coords = line || active;
    if (coords.length >= 2) feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: coords }, properties: {} });
    coords.forEach(function (c) { feats.push({ type: 'Feature', geometry: { type: 'Point', coordinates: c }, properties: {} }); });
    if (map.getSource(SRC)) map.getSource(SRC).setData(fc(feats));
  }
  function setPreview(coords) {
    if (!map.getSource(PREVIEW)) return;
    map.getSource(PREVIEW).setData(fc(coords.length >= 2
      ? [{ type: 'Feature', geometry: { type: 'LineString', coordinates: coords }, properties: {} }] : []));
  }
  function setCursor(lngLat) {
    if (!map.getSource(CURSOR)) return;
    map.getSource(CURSOR).setData(fc(lngLat
      ? [{ type: 'Feature', geometry: { type: 'Point', coordinates: lngLat }, properties: {} }] : []));
  }

  // ---- the tool ----------------------------------------------------------
  function setMode(m) {
    if (m === mode) return;
    mode = m;
    if (m === 'draw') {
      LSTools.cursor.set('tool', 'crosshair');
      map.doubleClickZoom.disable();
    } else {
      LSTools.cursor.clear('tool');
      map.doubleClickZoom.enable();
      active = []; setPreview([]); render();
    }
  }
  function onClick(e) {
    if (mode !== 'draw') return;
    var now = Date.now();
    if (lastClick && now - lastClick < 350 && Math.hypot(e.point.x - lastX, e.point.y - lastY) < 6) {
      lastClick = 0; finish(); return;
    }
    lastClick = now; lastX = e.point.x; lastY = e.point.y;
    if (line) { line = null; result = null; }          // a new line replaces the old
    active.push([e.lngLat.lng, e.lngLat.lat]);
    render();
  }
  function onMove(e) {
    if (mode !== 'draw' || !active.length) { setPreview([]); return; }
    setPreview([active[active.length - 1], [e.lngLat.lng, e.lngLat.lat]]);
  }
  function onKey(e) {
    if (mode !== 'draw' || e.key !== 'Enter') return;
    if (active.length >= 2) { e.preventDefault(); finish(); }
  }
  // Esc: cancel the line in progress; with none, leave the tool.
  function cancel() {
    if (active.length) { active = []; setPreview([]); render(); return; }
    LSTools.release('profile');
    setMode('idle');
  }
  function finish() {
    if (active.length < 2) return;
    line = active.slice(); active = []; setPreview([]); render();
    window.LSTrack && LSTrack.event('map_tool', { tool: 'profile' });
    sample(line);
  }
  function clearAll() {
    line = null; result = null; active = [];
    setPreview([]); setCursor(null); render();
    if (panelApi) panelApi.close();
  }

  // ---- sampling ----------------------------------------------------------
  var R = 6378137, TILE_CIRC = 2 * Math.PI * R;
  function metersPerPixel(lat, z, size) {
    return TILE_CIRC * Math.cos(lat * Math.PI / 180) / (size * Math.pow(2, z));
  }
  function tileXY(lon, lat, z) {           // fractional tile coordinates
    var n = Math.pow(2, z), la = lat * Math.PI / 180;
    return [(lon + 180) / 360 * n, (1 - Math.log(Math.tan(la) + 1 / Math.cos(la)) / Math.PI) / 2 * n];
  }
  function decode(bitmap, encoding) {
    var c = document.createElement('canvas');
    c.width = bitmap.width; c.height = bitmap.height;
    var ctx = c.getContext('2d');
    ctx.drawImage(bitmap, 0, 0);
    var img = ctx.getImageData(0, 0, c.width, c.height).data;
    var out = new Float32Array(c.width * c.height);
    for (var i = 0, j = 0; i < img.length; i += 4, j++) {
      var r = img[i], g = img[i + 1], b = img[i + 2], a = img[i + 3];
      if (a < 255) { out[j] = NaN; continue; }
      out[j] = encoding === 'terrarium'
        ? (r * 256 + g + b / 256) - 32768
        : -10000 + (r * 65536 + g * 256 + b) * 0.1;
    }
    return { w: c.width, h: c.height, data: out };
  }
  function tileFor(source, z, x, y) {
    var key = source.id + '/' + z + '/' + x + '/' + y;
    if (tileCache[key]) return tileCache[key];
    var p;
    if (source.pmtiles) {
      p = source.pmtiles.getZxy(z, x, y).then(function (res) {
        if (!res || !res.data) return null;
        return createImageBitmap(new Blob([res.data])).then(function (bm) { return decode(bm, source.encoding); });
      });
    } else {
      var url = source.tiles.replace('{z}', z).replace('{x}', x).replace('{y}', y);
      p = fetch(url).then(function (r) { return r.ok ? r.blob() : null; })
        .then(function (b) { return b ? createImageBitmap(b).then(function (bm) { return decode(bm, source.encoding); }) : null; });
    }
    p = p.catch(function () { return null; });
    tileCache[key] = p;
    return p;
  }
  // Bilinear read of one source at one point; NaN where there is no data.
  function elevationAt(source, z, lon, lat) {
    var t = tileXY(lon, lat, z), tx = Math.floor(t[0]), ty = Math.floor(t[1]);
    return tileFor(source, z, tx, ty).then(function (tile) {
      if (!tile) return NaN;
      var px = (t[0] - tx) * tile.w - 0.5, py = (t[1] - ty) * tile.h - 0.5;
      var x0 = Math.max(0, Math.min(tile.w - 1, Math.floor(px))), y0 = Math.max(0, Math.min(tile.h - 1, Math.floor(py)));
      var x1 = Math.min(tile.w - 1, x0 + 1), y1 = Math.min(tile.h - 1, y0 + 1);
      var fx = Math.max(0, Math.min(1, px - x0)), fy = Math.max(0, Math.min(1, py - y0));
      var d = tile.data, w = tile.w;
      var v00 = d[y0 * w + x0], v10 = d[y0 * w + x1], v01 = d[y1 * w + x0], v11 = d[y1 * w + x1];
      if (isNaN(v00) || isNaN(v10) || isNaN(v01) || isNaN(v11)) {
        // Nearest instead, so a point beside a void still reads.
        var v = d[Math.round(py) * w + Math.round(px)];
        return isNaN(v) ? NaN : v;
      }
      return (v00 * (1 - fx) + v10 * fx) * (1 - fy) + (v01 * (1 - fx) + v11 * fx) * fy;
    });
  }
  function sources(lineFeature, pts) {
    var out = [];
    var cat = catalog();
    var bbox = turf.bbox(lineFeature);
    if (cat) {
      cat.features.forEach(function (f) {
        var p = f.properties, b = p.bounds;
        if (!b || !p.pmtiles_url) return;
        if (b[0] > bbox[2] || b[2] < bbox[0] || b[1] > bbox[3] || b[3] < bbox[1]) return;
        // Inside the real footprint anywhere along the line?
        var inside = pts.map(function (q) { return turf.booleanPointInPolygon([q.lon, q.lat], f); });
        if (!inside.some(Boolean)) return;
        out.push({ id: p.id, title: p.title, encoding: 'mapbox', maxzoom: p.max_zoom,
                   native: p.native_res_m || 1, inside: inside,
                   pmtiles: new pmtiles.PMTiles(new URL(p.pmtiles_url, location.href).href) });
      });
    }
    out.push({ id: 'mapterhorn', title: 'Mapterhorn (3DEP 1/3″ in Alaska)', encoding: 'terrarium',
               maxzoom: 13, native: 10, tiles: MAPTERHORN, tileSize: 512, sea: 0 });
    return out;
  }
  function sample(coords) {
    var lf = turf.lineString(coords);
    var L = turf.length(lf, { units: 'meters' });
    // ~400 samples, never finer than 0.5 m, never coarser than 50 m.
    var step = Math.max(0.5, Math.min(50, L / 400));
    var pts = [];
    for (var d = 0; d <= L; d += step) {
      var c = turf.along(lf, d, { units: 'meters' }).geometry.coordinates;
      pts.push({ d: d, lon: c[0], lat: c[1] });
    }
    if (pts[pts.length - 1].d < L) {
      var e = coords[coords.length - 1];
      pts.push({ d: L, lon: e[0], lat: e[1] });
    }
    var midLat = (turf.bbox(lf)[1] + turf.bbox(lf)[3]) / 2;
    var srcs = sources(lf, pts);
    result = { length: L, step: step, points: pts, series: [], pending: srcs.length };
    showPanel();
    status('Reading ' + srcs.length + ' DEM' + (srcs.length === 1 ? '' : 's') + '…');
    srcs.forEach(function (s, i) {
      // Zoom: finest the source has, but no finer than the sample spacing needs.
      var z = s.maxzoom;
      var size = s.tileSize || 256;
      while (z > 0 && metersPerPixel(midLat, z, size) < step / 2) z--;
      var reads = pts.map(function (q, k) {
        if (s.inside && !s.inside[k]) return Promise.resolve(NaN);
        return elevationAt(s, z, q.lon, q.lat).then(function (v) {
          return (s.sea !== undefined && v <= s.sea) ? NaN : v;
        });
      });
      Promise.all(reads).then(function (zs) {
        result.series.push({ id: s.id, title: s.title, color: PALETTE[i % PALETTE.length], z: zs, zoom: z });
        result.pending--;
        draw();
      });
    });
  }

  // ---- panel + chart -----------------------------------------------------
  function showPanel() { if (panelApi) panelApi.open(); }
  function status(msg) { if (el.status) el.status.textContent = msg; }
  function fmtD(m) { return m >= 10000 ? (m / 1000).toFixed(1) + ' km' : Math.round(m) + ' m'; }
  function niceStep(span, target) {
    var raw = span / target, p = Math.pow(10, Math.floor(Math.log10(raw)));
    var f = raw / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  }
  function draw() {
    if (!result || !el.svg) return;
    var series = result.series.slice().sort(function (a, b) { return a.id === 'mapterhorn' ? 1 : b.id === 'mapterhorn' ? -1 : 0; });
    var rect = el.body.getBoundingClientRect();
    var W = Math.max(200, rect.width), H = Math.max(120, rect.height);
    var m = { l: 52, r: 14, t: 16, b: 28 };
    var iw = W - m.l - m.r, ih = H - m.t - m.b;
    var zmin = Infinity, zmax = -Infinity;
    series.forEach(function (s) { s.z.forEach(function (v) { if (!isNaN(v)) { if (v < zmin) zmin = v; if (v > zmax) zmax = v; } }); });
    if (!isFinite(zmin)) { zmin = 0; zmax = 1; }
    if (zmax - zmin < 1) { zmax = zmin + 1; }
    var L = result.length, range = zmax - zmin;
    var sx, sy, ve;
    if (locked) {
      var s = Math.min(iw / L, ih / range);     // one scale for both, the profile fits
      sx = s; sy = s; ve = 1;
    } else {
      sx = iw / L; sy = ih / range; ve = sy / sx;
    }
    var cw = sx * L, ch = sy * range;
    var ox = m.l, oy = m.t + (ih - ch) / 2;        // centre vertically when locked
    var X = function (d) { return ox + d * sx; };
    var Y = function (z) { return oy + (zmax - z) * sy; };
    var parts = [];
    parts.push('<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="#fff"/>');
    // grid + axes
    var xs = niceStep(L, 6), ys = niceStep(range, 4);
    for (var d = 0; d <= L + 1e-9; d += xs) {
      parts.push('<line x1="' + X(d) + '" y1="' + oy + '" x2="' + X(d) + '" y2="' + (oy + ch) + '" stroke="#eee"/>');
      parts.push('<text x="' + X(d) + '" y="' + (oy + ch + 14) + '" font-size="10" text-anchor="middle" fill="#555">' + fmtD(d) + '</text>');
    }
    for (var z = Math.ceil(zmin / ys) * ys; z <= zmax + 1e-9; z += ys) {
      parts.push('<line x1="' + ox + '" y1="' + Y(z) + '" x2="' + (ox + cw) + '" y2="' + Y(z) + '" stroke="#eee"/>');
      parts.push('<text x="' + (ox - 4) + '" y="' + (Y(z) + 3) + '" font-size="10" text-anchor="end" fill="#555">' + Math.round(z) + '</text>');
    }
    parts.push('<rect x="' + ox + '" y="' + oy + '" width="' + cw + '" height="' + ch + '" fill="none" stroke="#999"/>');
    parts.push('<text x="' + (ox - 36) + '" y="' + (oy + ch / 2) + '" font-size="10" fill="#555" text-anchor="middle" transform="rotate(-90 ' + (ox - 36) + ' ' + (oy + ch / 2) + ')">elevation (m)</text>');
    // lines: a gap where there is no data
    series.forEach(function (s) {
      var dstr = '', pen = false;
      result.points.forEach(function (q, k) {
        var v = s.z[k];
        if (isNaN(v)) { pen = false; return; }
        dstr += (pen ? 'L' : 'M') + X(q.d).toFixed(1) + ' ' + Y(v).toFixed(1);
        pen = true;
      });
      if (dstr) parts.push('<path d="' + dstr + '" fill="none" stroke="' + s.color + '" stroke-width="' + (s.id === 'mapterhorn' ? 1.2 : 1.8) + '"' + (s.id === 'mapterhorn' ? ' stroke-dasharray="4 3"' : '') + '/>');
    });
    // legend: inside the plot when it fills the panel, below it when a 1:1
    // profile is a thin band with room to spare (the usual case at 1:1).
    var need = series.length * 13 + 6, below = (H - m.b - (oy + ch)) > need + 18;
    var ly = below ? oy + ch + 30 : oy + 12, lx = ox + 8;
    series.forEach(function (s) {
      parts.push('<line x1="' + lx + '" y1="' + ly + '" x2="' + (lx + 18) + '" y2="' + ly + '" stroke="' + s.color + '" stroke-width="2"/>');
      parts.push('<text x="' + (lx + 22) + '" y="' + (ly + 3) + '" font-size="10" fill="#333">' + esc(s.title) + '</text>');
      ly += 13;
    });
    parts.push('<text x="' + (ox + cw - 4) + '" y="' + (oy - 2) + '" font-size="10" text-anchor="end" fill="#333">' +
               (locked ? 'VE 1:1' : 'VE ×' + (ve >= 10 ? ve.toFixed(0) : ve.toFixed(1))) + '</text>');
    // hover guide (positioned by the mouse handler)
    parts.push('<line id="profile-guide" x1="0" y1="' + oy + '" x2="0" y2="' + (oy + ch) + '" stroke="#333" stroke-dasharray="2 2" visibility="hidden"/>');
    el.svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    el.svg.setAttribute('width', W); el.svg.setAttribute('height', H);
    el.svg.innerHTML = parts.join('');
    el.svg._geom = { ox: ox, sx: sx, L: L, cw: cw };
    status(fmtD(L) + ' · ' + series.length + ' DEM' + (series.length === 1 ? '' : 's') +
           (result.pending ? ' · reading ' + result.pending + ' more…' : '') +
           ' · ' + result.points.length + ' samples every ' + (result.step < 10 ? result.step.toFixed(1) : Math.round(result.step)) + ' m');
  }
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'); }
  function onChartMove(e) {
    if (!result || !el.svg._geom) return;
    var g = el.svg._geom, r = el.svg.getBoundingClientRect();
    var x = (e.clientX - r.left) * (el.svg.viewBox.baseVal.width / r.width);
    var d = (x - g.ox) / g.sx;
    var guide = document.getElementById('profile-guide');
    if (d < 0 || d > g.L) { onChartLeave(); return; }
    if (guide) { guide.setAttribute('x1', x); guide.setAttribute('x2', x); guide.setAttribute('visibility', 'visible'); }
    var k = Math.min(result.points.length - 1, Math.round(d / result.step));
    var q = result.points[k];
    setCursor([q.lon, q.lat]);
    // Footer line, and a tooltip at the cursor: one row per DEM, in its
    // colour, with the elevation at this point (a dash where it has none).
    var txt = fmtD(q.d), rows = [];
    result.series.forEach(function (s) {
      var has = !isNaN(s.z[k]);
      if (has) txt += ' · ' + s.id + ' ' + s.z[k].toFixed(1) + ' m';
      rows.push('<div><span class="profile-tip-sw" style="background:' + s.color + '"></span>' +
                esc(s.title) + '<b>' + (has ? s.z[k].toFixed(1) + ' m' : '—') + '</b></div>');
    });
    el.read.textContent = txt;
    el.tip.innerHTML = '<div class="profile-tip-d">' + fmtD(q.d) + ' along the line</div>' + rows.join('');
    el.tip.style.display = 'block';
    // Keep the box inside the panel: flip to the left of the cursor near the right edge.
    var bw = el.body.clientWidth, tw = el.tip.offsetWidth;
    var px = e.clientX - el.body.getBoundingClientRect().left;
    el.tip.style.left = (px + 14 + tw > bw ? px - tw - 10 : px + 14) + 'px';
    el.tip.style.top = Math.max(4, Math.min(el.body.clientHeight - el.tip.offsetHeight - 4,
                                e.clientY - el.body.getBoundingClientRect().top - 10)) + 'px';
  }
  function onChartLeave() {
    var guide = document.getElementById('profile-guide');
    if (guide) guide.setAttribute('visibility', 'hidden');
    setCursor(null); el.read.textContent = '';
    if (el.tip) el.tip.style.display = 'none';
  }

  // ---- downloads ---------------------------------------------------------
  function stamp() { return new Date().toISOString().slice(0, 10).replace(/-/g, ''); }
  function download(name, mime, text) {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: mime }));
    a.download = name; document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
    window.LSTrack && LSTrack.event('download', { kind: 'profile_' + name.split('.').pop() });
  }
  function downloadSvg() {
    if (!result) return;
    var clone = el.svg.cloneNode(true);
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    clone.setAttribute('font-family', 'Helvetica, Arial, sans-serif');
    var g = clone.querySelector('#profile-guide'); if (g) g.remove();
    download('profile_' + stamp() + '.svg', 'image/svg+xml',
             '<?xml version="1.0" encoding="UTF-8"?>\n' + clone.outerHTML);
  }
  function downloadCsv() {
    if (!result) return;
    var cols = ['distance_m', 'lon', 'lat'].concat(result.series.map(function (s) { return s.id + '_m'; }));
    var rows = [cols.join(',')];
    result.points.forEach(function (q, k) {
      var r = [q.d.toFixed(1), q.lon.toFixed(6), q.lat.toFixed(6)];
      result.series.forEach(function (s) { r.push(isNaN(s.z[k]) ? '' : s.z[k].toFixed(2)); });
      rows.push(r.join(','));
    });
    rows.push('# ' + result.series.map(function (s) { return s.id + ': ' + s.title + ' (z' + s.zoom + ')'; }).join('; '));
    download('profile_' + stamp() + '.csv', 'text/csv', rows.join('\n') + '\n');
  }

  // ---- init --------------------------------------------------------------
  function init(opts) {
    map = opts.map;
    catalog = opts.catalog || catalog;
    var panel = document.getElementById('profile-panel');
    if (!panel) return;
    el.body = panel.querySelector('.profile-body');
    el.svg = panel.querySelector('svg');
    el.status = panel.querySelector('.profile-status');
    el.read = panel.querySelector('.profile-read');
    el.tip = document.createElement('div');
    el.tip.className = 'profile-tip';
    el.body.appendChild(el.tip);
    el.lock = panel.querySelector('.profile-lock');
    el.lock.addEventListener('click', function () {
      locked = !locked;
      el.lock.classList.toggle('active', locked);
      el.lock.title = locked ? 'True aspect (1:1). Click to let the profile fill the panel.' : 'Vertically exaggerated to fill the panel. Click for 1:1.';
      draw();
    });
    el.lock.classList.add('active');
    panel.querySelector('.profile-svg').addEventListener('click', downloadSvg);
    panel.querySelector('.profile-csv').addEventListener('click', downloadCsv);
    el.svg.addEventListener('mousemove', onChartMove);
    el.svg.addEventListener('mouseleave', onChartLeave);
    panelApi = opts.makePanel(panel, {
      handle: panel.querySelector('.float-header'),
      close: panel.querySelector('.profile-close'),
      onResize: draw
    });

    // As with measure: the mode claims the map itself (claim releases
    // whichever tool held it, and may be refused by an open draw ring).
    LSTools.mode({
      id: 'profile', order: 25, label: '\u223F',
      title: 'Elevation profile: click vertices, double-click or Enter to finish (Esc cancels, Esc again exits)',
      onSelect: function () { if (LSTools.claim('profile')) setMode('draw'); },
      onRelease: function () { LSTools.release('profile'); setMode('idle'); },
      cancel: cancel
    });
    LSTools.clicks.register({ id: 'profile', kind: 'action', holders: ['profile'],
                              handler: function (_f, e) { onClick(e); } });
    map.on('mousemove', onMove);
    document.addEventListener('keydown', onKey);
    map.on('style.load', ensureLayers);
    if (map.isStyleLoaded()) ensureLayers();
  }

  return { init: init, clear: clearAll };
})();
