/* soundings.js — bathymetric soundings and depth contours as a map layer.
 * Global `window.LSSoundings`. Used by /inventory/ (a reference layer) and
 * /lidar/ (beside the surveys); the data are inventory/soundings.py's.
 *
 *   LSSoundings.init({map, api: '/inventory/'}) -> Promise   fetch + add layers (idempotent)
 *   LSSoundings.setVisible(bool) / visible()
 *   LSSoundings.all() -> the FeatureCollection (with .surveys, .levels)
 *   LSSoundings.near(coords [[lon,lat],...], maxM) -> [{d, off, z, sigma, f}]
 *        soundings with an elevation within maxM of the line, d = metres along it
 *
 * Points are coloured by DEPTH (pale shallow → dark deep, one fixed ramp so
 * two lakes read alike); a rejected sounding is a hollow grey ring, kept on the
 * map because the record of what was measured outlives the judgement on it.
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
      CON = 'soundings-con', LBL = 'soundings-lbl';
  var map = null, api = '/inventory/', data = null, shown = false, loading = null, wired = false;
  var surveys = {}, levels = {};

  // Depth ramp (m): pale blue at the shore to near-black navy at 100 m.
  var RAMP = ['interpolate', ['linear'], ['get', 'depth_m'],
              0, '#c6e6f5', 5, '#7fc4e8', 15, '#3b8fd0', 30, '#1f5fa8', 60, '#14336e', 100, '#0a1838'];

  function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'); }
  function vis() { return shown ? 'visible' : 'none'; }

  function addLayers() {
    if (!map || !data || map.getSource(SRC)) return;
    // A link's ref= can arrive before the style has (isStyleLoaded() is also false
    // while tiles load, so it cannot be the test): try, and wait for the style if refused.
    try { map.addSource(SRC, { type: 'geojson', data: data }); }
    catch (e) { map.once('style.load', addLayers); return; }
    map.addLayer({ id: CON, type: 'line', source: SRC, layout: { visibility: vis() },
      filter: ['==', ['get', 'kind'], 'contour'],
      paint: { 'line-color': RAMP, 'line-width': 1.6, 'line-dasharray': [3, 2] } });
    map.addLayer({ id: PT, type: 'circle', source: SRC, layout: { visibility: vis() },
      filter: ['all', ['==', ['get', 'kind'], 'sounding'], ['!=', ['get', 'flag'], 'rejected']],
      paint: { 'circle-color': RAMP,
               'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 1.5, 13, 3, 16, 5.5],
               'circle-stroke-color': ['case', ['==', ['get', 'flag'], 'suspect'], '#e65100', '#ffffff'],
               'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 9, 0.3, 14, 1] } });
    map.addLayer({ id: REJ, type: 'circle', source: SRC, layout: { visibility: vis() },
      filter: ['all', ['==', ['get', 'kind'], 'sounding'], ['==', ['get', 'flag'], 'rejected']],
      paint: { 'circle-color': 'rgba(0,0,0,0)', 'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 2, 16, 6],
               'circle-stroke-color': '#888', 'circle-stroke-width': 1.5 } });
    // Depth labels close in, only where the style can draw text.
    var st = map.getStyle && map.getStyle();
    if (st && st.glyphs) {
      map.addLayer({ id: LBL, type: 'symbol', source: SRC, minzoom: 15.5, layout: { visibility: vis(),
          'text-field': ['concat', ['to-string', ['round', ['get', 'depth_m']]], ' m'],
          'text-font': ['Noto Sans Regular'], 'text-size': 10, 'text-offset': [0, 1.0], 'text-anchor': 'top',
          'text-allow-overlap': false },
        filter: ['==', ['get', 'kind'], 'sounding'],
        paint: { 'text-color': '#0a1838', 'text-halo-color': '#fff', 'text-halo-width': 1.2 } });
    }
    map.addLayer({ id: HIT, type: 'circle', source: SRC, layout: { visibility: vis() },
      paint: { 'circle-color': '#000', 'circle-opacity': 0, 'circle-radius': 8 } });
    wire();
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
  }

  function fmtWhen(s) { return s ? s.replace('T', ' ').replace(/:\d\d(\+00:00|Z)$/, ' UTC') : ''; }

  function popup(f, lngLat) {
    if (!f) return;
    var p = f.properties, sv = surveys[p.survey_id] || {}, lv = levels[p.level_id] || null;
    var orig = (p.depth_unit && p.depth_unit !== 'm' && p.depth_orig != null) ? ' (' + p.depth_orig + ' ' + esc(p.depth_unit) + ')' : '';
    var head = p.kind === 'contour' ? 'Depth contour' : 'Sounding';
    var html = '<div class="snd-pop" style="font-size:12px;line-height:1.4;max-width:270px">' +
      '<div style="font-weight:600">' + head + (lv && lv.water_body ? ' · ' + esc(lv.water_body) : '') + '</div>' +
      '<div><b>' + Number(p.depth_m).toFixed(1) + ' m</b> deep' + orig +
      (p.sigma_m != null ? ' <span style="color:#777">±' + Number(p.sigma_m).toFixed(1) + ' m</span>' : '') + '</div>';
    if (p.z != null && lv) {
      html += '<div>bed ' + Number(p.z).toFixed(1) + ' m ' + esc(lv.datum) +
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
        return data;
      });
    return loading;
  }

  function init(opts) {
    map = opts.map; api = opts.api || api;
    return load();
  }

  function setVisible(on) {
    shown = !!on;
    if (shown && !data && map) load();
    [CON, PT, REJ, LBL, HIT].forEach(function (id) {
      if (map && map.getLayer(id)) map.setLayoutProperty(id, 'visibility', vis());
    });
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

  return { init: init, setVisible: setVisible, visible: function () { return shown; },
           all: function () { return data; }, near: near, survey: function (id) { return surveys[id]; },
           level: function (id) { return levels[id]; } };
})();
