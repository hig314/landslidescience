#!/usr/bin/env python3
"""mask_fill.py -- turn a vendor's constant fill value into real nodata,
without touching genuine pixels that happen to hold the same value.

    tools/lidar/mask_fill.py SRC OUT --fill 0 [--coarse 8] [--nodata -99999]

The problem (matsu_2019, 2026-09-12): a SAGA grid whose voids outside the
lidar swath are written as 0.00 rather than its declared nodata. Masking
every 0 would also erase true 0.00 m ground on the tidal flats. The fill is
distinguishable by its shape, not its value: it is a contiguous region of
the fill value that touches the raster border. So:

  1. read the grid decimated by --coarse (nearest), label connected regions
     of the fill value, keep the ones that touch the border (the voids);
  2. dilate that coarse mask by one cell, so its boundary certainly covers
     the true swath edge;
  3. stream the grid at full resolution and set a pixel to nodata only where
     it equals the fill value AND lies inside the dilated coarse mask.

A genuine 0.00 m pixel more than one coarse cell inside the swath survives;
one right at the swath edge is lost, which is the accepted cost. The output
is a tiled ZSTD GeoTIFF carrying the declared nodata, for build_lidar.py
to warp as usual.
"""
import argparse
import sys
import time

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import Window
from scipy import ndimage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--fill", type=float, required=True, help="the vendor's fill value")
    ap.add_argument("--coarse", type=int, default=8, help="decimation for the region search")
    ap.add_argument("--nodata", type=float, default=None, help="nodata to write (default: the source's)")
    ap.add_argument("--rows", type=int, default=512, help="rows per streaming window")
    a = ap.parse_args()

    t0 = time.time()
    with rasterio.open(a.src) as src:
        nodata = a.nodata if a.nodata is not None else src.nodata
        if nodata is None:
            sys.exit("source has no nodata and --nodata not given")
        fill = np.float32(a.fill)
        f = a.coarse
        ch, cw = -(-src.height // f), -(-src.width // f)
        print(f"{src.width} x {src.height}; coarse {cw} x {ch} at 1/{f}", file=sys.stderr)
        coarse = src.read(1, out_shape=(ch, cw), resampling=Resampling.nearest)
        isfill = coarse == fill
        labels, n = ndimage.label(isfill)
        border = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
        border = border[border != 0]
        void = np.isin(labels, border)
        inner = isfill & ~void
        print(f"fill regions: {n}; touching the border: {len(border)} covering "
              f"{void.mean() * 100:.1f}% of the box; interior fill-valued pixels kept: "
              f"{int(inner.sum())} coarse cells ({time.time() - t0:.0f}s)", file=sys.stderr)
        void = ndimage.binary_dilation(void, iterations=1)

        prof = src.profile.copy()
        prof.update(driver="GTiff", dtype="float32", nodata=nodata, tiled=True,
                    blockxsize=512, blockysize=512, compress="zstd", zstd_level=9,
                    predictor=3, bigtiff="YES", num_threads="ALL_CPUS")
        masked = 0
        with rasterio.open(a.out, "w", **prof) as dst:
            for row in range(0, src.height, a.rows):
                h = min(a.rows, src.height - row)
                win = Window(0, row, src.width, h)
                arr = src.read(1, window=win)
                # coarse mask rows/cols for every fine pixel in this window
                cr = np.minimum((np.arange(row, row + h) // f), ch - 1)
                cc = np.minimum((np.arange(src.width) // f), cw - 1)
                m = void[cr][:, cc] & (arr == fill)
                masked += int(m.sum())
                arr[m] = nodata
                dst.write(arr, 1, window=win)
                if (row // a.rows) % 20 == 0:
                    print(f"  row {row}/{src.height} ({time.time() - t0:.0f}s)", file=sys.stderr)
        print(f"masked {masked} pixels ({masked / (src.width * src.height) * 100:.1f}% of the box) "
              f"-> {a.out} in {time.time() - t0:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
