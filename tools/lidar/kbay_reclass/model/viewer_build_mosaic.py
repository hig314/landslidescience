"""Put the full reclassified mosaic into the DEV /lidar/ catalogue, beside the published vendor survey.

    python model/viewer_build_mosaic.py

Adds (replacing earlier copies of) two entries to data/lidar_dev/catalog.geojson, leaving the
comparison sites viewer_build.py wrote untouched:

  kbay_2023_reclass   the mosaic (full/mosaic/dtm_final.tif): elevation + slope pyramids built
                      here by build_lidar.py's own build_web / build_slope_web / build_pmtiles,
                      same zooms as the published survey (z8-z17), pmtiles in data/lidar_dev/.
  kbay_2023           the PUBLISHED vendor survey, copied from the public catalogue (its R2 / tile-
                      Worker URLs, nothing rebuilt), relabelled as the vendor classification. Hig,
                      2026-09-28: keep the vendor version, even on production, labelled as such.

Both carry year 2023, so /lidar/'s difference layer shows plain metres, reclass minus vendor.
"""
import json, os, sys
from pathlib import Path

HERE = Path('/Volumes/Nunatak/lidar_build/kbay_reclass_test')
REPO = Path('/Users/Hig/Claude_projects/landslidescience')
os.environ['LIDAR_BUILD'] = str(HERE / 'viewer_build')
os.environ['LIDAR_PM_OUT'] = str(REPO / 'data' / 'lidar_dev' / 'pmtiles')
sys.path.insert(0, str(REPO / 'tools' / 'lidar')); import build_lidar as B
import numpy as np
import rasterio
from rasterio.warp import transform_bounds

VENDOR_TITLE = 'Kachemak Bay 2023 lidar DEM (NOAA vendor ground classification)'
ID = 'kbay_2023_reclass'
KNOWN_ISSUE = (" KNOWN ISSUE (2026-09-28): structures are classified as ground -- buildings and bridges stand up in this surface where the vendor surface goes under them. The vendor's building (6) and bridge (17) classes were not excluded from ground; the fix is recorded in tools/lidar/kbay_reclass/README.md.")
KNOWN_ISSUE += ' KNOWN ISSUE (2026-09-28): in places spurious returns above water are kept as ground (e.g. 59.45329, -151.71004); see the README.'


def main():
    arch = HERE / 'full' / 'mosaic' / 'dtm_final.tif'
    ds = dict(id=ID, title='Kachemak Bay 2023 lidar DTM, reclassified (calibrated on Grewingk 2021)',
              min_zoom=8, max_zoom=17, target_epsg=6334)
    env = B.clean_env()
    tiles = B.build_web(ds, arch, env)
    pm = B.build_pmtiles(ds, tiles)
    stiles = B.build_slope_web(ds, arch, env, B.merc_bounds(arch, env))
    spm = B.build_pmtiles(ds, stiles, suffix='_slope')
    with rasterio.open(arch) as s:
        b = transform_bounds(s.crs, 'EPSG:4326', *s.bounds)
        ov = s.read(1, out_shape=(s.height // 16, s.width // 16))
    ok = ov != -9999
    reclass = {'type': 'Feature',
               'geometry': {'type': 'Polygon', 'coordinates': [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]]},
               'properties': dict(
                   id=ID, title=ds['title'], region='Kenai Peninsula', year=2023, product='DTM (bare earth, reclassified)',
                   native_res_m=1.0, coverage_km2=round(float(ok.sum()) * 256e-6, 1), horizontal_crs='EPSG:6334',
                   vertical_datum='NAVD88 (GEOID12B)', min_zoom=8, max_zoom=17, bounds=[round(v, 6) for v in b],
                   pmtiles_url=f'/lidar/dev/pmtiles/{ID}.pmtiles', pmtiles_bytes=pm.stat().st_size,
                   slope_url=f'/lidar/dev/pmtiles/{ID}_slope.pmtiles', slope_bytes=spm.stat().st_size, slope_step=0.5,
                   tiles_url=None, slope_tiles_url=None, fill_mode='holes', ortho_url=None, cog_url=None,
                   z_min=float(ov[ok].min()), z_max=float(ov[ok].max()),
                   source='NOAA OCM 10418 point cloud (NV5), reclassified here; calibrated on DGGS Grewingk 2021',
                   notes='Full-survey reclassification (tools/lidar/kbay_reclass): 1,350 1 km units, 27 empty edge '
                         'slivers; ground model + zone solver + learned blend + rock/cliff faces; enclosed holes '
                         'harmonically filled per unit and across unit seams (fill_mask 2). Water is what the data '
                         'shows: no hydroflattening.' + KNOWN_ISSUE)}
    pub = json.loads((REPO / 'data' / 'lidar' / 'catalog.geojson').read_text())
    vend = next(f for f in pub['features'] if f['properties']['id'] == 'kbay_2023')
    vend = json.loads(json.dumps(vend))
    vend['properties']['title'] = VENDOR_TITLE
    cat = REPO / 'data' / 'lidar_dev' / 'catalog.geojson'
    fc = json.loads(cat.read_text())
    fc['features'] = [f for f in fc['features'] if f['properties']['id'] not in (ID, 'kbay_2023')] + [vend, reclass]
    tmp = cat.with_suffix('.tmp'); tmp.write_text(json.dumps(fc)); os.replace(tmp, cat)   # atomic: Docker file sharing
    print('dev catalogue now has', len(fc['features']), 'surveys, incl.', ID, 'and the relabelled vendor survey', flush=True)


if __name__ == '__main__':
    main()
