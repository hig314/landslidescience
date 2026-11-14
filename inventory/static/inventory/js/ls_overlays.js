/* Shared glacier-overlay descriptors — the single source of truth for the
 * ITS_LIVE velocity layers and the Hugonnet thinning layer (incl. its
 * smoothed variant), consumed by BOTH the inventory map (map.js, merged
 * into its OVERLAYS registry alongside susceptibility/OPERA) and the
 * /glaciers app. Add or change a glacier overlay ONLY here, the same way
 * basemaps live only in basemaps.js — two registries would drift.
 *
 * Descriptor shape matches map.js's OVERLAYS entries:
 *   { id, layerId, sourceId, label, sub, sourceDef(), defOpacity, variant? }
 * `variant` (thinning only) is the smoothed-build toggle: get()/set(on)
 * backed by localStorage, shared across both apps; the ov= URL hash carries
 * it as a `~s` id suffix (encoded/parsed in each app's hash layer).
 *
 * Tile serving contract (2026-11-14): one PMTiles archive per layer on R2,
 * read through /overlays/<id>.pmtiles — itslive_<v|vamp|dvdt>,
 * hugonnet_<dhdt|dhdt_smooth>. The page must load pmtiles.js before this
 * file. A rebuild with different pixels gets a new archive id. (They were
 * tile trees on the droplet under /tiles/itslive/ and /tiles/hugonnet/.)
 */
(function () {
    'use strict';

    var ITSLIVE_ATTR = 'ITS_LIVE velocity (auto-RIFT; Gardner et al. 2018) · NASA MEaSUREs';
    var HUGONNET_ATTR = 'Glacier elevation change © Hugonnet et al. 2021 (CC BY 4.0)';

    // Thinning-layer variant flag: standard vs bilateral-smoothed build with
    // the near-zero-sensitive ramp. One browser-wide preference.
    var _dhdtVariant = false;
    try { _dhdtVariant = localStorage.getItem('ls_dhdt_variant') === '1'; } catch (e) {}
    var dhdtVariant = {
        label: 'smoothed — reveals near-zero change',
        title: 'Edge-preserving (bilateral) smoothing with a color scale ' +
               'sensitive down to ±0.04 m/yr — slight thickening becomes visible.',
        get: function () { return _dhdtVariant; },
        set: function (on) {
            _dhdtVariant = !!on;
            try { localStorage.setItem('ls_dhdt_variant', on ? '1' : '0'); } catch (e) {}
        }
    };

    function rasterDef(archive, attr) {
        if (!window.__lsPmtilesReg && typeof maplibregl !== 'undefined' &&
                typeof pmtiles !== 'undefined') {
            window.__lsPmtilesReg = true;
            maplibregl.addProtocol('pmtiles', new pmtiles.Protocol().tile);
        }
        return {
            type: 'raster',
            tiles: ['pmtiles://' + location.origin + '/overlays/' + archive + '.pmtiles/{z}/{x}/{y}'],
            tileSize: 256,
            minzoom: 3,
            maxzoom: 10,
            attribution: attr
        };
    }

    /* Build the four glacier-overlay descriptors.
     * opts: { itsliveBase?, hugonnetBase? } — override tile bases (snapshot
     * bundles may need it); defaults are the live-site routes. */
    function glacierOverlays(opts) {
        opts = opts || {};
        return [
            { id: 'ice-v',    layerId: 'ov-ice-v',    sourceId: 'ov-ice-v-src',
              label: 'Glacier speed', sub: 'ITS_LIVE 2014–2022 composite, 120 m · NASA',
              sourceDef: function () { return rasterDef('itslive_v', ITSLIVE_ATTR); },
              defOpacity: 0.85 },
            { id: 'ice-amp',  layerId: 'ov-ice-amp',  sourceId: 'ov-ice-amp-src',
              label: 'Glacier seasonal amplitude', sub: 'ITS_LIVE v_amp, 120 m · NASA',
              sourceDef: function () { return rasterDef('itslive_vamp', ITSLIVE_ATTR); },
              defOpacity: 0.85 },
            { id: 'ice-dvdt', layerId: 'ov-ice-dvdt', sourceId: 'ov-ice-dvdt-src',
              label: 'Glacier speed trend', sub: 'ITS_LIVE dv/dt — red speeding, blue slowing · NASA',
              sourceDef: function () { return rasterDef('itslive_dvdt', ITSLIVE_ATTR); },
              defOpacity: 0.9 },
            { id: 'ice-dhdt', layerId: 'ov-ice-dhdt', sourceId: 'ov-ice-dhdt-src',
              label: 'Glacier thinning', sub: 'dh/dt 2000–2019, red thinning · Hugonnet et al. 2021',
              sourceDef: function () {
                  return rasterDef(dhdtVariant.get() ? 'hugonnet_dhdt_smooth' : 'hugonnet_dhdt',
                                   HUGONNET_ATTR);
              },
              defOpacity: 0.9,
              variant: dhdtVariant },
        ];
    }

    window.LSOverlays = {
        glacierOverlays: glacierOverlays,
        ITSLIVE_ATTR: ITSLIVE_ATTR,
        HUGONNET_ATTR: HUGONNET_ATTR
    };
})();
