/* The sample tool: a circle on the map, and two ways to read what is inside it.
 *
 * One circle, shared by whoever asks (the analysis panel, the permafrost
 * detail panel). Arm the tool and a ghost of the circle follows the cursor
 * at the current radius -- the footprint the next click would read; click
 * and the circle is set there, and the tool STAYS armed so the next click
 * moves it (Hig, 2026-10-10: "users can click in different places to see
 * what they get"). Esc, or the panel's button, puts the tool away -- AND
 * THE SAMPLE WITH IT: the circle exists only while the tool is on (Hig,
 * same day), so a sample arriving by URL arms the tool. The circle is
 * drawn on the map and rides the URL as smp=lat,lon,r_km (map.js
 * _VIEW_PARAMS). Registered as an LSTools mode with no toolbar button:
 * the sampler belongs to the panels that use it (Hig, 2026-10-09).
 *
 * Two readers, chosen by the caller per quantity:
 *   cells(keys)  -- the lattice cells of the quantity cube inside the
 *                   circle, from api/analysis/sample/ (inventory/analysis.py;
 *                   1 km EPSG:3338, the same cells every density is drawn
 *                   from). Works for every quantity; ~300 cells at 10 km.
 *   fine(names)  -- the 60 m value archives read in the browser
 *                   (/overlays/pfv_<field>.pmtiles, tools/permafrost/
 *                   bake_values.py: terrain-RGB of value x scale at z10
 *                   only; the colour overlays are lossy WebP and cannot be
 *                   read back). Slope, aspect and northness are derived from
 *                   the elevation tile here rather than stored: at 60 m they
 *                   are high-frequency by nature and did not compress.
 *                   Thousands of cells at 10 km, subsampled to KEEP.
 *
 * Global `window.LSSample`; map.js calls init() once the map exists.
 */
window.LSSample = (function () {
  'use strict';

  var Z = 10, KEEP = 9000, R_MIN = 0.5, R_MAX = 30;
  // The fine fields: an archive (terrain-RGB / scale) or a derivation from one.
  var FINE = {
    elev60:   { archive: 'pfv_elev60', scale: 0.1 },
    maat60:   { archive: 'pfv_maat60', scale: 10 },
    pzi60:    { archive: 'pfv_pzi60',  scale: 1000 },
    magt60:   { archive: 'pfv_magt60', scale: 10 },
    prob60:   { archive: 'pfv_prob60', scale: 1000 },
    slope60:  { derive: 'slope',  from: 'elev60' },
    aspect60: { derive: 'aspect', from: 'elev60' },
    north60:  { derive: 'north',  from: 'elev60' }
  };
  var SRC = 'ls-sample', GHOST = 'ls-sample-ghost', COLOR = '#ff8f00';

  var map = null, apiBase = '', onArm = function () {};
  var circle = null, pending = null, listeners = [];
  var armed = false, radius = 10, ghostAt = null;
  var archives = {}, tileCache = {};

  function init(opts) {
    map = opts.map; apiBase = opts.apiBase || ''; onArm = opts.onArm || onArm;
    LSTools.mode({
      id: 'sample', order: 26, label: '◎', className: 'ls-tool-hidden',
      title: 'Sample: click the map to read the values inside a circle (Esc exits)',
      onSelect: function () { if (LSTools.claim('sample')) setArmed(true); },
      onRelease: function () { LSTools.release('sample'); setArmed(false); },
      cancel: function () { LSTools.release('sample'); setArmed(false); }
    });
    LSTools.clicks.register({ id: 'sample', kind: 'action', holders: ['sample'],
                              handler: function (_f, e) { onMapClick(e); } });
    map.on('mousemove', function (e) { if (armed) { ghostAt = e.lngLat; renderGhost(); } });
    map.on('mouseout', function () { if (ghostAt) { ghostAt = null; renderGhost(); } });
    map.on('style.load', ensureLayers);
    if (map.isStyleLoaded()) ensureLayers();
    if (pending) { var p = pending; pending = null; set(p.lon, p.lat, p.r); arm(p.r); }
  }

  // ---- the circle --------------------------------------------------------------
  function fc(f) { return { type: 'FeatureCollection', features: f }; }
  function ensureLayers() {
    if (!map.getSource(SRC)) map.addSource(SRC, { type: 'geojson', data: fc([]) });
    if (!map.getLayer(SRC + '-fill')) {
      map.addLayer({ id: SRC + '-fill', type: 'fill', source: SRC, paint: { 'fill-color': COLOR, 'fill-opacity': 0.08 } });
      map.addLayer({ id: SRC + '-line', type: 'line', source: SRC, paint: { 'line-color': COLOR, 'line-width': 2, 'line-dasharray': [3, 2] } });
    }
    // The ghost: the footprint under the cursor while the tool is armed.
    if (!map.getSource(GHOST)) map.addSource(GHOST, { type: 'geojson', data: fc([]) });
    if (!map.getLayer(GHOST)) {
      map.addLayer({ id: GHOST, type: 'line', source: GHOST, paint: { 'line-color': COLOR, 'line-width': 1.5, 'line-opacity': 0.7, 'line-dasharray': [2, 2] } });
    }
    render(); renderGhost();
  }
  function disc(lon, lat, r) { return turf.circle([lon, lat], r, { units: 'kilometers', steps: 72 }); }
  function render() {
    if (!map || !map.getSource(SRC)) return;
    map.getSource(SRC).setData(fc(circle ? [disc(circle.lon, circle.lat, circle.r)] : []));
  }
  function renderGhost() {
    if (!map || !map.getSource(GHOST)) return;
    map.getSource(GHOST).setData(fc(armed && ghostAt ? [disc(ghostAt.lng, ghostAt.lat, radius)] : []));
  }
  function emit() { listeners.forEach(function (fn) { fn(circle); }); }
  function set(lon, lat, r) {
    r = Math.max(R_MIN, Math.min(R_MAX, r || radius));
    if (!map) { pending = { lon: lon, lat: lat, r: r }; return; }
    circle = { lon: lon, lat: lat, r: r };
    render(); emit();
  }
  function clear() { if (!circle) return; circle = null; render(); emit(); }
  function get() { return circle; }
  function on(fn) { listeners.push(fn); }
  // Inside the circle? (lon, lat), metres on a local flat earth -- fine at 30 km.
  function inside(lon, lat) {
    if (!circle) return false;
    var dy = (lat - circle.lat) * 111320, dx = (lon - circle.lon) * 111320 * Math.cos(circle.lat * Math.PI / 180), rm = circle.r * 1000;
    return dx * dx + dy * dy <= rm * rm;
  }

  // ---- the tool ----------------------------------------------------------------
  function setArmed(on) {
    armed = on;
    if (on) LSTools.cursor.set('tool', 'crosshair');
    else { LSTools.cursor.clear('tool'); ghostAt = null; clear(); }   // the sample goes with the tool
    renderGhost();
    onArm(on);
  }
  function arm(r) { if (r) radius = r; if (LSTools.claim('sample')) setArmed(true); }
  function disarm() { if (armed) { LSTools.release('sample'); setArmed(false); } }
  function isArmed() { return armed; }
  function setRadius(r) { radius = Math.max(R_MIN, Math.min(R_MAX, r || radius)); renderGhost(); }
  // Each click sets the sample there; the tool stays armed for the next one.
  function onMapClick(e) {
    if (!armed) return;
    set(e.lngLat.lng, e.lngLat.lat, radius);
    window.LSTrack && LSTrack.event('map_tool', { tool: 'sample' });
  }

  // ---- reader 1: the cube's lattice cells ---------------------------------------
  function cells(keys) {
    if (!circle) return Promise.resolve(null);
    return fetch(apiBase + 'api/analysis/sample/?lon=' + circle.lon + '&lat=' + circle.lat + '&r=' + circle.r + '&q=' + keys.join(','))
      .then(function (r) { return r.json(); });
  }

  // ---- reader 2: the 60 m value archives --------------------------------------------
  function archive(name) {
    if (!archives[name]) archives[name] = new pmtiles.PMTiles(location.origin + '/overlays/' + name + '.pmtiles');
    return archives[name];
  }
  function tileXY(lon, lat, z) {
    var n = Math.pow(2, z), la = lat * Math.PI / 180;
    return [(lon + 180) / 360 * n, (1 - Math.log(Math.tan(la) + 1 / Math.cos(la)) / Math.PI) / 2 * n];
  }
  function decode(bitmap, scale) {
    var c = document.createElement('canvas'); c.width = bitmap.width; c.height = bitmap.height;
    var ctx = c.getContext('2d'); ctx.drawImage(bitmap, 0, 0);
    var img = ctx.getImageData(0, 0, c.width, c.height).data, out = new Float32Array(c.width * c.height);
    for (var i = 0, j = 0; i < img.length; i += 4, j++) {
      out[j] = img[i + 3] < 255 ? NaN : (-10000 + (img[i] * 65536 + img[i + 1] * 256 + img[i + 2]) * 0.1) / scale;
    }
    return { w: c.width, h: c.height, data: out };
  }
  function tile(field, x, y) {
    var key = field + '/' + x + '/' + y, f = FINE[field];
    if (!tileCache[key]) {
      tileCache[key] = archive(f.archive).getZxy(Z, x, y).then(function (r) {
        return (r && r.data) ? createImageBitmap(new Blob([r.data])).then(function (bm) { return decode(bm, f.scale); }) : null;
      }).catch(function () { return null; });
    }
    return tileCache[key];
  }
  // Slope (deg), aspect (deg clockwise from north, downslope direction; flat
  // = 0 like gdaldem -zero_for_flat) and northness = cos(aspect)·sin(slope)
  // for every pixel of an elevation tile: a 3x3 central-difference gradient,
  // one-sided at the tile edge. Matches the sign convention of the baked
  // 60 m rasters (gdaldem): a north-facing slope has aspect 0 and +northness.
  function terrain(t, lat) {
    var w = t.w, h = t.h, d = t.data, slope = new Float32Array(w * h), aspect = new Float32Array(w * h), north = new Float32Array(w * h);
    var px = 40075016.686 * Math.cos(lat * Math.PI / 180) / (w * Math.pow(2, Z));
    for (var y = 0; y < h; y++) for (var x = 0; x < w; x++) {
      var i = y * w + x;
      var x0 = Math.max(0, x - 1), x1 = Math.min(w - 1, x + 1), y0 = Math.max(0, y - 1), y1 = Math.min(h - 1, y + 1);
      var dzdx = (d[y * w + x1] - d[y * w + x0]) / ((x1 - x0) * px);      // east
      var dzdy = (d[y0 * w + x] - d[y1 * w + x]) / ((y1 - y0) * px);      // north
      if (isNaN(dzdx) || isNaN(dzdy)) { slope[i] = aspect[i] = north[i] = NaN; continue; }
      var s = Math.atan(Math.hypot(dzdx, dzdy));
      var a = (dzdx === 0 && dzdy === 0) ? 0 : Math.atan2(-dzdx, -dzdy);  // downslope azimuth, 0 = north
      if (a < 0) a += 2 * Math.PI;
      slope[i] = s * 180 / Math.PI; aspect[i] = a * 180 / Math.PI; north[i] = Math.cos(a) * Math.sin(s);
    }
    return { slope: slope, aspect: aspect, north: north };
  }
  // Every 60 m cell inside the circle for the named fine fields, as parallel
  // arrays {n, lon, lat, <name>...}, subsampled to KEEP.
  function fine(names) {
    if (!circle) return Promise.resolve(null);
    var lon = circle.lon, lat = circle.lat, r = circle.r;
    var fields = [], derived = [];
    names.forEach(function (nm) {
      var f = FINE[nm]; if (!f) return;
      if (f.derive) { derived.push(nm); if (fields.indexOf(f.from) < 0) fields.push(f.from); }
      else if (fields.indexOf(nm) < 0) fields.push(nm);
    });
    if (derived.length && fields.indexOf('elev60') < 0) fields.push('elev60');
    var n = Math.pow(2, Z);
    var dlat = r / 111.32, dlon = r / (111.32 * Math.cos(lat * Math.PI / 180));
    var t0 = tileXY(lon - dlon, lat + dlat, Z), t1 = tileXY(lon + dlon, lat - dlat, Z);
    var jobs = [];
    for (var tx = Math.floor(t0[0]); tx <= Math.floor(t1[0]); tx++)
      for (var ty = Math.floor(t0[1]); ty <= Math.floor(t1[1]); ty++)
        (function (tx, ty) {
          jobs.push(Promise.all(fields.map(function (f) { return tile(f, tx, ty); })).then(function (ts) { return { tx: tx, ty: ty, ts: ts }; }));
        })(tx, ty);
    return Promise.all(jobs).then(function (tiles) {
      var out = { lon: [], lat: [] };
      fields.forEach(function (f) { out[f] = []; }); derived.forEach(function (f) { out[f] = []; });
      var cosl = Math.cos(lat * Math.PI / 180), rm = r * 1000, ei = fields.indexOf('elev60');
      tiles.forEach(function (t) {
        var ref = t.ts[0]; if (!ref) return;
        var w = ref.w, h = ref.h, tr = (derived.length && t.ts[ei]) ? terrain(t.ts[ei], lat) : null;
        for (var py = 0; py < h; py++) {
          var ylat = (2 * Math.atan(Math.exp(Math.PI * (1 - 2 * (t.ty + (py + 0.5) / h) / n))) - Math.PI / 2) * 180 / Math.PI;
          var dy = (ylat - lat) * 111320;
          if (Math.abs(dy) > rm) continue;
          for (var px = 0; px < w; px++) {
            var xlon = (t.tx + (px + 0.5) / w) / n * 360 - 180;
            var dx = (xlon - lon) * 111320 * cosl;
            if (dx * dx + dy * dy > rm * rm) continue;
            var i = py * w + px;
            if (isNaN(ref.data[i])) continue;
            fields.forEach(function (f, k) { out[f].push(t.ts[k] ? t.ts[k].data[i] : NaN); });
            derived.forEach(function (f) { out[f].push(tr ? tr[FINE[f].derive][i] : NaN); });
            out.lon.push(xlon); out.lat.push(ylat);
          }
        }
      });
      out.n = out.lon.length;
      if (out.n > KEEP) {
        var step = out.n / KEEP, idx = [];
        for (var s = 0; s < out.n; s += step) idx.push(Math.floor(s));
        Object.keys(out).forEach(function (k) { if (Array.isArray(out[k])) out[k] = idx.map(function (i) { return out[k][i]; }); });
      }
      return out;
    });
  }

  // ---- URL state (map.js _VIEW_PARAMS row 'smp') ---------------------------------------
  function state() { return circle ? { lon: circle.lon, lat: circle.lat, r: circle.r } : null; }
  function setState(st) {
    if (!st) { clear(); return; }
    radius = st.r;
    set(st.lon, st.lat, st.r);
    if (map) arm(st.r);
  }

  return { init: init, FINE: FINE, set: set, clear: clear, get: get, on: on, inside: inside,
           arm: arm, disarm: disarm, isArmed: isArmed, setRadius: setRadius, cells: cells, fine: fine, state: state, setState: setState };
})();
