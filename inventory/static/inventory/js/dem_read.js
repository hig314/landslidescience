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
 *   LSDemRead.tileFor(src, z, x, y)    // Promise<{w, h, data: Float32Array} | null>, cached
 *
 * A source is {id, title, encoding: 'mapbox'|'terrarium', maxzoom,
 * tileSize?, pmtiles? | tiles? | imageServer?, sea?, datum?}. Voids (alpha <
 * 255) read NaN; `sea` (Mapterhorn's 0 m) is masked by the caller via readAt().
 *
 *   LSDemRead.ARCTICDEM                 // PGC's 2 m mosaic, orthometric, read live
 *   LSDemRead.arcticCovers(lat)         // worth asking ArcticDEM here at all?
 *
 * An `imageServer` source is an ArcGIS ImageServer read as float32 TIFF
 * exports of each 256 px web-mercator tile (decoded here; ArcGIS writes them
 * uncompressed), with an optional `renderingRule`.
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

  // ArcticDEM v4.1 mosaic from the Polar Geospatial Center's public image
  // service, the same one the Shaded relief overlays shade. Read here as
  // 'Height Orthometric' (PGC's EGM2008 conversion), not the service's raw
  // ellipsoidal heights: at Barry Arm those sit ~14 m above the sea, which
  // on a profile beside NAVD88 surveys reads as a 14 m error. 2 m is about
  // z16 at these latitudes.
  var ARCTICDEM = { id: 'arcticdem', title: 'ArcticDEM 2 m (PGC)', maxzoom: 16, native: 2,
                    imageServer: 'https://di-pgc.img.arcgis.com/arcgis/rest/services/arcticdem_latest/ImageServer',
                    renderingRule: { rasterFunction: 'Height Orthometric' },
                    datum: 'EGM2008 (PGC orthometric heights)' };
  // ArcticDEM is the Arctic plus Alaska and the coast ranges south of 60°N
  // (it reaches Stewart, 56.5°N). South of 50°N there is nothing to ask for.
  function arcticCovers(lat) { return lat >= 50; }
  var NODATA = -999999;

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
  // An uncompressed float32 TIFF (strips or tiles, either byte order) ->
  // {w, h, data}; null for anything else. ArcGIS exportImage writes exactly
  // this for format=tiff&pixelType=F32 (2026-10-30: little-endian, 128 px
  // tiles), so a full TIFF library is not needed.
  function decodeTiff(buf) {
    var v = new DataView(buf);
    if (buf.byteLength < 8) return null;
    var le = v.getUint16(0) === 0x4949;
    var u16 = function (o) { return v.getUint16(o, le); }, u32 = function (o) { return v.getUint32(o, le); };
    if (u16(2) !== 42) return null;
    var ifd = u32(4), n = u16(ifd), tags = {};
    for (var i = 0; i < n; i++) {
      var e = ifd + 2 + 12 * i, tag = u16(e), type = u16(e + 2), cnt = u32(e + 4);
      var size = type === 3 ? 2 : 4, at = cnt * size <= 4 ? e + 8 : u32(e + 8), vals = [];
      for (var k = 0; k < cnt; k++) vals.push(size === 2 ? u16(at + 2 * k) : u32(at + 4 * k));
      tags[tag] = vals;
    }
    var w = tags[256][0], h = tags[257][0];
    if ((tags[259] || [1])[0] !== 1 || (tags[258] || [0])[0] !== 32 || (tags[339] || [1])[0] !== 3) return null;
    var out = new Float32Array(w * h);
    var put = function (off, bw, bh, x0, y0, rows) {
      for (var y = 0; y < rows; y++) {
        var gy = y0 + y; if (gy >= h) break;
        for (var x = 0; x < bw; x++) {
          var gx = x0 + x; if (gx >= w) continue;
          var val = v.getFloat32(off + 4 * (y * bw + x), le);
          out[gy * w + gx] = (val <= NODATA || val !== val) ? NaN : val;
        }
      }
    };
    if (tags[324]) {                         // tiled
      var tw = tags[322][0], th = tags[323][0], across = Math.ceil(w / tw);
      tags[324].forEach(function (off, t) { put(off, tw, th, (t % across) * tw, Math.floor(t / across) * th, th); });
    } else {                                 // strips
      var rps = (tags[278] || [h])[0];
      tags[273].forEach(function (off, si) { put(off, w, rps, 0, si * rps, rps); });
    }
    return { w: w, h: h, data: out };
  }
  function imageServerUrl(source, z, x, y) {
    var s = TILE_CIRC / Math.pow(2, z), x0 = -TILE_CIRC / 2 + x * s, y1 = TILE_CIRC / 2 - y * s;
    var q = 'bbox=' + [x0, y1 - s, x0 + s, y1].join(',') + '&bboxSR=3857&imageSR=3857&size=256,256' +
            '&format=tiff&pixelType=F32&noData=' + NODATA + '&noDataInterpretation=esriNoDataMatchAny' +
            '&interpolation=RSP_BilinearInterpolation&f=image';
    if (source.renderingRule) q += '&renderingRule=' + encodeURIComponent(JSON.stringify(source.renderingRule));
    return source.imageServer + '/exportImage?' + q;
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
    } else if (source.imageServer) {
      p = fetch(imageServerUrl(source, z, x, y)).then(function (r) { return r.ok ? r.arrayBuffer() : null; })
        .then(function (b) {
          var t = b && decodeTiff(b);
          if (!t) return null;
          for (var i = 0; i < t.data.length; i++) if (t.data[i] === t.data[i]) return t;
          return null;                       // all void: outside the mosaic
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

  return { MAPTERHORN: MAPTERHORN, ARCTICDEM: ARCTICDEM, arcticCovers: arcticCovers,
           decodeTiff: decodeTiff, surveySource: surveySource, archive: archive, tileFor: tileFor,
           elevationAt: elevationAt, readAt: readAt, zoomFor: zoomFor,
           metersPerPixel: metersPerPixel };
})();
