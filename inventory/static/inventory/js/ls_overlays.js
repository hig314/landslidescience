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

    // o: optional { maxzoom, minzoom, tileSize } (defaults 10, 3, 256).
    function rasterDef(archive, attr, o) {
        o = o || {};
        if (!window.__lsPmtilesReg && typeof maplibregl !== 'undefined' &&
                typeof pmtiles !== 'undefined') {
            window.__lsPmtilesReg = true;
            maplibregl.addProtocol('pmtiles', new pmtiles.Protocol().tile);
        }
        return {
            type: 'raster',
            tiles: ['pmtiles://' + location.origin + '/overlays/' + archive + '.pmtiles/{z}/{x}/{y}'],
            tileSize: o.tileSize || 256,
            minzoom: o.minzoom != null ? o.minzoom : 3,
            maxzoom: o.maxzoom || 10,
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

    // ------------------------------------------------------------------------
    // Baked overlays (2026-11-15): every overlay whose tiles are one PMTiles
    // archive under /overlays/ and need nothing else from the page (no
    // protocol of their own, no date, no sign-in). The single source of
    // truth for these rows: the inventory map merges them into its OVERLAYS
    // registry by id (bakedOverlays().byId), and /lidar/compare/ offers
    // them as shared layers. OPERA, NRCan, DIST, geology (Macrostrat),
    // shaded relief, contours, review history and the erosion rows stay in
    // map.js: each needs code that lives there.
    //
    // The one page-dependent case is slope: with slope_scales.js loaded
    // (window.LSSlope, the inventory map) local and 60 m are computed in the
    // page at z12 from the tiles the bake read; without it (compare) they
    // stop at z11 like the coarser four.
    // ------------------------------------------------------------------------
    var SUSC_ATTR = {
        lw:  'Susceptibility (lw): USGS, Belair et al. 2024',
        n10: 'Susceptibility (n10): USGS, Belair et al. 2024'
    };
    var PF_ATTR = 'Permafrost: Gruber 2012 (UZH); Obu et al. 2019 (CC-BY 3.0); Pastick et al. 2015 (USGS); ' +
                  'downscaled layers © landslidescience.org on Mapterhorn / USGS 3DEP terrain';
    var SP_ATTR = 'Terrain: USGS 3DEP via Mapterhorn';
    var DAYMET_ATTR = 'Climate: Daymet V4 R1 (Thornton et al. 2022, ORNL DAAC), 1991–2020 means';
    var GLAC_ATTR = 'Glacier area 2020: Roberts-Pierel, Kirchner, Kilbride & Kennedy (2022), NSIDC G10040, doi:10.7265/8esq-w553';
    var COHERENCE_ATTR = 'Sentinel-1 coherence: Kellndorfer et al. 2022 ' +
                         '(doi:10.1038/s41597-022-01189-6, CC BY 4.0)';
    var ICEBOOST_ATTR = 'Ice thickness: IceBoost v2.0 (Maffezzoli et al. 2025, CC-BY 4.0)';

    // A baked raster: archive id, tile size, zoom range, credit. A rebuild
    // with different pixels gets a new archive id (no ?v= tokens).
    function archive(id, maxzoom, attr, minzoom, tileSize) {
        return rasterDef(id, attr, { maxzoom: maxzoom, minzoom: minzoom, tileSize: tileSize });
    }
    function row(id, label, sub, defOpacity, sourceDef) {
        return { id: id, layerId: 'ov-' + id, sourceId: 'ov-' + id + '-src',
                 label: label, sub: sub, defOpacity: defOpacity, sourceDef: sourceDef };
    }
    function pfRow(id, archiveId, maxzoom, label, sub, defOpacity, attr) {
        return row(id, label, sub, defOpacity, function () { return archive(archiveId, maxzoom, attr || PF_ATTR); });
    }
    function slopeSourceDef(sid) {
        var fine = window.LSSlope && (sid === 'local' || sid === '60');
        if (!fine) return archive('sp_slope_' + sid, 11, SP_ATTR, 3, 512);
        return { type: 'raster', tiles: ['lsslope://' + sid + '/{z}/{x}/{y}'],
                 tileSize: 512, minzoom: 3, maxzoom: 12, attribution: SP_ATTR };
    }
    function slopeRow(id, label, sid, sub) {
        return row(id, label, sub, 0.7, function () { return slopeSourceDef(sid); });
    }

    // Susceptibility is purple (2026-11-25): the purple arm of the InSAR ramp
    // (OPERA_RAMP in map.js), pale to dark, because yellow-orange-red sat
    // under the pink and yellow landslide dots. The archives are the
    // `_purple` ones, recoloured from the yellow-red bakes by
    // tools/recolor_susc_archives.py; colours in tools/susc_purple_ramps.py.
    function bakedOverlays() {
        var rows = [
            // The two USGS models keep their original layer/source ids: the
            // trace-raster and lidar insertion chains name 'susc-*-layer'.
            { id: 'susc-lw',    layerId: 'susc-lw-layer',   sourceId: 'susc-lw',
              label: 'Susceptibility — lw',  sub: 'USGS Belair et al. 2024, 90 m',
              sourceDef: function () { return archive('susc_lw_purple', 10, SUSC_ATTR.lw); }, defOpacity: 1 },
            { id: 'susc-n10',   layerId: 'susc-n10-layer',  sourceId: 'susc-n10',
              label: 'Susceptibility — n10', sub: 'USGS Belair et al. 2024, 90 m',
              sourceDef: function () { return archive('susc_n10_purple', 10, SUSC_ATTR.n10); }, defOpacity: 1 },
            // The state's own model (rock strength x slope class). Baked from
            // the raster recovered from DGGS's MapServer, coloured from
            // tools/dggs_susc_color.txt on the USGS pair's scale. z12 is
            // the 20 m grid's own ceiling; the client overzooms above it.
            { id: 'susc-dggs', layerId: 'ov-susc-dggs', sourceId: 'ov-susc-dggs-src',
              label: 'Susceptibility — DGGS',
              sub: 'Alaska DGGS PIR 2025-3 · rock strength × slope · 20 m',
              sourceDef: function () {
                  return archive('susc_dggs_purple', 12, 'Deep-seated landslide susceptibility: Alaska DGGS, ' +
                                                  'Wikstrom Jones & Larsen 2025 (PIR 2025-3)');
              }, defOpacity: 0.8 },
            // NASA's global map (Stanley & Kirschbaum 2017, 2023 update), five
            // classes on a 30" grid, 72 N to 60 S; z8 resolves the ~1 km cells.
            { id: 'susc-nasa', layerId: 'ov-susc-nasa', sourceId: 'ov-susc-nasa-src',
              label: 'Susceptibility — NASA global',
              sub: 'Stanley & Kirschbaum 2017 (2023 update) · slope, geology, faults, roads, forest loss · ~1 km, 72°N–60°S',
              sourceDef: function () {
                  return archive('susc_nasa_purple', 8, 'Global landslide susceptibility: NASA GSFC, ' +
                                                 'Stanley & Kirschbaum 2017 (2023 update)', 0);
              }, defOpacity: 0.8 },
            // Surface properties (2026-10-09): the 60 m terrain grids the
            // permafrost downscale used.
            pfRow('sp-elev', 'sp_elev', 10, 'Elevation', 'm, hypsometric · 3DEP via Mapterhorn, 60 m', 0.7, SP_ATTR),
            // Slope at six fixed horizontal scales (2026-11-01). `sp-slope`
            // was one layer whose scale followed the zoom; the id stays for
            // stored views and is now the 60 m row.
            slopeRow('sp-slope-local', 'Slope — local', 'local',
                     'ordinary slope on the ~9 m grid · Alaska'),
            slopeRow('sp-slope', 'Slope — 60 m', '60',
                     'steepest line 60 m long centred on each pixel · Alaska'),
            slopeRow('sp-slope-200', 'Slope — 200 m', '200',
                     'steepest line 200 m long centred on each pixel · Alaska'),
            slopeRow('sp-slope-500', 'Slope — 500 m', '500',
                     'steepest line 500 m long centred on each pixel · Alaska'),
            slopeRow('sp-slope-1k', 'Slope — 1 km', '1k',
                     'steepest line 1 km long centred on each pixel · Alaska'),
            slopeRow('sp-slope-2k', 'Slope — 2 km', '2k',
                     'steepest line 2 km long centred on each pixel · Alaska'),
            pfRow('sp-aspect', 'sp_aspect', 10, 'Aspect',
                  'N blue · E green · S yellow · W magenta, 60 m · flat cells read as north', 0.7, SP_ATTR),
            pfRow('sp-north', 'sp_north', 10, 'Northness',
                  'cos(aspect)·sin(slope), 60 m · blue north-facing, red south-facing · the aspect term of the permafrost downscale',
                  0.7, SP_ATTR),
            // Glacier covered area 2020 (NSIDC G10040): the recent outline the
            // permafrost downscale is read against.
            pfRow('glac-outline-2020', 'glac_outline_2020', 12, 'Glacier covered area — 2020',
                  'Landsat-derived outlines, 30 m · NSIDC G10040 (1985–2020 series, 2020 epoch)', 0.9, GLAC_ATTR),
            // Permafrost (2026-10-09): three published products and our
            // terrain downscale of the two 1 km ones.
            // Daymet V4 R1 (2026-11-19, tools/climate/): station-interpolated
            // daily weather on a 1 km grid, here as 1991-2020 means of its
            // annual summaries. The temperature row shares the MAAT ramp.
            pfRow('cl-daymet-prcp', 'cl_daymet_prcp', 9, 'Mean annual precipitation — Daymet',
                  'mm per year, 1 km · 1991–2020 · interpolated from stations, weakest in high ungauged terrain', 0.8, DAYMET_ATTR),
            pfRow('cl-daymet-tmean', 'cl_daymet_tmean', 9, 'Mean annual air temperature — Daymet',
                  '°C, 1 km · 1991–2020 · mean of daily maximum and minimum', 0.8, DAYMET_ATTR),
            pfRow('pf-gruber-maat', 'pf_gruber_maat', 9, 'Mean annual air temperature — Gruber 2012',
                  '°C, 1 km · the MAAT climatology Gruber derived PZI from', 0.8),
            pfRow('pf-gruber-maat60', 'pf_gruber_maat60', 11, 'Mean annual air temperature — terrain-downscaled',
                  '°C, 60 m · Gruber MAAT moved by a kernel-fitted local lapse onto 3DEP elevation (our inference)', 0.8),
            pfRow('pf-gruber-pzi', 'pf_gruber_pzi', 9, 'Permafrost zonation index — Gruber 2012',
                  'PZI 0–1, 1 km · fraction of the cell with permafrost, from MAAT', 0.8),
            pfRow('pf-gruber-pzi60', 'pf_gruber_pzi60', 11, 'Permafrost zonation index — terrain-downscaled',
                  'PZI 0–1, 60 m · Gruber MAAT lapse-rate fit per 10 km, applied to 3DEP elevation (our inference)', 0.8),
            pfRow('pf-obu-prob', 'pf_obu_prob', 9, 'Permafrost probability — Obu 2019',
                  'probability 0–1, 1 km · TTOP model on MODIS land-surface temperature, 2000–2016', 0.8),
            pfRow('pf-obu-prob60', 'pf_obu_prob60', 11, 'Permafrost probability — terrain-downscaled',
                  'probability 0–1, 60 m · Obu MAGT fit on elevation + aspect per 10 km (our inference)', 0.8),
            pfRow('pf-obu-magt', 'pf_obu_magt', 9, 'Ground temperature — Obu 2019',
                  '°C at the permafrost top, 1 km · 0 °C is the permafrost boundary', 0.8),
            pfRow('pf-obu-magt60', 'pf_obu_magt60', 11, 'Ground temperature — terrain-downscaled',
                  '°C, 60 m · the downscaled Obu MAGT the probability above is derived from (our inference)', 0.8),
            pfRow('pf-pastick-nsp', 'pf_pastick_nsp', 12, 'Near-surface permafrost probability — Pastick 2015',
                  'probability 0–1 of permafrost within 1 m, 30 m, Alaska only · field-trained; water, ice, barren, developed masked', 0.8),
            // IceBoost v2.0 (Maffezzoli et al. 2025, GMD). Bed =
            // (surface - geoid) - thickness; overdeepenings = per-complex
            // sink-fill depth of that bed.
            row('ice-thick', 'Ice thickness', 'IceBoost v2.0, 100 m · Maffezzoli et al. 2025', 0.85,
                function () { return archive('iceboost_thickness', 10, ICEBOOST_ATTR); }),
            row('ice-bed', 'Bed elevation (under ice)', 'IceBoost surface − thickness · blue = below sea level', 0.9,
                function () { return archive('iceboost_bed', 10, ICEBOOST_ATTR); }),
            row('ice-over', 'Bed overdeepenings', 'closed-basin depth >10 m — future lakes / fjord arms', 0.9,
                function () { return archive('iceboost_overdeep', 10, ICEBOOST_ATTR); }),
            row('coh-summer', 'Radar coherence',
                'Sentinel-1 12-day VV summer, 93 m · where InSAR works; low patches can mark active ground', 0.85,
                function () { return archive('coh_summer', 10, COHERENCE_ATTR); }),
        ].concat(glacierOverlays());
        var byId = {};
        rows.forEach(function (r) { byId[r.id] = r; });
        return { rows: rows, byId: byId };
    }

    // The overlay groups, in the order the inventory sidebar lists them.
    // An id absent from a page's registry is skipped (and a group left
    // empty), so this lists every id either page may have.
    var CATS = [
        { key: 'clim',  label: 'Climate',
          ids: ['cl-daymet-prcp', 'cl-daymet-tmean', 'pf-gruber-maat', 'pf-gruber-maat60', 'pf-obu-magt', 'pf-obu-magt60'] },
        // Surface (2026-11-01, Hig): one group, two levels. Statistics are
        // values a pixel carries (an axis can be one); visualizations are
        // pictures of the DEM. The keys 'sp' / 'relief' are only fold-state
        // names, so they stay.
        { key: 'surface', label: 'Surface', subs: [
            { key: 'sp',     label: 'DEM statistics',
              ids: ['sp-elev', 'sp-slope-local', 'sp-slope', 'sp-slope-200', 'sp-slope-500',
                    'sp-slope-1k', 'sp-slope-2k', 'sp-aspect', 'sp-north'] },
            { key: 'creq',   label: 'Required strength (exploratory)',
              ids: ['sp-creq-20-200'] },
            { key: 'landform', label: 'Landform statistics (exploratory)',
              ids: ['lf-ridge-r500', 'lf-ridge-n500', 'lf-valley-f'] },
            { key: 'relief', label: 'DEM visualizations',
              ids: ['relief-mt-hs', 'relief-mt-pre', 'relief-adem-hs', 'relief-adem-pre', 'sp-contours'] },
        ] },
        // 'dist-alert' stays listed although it is retired: absent ids are
        // skipped, so this needs no edit if DIST_ALERT_ACTIVE is flipped back on.
        { key: 'surf',  label: 'Surface change',
          ids: ['opera-asc', 'opera-desc', 'nrcan-asc', 'coh-summer', 'dist-ann', 'dist-alert'] },
        { key: 'susc',  label: 'Landslide susceptibility',
          ids: ['susc-lw', 'susc-n10', 'susc-dggs', 'susc-nasa'] },
        { key: 'glac',  label: 'Glaciers',
          ids: ['glac-outline-2020', 'ice-v', 'ice-amp', 'ice-dvdt', 'ice-dhdt', 'ice-thick', 'ice-bed', 'ice-over'] },
        { key: 'sub',   label: 'Subsurface',
          ids: ['pf-gruber-pzi', 'pf-gruber-pzi60', 'pf-obu-prob', 'pf-obu-prob60', 'pf-pastick-nsp', 'geology'] },
        // Rates of rock removal, long-term (thermochronology, Myr) and recent
        // (cosmogenic nuclides, kyr), on one colour scale. Switched off
        // (EROSION_ACTIVE in map.js): absent ids are skipped and the group with them.
        { key: 'ero',   label: 'Erosion rates',
          ids: ['exh-2-0', 'exh-4-2', 'exh-6-4', 'exh-8-6', 'den-be10'] },
        // Signed-in only (the entry exists only then; absent ids are skipped).
        { key: 'review', label: 'Review history', ids: ['review'] },
    ];

    window.LSOverlays = {
        bakedOverlays: bakedOverlays,
        CATS: CATS,
        rasterDef: rasterDef,
        SP_ATTR: SP_ATTR,
        PF_ATTR: PF_ATTR,
        glacierOverlays: glacierOverlays,
        ITSLIVE_ATTR: ITSLIVE_ATTR,
        HUGONNET_ATTR: HUGONNET_ATTR
    };
})();
