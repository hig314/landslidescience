#!/opt/anaconda3/bin/python3
"""Coarse-to-fine against a brute-force dense search, same patch, both pairings."""
import sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import explore_patch as X, scoops as S
from rasterio.warp import transform as tr
from scipy.ndimage import binary_dilation
LAT, LON, CORE = 61.82, -149.25, 24000
(cx,), (cy,) = tr('EPSG:4326', 'EPSG:3338', [LON], [LAT])
half = CORE / 2 + 9000
lons, lats = tr('EPSG:3338', 'EPSG:4326', [cx - half, cx + half], [cy - half, cy + half])
src, tf = X.mercator_mosaic(min(lons), min(lats), max(lons), max(lats))
for v0, dx, phi in [(1e9, 200, 20), (1e8, 100, 30)]:
    r0 = 1.36 * v0 ** (1 / 3)
    pad = int(6 * r0 / dx) + 2; nc = int(CORE / dx); n = nc + 2 * pad
    dem = X.dem_for(src, tf, cx - n * dx / 2, cy + n * dx / 2, n, n, dx)[::-1].astype(float)
    g = S.Grid(dem, dx)
    zmin, zmax = float(dem.min()), float(dem.max()) + 40 * r0
    kw = dict(gamma=26.0, phis=[phi], edges=[v0, v0 * 10], workers=5, zspan=10 * r0)
    def run(xn, yn, f):
        return S.search(g, xn, yn, zmin, zmax, r0 / 2 * f, r0 / 4 * f, v0, v0 * 10, 0.1 * v0, **kw)
    crop = lambda c: c[0, 0, pad:pad + nc, pad:pad + nc]
    step_c = 3
    xa, ya = S.lattice(n, n, dx, 1, 1, n, n, step_c)
    t = time.time(); dense = run(*S.lattice(n, n, dx), 0.25); t_dense = time.time() - t
    ref = crop(dense['creq'])
    t = time.time(); co = run(xa, ya, 1.0); t_c = time.time() - t
    nb = co['node_best']
    side = int(round(len(xa) ** .5))
    print(f"\n{v0:.0e} dx {dx} phi {phi}: dense x4 {t_dense:.0f} s, needs {np.mean(ref > 0):.3f}, max {ref.max():.0f}; coarse {t_c:.0f} s; coarse best c percentiles {np.percentile(nb[nb > -1e29], [50, 90, 99]).round(0)}", flush=True)
    for tau in (50, 150, 300, 600):
        for f, st in ((1 / 3, 1),):
            grid = (nb >= -tau).reshape(side, side)
            grid = binary_dilation(grid, iterations=1)
            ii = np.arange(0, n)
            mask = np.zeros((n, n), bool)
            jj_c, ii_c = np.nonzero(grid)
            for jc, ic in zip(jj_c, ii_c):
                j0, i0 = jc * step_c, ic * step_c
                mask[j0:j0 + step_c, i0:i0 + step_c] = True
            jj, ii2 = np.nonzero(mask)
            xs, ys = (ii2 + .5) * dx, (jj + .5) * dx
            t = time.time(); fi = run(xs, ys, f); wall = time.time() - t
            c = np.maximum(crop(co['creq']), crop(fi['creq']))
            both = (c > 0) & (ref > 0)
            print(f"  tau {tau:4d}: nodes kept {mask.mean():.3f}  fine {wall:5.0f} s ({(wall + t_c) / t_dense:.3f} of dense)  found {both.sum() / max(1, (ref > 0).sum()):.3f}  ratio p50 {np.median(c[both] / ref[both]):.3f} p5 {np.percentile(c[both] / ref[both], 5):.3f}  max {c.max():.0f}", flush=True)
