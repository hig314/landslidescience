#!/opt/anaconda3/bin/python3
"""Required cohesion over Alaska for one friction angle and one volume class.

One grid, cut into blocks that fail and resume independently; each block carries a
margin of 6 r0, enough that every surface a core cell could belong to fits. Nodes
over ground too gentle to need cohesion are skipped (see `active_nodes`).

    statewide.py --phi 30 --v0 1e8 --dx 100 --out DIR [--workers 6]
    statewide.py ... --only-lonlat -149.25,61.82      # one block: a pilot
    statewide.py ... --mosaic                         # blocks -> DIR/creq.tif, volume.tif

Terrain: Mapterhorn z12, falling back on z11 beyond the Alaska tile set (Canada),
and sea level where neither has a tile. Reads a tile list through a mercator centred
on 150 W, in which Alaska does not cross a seam at the antimeridian.
"""
import argparse
import json
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing as mp
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import Affine, from_origin
from rasterio.warp import Resampling, reproject, transform as tr
from scipy.ndimage import binary_dilation, maximum_filter, minimum_filter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoops as S

ROOT = Path(__file__).resolve().parents[3]
Z12 = Path('/Volumes/Nunatak/terrain_src/mapterhorn/12')
Z11 = Path('/Volumes/Nunatak/permafrost_src/mapterhorn/11')
COVERAGE = ROOT / 'data/regions_seed/alaska_3338.geojson'
WORLD = 2 * math.pi * 6378137.0
MERC = '+proj=merc +lon_0=-150 +R=6378137 +units=m +no_defs'
GAMMA = 26.0
N12 = 2 ** 12
BLOCK_M = 150000.0
TP = 128              # px kept per z12 tile: ~35 m at 62 N, plenty for 100 m cells
_coverage = None


def coverage():
    global _coverage
    if _coverage is None:
        import shapely
        from shapely.geometry import shape
        g = json.loads(COVERAGE.read_text())
        _coverage = shape(g) if 'features' not in g else shapely.union_all([shape(f['geometry']) for f in g['features']])
    return _coverage


def _decode(path):
    with rasterio.open(path) as d:
        b = d.read().astype(np.float32)
    if b.shape[0] < 3:
        raise ValueError(f'{path}: {b.shape[0]} bands')
    return b[0] * 256 + b[1] + b[2] / 256 - 32768


def tile_128(x, y, tp=None):
    """One z12 tile averaged down to tp px (default TP). tp 512 is the native grid
    (~9 m at 62 N); beyond the z12 set a z11 tile is doubled up to that."""
    tp = tp or TP
    def down(h):
        f = h.shape[0] // tp
        return h.reshape(tp, f, tp, f).mean(axis=(1, 3)) if f > 1 else h
    p = Z12 / str(x) / f'{y}.webp'
    if p.exists():
        return down(_decode(p))
    p = Z11 / str(x >> 1) / f'{y >> 1}.webp'
    if p.exists():
        h = _decode(p)
        ox, oy = (x & 1) * 256, (y & 1) * 256
        h = h[oy:oy + 256, ox:ox + 256]
        return down(h) if tp <= 256 else np.kron(h, np.ones((tp // 256, tp // 256), np.float32))
    return np.zeros((tp, tp), np.float32)


def block_dem(x0, y1, nx, ny, dx, tp=None):
    """Average heights on an Albers block, north-up. tp: px kept per z12 tile."""
    tp = tp or TP
    xs = np.concatenate([np.linspace(x0, x0 + nx * dx, 40), np.linspace(x0, x0 + nx * dx, 40),
                         np.full(40, x0), np.full(40, x0 + nx * dx)])
    ys = np.concatenate([np.full(40, y1), np.full(40, y1 - ny * dx),
                         np.linspace(y1 - ny * dx, y1, 40), np.linspace(y1 - ny * dx, y1, 40)])
    mx, my = tr('EPSG:3338', MERC, xs.tolist(), ys.tolist())
    res = WORLD / N12                         # metres per z12 tile
    # column of tiles counted from the west edge of this mercator (lon 30 E)
    c0 = int(math.floor((min(mx) + WORLD / 2) / res)) - 1
    c1 = int(math.floor((max(mx) + WORLD / 2) / res)) + 1
    r0 = int(math.floor((WORLD / 2 - max(my)) / res)) - 1
    r1 = int(math.floor((WORLD / 2 - min(my)) / res)) + 1
    t0 = int((30 + 180) / 360 * N12)          # z12 column of lon 30 E
    a = np.zeros(((r1 - r0 + 1) * tp, (c1 - c0 + 1) * tp), np.float32)
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            a[(r - r0) * tp:(r - r0 + 1) * tp, (c - c0) * tp:(c - c0 + 1) * tp] = tile_128((c + t0) % N12, r, tp)
    px = res / tp
    src_tf = Affine(px, 0, -WORLD / 2 + c0 * res, 0, -px, WORLD / 2 - r0 * res)
    out = np.zeros((ny, nx), np.float32)
    reproject(a, out, src_transform=src_tf, src_crs=MERC, dst_transform=from_origin(x0, y1, dx, dx),
              dst_crs='EPSG:3338', resampling=Resampling.average)
    return out


def active_nodes(dem_south, dx, r0, phi, step, skip=True):
    """Lattice nodes worth searching. A surface in this class has a footprint of
    radius about r0 or more, so a drop of `relief` across it means a mean slope near
    relief / r0. Half of tan(phi) of that, inside 3 r0 of the node, cannot need
    cohesion: the node is skipped. Check: --no-skip must change nothing."""
    ny, nx = dem_south.shape
    ii, jj = np.meshgrid(np.arange(0, nx, step), np.arange(0, ny, step))
    ii, jj = ii.ravel(), jj.ravel()
    if skip:
        # on a coarser copy: the filter is the expensive part at 100 m
        f = max(1, step)
        small = dem_south[::f, ::f]
        k = max(1, int(round(3 * r0 / (dx * f))))
        yy, xx = np.ogrid[-k:k + 1, -k:k + 1]
        fp = xx * xx + yy * yy <= k * k
        rel = maximum_filter(small, footprint=fp) - minimum_filter(small, footprint=fp)
        keep = rel[jj // f, ii // f] >= 0.5 * math.tan(math.radians(phi)) * r0
        ii, jj = ii[keep], jj[keep]
    return (ii + .5) * dx, (jj + .5) * dx


def run_block(a, bx, by, grid):
    t = time.time()
    X0, Y1, core_n, pad, dx, r0, step = grid
    x0 = X0 + bx * core_n * dx - pad * dx
    y1 = Y1 - by * core_n * dx + pad * dx
    n = core_n + 2 * pad
    dem = block_dem(x0, y1, n, n, dx)
    south = dem[::-1].astype(np.float64)
    xn, yn = active_nodes(south, dx, r0, a.phi, step, skip=not a.no_skip)
    if len(xn) == 0:
        creq = np.zeros((core_n, core_n), np.float32); vol = creq.copy()
        stats = dict(surfaces=0, columns=0, req_filtered=0, bishop_failed=0, wrapped=0)
    else:
        def run(xs, ys, f):
            return S.search(S.Grid(south, dx), xs, ys, float(dem.min()), float(dem.max()) + 40 * r0,
                            r0 / 2 * f, r0 / 4 * f, a.v0, a.v0 * 10, 0.1 * a.v0, gamma=GAMMA, phis=[a.phi],
                            edges=[a.v0, a.v0 * 10], workers=1, zspan=10 * r0)
        r = run(xn, yn, 1.0)
        c_all, v_all = r['creq'][0, 0], r['creq_vol'][0, 0]
        stats = dict(r['stats'])
        fine = 0
        if a.tau > 0:
            # Coarse to fine: every cell, a third of the z and radius steps, only
            # near nodes that came within tau of needing cohesion (refine_test.py).
            ci, cj = (xn / dx - .5).astype(int) // step, (yn / dx - .5).astype(int) // step
            ncg = n // step + 1
            near = np.zeros((ncg, ncg), bool)
            near[cj, ci] = r['node_best'] >= -a.tau
            near = binary_dilation(near)
            jj, ii = np.nonzero(np.kron(near, np.ones((step, step), bool))[:n, :n])
            fine = len(ii)
            if fine:
                q = run((ii + .5) * dx, (jj + .5) * dx, 1 / 3)
                better = q['creq'][0, 0] > c_all
                c_all = np.where(better, q['creq'][0, 0], c_all)
                v_all = np.where(better, q['creq_vol'][0, 0], v_all)
                for k in stats:
                    stats[k] += q['stats'][k]
        stats['fine_nodes'] = fine
        creq = c_all[pad:pad + core_n, pad:pad + core_n][::-1]
        vol = v_all[pad:pad + core_n, pad:pad + core_n][::-1]
    tf = from_origin(x0 + pad * dx, y1 - pad * dx, dx, dx)
    prof = dict(driver='GTiff', height=core_n, width=core_n, count=3, dtype='float32', crs='EPSG:3338', transform=tf,
                compress='deflate', predictor=3, tiled=True)
    out = Path(a.out) / 'blocks' / f'{bx}_{by}.tif'
    tmp = out.with_suffix('.tmp.tif')
    with rasterio.open(tmp, 'w', **prof) as d:
        d.write(creq, 1); d.write(vol, 2); d.write(dem[pad:pad + core_n, pad:pad + core_n], 3)
    tmp.rename(out)
    return dict(b=f'{bx}_{by}', nodes=len(xn), wall=round(time.time() - t, 1), **stats,
                needs=round(float(np.mean(creq > 0)), 4), max=round(float(creq.max()), 1))


def layout(a):
    import shapely
    w, s, e, n = coverage().bounds
    r0 = 1.36 * a.v0 ** (1 / 3)
    pad = int(6 * r0 / a.dx) + 2
    core_n = int(BLOCK_M / a.dx)
    step = max(1, int(round((r0 / 2) / a.dx)))
    X0 = math.floor(w / a.dx) * a.dx
    Y1 = math.ceil(n / a.dx) * a.dx
    nbx = int(math.ceil((e - X0) / (core_n * a.dx)))
    nby = int(math.ceil((Y1 - s) / (core_n * a.dx)))
    blocks = []
    g = coverage()
    shapely.prepare(g)
    for by in range(nby):
        for bx in range(nbx):
            bb = shapely.box(X0 + bx * core_n * a.dx, Y1 - (by + 1) * core_n * a.dx,
                             X0 + (bx + 1) * core_n * a.dx, Y1 - by * core_n * a.dx)
            if g.intersects(bb):
                blocks.append((bx, by))
    return (X0, Y1, core_n, pad, a.dx, r0, step), blocks


def mosaic(a):
    from rasterio.features import geometry_mask
    grid, blocks = layout(a)
    X0, Y1, core_n, pad, dx, r0, step = grid
    xs = [b[0] for b in blocks]; ys = [b[1] for b in blocks]
    bx0, by0 = min(xs), min(ys)
    W, H = (max(xs) - bx0 + 1) * core_n, (max(ys) - by0 + 1) * core_n
    tf = from_origin(X0 + bx0 * core_n * dx, Y1 - by0 * core_n * dx, dx, dx)
    prof = dict(driver='GTiff', width=W, height=H, count=1, dtype='float32', crs='EPSG:3338', transform=tf, nodata=-1,
                compress='deflate', predictor=3, tiled=True, BIGTIFF='IF_SAFER')
    out = Path(a.out)
    stats = dict(land_cells=0, needs=0, hist={})
    edges = [0, 1, 25, 50, 100, 200, 400, 800, 1600, 1e9]
    hist = np.zeros(len(edges) - 1, np.int64)
    with rasterio.open(out / 'creq.tif', 'w', **prof) as dc, rasterio.open(out / 'volume.tif', 'w', **prof) as dv:
        for bx, by in blocks:
            win = rasterio.windows.Window((bx - bx0) * core_n, (by - by0) * core_n, core_n, core_n)
            path = out / 'blocks' / f'{bx}_{by}.tif'
            if not path.exists():              # still running: a hole, and counted
                blank = np.full((core_n, core_n), -1, np.float32)
                dc.write(blank, 1, window=win); dv.write(blank, 1, window=win)
                stats['missing_blocks'] = stats.get('missing_blocks', 0) + 1
                continue
            with rasterio.open(path) as d:
                c, v, z = d.read()
                t = d.transform
            inside = ~geometry_mask([coverage()], (core_n, core_n), t, all_touched=False)
            land = inside & (z > 0)
            c = np.where(land, c, -1); v = np.where(land, v, -1)
            dc.write(c, 1, window=win); dv.write(v, 1, window=win)
            stats['land_cells'] += int(land.sum()); stats['needs'] += int((c > 0).sum())
            hist += np.histogram(c[land], bins=edges)[0]
    stats['hist'] = dict(zip([str(e) for e in edges[:-1]], hist.tolist()))
    (out / 'mosaic.json').write_text(json.dumps(stats, indent=1))
    print(stats)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--phi', type=float, required=True)
    ap.add_argument('--v0', type=float, required=True)
    ap.add_argument('--dx', type=float, required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--tau', type=float, default=300, help='refine near nodes within this many kPa of needing c; 0 = no refinement')
    ap.add_argument('--only-lonlat', default='')
    ap.add_argument('--no-skip', action='store_true')
    ap.add_argument('--mosaic', action='store_true')
    a = ap.parse_args()
    out = Path(a.out); (out / 'blocks').mkdir(parents=True, exist_ok=True)
    grid, blocks = layout(a)
    if a.mosaic:
        return mosaic(a)
    X0, Y1, core_n, pad, dx, r0, step = grid
    print(f'{len(blocks)} blocks of {core_n} cells + {pad} margin; lattice every {step} cells; r0 {r0:.0f} m', flush=True)
    if a.only_lonlat:
        lo, la = [float(v) for v in a.only_lonlat.split(',')]
        (x,), (y,) = tr('EPSG:4326', 'EPSG:3338', [lo], [la])
        blocks = [(int((x - X0) // (core_n * dx)), int((Y1 - y) // (core_n * dx)))]
    done_file = out / 'done_blocks.txt'
    done = set(done_file.read_text().split()) if done_file.exists() else set()
    todo = [b for b in blocks if f'{b[0]}_{b[1]}' not in done]
    t0 = time.time(); fails = 0
    with ProcessPoolExecutor(a.workers, mp_context=mp.get_context('fork')) as ex:
        futs = {ex.submit(run_block, a, bx, by, grid): (bx, by) for bx, by in todo}
        for k, f in enumerate(as_completed(futs), 1):
            bx, by = futs[f]
            try:
                r = f.result()
            except Exception as e:
                fails += 1
                print(f'FAILED {bx}_{by}: {e}', flush=True)
                continue
            with open(done_file, 'a') as d:
                d.write(f'{bx}_{by}\n')
            print(f'{k}/{len(todo)} {time.time() - t0:.0f}s {json.dumps(r)}', flush=True)
    print(f'finished in {time.time() - t0:.0f} s, {fails} failed', flush=True)
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
