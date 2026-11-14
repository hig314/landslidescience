#!/opt/anaconda3/bin/python3
"""Does the answer depend on the search lattice? Same ground and grid; denser and
shifted lattices. Near the zero threshold few surfaces qualify, so this matters more
there than the grid does. Prints share of cells needing c, percentiles, peak, and
agreement with the densest run."""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import explore_patch as X
import scoops as S
from rasterio.warp import transform as tr

LAT, LON, CORE = 61.82, -149.25, 24000
(cx,), (cy,) = tr('EPSG:4326', 'EPSG:3338', [LON], [LAT])
half = CORE / 2 + 9000
lons, lats = tr('EPSG:3338', 'EPSG:4326', [cx - half, cx + half], [cy - half, cy + half])
src, tf = X.mercator_mosaic(min(lons), min(lats), max(lons), max(lats))
for v0, dx, phi in [(1e9, 200, 20), (1e8, 100, 30)]:
    r0 = 1.36 * v0 ** (1 / 3)
    pad = int(6 * r0 / dx) + 2
    nc = int(CORE / dx); n = nc + 2 * pad
    dem = X.dem_for(src, tf, cx - n * dx / 2, cy + n * dx / 2, n, n, dx)[::-1].astype(float)
    g = S.Grid(dem, dx)
    runs = []
    for label, step_f, dz_f, off in [('as run', 1.0, 1.0, 0), ('shifted', 1.0, 1.0, 1), ('x2 nodes+z+r', 0.5, 0.5, 0),
                                     ('x3', 1 / 3, 1 / 3, 0), ('x4', 0.25, 0.25, 0)]:
        step = max(1, int(round(r0 / 2 / dx * step_f)))
        xn, yn = S.lattice(n, n, dx, 1 + off, 1 + off, n, n, step)
        t = time.time()
        r = S.search(g, xn, yn, float(dem.min()) + off * r0 / 7, float(dem.max()) + 40 * r0, r0 / 2 * dz_f, r0 / 4 * dz_f,
                     v0, v0 * 10, 0.1 * v0, gamma=26.0, phis=[phi], edges=[v0, v0 * 10], workers=5, zspan=10 * r0)
        c = r['creq'][0, 0, pad:pad + nc, pad:pad + nc]
        runs.append((label, c))
        pos = c[c > 0]
        print(f"{v0:.0e} dx {dx} phi {phi} {label:13s} step {step} surfaces {r['stats']['surfaces']:9d} {time.time() - t:6.0f}s  "
              f"needs {np.mean(c > 0):.3f}  p50+ {np.median(pos) if pos.size else 0:6.1f}  p99 {np.percentile(c, 99):6.1f}  max {c.max():6.1f}", flush=True)
    best = runs[-1][1]
    for label, c in runs[:-1]:
        both = (c > 0) & (best > 0)
        print(f"   {label:13s} vs densest: of densest's cells, found {both.sum() / max(1, (best > 0).sum()):.3f}; "
              f"cells only here {((c > 0) & (best <= 0)).sum()}; ratio p50 {np.median(c[both] / best[both]) if both.sum() else 0:.2f}", flush=True)
