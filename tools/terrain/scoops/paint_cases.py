#!/opt/anaconda3/bin/python3
"""One window of a statewide block, with every way of painting a surface's requirement.

The window keeps the block's own lattice, z levels and refinement rule, so its `creq`
should equal the statewide map there: that comparison is printed as the check.

    paint_cases.py --phi 20 --v0 1e9 --dx 200 --tau 300 --lonlat -149.25,61.82 \
        --size 48000 --name hatcher --out DIR [--state DIR_OF_STATEWIDE_RUN]

Writes DIR/<name>.tif (bands: creq, volume, dem, then scoops.XF_NAMES) and <name>.json.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform as tr
from scipy.ndimage import binary_dilation

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoops as S
import statewide as W


def main():
    ap = argparse.ArgumentParser()
    for k in ('phi', 'v0', 'dx', 'tau', 'size'):
        ap.add_argument('--' + k, type=float, required=True)
    ap.add_argument('--lonlat', required=True)
    ap.add_argument('--name', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--state', default='')
    ap.add_argument('--workers', type=int, default=8)
    a = ap.parse_args()
    t0 = time.time()
    (X0, Y1, core_n, pad, dx, r0, step), blocks = W.layout(a)
    lo, la = [float(v) for v in a.lonlat.split(',')]
    (x,), (y,) = tr('EPSG:4326', 'EPSG:3338', [lo], [la])
    bx, by = int((x - X0) // (core_n * dx)), int((Y1 - y) // (core_n * dx))
    m = min(int(a.size / dx), core_n)
    # window position inside the block's core, on the lattice, kept whole
    ci = int(np.clip(round((x - (X0 + bx * core_n * dx)) / dx - m / 2), 0, core_n - m))
    cj_s = int(np.clip(round((y - (Y1 - (by + 1) * core_n * dx)) / dx - m / 2), 0, core_n - m))
    ci = ci // step * step
    cj_s = cj_s // step * step
    x0 = X0 + bx * core_n * dx - pad * dx
    y1 = Y1 - by * core_n * dx + pad * dx
    n = core_n + 2 * pad
    dem = W.block_dem(x0, y1, n, n, dx)
    south = dem[::-1].astype(np.float64)
    zlo, zhi = float(dem.min()), float(dem.max()) + 40 * r0
    xn, yn = W.active_nodes(south, dx, r0, a.phi, step)     # the block's own, then cropped
    ni = m + 2 * pad
    # local indices of the crop; the block's lattice starts at 0, so ci, cj_s keep it
    sub = south[cj_s:cj_s + ni, ci:ci + ni]
    ii = (xn / dx - .5).round().astype(int) - ci
    jj = (yn / dx - .5).round().astype(int) - cj_s
    keep = (ii >= 0) & (jj >= 0) & (ii < ni) & (jj < ni)
    ii, jj = ii[keep], jj[keep]
    print(f'{a.name}: block {bx}_{by}, crop {ni} cells, {len(ii)} nodes, r0 {r0:.0f}', flush=True)

    def run(ix, jx, f, final=None):
        return S.search(S.Grid(sub, dx), (ix + .5) * dx, (jx + .5) * dx, zlo, zhi, r0 / 2 * f, r0 / 4 * f, a.v0, a.v0 * 10,
                        0.1 * a.v0, gamma=W.GAMMA, phis=[a.phi], edges=[a.v0, a.v0 * 10], workers=a.workers,
                        zspan=10 * r0, extras=True, final=final)
    r = run(ii, jj, 1.0)
    c, xf = r['creq'][0, 0], r['xf']
    vol = r['creq_vol'][0, 0]
    stats = dict(r['stats'])
    t1 = time.time() - t0
    near = np.zeros((ni // step + 1,) * 2, bool)
    near[jj // step, ii // step] = r['node_best'] >= -a.tau
    near = binary_dilation(near)
    fj, fi = np.nonzero(np.kron(near, np.ones((step, step), bool))[:ni, :ni])
    if len(fi):
        q = run(fi, fj, 1 / 3)
        vol = np.where(q['creq'][0, 0] > c, q['creq_vol'][0, 0], vol)
        c2, xf = S.merge_extras([c, q['creq'][0, 0]], [xf, q['xf']])
        c = c2
        stats['iderr'] = max(stats['iderr'], q['stats']['iderr'])
        for k in q['stats']:
            if k != 'iderr':
                stats[k] += q['stats'][k]
    # second pass over both lattices: which surfaces held the top c somewhere, now the map is final
    iw = [S.XF_NAMES.index('h_win'), S.XF_NAMES.index('hmax_win')]
    for ix, jx, f in [(ii, jj, 1.0)] + ([(fi, fj, 1 / 3)] if len(fi) else []):
        q = run(ix, jx, f, final=c)
        xf[iw] = np.maximum(xf[iw], q['xf'][iw])
    stats['win_missing'] = int(((c > 0) & (xf[iw[0]] <= 0)).sum())
    crop = lambda v: v[pad:pad + m, pad:pad + m][::-1]
    bands = [crop(c), crop(vol), crop(sub.astype(np.float32))] + [crop(xf[i]) for i in range(len(S.XF_NAMES))]
    # top-left of the window in map coordinates
    wx = x0 + (ci + pad) * dx
    wy = y1 - n * dx + (cj_s + pad + m) * dx
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out / f'{a.name}.tif', 'w', driver='GTiff', width=m, height=m, count=len(bands), dtype='float32',
                       crs='EPSG:3338', transform=from_origin(wx, wy, dx, dx), compress='deflate', predictor=3) as d:
        for k, b in enumerate(bands, 1):
            d.write(b.astype(np.float32), k)
        d.descriptions = tuple(['creq', 'volume', 'dem'] + list(S.XF_NAMES))
    res = dict(name=a.name, block=f'{bx}_{by}', wall=round(time.time() - t0), coarse=round(t1), fine_nodes=len(fi), nodes=len(ii),
               lonlat=[lo, la], **stats)
    if a.state:
        # the check: same grid, same lattice, so the same numbers as the statewide run
        p = Path(a.state) / 'blocks' / f'{bx}_{by}.tif'
        if p.exists():
            with rasterio.open(p) as d:
                s = d.read(1)[core_n - cj_s - m:core_n - cj_s, ci:ci + m]
            ok = (s > 0) | (bands[0] > 0)
            ratio = bands[0][ok] / np.maximum(s[ok], 1e-3)
            res['vs_statewide'] = dict(cells=int(ok.sum()), exact=float(np.mean(np.abs(bands[0][ok] - s[ok]) < .01)),
                                       within_1pct=float(np.mean(np.abs(ratio - 1) < .01)),
                                       p1=float(np.percentile(ratio, 1)), p99=float(np.percentile(ratio, 99)))
    (out / f'{a.name}.json').write_text(json.dumps(res, indent=1))
    print(json.dumps(res), flush=True)


if __name__ == '__main__':
    main()
