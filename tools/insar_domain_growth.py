#!/usr/bin/env python3
"""Kinematic domain discovery — boundary-agnostic rigid-subset search (demo).

Principle (Hig): mapped polygons are uncertain and gradational; if a rigid-
body signal exists it likely belongs to a SUBSET of the landslide. So the
polygon seeds the search and the kinematics decide membership:

  1. Per-point LOS rates (both tracks) from the cached point series —
     polygon + halo + ring, no clipping to the mapped boundary.
  2. Seed = the spatially-local patch (kNN) with the best internal rigid
     consistency (6-param linear rigid fit, rms residual).
  3. Grow: repeatedly offer every non-member within NEIGHBOR_M of the
     domain; accept the point whose inclusion keeps the refit rms lowest,
     while rms stays under a tolerance scaled from the ring-control noise.
     Contiguity is enforced by the neighbor rule — rigid blocks are
     contiguous kinematic domains, and scattered inliers are how noise
     pretends to be structure.
  4. Output: domain membership vs the mapped polygon, drawn.

DEMONSTRATION of mechanics on confirmed-real sites, NOT a detection claim:
the production pipeline must run this identical search on control fields to
calibrate how easily subset-growth "finds" domains in noise (selection
inside the null, or the FPR is fiction — see the plan).

Uses only cached fetches (run insar_site_characterization.py first).
"""
import json
import math
from itertools import combinations
from pathlib import Path

import numpy as np
from shapely.geometry import shape, Point

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'data' / 'insar_power'
L_ASC = np.array([-0.613, -0.142, 0.777])
L_DESC = np.array([0.613, -0.142, 0.777])
KNN_SEED = 6
NEIGHBOR_M = 320
TOL_FACTOR = 2.5          # x ring rms — provisional; production calibrates


def load_rates(name, site):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'sc', ROOT / 'tools' / 'insar_site_characterization.py')
    sc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sc)
    inside, ring = sc.probe_points(site['geom'])
    rows = []
    for group, pts in (('in', inside), ('ring', ring)):
        for lat, lon in pts:
            row = {'group': group, 'lat': lat, 'lon': lon}
            ok = False
            for dirn, l in (('ascending', L_ASC), ('descending', L_DESC)):
                fit = sc.rate_fit(sc.fetch_ts(lat, lon, dirn))
                row[dirn] = fit['rate'] if fit else None
                ok = ok or fit is not None
            if ok:
                rows.append(row)
    return rows


def local_xyz(rows, lat0, lon0):
    """Rows -> meters east/north (+ z=0; elevations unknown here, so the demo
    fits the HORIZONTAL rigid family — production adds DEM z)."""
    X = []
    for r in rows:
        e = (r['lon'] - lon0) * 111000 * math.cos(math.radians(lat0))
        n = (r['lat'] - lat0) * 111000
        X.append([e, n, 0.0])
    return np.array(X)


def rigid_rms(idx, X, rows):
    """6-param rigid fit over member indices; returns (rms, n_obs)."""
    G, R = [], []
    for i in idx:
        for dirn, l in (('ascending', L_ASC), ('descending', L_DESC)):
            v = rows[i][dirn]
            if v is None:
                continue
            G.append(np.concatenate([np.cross(X[i], l), l]))
            R.append(v)
    if len(R) < 8:
        return np.inf, len(R)
    G, R = np.array(G), np.array(R)
    coef, res, rank, _ = np.linalg.lstsq(G, R, rcond=None)
    resid = R - G @ coef
    dof = max(len(R) - rank, 1)
    return float(np.sqrt(resid @ resid / dof)), len(R)


def grow(name, site):
    print(f'\n===== {name} =====')
    rows = load_rates(name, site)
    g = shape(site['geom'])
    lat0, lon0 = g.centroid.y, g.centroid.x
    X = local_xyz(rows, lat0, lon0)
    n = len(rows)

    # noise floor from ring points' rate scatter
    ring_rates = [r[d] for r in rows if r['group'] == 'ring'
                  for d in ('ascending', 'descending') if r[d] is not None]
    ring_rms = float(np.std(ring_rates)) if len(ring_rates) >= 5 else 3.0
    tol = TOL_FACTOR * ring_rms
    print(f'  {n} rated points; ring rms {ring_rms:.1f} mm/yr -> growth tol {tol:.1f}')

    # seed: best kNN patch by rigid rms
    best_seed, best_rms = None, np.inf
    for i in range(n):
        d2 = ((X - X[i]) ** 2).sum(axis=1)
        knn = list(np.argsort(d2)[:KNN_SEED])
        rms, nobs = rigid_rms(knn, X, rows)
        if rms < best_rms:
            best_rms, best_seed = rms, knn
    dom = set(best_seed)
    print(f'  seed patch rms {best_rms:.1f} mm/yr')

    # greedy contiguous growth
    while True:
        cands = []
        for j in range(n):
            if j in dom:
                continue
            dmin = min(np.linalg.norm(X[j][:2] - X[i][:2]) for i in dom)
            if dmin > NEIGHBOR_M:
                continue
            rms, _ = rigid_rms(list(dom) + [j], X, rows)
            cands.append((rms, j))
        if not cands:
            break
        cands.sort()
        rms, j = cands[0]
        if rms > tol:
            break
        dom.add(j)
    rms_final, nobs = rigid_rms(list(dom), X, rows)
    inside_flags = [g.contains(Point(rows[i]['lon'], rows[i]['lat'])) for i in range(n)]
    n_in_dom_in_poly = sum(1 for i in dom if inside_flags[i])
    n_poly = sum(inside_flags)
    print(f'  domain: {len(dom)} of {n} points (rms {rms_final:.1f} mm/yr, {nobs} obs)')
    print(f'  domain∩polygon: {n_in_dom_in_poly} | polygon points total: {n_poly} '
          f'| domain points outside polygon: {len(dom) - n_in_dom_in_poly}')

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8.5, 8))
    if g.geom_type == 'MultiPolygon':
        for part in g.geoms:
            ax.plot(*part.exterior.xy, color='k', lw=1.2, label=None)
    else:
        ax.plot(*g.exterior.xy, color='k', lw=1.2)
    for i in range(n):
        r = rows[i]
        v = r['descending'] if r['descending'] is not None else r['ascending']
        in_dom = i in dom
        ax.scatter(r['lon'], r['lat'], s=110 if in_dom else 45,
                   c=[v], cmap='PRGn', vmin=-30, vmax=30,
                   edgecolors='#c22' if in_dom else '#888',
                   linewidths=2.2 if in_dom else 0.6, zorder=3 if in_dom else 2)
    ax.set_title(f'{name}: kinematic domain (red rims, n={len(dom)}) vs mapped polygon\n'
                 f'rigid rms {rms_final:.1f} mm/yr; boundary-agnostic growth, tol {tol:.1f}')
    ax.set_aspect(1 / math.cos(math.radians(lat0)))
    out = OUT / f'domain_{name}.png'
    plt.savefig(out, dpi=115, bbox_inches='tight')
    print(f'  wrote {out}')


def main():
    sites = json.load(open(OUT / 'sites.json'))
    for name, site in sites.items():
        grow(name, site)


if __name__ == '__main__':
    main()
