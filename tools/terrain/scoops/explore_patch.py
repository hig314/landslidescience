#!/opt/anaconda3/bin/python3
"""Required cohesion over one patch of terrain, per volume class and friction angle.

For every trial sphere, the cohesion needed for F = 1 (Bishop, dry, uniform); each
cell keeps the highest of any surface holding it. Topography is the only input:
no landslide, geology or strength data. Each class runs on its own grid (about 100
or more columns a surface), lattice proportional to its footprint.

    explore_patch.py --lat 61.82 --lon -149.25 --core 24000 --out DIR
Writes DIR/class_<V>.tif: bands = required c (kPa) at each phi, then the critical
volume at each phi, cropped to the core; DIR/run.json with parameters and counts.
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin, Affine
from rasterio.warp import Resampling, reproject, transform as tr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoops as S

SRC = Path('/Volumes/Nunatak/terrain_src/mapterhorn/12')
WORLD = 2 * math.pi * 6378137.0
PHIS = [0, 10, 20, 30, 40, 50]
GAMMA = 26.0       # kN/m3
# (lowest volume in class, grid in metres): about r0 / 6, r0 = 1.36 V^(1/3)
CLASSES = [(1e5, 10), (1e6, 20), (1e7, 50), (1e8, 100), (1e9, 200)]


def mercator_mosaic(w, s, e, n):
    """Heights from z12 terrarium tiles covering this lon/lat box."""
    N = 2 ** 12
    def tx(lon): return int((lon + 180) / 360 * N)
    def ty(lat):
        r = math.radians(lat)
        return int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * N)
    x0, x1, y0, y1 = tx(w), tx(e), ty(n), ty(s)
    a = np.zeros(((y1 - y0 + 1) * 512, (x1 - x0 + 1) * 512), np.float32)
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            with rasterio.open(SRC / str(x) / f'{y}.webp') as d:
                b = d.read().astype(np.float32)
            a[(y - y0) * 512:(y - y0 + 1) * 512, (x - x0) * 512:(x - x0 + 1) * 512] = b[0] * 256 + b[1] + b[2] / 256 - 32768
    res = WORLD / (N * 512)
    return a, Affine(res, 0, -WORLD / 2 + x0 * 512 * res, 0, -res, WORLD / 2 - y0 * 512 * res)


def dem_for(src, src_tf, x0, y1, nx, ny, dx):
    """Albers grid; north-up. Average when coarser than the source, else bilinear."""
    out = np.zeros((ny, nx), np.float32)
    reproject(src, out, src_transform=src_tf, src_crs='EPSG:3857',
              dst_transform=from_origin(x0, y1, dx, dx), dst_crs='EPSG:3338',
              resampling=Resampling.average if dx > 12 else Resampling.bilinear)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lat', type=float, required=True)
    ap.add_argument('--lon', type=float, required=True)
    ap.add_argument('--core', type=float, default=24000)
    ap.add_argument('--out', required=True)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--classes', default='')
    ap.add_argument('--dx-scale', type=float, default=1.0, help='grid and lattice finer (< 1): convergence test')
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (cx,), (cy,) = tr('EPSG:4326', 'EPSG:3338', [a.lon], [a.lat])
    pad_max = 6 * 1.36 * (CLASSES[-1][0]) ** (1 / 3)
    half = a.core / 2 + pad_max + 1000
    xs = [cx - half, cx + half, cx - half, cx + half]; ys = [cy - half, cy - half, cy + half, cy + half]
    lons, lats = tr('EPSG:3338', 'EPSG:4326', xs, ys)
    src, tf = mercator_mosaic(min(lons), min(lats), max(lons), max(lats))
    runs = []
    for v0, dx0 in CLASSES:
        if a.classes and f'{v0:.0e}' not in a.classes.split(','):
            continue
        dx = dx0 * a.dx_scale
        r0 = 1.36 * v0 ** (1 / 3)
        pad = int(6 * r0 / dx) + 2
        nc = int(a.core / dx)
        nx = ny = nc + 2 * pad
        x0, y1 = cx - nx * dx / 2, cy + ny * dx / 2
        dem = dem_for(src, tf, x0, y1, nx, ny, dx)
        g = S.Grid(dem[::-1], dx)
        step = max(1, int(round((r0 / 2) / dx)))
        xn, yn = S.lattice(nx, ny, dx, 1, 1, nx, ny, step)
        v1 = v0 * 10
        t = time.time()
        r = S.search(g, xn, yn, float(dem.min()), float(dem.max()) + 40 * r0, r0 / 2, r0 / 4, v0, v1, 0.1 * v0,
                     gamma=GAMMA, phis=PHIS, edges=[v0, v1], workers=a.workers, zspan=10 * r0, ordinary=True)
        wall = time.time() - t
        def crop(x):
            return x[..., pad:pad + nc, pad:pad + nc][..., ::-1, :]    # north-up core
        creq = crop(r['creq'][:, 0]); vol = crop(r['creq_vol'][:, 0]); cord = crop(r['creq_ord'][:, 0])
        core_tf = from_origin(x0 + pad * dx, y1 - pad * dx, dx, dx)
        tag = f'{v0:.0e}'
        prof = dict(driver='GTiff', height=nc, width=nc, count=3 * len(PHIS), dtype='float32', crs='EPSG:3338',
                    transform=core_tf, compress='deflate', predictor=3, tiled=True)
        with rasterio.open(out / f'class_{tag}.tif', 'w', **prof) as d:
            d.write(np.concatenate([creq, vol, cord]))
            for i, p in enumerate(PHIS):
                d.set_band_description(i + 1, f'creq_kPa_phi{p}')
                d.set_band_description(i + 1 + len(PHIS), f'critical_volume_m3_phi{p}')
                d.set_band_description(i + 1 + 2 * len(PHIS), f'creq_ordinary_kPa_phi{p}')
        with rasterio.open(out / f'dem_{tag}.tif', 'w', **{**prof, 'count': 1}) as d:
            d.write(dem[pad:pad + nc, pad:pad + nc], 1)
        rec = dict(v0=v0, v1=v1, dx=dx, r0=r0, step=step, nodes=len(xn), wall_s=round(wall), **r['stats'])
        rec['zero_frac'] = [round(float(np.mean(creq[i] <= 0)), 3) for i in range(len(PHIS))]
        rec['p50_kPa'] = [round(float(np.median(creq[i])), 1) for i in range(len(PHIS))]
        rec['max_kPa'] = [round(float(creq[i].max()), 1) for i in range(len(PHIS))]
        runs.append(rec)
        print(rec, flush=True)
        (out / 'run.json').write_text(json.dumps(dict(lat=a.lat, lon=a.lon, core=a.core, phis=PHIS, gamma=GAMMA, classes=runs), indent=1))


if __name__ == '__main__':
    main()
