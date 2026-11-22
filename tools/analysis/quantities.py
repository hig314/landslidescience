"""The quantity registry: every overlay that can be an axis of the analysis
panel, and where its numbers come from (ANALYSIS_TOOL_PLAN.md, phase 2).

One entry per quantity. `overlay` is the map.js OVERLAYS id the quantity
is read through on the map; the panel's pickers list overlays that carry a
`values.key` naming one of these. `src` is the raster the cube builder and
the landslide sampler read -- the NATIVE product, never a colour tile. The
builder point-samples it at the centre of every cell of the 1 km EPSG:3338
lattice (the permafrost grid, tools/permafrost/export_web.py), which is
exactly the published value for a 1 km product and a systematic 1-in-278
sample of a 60 m one. A density over the lattice is therefore a density of
the native field, and a landslide sampled from the same raster at its
centroid sits in the same distribution. Nothing here is a mean over a cell.

`range`/`bins` fix the density axis; `classes` marks an ordinal quantity
(DGGS) whose axis is class position, not class value. `nodata` lists
values to drop beyond the raster's own nodata. `label`, `units`, `note`
are what the panel shows.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The permafrost working grids. Since 2026-10-31 they live only on R2
# (lidar bucket, permafrost_build/); pull them back with rclone to this path
# or point PF_BUILD at the copy before building those quantities.
B = Path(os.environ.get('PF_BUILD', '/Volumes/Nunatak/permafrost_build'))
DATA = ROOT / 'data'
# The landform statistics' statewide run (tools/terrain/landform/landform_state.py):
# 40 m EPSG:3338 VRTs of the blurred, spread fields, on Nunatak.
LF = Path(os.environ.get('LF_STATE', '/Volumes/Nunatak/terrain_build/landform/state'))

# Alaska (the `alaska` region subset, in EPSG:3338), for products that cover
# Alaska only: a raster whose model stops at the border but writes 0 beyond
# it (the USGS susceptibility models -- 98 % zeros over Canadian ground that
# is steeper than Alaska's on average) would otherwise put 800,000 km² of
# false "not susceptible" into every plot. Export it from the database:
#   psql ... -At -c "SELECT ST_AsGeoJSON(ST_Transform(region_geom,3338)) FROM subsets WHERE slug='alaska'"
ALASKA = DATA / 'regions_seed' / 'alaska_3338.geojson'

# The lattice: the permafrost 1 km grid (EPSG:3338).
GRID = {'crs': 'EPSG:3338', 'width': 2706, 'height': 2179,
        'transform': [1000.0, 0.0, -1041000.0, 0.0, -1000.0, 2622000.0]}

QUANTITIES = {
    # ---- Surface properties: the 60 m terrain grids (Mapterhorn z11 = 3DEP 1/3") --
    'elev':   dict(overlay='sp-elev',  label='Elevation', units='m', range=(0, 3500), bins=70, res='60 m',
                   src=B / 'out/elev60_land.tif'),
    'slope':  dict(overlay='sp-slope', label='Slope', units='°', range=(0, 60), bins=60, res='60 m',
                   src=B / 'out/slope60_land.tif'),
    'aspect': dict(overlay='sp-aspect', label='Aspect', units='° from N', range=(0, 360), bins=36, res='60 m',
                   src=B / 'out/aspect60_land.tif', note='flat cells have no aspect (masked)'),
    # The fixed-scale slope rows (tools/terrain/bake_slope.py), whose bake kept
    # no values: tools/analysis/slope_scale_values.py evaluates the same
    # measure on the same Mapterhorn z12 tiles at the lattice points (writing
    # the cube column itself) and at the landslide centroids (`points`).
    # `computed` = no raster to sample; the cube builder leaves these alone.
    # The 60 m row (`sp-slope`) stays bound to `slope` above, which can paint.
    **{k: dict(overlay=ov, label=lab, units='°', range=(0, 60), bins=60, res=res, computed=True,
               points=DATA / 'slope_scales/landslides.json', alaska_only=True, note=note)
       for k, ov, lab, res, note in (
           ('slope_local', 'sp-slope-local', 'Slope, local', '~9 m', 'ordinary slope on the z12 grid'),
           ('slope_200', 'sp-slope-200', 'Slope, 200 m', '200 m', 'steepest line 200 m long centred on the point'),
           ('slope_500', 'sp-slope-500', 'Slope, 500 m', '500 m', 'steepest line 500 m long centred on the point'),
           ('slope_1k', 'sp-slope-1k', 'Slope, 1 km', '1 km', 'steepest line 1 km long centred on the point'),
           ('slope_2k', 'sp-slope-2k', 'Slope, 2 km', '2 km', 'steepest line 2 km long centred on the point'))},
    'north':  dict(overlay='sp-north', label='Northness', units='', range=(-1, 1), bins=40, res='60 m',
                   src=B / 'out/northness60_land.tif', note='cos(aspect)·sin(slope)'),
    # ---- Landform statistics (tools/terrain/landform/, form only) ---------------
    # Each is spread onto the slopes (ridges down, valleys up) and blurred 1 km
    # before it reaches the 40 m grid, so a lattice point or a centroid reads the
    # signature of the landscape around it, not the crest or floor it sits on.
    # `log10`: the builder and the sampler store log10 of the value (F spans
    # three decades; the panel has no log axis).
    'ridge_r500': dict(overlay='lf-ridge-r500', label='Ridge radius (500 m disc)', units='m', range=(0, 1600), bins=64,
                       res='40 m, 1 km blur', src=LF / 'ridge_R500.vrt',
                       note='radius of the crest cross-section; saturates near 1,560 m in low relief (the crest test needs a 20 m drop)'),
    'ridge_n500': dict(overlay='lf-ridge-n500', label='Ridge scale exponent n', units='', range=(-0.5, 3), bins=70,
                       res='40 m, 1 km blur', src=LF / 'ridge_n500.vrt',
                       note='log2(radius at the 1 km disc / at the 500 m disc): 0 round, 1 straight flanks, more cusped'),
    'valley_f': dict(overlay='lf-valley-f', label='Valley fall across the floor', units='log₁₀ m', range=(-1, 2.7), bins=74,
                     res='40 m, 1 km blur', src=LF / 'valley_F.vrt', log10=True,
                     note='log10 of F = down-valley gradient × floor width: 0 = 1 m, 1 = 10 m, 2 = 100 m'),
    # ---- Climate / subsurface: the three permafrost products and our downscale --
    # Daymet V4 R1, 1991-2020 means of its annual summaries (tools/climate/):
    # Lambert conformal conic, so the builder warps it (nearest) onto the lattice.
    'daymet_prcp':  dict(overlay='cl-daymet-prcp', label='Precipitation (Daymet)', units='mm/yr', range=(0, 3000), bins=60,
                         res='1 km', src=DATA / 'climate_src/daymet/prcp_mean.tif',
                         note='mean annual total 1991–2020; the axis stops at 3000 mm, wetter ground falls in the last bin'),
    'daymet_tmean': dict(overlay='cl-daymet-tmean', label='Air temperature (Daymet)', units='°C', range=(-24, 8), bins=64,
                         res='1 km', src=DATA / 'climate_src/daymet/tmean.tif',
                         note='mean annual 1991–2020, mean of daily maximum and minimum'),
    'maat':   dict(overlay='pf-gruber-maat', label='MAAT (Gruber 2012)', units='°C', range=(-24, 8), bins=64, res='1 km',
                   src=B / 'src/gruber_maat_3338_1km.tif'),
    'maat60': dict(overlay='pf-gruber-maat60', label='MAAT, downscaled', units='°C', range=(-24, 8), bins=64, res='60 m',
                   src=B / 'out/gruber_maat60.tif'),
    'pzi':    dict(overlay='pf-gruber-pzi', label='PZI (Gruber 2012)', units='', range=(0, 1), bins=50, res='1 km',
                   src=B / 'src/gruber_pzi_3338_1km.tif'),
    'pzi60':  dict(overlay='pf-gruber-pzi60', label='PZI, downscaled', units='', range=(0, 1), bins=50, res='60 m',
                   src=B / 'out/gruber_pzi60.tif'),
    'magt':   dict(overlay='pf-obu-magt', label='MAGT (Obu 2019)', units='°C', range=(-16, 8), bins=48, res='1 km',
                   src=B / 'src/obu_magtm_3338_1km.tif', note='no value over glaciers'),
    'magt60': dict(overlay='pf-obu-magt60', label='MAGT, downscaled', units='°C', range=(-16, 8), bins=48, res='60 m',
                   src=B / 'out/obu_magt60.tif'),
    'prob':   dict(overlay='pf-obu-prob', label='Permafrost probability (Obu 2019)', units='', range=(0, 1), bins=50, res='1 km',
                   src=B / 'src/obu_perprob_3338_1km.tif'),
    'prob60': dict(overlay='pf-obu-prob60', label='Permafrost probability, downscaled', units='', range=(0, 1), bins=50, res='60 m',
                   src=B / 'out/obu_prob60.tif'),
    'pastick': dict(overlay='pf-pastick-nsp', label='Near-surface permafrost probability (Pastick 2015)', units='',
                    range=(0, 1), bins=50, res='30 m', src=B / 'src/pastick30_frac.tif',
                    note='Alaska only; water, ice, barren and developed ground have no value'),
    # ---- Landslide susceptibility --------------------------------------------
    'lw':     dict(overlay='susc-lw', label='Susceptibility lw (USGS)', units='of 81', range=(0, 82), bins=82, res='90 m',
                   src=DATA / 'usgs_susceptibility/lw_susc/lw_ak.tif', nodata=[2147483647], integer=True,
                   alaska_only=True, note='Alaska only'),
    'n10':    dict(overlay='susc-n10', label='Susceptibility n10 (USGS)', units='of 81', range=(0, 82), bins=82, res='90 m',
                   src=DATA / 'usgs_susceptibility/n10_susc/n10_ak.tif', nodata=[2147483647], integer=True,
                   alaska_only=True, note='Alaska only'),
    'dggs':   dict(overlay='susc-dggs', label='Susceptibility class (DGGS)', units='class', res='20 m',
                   src=Path('/Volumes/Nunatak/lidar_src/dggs_susc/dggs_susc_20m.vrt'),
                   classes=[0, 3, 5, 6, 7, 8, 9, 10], nodata=[200, 255],
                   note='ordinal classes, evenly spaced; 200 (water, ice) dropped'),
    # NASA's global map (Stanley & Kirschbaum 2017, 2023 update): 30 arc-second
    # cells in EPSG:4326, so the builder warps it (nearest) onto the lattice.
    # Int8 classes 1-5; 0 is sea / not assessed (the file's nodata is 127).
    # Global, so no Alaska mask: it is the one model with a value under every
    # record outside the state.
    'nasa':   dict(overlay='susc-nasa', label='Susceptibility class (NASA global)', units='class', res='~1 km',
                   src=DATA / 'susc_src/nasa/global-landslide-susceptibility-map-2-27-23.tif',
                   classes=[1, 2, 3, 4, 5], nodata=[0],
                   note='ordinal classes 1 very low .. 5 very high; sea and unassessed ground dropped'),
    # ---- Surface change ---------------------------------------------------------
    'coh':    dict(overlay='coh-summer', label='Radar coherence, summer', units='%', range=(0, 100), bins=50, res='93 m',
                   src=DATA / 'coherence_src/summer_vv_COH12.vrt', nodata=[0], build_vrt=DATA / 'coherence_src/summer_vv_COH12',
                   note='Sentinel-1 12-day VV, seasonal median'),
    # ---- Glaciers ---------------------------------------------------------------
    'ice_v':  dict(overlay='ice-v', label='Ice speed (ITS_LIVE)', units='m/yr', range=(0, 600), bins=60, res='120 m',
                   src=DATA / 'itslive_src/ITS_LIVE_velocity_120m_RGI01A_0000_v02_v.tif', note='glaciers only'),
    # Not here yet: Hugonnet dh/dt and IceBoost thickness/bed are published as
    # per-UTM-zone (per-complex) tiles, which gdalbuildvrt cannot mosaic; they
    # need a warp to one grid first (ANALYSIS_TOOL_PLAN.md phase 3). OPERA
    # velocity has no statewide raster (it is read from ASF's value tiles).
}

# The land mask: the 60 m terrain grid's own (sea = nodata there).
LAND_SRC = B / 'out/elev60_land.tif'


def alaska_mask():
    """ALASKA rasterised onto the lattice at cell centres -> bool (height, width)."""
    import json
    from affine import Affine
    from rasterio.features import rasterize
    return rasterize([(json.loads(ALASKA.read_text()), 1)], out_shape=(GRID['height'], GRID['width']),
                     transform=Affine(*GRID['transform']), fill=0, dtype='uint8').astype(bool)


def public(key):
    """The part of an entry the server and the browser see (no paths)."""
    q = QUANTITIES[key]
    out = {'key': key, 'overlay': q['overlay'], 'label': q['label'], 'units': q.get('units', ''),
           'res': q['res'], 'note': q.get('note', '')}
    if 'classes' in q:
        out['classes'] = q['classes']; out['bins'] = len(q['classes']); out['min'] = 0; out['max'] = len(q['classes'])
    else:
        out['min'], out['max'] = q['range']; out['bins'] = q['bins']
    if q.get('integer'): out['integer'] = True
    return out
