#!/usr/bin/env python3
"""Sample every susceptibility model at each landslide, and build the terrain
joint densities the Analysis scatter draws behind them.

    python3 tools/sample_susc.py            # everything
    python3 tools/sample_susc.py --values   # per-landslide values only
    python3 tools/sample_susc.py --terrain  # joint densities only

WHY OFFLINE
-----------
The web container has no GDAL, and these are whole-Alaska rasters. Both outputs
are small JSON files committed as static assets and read once by map.js.

THE THREE MODELS
----------------
  lw    USGS Belair and others (2024), 90 m, 0-81 = count of susceptible 10 m
        sub-cells in each 90 m cell
  n10   the same study's other variant, same grid and units
  dggs  Alaska DGGS PIR 2025-3 (Wikstrom Jones & Larsen 2025), 20 m, discrete
        classes 0/3/5/6/7/8/9/10, recovered by tools/harvest_dggs_susc.py

lw and n10 already share one 90 m EPSG:3338 grid. DGGS is 20 m on its own grid,
so it is resampled to the USGS grid with MODE (majority) -- the only correct
aggregation for a class raster; averaging classes would invent values that mean
nothing, and nearest would throw away the 20 m detail that makes the majority
meaningful.

OUTPUTS
-------
  inventory/static/inventory/susc_values.json
      {landslide_id: {lw, n10, dggs}}  -- null where a model has no coverage

  inventory/static/inventory/susc_terrain_density.json
      {pairs: {"lw|n10": {xsize, ysize, grid, max, total}, "lw|dggs": ..., ...}}
      Each grid is a 2D histogram of ALL Alaska terrain in that pair's value
      space, row-major [y*xsize + x]. It is the backdrop that turns "landslides
      are here" into "landslides are here MORE than terrain is", which is the
      only version of the plot that says anything.

      DGGS axes are indexed by CLASS POSITION (0..7 for the eight classes), not
      by class value, so the axis is evenly spaced -- the values are ordinal
      labels, not a measured quantity, and spacing them 0,3,5,6,7,8,9,10 would
      imply a linearity the model does not claim.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

ROOT = Path(__file__).resolve().parent.parent
LW = ROOT / 'data/usgs_susceptibility/lw_susc/lw_ak.tif'
N10 = ROOT / 'data/usgs_susceptibility/n10_susc/n10_ak.tif'
DGGS = Path('/Volumes/Nunatak/lidar_src/dggs_susc/dggs_susc_20m.vrt')
OUT_VALUES = ROOT / 'inventory/static/inventory/susc_values.json'
OUT_TERRAIN = ROOT / 'inventory/static/inventory/susc_terrain_density.json'

USGS_NODATA = 2147483647
USGS_MAX = 81                      # 0..81 inclusive -> 82 bins
DGGS_CLASSES = [0, 3, 5, 6, 7, 8, 9, 10]
DGGS_WATER = 200
DGGS_NODATA = 255
CLASS_INDEX = {c: i for i, c in enumerate(DGGS_CLASSES)}


CENTROIDS = ROOT / 'data/landslide_centroids_3338.json'
CENTROID_SQL = """
    SELECT landslide_id,
           ST_X(ST_Transform(ST_Centroid(ST_Collect(geom)), 3338)),
           ST_Y(ST_Transform(ST_Centroid(ST_Collect(geom)), 3338))
      FROM landslide_polygons GROUP BY landslide_id
"""


def landslide_points():
    """(id, x, y) in EPSG:3338 for every landslide centroid.

    Read from data/landslide_centroids_3338.json, which the web container
    writes -- the split exists because Django and PostGIS live in the
    container while GDAL and the rasters live out here, and neither side has
    both. Refresh it with:

        docker compose exec -T web python -c "..."   # see CENTROID_SQL

    or just re-run this after adding landslides.
    """
    if not CENTROIDS.is_file():
        sys.exit(f'missing {CENTROIDS}\n'
                 f'  generate it in the web container with CENTROID_SQL '
                 f'(see this function\'s docstring)')
    return [tuple(r) for r in json.loads(CENTROIDS.read_text())]


def sample_values():
    pts = landslide_points()
    print(f'  {len(pts)} landslide centroids')
    out = {}
    xy = [(x, y) for _, x, y in pts]
    with rasterio.open(LW) as lw, rasterio.open(N10) as n10, rasterio.open(DGGS) as dg:
        lwv = [v[0] for v in lw.sample(xy)]
        n10v = [v[0] for v in n10.sample(xy)]
        dgv = [v[0] for v in dg.sample(xy)]
    for (lid, _, _), a, b, c in zip(pts, lwv, n10v, dgv):
        rec = {}
        rec['lw'] = None if a == USGS_NODATA else int(a)
        rec['n10'] = None if b == USGS_NODATA else int(b)
        rec['dggs'] = int(c) if int(c) in CLASS_INDEX else None
        out[str(lid)] = rec
    have = {k: sum(1 for r in out.values() if r[k] is not None) for k in ('lw', 'n10', 'dggs')}
    print('  coverage: ' + ', '.join(f'{k}={v}' for k, v in have.items()))
    OUT_VALUES.write_text(json.dumps(out, separators=(',', ':')))
    print(f'  wrote {OUT_VALUES} ({OUT_VALUES.stat().st_size/1024:.1f} KB)')


def _dggs_on_usgs_grid(ref):
    """DGGS classes resampled to the USGS 90 m grid by majority."""
    return WarpedVRT(rasterio.open(DGGS), crs=ref.crs, transform=ref.transform,
                     width=ref.width, height=ref.height,
                     resampling=Resampling.mode, nodata=DGGS_NODATA)


def terrain_density():
    """Row-by-row 2D histograms for the three model pairs."""
    pairs = {'lw|n10': (USGS_MAX + 1, USGS_MAX + 1),
             'lw|dggs': (USGS_MAX + 1, len(DGGS_CLASSES)),
             'n10|dggs': (USGS_MAX + 1, len(DGGS_CLASSES))}
    grids = {k: np.zeros(v[0] * v[1], dtype=np.int64) for k, v in pairs.items()}
    lut = np.full(256, -1, dtype=np.int16)
    for c, i in CLASS_INDEX.items():
        lut[c] = i
    with rasterio.open(LW) as lw, rasterio.open(N10) as n10:
        with _dggs_on_usgs_grid(lw) as dg:
            H, W = lw.height, lw.width
            step = 512
            for r0 in range(0, H, step):
                h = min(step, H - r0)
                win = rasterio.windows.Window(0, r0, W, h)
                a = lw.read(1, window=win)
                b = n10.read(1, window=win)
                c = dg.read(1, window=win)
                ok_ab = (a != USGS_NODATA) & (b != USGS_NODATA)
                ci = lut[np.clip(c, 0, 255)]
                ok_c = ci >= 0
                if ok_ab.any():
                    np.add.at(grids['lw|n10'],
                              b[ok_ab].astype(np.int64) * (USGS_MAX + 1) + a[ok_ab].astype(np.int64), 1)
                m = (a != USGS_NODATA) & ok_c
                if m.any():
                    np.add.at(grids['lw|dggs'],
                              ci[m].astype(np.int64) * (USGS_MAX + 1) + a[m].astype(np.int64), 1)
                m = (b != USGS_NODATA) & ok_c
                if m.any():
                    np.add.at(grids['n10|dggs'],
                              ci[m].astype(np.int64) * (USGS_MAX + 1) + b[m].astype(np.int64), 1)
                if (r0 // step) % 8 == 0:
                    print(f'    row {r0}/{H}', flush=True)
    out = {'cell_km2': 0.09 * 0.09, 'dggs_classes': DGGS_CLASSES, 'pairs': {}}
    for k, (xs, ys) in pairs.items():
        g = grids[k]
        out['pairs'][k] = {'xsize': xs, 'ysize': ys, 'max': int(g.max()),
                           'total': int(g.sum()), 'grid': g.tolist()}
        print(f'  {k}: {int(g.sum()):,} cells, max {int(g.max()):,}')
    OUT_TERRAIN.write_text(json.dumps(out, separators=(',', ':')))
    print(f'  wrote {OUT_TERRAIN} ({OUT_TERRAIN.stat().st_size/1024:.0f} KB)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--values', action='store_true')
    ap.add_argument('--terrain', action='store_true')
    a = ap.parse_args()
    both = not (a.values or a.terrain)
    if a.values or both:
        print('== sampling landslide centroids'); sample_values()
    if a.terrain or both:
        print('== terrain joint densities'); terrain_density()
    return 0


if __name__ == '__main__':
    sys.exit(main())
