#!/opt/anaconda3/bin/python3
"""Prototype 2: the size-free search over a pyramid of terrain grids (2026-11-20).

vproto.py searched 1e8..1e10 m3 on one 100 m grid. Here each level has its own grid and
the range of volumes that grid resolves (r0 / dx >= 6 at its floor), coarse to fine:

    dx 200 m: 6.9e8 ..        dx 100 m: 8.6e7 ..       dx 50 m: 1.1e7 ..       dx 25 m: 1.3e6 ..

At each level seeds climb in (x, y, ln R, ln V), the centre height solved from volume
(scoops_core.c scoops_vclimb). A seed that ends at its level's floor is still gaining as it
shrinks: it is handed down to the next grid and climbs on from where it stood. Fresh
lattice seeds are added at each level too (--fresh): how many of those are needed is the
open question this script is for.

    vlevels.py --name chugach --at=-148.81,61.38 --tag a [--levels 200,100,50,25]
writes multi_pilot/vlev_<name>_<tag>/block.tif on the finest grid (bands as multisize.py).
"""
import argparse, json, math, sys, time
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoops as S
import statewide as W
import multisize as M
from vproto import block, OUT

R0 = lambda v: 1.36 * v ** (1 / 3)
FLOOR = lambda dx: (6 * dx / 1.36) ** 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True); ap.add_argument('--at', required=True)
    ap.add_argument('--tag', default='a'); ap.add_argument('--phi', type=float, default=20.0)
    ap.add_argument('--core-km', type=float, default=20); ap.add_argument('--pad-km', type=float, default=9)
    ap.add_argument('--levels', default='200,100,50,25')
    ap.add_argument('--span', type=float, default=20, help='each level searches floor .. span x floor (the coarsest: to 1e10)')
    ap.add_argument('--fresh', default='all', help='all | first | none: which levels get lattice seeds')
    ap.add_argument('--starts', default='3', help='fresh seeds start at these multiples of the level floor')
    ap.add_argument('--rmul', default='3'); ap.add_argument('--spacing', type=float, default=0.44)
    ap.add_argument('--hand', type=float, default=1.5, help='hand a seed down if it ends below this x the floor')
    ap.add_argument('--hand-c', type=float, default=-1e9, help='... and its cohesion is above this (kPa)')
    ap.add_argument('--hxy', type=float, default=0.22); ap.add_argument('--hlr', type=float, default=0.15)
    ap.add_argument('--hlv', type=float, default=0.4); ap.add_argument('--nhalve', type=int, default=2)
    ap.add_argument('--maxmoves', type=int, default=24); ap.add_argument('--workers', type=int, default=3)
    a = ap.parse_args()
    t0 = time.time()
    if not W.Z12.exists():
        sys.exit('mount Nunatak first')
    lon, lat = [float(v) for v in a.at.split(',')]
    bx, by, core100, pad100, x0, y1 = block(lon, lat, a.core_km, 100.0, a.pad_km)
    ext = (core100 + 2 * pad100) * 100.0                       # same ground for every level
    ysouth = y1 - ext
    core_lo, core_hi = pad100 * 100.0, (pad100 + core100) * 100.0     # local metres
    levels = [float(v) for v in a.levels.split(',')]
    fine = levels[-1]
    acc = None                                                 # (c, vol, xf) on the finest grid
    hand = np.zeros((0, 4))                                    # x, y, R, V (local metres)
    stats, allseeds = [], []
    for li, dx in enumerate(levels):
        n = int(round(ext / dx))
        tp = 512 if dx < 30 else 256 if dx < 70 else 128
        dem = W.block_dem(x0, y1, n, n, dx, tp)
        south = dem[::-1].astype(np.float64)
        vmin = FLOOR(dx)
        vmax = 1e10 if li == 0 else a.span * vmin
        xs, ys, lr, lv = [hand[:, 0]], [hand[:, 1]], [np.log(hand[:, 2])], [np.log(np.clip(hand[:, 3], vmin * 1.05, vmax / 1.05))]
        nfresh = 0
        if a.fresh == 'all' or (a.fresh == 'first' and li == 0):
            for mult in [float(s) for s in a.starts.split(',')]:
                v = mult * vmin
                r = R0(v)
                step = max(1, int(round(a.spacing * r / dx)))
                xn, yn = W.active_nodes(south, dx, r, a.phi, step)
                keep = (xn > core_lo - 4 * r) & (xn < core_hi + 4 * r) & (yn > core_lo - 4 * r) & (yn < core_hi + 4 * r)
                xn, yn = xn[keep], yn[keep]
                for m in [float(s) for s in a.rmul.split(',')]:
                    xs.append(xn); ys.append(yn); lr.append(np.full(len(xn), math.log(m * r))); lv.append(np.full(len(xn), math.log(v)))
                    nfresh += len(xn)
        xs, ys, lr, lv = [np.concatenate(q) for q in (xs, ys, lr, lv)]
        st = dict(dx=dx, vmin=float(f'{vmin:.3g}'), vmax=float(f'{vmax:.3g}'), handed=len(hand), fresh=nfresh)
        if not len(xs):
            stats.append(st); continue
        t1 = time.time()
        r = S.search(S.Grid(south, dx), xs, ys, 0, 0, 1, 1, vmin, vmax, 0, gamma=W.GAMMA, phis=[a.phi], edges=[vmin, vmax],
                     workers=a.workers, extras=True,
                     vclimb=dict(lnR=lr, lnV=lv, hxy=a.hxy, hlr=a.hlr, hlv=a.hlv, nhalve=a.nhalve, maxmoves=a.maxmoves))
        st.update(surfaces=int(r['stats']['surfaces']), cpu=round((time.time() - t1) * a.workers, 1))
        vo = r['vout']                                         # c, x, y, z, R, V
        ok = (vo[:, 0] > a.hand_c) & (vo[:, 5] < a.hand * vmin) & (vo[:, 4] > 0)
        h = vo[ok][:, [1, 2, 4, 5]]
        if len(h):                                             # many seeds end on the same surface
            key = np.round(np.column_stack([h[:, 0] / (.1 * R0(vmin)), h[:, 1] / (.1 * R0(vmin)), np.log(h[:, 2]) / .05]))
            h = h[np.unique(key, axis=0, return_index=True)[1]]
        hand = h
        st.update(ended_at_floor=int(ok.sum()), hand_down=len(h), needs_c=int((vo[:, 0] > 0).sum()))
        allseeds.append(np.column_stack([np.full(len(xs), dx), xs + x0, ys + ysouth, np.exp(lr), np.exp(lv), vo]))
        f = int(round(dx / fine))
        up = lambda v: np.repeat(np.repeat(v, f, axis=-2), f, axis=-1)
        c, vol, xf = up(r['creq'][0, 0]), up(r['creq_vol'][0, 0]), up(r['xf'])
        if acc is None:
            acc = [c, vol, xf]
        else:
            newc, newxf = S.merge_extras([acc[0], c], [acc[2], xf])
            acc = [newc, np.where(c > acc[0], vol, acc[1]), newxf]
        stats.append(st)
        print(json.dumps(st), flush=True)
    c, vol, xf = acc
    n = c.shape[0]; pad = int(round(pad100 * 100 / fine)); core_n = int(round(core100 * 100 / fine))
    demf = W.block_dem(x0, y1, n, n, fine, 512 if fine < 30 else 256 if fine < 70 else 128)
    IA = M.IA
    sxyzr = [xf[IA[k]] for k in ('sx', 'sy', 'sz', 'sr')]
    crop = lambda v: np.ascontiguousarray(v[pad:pad + core_n, pad:pad + core_n][::-1], dtype=np.float32)
    on = c > 0
    sxyzr = [np.where(on, sxyzr[0] + x0, 0), np.where(on, sxyzr[1] + ysouth, 0), sxyzr[2], sxyzr[3]]
    z = np.zeros((core_n, core_n), np.float32)                 # depth repaint left out here: see vproto.py
    d = OUT / f'vlev_{a.name}_{a.tag}'
    d.mkdir(parents=True, exist_ok=True)
    prof = dict(driver='GTiff', height=core_n, width=core_n, count=len(M.BANDS), dtype='float32', crs='EPSG:3338',
                transform=from_origin(x0 + pad * fine, y1 - pad * fine, fine, fine), compress='deflate', predictor=3, tiled=True)
    with rasterio.open(d / 'block.tif', 'w', **prof) as o:
        for i, b in enumerate((crop(c), crop(vol), crop(xf[IA['h_a']]), crop(xf[IA['hmax_a']]), demf[pad:pad + core_n, pad:pad + core_n],
                               z, z, *[crop(v) for v in sxyzr]), 1):
            o.write(b, i)
        o.descriptions = M.BANDS
    np.save(d / 'seeds.npy', np.concatenate(allseeds))
    cc = crop(c)
    out = dict(block=f'{bx}_{by}', wall=round(time.time() - t0, 1), cpu_search=round(sum(s.get('cpu', 0) for s in stats), 1),
               seeds=int(sum(s['handed'] + s['fresh'] for s in stats)), surfaces=int(sum(s.get('surfaces', 0) for s in stats)),
               needs=round(float(np.mean(cc > 0)), 4), max=round(float(cc.max()), 1), levels=stats, args=vars(a))
    (d / 'stats.json').write_text(json.dumps(out))
    print(json.dumps({k: v for k, v in out.items() if k not in ('levels', 'args')}))


if __name__ == '__main__':
    main()
