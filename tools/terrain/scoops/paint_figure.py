#!/opt/anaconda3/bin/python3
"""Figure and numbers for one paint_cases.py window.

    paint_figure.py DIR/<name>.tif DIR/<name>.png
All required-cohesion maps share color_creq.txt's classes; depths share their own.
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import BoundaryNorm, ListedColormap, LightSource
from matplotlib.patches import Patch
import numpy as np
import rasterio
from scipy.ndimage import distance_transform_edt as edt

HERE = Path(__file__).resolve().parent
rows = [l.split() for l in (HERE / 'color_creq.txt').read_text().splitlines() if l and l[0].isdigit()]
EDGES = [float(r[0]) for r in rows] + [1e6]
CMAP = ListedColormap([tuple(int(v) / 255 for v in r[1:4]) for r in rows])
NORM = BoundaryNorm(EDGES, CMAP.N)
E_NORM = BoundaryNorm([8 * e for e in EDGES], CMAP.N)     # same colours, eight times the values
H_EDGES = [1, 25, 50, 100, 200, 300, 450, 700, 1e5]          # m
H_CMAP = ListedColormap(['#eff3ff', '#c6dbef', '#9ecae1', '#6baed6', '#4292c6', '#2171b5', '#08519c', '#08306b'])
H_NORM = BoundaryNorm(H_EDGES, H_CMAP.N)


def main(path, png):
    with rasterio.open(path) as d:
        b = {n: d.read(i + 1) for i, n in enumerate(d.descriptions)}
        dx = d.transform.a
    dem = b['dem']; land = dem > 0
    gy, gx = np.gradient(dem, dx)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    shade = LightSource(315, 45).hillshade(dem, dx=dx, dy=dx, vert_exag=1.5)
    dist = edt(~(slope >= 15)) * dx
    flat = land & (slope < 3)
    steep = land & (slope >= 25)
    A = b['creq']
    pan = [('A. Now: highest c of any surface reaching the cell', A, 'c'),
           ('B. Only where the base dips with slip', b['gate'], 'c'),
           ('C. c · sin(base dip along slip)', b['csin'], 'c'),
           ('D. c · sin(ground fall along slip)', b['cground'], 'c'),
           ('E. Local demand of the column, classes × 8 (see key)', b['local'], 'e'),
           ('Depth at cell, surface winning A', b['h_a'], 'h'),
           ('Deepest column, surface winning A', b['hmax_a'], 'h'),
           ('Depth at cell, surface winning C', b['h_c'], 'h'),
           ('Deepest column, surface winning C', b['hmax_c'], 'h')]
    fig, ax = plt.subplots(3, 3, figsize=(15, 15.8), constrained_layout=True)
    ext = [0, dem.shape[1] * dx / 1000, 0, dem.shape[0] * dx / 1000]
    for a, (t, v, kind) in zip(ax.ravel(), pan):
        a.imshow(shade, cmap='gray', vmin=0, vmax=1, extent=ext)
        floor = 8 if kind == 'e' else 1
        m = np.ma.masked_where(~land | (v < floor), v)
        a.imshow(m, cmap=H_CMAP if kind == 'h' else CMAP, norm=dict(c=NORM, e=E_NORM, h=H_NORM)[kind], extent=ext, alpha=.9,
                 interpolation='nearest')
        a.set_title(t, fontsize=11, loc='left'); a.set_xticks([]); a.set_yticks([])
    h1 = [Patch(color=CMAP(i), label=f'{EDGES[i]:g}+') for i in range(CMAP.N)]
    h2 = [Patch(color=H_CMAP(i), label=f'{H_EDGES[i]:g}+') for i in range(H_CMAP.N)]
    fig.legend(handles=h1, title='kPa (A–D; E is 8× these)', loc='outside lower left', ncol=8, fontsize=9)
    fig.legend(handles=h2, title='metres (depths)', loc='outside lower right', ncol=8, fontsize=9)
    fig.suptitle(f'{Path(path).stem}: {dem.shape[1] * dx / 1000:.0f} km across, {dx:.0f} m grid', fontsize=13)
    fig.savefig(png, dpi=95); plt.close(fig)
    out = {}
    for t, v, kind in pan[:5]:
        k = t.split('.')[0]
        p = land & (v >= (200 if kind == 'e' else 25))
        out[k] = dict(painted_pct=round(100 * p.sum() / land.sum(), 1),
                      flat_share_pct=round(100 * (p & flat).sum() / max(p.sum(), 1), 1),
                      flat_far=int((p & flat & (dist > 800)).sum()),
                      steep_painted=int((p & steep).sum()),
                      steep_median=round(float(np.median(v[p & steep])), 0) if (p & steep).any() else 0,
                      p99=round(float(np.percentile(v[p], 99)), 0) if p.any() else 0)
    # do the depth maps pick out protrusions? share of each depth class by slope, and
    # the relief of the ground against its neighbourhood under the deepest surfaces
    from scipy.ndimage import uniform_filter
    r = int(round(3000 / dx))
    tpi = dem - uniform_filter(dem, 2 * r + 1)
    p = land & (A >= 25)
    hm = b['h_a']
    if p.any():
        q = np.percentile(hm[p], [50, 90])
        top = p & (hm >= q[1])
        out['depth'] = dict(h_a_p50=round(float(q[0])), h_a_p90=round(float(q[1])),
                            tpi_all=round(float(np.median(tpi[p]))), tpi_deepest=round(float(np.median(tpi[top]))),
                            corr_h_c=round(float(np.corrcoef(hm[p], A[p])[0, 1]), 2),
                            sina_lt0_pct=round(100 * float(np.mean(b['sina_a'][p] <= 0)), 1))
    Path(png).with_suffix('.stats.json').write_text(json.dumps(out, indent=1))
    print(json.dumps(out))


if __name__ == '__main__':
    main(*sys.argv[1:3])
