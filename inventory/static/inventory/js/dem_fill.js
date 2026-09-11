/* dem_fill.js — a raster-dem source that is lidar where there is lidar and a
 * statewide DEM everywhere else.
 *
 * MapLibre's 3D terrain takes ONE raster-dem source. Fed the lidar archive
 * alone, every pixel outside the survey footprint decodes to 0 m (nodata is
 * encoded as sea level — see build_lidar.py), so a survey stands on a flat
 * plane as a block of towers. This protocol composites, per tile:
 *
 *     lidar (alpha > 0)   ->  the lidar height
 *     elsewhere           ->  AWS Terrain Tiles (Mapzen "terrarium"), which
 *                             cover Alaska from the USGS 2-arc-second NED
 *
 * and re-encodes the result as Mapbox terrain-RGB with alpha 255 throughout,
 * so terrain follows real ground to the horizon and the lidar sits on it.
 * Terrarium includes bathymetry, so the context is clamped at 0 m: the fjord
 * outside a coastal survey stays at sea level rather than dropping into a
 * trench next to the lidar's 0 m shoreline.
 *
 * Terrarium stops at z15. Deeper tiles sample the z15 ancestor bilinearly,
 * which is only ever visible along a survey's edge.
 *
 *   demfill://<dataset id>/{z}/{x}/{y}
 *
 * Failure policy matches dem_shade.js: an absent lidar tile is a legitimate
 * "all context"; a failed request is retried once and then REJECTED so
 * MapLibre marks the tile errored and re-asks on the next move — never cached
 * as if it were data.
 */
var DemFill = (function () {
  'use strict';

  var TILE = 256;
  var CONTEXT_URL = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png';
  var CONTEXT_MAXZ = 15;
  var LIDAR_MAX = 300;      // decoded lidar tiles, Float32Array(65536) each
  var CTX_MAX = 300;        // decoded context tiles

  var datasets = {};        // id -> pmtiles.PMTiles
  var lidarCache = new Map();
  var ctxCache = new Map();

  function lruGet(m, k) {
    if (!m.has(k)) return undefined;
    var v = m.get(k); m.delete(k); m.set(k, v); return v;
  }
  function lruSet(m, k, v, max) {
    m.set(k, v);
    if (m.size > max) m.delete(m.keys().next().value);
  }

  function addDataset(id, pmtilesUrl) {
    if (!datasets[id]) {
      datasets[id] = new pmtiles.PMTiles(new URL(pmtilesUrl, location.href).href);
    }
    return datasets[id];
  }

  function withRetry(fn) {
    return fn().catch(function () {
      return new Promise(function (r) { setTimeout(r, 400); }).then(fn);
    });
  }

  function pixelsOf(blobParts) {
    return createImageBitmap(new Blob(blobParts)).then(function (bmp) {
      var c = document.createElement('canvas');
      c.width = c.height = TILE;
      var ctx = c.getContext('2d', { willReadFrequently: true });
      ctx.drawImage(bmp, 0, 0);
      bmp.close && bmp.close();
      return ctx.getImageData(0, 0, TILE, TILE).data;
    });
  }

  // Lidar tile -> Float32Array heights, NaN outside the footprint; null if the
  // archive has no tile there at all.
  function lidar(id, z, x, y) {
    var key = id + '/' + z + '/' + x + '/' + y;
    var hit = lruGet(lidarCache, key);
    if (hit !== undefined) return hit;
    var pm = datasets[id];
    if (!pm) return Promise.resolve(null);
    var job = withRetry(function () {
      return pm.getZxy(z, x, y).then(function (t) {
        if (!t) return null;
        return pixelsOf([t.data]).then(function (d) {
          var out = new Float32Array(TILE * TILE);
          for (var i = 0, p = 0; i < out.length; i++, p += 4) {
            out[i] = d[p + 3] === 0 ? NaN
                   : -10000 + (d[p] * 65536 + d[p + 1] * 256 + d[p + 2]) * 0.1;
          }
          return out;
        });
      });
    });
    job.catch(function () { lidarCache.delete(key); });
    lruSet(lidarCache, key, job, LIDAR_MAX);
    return job;
  }

  // Context tile at its own zoom (<= CONTEXT_MAXZ) -> Float32Array heights.
  function contextTile(z, x, y) {
    var key = z + '/' + x + '/' + y;
    var hit = lruGet(ctxCache, key);
    if (hit !== undefined) return hit;
    var url = CONTEXT_URL.replace('{z}', z).replace('{x}', x).replace('{y}', y);
    var job = withRetry(function () {
      return fetch(url).then(function (r) {
        if (!r.ok) throw new Error('context ' + r.status);
        return r.arrayBuffer();
      }).then(function (buf) {
        return pixelsOf([buf]).then(function (d) {
          var out = new Float32Array(TILE * TILE);
          for (var i = 0, p = 0; i < out.length; i++, p += 4) {
            // terrarium: h = R*256 + G + B/256 - 32768
            var h = d[p] * 256 + d[p + 1] + d[p + 2] / 256 - 32768;
            out[i] = h < 0 ? 0 : h;          // clamp bathymetry to sea level
          }
          return out;
        });
      });
    });
    job.catch(function () { ctxCache.delete(key); });
    lruSet(ctxCache, key, job, CTX_MAX);
    return job;
  }

  // Context heights on THIS tile's grid, resampling from the z15 ancestor
  // when asked for something deeper.
  function context(z, x, y) {
    if (z <= CONTEXT_MAXZ) return contextTile(z, x, y);
    var dz = z - CONTEXT_MAXZ, f = 1 << dz;
    var ax = Math.floor(x / f), ay = Math.floor(y / f);
    var ox = (x - ax * f) * TILE / f, oy = (y - ay * f) * TILE / f;   // sub-window origin, ancestor px
    return contextTile(CONTEXT_MAXZ, ax, ay).then(function (A) {
      var out = new Float32Array(TILE * TILE);
      for (var j = 0; j < TILE; j++) {
        var sy = oy + (j + 0.5) / f - 0.5;
        var y0 = Math.max(0, Math.min(TILE - 1, Math.floor(sy))), y1 = Math.min(TILE - 1, y0 + 1);
        var ty = Math.max(0, Math.min(1, sy - y0));
        for (var i = 0; i < TILE; i++) {
          var sx = ox + (i + 0.5) / f - 0.5;
          var x0 = Math.max(0, Math.min(TILE - 1, Math.floor(sx))), x1 = Math.min(TILE - 1, x0 + 1);
          var tx = Math.max(0, Math.min(1, sx - x0));
          var a = A[y0 * TILE + x0], b = A[y0 * TILE + x1],
              c = A[y1 * TILE + x0], d = A[y1 * TILE + x1];
          out[j * TILE + i] = (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty;
        }
      }
      return out;
    });
  }

  function encode(H) {
    var px = new Uint8ClampedArray(TILE * TILE * 4);
    for (var i = 0, p = 0; i < H.length; i++, p += 4) {
      var h = H[i];
      if (h < -9999) h = -9999; else if (h > 6553) h = 6553;
      var v = Math.round((h + 10000) / 0.1);
      px[p] = v >> 16; px[p + 1] = (v >> 8) & 255; px[p + 2] = v & 255; px[p + 3] = 255;
    }
    var c = document.createElement('canvas');
    c.width = c.height = TILE;
    c.getContext('2d').putImageData(new ImageData(px, TILE, TILE), 0, 0);
    return new Promise(function (resolve, reject) {
      c.toBlob(function (blob) {
        if (!blob) { reject(new Error('demfill toBlob failed')); return; }
        blob.arrayBuffer().then(function (buf) { resolve({ data: buf }); }, reject);
      }, 'image/png');
    });
  }

  // Same abort policy as dem_shade.js: the shared, cached fetches carry on
  // (another tile may need them), but a tile MapLibre has already dropped
  // skips the composite and the PNG encode.
  function loader(params, abortController) {
    var ac = abortController;
    var u = new URL(params.url.replace('demfill://', 'https://d.invalid/'));
    var seg = u.pathname.split('/').filter(Boolean);
    var id = u.host === 'd.invalid' ? seg[0] : u.host;
    var z = +seg[1], x = +seg[2], y = +seg[3];
    return Promise.all([lidar(id, z, x, y), context(z, x, y)]).then(function (r) {
      if (ac && ac.signal && ac.signal.aborted) {
        var err = new Error('demfill tile aborted'); err.name = 'AbortError'; throw err;
      }
      var L = r[0], C = r[1];
      if (!L) return encode(C);
      var H = new Float32Array(TILE * TILE);
      for (var i = 0; i < H.length; i++) {
        var l = L[i];
        H[i] = l === l ? l : C[i];       // NaN test without isNaN's coercion
      }
      return encode(H);
    });
  }

  function register(gl) {
    if (!gl.addProtocol) return;
    gl.addProtocol('demfill', loader);
  }

  return {
    register: register,
    addDataset: addDataset,
    url: function (id) { return 'demfill://' + id + '/{z}/{x}/{y}'; },
    loadTile: loader,
    clearCache: function () { lidarCache.clear(); ctxCache.clear(); }
  };
})();
