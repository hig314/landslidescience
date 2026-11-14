#!/opt/anaconda3/bin/python3
"""Sheet + table for explore_patch.py output: required cohesion beside same-scale
slope (steepest diameter, L = 2 r0, chord_slope.py) and local relief (range within
2 r0). Terrain only: nothing else goes on these maps.

    compare_patch.py DIR
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm, Normalize
import numpy as np
import rasterio
from scipy.ndimage import maximum_filter, minimum_filter
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chord_slope as CS

d = Path(sys.argv[1])
run = json.loads((d / 'run.json').read_text())
if (d / 'run_1e9.json').exists():            # the smoke run wrote this class first
    extra = json.loads((d / 'run_1e9.json').read_text())['classes']
    have = {c['v0'] for c in run['classes']}
    run['classes'] += [c for c in extra if c['v0'] not in have]
run['classes'].sort(key=lambda c: c['v0'])
PHIS = run['phis']
show = [0, 10, 20, 30, 40]
n = len(run['classes'])
ncol = 2 + len(show)
fig, ax = plt.subplots(n, ncol, figsize=(2.35 * ncol, 2.45 * n), constrained_layout=True)
ax = np.atleast_2d(ax)
cmap_c = plt.get_cmap('Oranges').copy(); cmap_c.set_under((0, 0, 0, 0))
norm_c = LogNorm(10, 10000)
table = []
for r, c in enumerate(run['classes']):
    tag = f"{c['v0']:.0e}"
    with rasterio.open(d / f'class_{tag}.tif') as s:
        all_b = s.read()
    with rasterio.open(d / f'dem_{tag}.tif') as s:
        dem = s.read(1).astype(np.float64)
    dx, r0 = c['dx'], c['r0']
    # hillshade for context, sun from the NW
    gy, gx = np.gradient(dem, dx)
    hs = np.clip(.5 + .5 * (-(gx - gy) * .7) / np.sqrt(1 + gx * gx + gy * gy), 0, 1)
    sl = CS.diameter_slope(dem, 2 * r0, dx)
    k = int(round(2 * r0 / dx))
    yy, xx = np.ogrid[-k:k + 1, -k:k + 1]
    fp = xx * xx + yy * yy <= k * k
    rel = maximum_filter(dem, footprint=fp) - minimum_filter(dem, footprint=fp)
    panels = [('slope', sl), ('relief', rel)]
    for j, p in enumerate(panels + [(f'phi{ph}', all_b[PHIS.index(ph)]) for ph in show]):
        a = ax[r, j]
        a.imshow(hs, cmap='gray', vmin=0, vmax=1, interpolation='bilinear')
        if p[0] == 'slope':
            im = a.imshow(sl, cmap='Purples', norm=Normalize(0, 45), alpha=.9, interpolation='nearest')
            im_s = im
        elif p[0] == 'relief':
            im_r = a.imshow(rel, cmap='Greens', norm=Normalize(0, np.nanpercentile(rel, 99.5)), alpha=.9, interpolation='nearest')
        else:
            im_c = a.imshow(np.where(p[1] > 0, p[1], np.nan), cmap=cmap_c, norm=norm_c, alpha=.92, interpolation='nearest')
        a.set_xticks([]); a.set_yticks([])
        for s_ in a.spines.values():
            s_.set_visible(False)
        if r == 0:
            a.set_title({'slope': 'steepest diameter', 'relief': 'local relief'}.get(p[0], f"c required, phi {p[0][3:]}°"), fontsize=9)
    ax[r, 0].set_ylabel(f"{c['v0']:.0e}–{c['v1']:.0e} m³\nL {2 * r0:.0f} m, grid {dx:.0f} m", fontsize=8)
    ok = np.isfinite(sl) & np.isfinite(rel)
    sub = (slice(None, None, max(1, dem.shape[0] // 500)),) * 2
    for i, ph in enumerate(PHIS):
        v = all_b[i]
        pos = v > 0
        row = dict(v0=c['v0'], phi=ph, frac_needs_c=round(float(np.mean(v[ok] > 0)), 3),
                   p50=round(float(np.median(v[ok])), 1), p99=round(float(np.percentile(v[ok], 99)), 1), max=round(float(v[ok].max()), 1))
        if np.mean(v[ok] > 0) > .02:
            m = (ok & pos)[sub]
            row['rho_slope'] = round(float(spearmanr(v[sub][m], sl[sub][m]).statistic), 3)
            row['rho_relief'] = round(float(spearmanr(v[sub][m], rel[sub][m]).statistic), 3)
            row['rho_ord'] = round(float(spearmanr(v[sub][m], all_b[i + 2 * len(PHIS)][sub][m]).statistic), 3)
            mv = all_b[i + len(PHIS)][pos & ok]
            row['critV_at_lowest_decile_frac'] = round(float(np.mean(mv < c['v0'] * 10 ** .1)), 3)
            row['critV_at_top_decile_frac'] = round(float(np.mean(mv > c['v1'] * 10 ** -.1)), 3)
        table.append(row)
fig.colorbar(im_c, ax=ax[:, 2:], shrink=.5, location='bottom', pad=.01, label='required cohesion, kPa (F = 1, dry, uniform, γ 26 kN/m³); clear = none needed')
fig.colorbar(im_s, ax=ax[:, 0], shrink=.8, location='bottom', pad=.01, label='degrees')
fig.colorbar(im_r, ax=ax[:, 1], shrink=.8, location='bottom', pad=.01, label='m (top class scale)')
fig.savefig(d / 'sheet.png', dpi=120)
(d / 'table.json').write_text(json.dumps(table, indent=0))
keys = ['v0', 'phi', 'frac_needs_c', 'p50', 'p99', 'max', 'rho_slope', 'rho_relief', 'rho_ord', 'critV_at_lowest_decile_frac', 'critV_at_top_decile_frac']
print('\t'.join(keys))
for t in table:
    print('\t'.join(str(t.get(k, '')) for k in keys))
