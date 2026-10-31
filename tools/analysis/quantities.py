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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
B = Path('/Volumes/Nunatak/permafrost_build')
DATA = ROOT / 'data'

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
    'north':  dict(overlay='sp-north', label='Northness', units='', range=(-1, 1), bins=40, res='60 m',
                   src=B / 'out/northness60_land.tif', note='cos(aspect)·sin(slope)'),
    # ---- Climate / subsurface: the three permafrost products and our downscale --
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
