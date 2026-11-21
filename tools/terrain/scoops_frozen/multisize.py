#!/opt/anaconda3/bin/python3
"""Required cohesion over Alaska at one friction angle, across every size class.

For each class (a decade of volume, on its own grid: r0 / dx about 6.5) every cell keeps
the most demanding surface containing it: the cohesion, that surface's volume, and its
depth under the cell. `combine` then takes the most demanding class at each place.

The search in a block:
  1. the coarse lattice of statewide.py (nodes every r0/2, dz r0/2, dr r0/4), skipping
     ground too gentle to need cohesion, recording each node's best surface;
  2. hill climbing from that surface at every node that came near needing cohesion
     (scoops_core.c scoops_climb), in place of the dense fine lattice. --method fine
     runs the old fine lattice instead, to check the climb against.

    multisize.py --phi 20 --out DIR [--classes 10,9,8,7,6] [--workers 3]
    multisize.py ... --classes 8 --at -148.81,61.38 --block-km 30 --method fine --tag _fine   # a pilot
    multisize.py --phi 20 --out DIR --mosaic        # per class: DIR/v<k>/{creq,vol,depth}.vrt

Stopping and resuming: every block is its own file, written whole and renamed, and listed
in DIR/v<k>/done.txt. `touch DIR/STOP` lets running blocks finish and starts no more;
killing the process loses only the blocks in flight. The same command resumes.
Classes run largest first, so each finished class is a complete map.
"""
import argparse
import json
import math
import multiprocessing as mp
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform as tr
from scipy.ndimage import binary_dilation

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoops as S
import statewide as W

DX = {10: 450.0, 9: 200.0, 8: 100.0, 7: 45.0, 6: 20.0}
BLOCK_KM = {10: 150, 9: 150, 8: 75, 7: 45, 6: 20}       # small enough that a block is minutes
TAU_FRAC = 0.23            # climb from nodes within this x r0 kPa of needing cohesion
BANDS = ('creq', 'vol', 'depth', 'depth_max', 'dem', 'depth_win', 'c_at_depth', 'sx', 'sy', 'sz', 'sr')
IA = {n: i for i, n in enumerate(S.XF_NAMES)}


def paint_winners(dem, dx, creq, sx, sy, sz, sr):
    """Depth under every cell to the deepest surface that is the most demanding one at
    some cell. dem and the rest south-up, [ny, nx]; sx.. the winner's centre (m from the
    grid's south-west corner) and radius at each cell. Geometry only: each distinct winner
    is one sphere, its mass the 4-connected patch of ground above it that holds a cell it
    wins. Returns depth, the cohesion of the surface that sets it, and counts."""
    from scipy.ndimage import label
    ny, nx = dem.shape
    # corner heights and columns exactly as scoops_core.c builds them (setup, sphere, column_geometry)
    pz = np.pad(dem.astype(float), 1); pn = np.pad(np.ones((ny, nx)), 1)
    zg = (pz[:-1, :-1] + pz[:-1, 1:] + pz[1:, :-1] + pz[1:, 1:]) / (pn[:-1, :-1] + pn[:-1, 1:] + pn[1:, :-1] + pn[1:, 1:])
    f = (np.arange(6) + .5) / 6
    hw, cw = np.zeros((ny, nx), np.float32), np.zeros((ny, nx), np.float32)
    jj, ii = np.nonzero((creq > 0) & (sr > 0))
    if not len(jj):
        return hw, cw, dict(winners=0, lost=0)
    key = np.stack([sx[jj, ii], sy[jj, ii], sz[jj, ii], sr[jj, ii]], 1)
    uq, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    order = np.argsort(inv, kind='stable')
    cuts = np.searchsorted(inv[order], np.arange(len(uq) + 1))
    lost = 0
    for u, (xc, yc, zc, R) in enumerate(uq.astype(float)):
        mine = order[cuts[u]:cuts[u + 1]]
        i0, i1 = max(int(np.floor((xc - R) / dx)), 0), min(int(np.ceil((xc + R) / dx)), nx)
        j0, j1 = max(int(np.floor((yc - R) / dx)), 0), min(int(np.ceil((yc + R) / dx)), ny)
        if i0 >= i1 or j0 >= j1:
            lost += 1
            continue
        R2 = R * R
        base = lambda x, y: zc - np.sqrt(np.maximum(R2 - (x - xc) ** 2 - (y - yc) ** 2, 0))
        kx, ly = np.arange(i0, i1 + 1) * dx, np.arange(j0, j1 + 1) * dx
        z = zg[j0:j1 + 1, i0:i1 + 1]
        inside = (kx[None] - xc) ** 2 + (ly[:, None] - yc) ** 2 < R2
        dc = np.where(inside, z - base(kx[None], ly[:, None]), -1.0)
        d1, d2, d3, d4 = dc[:-1, :-1], dc[:-1, 1:], dc[1:, 1:], dc[1:, :-1]
        z1, z2, z3, z4 = z[:-1, :-1], z[:-1, 1:], z[1:, 1:], z[1:, :-1]
        on = (d1 > 0) | (d2 > 0) | (d3 > 0) | (d4 > 0)
        lab, _ = label(on)
        wj, wi = jj[mine] - j0, ii[mine] - i0
        ok = (wj >= 0) & (wi >= 0) & (wj < j1 - j0) & (wi < i1 - i0)
        ids = np.unique(lab[wj[ok], wi[ok]]); ids = ids[ids > 0]
        if not len(ids):
            lost += 1
            continue
        keep = np.isin(lab, ids)
        full = keep & (d1 > 0) & (d2 > 0) & (d3 > 0) & (d4 > 0)
        cx, cy = (np.arange(i0, i1) + .5) * dx, (np.arange(j0, j1) + .5) * dx
        mid = .25 * (z1 + z2 + z3 + z4) - base(cx[None], cy[:, None])
        dep = np.where(full, (.5 * (d1 + d4) + 4 * mid + .5 * (d2 + d3)) / 6, 0)
        pj, pi = np.nonzero(keep & ~full)                 # partial columns: 6 x 6 sub-points, mean over those inside
        if len(pj):
            fx, fy = f[None, None, :], f[None, :, None]
            x = (i0 + pi)[:, None, None] * dx + fx * dx; y = (j0 + pj)[:, None, None] * dx + fy * dx
            g = (z1[pj, pi][:, None, None] * (1 - fx) * (1 - fy) + z2[pj, pi][:, None, None] * fx * (1 - fy)
                 + z3[pj, pi][:, None, None] * fx * fy + z4[pj, pi][:, None, None] * (1 - fx) * fy)
            d = np.where((x - xc) ** 2 + (y - yc) ** 2 < R2, g - base(x, y), 0)
            pos = d > 0
            cnt = pos.sum((1, 2))
            dep[pj, pi] = np.where(cnt > 0, (d * pos).sum((1, 2)) / np.maximum(cnt, 1), 0)
        dep = dep.astype(np.float32)
        c = creq[jj[mine[0]], ii[mine[0]]]
        v = hw[j0:j1, i0:i1]; up = dep > v
        v[up] = dep[up]; cw[j0:j1, i0:i1][up] = c
    return hw, cw, dict(winners=len(uq), lost=lost)


def class_grid(k, phi, block_km=None):
    a = SimpleNamespace(v0=10.0 ** k, dx=DX[k], phi=phi)
    W.BLOCK_M = (block_km or BLOCK_KM[k]) * 1000.0
    grid, blocks = W.layout(a)
    return a, grid, blocks


def run_block(k, phi, bx, by, grid, out, method):
    if (Path(out).parent / 'STOP').exists():
        return None
    if not W.Z12.exists() or not Path(out).exists():
        # block_dem reads a missing tile as sea level: with the drive gone it would map all of Alaska as flat
        raise RuntimeError('terrain tiles or output folder missing (is Nunatak mounted?)')
    t = time.time()
    X0, Y1, core_n, pad, dx, r0, step = grid
    v0 = 10.0 ** k
    x0 = X0 + bx * core_n * dx - pad * dx
    y1 = Y1 - by * core_n * dx + pad * dx
    n = core_n + 2 * pad
    tp = 512 if dx < 30 else 256 if dx < 70 else 128
    dem = W.block_dem(x0, y1, n, n, dx, tp)
    south = dem[::-1].astype(np.float64)
    xn, yn = W.active_nodes(south, dx, r0, phi, step)
    z = np.zeros((n, n), np.float32)
    c_all, v_all, h, hmax = z, z, z, z
    hw, cw, sxyzr = z, z, [z, z, z, z]
    st = dict(nodes=len(xn), seeds=0, surfaces=0, t_coarse=0.0)
    if len(xn):
        kw = dict(gamma=W.GAMMA, phis=[phi], edges=[v0, v0 * 10], workers=1, extras=True)
        g = S.Grid(south, dx)
        zlo, zhi = float(dem.min()), float(dem.max()) + 40 * r0
        r = S.search(g, xn, yn, zlo, zhi, r0 / 2, r0 / 4, v0, v0 * 10, .1 * v0, zspan=10 * r0, **kw)
        st['t_coarse'] = round(time.time() - t, 1)
        cs, xfs, vs = [r['creq'][0, 0]], [r['xf']], [r['creq_vol'][0, 0]]
        st['surfaces'] = int(r['stats']['surfaces'])
        near = r['node_best'] >= -TAU_FRAC * r0
        if method == 'climb':
            import os
            var = os.environ.get('MS_VARIANT', 'bands,dense')
            def add(q):
                cs.append(q['creq'][0, 0]); xfs.append(q['xf']); vs.append(q['creq_vol'][0, 0])
                st['surfaces'] += int(q['stats']['surfaces'])
            base = dict(hxy=step * dx / 2, hz=r0 / 4, hr=r0 / 8, nhalve=2, maxmoves=16)
            nb = r['node_band']
            if 'bands' in var:
                # 4-D climb from each node's best surface in each third of the decade
                for b in range(3):
                    sel = (nb[:, b, 0] >= -TAU_FRAC * r0) & (nb[:, b, 2] > 0)
                    st['seeds'] += int(sel.sum())
                    if sel.any():
                        lo, hi = v0 * 10 ** (b / 3), v0 * 10 ** ((b + 1) / 3)
                        add(S.search(g, xn[sel], yn[sel], zlo, zhi, r0 / 2, r0 / 4, lo, hi, .1 * v0,
                                     climb=dict(z=nb[sel, b, 1], R=nb[sel, b, 2], **base), **kw))
            else:
                sel = near & (r['node_R'] > 0)
                st['seeds'] = int(sel.sum())
                if sel.any():
                    add(S.search(g, xn[sel], yn[sel], zlo, zhi, r0 / 2, r0 / 4, v0, v0 * 10, .1 * v0,
                                 climb=dict(z=r['node_z'][sel], R=r['node_R'][sel], **base), **kw))
            if 'dense' in var:
                # every cell near a demanding node: climb in (z, R) only, from the nearest node's surfaces
                ci, cj = (xn / dx - .5).astype(int) // step, (yn / dx - .5).astype(int) // step
                ng = n // step + 1
                idx = np.full((ng, ng), -1); idx[cj, ci] = np.arange(len(xn))
                gn = np.zeros((ng, ng), bool); gn[cj, ci] = near
                if 'dil' in var:
                    gn = binary_dilation(gn)     # one node further out, as the fine pass does
                jj, ii = np.nonzero(np.kron(gn, np.ones((step, step), bool))[:n, :n])
                node = idx[jj // step, ii // step]
                on_node = (ii % step == 0) & (jj % step == 0)
                nbands = range(3) if 'dense3' in var else [None]
                for b in nbands:
                    if b is None:
                        zz, rr, lo, hi = r['node_z'][node], r['node_R'][node], v0, v0 * 10
                    else:
                        zz, rr, lo, hi = nb[node, b, 1], nb[node, b, 2], v0 * 10 ** (b / 3), v0 * 10 ** ((b + 1) / 3)
                    sel = (rr > 0) & ~on_node
                    st['dense'] = st.get('dense', 0) + int(sel.sum())
                    if sel.any():
                        add(S.search(g, (ii[sel] + .5) * dx, (jj[sel] + .5) * dx, zlo, zhi, r0 / 2, r0 / 4, lo, hi, .1 * v0,
                                     climb=dict(z=zz[sel], R=rr[sel], **dict(base, dims=12, maxmoves=8)), **kw))
        elif method == 'fine':
            ci, cj = (xn / dx - .5).astype(int) // step, (yn / dx - .5).astype(int) // step
            grid_near = np.zeros((n // step + 1, n // step + 1), bool)
            grid_near[cj, ci] = near
            grid_near = binary_dilation(grid_near)
            jj, ii = np.nonzero(np.kron(grid_near, np.ones((step, step), bool))[:n, :n])
            st['seeds'] = len(ii)
            if len(ii):
                q = S.search(g, (ii + .5) * dx, (jj + .5) * dx, zlo, zhi, r0 / 6, r0 / 12, v0, v0 * 10, .1 * v0, zspan=10 * r0, **kw)
                cs.append(q['creq'][0, 0]); xfs.append(q['xf']); vs.append(q['creq_vol'][0, 0])
                st['surfaces'] += int(q['stats']['surfaces'])
        c_all, xf = S.merge_extras(cs, xfs)
        v_all = np.take_along_axis(np.stack(vs), np.argmax(np.stack(cs), axis=0)[None], axis=0)[0]
        h, hmax = xf[IA['h_a']], xf[IA['hmax_a']]
        sxyzr = [xf[IA[n_]] for n_ in ('sx', 'sy', 'sz', 'sr')]
        hw, cw, pw = paint_winners(south, dx, c_all, *sxyzr)
        st.update(pw)
        on = c_all > 0                      # absolute EPSG:3338 centre, so winners can be matched across blocks
        sxyzr = [np.where(on, sxyzr[0] + x0, 0), np.where(on, sxyzr[1] + (y1 - n * dx), 0), sxyzr[2], sxyzr[3]]
    crop = lambda v: np.ascontiguousarray(v[pad:pad + core_n, pad:pad + core_n][::-1], dtype=np.float32)
    creq = crop(c_all)
    prof = dict(driver='GTiff', height=core_n, width=core_n, count=len(BANDS), dtype='float32', crs='EPSG:3338',
                transform=from_origin(x0 + pad * dx, y1 - pad * dx, dx, dx), compress='deflate', predictor=3, tiled=True)
    path = Path(out) / 'blocks' / f'{bx}_{by}.tif'
    tmp = path.with_suffix('.tmp.tif')
    with rasterio.open(tmp, 'w', **prof) as d:
        for i, a in enumerate((creq, crop(v_all), crop(h), crop(hmax), dem[pad:pad + core_n, pad:pad + core_n],
                               crop(hw), crop(cw), *[crop(v) for v in sxyzr]), 1):
            d.write(a, i)
        d.descriptions = BANDS
    tmp.rename(path)
    return dict(b=f'{bx}_{by}', wall=round(time.time() - t, 1), **st,
                needs=round(float(np.mean(creq > 0)), 4), max=round(float(creq.max()), 1))


def mosaic(root, classes, phi):
    for k in classes:
        d = root / f'v{k}'
        files = sorted(str(p) for p in (d / 'blocks').glob('*.tif') if '.tmp' not in p.name)
        if not files:
            continue
        lst = d / 'blocks.txt'
        lst.write_text('\n'.join(files) + '\n')
        for name, band in (('creq', 1), ('vol', 2), ('depth', 3), ('depth_max', 4), ('dem', 5)):
            subprocess.run(['/opt/homebrew/bin/gdalbuildvrt', '-q', '-overwrite', '-b', str(band), '-input_file_list', str(lst),
                            str(d / f'{name}.vrt')], check=True)
        print(f'v{k}: {len(files)} blocks')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--phi', type=float, required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--classes', default='10,9,8,7,6')
    ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--method', default='climb', choices=['climb', 'fine', 'coarse'])
    ap.add_argument('--at', default='', help='lon,lat: only the block holding this point')
    ap.add_argument('--block-km', type=float, default=0)
    ap.add_argument('--tag', default='')
    ap.add_argument('--mosaic', action='store_true')
    a = ap.parse_args()
    root = Path(a.out)
    classes = [int(v) for v in a.classes.split(',')]
    if a.mosaic:
        return mosaic(root, classes, a.phi)
    t0 = time.time()
    if not W.Z12.exists():
        sys.exit(f'{W.Z12} is missing: mount Nunatak first')
    for k in classes:
        _, grid, blocks = class_grid(k, a.phi, a.block_km or None)
        X0, Y1, core_n, pad, dx, r0, step = grid
        out = root / f'v{k}{a.tag}'
        (out / 'blocks').mkdir(parents=True, exist_ok=True)
        (out / 'grid.json').write_text(json.dumps(dict(k=k, phi=a.phi, X0=X0, Y1=Y1, core_n=core_n, pad=pad, dx=dx, r0=r0, step=step,
                                                       method=a.method, block_m=W.BLOCK_M)))
        if a.at:
            lo, la = [float(v) for v in a.at.split(',')]
            (x,), (y,) = tr('EPSG:4326', 'EPSG:3338', [lo], [la])
            blocks = [(int((x - X0) // (core_n * dx)), int((Y1 - y) // (core_n * dx)))]
        done_file = out / 'done.txt'
        done = set(done_file.read_text().split()) if done_file.exists() else set()
        todo = [b for b in blocks if f'{b[0]}_{b[1]}' not in done]
        print(f'class 1e{k}: dx {dx:.0f} m, r0 {r0:.0f} m, {len(blocks)} blocks of {core_n} cells (+{pad}), {len(todo)} to do', flush=True)
        fails = 0
        with ProcessPoolExecutor(a.workers, mp_context=mp.get_context('fork')) as ex:
            futs = {ex.submit(run_block, k, a.phi, bx, by, grid, str(out), a.method): (bx, by) for bx, by in todo}
            for i, f in enumerate(as_completed(futs), 1):
                bx, by = futs[f]
                try:
                    r = f.result()
                except Exception as e:
                    fails += 1
                    print(f'FAILED 1e{k} {bx}_{by}: {e}', flush=True)
                    continue
                if r is None:
                    continue
                with open(done_file, 'a') as d:
                    d.write(f'{bx}_{by}\n')
                print(f'1e{k} {i}/{len(todo)} {(time.time() - t0) / 3600:.2f} h {json.dumps(r)}', flush=True)
        if (root / 'STOP').exists():
            print('STOP file found: stopped cleanly', flush=True)
            return
        print(f'class 1e{k} finished, {fails} failed, {(time.time() - t0) / 3600:.2f} h', flush=True)


if __name__ == '__main__':
    main()
