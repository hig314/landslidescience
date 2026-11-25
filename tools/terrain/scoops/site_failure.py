#!/opt/anaconda3/bin/python3
"""A plausible large failure at one named slope: local peaks of required cohesion.

Unlike the maps (one size class per grid, each cell keeping its most demanding
surface), this looks for whole GEOMETRIES: spheres (x, y, z, R) at which the cohesion
needed for F = 1 (Bishop, dry, uniform, one friction angle) is a local maximum, with
the volume left free. Topography is the only input.

  1. seeds: the lattice search of scoops.search, per size class on that class's grid
     (block means of the source DEM), nodes in a box round the site;
  2. climb: Nelder-Mead from every seed on ONE grid (--climb-dx) so sizes compare,
     in (x, y, ln R, depth of the sphere's lowest point), volume free between
     --vmin and --vmax; a second climb holds volume above --big;
  3. peaks: converged surfaces grouped by footprint overlap, the highest of each
     group kept, and judged against the --az window (slide direction, centroid bearing);
  4. products (--make ID ...): the chosen surface re-climbed on the source grid, then
     thickness and the ground with the mass removed, cropped, on the source grid.

    site_failure.py peaks    --name sadie_peak --lon -151.304173 --lat 59.472539 --phi 30
    site_failure.py products --name sadie_peak --phi 30 --make 0 7

The evaluator is scoops_core.c's own `sphere()` reached through scoops_climb with no
moves allowed, so nothing here re-derives the mechanics. A sphere that cuts the ground
in several patches is judged patch by patch, as in the core; the product is one patch.
"""
import argparse
import ctypes as C
import json
import math
import multiprocessing as mp
import subprocess
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from scipy import ndimage
from scipy.optimize import minimize

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoops as S

SRC = '/vsicurl/https://lidar.landslidescience.org/cog/kbay_topobathy_v1_8m.tif'
OUT = Path('/Volumes/Nunatak/terrain_build/scoops_state/sites')
GDAL = '/opt/homebrew/bin'
GAMMA = 26.0
CLASSES = [(1e6, 3), (1e7, 6), (1e8, 12), (1e9, 24)]      # (class floor m3, block factor on the 8 m source)
BAD = 1e7


def window(name, lon, lat, half):
    """The source DEM round the site: array (north up), west, north, cell, crs, site x, y."""
    d = OUT / name; d.mkdir(parents=True, exist_ok=True)
    f = d / 'dem_src.tif'
    from rasterio.warp import transform
    if not f.exists():                      # the only step that needs the network
        with rasterio.open(SRC) as s:
            (x,), (y,) = transform('EPSG:4326', s.crs, [lon], [lat])
            cell = s.res[0]
            c0 = round((x - half - s.bounds.left) / cell); r0 = round((s.bounds.top - (y + half)) / cell)
            n = int(2 * half / cell) // 48 * 48                  # every block factor divides it
            subprocess.run([f'{GDAL}/gdal_translate', '-q', '-srcwin', str(c0), str(r0), str(n), str(n),
                            '-co', 'COMPRESS=DEFLATE', '-co', 'PREDICTOR=3', '-co', 'TILED=YES', SRC, str(f)], check=True)
    with rasterio.open(f) as s:
        (x,), (y,) = transform('EPSG:4326', s.crs, [lon], [lat])
        a = s.read(1).astype(np.float64)
        assert s.nodata is None or not (a == s.nodata).any(), 'holes in the window'
        return a, s.transform.c, s.transform.f, s.res[0], s.crs, x, y


def block(a, f):
    ny, nx = a.shape[0] // f, a.shape[1] // f
    return a[:ny * f, :nx * f].reshape(ny, f, nx, f).mean((1, 3))


class Eval:
    """One grid in the core; cohesion of a sphere's most demanding patch, and its footprint."""

    def __init__(self, dem_north_up, dx, phi, vmin, vmax):
        self.dem = np.ascontiguousarray(dem_north_up[::-1], dtype=np.float64)      # south up, kept alive
        self.dx, self.ny, self.nx = dx, *self.dem.shape
        self.phi = np.array([float(phi)]); self.vmin, self.vmax = vmin, vmax
        self.lib = S._load()
        self.lib.scoops_setup(S._dp(self.dem), self.ny, self.nx, C.c_double(dx))
        self.lib.scoops_extras_set(None); self.lib.scoops_nodepar_set(None, None)
        self.creq = np.zeros((self.ny, self.nx), np.float32); self.cvol = np.zeros_like(self.creq)
        self.edges = np.array([vmin, vmax]); self.st = S.Stats()

    def _call(self, p, paint):
        p = np.asarray(p, float)
        a = [np.array([v]) for v in p]; best = np.full(1, -1e30, np.float32); d = C.c_double
        self.lib.scoops_climb(*[S._dp(v) for v in a], C.c_long(1), d(0), d(0), d(0), 0, 0,
                              d(self.vmin), d(self.vmax), 0, d(GAMMA), 1, S._dp(self.phi),
                              1 if paint else 0, S._dp(self.edges),
                              S._fp(self.creq) if paint else None, S._fp(self.cvol) if paint else None,
                              d(0), S._fp(best), C.byref(self.st))
        return float(best[0])

    def c(self, p):
        return self._call(p, False)

    def paint(self, p):
        """(c, volume, mask south-up, thickness south-up) of the patch that holds c; None if c <= 0."""
        self.creq[:] = 0; self.cvol[:] = 0
        c = self._call(p, True)
        if not c > 0:
            return None
        lab, _ = ndimage.label(self.creq > 0)
        j, i = np.unravel_index(np.argmax(self.creq), self.creq.shape)
        m = lab == lab[j, i]
        j0, j1 = np.where(m.any(1))[0][[0, -1]]; i0, i1 = np.where(m.any(0))[0][[0, -1]]
        th = np.zeros((self.ny, self.nx), np.float32)
        yy, xx = np.mgrid[j0:j1 + 1, i0:i1 + 1]
        r2 = ((xx + .5) * self.dx - p[0]) ** 2 + ((yy + .5) * self.dx - p[1]) ** 2
        base = p[2] - np.sqrt(np.maximum(p[3] ** 2 - r2, 0))
        t = np.where(r2 < p[3] ** 2, self.dem[j0:j1 + 1, i0:i1 + 1] - base, 0)
        th[j0:j1 + 1, i0:i1 + 1] = np.where(m[j0:j1 + 1, i0:i1 + 1], np.maximum(t, 0), 0)
        return c, float(self.cvol[j, i]), m, th


def to_p(u, zref):
    R = math.exp(u[2])
    return [u[0], u[1], zref - u[3] + R, R]


R_PLANE = 2e5      # a sphere this large is a plane to 6 m over a 3 km footprint


def plane_p(u, anchor):
    """Plane dipping u[1] deg toward azimuth u[0], u[2] m under the anchor point, as a huge sphere."""
    az, b = math.radians(u[0]), math.radians(u[1])
    n = (math.sin(az) * math.sin(b), math.cos(az) * math.sin(b), math.cos(b))
    return [anchor[0] + R_PLANE * n[0], anchor[1] + R_PLANE * n[1], anchor[2] - u[2] + R_PLANE * n[2], R_PLANE]


def _nm(ev, u, steps, top, restarts=2):
    f = lambda u: (lambda c: -c if c > -1e29 else BAD)(ev.c(top(u)))
    if f(u) >= BAD:
        return None
    n = 0
    for k in range(restarts):
        sim = np.vstack([u] + [u + e for e in np.diag(np.asarray(steps) / 2 ** k)])
        r = minimize(f, u, method='Nelder-Mead', options=dict(initial_simplex=sim, xatol=0.5, fatol=0.01, maxfev=800))
        u = r.x; n += r.nfev
    return top(u), -r.fun, n, [float(v) for v in u]


def climb(ev, p0, h):
    """Nelder-Mead from sphere p0 = (x, y, z, R); h = first step, metres. Returns (p, c, evaluations, u)."""
    zref = p0[2] - p0[3]
    return _nm(ev, np.array([p0[0], p0[1], math.log(p0[3]), 0.0]), [h, h, 0.15, h / 2], lambda u: to_p(u, zref))


def climb_plane(ev, u0, anchor, scale=1.0):
    """The same for a plane: (dip direction, dip, depth under the anchor). scale < 1: a polish that stays put."""
    return _nm(ev, np.array(u0, float), [8.0 * scale, 4.0 * scale, 20.0 * scale], lambda u: plane_p(u, anchor))


_EV = {}


def _init(dem, dx, phi, bounds):
    for k, (lo, hi) in bounds.items():
        _EV[k] = Eval(dem, dx, phi, lo, hi)


def _job(a):
    k, p0, h = a
    r = climb_plane(_EV[k], p0, h) if k.startswith('p') else climb(_EV[k], p0, h)
    return None if r is None else (k, r[0], r[1], r[2], r[3], h if k.startswith('p') else None)


def describe(ev, p, W, y0, sx, sy):
    r = ev.paint(p)
    if r is None:
        return None
    c, V, m, th = r
    dx = ev.dx
    jj, ii = np.nonzero(m); t = th[jj, ii]; z = ev.dem[jj, ii]
    if not (t > 0).any():
        return None
    wsum = max(t.sum(), 1e-9)
    mx = W + ((ii + .5) * dx * t).sum() / wsum; my = y0 + ((jj + .5) * dx * t).sum() / wsum
    gy, gx = np.gradient(ev.dem, dx)
    az = math.degrees(math.atan2(-(gx[jj, ii] * t).sum(), -(gy[jj, ii] * t).sum())) % 360
    return dict(c_kPa=round(c, 1), vol=float(f'{V:.4g}'), vol_cells=float(f'{t.sum() * dx * dx:.4g}'),
                x=round(W + p[0], 1), y=round(y0 + p[1], 1), z=round(p[2], 1), R=round(p[3], 1),
                area_km2=round(m.sum() * dx * dx / 1e6, 3), tmax=round(float(t.max())), tmean=round(float(t[t > 0].mean())),
                ztop=round(float(z.max())), ztoe=round(float(z.min())),
                below_sea=round(float(t[z < 0].sum() / wsum), 3),
                brg=round(math.degrees(math.atan2(mx - sx, my - sy)) % 360), dist=round(math.hypot(mx - sx, my - sy)),
                slide_az=round(az), cx=round(mx), cy=round(my)), m


def peaks(a):
    A, W, N, cell, crs, sx, sy = window(a.name, a.lon, a.lat, a.half)
    y0 = N - A.shape[0] * cell
    out = OUT / a.name
    seeds = []
    for v0, f in CLASSES:
        dx = cell * f; dem = block(A, f); ny, nx = dem.shape
        g = S.Grid(dem[::-1].copy(), dx); r0 = 1.36 * v0 ** (1 / 3)
        step = max(1, int(round(r0 / (2 if v0 < 1e7 else 4) / dx)))
        bx, by = sx - a.box_off, sy - a.box_off
        i0, i1 = int((bx - a.box - W) / dx), int((bx + a.box - W) / dx)
        j0, j1 = int((by - a.box - y0) / dx), int((by + a.box - y0) / dx)
        xn, yn = S.lattice(nx, ny, dx, i0, j0, i1, j1, step)
        r = S.search(g, xn, yn, float(dem.min()), float(dem.max()) + 40 * r0, r0 / 4, r0 / 8, v0, 10 * v0, 0.05 * v0,
                     gamma=GAMMA, phis=[a.phi], edges=[v0, 10 * v0], workers=a.workers, zspan=10 * r0)
        nb = r['node_best']; k = np.argsort(-nb)[:a.seeds]; k = k[nb[k] > -1e29]
        seeds += [(v0, [xn[q], yn[q], r['node_z'][q], r['node_R'][q]], r0 / 4) for q in k]
        print(f'class {v0:.0e}: {len(xn)} nodes, {r["stats"]["surfaces"]} surfaces, seeds {len(k)}, best {nb.max():.1f} kPa', flush=True)
    f = int(round(a.climb_dx / cell)); demc = block(A, f); dxc = cell * f
    bounds = {'free': (a.vmin, a.vmax), 'big': (a.big, a.vmax)}
    jobs = [('free', p, h) for _, p, h in seeds] + [('big', p, h) for v0, p, h in seeds if v0 >= a.big / 10]
    if a.planes:
        bounds.update(pfree=bounds['free'], pbig=bounds['big'])
        bx, by = sx - a.box_off - W, sy - a.box_off - y0
        for ax in np.arange(bx - a.box, bx + a.box + 1, 400):
            for ay in np.arange(by - a.box, by + a.box + 1, 400):
                anc = (float(ax), float(ay), float(demc[::-1][int(ay / dxc), int(ax / dxc)]))
                for az in np.arange(a.az[0] + 15, a.az[1], 30):
                    for dip in (18, 26, 34, 42):
                        for dep in (30, 80, 160):
                            jobs += [(k, (float(az), dip, dep), anc) for k in ('pfree', 'pbig')]
    raw = out / f'climbs_phi{a.phi:g}.json'
    if a.regroup and raw.exists():
        res = [tuple(r) for r in json.loads(raw.read_text())]
    else:
        with mp.get_context('fork').Pool(a.workers, _init, (demc, dxc, a.phi, bounds)) as pool:
            res = [r for r in pool.imap_unordered(_job, jobs, chunksize=4) if r is not None]
        raw.write_text(json.dumps(res))
    print(f'climbs {len(res)} of {len(jobs)}, evaluations {sum(r[3] for r in res)}', flush=True)
    _init(demc, dxc, a.phi, bounds)
    rows = {}
    for kind in bounds:
        ev = _EV[kind]; kept = []
        for _, p, c, _n, u, anc in sorted((r for r in res if r[0] == kind), key=lambda r: -r[2]):
            if c <= 0:
                break
            d = describe(ev, p, W, y0, sx, sy)
            if d is None:
                continue
            rec, m = d
            dup = False
            for q in kept:
                inter = (m & q[1]).sum()
                if inter / (m.sum() + q[1].sum() - inter) > a.iou:
                    q[0]['n'] += 1; dup = True; break
            if not dup:
                rec['n'] = 1; rec['p'] = [float(v) for v in p]
                if anc is not None:
                    rec.update(plane_az=round(u[0] % 360, 1), plane_dip=round(u[1], 1), u=u, anchor=list(anc))
                d_az = lambda v: a.az[0] <= v <= a.az[1]
                rec['on_face'] = bool(d_az(rec['slide_az']) and d_az(rec['brg']) and rec['dist'] <= a.maxdist)
                kept.append((rec, m))
        rows[kind] = [q[0] for q in kept]
        for i, q in enumerate(rows[kind]):
            q['id'] = f'{kind}{i}'
    meta = dict(name=a.name, lon=a.lon, lat=a.lat, phi=a.phi, gamma=GAMMA, climb_dx=dxc, vmin=a.vmin, vmax=a.vmax, big=a.big,
                az=a.az, maxdist=a.maxdist, iou=a.iou, site_xy=[sx, sy], crs=str(crs), peaks=rows)
    (out / f'peaks_phi{a.phi:g}.json').write_text(json.dumps(meta, indent=1))
    for kind in rows:
        print(f'--- {kind}: {len(rows[kind])} distinct, on the face {sum(q["on_face"] for q in rows[kind])}')
        shown = [q for q in rows[kind] if q['on_face']][:12] + [q for q in rows[kind] if not q['on_face']][:4]
        for q in shown:
            print(' {id:>6} {on} c {c_kPa:7.1f} V {vol:9.3g} A {area_km2:6.3f} tmax {tmax:4d} top {ztop:5d} toe {ztoe:5d} az {slide_az:3d} brg {brg:3d} d {dist:5d} R {R:8.1f} n {n} {pl}'.format(pl='dip %.1f toward %.1f' % (q['plane_dip'], q['plane_az']) if 'plane_dip' in q else '',
                on='face' if q['on_face'] else ' off', **q))


def products(a):
    meta = json.loads((OUT / a.name / f'peaks_phi{a.phi:g}.json').read_text())
    A, W, N, cell, crs, sx, sy = window(a.name, meta['lon'], meta['lat'], a.half)
    y0 = N - A.shape[0] * cell
    out = OUT / a.name
    allp = {q['id']: q for k in meta['peaks'].values() for q in k}
    done = []
    for pid in a.make:
        q = allp[pid]
        lo = meta['big'] if 'big' in pid else meta['vmin']
        ev = Eval(A, cell, meta['phi'], lo, meta['vmax'])
        c0 = ev.c(q['p'])
        if 'u' in q:
            p, c, n, u = climb_plane(ev, q['u'], q['anchor'], 0.1)
        else:
            p, c, n, u = climb(ev, q['p'], 4 * cell)
        rec, m = describe(ev, p, W, y0, sx, sy)
        _, V, m, th = ev.paint(p)
        if 'u' in q:
            rec.update(plane_az=round(u[0] % 360, 1), plane_dip=round(u[1], 1))
        rec.update(id=pid, phi=meta['phi'], gamma=GAMMA, c_at_climb_grid=q['c_kPa'], c_before_polish=round(c0, 1), evaluations=n)
        jj, ii = np.nonzero(m); pad = int(a.margin / cell)
        j0, j1 = max(jj.min() - pad, 0), min(jj.max() + pad + 1, ev.ny); i0, i1 = max(ii.min() - pad, 0), min(ii.max() + pad + 1, ev.nx)
        th_n = th[j0:j1, i0:i1][::-1]; dem_n = ev.dem[j0:j1, i0:i1][::-1]
        tf = from_origin(W + i0 * cell, y0 + j1 * cell, cell, cell)
        prof = dict(driver='GTiff', height=th_n.shape[0], width=th_n.shape[1], count=1, dtype='float32', crs=crs, transform=tf,
                    compress='deflate', predictor=3, tiled=True)
        tag = f'{a.name}_phi{meta["phi"]:g}_{pid}'
        for nm, arr in (('thickness', th_n), ('dem_failed', dem_n - th_n), ('dem_before', dem_n)):
            with rasterio.open(out / f'{tag}_{nm}.tif', 'w', **prof) as d:
                d.write(arr.astype('float32'), 1)
        (out / f'{tag}.json').write_text(json.dumps(rec, indent=1))
        print(json.dumps(rec), flush=True)
        done.append(rec)
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('stage', choices=['peaks', 'products'])
    ap.add_argument('--name', required=True)
    ap.add_argument('--lon', type=float); ap.add_argument('--lat', type=float)
    ap.add_argument('--phi', type=float, default=30)
    ap.add_argument('--half', type=float, default=10000, help='half width of the DEM window, m')
    ap.add_argument('--box', type=float, default=4500, help='seed nodes within this of the site, m')
    ap.add_argument('--box-off', type=float, default=0, help='move the seed box this far west and south, m')
    ap.add_argument('--seeds', type=int, default=250, help='per class')
    ap.add_argument('--climb-dx', type=float, default=16)
    ap.add_argument('--vmin', type=float, default=3e5); ap.add_argument('--vmax', type=float, default=1e10)
    ap.add_argument('--big', type=float, default=1e8)
    ap.add_argument('--az', type=float, nargs=2, default=[180, 270], help='slide direction and centroid bearing window')
    ap.add_argument('--maxdist', type=float, default=3000)
    ap.add_argument('--iou', type=float, default=0.5)
    ap.add_argument('--workers', type=int, default=5)
    ap.add_argument('--regroup', action='store_true', help='reuse the saved climbs')
    ap.add_argument('--planes', action='store_true', help='also planar surfaces (spheres of 200 km radius)')
    ap.add_argument('--make', nargs='*', default=[])
    ap.add_argument('--margin', type=float, default=2000)
    a = ap.parse_args()
    peaks(a) if a.stage == 'peaks' else products(a)


if __name__ == '__main__':
    main()
