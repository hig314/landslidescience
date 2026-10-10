#!/opt/anaconda3/bin/python3
"""Two small raster calculations done in Python because Homebrew's gdal_calc.py
(GDAL 3.13 / Python 3.14) rejects its own -A argument ("did not expand to any
file") for any path, 2026-10-09. Chunked, same profile as the input.

    rastercalc.py northness <slope.tif> <aspect.tif> <out.tif>
        cos(aspect) * sin(slope): +1 a steep north face, -1 a steep south face
    rastercalc.py pastick <in.img> <out.tif>
        Pastick 2015 Byte percent -> fraction; codes 101-105 (water, ice,
        developed, barren, cultivated) and 255 -> nodata
    rastercalc.py mask <in.tif> <valid.tif> <out.tif>
        in where valid.tif has data, else nodata (same grid). The 60 m
        downscale is masked to the NEAREST 1 km source cell's validity:
        bilinear parameter interpolation otherwise reaches ~10 km past the
        last fitted block (sea, and Obu's masked glaciers came out as
        probability 1 by extrapolation).
    rastercalc.py nan2nodata <in.tif> <out.tif>
        NaN -> -9999. Obu's MAGT and MAGTSTD rasters hold NaN over near-shore
        sea (their declared nodata only offshore); NaN survives gdalwarp and
        gdaldem color-relief paints it with the top ramp colour.
"""
import sys
import numpy as np
import rasterio
from rasterio.windows import Window

ROWS = 1024


def run(op, srcs, out):
    ds = [rasterio.open(s) for s in srcs]
    prof = ds[0].profile.copy()
    prof.update(dtype='float32', nodata=-9999, compress='ZSTD', predictor=3, tiled=True,
                blockxsize=512, blockysize=512, bigtiff='YES', count=1, driver='GTiff')
    H, W = ds[0].height, ds[0].width
    with rasterio.open(out, 'w', **prof) as dst:
        for r0 in range(0, H, ROWS):
            win = Window(0, r0, W, min(ROWS, H - r0))
            a = [d.read(1, window=win).astype(np.float64) for d in ds]
            if op == 'northness':
                slope, aspect = a
                bad = (slope == ds[0].nodata) | (aspect == ds[1].nodata) | (slope < 0)
                v = np.cos(np.radians(aspect)) * np.sin(np.radians(slope))
            elif op == 'pastick':
                x = a[0]
                bad = x > 100
                v = x / 100.0
            elif op == 'mask':
                v, m = a
                bad = ~np.isfinite(v) | (v == ds[0].nodata) | ~np.isfinite(m) | (m == ds[1].nodata)
            else:
                v = a[0]
                bad = ~np.isfinite(v) | (v == ds[0].nodata)
            v = v.astype(np.float32); v[bad] = -9999
            dst.write(v, 1, window=win)


if __name__ == '__main__':
    op = sys.argv[1]
    if op in ('northness', 'mask'):
        run(op, sys.argv[2:4], sys.argv[4])
    elif op in ('pastick', 'nan2nodata'):
        run(op, sys.argv[2:3], sys.argv[3])
    else:
        sys.exit(__doc__)
