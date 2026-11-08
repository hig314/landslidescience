#!/opt/anaconda3/bin/python3
"""The fixed-scale slopes as VALUES at the analysis lattice and at every
landslide centroid (Hig, 2026-11-07: every slope row an axis, not just 60 m).

    slope_scale_values.py [--workers 3] [--block 8 45 150]

The slope overlays (tools/terrain/bake_slope.py) were baked straight to
coloured tiles, so no value raster exists to sample. This evaluates the same
measure, with bake_slope.py's own functions on the same Mapterhorn z12 tiles,
at just the points the analysis needs:

  * the centre of every cell of the 1 km EPSG:3338 lattice (quantities.GRID)
    -> data/quantities/slope_<sid>.npy, the cube column itself;
  * every landslide centroid in data/landslide_centroids_3338.json
    -> data/slope_scales/landslides.json, {key: {id: value}}, which
    sample_quantities.py reads for these keys instead of a raster.

Per scale, the value at a point is the bake's value at the z12 pixel the
point falls in: `local` is Horn's slope there; `60` the steepest 60 m
diameter centred there (the tile-constant radius and per-row length of
chord_slope.diameter_slope_tile); 200 m .. 2 km are computed on their own
coarser grid over the whole block (bake_slope.coarse_values) and read
bilinear at the z12 pixel centre, exactly as the bake's upsample() colours
that pixel. Water (bake_slope.land_mask) is NaN; so is everything outside
Alaska (the bake's coverage polygon, which is quantities.ALASKA).

The work unit is the bake's: a z8 block plus a one-tile margin, ~1 GB a
worker. About 15 minutes on 3 workers. --block runs one block and prints a
check of the point evaluation against the bake's own raster functions.
"""
import argparse
import json
import math
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np
from rasterio.warp import transform as warp_transform

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'tools' / 'terrain'))
import bake_slope as BS                                          # noqa: E402
import chord_slope as C                                          # noqa: E402
from quantities import GRID, alaska_mask                         # noqa: E402

OUT = ROOT / 'data' / 'quantities'
LS_OUT = ROOT / 'data' / 'slope_scales' / 'landslides.json'
CENTROIDS = ROOT / 'data' / 'landslide_centroids_3338.json'
KEY = {'local': 'slope_local', '60': 'slope_60', '200': 'slope_200',
       '500': 'slope_500', '1k': 'slope_1k', '2k': 'slope_2k'}
TILE, ZS, ZB = BS.TILE, BS.Z_SRC, BS.Z_BLOCK
S = 2 ** (ZS - ZB) * TILE                 # block side in z12 pixels (8192)
PAD = BS.PAD_TILES * TILE
W = 2 ** ZS * TILE                        # world width in z12 pixels


def to_px(x3338, y3338):
    """EPSG:3338 -> global z12 pixel coordinates (float), standard -180..180 wrap."""
    lon, lat = warp_transform('EPSG:3338', 'EPSG:4326', list(x3338), list(y3338))
    lon = np.asarray(lon, float); lat = np.radians(np.asarray(lat, float))
    px = (lon + 180) / 360 * W
    py = (1 - np.log(np.tan(lat) + 1 / np.cos(lat)) / np.pi) / 2 * W
    return px, py


def point_values(z, y_global, land, sid, length, zc, i, j, coarse=None):
    """The scale's value at block pixels (j row, i col: unpadded, int arrays)."""
    ya, xa = j + PAD, i + PAD
    if length is None:                       # local: Horn at the pixel
        rows = y_global + ya
        row_m = C.mercator_res(ZS) * np.cos(np.radians(C.row_lat(rows, ZS)))
        g = lambda dy, dx: z[ya + dy, xa + dx]          # noqa: E731
        dzdx = ((g(-1, 1) + 2 * g(0, 1) + g(1, 1)) - (g(-1, -1) + 2 * g(0, -1) + g(1, -1))) / (8 * row_m)
        dzdy = ((g(1, -1) + 2 * g(1, 0) + g(1, 1)) - (g(-1, -1) + 2 * g(-1, 0) + g(-1, 1))) / (8 * row_m)
        v = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    elif zc == ZS:                           # 60 m: steepest diameter on the z12 grid
        rows = y_global + ya
        row_m = C.mercator_res(ZS) * np.cos(np.radians(C.row_lat(rows, ZS)))
        # the bake's radius is constant per z12 tile, from the tile's centre row
        tile_c = y_global + PAD + (j // TILE) * TILE + TILE // 2
        r = (length / 2) / (C.mercator_res(ZS) * np.cos(np.radians(C.row_lat(tile_c, ZS))))
        best = np.zeros(len(i), np.float64)
        zf = z.astype(np.float64)
        for k in range(C.NDIR):
            t = math.pi * k / C.NDIR
            dy, dx = r * math.sin(t), r * math.cos(t)
            a = C._bilinear(zf, ya + dy, xa + dx)
            b = C._bilinear(zf, ya - dy, xa - dx)
            best = np.fmax(best, np.abs(a - b))
        v = np.degrees(np.arctan(best / (2 * r * row_m)))
    else:                                    # coarser: its own grid, bilinear at the pixel centre
        out, k, edge = coarse
        my = ((j + 0.5) / k - 0.5 + edge).astype(np.float32).reshape(-1, 1)
        mx = ((i + 0.5) / k - 0.5 + edge).astype(np.float32).reshape(-1, 1)
        v = cv2.remap(out, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)[:, 0]
    v = np.asarray(v, np.float32)
    v[~land[ya, xa]] = np.nan
    return v


def do_block(args):
    """All six scales at the given points of one z8 block -> {sid: values}."""
    bx, by, pi, pj = args                    # pi, pj: block pixel cols / rows of the points
    z, y_global, have = BS.read_block(bx, by)
    if have == 0:
        return bx, by, None
    land = BS.land_mask(z)
    res = {}
    for sid, length, zc in BS.SCALES:
        coarse = None if (length is None or zc == ZS) else BS.coarse_values(z, y_global, length, zc)
        res[sid] = point_values(z, y_global, land, sid, length, zc, pi, pj, coarse)
    return bx, by, res


def do_block_safe(args):
    try:
        return do_block(args)
    except Exception as e:                   # name it; never let one block lose the run
        return args[0], args[1], f'ERROR {e}'


def group(px, py, ok):
    """Point indices per z8 block: {(bx, by): idx}."""
    bx = np.floor(px / S).astype(np.int64); by = np.floor(py / S).astype(np.int64)
    key = bx * 4096 + by
    idx = np.nonzero(ok)[0]
    order = idx[np.argsort(key[idx], kind='stable')]
    out = {}
    if not len(order):
        return out
    ks = key[order]
    cuts = np.nonzero(np.diff(ks))[0] + 1
    for part in np.split(order, cuts):
        out[(int(bx[part[0]]), int(by[part[0]]))] = part
    return out


def check_block(bx, by):
    """Point evaluation vs the bake's raster functions, at every 97th lattice-like pixel."""
    z, y_global, have = BS.read_block(bx, by)
    assert have, 'no tiles in that block'
    land = BS.land_mask(z)
    rng = np.random.default_rng(2)
    tx, ty = 7, 9                            # one z12 tile inside the block
    jj = rng.integers(0, TILE, 400); ii = rng.integers(0, TILE, 400)
    pj, pi = ty * TILE + jj, tx * TILE + ii
    for sid, length, zc in BS.SCALES:
        coarse = None if (length is None or zc == ZS) else BS.coarse_values(z, y_global, length, zc)
        mine = point_values(z, y_global, land, sid, length, zc, pi, pj, coarse)
        ref = BS.upsample(coarse, tx, ty) if coarse is not None else BS.fine_values(z, tx, ty, y_global, length)
        ref = np.where(land[ty * TILE + PAD:(ty + 1) * TILE + PAD, tx * TILE + PAD:(tx + 1) * TILE + PAD], ref, np.nan)[jj, ii]
        ok = np.isfinite(mine) & np.isfinite(ref)
        d = np.abs(mine[ok] - ref[ok])
        print(f'  {sid:5s} {ok.sum():3d} pts  max |diff| {d.max() if len(d) else float("nan"):.5f} deg  '
              f'median {np.nanmedian(mine):5.1f} deg  nan-mismatch {int((np.isfinite(mine) != np.isfinite(ref)).sum())}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--block', nargs=3, type=int, metavar=('Z', 'X', 'Y'), help='check one z8 block and stop')
    a = ap.parse_args()
    if a.block:
        assert a.block[0] == ZB
        check_block(a.block[1], a.block[2]); return

    # the lattice: cell centres inside Alaska
    ga, _, gc, _, ge, gf = GRID['transform']
    H, Wd = GRID['height'], GRID['width']
    ak = alaska_mask()
    jj, ii = np.nonzero(ak)
    lx = gc + (ii + 0.5) * ga; ly = gf + (jj + 0.5) * ge
    # the landslide centroids inside Alaska (the sampler's cell-centre test)
    pts = json.loads(CENTROIDS.read_text())
    ids = np.array([int(p[0]) for p in pts])
    cx = np.array([float(p[1]) for p in pts]); cy = np.array([float(p[2]) for p in pts])
    ci = ((cx - gc) // ga).astype(np.int64); cj = ((cy - gf) // ge).astype(np.int64)
    cin = (ci >= 0) & (ci < Wd) & (cj >= 0) & (cj < H)
    cin[cin] = ak[cj[cin], ci[cin]]

    allx = np.concatenate([lx, cx]); ally = np.concatenate([ly, cy])
    ok = np.concatenate([np.ones(len(lx), bool), cin])
    t0 = time.time()
    px, py = to_px(allx, ally)
    print(f'{len(lx):,} lattice points + {int(cin.sum())} centroids in Alaska; projected in {time.time() - t0:.0f} s', flush=True)
    blocks = group(px, py, ok)
    tasks = []
    for (bx, by), idx in blocks.items():
        pi = np.floor(px[idx] - bx * S).astype(np.int64)
        pj = np.floor(py[idx] - by * S).astype(np.int64)
        tasks.append((bx, by, pi, pj))
    print(f'{len(tasks)} z8 blocks', flush=True)

    vals = {sid: np.full(len(allx), np.nan, np.float32) for sid, _, _ in BS.SCALES}
    done = 0; errors = []
    with Pool(a.workers, maxtasksperchild=8) as pool:
        for bx, by, res in pool.imap_unordered(do_block_safe, tasks):
            done += 1
            if isinstance(res, str):
                errors.append((bx, by, res)); print(f'  block {bx},{by}: {res}', flush=True); continue
            if res is not None:
                idx = blocks[(bx, by)]
                for sid in vals:
                    vals[sid][idx] = res[sid]
            if done % 25 == 0 or done == len(tasks):
                print(f'  {done}/{len(tasks)} blocks, {time.time() - t0:.0f} s', flush=True)
    if errors:
        sys.exit(f'{len(errors)} blocks failed; nothing written')

    OUT.mkdir(parents=True, exist_ok=True)
    nl = len(lx)
    ls = {}
    for sid, v in vals.items():
        g = np.full((H, Wd), np.nan, np.float32)
        g[jj, ii] = v[:nl]
        np.save(OUT / f'{KEY[sid]}.npy', g)
        cv = v[nl:]
        ls[KEY[sid]] = {str(int(i)): (None if not np.isfinite(t) else round(float(t), 2))
                        for i, t, inside in zip(ids, cv, cin) if inside}
        print(f'  {KEY[sid]:12s} lattice {int(np.isfinite(g).sum()):>9,} valid, median {np.nanmedian(g):5.1f} deg; '
              f'landslides {sum(t is not None for t in ls[KEY[sid]].values())} of {len(ids)}')
    LS_OUT.parent.mkdir(parents=True, exist_ok=True)
    LS_OUT.write_text(json.dumps({'built': time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime()),
                                  'centroids': str(CENTROIDS.name), 'values': ls}))
    print('wrote', OUT, 'and', LS_OUT, f'in {time.time() - t0:.0f} s')


if __name__ == '__main__':
    main()
