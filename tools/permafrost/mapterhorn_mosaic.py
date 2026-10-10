#!/opt/anaconda3/bin/python3
"""Mapterhorn z11 terrarium tiles -> one Float32 elevation mosaic (EPSG:3857).

Input: permafrost_src/mapterhorn/11/<x>/<y>.webp (fetch_mapterhorn.sh), 512 px
terrarium WebP: h = R*256 + G + B/256 - 32768 metres. Output: a tiled, ZSTD
BigTIFF on the exact z11 pixel grid of the fetched tile range, nodata -9999
where a tile is missing. Resumable: a tile already written is recorded in
<out>.done and skipped on the next run, so it can follow the fetch.

    mapterhorn_mosaic.py --out /Volumes/Nunatak/permafrost_build/mapterhorn_z11.tif [--limit N]

The mosaic is the one heavy intermediate (~15 G px); everything downstream
(terrain60.sh) warps it once to the 60 m EPSG:3338 working grid.
"""
import argparse, io, math, sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import Window
from PIL import Image

SRC = Path('/Volumes/Nunatak/permafrost_src/mapterhorn/11')
Z, TILE = 11, 512
R = 6378137.0
WORLD = 2 * math.pi * R                       # 3857 world width, metres
RES = WORLD / (2 ** Z * TILE)                 # metres per pixel at z11 (equator)


def tile_origin(x, y):
    return -WORLD / 2 + x * TILE * RES, WORLD / 2 - y * TILE * RES


def decode(path):
    # GDAL's WEBP driver (through rasterio) rather than Pillow: Pillow's
    # decoder refused some of these tiles ("could not create decoder
    # object", 2026-10-09) while GDAL reads every one.
    try:
        with rasterio.open(path) as t:
            a = t.read().astype(np.float32)          # (bands, 512, 512)
        r, g, b = a[0], a[1], a[2]
    except Exception:
        a = np.asarray(Image.open(path).convert('RGB')).astype(np.float32)
        r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return r * 256 + g + b / 256 - 32768


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--limit', type=int, default=0, help='stop after N new tiles (testing)')
    args = ap.parse_args()
    tiles = sorted((int(p.parent.name), int(p.stem)) for p in SRC.glob('*/*.webp'))
    if not tiles:
        sys.exit('no tiles')
    xs = [t[0] for t in tiles]; ys = [t[1] for t in tiles]
    # The grid is the full fetch range (fetch_mapterhorn.sh), fixed so a
    # resumed run writes into the same file.
    x0, x1, y0, y1 = 56, 295, 423, 657
    out = Path(args.out)
    done_path = out.with_suffix('.done')
    done = set(done_path.read_text().split()) if done_path.exists() else set()
    width, height = (x1 - x0 + 1) * TILE, (y1 - y0 + 1) * TILE
    ox, oy = tile_origin(x0, y0)
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(out, 'w', driver='GTiff', width=width, height=height, count=1,
                           dtype='float32', crs='EPSG:3857',
                           transform=from_origin(ox, oy, RES, RES), nodata=-9999,
                           tiled=True, blockxsize=512, blockysize=512,
                           compress='ZSTD', predictor=3, zstd_level=9, bigtiff='YES', sparse_ok=True):
            pass
        print(f'created {out} {width}x{height} px, {RES:.3f} m/px at the equator')
    n_new = 0
    with rasterio.open(out, 'r+') as dst, open(done_path, 'a') as df:
        for x, y in tiles:
            key = f'{x}/{y}'
            if key in done:
                continue
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                continue
            try:
                h = decode(SRC / str(x) / f'{y}.webp')
            except Exception as e:
                # Leave the hole (nodata) and say so; the fetch is resumable
                # and a re-run writes the tile once it reads.
                print(f'  UNREADABLE {key}: {str(e)[:70]}', flush=True)
                continue
            dst.write(h, 1, window=Window((x - x0) * TILE, (y - y0) * TILE, TILE, TILE))
            df.write(key + '\n'); df.flush()
            n_new += 1
            if n_new % 500 == 0:
                print(f'  {n_new} tiles written', flush=True)
            if args.limit and n_new >= args.limit:
                break
    print(f'wrote {n_new} new tiles ({len(done) + n_new} of {len(tiles)} fetched; grid holds {(x1-x0+1)*(y1-y0+1)})')


if __name__ == '__main__':
    main()
