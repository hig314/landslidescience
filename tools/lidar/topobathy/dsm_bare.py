#!/usr/bin/env python3
"""A surface model (photogrammetric DSM) as BARE GROUND where it is bare -- a fallback layer beyond a lidar.

    /opt/anaconda3/bin/python3 tools/lidar/topobathy/dsm_bare.py composites/grewingk/recipe.json \
        --dsm /Volumes/Nunatak/lidar_build/cog/dixon_2023.tif --ref /Volumes/Nunatak/lidar_build/ctx_3dep/all.vrt \
        --lidar /Volumes/.../dtm_1m.tif --lidar-shift 0.36 --exclude a.tif b.tif ... --out sfm_bare.tif

Hig (2026-10-14): beyond the Grewingk 2021 lidar the 2023 SfM is likely better than 3DEP.  It is a DSM:
measured against the lidar near its edge it reads 0 to 0.5 m off on bare ground but 3-7 m HIGH over the
western forest and the alder, and the ortho's greenness does not find the canopy (dark spruce and shadow
read least green).  What does: the DSM against a bare-earth model.  Where the DSM's 30 m median stands
< BARE_M above 3DEP it matches the lidar (median -0.3 m on 1.0 M stable overlap cells); > 2 m it is mostly
canopy (lidar - DSM < -2 m on 54-86 % of cells).  So: the DSM where bare, nothing elsewhere (the layer below,
3DEP, stands in under canopy), levelled to the lidar by one constant measured on the bare overlap, and kept
on a band BAND_M inside the lidar's edge so the lidar's feather has it underneath (composite.post_masks).
Cells in any --exclude mask (lakes, ice, retreat: SfM over water is poor) are left out.
"""
import argparse, json, sys
from pathlib import Path
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from scipy import ndimage

BARE_M = 1.0        # DSM 30 m median above the bare-earth reference: below this, bare
WIN_PX = 15         # the median window (cells): 30 m at 2 m
BAND_M = 60.0       # kept this far inside the lidar's edge (under its feather)
MIN_M2 = 2000.0     # bare patches smaller than this dropped (despeckle)


def on_grid(path, grid, rs):
    g = grid; res = g['res_m']; x0, y0, x1, y1 = g['bounds']
    W, H = int(round((x1 - x0) / res)), int(round((y1 - y0) / res))
    T = rasterio.transform.from_origin(x0, y1, res, res)
    with rasterio.open(path) as s, WarpedVRT(s, crs=f"EPSG:{g['epsg']}", transform=T, width=W, height=H, resampling=rs) as v:
        return v.read(1, masked=True).astype(np.float32).filled(np.nan), T


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('recipe'); ap.add_argument('--dsm', required=True); ap.add_argument('--ref', required=True)
    ap.add_argument('--lidar', required=True); ap.add_argument('--lidar-shift', type=float, default=0.0)
    ap.add_argument('--exclude', nargs='*', default=[]); ap.add_argument('--out', required=True)
    a = ap.parse_args()
    grid = json.loads(Path(a.recipe).read_text())['grid']; res = grid['res_m']
    dsm, T = on_grid(a.dsm, grid, Resampling.average)
    ref, _ = on_grid(a.ref, grid, Resampling.bilinear)
    lid, _ = on_grid(a.lidar, grid, Resampling.average); lid = lid + a.lidar_shift
    excl = np.zeros(dsm.shape, bool)
    for p in a.exclude:
        m, _ = on_grid(p, grid, Resampling.nearest); excl |= np.nan_to_num(m) > 0
    h = ndimage.median_filter(np.nan_to_num(dsm - ref, nan=99.0), size=WIN_PX)
    bare = np.isfinite(dsm) & (h < BARE_M) & ~excl
    bare = ndimage.binary_opening(bare, iterations=1)
    lab, n = ndimage.label(bare)
    if n:
        sz = ndimage.sum(bare, lab, np.arange(1, n + 1)) * res * res
        bare &= ~np.isin(lab, np.flatnonzero(sz < MIN_M2) + 1)
    # the level: lidar - DSM on bare, gentle overlap
    gy, gx = np.gradient(np.nan_to_num(lid), res); slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    ov = bare & np.isfinite(lid) & (slope < 25)
    dz = (lid - dsm)[ov]
    off = float(np.median(dz)) if dz.size > 1000 else 0.0
    nmad = float(1.4826 * np.median(np.abs(dz - off))) if dz.size > 1000 else None
    has = np.isfinite(lid)
    band = has & (ndimage.distance_transform_edt(has) * res <= BAND_M)
    keep = bare & (~has | band)
    out = np.where(keep, dsm + off, np.nan).astype(np.float32)
    with rasterio.open(a.out, 'w', driver='GTiff', height=out.shape[0], width=out.shape[1], count=1, dtype='float32',
                       crs=f"EPSG:{grid['epsg']}", transform=T, nodata=np.nan, compress='deflate', predictor=3, tiled=True) as ds:
        ds.write(out, 1)
    info = dict(bare_m=BARE_M, win_m=WIN_PX * res, band_m=BAND_M, offset_m=round(off, 2), offset_nmad_m=round(nmad, 2) if nmad else None,
                n_overlap=int(dz.size), dsm_km2=round(float(np.isfinite(dsm).sum()) * res * res / 1e6, 2),
                bare_km2=round(float(bare.sum()) * res * res / 1e6, 2),
                beyond_lidar_km2=round(float((keep & ~has).sum()) * res * res / 1e6, 2),
                canopy_beyond_lidar_km2=round(float((np.isfinite(dsm) & ~bare & ~excl & ~has).sum()) * res * res / 1e6, 2))
    Path(a.out).with_suffix('.json').write_text(json.dumps(info, indent=1))
    print(json.dumps(info))


if __name__ == '__main__':
    main()
