/* dem_shade.js — client-side shaded relief from the self-hosted lidar DEMs.
 *
 * Registers a MapLibre protocol, `demshade://`, that reads Mapbox terrain-RGB
 * tiles out of a PMTiles archive and renders them into an ordinary RGBA raster
 * tile. Same trick as the `operacolor` protocol in map.js, one step further:
 * that one recolors a value; this one differentiates a surface.
 *
 * WHY NOT MapLibre's built-in `hillshade` layer
 * ---------------------------------------------
 *  - It has no `hillshade-opacity` (verified against the 5.5.0 bundle — the
 *    property simply does not exist), so a hillshade can only be faded by
 *    abusing `hillshade-exaggeration`, which changes the shading itself rather
 *    than blending it. A plain `raster` layer has real `raster-opacity`.
 *  - `hillshade-illumination-altitude` is ignored by several of the
 *    `hillshade-method` variants, so a sun-altitude control silently does
 *    nothing.
 *  - It cannot do slope shade, aspect shade, or elevation banding at all.
 *
 * Computing here costs no extra storage: slope, aspect and contour banding are
 * all derivatives of the elevation tiles that are already hosted.
 *
 * TILE SEAMS: a slope needs its neighbours, so each output tile is built from a
 * 258x258 grid — the tile plus a one-pixel halo lifted off the eight
 * surrounding tiles. Without that, every tile border gets a false ridge. Absent
 * neighbours (survey edge) fall back to edge replication, which yields zero
 * slope there rather than a cliff.
 *
 * Usage:
 *   DemShade.register(map);                       // once, before addSource
 *   DemShade.addDataset('homer_2019', '/lidar/pmtiles/homer_2019.pmtiles');
 *   map.addSource('shade', { type:'raster', tiles:[DemShade.url('homer_2019', opts)],
 *                            tileSize:256, minzoom:8, maxzoom:17 });
 */
window.DemShade = (function () {
  'use strict';

  var TILE = 256;
  var PAD = TILE + 2;                       // 258 — one-pixel halo each side
  var EARTH = 40075016.686;

  var datasets = {};                        // id -> PMTiles instance

  // Two caches, because there are two distinct costs to avoid.
  //
  //  elevCache  decoded elevations, keyed by tile. Survives changes to the
  //             shading options, so dragging a slider is pure recompute with
  //             no network and no PNG decode.
  //  tileCache  finished PNG bytes, keyed by the full request URL (dataset +
  //             tile + every shading parameter). This is the one that makes
  //             flipping between two DEMs over the same ground instant: the
  //             second visit to a view is a Map lookup, not a re-render.
  //
  // Both are LRU by insertion order — re-inserting on hit moves the entry to
  // the end, and eviction takes from the front.
  var elevCache = new Map();                // "id/z/x/y" -> Float32Array(65536)
  var ELEV_MAX = 220;                       // ~58 MB of Float32 at 256^2
  var tileCache = new Map();                // full url -> ArrayBuffer (PNG)
  var TILE_MAX = 600;                       // ~30 MB at ~50 KB/tile
  var stats = { elevHit: 0, elevMiss: 0, tileHit: 0, tileMiss: 0 };
  var EMPTY = { empty: true };              // sentinel for "no data here"

  function lruGet(m, k) {
    if (!m.has(k)) return undefined;
    var v = m.get(k);
    m.delete(k); m.set(k, v);
    return v;
  }
  function lruSet(m, k, v, max) {
    if (m.size >= max) m.delete(m.keys().next().value);
    m.set(k, v);
  }

  // ---- the KBSP layer stack ----------------------------------------------
  // Reproduces the compositing Hig uses in QGIS (KBSP_Map/Spring_2026):
  //
  //     hillshade        <- grey, OVERLAY blend
  //     slope ramp       <- 60% opacity
  //     mod-5 m ramp     <- cyclic, opaque
  //
  // The two colour ramps below are lifted verbatim from that project's
  // colorrampshader stops, so a site rendered here matches one rendered there.
  // The 5 m interval is deliberately FIXED, not a slider: holding it constant
  // is what makes banding comparable between sites.
  var MOD_INTERVAL = 5;

  // Cyclic BrBG-with-black. First and last stop are the same colour, which is
  // what lets it wrap at the interval boundary without a visible seam.
  var MOD_STOPS = [
    [0.000, 0xa6, 0x61, 0x1a], [0.833, 0xdf, 0xc2, 0x7d],
    [1.667, 0xf5, 0xf5, 0xf5], [2.500, 0x80, 0xcd, 0xc1],
    [3.333, 0x01, 0x85, 0x71], [4.167, 0x00, 0x00, 0x00],
    [5.000, 0xa6, 0x61, 0x1a]
  ];

  // Slope ramp with two deliberate sensitivities: green->white across 0-10
  // resolves near-horizontal ground (benches, scarp crowns, terraces), and the
  // white->yellow->red->purple run across 32-50 brackets the angle of repose.
  var SLOPE_STOPS = [
    [0,  0x00, 0x97, 0x08], [10, 0xff, 0xff, 0xff], [32, 0xeb, 0xec, 0xe2],
    [40, 0xff, 0xec, 0x00], [45, 0xf0, 0x00, 0x00], [50, 0xb8, 0x00, 0xff],
    [90, 0x00, 0x00, 0x00]
  ];

  var LUT_N = 512;
  function buildLut(stops, lo, hi) {
    var lut = new Uint8Array(LUT_N * 3);
    for (var i = 0; i < LUT_N; i++) {
      var v = lo + (hi - lo) * i / (LUT_N - 1), k = 0;
      while (k < stops.length - 2 && v > stops[k + 1][0]) k++;
      var s0 = stops[k], s1 = stops[k + 1];
      var f = s1[0] === s0[0] ? 0 : (v - s0[0]) / (s1[0] - s0[0]);
      f = f < 0 ? 0 : f > 1 ? 1 : f;
      lut[i * 3] = s0[1] + (s1[1] - s0[1]) * f;
      lut[i * 3 + 1] = s0[2] + (s1[2] - s0[2]) * f;
      lut[i * 3 + 2] = s0[3] + (s1[3] - s0[3]) * f;
    }
    return lut;
  }
  var MOD_LUT = buildLut(MOD_STOPS, 0, MOD_INTERVAL);
  var SLOPE_LUT = buildLut(SLOPE_STOPS, 0, 90);

  // ---- defaults -----------------------------------------------------------
  var DEFAULTS = {
    az: 315,      // sun azimuth, degrees clockwise from north
    alt: 45,      // sun altitude above horizon, degrees
    hs: 1,        // hillshade strength 0..1
    bl: 0,        // hillshade blend: 0 = Overlay, 1 = normal transparency
    sl: 0,        // slope-ramp layer opacity 0..1  (QGIS project uses 0.6)
    md: 0,        // mod-5 m layer opacity 0..1
    as: 0,        // aspect-shade opacity 0..1 (extra, not in the QGIS stack)
    ve: 1         // vertical exaggeration applied to the shading only
  };

  function url(id, o) {
    o = o || {};
    var q = [];
    Object.keys(DEFAULTS).forEach(function (k) {
      q.push(k + '=' + (o[k] === undefined ? DEFAULTS[k] : o[k]));
    });
    return 'demshade://' + id + '/{z}/{x}/{y}?' + q.join('&');
  }

  function addDataset(id, pmtilesUrl) {
    if (!datasets[id]) {
      datasets[id] = new pmtiles.PMTiles(new URL(pmtilesUrl, location.href).href);
    }
    return datasets[id];
  }

  // ---- terrain-RGB -> Float32 elevations ----------------------------------
  function decodeTile(id, z, x, y) {
    var key = id + '/' + z + '/' + x + '/' + y;
    // The cache holds the in-flight PROMISE, not the finished array. A 4x4
    // block of tiles asks for 144 neighbours covering only 36 distinct tiles,
    // and they all start together — caching the resolved value would let every
    // one of those miss and decode the same tile several times over. Caching
    // the promise collapses them onto one decode each.
    var hit = lruGet(elevCache, key);
    if (hit !== undefined) { stats.elevHit++; return hit; }
    stats.elevMiss++;
    var pm = datasets[id];
    if (!pm) return Promise.resolve(null);
    var fetchOnce = function () { return pm.getZxy(z, x, y).then(function (t) {
      // undefined means the archive has no such tile: outside the footprint
      // (--skip-blank). That is a real, cacheable "empty". A rejection is a
      // failed request and must never be mistaken for it.
      if (!t) return null;
      return createImageBitmap(new Blob([t.data])).then(function (bmp) {
        var c = document.createElement('canvas');
        c.width = c.height = TILE;
        var ctx = c.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(bmp, 0, 0);
        bmp.close && bmp.close();
        var d = ctx.getImageData(0, 0, TILE, TILE).data;
        var out = new Float32Array(TILE * TILE);
        for (var i = 0, p = 0; i < out.length; i++, p += 4) {
          // alpha 0 marks nodata; NaN keeps it out of every derivative
          out[i] = d[p + 3] === 0 ? NaN
                 : -10000 + (d[p] * 65536 + d[p + 1] * 256 + d[p + 2]) * 0.1;
        }
        return out;
      });
    }); };
    // One retry absorbs the single dropped request a jittery link produces.
    // Anything worse REJECTS: MapLibre then marks the tile errored and asks
    // again on the next view change. The old `.catch(() => null)` turned a
    // network failure into a permanent blank -- the null was cached here and
    // as EMPTY in tileCache, so a hiccup blanked tiles for the whole session.
    var job = fetchOnce().catch(function () {
      return new Promise(function (r) { setTimeout(r, 400); }).then(fetchOnce);
    });
    job.catch(function () { elevCache.delete(key); });
    lruSet(elevCache, key, job, ELEV_MAX);
    return job;
  }

  // Assemble the 258x258 padded grid from the 3x3 tile neighbourhood.
  function buildPadded(id, z, x, y) {
    var jobs = [];
    for (var dy = -1; dy <= 1; dy++) {
      for (var dx = -1; dx <= 1; dx++) {
        jobs.push(decodeTile(id, z, x + dx, y + dy));
      }
    }
    return Promise.all(jobs).then(function (t) {
      var C = t[4];                          // centre tile
      if (!C) return null;
      var E = new Float32Array(PAD * PAD);
      var get = function (ti, i, j) {        // pixel (i,j) of neighbour ti
        var a = t[ti];
        return a ? a[i * TILE + j] : NaN;
      };
      // interior
      for (var i = 0; i < TILE; i++) {
        for (var j = 0; j < TILE; j++) E[(i + 1) * PAD + (j + 1)] = C[i * TILE + j];
      }
      // edges — fall back to replicating the centre tile's own edge
      for (var k = 0; k < TILE; k++) {
        var top = get(1, TILE - 1, k); E[0 * PAD + (k + 1)] = isNaN(top) ? C[k] : top;
        var bot = get(7, 0, k);
        E[(PAD - 1) * PAD + (k + 1)] = isNaN(bot) ? C[(TILE - 1) * TILE + k] : bot;
        var lef = get(3, k, TILE - 1);
        E[(k + 1) * PAD + 0] = isNaN(lef) ? C[k * TILE] : lef;
        var rig = get(5, k, 0);
        E[(k + 1) * PAD + (PAD - 1)] = isNaN(rig) ? C[k * TILE + TILE - 1] : rig;
      }
      // corners
      E[0] = get(0, TILE - 1, TILE - 1);
      E[PAD - 1] = get(2, TILE - 1, 0);
      E[(PAD - 1) * PAD] = get(6, 0, TILE - 1);
      E[PAD * PAD - 1] = get(8, 0, 0);
      for (var c = 0, idx = [0, PAD - 1, (PAD - 1) * PAD, PAD * PAD - 1]; c < 4; c++) {
        if (isNaN(E[idx[c]])) E[idx[c]] = E[PAD + 1];
      }
      return E;
    });
  }

  // ---- shading ------------------------------------------------------------
  function shade(E, z, y, o) {
    // Ground resolution at this tile's centre latitude. Web-mercator metres are
    // not ground metres, and at 60N the difference is a factor of two — get it
    // wrong and every slope angle is wrong.
    var n = Math.pow(2, z);
    var latRad = Math.atan(Math.sinh(Math.PI * (1 - 2 * (y + 0.5) / n)));
    var res = (EARTH / (TILE * n)) * Math.cos(latRad);

    var out = new Uint8ClampedArray(TILE * TILE * 4);
    var azRad = (360 - o.az + 90) * Math.PI / 180;   // math convention
    var zenRad = (90 - o.alt) * Math.PI / 180;
    var cosZen = Math.cos(zenRad), sinZen = Math.sin(zenRad);
    var eightRes = 8 * res;

    for (var i = 0; i < TILE; i++) {
      for (var j = 0; j < TILE; j++) {
        var p = (i + 1) * PAD + (j + 1);
        var c = E[p];
        var q = (i * TILE + j) * 4;
        if (isNaN(c)) { out[q + 3] = 0; continue; }

        // Horn 3x3 gradient
        var a = E[p - PAD - 1], b = E[p - PAD], cc = E[p - PAD + 1];
        var d = E[p - 1], f = E[p + 1];
        var g = E[p + PAD - 1], h = E[p + PAD], ii = E[p + PAD + 1];
        if (isNaN(a) || isNaN(b) || isNaN(cc) || isNaN(d) || isNaN(f) ||
            isNaN(g) || isNaN(h) || isNaN(ii)) {
          a = b = cc = d = f = g = h = ii = c;
        }
        var dzdx = ((cc + 2 * f + ii) - (a + 2 * d + g)) / eightRes * o.ve;
        var dzdy = ((g + 2 * h + ii) - (a + 2 * b + cc)) / eightRes * o.ve;

        var slope = Math.atan(Math.sqrt(dzdx * dzdx + dzdy * dzdy));
        var slopeDeg = slope * 180 / Math.PI;
        var aspect = Math.atan2(dzdy, -dzdx);

        // --- bottom of the stack: mod-5 m cyclic ramp ------------------------
        var r = 255, gg = 255, bb = 255, under = false;
        if (o.md > 0) {
          var m = c - Math.floor(c / MOD_INTERVAL) * MOD_INTERVAL;   // wraps
          var mi = (m / MOD_INTERVAL * (LUT_N - 1)) | 0;
          mi *= 3;
          r = mix(r, MOD_LUT[mi], o.md);
          gg = mix(gg, MOD_LUT[mi + 1], o.md);
          bb = mix(bb, MOD_LUT[mi + 2], o.md);
          under = true;
        }

        // --- slope ramp over it ---------------------------------------------
        if (o.sl > 0) {
          var si = ((slopeDeg / 90) * (LUT_N - 1)) | 0;
          si = (si < 0 ? 0 : si > LUT_N - 1 ? LUT_N - 1 : si) * 3;
          r = mix(r, SLOPE_LUT[si], o.sl);
          gg = mix(gg, SLOPE_LUT[si + 1], o.sl);
          bb = mix(bb, SLOPE_LUT[si + 2], o.sl);
          under = true;
        }

        // --- optional aspect tint (not part of the QGIS stack) ---------------
        if (o.as > 0) {
          var deg = ((450 - aspect * 180 / Math.PI) % 360 + 360) % 360;
          var sat = Math.min(1, slopeDeg / 15);   // flats stay neutral
          var rgb = hsl2rgb(deg / 360, 0.65 * sat, 0.5);
          r = mix(r, rgb[0], o.as); gg = mix(gg, rgb[1], o.as); bb = mix(bb, rgb[2], o.as);
          under = true;
        }

        // --- hillshade on top, Overlay blend --------------------------------
        if (o.hs > 0) {
          var hsv = cosZen * Math.cos(slope) +
                    sinZen * Math.sin(slope) * Math.cos(azRad - aspect);
          hsv = hsv < 0 ? 0 : hsv > 1 ? 1 : hsv;
          // Overlay keeps the ramp colours' hue while relief modulates their
          // lightness, which is the point of the QGIS stack -- but it is weak
          // wherever the underlying ramp sits near mid-grey, because Overlay is
          // an identity there. Normal transparency has uniform strength across
          // the whole tonal range and reads better on those sites, at the cost
          // of washing the ramp colours toward grey. Hence the switch.
          //
          // With nothing underneath, Overlay against white returns white, so a
          // lone hillshade always composites normally regardless of `bl`.
          if (under && !o.bl) {
            r = mix(r, overlay(r, hsv), o.hs);
            gg = mix(gg, overlay(gg, hsv), o.hs);
            bb = mix(bb, overlay(bb, hsv), o.hs);
          } else {
            var v = 255 * hsv;
            r = mix(r, v, o.hs); gg = mix(gg, v, o.hs); bb = mix(bb, v, o.hs);
          }
        }

        out[q] = r; out[q + 1] = gg; out[q + 2] = bb; out[q + 3] = 255;
      }
    }
    return out;
  }

  function mix(base, v, w) { return base * (1 - w) + v * w; }

  // Photoshop/QGIS "Overlay": base decides, top modulates. base is 0..255,
  // top is 0..1, result 0..255.
  function overlay(base, top) {
    var b = base / 255;
    return 255 * (b <= 0.5 ? 2 * b * top : 1 - 2 * (1 - b) * (1 - top));
  }

  function hsl2rgb(h, s, l) {
    var f = function (n) {
      var k = (n + h * 12) % 12;
      var A = s * Math.min(l, 1 - l);
      return 255 * (l - A * Math.max(-1, Math.min(Math.min(k - 3, 9 - k), 1)));
    };
    return [f(0), f(8), f(4)];
  }

  // ---- protocol -----------------------------------------------------------
  function loader(params) {
    // Finished-tile cache first: on a repeat view this returns without
    // touching PMTiles, the decoder, the shader, or the PNG encoder.
    var cached = lruGet(tileCache, params.url);
    if (cached !== undefined) {
      stats.tileHit++;
      // EMPTY is cached too: outside a survey's footprint every tile would
      // otherwise re-query the PMTiles directory on each pass.
      if (cached === EMPTY) return Promise.resolve({ data: null });
      // Hand out a copy — MapLibre may transfer the buffer to a worker, which
      // would neuter the cached one and make every later hit return 0 bytes.
      return Promise.resolve({ data: cached.slice(0) });
    }
    stats.tileMiss++;

    var u = new URL(params.url.replace('demshade://', 'https://d.invalid/'));
    var seg = u.pathname.split('/').filter(Boolean);   // [id, z, x, y]
    var id = u.host === 'd.invalid' ? seg[0] : u.host;
    var z = +seg[1], x = +seg[2], y = +seg[3];

    var o = {};
    Object.keys(DEFAULTS).forEach(function (k) {
      var v = u.searchParams.get(k);
      o[k] = v === null ? DEFAULTS[k] : +v;
    });

    return buildPadded(id, z, x, y).then(function (E) {
      if (!E) { lruSet(tileCache, params.url, EMPTY, TILE_MAX); return { data: null }; }
      var px = shade(E, z, y, o);
      var c = document.createElement('canvas');
      c.width = c.height = TILE;
      c.getContext('2d').putImageData(new ImageData(px, TILE, TILE), 0, 0);
      return new Promise(function (resolve, reject) {
        c.toBlob(function (blob) {
          if (!blob) { reject(new Error('demshade toBlob failed')); return; }
          blob.arrayBuffer().then(function (buf) {
            lruSet(tileCache, params.url, buf, TILE_MAX);
            resolve({ data: buf.slice(0) });
          }, reject);
        }, 'image/png');
      });
    });
  }

  function register(mapboxlike) {
    if (!maplibregl.addProtocol) return;
    maplibregl.addProtocol('demshade', loader);
  }

  return {
    register: register,
    addDataset: addDataset,
    url: url,
    defaults: DEFAULTS,
    // Exposed so the tile pipeline can be exercised without a rendered map —
    // the browser test harness runs with the tab hidden, where MapLibre never
    // fires 'load' because requestAnimationFrame is paused.
    loadTile: loader,
    stats: function () {
      return { elevHit: stats.elevHit, elevMiss: stats.elevMiss,
               tileHit: stats.tileHit, tileMiss: stats.tileMiss,
               elevCached: elevCache.size, tilesCached: tileCache.size };
    },
    resetStats: function () {
      stats.elevHit = stats.elevMiss = stats.tileHit = stats.tileMiss = 0;
    },
    clearCache: function () { elevCache.clear(); tileCache.clear(); }
  };
})();
