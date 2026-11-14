#!/opt/anaconda3/bin/python3
"""Summary figure across windows and sizes: the required envelope and how it depends on size.

    envelope_summary.py DIR OUT.png

A tile counts if it is mostly land inside the coverage polygon. tau(sigma): the p90 across
tiles of each tile's highest tau in that sigma band.
"""
import glob
import json
import os
import re
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

WINDOWS = [('coast', 'Coast Mtns', '#2a78d6'), ('chugach', 'Chugach', '#eb6834'), ('stelias', 'St. Elias', '#1baf7a'),
           ('alaskarange', 'Alaska Range', '#eda100'), ('brooks', 'Brooks Range', '#e87ba4'), ('kodiak', 'Kodiak', '#008300')]
SIZES = [6, 7, 8, 9, 10]
SIZE_COL = ['#86b6ef', '#5598e7', '#2a78d6', '#1c5cab', '#0d366b']
E = 10 ** (1 + np.arange(129) / 32)
MID = np.sqrt(E[:-1] * E[1:])
BANDS = 10 ** np.arange(2, 4.21, .15)     # kPa


def load(d):
    R = {}
    for f in glob.glob(os.path.join(d, '*_v*_dx*.npz')):
        m = re.match(r'(.+)_v(\d+)_dx', os.path.basename(f))
        z = np.load(f)
        ok = (z['tile_land'] > .5) & z['tile_cov']
        R[(m[1], int(m[2]))] = dict(tau=z['tile_tau'][ok], ratio=z['tile_ratio'][ok], c30=z['tile_top'][ok][:, list(z['phis']).index(30), 0], r0=float(z['r0']))
    return R


def env(rs, q=90, minn=10, bands=BANDS):
    out = []
    for b in bands:
        k = np.where((MID >= b / 10 ** .075) & (MID < b * 10 ** .075))[0]
        v = np.concatenate([r['tau'][:, k].max(1) for r in rs])
        v = v[v > 0]
        out.append(np.percentile(v, q) if len(v) >= minn else np.nan)
    return np.array(out)


def main(d, png):
    R = load(d)
    fig, ax = plt.subplots(1, 4, figsize=(17, 4.6), constrained_layout=True)
    a = ax[0]
    for w, lab, c in WINDOWS:
        a.plot(BANDS, env([R[(w, s)] for s in SIZES]), color=c, lw=2, label=lab)
    a.set_title('Required shear stress by window (all sizes, p90)', fontsize=10, loc='left')
    a = ax[1]
    for s, c in zip(SIZES, SIZE_COL):
        a.plot(BANDS, env([R[(w, s)] for w, _, _ in WINDOWS]), color=c, lw=2, label=f'10$^{{{s}}}$ m³')
    a.set_title('Same, by surface size (all windows, p90)', fontsize=10, loc='left')
    for a in ax[:2]:
        a.set_xscale('log'); a.set_yscale('log'); a.set_xlim(90, 2e4); a.set_ylim(50, 1e4)
        for ang in (20, 30, 40, 50):
            t = np.tan(np.radians(ang))
            a.plot([90, 2e4], [90 * t, 2e4 * t], color='#b5b4ae', lw=.7, ls=':')
            x = min(1e4 / t, 2e4) / 1.15
            a.text(x, x * t, f'{ang}°', fontsize=7, color='#52514e', rotation=38, ha='right', va='bottom')
        a.set_xlabel('normal stress σ, kPa'); a.set_ylabel('shear stress τ, kPa')
        a.grid(alpha=.15); a.legend(fontsize=8, frameon=False, loc='upper left')
    a = ax[2]
    for w, lab, c in WINDOWS:
        a.plot(SIZES, [np.degrees(np.arctan(np.percentile(R[(w, s)]['ratio'], 90))) for s in SIZES], color=c, lw=2, marker='o', ms=8, label=lab)
    a.set_title('Friction angle needing no cohesion (p90 of tiles)', fontsize=10, loc='left')
    a.set_ylabel('degrees'); a.set_ylim(0, 65)
    a = ax[3]
    for w, lab, c in WINDOWS:
        a.plot(SIZES, [np.percentile(R[(w, s)]['c30'], 99) / 1000 for s in SIZES], color=c, lw=2, marker='o', ms=8, label=lab)
    a.set_title('Cohesion needed at φ = 30° (p99 of tiles)', fontsize=10, loc='left')
    a.set_ylabel('MPa')
    for a in ax[2:]:
        a.set_xticks(SIZES, [f'10$^{{{s}}}$' for s in SIZES]); a.set_xlabel('surface volume, m³'); a.grid(alpha=.15); a.legend(fontsize=8, frameon=False)
    fig.savefig(png, dpi=110)
    plt.close(fig)
    bands = [100, 200, 400, 800, 1600, 3200, 6400, 12800]
    tab = {}
    for s in SIZES:
        tab[f'v{s}'] = [None if np.isnan(v) else round(v) for v in env([R[(w, s)] for w, _, _ in WINDOWS], 90, bands=bands)]
    print(json.dumps(dict(sigma=bands, **tab)))


if __name__ == '__main__':
    main(*sys.argv[1:3])
