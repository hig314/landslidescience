#!/opt/anaconda3/bin/python3
"""Figures and a table from the envelope runs: what strength the terrain requires, by size.

    envelope_figure.py DIR OUT_PREFIX

Across land tiles inside the coverage polygon, each tile standing for its most demanding
surface. c(phi) is the required cohesion on the Bishop top surface of each tile (0 where
none is needed); phi0 = atan(max tau/sigma) is the friction angle that needs no cohesion.
Writes <prefix>_curves.png, <prefix>_collapse.png, <prefix>_cloud.png, <prefix>.json.
"""
import json
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

GAMMA = 26.0
COLORS = ['#c6dbef', '#6baed6', '#2171b5', '#08519c', '#08306b']   # one hue, small to large


def load(d):
    runs = {}
    for p in sorted(Path(d).glob('*_v*_dx*.npz')):
        m = re.match(r'(.+)_v(\d+)_dx(\d+)$', p.stem)
        z = np.load(p)
        ok = (z['tile_land'] > .5) & z['tile_cov']
        runs[(m[1], int(m[2]))] = dict(z=z, ok=ok, r0=float(z['r0']), phis=z['phis'], n=int(ok.sum()))
    return runs


def curve(r, q):
    o = np.argsort(r['phis'])
    return r['phis'][o], np.percentile(r['z']['tile_top'][r['ok']][:, o, 0], q, axis=0)


def phi0(r, q):
    return float(np.degrees(np.arctan(np.percentile(r['z']['tile_ratio'][r['ok']], q))))


def main(d, prefix):
    runs = load(d)
    names = sorted({k[0] for k in runs})
    sizes = sorted({k[1] for k in runs})
    color = {s: COLORS[i + 5 - len(sizes)] for i, s in enumerate(sizes)}
    table = {}
    for (n, s), r in sorted(runs.items()):
        ph, c50 = curve(r, 50)
        _, c90 = curve(r, 90)
        _, c99 = curve(r, 99)
        table[f'{n}_v{s}'] = dict(tiles=r['n'], r0=round(r['r0']), phi0_p50=round(phi0(r, 50), 1), phi0_p90=round(phi0(r, 90), 1),
                                  phi0_p99=round(phi0(r, 99), 1), phis=[int(p) for p in ph], c_p50=[round(v) for v in c50],
                                  c_p90=[round(v) for v in c90], c_p99=[round(v) for v in c99])
    Path(prefix + '.json').write_text(json.dumps(table, indent=1))
    nc = max(len(names), 1)
    for kind, scale, ylab, name in (('curves', False, 'required cohesion, MPa', '_curves'), ('collapse', True, 'c / (γ r0)', '_collapse')):
        fig, ax = plt.subplots(2, nc, figsize=(3.3 * nc, 6.6), sharex=True, sharey='row', squeeze=False, constrained_layout=True)
        for j, n in enumerate(names):
            for i, q in enumerate((50, 99)):
                a = ax[i, j]
                for s in sizes:
                    if (n, s) not in runs:
                        continue
                    r = runs[(n, s)]
                    ph, c = curve(r, q)
                    a.plot(ph, c / (GAMMA * r['r0']) if scale else c / 1000, color=color[s], lw=2, marker='o', ms=3, label=f"10$^{{{s}}}$ m³ ({r['n']} tiles)")
                a.grid(alpha=.25)
                a.set_title(f'{n}, p{q} of tiles', fontsize=10, loc='left')
                if j == 0:
                    a.set_ylabel(ylab)
                if i == 1:
                    a.set_xlabel('friction angle φ, deg')
                a.legend(fontsize=7, frameon=False)
        fig.savefig(prefix + name + '.png', dpi=110)
        plt.close(fig)
    # the cloud: (sigma, tau) of all surfaces, with the tile-envelope per sigma bin
    ns = len(sizes)
    fig, ax = plt.subplots(nc, ns, figsize=(2.9 * ns, 2.8 * nc), squeeze=False, sharex=True, sharey=True, constrained_layout=True)
    lo, per = 1.0, 32.0
    e = 10 ** (lo + np.arange(129) / per)
    for j, n in enumerate(names):
        for i, s in enumerate(sizes):
            a = ax[j, i]
            a.set_xscale('log'); a.set_yscale('log'); a.set_xlim(30, 3e4); a.set_ylim(30, 3e4)
            a.set_title(f'{n} 10$^{{{s}}}$', fontsize=9, loc='left')
            if (n, s) not in runs:
                continue
            r = runs[(n, s)]
            h = r['z']['hist'].astype(float)
            a.pcolormesh(e, e, np.log10(np.where(h > 0, h, np.nan)).T, cmap='Blues', shading='flat', rasterized=True)
            tt = r['z']['tile_tau'][r['ok']]
            mid = np.sqrt(e[:-1] * e[1:])
            for q, col in ((50, '#555555'), (99, '#000000')):
                has = (tt > 0).sum(axis=0) >= 5
                v = np.array([np.percentile(tt[:, k][tt[:, k] > 0], q) if has[k] else np.nan for k in range(tt.shape[1])])
                a.plot(mid, v, color=col, lw=1.2)
            for ang in (30, 40, 50):
                a.plot([30, 3e4], [30 * np.tan(np.radians(ang)), 3e4 * np.tan(np.radians(ang))], color='#999999', lw=.6, ls=':')
    for a in ax[-1]:
        a.set_xlabel('σ, kPa')
    for a in ax[:, 0]:
        a.set_ylabel('τ, kPa')
    fig.savefig(prefix + '_cloud.png', dpi=100)
    plt.close(fig)
    print(json.dumps({k: {x: v[x] for x in ('tiles', 'phi0_p50', 'phi0_p90', 'phi0_p99')} for k, v in table.items()}))


if __name__ == '__main__':
    main(*sys.argv[1:3])
