#!/opt/anaconda3/bin/python3
"""Prototype: one search over sizes instead of a run per decade class (2026-11-20).

Each trial sphere is set by (x, y, R, volume); its centre height is solved from the volume
(scoops_core.c scoops_vclimb). Seeds start at a few volumes on lattices scaled to each,
then climb in all four, so a seed can drift to the size that needs the most cohesion.
One terrain grid for now (100 m, so volumes from 1e8 m3 up).

    vproto.py --name chugach --at=-148.81,61.38 --tag a
writes multi_pilot/vproto_<name>_<tag>/block.tif (bands as multisize.py) and seeds.npy.
"""
import argparse, json, math, sys, time
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform as tr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoops as S
import statewide as W
import multisize as M

OUT = Path('/Volumes/Nunatak/terrain_build/scoops_state/multi_pilot')


def block(lon, lat, core_km, dx, pad_km):
    """The 100 m class's own block holding the point (so it lines up with its fine-lattice reference)."""
    _, grid, _ = M.class_grid(8, 20, core_km)
    X0, Y1, core_n, _, gdx, _, _ = grid
    assert gdx == dx
    (x,), (y,) = tr('EPSG:4326', 'EPSG:3338', [lon], [lat])
    bx, by = int((x - X0) // (core_n * dx)), int((Y1 - y) // (core_n * dx))
    pad = int(round(pad_km * 1000 / dx))
    x0, y1 = X0 + bx * core_n * dx - pad * dx, Y1 - by * core_n * dx + pad * dx
    return bx, by, core_n, pad, x0, y1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True); ap.add_argument('--at', required=True)
    ap.add_argument('--tag', default='a'); ap.add_argument('--phi', type=float, default=20.0)
    ap.add_argument('--core-km', type=float, default=20); ap.add_argument('--pad-km', type=float, default=9)
    ap.add_argument('--starts', default='3e9,1e9,3e8'); ap.add_argument('--rmul', default='3')
    ap.add_argument('--spacing', type=float, default=0.44, help='seed lattice spacing, in r0 of the start volume')
    ap.add_argument('--vmin', type=float, default=1e8); ap.add_argument('--vmax', type=float, default=1e10)
    ap.add_argument('--hxy', type=float, default=0.22); ap.add_argument('--hlr', type=float, default=0.15)
    ap.add_argument('--hlv', type=float, default=0.4); ap.add_argument('--nhalve', type=int, default=2)
    ap.add_argument('--maxmoves', type=int, default=24); ap.add_argument('--workers', type=int, default=4)
    a = ap.parse_args()
    t0 = time.time()
    dx = 100.0
    lon, lat = [float(v) for v in a.at.split(',')]
    bx, by, core_n, pad, x0, y1 = block(lon, lat, a.core_km, dx, a.pad_km)
    n = core_n + 2 * pad
    if not W.Z12.exists():
        sys.exit('mount Nunatak first')
    dem = W.block_dem(x0, y1, n, n, dx, 128)
    south = dem[::-1].astype(np.float64)
    xs, ys, lr, lv = [], [], [], []
    for v in [float(s) for s in a.starts.split(',')]:
        r = 1.36 * v ** (1 / 3)
        step = max(1, int(round(a.spacing * r / dx)))
        xn, yn = W.active_nodes(south, dx, r, a.phi, step)
        for m in [float(s) for s in a.rmul.split(',')]:
            xs.append(xn); ys.append(yn); lr.append(np.full(len(xn), math.log(m * r))); lv.append(np.full(len(xn), math.log(v)))
    xs, ys, lr, lv = [np.concatenate(q) for q in (xs, ys, lr, lv)]
    t1 = time.time()
    r = S.search(S.Grid(south, dx), xs, ys, 0, 0, 1, 1, a.vmin, a.vmax, 0, gamma=W.GAMMA, phis=[a.phi], edges=[a.vmin, a.vmax],
                 workers=a.workers, extras=True,
                 vclimb=dict(lnR=lr, lnV=lv, hxy=a.hxy, hlr=a.hlr, hlv=a.hlv, nhalve=a.nhalve, maxmoves=a.maxmoves))
    t2 = time.time()
    c, vol, xf = r['creq'][0, 0], r['creq_vol'][0, 0], r['xf']
    IA = M.IA
    sxyzr = [xf[IA[k]] for k in ('sx', 'sy', 'sz', 'sr')]
    hw, cw, pw = M.paint_winners(south, dx, c, *sxyzr)
    on = c > 0
    sxyzr = [np.where(on, sxyzr[0] + x0, 0), np.where(on, sxyzr[1] + (y1 - n * dx), 0), sxyzr[2], sxyzr[3]]
    crop = lambda v: np.ascontiguousarray(v[pad:pad + core_n, pad:pad + core_n][::-1], dtype=np.float32)
    d = OUT / f'vproto_{a.name}_{a.tag}'
    d.mkdir(parents=True, exist_ok=True)
    prof = dict(driver='GTiff', height=core_n, width=core_n, count=len(M.BANDS), dtype='float32', crs='EPSG:3338',
                transform=from_origin(x0 + pad * dx, y1 - pad * dx, dx, dx), compress='deflate', predictor=3, tiled=True)
    with rasterio.open(d / 'block.tif', 'w', **prof) as o:
        for i, b in enumerate((crop(c), crop(vol), crop(xf[IA['h_a']]), crop(xf[IA['hmax_a']]), dem[pad:pad + core_n, pad:pad + core_n],
                               crop(hw), crop(cw), *[crop(v) for v in sxyzr]), 1):
            o.write(b, i)
        o.descriptions = M.BANDS
    vo = r['vout'].copy(); vo[:, 1] += x0; vo[:, 2] += y1 - n * dx
    np.save(d / 'seeds.npy', np.column_stack([xs + x0, ys + (y1 - n * dx), np.exp(lr), np.exp(lv), vo]))
    cc = crop(c)
    st = dict(block=f'{bx}_{by}', seeds=len(xs), surfaces=int(r['stats']['surfaces']), wall=round(time.time() - t0, 1),
              t_dem=round(t1 - t0, 1), t_search=round(t2 - t1, 1), cpu_search=round((t2 - t1) * a.workers, 1),
              needs=round(float(np.mean(cc > 0)), 4), max=round(float(cc.max()), 1), **pw, args=vars(a))
    (d / 'stats.json').write_text(json.dumps(st))
    print(json.dumps(st))


if __name__ == '__main__':
    main()
