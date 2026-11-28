/* value_tiles.js: the grid stack's value tiles, coloured in the page.
 * Global `PH.ValueTiles`. Needs maplibregl and pmtiles on the page; no DOM.
 *
 * A value tile is a 256 px PNG holding numbers (prep/tiles.py):
 * n = R*65536 + G*256 + B, value = offset + n*scale, alpha 0 = no value; a
 * `log10` layer stores the logarithm. This registers the `valtile://`
 * protocol, which turns one into a picture with a colour ramp over [lo, hi],
 * so the ramp and its range are a slider rather than a rebuild.
 *
 *   PH.ValueTiles.add(entry, url)            entry = a tiles.json layer
 *   PH.ValueTiles.tileUrl(name, {lo, hi, hl: [a, b] | null})
 *   PH.ValueTiles.valueAt(name, lng, lat, zoom)   Promise<number | null>
 *   PH.ValueTiles.rampCss(name, a, b, m)     CSS gradient for a legend: the ramp over the
 *                                            fractions a..b of the bar, its end colours beyond;
 *                                            m = [from, to], where the middle colour sits, for a diverging layer
 *   PH.ValueTiles.rampRange(name)            the [lo, hi] a ramp is defined over, or null
 *
 * A `diverging` layer keeps its middle colour at zero whatever lo and hi are,
 * so the two sides can be stretched separately. `ilo` and `ihi` widen that
 * middle into a neutral band, so the colour can be spent on the extremes.
 *   PH.ValueTiles.classColor(name, code)
 *
 * `hl` keeps full colour inside [a, b] and fades the rest: the histogram's
 * brush. Decoded tiles are cached as numbers, so a range change is a recolour
 * with no fetch and no PNG decode.
 */
window.PH = window.PH || {};
PH.ValueTiles = (function () {
  var layers = {};          // name -> {entry, archive}
  var decoded = new Map();  // 'name/z/x/y' -> Promise<Float32Array | null>, in use order
  var MAX_DECODED = 160;

  var RAMPS = {
    // matplotlib viridis, magma-like heat, and a blue-white-red diverging ramp, as stops
    continuous: [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]],
    log: [[247, 251, 255], [158, 202, 225], [49, 130, 189], [8, 48, 107], [63, 0, 125]],
    diverging: [[33, 102, 172], [146, 197, 222], [247, 247, 247], [244, 165, 130], [178, 24, 43]],
    // ground lost and gained: purple below zero, green above (ColorBrewer PRGn), white at no change
    change: [[64, 0, 75], [153, 112, 171], [247, 247, 247], [90, 174, 97], [0, 68, 27]],
    // a factor of safety: dark red at `lo`, through red and orange to yellow, fading to nothing at `hi`,
    // so the range typed or dragged is the range the warm colours cover and ground above it is left clear
    // (a fourth number in a stop is its opacity, 0-255)
    safety: [[103, 0, 13], [203, 24, 29], [252, 141, 60], [254, 217, 118], [255, 255, 204, 0]],
    // The slope ramp of landslidescience.org /lidar/ (maplibre-gl-demshade DEFAULT_SLOPE_STOPS), so slope
    // reads the same in every DEM project: green to white over 0-10 degrees (benches, terraces), pale to 32,
    // then yellow 40, red 45, purple 50 (the angle of repose), black at 90. `at` places the stops, in degrees.
    slope: {at: [0, 10, 32, 40, 45, 50, 90],
            stops: [[0, 151, 8], [255, 255, 255], [235, 236, 226], [255, 236, 0], [240, 0, 0], [184, 0, 255], [0, 0, 0]]},
    // Diffusion age and the like: strongest where the value is LOW (a young, sharp edge), fading as it rises.
    // The purple arm of the landslidescience.org /inventory/ overlays (OPERA_RAMP; tools/susc_purple_ramps.py),
    // where dark means it matters more, so these layers read with the susceptibility overlays there.
    age: [[123, 50, 148, 255], [194, 165, 207, 215], [231, 212, 232, 150]],
    circular: [[196, 78, 82], [221, 204, 119], [85, 168, 104], [76, 114, 176], [129, 114, 179], [196, 78, 82]]
  };
  var CLASS_COLORS = {
    cover: {1: [0, 0, 0, 0], 2: [70, 140, 220, 200], 3: [222, 196, 132, 200], 4: [40, 80, 150, 200]},
    dggs_fos: {1: [253, 141, 60, 230], 2: [215, 25, 28, 230]},
    fos_dggs_class: {1: [253, 141, 60, 230], 2: [215, 25, 28, 230]},
    soil_group: {1: [96, 125, 139, 200], 2: [189, 189, 189, 200], 3: [102, 166, 30, 200], 4: [166, 216, 84, 200], 5: [255, 247, 188, 200], 6: [254, 227, 145, 200], 7: [254, 196, 79, 200], 8: [254, 153, 41, 200], 9: [236, 112, 20, 200], 10: [217, 95, 14, 200], 11: [204, 76, 2, 200], 12: [153, 52, 4, 200], 13: [127, 39, 4, 200], 14: [102, 37, 6, 200], 15: [63, 24, 4, 200]}
  };

  // A ramp as {stops, at}: `at` is each stop's place from 0 to 1 (even spacing unless the ramp says).
  function ramp(kind) {
    var r = RAMPS[kind] || RAMPS.continuous, stops = r.stops || r, n = stops.length - 1;
    if (!r.stops) r = {};                       // a plain list of stops: evenly spaced (an array has its own .at)
    var at = r.at ? r.at.map(function (v) { return (v - r.at[0]) / (r.at[n] - r.at[0]); })
                  : stops.map(function (_, i) { return i / n; });
    return {stops: stops, at: at, range: r.at ? [r.at[0], r.at[n]] : null};
  }
  function lut(kind) {
    var R = ramp(kind), stops = R.stops, out = new Uint8Array(256 * 4);
    function ch(s, c) { return c < 3 ? s[c] : (s.length > 3 ? s[3] : 255); }
    for (var i = 0; i < 256; i++) {
      var t = i / 255, k = 0;
      while (k < stops.length - 2 && t > R.at[k + 1]) k++;
      var f = (t - R.at[k]) / (R.at[k + 1] - R.at[k]);
      for (var c = 0; c < 4; c++) out[i * 4 + c] = Math.round(ch(stops[k], c) + (ch(stops[k + 1], c) - ch(stops[k], c)) * f);
    }
    return out;
  }
  var LUTS = {};
  Object.keys(RAMPS).forEach(function (k) { LUTS[k] = lut(k); });

  function add(entry, url) {
    layers[entry.name] = {entry: entry, archive: new pmtiles.PMTiles(url)};
  }

  function decode(name, z, x, y) {
    var key = name + '/' + z + '/' + x + '/' + y, hit = decoded.get(key);
    if (hit) { decoded.delete(key); decoded.set(key, hit); return hit; }
    var L = layers[name];
    var p = L.archive.getZxy(z, x, y).then(function (t) {
      if (!t || !t.data) return null;
      return createImageBitmap(new Blob([t.data], {type: 'image/png'}),
                               {premultiplyAlpha: 'none', colorSpaceConversion: 'none'}).then(function (bmp) {
        var cv = new OffscreenCanvas(256, 256), cx = cv.getContext('2d', {willReadFrequently: true});
        cx.drawImage(bmp, 0, 0);
        var px = cx.getImageData(0, 0, 256, 256).data, v = new Float32Array(65536);
        for (var i = 0; i < 65536; i++) {
          var j = i * 4;
          v[i] = px[j + 3] ? L.entry.offset + (px[j] * 65536 + px[j + 1] * 256 + px[j + 2]) * L.entry.scale : NaN;
        }
        return v;
      });
    });
    p.catch(function () { decoded.delete(key); });   // a failed read is asked for again, never kept
    decoded.set(key, p);
    if (decoded.size > MAX_DECODED) decoded.delete(decoded.keys().next().value);
    return p;
  }

  function paint(name, v, lo, hi, hl, ilo, ihi) {
    var e = layers[name].entry, img = new ImageData(256, 256), d = img.data, i, j;
    if (e.kind === 'class') {
      var cc = CLASS_COLORS[name] || {};
      for (i = 0; i < 65536; i++) {
        var c = cc[v[i]];
        if (c) { j = i * 4; d[j] = c[0]; d[j + 1] = c[1]; d[j + 2] = c[2]; d[j + 3] = c[3]; }
      }
      return img;
    }
    var lu = LUTS[e.ramp] || LUTS[e.kind] || LUTS.continuous;
    if (e.log10) { lo = Math.log10(lo); hi = Math.log10(hi); if (hl) hl = [Math.log10(hl[0]), Math.log10(hl[1])]; }
    var k = 255 / (hi - lo), piv = e.kind === 'diverging' && lo < 0 && hi > 0;   // zero keeps the middle colour
    for (i = 0; i < 65536; i++) {
      var x = v[i];
      if (x !== x) continue;
      // diverging: the middle colour from ilo to ihi (both 0 unless set), then each side's ramp out to lo and hi
      var t = !piv ? (x - lo) * k : x < ilo ? 127.5 * (1 - (x - ilo) / (lo - ilo)) : x > ihi ? 127.5 * (1 + (x - ihi) / (hi - ihi)) : 127.5;
      var n = Math.max(0, Math.min(255, Math.round(t))) * 4;
      j = i * 4;
      d[j] = lu[n]; d[j + 1] = lu[n + 1]; d[j + 2] = lu[n + 2];
      d[j + 3] = hl && (x < hl[0] || x > hl[1]) ? Math.min(40, lu[n + 3]) : lu[n + 3];
    }
    return img;
  }

  maplibregl.addProtocol('valtile', function (params) {
    var m = /^valtile:\/\/([a-z0-9_]+)\/(\d+)\/(\d+)\/(\d+)\?(.*)$/.exec(params.url);
    if (!m || !layers[m[1]]) return Promise.reject(new Error('valtile: unknown ' + params.url));
    var q = new URLSearchParams(m[5]);
    var hl = q.get('hl') ? q.get('hl').split(',').map(Number) : null;
    return decode(m[1], +m[2], +m[3], +m[4]).then(function (v) {
      var img = v ? paint(m[1], v, +q.get('lo'), +q.get('hi'), hl, +q.get('ilo') || 0, +q.get('ihi') || 0) : new ImageData(256, 256);
      return createImageBitmap(img).then(function (bmp) { return {data: bmp}; });
    });
  });

  function tileUrl(name, o) {
    return 'valtile://' + name + '/{z}/{x}/{y}?lo=' + o.lo + '&hi=' + o.hi + (o.hl ? '&hl=' + o.hl.join(',') : '') +
           (o.ilo || o.ihi ? '&ilo=' + (o.ilo || 0) + '&ihi=' + (o.ihi || 0) : '');
  }

  function valueAt(name, lng, lat, zoom) {
    var e = layers[name].entry, z = Math.max(e.minzoom, Math.min(e.maxzoom, Math.round(zoom)));
    var n = Math.pow(2, z), fx = (lng + 180) / 360 * n;
    var s = Math.sin(lat * Math.PI / 180), fy = (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * n;
    var tx = Math.floor(fx), ty = Math.floor(fy);
    return decode(name, z, tx, ty).then(function (v) {
      if (!v) return null;
      var x = v[Math.floor((fy - ty) * 256) * 256 + Math.floor((fx - tx) * 256)];
      return x !== x ? null : (e.log10 ? Math.pow(10, x) : x);
    });
  }

  function rampOf(name) { var e = layers[name].entry; return ramp(RAMPS[e.ramp] ? e.ramp : e.kind); }
  function rampCss(name, a, b, m) {
    var R = rampOf(name);
    if (a == null) { a = 0; b = 1; }
    return 'linear-gradient(to right,' + R.stops.map(function (c, i) {
      var t = R.at[i], p = m == null ? a + (b - a) * t : t < 0.5 ? a + (m[0] - a) * t * 2 : t > 0.5 ? m[1] + (b - m[1]) * (t * 2 - 1) : null;
      if (p === null) {                       // the middle colour spans the neutral band
        var mid = 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',1.00) ';
        return mid + (m[0] * 100).toFixed(2) + '%,' + mid + (m[1] * 100).toFixed(2) + '%';
      }
      return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + ((c.length > 3 ? c[3] : 255) / 255).toFixed(2) + ') ' +
             (p * 100).toFixed(2) + '%';
    }).join(',') + ')';
  }

  function classColor(name, code) {
    var c = (CLASS_COLORS[name] || {})[code];
    return c ? 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + (c[3] / 255).toFixed(2) + ')' : 'transparent';
  }

  return {add: add, tileUrl: tileUrl, valueAt: valueAt, rampCss: rampCss, classColor: classColor,
          rampRange: function (name) { return rampOf(name).range; },
          entry: function (name) { return layers[name] && layers[name].entry; }};
})();
