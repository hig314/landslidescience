#!/opt/anaconda3/bin/python3
"""Our rebuild against USGS's own published runs (Scoops3D v1.3 example set,
tm14-a1_Scoops3Dexamples_1.3.zip): G a single surface on a 10 m embankment; F, L,
N box searches. Inputs and outputs are read from their files, untouched.

    benchmark_usgs.py [--root DIR]
"""
import argparse
import re
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoops as S

ROOT = Path('/Volumes/Nunatak/terrain_build/scoops_ref/examples/tm14-a1_Scoops3Dexamples_1.3/original_files_1.3')


def read_asc(path):
    """-> (array with row 0 = SOUTH, cellsize)"""
    head = {}
    with open(path) as f:
        for _ in range(6):
            k, v = f.readline().split()
            head[k.lower()] = float(v)
        a = np.loadtxt(f)
    a = a.reshape(int(head['nrows']), int(head['ncols']))[::-1]
    a = np.where(a == head['nodata_value'], np.nan, a)
    return a, head['cellsize']


def scp(path):
    """The .scp file as {line of names: line of values}, in order of appearance."""
    lines = [l.strip() for l in open(path) if l.strip()]
    return {lines[i]: lines[i + 1] for i in range(0, len(lines) - 1, 2)}


def out_values(path):
    t = open(path).read()
    t = t[t.index('IV. RESULTS'):]
    m = {}
    for name, pat in (('f_bishop', r"Bishop's 3D factor of safety:\s+([\d.Ee+-]+)"),
                      ('f_ordinary', r"Ordinary 3D factor of safety:\s+([\d.Ee+-]+)"),
                      ('v', r"Volume \(m \^3\):\s+([\d.Ee+-]+)"),
                      ('ah', r"Horizontal surface area \(m \^2\):\s+([\d.Ee+-]+)"),
                      ('area', r"Slip surface area \(m \^2\):\s+([\d.Ee+-]+)"),
                      ('ncols', r"Number of active columns:\s+(\d+)"),
                      ('delta_deg', r"Slip direction, relative to search lattice:\s+([\d.Ee+-]+)"),
                      ('surfaces', r"Number of trial surfaces tried:\s+(\d+)")):
        r = re.search(pat, t)
        if r:
            m[name] = float(r.group(1))
    r = re.search(r"Elapsed cpu time in seconds:\s+([\d.Ee+-]+)", t)
    m['cpu'] = float(r.group(1)) if r else None
    return m


def run_single(root):
    d = root / 'DonaldGiam'
    p = scp(d / 'G_emb10single.scp')
    dem, dx = read_asc(d / 'input/emb10DEM.asc')
    x, y, z, R, ang = [float(v) for v in p['xcen   ycen   zcen   rad   angle'].split()]
    lnum, c, phi, gam = [float(v) for v in p['lnum   cee   phi   gamt'].split()]
    ours = S.single(S.Grid(dem, dx), x, y, z, R, c, phi, gam, 999.0)
    ref = out_values(d / 'G_emb10single_output/G_emb10single_out.txt')
    print('\nG  single surface, 10 m embankment (c 3 kPa, phi 19.6, gamma 20)')
    print(f"   {'':12s}{'USGS':>12s}{'ours':>12s}{'diff %':>9s}")
    for k in ('f_bishop', 'f_ordinary', 'v', 'ah', 'area', 'delta_deg', 'ncols'):
        print(f"   {k:12s}{ref[k]:12.4f}{ours[k]:12.4f}{100 * (ours[k] / ref[k] - 1):9.3f}")
    return ours, ref


def run_box(root, group, name, workers):
    d = root / group
    p = scp(d / f'{name}.scp')
    dem, dx = read_asc(d / p['DEM file'].rstrip('/'))
    ny, nx = dem.shape
    lnum, c, phi, gam = [float(v) for v in p['lnum   cee   phi   gamt'].split()]
    ismin, jsmin, ismax, jsmax, nres = [int(v) for v in p['ismin   jsmin   ismax   jsmax   nsrchres'].split()]
    zmin, zmax, dz = [float(v) for v in p['zsmin   zsmax   zsrchres'].split()]
    dr = float(p['dr   deginc   degmax   numdir'].split()[0])
    _, _, _, vmin, vmax, tol, limcol = p['vacriterion   armin   armax   vmin   vmax   tol   limcol'].split()
    vmin, vmax, tol = float(vmin), float(vmax), float(tol)
    xn, yn = S.lattice(nx, ny, dx, ismin, jsmin, ismax, jsmax, nres)
    t = time.time()
    r = S.search(S.Grid(dem, dx), xn, yn, zmin, zmax, dz, dr, vmin, vmax, tol, gamma=gam, c=c, phi_f=phi, workers=workers)
    wall = time.time() - t
    o = d / f'{name}_output'
    ref = out_values(o / f'{name}_out.txt')
    fos, _ = read_asc(o / f'{name}_fos3d_out.asc')
    ford, _ = read_asc(o / f'{name}_ordfos3d_out.asc')
    vol = None
    for suffix in ('fosvol', 'fosarea'):
        q = o / f'{name}_{suffix}_out.asc'
        if q.exists():
            vol, _ = read_asc(q)
            break
    print(f"\n{name}  lattice {len(xn)} nodes, c {c:g}, phi {phi:g}, gamma {gam:g}, V {vmin:g}-{vmax:g}")
    print(f"   surfaces:  USGS {ref.get('surfaces', float('nan')):.0f} (coarse-to-fine)   ours {r['stats']['surfaces']} (full box)")
    print(f"   global min F (Bishop):  USGS {ref['f_bishop']:.4f}   ours {np.nanmin(r['fmin']):.4f}")
    print(f"   wall: {wall:.1f} s on {workers} procs; USGS cpu {ref['cpu']} s; columns {r['stats']['columns']:.3g}, "
          f"Bishop not converged {r['stats']['bishop_failed']}")
    ok = np.isfinite(fos) & (fos < 100) & np.isfinite(r['fmin'])
    only_usgs = np.sum(np.isfinite(fos) & (fos < 100) & ~np.isfinite(r['fmin']))
    only_ours = np.sum((~np.isfinite(fos) | (fos >= 100)) & np.isfinite(r['fmin']))
    rel = r['fmin'][ok] / fos[ok] - 1
    q = np.percentile(rel, [1, 5, 50, 95, 99]) * 100
    print(f"   cell F, ours/USGS - 1 (%): p1 {q[0]:.2f} p5 {q[1]:.2f} median {q[2]:.2f} p95 {q[3]:.2f} p99 {q[4]:.2f}"
          f"  ({ok.sum()} cells; only USGS {only_usgs}, only ours {only_ours})")
    if vol is not None:
        rv = r['fvol'][ok] / vol[ok] - 1
        qv = np.percentile(rv, [5, 50, 95]) * 100
        print(f"   critical volume, ours/USGS - 1 (%): p5 {qv[0]:.1f} median {qv[1]:.1f} p95 {qv[2]:.1f}")
    return r, fos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default=str(ROOT))
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--only', default='')
    a = ap.parse_args()
    root = Path(a.root)
    if not a.only or 'G' in a.only:
        run_single(root)
    for group, name, tag in (('DonaldGiam', 'F_emb10', 'F'), ('cone', 'L_conemed', 'L'), ('cone', 'N_conemedfullsearch', 'N')):
        if not a.only or tag in a.only:
            run_box(root, group, name, a.workers)


if __name__ == '__main__':
    main()
