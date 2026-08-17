"""Sweep RAW image-pair velocities for a small AOI into a local zarr.

Unlike build_glacier_recent.py (which composites pairs into quarterly
medians and discards the individuals), this keeps EVERY measurement with
its provenance: each pair's two acquisition dates, their separation, the
mid-date, the satellites, and the per-pair error estimate. That is the
"nuanced data" the ITS_LIVE web app shows when you click a point — the
raw material for asking how velocity actually varied, how it depends on
pair separation, and how much of the scatter is real signal.

Only sane for SMALL boxes: cost scales with area x time, and the cubes are
chunked [20000 time, 10 y, 10 x] for point access. The default AOI
(Columbia Glacier lower trunk, ~21 x 18 km) is ~288 chunk columns ~ 61 MB.
A whole-glacier sweep would be 30x that; a regional one is out of reach.

Output: data/glaciers/experiments/<name>.zarr
    vx, vy, v_error   int16 [pair, y, x], chunks [all_pairs, 10, 10]
                      (full time per chunk = instant point time series)
    mid_date, date_dt, acquisition_date_img1/2, satellite_img1/2  [pair]
    .attrs: grid geometry (EPSG:3413), source cube, sweep bbox

Usage:
    python tools/sweep_pairs.py                    # default Columbia box
    python tools/sweep_pairs.py --name=foo --bbox=W,S,E,N
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import zarr
from numcodecs import Blosc
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_glacier_recent import PrefetchStore, datacube_url

OUT_ROOT = Path(__file__).resolve().parent.parent / 'data' / 'glaciers' / 'experiments'
TILE = 100000.0
GRID = 120.0
PREFETCH_ROWS = 4          # chunk-rows prefetched per batch

# Columbia Glacier lower trunk / terminus complex (user-specified corners).
DEFAULT_NAME = 'columbia_pairs'
DEFAULT_BBOX = (-147.11907, 61.07494, -146.85457, 61.23517)   # W, S, E, N

VARS_3D = ('vx', 'vy', 'v_error')
VARS_1D = ('mid_date', 'date_dt', 'acquisition_date_img1',
           'acquisition_date_img2', 'satellite_img1', 'satellite_img2')

_to3413 = Transformer.from_crs(4326, 3413, always_xy=True)


def sweep(name, bbox):
    w, s, e, n = bbox
    xs, ys = [], []
    for lon in (w, e):
        for lat in (s, n):
            x, y = _to3413.transform(lon, lat)
            xs.append(x); ys.append(y)
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)

    cubes = set()
    cx = math.floor(x0 / TILE) * TILE + TILE / 2
    while cx < x1 + TILE / 2:
        cy = math.floor(y0 / TILE) * TILE + TILE / 2
        while cy < y1 + TILE / 2:
            cubes.add((cx, cy))
            cy += TILE
        cx += TILE
    if len(cubes) != 1:
        raise SystemExit(f'{name}: box spans {len(cubes)} cubes — this script '
                         f'handles one (keep experiment boxes inside a tile).')
    ccx, ccy = cubes.pop()
    url = datacube_url(ccx, ccy)
    if url is None:
        raise SystemExit(f'{name}: no datacube at X{int(ccx)}_Y{int(ccy)}')
    print(f'== {name}: {url.rsplit("/", 1)[-1]}', flush=True)

    store = PrefetchStore(url)
    g = zarr.open(store, mode='r')
    gx = np.asarray(g['x']); gy = np.asarray(g['y'])       # x asc, y desc
    sx = np.nonzero((gx >= x0) & (gx <= x1))[0]
    sy = np.nonzero((gy >= y0) & (gy <= y1))[0]
    if not sx.size or not sy.size:
        raise SystemExit(f'{name}: box does not intersect the cube grid')
    ix0, ix1 = int(sx.min()), int(sx.max()) + 1
    iy0, iy1 = int(sy.min()), int(sy.max()) + 1
    nx, ny = ix1 - ix0, iy1 - iy0
    npair = g['vx'].shape[0]
    cy_, cx_ = g['vx'].chunks[1], g['vx'].chunks[2]
    print(f'   grid {nx} x {ny} px @120 m, {npair} pairs, source chunks '
          f'[{g["vx"].chunks[0]}, {cy_}, {cx_}]', flush=True)

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    out_path = OUT_ROOT / f'{name}.zarr'
    root = zarr.open(str(out_path), mode='w')
    comp = Blosc(cname='zstd', clevel=5, shuffle=Blosc.BITSHUFFLE)
    arrs = {}
    for v in VARS_3D:
        arrs[v] = root.create_dataset(
            v, shape=(npair, ny, nx), chunks=(npair, 10, 10),
            dtype='<i2', compressor=comp, fill_value=-32767, overwrite=True)

    # 1-D pair provenance (small; copied verbatim).
    meta = {}
    for v in VARS_1D:
        if v not in g:
            print(f'   note: {v} absent in cube — skipping')
            continue
        a = np.asarray(g[v])
        meta[v] = a
        root.create_dataset(v, data=a, chunks=(npair,), overwrite=True)

    # Sweep chunk-column by chunk-column, prefetching a band of rows at a
    # time so the HTTP fetches parallelize but memory stays bounded.
    ycs = list(range(iy0 // cy_, (iy1 - 1) // cy_ + 1))
    xcs = list(range(ix0 // cx_, (ix1 - 1) // cx_ + 1))
    tchunks = math.ceil(npair / g['vx'].chunks[0])
    total_cols = len(ycs) * len(xcs)
    print(f'   {total_cols} chunk columns x {tchunks} time-chunks x '
          f'{len(VARS_3D)} vars', flush=True)

    done = 0
    for bi in range(0, len(ycs), PREFETCH_ROWS):
        band = ycs[bi:bi + PREFETCH_ROWS]
        keys = [f'{v}/{tc}.{yc}.{xc}'
                for v in VARS_3D for tc in range(tchunks)
                for yc in band for xc in xcs]
        store.prefetch(keys)
        for yc in band:
            for xc in xcs:
                sy0, sy1 = max(iy0, yc * cy_), min(iy1, (yc + 1) * cy_)
                sx0, sx1 = max(ix0, xc * cx_), min(ix1, (xc + 1) * cx_)
                if sy0 >= sy1 or sx0 >= sx1:
                    continue
                for v in VARS_3D:
                    block = np.asarray(g[v][:, sy0:sy1, sx0:sx1])
                    arrs[v][:, sy0 - iy0:sy1 - iy0, sx0 - ix0:sx1 - ix0] = block
                done += 1
        store.cache.clear()
        print(f'   {done}/{total_cols} columns', flush=True)

    root.attrs.update({
        'name': name,
        'bbox_lonlat': list(bbox),
        'source_cube': url,
        'grid': {'epsg': 3413, 'x0': float(gx[ix0] - GRID / 2),
                 'y0_north': float(gy[iy0] + GRID / 2),
                 'dx': GRID, 'nx': nx, 'ny': ny},
        'note': 'raw ITS_LIVE image-pair velocities; -32767 = no data',
    })
    report(out_path)


def report(out_path):
    """Stats that tell us what we can actually ask of this data."""
    root = zarr.open(str(out_path), mode='r')
    vx = root['vx']
    ny, nx = vx.shape[1], vx.shape[2]
    npair = vx.shape[0]
    # Sample a stripe of columns rather than the whole cube for speed.
    step = max(1, nx // 12)
    valid_per_pair = np.zeros(npair, np.int32)
    counts = []
    for j in range(0, nx, step):
        blk = np.asarray(vx[:, :, j:j + 1])
        ok = blk != -32767
        valid_per_pair += ok.sum(axis=(1, 2))
        counts.append(ok.sum(axis=0).ravel())
    per_pixel = np.concatenate(counts)
    dt = np.asarray(root['date_dt']) if 'date_dt' in root else None
    md = np.asarray(root['mid_date']) if 'mid_date' in root else None

    print('\n--- sweep summary ---')
    print(f'grid {nx} x {ny} px, {npair} image pairs on file')
    print(f'pairs with data in the sampled stripe: {(valid_per_pair > 0).sum()}')
    print(f'measurements per pixel: median {np.median(per_pixel):.0f}, '
          f'p90 {np.percentile(per_pixel, 90):.0f}, max {per_pixel.max()}')
    if dt is not None:
        d = dt[valid_per_pair > 0]
        print(f'pair separation (days): min {d.min():.0f}, median {np.median(d):.0f}, '
              f'p90 {np.percentile(d, 90):.0f}, max {d.max():.0f}')
        for lo, hi in ((0, 16), (16, 32), (32, 96), (96, 400), (400, 1e9)):
            print(f'   {lo:>4}-{hi if hi < 1e9 else "inf":>4} d: '
                  f'{int(((d >= lo) & (d < hi)).sum())}')
    if md is not None:
        try:
            import datetime as dt_
            base = dt_.date(1970, 1, 1)
            years = np.array([(base + dt_.timedelta(days=float(x))).year
                              for x in md[valid_per_pair > 0]])
            yrs, cts = np.unique(years, return_counts=True)
            print('pairs per year: ' +
                  ', '.join(f'{y}:{c}' for y, c in zip(yrs, cts)))
        except Exception as exc:
            print('year histogram unavailable:', exc)
    size = sum(f.stat().st_size for f in out_path.rglob('*') if f.is_file())
    print(f'on disk: {size / 1e6:.0f} MB at {out_path}')


if __name__ == '__main__':
    name, bbox = DEFAULT_NAME, DEFAULT_BBOX
    for a in sys.argv[1:]:
        if a.startswith('--name='):
            name = a.split('=', 1)[1]
        elif a.startswith('--bbox='):
            bbox = tuple(float(x) for x in a.split('=', 1)[1].split(','))
    sweep(name, bbox)
