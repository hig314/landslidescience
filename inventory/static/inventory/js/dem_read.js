/* dem_read.js — read elevations from the lidar catalogue's terrain-RGB
 * PMTiles and Mapterhorn's terrarium tiles, in the page. Global
 * `window.LSDemRead`.
 *
 * Extracted 2026-10-11 from profile.js (the elevation profile and the
 * Alt-click probe on /inventory/) so the group profile on /lidar/
 * (trace_profile.js) reads the same way: one decode, one cache, one bilinear
 * rule. Needs pmtiles.js on the page; no DOM, no map.
 *
 *   LSDemRead.surveySource(props)       // a catalogue feature's properties -> source
 *   LSDemRead.MAPTERHORN                // the reference source everywhere
 *   LSDemRead.elevationAt(src, z, lon, lat)  // Promise<metres | NaN>
 *   LSDemRead.zoomFor(src, lat, step)   // deepest zoom no finer than step/2
 *   LSDemRead.metersPerPixel(lat, z, size)
 *
 * A source is {id, title, encoding: 'mapbox'|'terrarium', maxzoom,
 * tileSize?, pmtiles? | tiles?, sea?, datum?}. Voids (alpha < 255) read NaN;
 * `sea` (Mapterhorn's 0 m) is masked by the caller via readAt().
 */
window.LSDemRead = (function () {
  'use strict';

  var R = 6378137, TILE_CIRC = 2 * Math.PI * R;
  var tileCache = {};           // source id/z/x/y -> Promise<{w, h, data: Float32Array} | null>
  var archives = {};            // absolute URL -> PMTiles reader (header + directory reused)

  var MAPTERHORN = { id: 'mapterhorn', title: 'Mapterhorn (3DEP 1/3″ in Alaska)',
                     encoding: 'terrarium', maxzoom: 13, native: 10, tileSize: 512, sea: 0,
                     tiles: 'https://tiles.mapterhorn.com/{z}/{x}/{y}.webp',
                     datum: 'NAVD88 (3DEP, Alaska); EGM2008 (Copernicus, Canada)' };

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
  function archive(url) {
    var u = new URL(url, location.href).href;
    return archives[u] || (archives[u] = new pmtiles.PMTiles(u));
  }
  function surveySource(p) {
    return { id: p.id, title: p.title, encoding: 'mapbox', maxzoom: p.max_zoom,
             native: p.native_res_m || 1, datum: p.vertical_datum || 'not declared by the source',
             pmtiles: archive(p.pmtiles_url) };
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
  // elevationAt with the source's sea mask applied (Mapterhorn writes the
  // sea as exactly 0 m).
  function readAt(source, z, lon, lat) {
    return elevationAt(source, z, lon, lat).then(function (v) {
      return (source.sea !== undefined && v <= source.sea) ? NaN : v;
    });
  }
  // The finest zoom the source has, but no finer than the sample spacing
  // needs (a pixel of at least half a step).
  function zoomFor(source, lat, step) {
    var z = source.maxzoom, size = source.tileSize || 256;
    while (z > 0 && metersPerPixel(lat, z, size) < step / 2) z--;
    return z;
  }

  return { MAPTERHORN: MAPTERHORN, surveySource: surveySource, archive: archive,
           elevationAt: elevationAt, readAt: readAt, zoomFor: zoomFor,
           metersPerPixel: metersPerPixel };
})();
