#!/usr/bin/env python3
"""Water in a HYDROFLATTENED bare-earth model: patches that are exactly flat.

    /opt/anaconda3/bin/python3 tools/lidar/topobathy/flat_water.py composites/grewingk/recipe.json \
        --dem /Volumes/Nunatak/lidar_build/ctx_3dep/all.vrt --out water_3dep.tif [--min-m2 5000]

A vendor or 3DEP DTM holds each lake as one constant surface; real ground, however gentle (outwash gravel,
tidal flats), is not constant to the centimetre.  Cells whose 3x3 range (on the recipe grid, bilinear)
is below RANGE_M, in connected patches of at least --min-m2, are water.  Used as a class source for a
fallback layer, so its lakes get a water class and a basin where it is the only data (Emerald Lake,
2026-10-14: 3DEP was its only surface and it read as flat ground at 351 m).

Output codes: 1 = a hydroflattened lake (level above --min-level), 2 = 3DEP's SEA (a flat patch at exactly
0 m, |level| < SEA_TOL_M).  The recipe maps 2 to void (2026-10-20, Hig: the outer coast stood as a flat 0 m
plateau between the coarse bathymetry and the land, where a beach belongs): the sea is not an observation
of anything, so the fill builds the shore there between the land and the marine priors, as it does
wherever else the sea floor between MLLW and the shore is unobserved.
"""
import argparse, json
from pathlib import Path
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from scipy import ndimage

RANGE_M = 0.01
SEA_TOL_M = 0.05
RIM_TOL_M = 0.05


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('recipe'); ap.add_argument('--dem', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--min-m2', type=float, default=5000.0)
    ap.add_argument('--min-level', type=float, default=0.5, help='patches at or below this level (m) are left out: 3DEP holds the SEA as exactly 0 m, and the sea is the priors\' and the tide gauge\'s, not a lake')
    a = ap.parse_args()
    g = json.loads(Path(a.recipe).read_text())['grid']; res = g['res_m']; x0, y0, x1, y1 = g['bounds']
    W, H = int(round((x1 - x0) / res)), int(round((y1 - y0) / res))
    T = rasterio.transform.from_origin(x0, y1, res, res)
    with rasterio.open(a.dem) as s, WarpedVRT(s, crs=f"EPSG:{g['epsg']}", transform=T, width=W, height=H, resampling=Resampling.bilinear) as v:
        z = v.read(1, masked=True).astype(np.float32).filled(np.nan)
    zf = np.nan_to_num(z, nan=-1e5)
    flat = np.isfinite(z) & (ndimage.maximum_filter(zf, 3) - ndimage.minimum_filter(zf, 3) < RANGE_M)
    lab, n = ndimage.label(flat)
    rows = []
    if n:
        sz = ndimage.sum(flat, lab, np.arange(1, n + 1)) * res * res
        lev = ndimage.median(z, lab, np.arange(1, n + 1))
        keep = np.flatnonzero((sz >= a.min_m2) & (lev > a.min_level)) + 1
        sea = np.isin(lab, np.flatnonzero((sz >= a.min_m2) & (np.abs(lev) < SEA_TOL_M)) + 1)
        flat = np.isin(lab, keep)
        for k in keep:
            r, c = ndimage.center_of_mass(lab == k)
            rows.append(dict(x=round(x0 + (c + 0.5) * res), y=round(y1 - (r + 0.5) * res), area_m2=int(sz[k - 1]),
                             level_m=round(float(np.nanmedian(z[lab == k])), 2)))
    else:
        sea = np.zeros_like(flat)
    # the patch's RIM: a cell at the patch's level beside it is the same flattened water (its 3x3 range includes the
    # shore, so the flatness test misses it); grown twice (joint pilot run 6, 2026-10-22: 3DEP shore cells at 41.3 m,
    # the 2012 lake level, stood as ground in the 48.9 m shore and read as 7 m pits)
    if n and len(keep):
        lev_map = np.zeros(n + 1, np.float32); lev_map[keep] = lev[keep - 1]
        for _ in range(2):
            near = ndimage.grey_dilation(np.where(flat, lab, 0), size=3)
            rim = ~flat & np.isfinite(z) & (near > 0) & (np.abs(z - lev_map[near]) < RIM_TOL_M)
            flat |= rim; lab = np.where(rim, near, lab)
    flat = ndimage.binary_closing(flat, iterations=1) & np.isfinite(z)
    sea = ndimage.binary_closing(sea, iterations=1) & np.isfinite(z) & ~flat
    with rasterio.open(a.out, 'w', driver='GTiff', height=H, width=W, count=1, dtype='uint8', crs=f"EPSG:{g['epsg']}",
                       transform=T, nodata=255, compress='deflate') as ds:
        ds.write(np.where(flat, 1, np.where(sea, 2, 0)).astype(np.uint8), 1)
    Path(a.out).with_suffix('.json').write_text(json.dumps(dict(range_m=RANGE_M, min_m2=a.min_m2, lakes=rows, sea_km2=round(float(sea.sum()) * res * res / 1e6, 3)), indent=1))
    print(f'3DEP sea (0 m): {sea.sum() * res * res / 1e6:.3f} km2 (code 2)')
    print(f'{len(rows)} flat water patches: ' + ', '.join(f"{r['area_m2']} m2 at {r['level_m']} m" for r in rows))


if __name__ == '__main__':
    main()
