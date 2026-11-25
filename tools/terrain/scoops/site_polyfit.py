#!/opt/anaconda3/bin/python3
"""Spheres whose footprint matches a mapped outline, and what each one requires.

    site_polyfit.py --name sadie_peak --poly inventory_1558_epsg6334.geojson

A footprint fixes a sphere only up to its radius: a small sphere cuts deep, a large one
shallow. For a series of radii this fits the centre to the outline (highest
intersection-over-union of the patch that overlaps it), then asks the core what
cohesion that patch needs at each friction angle. Writes polyfit.json in the site folder.
Uses the outline of ONE mapped feature, by Hig's request for this site (2026-11-24):
the standing rule against comparing these maps with landslide mapping is otherwise in force.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from scipy import ndimage
from scipy.optimize import minimize

sys.path.insert(0, str(Path(__file__).resolve().parent))
import site_failure as SF

PHIS = [0, 10, 15, 20, 25, 30, 35, 40, 45]


def patch(dem, dx, p, poly):
    """Thickness (south up) of the connected patch of sphere p that overlaps `poly` most; IoU."""
    ny, nx = dem.shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    r2 = ((xx + .5) * dx - p[0]) ** 2 + ((yy + .5) * dx - p[1]) ** 2
    t = np.where(r2 < p[3] ** 2, dem - (p[2] - np.sqrt(np.maximum(p[3] ** 2 - r2, 0))), 0)
    lab, n = ndimage.label(t > 0)
    if n == 0 or not (lab[poly] > 0).any():
        return None, 0.0
    k = np.bincount(lab[poly])[1:].argmax() + 1
    m = lab == k
    return np.where(m, t, 0), (m & poly).sum() / (m | poly).sum()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True); ap.add_argument('--poly', required=True)
    ap.add_argument('--dx', type=float, default=16)
    ap.add_argument('--radii', type=float, nargs='*', default=[1100, 1400, 1800, 2300, 3000, 4000, 5500, 8000, 12000, 20000])
    ap.add_argument('--compare', action='store_true', help='the free-search peaks against the outline')
    a = ap.parse_args()
    if a.compare:
        return compare(a.name, a.poly, a.dx)
    out = SF.OUT / a.name
    with rasterio.open(out / 'dem_src.tif') as s:
        A = s.read(1).astype(np.float64); W, N, cell = s.transform.c, s.transform.f, s.res[0]
    f = int(round(a.dx / cell)); dem_n = SF.block(A, f); dx = cell * f
    y0 = N - dem_n.shape[0] * dx
    g = json.loads((out / a.poly).read_text())
    poly = rasterize([(ft['geometry'], 1) for ft in g['features']], out_shape=dem_n.shape,
                     transform=from_origin(W, N, dx, dx)).astype(bool)[::-1]
    dem = np.ascontiguousarray(dem_n[::-1])
    jj, ii = np.nonzero(poly); area = poly.sum() * dx * dx
    cx, cy = (ii.mean() + .5) * dx, (jj.mean() + .5) * dx
    G = np.c_[(ii + .5) * dx - cx, (jj + .5) * dx - cy, np.ones(len(ii))]
    (sx_, sy_, z0), *_ = np.linalg.lstsq(G, dem[jj, ii], rcond=None)
    nrm = np.array([-sx_, -sy_, 1.0]); nrm /= np.linalg.norm(nrm)
    print(f'outline {area / 1e6:.3f} km2, mean slope {math.degrees(math.atan(math.hypot(sx_, sy_))):.1f} deg toward '
          f'{math.degrees(math.atan2(-sx_, -sy_)) % 360:.0f}, ground {dem[jj, ii].min():.0f} to {dem[jj, ii].max():.0f} m', flush=True)
    rows = []
    for R in a.radii:
        sag = min((area / math.pi) / (2 * R), 0.6 * R)
        c0 = np.array([cx, cy, z0]) + (R - sag) * nrm
        obj = lambda u: -patch(dem, dx, [c0[0] + u[0], c0[1] + u[1], c0[2] + u[2], R], poly)[1]
        best = None
        for h in (150, 50):
            u0 = np.zeros(3) if best is None else best.x
            sim = np.vstack([u0] + [u0 + e for e in np.eye(3) * h])
            best = minimize(obj, u0, method='Nelder-Mead', options=dict(initial_simplex=sim, xatol=2, fatol=1e-4, maxfev=400))
        p = [c0[0] + best.x[0], c0[1] + best.x[1], c0[2] + best.x[2], R]
        t, iou = patch(dem, dx, p, poly)
        if t is None:
            continue
        V = t.sum() * dx * dx; m = t > 0
        ev = SF.Eval(dem_n, dx, 0, 0.93 * V, 1.07 * V)
        cs = []
        for phi in PHIS:
            ev.phi[0] = phi; c = ev.c(p); cs.append(None if c < -1e29 else round(c, 1))
        rec = dict(R=R, p=[float(v) for v in p], x=round(W + p[0], 1), y=round(y0 + p[1], 1), z=round(p[2], 1), iou=round(float(iou), 3),
                   vol=float(f'{V:.4g}'), area_km2=round(m.sum() * dx * dx / 1e6, 3), tmax=round(float(t.max())), tmean=round(float(t[m].mean())),
                   ztop=round(float(dem[m].max())), ztoe=round(float(dem[m].min())), c_kPa=dict(zip(map(str, PHIS), cs)))
        pos = [(ph, c) for ph, c in zip(PHIS, cs) if c is not None]
        for (p1, c1), (p2, c2) in zip(pos, pos[1:]):
            if c1 > 0 >= c2:
                rec['phi_c0'] = round(p1 + (p2 - p1) * c1 / (c1 - c2), 1)
        rows.append(rec)
        print(f"R {R:6.0f} IoU {iou:.2f} V {V:9.3g} A {rec['area_km2']:.2f} tmax {rec['tmax']:4d} top {rec['ztop']:5d} toe {rec['ztoe']:5d}  c: "
              + ' '.join(f'{ph}:{c}' for ph, c in zip(PHIS, cs)) + f"  phi(c=0) {rec.get('phi_c0')}", flush=True)
    (out / 'polyfit.json').write_text(json.dumps(dict(poly=a.poly, dx=dx, area_m2=float(area), phis=PHIS, fits=rows), indent=1))



def compare(name, polyfile, dx=16):
    """Every on-the-face peak of every peaks_phi*.json against the outline: overlap, by friction angle."""
    out = SF.OUT / name
    with rasterio.open(out / 'dem_src.tif') as s:
        A = s.read(1).astype(np.float64); W, N, cell = s.transform.c, s.transform.f, s.res[0]
    f = int(round(dx / cell)); dem_n = SF.block(A, f)
    g = json.loads((out / polyfile).read_text())
    poly = rasterize([(ft['geometry'], 1) for ft in g['features']], out_shape=dem_n.shape,
                     transform=from_origin(W, N, dx, dx)).astype(bool)[::-1]
    dem = np.ascontiguousarray(dem_n[::-1])
    res = {}
    for pf in sorted(out.glob('peaks_phi*.json'), key=lambda p: float(p.stem.split('phi')[1]) if p.stem.split('phi')[1].replace('.', '').isdigit() else 1e9):
        tag = pf.stem.split('phi')[1]
        if not tag.replace('.', '').isdigit():
            continue
        m = json.loads(pf.read_text()); rows = []
        for q in m['peaks']['free']:
            t, iou = patch(dem, dx, q['p'], poly)
            inside = 0 if t is None else float(((t > 0) & poly).sum() / max((t > 0).sum(), 1))
            rows.append(dict(id=q['id'], c=q['c_kPa'], vol=q['vol'], area=q['area_km2'], tmax=q['tmax'], ztop=q['ztop'], ztoe=q['ztoe'],
                             R=q['R'], n=q['n'], iou=round(float(iou), 2), inside=round(inside, 2), on_face=q['on_face']))
        res[tag] = rows
        top = max(rows, key=lambda r: r['c']); cmax = top['c']
        print(f"phi {tag}: highest anywhere {top['id']} c {top['c']} V {top['vol']:.3g} overlap {top['iou']} inside {top['inside']}")
        for r in sorted((r for r in rows if r['inside'] > 0.5 or r['iou'] > 0.2), key=lambda r: -r['c'])[:6]:
            print(f"    {r['id']:>7} c {r['c']:6.1f} ({r['c'] / cmax:4.2f} of top) V {r['vol']:8.3g} A {r['area']:5.2f} tmax {r['tmax']:4d} top {r['ztop']:5d} toe {r['ztoe']:5d} "
                  f"R {r['R']:7.0f} overlap {r['iou']:.2f} inside {r['inside']:.2f} seeds {r['n']}")
    (out / 'peaks_vs_outline.json').write_text(json.dumps(res, indent=1))


if __name__ == '__main__':
    main()
