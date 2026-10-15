#!/usr/bin/env python3
"""Water in a HYDROFLATTENED bare-earth model: patches that are exactly flat.

    /opt/anaconda3/bin/python3 tools/lidar/topobathy/flat_water.py composites/grewingk/recipe.json \
        --dem /Volumes/Nunatak/lidar_build/ctx_3dep/all.vrt --out water_3dep.tif [--min-m2 5000]

A vendor or 3DEP DTM holds each lake as one constant surface; real ground, however gentle (outwash gravel,
tidal flats), is not constant to the centimetre.  Cells whose 3x3 range (on the recipe grid, bilinear)
is below RANGE_M, in connected patches of at least --min-m2, are water.  Used as a class source for a
fallback layer, so its lakes get a water class and a basin where it is the only data (Emerald Lake,
2026-10-14: 3DEP was its only surface and it read as flat ground at 351 m).
"""
import argparse, json
from pathlib import Path
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from scipy import ndimage

RANGE_M = 0.01


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('recipe'); ap.add_argument('--dem', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--min-m2', type=float, default=5000.0)
    a = ap.parse_args()
    g = json.loads(Path(a.recipe).read_text())['grid']; res = g['res_m']; x0, y0, x1, y1 = g['bounds']
    W, H = int(round((x1 - x0) / res)), int(round((y1 - y0) / res))
    T = rasterio.transform.from_origin(x0, y1, res, res)
    with rasterio.open(a.dem) as s, WarpedVRT(s, crs=f"EPSG:{g['epsg']}", transform=T, width=W, height=H, resampling=Resampling.bilinear) as v:
        z = v.read(1, masked=True).astype(np.float64).filled(np.nan)
    zf = np.nan_to_num(z, nan=-1e5)
    flat = np.isfinite(z) & (ndimage.maximum_filter(zf, 3) - ndimage.minimum_filter(zf, 3) < RANGE_M)
    lab, n = ndimage.label(flat)
    rows = []
    if n:
        sz = ndimage.sum(flat, lab, np.arange(1, n + 1)) * res * res
        keep = np.flatnonzero(sz >= a.min_m2) + 1
        flat = np.isin(lab, keep)
        for k in keep:
            r, c = ndimage.center_of_mass(lab == k)
            rows.append(dict(x=round(x0 + (c + 0.5) * res), y=round(y1 - (r + 0.5) * res), area_m2=int(sz[k - 1]),
                             level_m=round(float(np.nanmedian(z[lab == k])), 2)))
    flat = ndimage.binary_closing(flat, iterations=1) & np.isfinite(z)
    with rasterio.open(a.out, 'w', driver='GTiff', height=H, width=W, count=1, dtype='uint8', crs=f"EPSG:{g['epsg']}",
                       transform=T, nodata=255, compress='deflate') as ds:
        ds.write(flat.astype(np.uint8), 1)
    Path(a.out).with_suffix('.json').write_text(json.dumps(dict(range_m=RANGE_M, min_m2=a.min_m2, lakes=rows), indent=1))
    print(f'{len(rows)} flat water patches: ' + ', '.join(f"{r['area_m2']} m2 at {r['level_m']} m" for r in rows))


if __name__ == '__main__':
    main()
