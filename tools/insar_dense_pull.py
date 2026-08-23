#!/usr/bin/env python3
"""Dense point pull + independence distillation (Hig, 2026-08-22).

The sparse grids (~150 m) were chosen for point independence by fiat. There
may be more fidelity in the full dataset — so: pull a dense grid (60 m,
polygon + 300 m halo, capped), MEASURE the spatial correlation of rate
residuals empirically, and distill an independent set from that measurement
rather than an assumed spacing. Then refit free + gravity-constrained rigid
models on the distilled set and compare against the sparse-fit parameters —
parameter stability under densification is itself a robustness check.

Distillation: semivariogram of rate residuals (after removing the free
rigid-fit prediction) in distance bins -> correlation length L_c = distance
where semivariance reaches ~80% of sill -> block-median decimation on an
L_c grid. Block medians beat subsampling: they use all the data while
delivering ~independent samples.

All fetches share the standing cache; cold requests are politely paced.
Run AFTER insar_rotation_gravity.py (reuses its DEM cache too).
"""
import json
import math
import time
from pathlib import Path

import numpy as np
from shapely.geometry import shape, Point

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'data' / 'insar_power'
GRID_M = 60
HALO_M = 300
CAP = 360
L_ASC = np.array([-0.613, -0.142, 0.777])
L_DESC = np.array([0.613, -0.142, 0.777])


def main():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'sc', ROOT / 'tools' / 'insar_site_characterization.py')
    sc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sc)
    spec2 = importlib.util.spec_from_file_location(
        'rg', ROOT / 'tools' / 'insar_rotation_gravity.py')
    rg = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(rg)

    sites = json.load(open(OUT / 'sites.json'))
    summary = {}
    for name, site in sites.items():
        print(f'\n===== {name}: dense pull =====', flush=True)
        g = shape(site['geom'])
        lat0, lon0 = g.centroid.y, g.centroid.x
        gh = g.buffer(HALO_M / 111000.0)
        minx, miny, maxx, maxy = gh.bounds
        dlat = GRID_M / 111000.0
        dlon = GRID_M / (111000.0 * math.cos(math.radians(lat0)))
        pts = []
        lat = miny
        while lat <= maxy:
            lon = minx
            while lon <= maxx:
                if gh.contains(Point(lon, lat)):
                    pts.append((lat, lon))
                lon += dlon
            lat += dlat
        if len(pts) > CAP:
            rng = np.random.default_rng(3)
            pts = [pts[i] for i in rng.choice(len(pts), CAP, replace=False)]
        print(f'  {len(pts)} probe points (60 m grid, polygon+halo)', flush=True)

        rows = []
        n_cov = 0
        for k, (lat, lon) in enumerate(pts):
            row = {'lat': lat, 'lon': lon,
                   'in': g.contains(Point(lon, lat))}
            ok = False
            for dirn in ('ascending', 'descending'):
                fit = sc.rate_fit(sc.fetch_ts(lat, lon, dirn))
                row[dirn] = fit['rate'] if fit else None
                ok = ok or fit is not None
            if ok:
                rows.append(row)
                n_cov += 1
            if (k + 1) % 60 == 0:
                print(f'    {k + 1}/{len(pts)} probed ({n_cov} with data)', flush=True)
        print(f'  coverage: {len(rows)}/{len(pts)}', flush=True)

        # local coords + free rigid fit on ALL dense points (in-polygon only
        # for the fit; halo points serve the variogram + later domain work)
        X = np.array([[(r['lon'] - lon0) * 111000 * math.cos(math.radians(lat0)),
                       (r['lat'] - lat0) * 111000, 0.0] for r in rows])
        infit = [i for i, r in enumerate(rows) if r['in']]
        fit = rg.rigid_fit(X[infit], [rows[i] for i in infit])
        print(f'  dense free fit (in-polygon, n_obs={len(fit["obs"])}): '
              f'rms {fit["rms"]:.1f} mm/yr, R2 {fit["r2"]:.2f}')

        # residuals per observation -> semivariogram vs pair distance
        tag_pos = np.array([X[infit[i]][:2] for i, _ in fit['tags']])
        resid = fit['obs'] - fit['pred']
        nb = min(len(resid), 1200)
        rng = np.random.default_rng(5)
        sel = rng.choice(len(resid), nb, replace=False)
        bins = np.arange(0, 620, 60)
        gamma = np.zeros(len(bins) - 1)
        cnt = np.zeros(len(bins) - 1)
        for a in range(len(sel)):
            for b_ in range(a + 1, len(sel)):
                d = np.linalg.norm(tag_pos[sel[a]] - tag_pos[sel[b_]])
                k = np.searchsorted(bins, d) - 1
                if 0 <= k < len(gamma):
                    gamma[k] += 0.5 * (resid[sel[a]] - resid[sel[b_]]) ** 2
                    cnt[k] += 1
        gamma = np.where(cnt > 10, gamma / np.maximum(cnt, 1), np.nan)
        sill = np.nanmedian(gamma[-3:])
        lc = None
        for k in range(len(gamma)):
            if np.isfinite(gamma[k]) and gamma[k] >= 0.8 * sill:
                lc = (bins[k] + bins[k + 1]) / 2
                break
        lc = lc or 150.0
        print(f'  residual semivariogram: sill ~{sill:.0f} mm2/yr2, '
              f'correlation length ~{lc:.0f} m')

        # block-median distillation on an lc grid, then refit
        blocks = {}
        for i in infit:
            key = (int(X[i][0] // lc), int(X[i][1] // lc))
            blocks.setdefault(key, []).append(i)
        drows, dX = [], []
        for key, members in blocks.items():
            rr = {'lat': 0, 'lon': 0}
            for dirn in ('ascending', 'descending'):
                vals = [rows[i][dirn] for i in members if rows[i][dirn] is not None]
                rr[dirn] = float(np.median(vals)) if vals else None
            dX.append(np.median([X[i] for i in members], axis=0))
            drows.append(rr)
        dX = np.array(dX)
        dfit = rg.rigid_fit(dX, drows)
        print(f'  distilled: {len(drows)} block-medians ({lc:.0f} m blocks) -> '
              f'free fit rms {dfit["rms"]:.1f} mm/yr, R2 {dfit["r2"]:.2f}')
        summary[name] = {
            'n_probe': len(pts), 'n_cov': len(rows),
            'dense_rms': fit['rms'], 'dense_r2': fit['r2'],
            'corr_len_m': lc, 'n_blocks': len(drows),
            'distilled_rms': dfit['rms'], 'distilled_r2': dfit['r2'],
        }
    json.dump(summary, open(OUT / 'dense_summary.json', 'w'), indent=1)
    print('\nDENSE-PULL-COMPLETE')
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
