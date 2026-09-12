/* dem_shade_bridge.js — the old `window.DemShade` API on top of the
 * maplibre-gl-demshade package (vendor/maplibre-gl-demshade.iife.js).
 *
 * The package is the worker-based successor to dem_shade.js: same
 * `demshade://` protocol and byte-identical tile URLs, but the fetch, decode,
 * halo, shading and caches run off the main thread and tiles reach MapLibre
 * as transferred ImageBitmaps. This shim keeps map.js and the lidar preview
 * calling the API they always did:
 *
 *   DemShade.register(maplibregl);            // no-op: the instance registers itself
 *   DemShade.addDataset(id, pmtilesUrl, opts);// sync; tiles wait for the header read
 *   DemShade.addImageServer(id, url, opts);   // e.g. USGS 3DEP, live float32 exports
 *   DemShade.url(id, { az, alt, hs, bl, sl, md, as, ve });
 *   DemShade.demUrl(id);                      // raster-dem passthrough (terrain)
 *
 * `opts.fill` names another registered source to read where this one has no
 * data (a survey over 3DEP). `bl` codes: 0 overlay, 1 plain transparency,
 * 2 hard light, 3 multiply. Set window.DEMSHADE_OPTIONS before this script
 * to pass constructor options (cache sizes, ramps).
 */
window.DemShade = (function () {
  'use strict';
  var P = window.MapLibreGlDemShade;
  if (!P) { console.error('dem_shade_bridge: maplibre-gl-demshade not loaded'); return null; }
  var inst = new P.DemShade(window.DEMSHADE_OPTIONS || {});
  var known = {};
  var last = { elevHit: 0, elevMiss: 0, tileHit: 0, tileMiss: 0, elevCached: 0, tilesCached: 0 };

  return {
    register: function () { /* registered on construction */ },
    addDataset: function (id, pmtilesUrl, opts) {
      if (known[id]) return;
      known[id] = true;
      var spec = { pmtiles: new URL(pmtilesUrl, location.href).href, encoding: 'mapbox' };
      if (opts && opts.fill) spec.fill = opts.fill;
      inst.addSource(id, spec)
          .catch(function (e) { delete known[id]; console.warn('demshade: source ' + id + ' failed', e); });
    },
    addImageServer: function (id, url, opts) {
      if (known[id]) return;
      known[id] = true;
      var spec = { imageServer: url };
      if (opts && opts.maxzoom !== undefined) spec.maxzoom = opts.maxzoom;
      inst.addSource(id, spec)
          .catch(function (e) { delete known[id]; console.warn('demshade: source ' + id + ' failed', e); });
    },
    url: function (id, o) { return inst.tileUrl(id, P.toOptions(o || {})); },
    demUrl: function (id) { return inst.demTileUrl(id); },
    defaults: P.DEFAULT_PARAMS,
    loadTile: function (url, signal) { return inst.loadTile(url, signal); },
    // Old API was synchronous; return the last snapshot and refresh it.
    stats: function () {
      inst.stats().then(function (s) { last = s; });
      return last;
    },
    resetStats: function () { /* counters live in the worker; not resettable */ },
    clearCache: function () { inst.clearCache(); },
    // Keep the map centre on the terrain surface without MapLibre's per-frame
    // re-solve (see terrain-center.ts in the package). Returns a detach fn.
    trackTerrainCenter: function (map, opts) { return P.trackTerrainCenter(map, opts); },
    instance: inst
  };
})();
