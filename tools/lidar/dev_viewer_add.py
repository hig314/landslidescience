#!/usr/bin/env python3
"""Add (or replace) one surface in the DEV /lidar/ catalogue (data/lidar_dev/), for review only.

    PATH=/opt/homebrew/bin:$PATH /opt/anaconda3/bin/python3 tools/lidar/dev_viewer_add.py <id> <cog> \
        --title "..." [--year 2023] [--product "..."] [--notes "..."] [--max-zoom 17]

Builds the elevation and slope pyramids with build_lidar.py's own functions (the same terrain-RGB
encoding and per-zoom resampling as every published survey) into data/lidar_dev/pmtiles/, and writes
the catalogue entry with /lidar/dev/ URLs. Nothing here is published: data/lidar_dev/ is outside
data/lidar/ (never rsynced) and its routes 404 unless DEBUG. Generalises what
kbay_reclass/model/viewer_build_mosaic.py and grewingk_sonar/level_passes.py each did by hand.
Run from the MAIN worktree's repo (data/lidar_dev is where the dev server reads it).
"""
import argparse, json, os, sys
from pathlib import Path

REPO = Path(os.environ.get('LS_REPO', '/Users/Hig/Claude_projects/landslidescience'))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('id'); ap.add_argument('cog')
    ap.add_argument('--title', required=True); ap.add_argument('--year', type=int, default=None)
    ap.add_argument('--product', default='Topobathy composite (dev)'); ap.add_argument('--notes', default='')
    ap.add_argument('--source', default='landslidescience.org composite (dev)')
    ap.add_argument('--min-zoom', type=int, default=8); ap.add_argument('--max-zoom', type=int, default=17)
    ap.add_argument('--work', default='/Volumes/Nunatak/lidar_build/dev_viewer')
    # the water layer (2026-10-20, Hig): an inland water-surface raster (m, NAVD88; nodata where dry) and a sea mask
    # (any value where the ground is connected to the sea), each a terrain-RGB pyramid; the viewer floods the sea
    # mask to a tide level and draws water where the surface stands above the DEM
    ap.add_argument('--water-wse', default=None); ap.add_argument('--water-sea', default=None)
    ap.add_argument('--tide-levels', default=None, help='JSON {"MHHW": 4.24, ...} in the DEM\'s datum')
    # linked surfaces (2026-10-20, Hig: water, and eventually ice, as DEMs of their own drawn see-through over the
    # bed): water_surface.py output. The water surface is baked terrarium (1/256 m) so it can be shaded and meshed;
    # --surfaces-only rebuilds just these and updates the survey's catalogue entry, leaving the bed pyramid alone.
    ap.add_argument('--water-surface', default=None, help='water_surface.py output (terrarium pyramid)')
    ap.add_argument('--surfaces-only', action='store_true')
    a = ap.parse_args()
    os.environ['LIDAR_BUILD'] = str(Path(a.work) / a.id)
    os.environ['LIDAR_PM_OUT'] = str(REPO / 'data' / 'lidar_dev' / 'pmtiles')
    sys.path.insert(0, str(Path(__file__).resolve().parent)); import build_lidar as B
    import numpy as np, rasterio
    from rasterio.warp import transform_bounds
    src = Path(a.cog)
    with rasterio.open(src) as s:
        epsg = s.crs.to_epsg() or 6334
        b = transform_bounds(s.crs, 'EPSG:4326', *s.bounds)
        f = max(1, max(s.width, s.height) // 2000)
        ov = s.read(1, out_shape=(s.height // f, s.width // f), masked=True)
        res = abs(s.transform.a)
    ds = dict(id=a.id, title=a.title, min_zoom=a.min_zoom, max_zoom=a.max_zoom, target_epsg=epsg)
    env = B.clean_env()
    surfaces = []
    if a.water_surface:
        d2 = dict(ds, id=f'{a.id}_wsurf')
        pw = B.build_pmtiles(d2, B.build_web(d2, Path(a.water_surface), env, encoding='terrarium'))
        surfaces.append(dict(kind='water', url=f'/lidar/dev/pmtiles/{pw.name}', encoding='terrarium'))
    if a.water_sea:
        d2 = dict(ds, id=f'{a.id}_sea')
        pw = B.build_pmtiles(d2, B.build_web(d2, Path(a.water_sea), env))
        surfaces.append(dict(kind='sea', url=f'/lidar/dev/pmtiles/{pw.name}', encoding='mapbox'))
    cat = REPO / 'data' / 'lidar_dev' / 'catalog.geojson'
    if a.surfaces_only:
        fc = json.loads(cat.read_text())
        f = [x for x in fc['features'] if x['properties']['id'] == a.id]
        if not f: raise SystemExit(f'{a.id} is not in the dev catalogue; publish it whole first')
        pr = f[0]['properties']
        if surfaces: pr['surfaces'] = surfaces
        if a.tide_levels: pr['tide_levels'] = json.loads(a.tide_levels)
        tmp = cat.with_suffix('.tmp'); tmp.write_text(json.dumps(fc)); os.replace(tmp, cat)
        print(f'dev catalogue: {a.id} surfaces {[x["kind"] for x in surfaces]}')
        return
    pm = B.build_pmtiles(ds, B.build_web(ds, src, env))
    spm = B.build_pmtiles(ds, B.build_slope_web(ds, src, env, B.merc_bounds(src, env)), suffix='_slope')
    water = {}
    # legacy (the first water toggle): the raw inland surface, Mapbox-encoded; the sea is built once, above
    for key, path in (('water', a.water_wse),):
        if path:
            d2 = dict(ds, id=f'{a.id}_{key}')
            pw = B.build_pmtiles(d2, B.build_web(d2, Path(path), env))
            water[f'{key}_url'] = f'/lidar/dev/pmtiles/{pw.name}'
    if a.tide_levels: water['tide_levels'] = json.loads(a.tide_levels)
    if surfaces: water['surfaces'] = surfaces
    for x in surfaces:
        if x['kind'] == 'sea': water['sea_url'] = x['url']
    ok = ~np.ma.getmaskarray(ov) & np.isfinite(ov.filled(np.nan))
    v = ov.filled(np.nan)[ok]
    feat = {'type': 'Feature',
            'geometry': {'type': 'Polygon', 'coordinates': [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]]},
            'properties': dict(id=a.id, title=a.title, region='Kenai Peninsula', year=a.year, product=a.product,
                               native_res_m=res, coverage_km2=round(float(ok.sum()) * (res * f) ** 2 / 1e6, 2),
                               horizontal_crs=f'EPSG:{epsg}', vertical_datum='NAVD88 (GEOID12B)',
                               min_zoom=a.min_zoom, max_zoom=a.max_zoom, bounds=[round(x, 6) for x in b],
                               pmtiles_url=f'/lidar/dev/pmtiles/{a.id}.pmtiles', pmtiles_bytes=pm.stat().st_size,
                               slope_url=f'/lidar/dev/pmtiles/{a.id}_slope.pmtiles', slope_bytes=spm.stat().st_size,
                               slope_step=0.5, tiles_url=None, slope_tiles_url=None, fill_mode='holes', ortho_url=None,
                               cog_url=None, z_min=float(v.min()), z_max=float(v.max()), source=a.source, notes=a.notes, **water)}
    fc = json.loads(cat.read_text()) if cat.exists() else {'type': 'FeatureCollection', 'features': []}
    fc['features'] = [x for x in fc['features'] if x['properties']['id'] != a.id] + [feat]
    tmp = cat.with_suffix('.tmp'); tmp.write_text(json.dumps(fc)); os.replace(tmp, cat)   # atomic: Docker file sharing
    print(f'dev catalogue: {len(fc["features"])} surveys, {a.id} z {v.min():.1f}..{v.max():.1f} m')


if __name__ == '__main__':
    main()
