#!/opt/anaconda3/bin/python3
"""Colour one statewide required-cohesion grid into a PMTiles overlay.

    bake_required.py DIR/creq.tif <id> <maxzoom> "<title>" [--dest DIR]

Each tile at maxzoom is warped from Albers by itself, in its own part of Web
Mercator, so the Aleutians past 180 need no second pass. Colour classes are from
color_creq.txt. Lower zooms average the coloured picture (premultiplied), like the
slope layers: the measure never changes with zoom.
"""
import argparse
import math
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject, transform as tr
from rasterio.windows import Window

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'lidar'))
import build_lidar as BL  # noqa: E402

W = 20037508.342789244
ZMIN = 3
SIZE = 256


def ramp():
    v, c = [], []
    for line in open(HERE / 'color_creq.txt'):
        p = line.split()
        if p and p[0] not in ('#', 'nv'):
            v.append(float(p[0])); c.append([int(x) for x in p[1:5]])
    return np.array(v), np.array([[0, 0, 0, 0]] + c, np.uint8)


def merc_lat(lat):
    return W / math.pi * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def premul(a):
    f = a.astype(np.float32) / 255
    f[..., :3] *= f[..., 3:4]
    return f


def unpremul(f):
    a = f[..., 3:4]
    rgb = np.where(a > 1e-4, f[..., :3] / np.maximum(a, 1e-4), 0)
    return (np.concatenate([rgb, a], -1).clip(0, 1) * 255 + .5).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src'); ap.add_argument('id'); ap.add_argument('maxz', type=int); ap.add_argument('title')
    ap.add_argument('--dest', default='/Volumes/Nunatak/terrain_build/pmtiles')
    a = ap.parse_args()
    work = Path(a.src).parent / f'tiles_{a.id}'
    shutil.rmtree(work, ignore_errors=True)
    breaks, lut = ramp()
    n = 2 ** a.maxz
    res = 2 * W / n
    px = res / SIZE
    done = {}
    with rasterio.open(a.src) as d:
        ny, nx = d.height, d.width
        b = d.bounds
        xs = list(range(0, int((-128 + 180) / 360 * n) + 1)) + list(range(int((170 + 180) / 360 * n), n))
        r0 = int((W - merc_lat(72.5)) / res); r1 = int((W - merc_lat(50)) / res)
        for x in xs:
            for y in range(r0, r1 + 1):
                mx0, my1 = -W + x * res, W - y * res
                s = np.linspace(0, res, 6)
                ex = np.concatenate([mx0 + s, mx0 + s, np.full(6, mx0), np.full(6, mx0 + res)])
                ey = np.concatenate([np.full(6, my1), np.full(6, my1 - res), my1 - s, my1 - s])
                ax, ay = tr('EPSG:3857', 'EPSG:3338', ex.tolist(), ey.tolist())
                if max(ax) < b.left or min(ax) > b.right or max(ay) < b.bottom or min(ay) > b.top:
                    continue
                pad = 4
                c0 = max(0, int((min(ax) - b.left) / d.res[0]) - pad); c1 = min(nx, int((max(ax) - b.left) / d.res[0]) + pad)
                l0 = max(0, int((b.top - max(ay)) / d.res[1]) - pad); l1 = min(ny, int((b.top - min(ay)) / d.res[1]) + pad)
                if c1 <= c0 or l1 <= l0:
                    continue
                win = Window(c0, l0, c1 - c0, l1 - l0)
                v = d.read(1, window=win)
                if not (v >= breaks[0]).any():
                    continue
                out = np.full((SIZE, SIZE), -1, np.float32)
                reproject(v, out, src_transform=d.window_transform(win), src_crs=d.crs, src_nodata=-1,
                          dst_transform=from_origin(mx0, my1, px, px), dst_crs='EPSG:3857', dst_nodata=-1,
                          resampling=Resampling.nearest)
                if not (out >= breaks[0]).any():
                    continue
                img = lut[np.digitize(out, breaks)]
                p = work / str(a.maxz) / str(x) / f'{y}.png'
                p.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(img, 'RGBA').save(p, optimize=True)
                done[(x, y)] = 1
    print(f'z{a.maxz}: {len(done)} tiles', flush=True)
    for z in range(a.maxz - 1, ZMIN - 1, -1):
        parents = {(x >> 1, y >> 1) for x, y in done}
        for X, Y in parents:
            f = np.zeros((SIZE * 2, SIZE * 2, 4), np.float32)
            for i in (0, 1):
                for j in (0, 1):
                    p = work / str(z + 1) / str(2 * X + i) / f'{2 * Y + j}.png'
                    if p.exists():
                        f[j * SIZE:(j + 1) * SIZE, i * SIZE:(i + 1) * SIZE] = premul(np.array(Image.open(p).convert('RGBA')))
            f = f.reshape(SIZE, 2, SIZE, 2, 4).mean(axis=(1, 3))
            p = work / str(z) / str(X) / f'{Y}.png'
            p.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(unpremul(f), 'RGBA').save(p, optimize=True)
        done = parents
        print(f'z{z}: {len(done)} tiles', flush=True)
    mb = work.parent / f'{a.id}.mbtiles'
    BL.dir_to_mbtiles(work, mb, {'id': a.id, 'title': a.title, 'min_zoom': ZMIN, 'max_zoom': a.maxz}, 'png', a.title)
    final = Path(a.dest) / f'{a.id}.pmtiles'
    subprocess.run(['/opt/homebrew/bin/pmtiles', 'convert', '--tmpdir', str(work.parent), str(mb), str(final)], check=True)
    mb.unlink(); shutil.rmtree(work)
    print(f'{final}: {final.stat().st_size / 2**20:.1f} MB', flush=True)


if __name__ == '__main__':
    main()
