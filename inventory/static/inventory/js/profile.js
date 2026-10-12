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
 * drawn. Hovering the chart marks the position on the map. The panel and the
 * chart are profile_chart.js's (shared with the group profile on /lidar/
 * since 2026-10-11); this file is the tool, the sampling and the URL state.
 *
 * Global `window.LSProfile`; map.js calls init() once the map exists and
 * hands it the lidar catalogue (which arrives later) through a getter.
 */
window.LSProfile = (function () {
  'use strict';

  var SRC = 'profile-src', PREVIEW = 'profile-preview', CURSOR = 'profile-cursor';
  var COLOR = '#00897b';
  var PALETTE = ['#d81b60', '#1e88e5', '#43a047', '#fb8c00', '#8e24aa', '#00acc1',
                 '#6d4c41', '#c0ca33', '#3949ab', '#e53935'];

  var map = null, catalog = function () { return null; }, chart = null;
  var onChange = function () {};  // host callback: the URL hash records the profile
  var pendingLine = null;         // a line from the URL, waiting for the catalogue
  var mode = 'idle';            // 'idle' | 'draw'
  var active = [];              // vertices of the line in progress
  var line = null;              // finished line, [[lng, lat], …]
  var result = null;            // {points: [{d, lon, lat}], series: [{id, title, color, z: []}]}
  var lastClick = 0, lastX = 0, lastY = 0;

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
        paint: LSProfileChart.CURSOR_PAINT });
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
    if (line) { line = null; result = null; if (chart) chart.setWindow(null); }   // a new line replaces the old
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
    onChange();
  }
  function clearAll() {
    line = null; result = null; active = []; pendingLine = null;
    setPreview([]); setCursor(null); render();
    if (chart) chart.close();
    onChange();
  }

  // ---- state for the URL hash (map.js _VIEW_PARAMS row 'pr') ---------------
  // {line: [[lon, lat], …], sel: {d0, d1} | null, locked} or null when no line.
  function state() {
    if (!line) return null;
    return { line: line.slice(), sel: chart ? chart.window() : null, locked: chart ? chart.locked() : true };
  }
  // Put a line (and optionally a window and the aspect lock) on the map and
  // read it. Before the lidar catalogue is in, the line waits: reading it
  // now would find only Mapterhorn and the surveys would never appear.
  function setState(st) {
    if (!st || !st.line || st.line.length < 2) return;
    active = []; setPreview([]);
    if (chart) {
      if (st.locked != null) chart.setLocked(st.locked);
      chart.setWindow(st.sel || null);
    }
    line = st.line.slice(); render();
    if (!catalog()) { pendingLine = line; return; }
    pendingLine = null;
    sample(line);
  }
  // map.js calls this when the catalogue arrives.
  function onCatalog() {
    if (pendingLine) { var l = pendingLine; pendingLine = null; sample(l); }
  }

  // ---- sampling ----------------------------------------------------------
  // The readers (decode, tile cache, bilinear read) are dem_read.js's, shared
  // with the group profile on /lidar/ (2026-10-11).
  var metersPerPixel = LSDemRead.metersPerPixel, elevationAt = LSDemRead.elevationAt;
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
                   pmtiles: LSDemRead.archive(p.pmtiles_url) });
      });
    }
    out.push(LSDemRead.MAPTERHORN);
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

  // One point, read from the named catalogue surveys (where their footprint
  // covers it) at their deepest zoom, plus Mapterhorn. The Alt-click probe in
  // map.js. Resolves to [{id, title, z (m or NaN), zoom, res (m/px), datum}]
  // (datum = the catalogue's vertical_datum, as the manifest words it), the
  // surveys in the order given and Mapterhorn last; a survey whose footprint
  // misses the point is left out rather than reported as a void.
  function pointElevations(lon, lat, ids) {
    var cat = catalog(), srcs = [];
    (ids || []).forEach(function (id) {
      var f = cat && cat.features.filter(function (x) { return x.properties.id === id; })[0];
      if (!f || !f.properties.pmtiles_url) return;
      if (!turf.booleanPointInPolygon([lon, lat], f)) return;
      var p = f.properties;
      srcs.push({ id: p.id, title: p.title, encoding: 'mapbox', maxzoom: p.max_zoom,
                  datum: p.vertical_datum || 'not declared by the source',
                  pmtiles: LSDemRead.archive(p.pmtiles_url) });
    });
    srcs.push(LSDemRead.MAPTERHORN);
    return Promise.all(srcs.map(function (s) {
      return elevationAt(s, s.maxzoom, lon, lat).then(function (v) {
        if (s.sea !== undefined && v <= s.sea) v = NaN;
        return { id: s.id, title: s.title, z: v, zoom: s.maxzoom, datum: s.datum,
                 res: metersPerPixel(lat, s.maxzoom, s.tileSize || 256) };
      });
    }));
  }

  // ---- panel + chart (profile_chart.js) ---------------------------------
  function showPanel() { if (chart) chart.open(); }
  function status(msg) { if (chart) chart.setStatus(msg); }
  var fmtD = LSProfileChart.fmtD, esc = LSProfileChart.esc;
  // Mapterhorn last: drawn on top of the surveys, dashed, last in the legend.
  function ordered() {
    return result.series.slice().sort(function (a, b) { return a.id === 'mapterhorn' ? 1 : b.id === 'mapterhorn' ? -1 : 0; });
  }
  function draw() {
    if (!result || !chart) return;
    var series = ordered();
    chart.setData({ length: result.length, series: series.map(function (s) {
      return { id: s.id, label: s.title, short: s.id, color: s.color,
               dash: s.id === 'mapterhorn' ? '4 3' : null, width: s.id === 'mapterhorn' ? 1.2 : 1.8,
               runs: [result.points.map(function (q, k) { return { d: q.d, z: s.z[k] }; })] };
    }) });
    status(fmtD(result.length) + ' · ' + series.length + ' DEM' + (series.length === 1 ? '' : 's') +
           (result.pending ? ' · reading ' + result.pending + ' more…' : '') +
           ' · ' + result.points.length + ' samples every ' + (result.step < 10 ? result.step.toFixed(1) : Math.round(result.step)) + ' m');
  }
  // A vertical slice of the chart: every DEM at one distance.
  function tip(hit) {
    var k = Math.min(result.points.length - 1, Math.round(hit.d / result.step));
    var q = result.points[k], txt = fmtD(q.d), rows = [];
    result.series.forEach(function (s) {
      var has = !isNaN(s.z[k]);
      if (has) txt += ' · ' + s.id + ' ' + s.z[k].toFixed(1) + ' m';
      rows.push('<div><span class="profile-tip-sw" style="background:' + s.color + '"></span>' +
                esc(s.title) + '<b>' + (has ? s.z[k].toFixed(1) + ' m' : '—') + '</b></div>');
    });
    return { read: txt, html: '<div class="profile-tip-d">' + fmtD(q.d) + ' along the line</div>' + rows.join('') };
  }
  function onHover(hit) {
    if (!hit || !result) { setCursor(null); return; }
    var q = result.points[Math.min(result.points.length - 1, Math.round(hit.d / result.step))];
    setCursor([q.lon, q.lat]);
  }

  // ---- CSV ---------------------------------------------------------------
  function stamp() { return new Date().toISOString().slice(0, 10).replace(/-/g, ''); }
  function csv() {
    if (!result) return null;
    var cols = ['distance_m', 'lon', 'lat'].concat(result.series.map(function (s) { return s.id + '_m'; }));
    var rows = [cols.join(',')];
    result.points.forEach(function (q, k) {
      var r = [q.d.toFixed(1), q.lon.toFixed(6), q.lat.toFixed(6)];
      result.series.forEach(function (s) { r.push(isNaN(s.z[k]) ? '' : s.z[k].toFixed(2)); });
      rows.push(r.join(','));
    });
    rows.push('# ' + result.series.map(function (s) { return s.id + ': ' + s.title + ' (z' + s.zoom + ')'; }).join('; '));
    var w = chart.window();
    if (w) {
      rows.push('# window ' + w.d0.toFixed(1) + '-' + w.d1.toFixed(1) + ' m: id, n, mean_m, slope_deg, slope_pct, curvature_per_km (least-squares line / parabola)');
      chart.data().series.forEach(function (s) {
        var st = chart.stats(s);
        if (st && st.n >= 2) rows.push('# ' + [s.id, st.n, st.mean.toFixed(2), st.slopeDeg.toFixed(2), st.slopePct.toFixed(2), isNaN(st.curvature) ? '' : st.curvature.toFixed(3)].join(', '));
      });
    }
    return { name: 'profile_' + stamp() + '.csv', text: rows.join('\n') + '\n' };
  }

  // ---- init --------------------------------------------------------------
  function init(opts) {
    map = opts.map;
    catalog = opts.catalog || catalog;
    onChange = opts.onChange || onChange;
    var panel = document.getElementById('profile-panel');
    if (!panel) return;
    chart = LSProfileChart.create({
      panel: panel, makePanel: opts.makePanel, hover: 'column', statsName: 'DEM',
      tip: tip, onHover: onHover, onChange: onChange, csv: csv,
      svgName: function () { return 'profile_' + stamp() + '.svg'; },
      // The line belongs to the panel: closing the panel takes the line, the
      // vertices and the hover marker off the map too. Leaving the tool does
      // not -- the chart is still open and still hoverable.
      onClose: function () {
        line = null; result = null; active = []; pendingLine = null;
        setPreview([]); setCursor(null); render();
        onChange();
      }
    });
    chart.setStatus('Elevation profile');

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

  return { init: init, clear: clearAll, state: state, setState: setState, onCatalog: onCatalog,
           pointElevations: pointElevations };
})();
