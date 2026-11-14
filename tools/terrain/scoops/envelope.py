#!/opt/anaconda3/bin/python3
"""The strength the terrain requires, for one window and one surface size, no strength assumed.

Every surface of the class gives (sigma, tau), so a requirement c = tau - sigma tan phi
(scoops_core.c). Surfaces overlap by the thousand, so counts of surfaces mean little: what
is kept is the most demanding surface of each spatial tile, 10 r0 across, enough that
neighbouring tiles' tops are close to independent. Percentiles are taken across tiles.

    envelope.py --name chugach --lonlat=-148.81,61.38 --size 150000 --v0 1e8 --dx 100 --out DIR
    envelope.py ... --patches 12        # N random patches of 40 r0 inside the window instead

The surface size is the physics; dx only has to resolve it (r0 / dx about 6.5, checked by
running one class at several dx). Patches are for the small classes, whose cost grows
about 4.6-fold per decade: percentiles need a sample of tiles, not full coverage.
"""
import argparse
import json
import sys
import time
import zlib
from pathlib import Path

import numpy as np
import rasterio
import shapely
from rasterio.transform import from_origin
from rasterio.warp import transform as tr
from scipy.ndimage import binary_dilation

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoops as S
import statewide as W

PHIS = [30.0, 0.0, 10.0, 15.0, 20.0, 25.0, 35.0, 40.0, 45.0, 50.0]   # first drives refinement
PHI_SKIP = 10.0      # nodes too gentle to matter at this friction are skipped


def run_domain(x0, y1, n_core, a, r0, step, tp, keep_maps=False):
    """One square domain, core n_core cells, north-west corner of the core at (x0, y1)."""
    dx, pad = a.dx, int(np.ceil(6 * r0 / a.dx))
    n = n_core + 2 * pad
    dem = W.block_dem(x0 - pad * dx, y1 + pad * dx, n, n, dx, tp)
    south = dem[::-1].astype(np.float64)
    tile = 10 * r0
    nt = int(round(n_core * dx / tile))
    tile = n_core * dx / nt
    env = dict(x0=pad * dx, y0=pad * dx, size=tile, nt=nt)
    xn, yn = W.active_nodes(south, dx, r0, PHI_SKIP, step)
    zlo, zhi = float(dem.min()), float(dem.max()) + 40 * r0

    def run(xs, ys, f):
        return S.search(S.Grid(south, dx), xs, ys, zlo, zhi, r0 / 2 * f, r0 / 4 * f, a.v0, a.v0 * 10, .1 * a.v0, gamma=W.GAMMA,
                        phis=PHIS, edges=[a.v0, a.v0 * 10], workers=a.workers, zspan=10 * r0, env=env)
    nph = len(PHIS)
    e = dict(hist=np.zeros((S.ENV_NB, S.ENV_NB)), tile_tau=np.zeros((nt * nt, S.ENV_NB)), tile_ratio=np.zeros(nt * nt),
             tile_cord=np.full((nt * nt, nph), -1e30), tile_top=np.zeros((nt * nt, nph, S.ENV_NTOP)), cell_ratio=np.zeros((n, n), np.float32))
    creq = np.zeros((nph, n, n), np.float32)
    st = dict(surfaces=0, columns=0, req_filtered=0, wrapped=0, bishop_failed=0, nodes=len(xn), fine=0)

    def merge(r):
        q = r['env']
        e['hist'] += q['hist']
        for k in ('tile_tau', 'tile_ratio', 'tile_cord', 'cell_ratio'):
            e[k] = np.maximum(e[k], q[k])
        better = q['tile_top'][..., 0] > e['tile_top'][..., 0]
        e['tile_top'][better] = q['tile_top'][better]
        creq[:] = np.maximum(creq, r['creq'][:, 0])
        for k in st:
            if k in r['stats']:
                st[k] += r['stats'][k]
    if len(xn):
        r = run(xn, yn, 1.0)
        merge(r)
        if a.tau_frac > 0:
            ci, cj = (xn / dx - .5).round().astype(int) // step, (yn / dx - .5).round().astype(int) // step
            ncg = n // step + 1
            near = np.zeros((ncg, ncg), bool)
            near[cj, ci] = r['node_best'] >= -a.tau_frac * r0
            near = binary_dilation(near)
            jj, ii = np.nonzero(np.kron(near, np.ones((step, step), bool))[:n, :n])
            st['fine'] = len(ii)
            if len(ii):
                merge(run((ii + .5) * dx, (jj + .5) * dx, 1 / 3))
    # tile geometry, in Albers; `land` and `in_cov` decide which tiles count
    cov = W.coverage()
    k = n_core // nt
    core = south[pad:pad + n_core, pad:pad + n_core]
    land, relief, cx, cy = [], [], [], []
    for j in range(nt):
        for i in range(nt):
            b = core[j * k:(j + 1) * k, i * k:(i + 1) * k]
            land.append(float(np.mean(b > 0))); relief.append(float(b.max() - b.min()) if b.size else 0)
            cx.append(x0 + (i + .5) * tile); cy.append(y1 - n_core * dx + (j + .5) * tile)
    in_cov = shapely.contains_xy(cov, np.array(cx), np.array(cy))
    top = e['tile_top']
    ox, oy = x0 - pad * dx, y1 - n_core * dx - pad * dx      # local -> Albers
    top[..., 1] += ox * (top[..., 0] > 0); top[..., 2] += oy * (top[..., 0] > 0)
    out = dict(hist=e['hist'], tile_tau=e['tile_tau'], tile_ratio=e['tile_ratio'], tile_cord=e['tile_cord'], tile_top=top,
               tile_land=np.array(land), tile_relief=np.array(relief), tile_x=np.array(cx), tile_y=np.array(cy), tile_cov=in_cov)
    maps = None
    if keep_maps:
        crop = lambda v: v[pad:pad + n_core, pad:pad + n_core][::-1]
        maps = dict(ratio=crop(e['cell_ratio']), dem=crop(south.astype(np.float32)),
                    **{f'c{int(PHIS[i])}': crop(creq[i]) for i in range(nph) if PHIS[i] in (20, 30, 40)})
    return out, st, maps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True)
    ap.add_argument('--lonlat', required=True)
    ap.add_argument('--size', type=float, default=150000)
    ap.add_argument('--v0', type=float, required=True)
    ap.add_argument('--dx', type=float, required=True)
    ap.add_argument('--tp', type=int, default=0)
    ap.add_argument('--tau-frac', type=float, default=.23, help='refine near nodes within this x r0 kPa of needing cohesion at phis[0]')
    ap.add_argument('--patches', type=int, default=0)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--tag', default='')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    t0 = time.time()
    r0 = 1.36 * a.v0 ** (1 / 3)
    step = max(1, int(round(r0 / 2 / a.dx)))
    tp = a.tp or (512 if a.dx < 30 else 256 if a.dx < 70 else 128)
    lo, la = [float(v) for v in a.lonlat.split(',')]
    (xc,), (yc,) = tr('EPSG:4326', 'EPSG:3338', [lo], [la])
    stem = f'{a.name}_v{np.log10(a.v0):.0f}_dx{a.dx:.0f}{a.tag}'
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    parts, stats, maps = [], [], None
    if a.patches:
        # positions from the window's name alone, so nothing about the ground chooses them
        rng = np.random.default_rng(zlib.crc32(a.name.encode()))
        n_core = int(round(40 * r0 / a.dx))
        cov = W.coverage()
        done = 0
        while done < a.patches:
            px, py = rng.uniform(-a.size / 2, a.size / 2, 2)
            x0, y1 = xc + px - n_core * a.dx / 2, yc + py + n_core * a.dx / 2
            if not shapely.contains_xy(cov, xc + px, yc + py):
                continue
            o, st, _ = run_domain(x0, y1, n_core, a, r0, step, tp)
            parts.append(o); stats.append(st); done += 1
            print(f'{stem} patch {done}/{a.patches}: {st["surfaces"]} surfaces, {time.time() - t0:.0f} s', flush=True)
    else:
        n_core = int(round(a.size / a.dx))
        o, st, maps = run_domain(xc - a.size / 2, yc + a.size / 2, n_core, a, r0, step, tp, keep_maps=True)
        parts.append(o); stats.append(st)
    res = {k: np.concatenate([p[k] for p in parts]) for k in parts[0] if k != 'hist'}
    res['hist'] = sum(p['hist'] for p in parts)
    np.savez_compressed(out / f'{stem}.npz', phis=np.array(PHIS), v0=a.v0, dx=a.dx, r0=r0, **res)
    if maps:
        names = list(maps)
        with rasterio.open(out / f'{stem}.tif', 'w', driver='GTiff', width=n_core, height=n_core, count=len(names), dtype='float32',
                           crs='EPSG:3338', transform=from_origin(xc - a.size / 2, yc + a.size / 2, a.dx, a.dx), compress='deflate', predictor=3) as d:
            for i, m in enumerate(names, 1):
                d.write(maps[m].astype(np.float32), i)
            d.descriptions = tuple(names)
    meta = dict(name=a.name, lonlat=[lo, la], v0=a.v0, dx=a.dx, r0=r0, step=step, tp=tp, size=a.size, patches=a.patches, wall=round(time.time() - t0),
                **{k: int(sum(s[k] for s in stats)) for k in stats[0]})
    (out / f'{stem}.json').write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta), flush=True)


if __name__ == '__main__':
    main()
