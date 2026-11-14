#!/opt/anaconda3/bin/python3
"""Depth maps of one paint_cases.py window: the winning surface alone against the
maximum over overlapping surfaces, with checks and numbers.

    paint_max_figure.py NEW.tif OLD.tif OUT.png     (OLD: same window before the change)
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
from scipy.ndimage import uniform_filter

H_EDGES = [1, 25, 50, 100, 200, 300, 450, 700, 1e5]
H_CMAP = ListedColormap(['#eff3ff', '#c6dbef', '#9ecae1', '#6baed6', '#4292c6', '#2171b5', '#08519c', '#08306b'])
H_NORM = BoundaryNorm(H_EDGES, H_CMAP.N)


def main(new, old, png):
    with rasterio.open(new) as d:
        b = {n: d.read(i + 1) for i, n in enumerate(d.descriptions)}
        dx = d.transform.a
    with rasterio.open(old) as d:
        o = {n: d.read(i + 1) for i, n in enumerate(d.descriptions)}
    dem, c = b['dem'], b['creq']
    land = dem > 0
    shade = LightSource(315, 45).hillshade(dem, dx=dx, dy=dx, vert_exag=1.5)
    pan = [('Depth at cell: top-c surface only (before)', b['h_a']),
           ('Depth at cell: max over winning surfaces', b['h_win']),
           ('Depth at cell: max over every surface with c > 0', b['h_any']),
           ('Deepest column: top-c surface only (before)', b['hmax_a']),
           ('Deepest column: max over winning surfaces', b['hmax_win']),
           ('Deepest column: max over every surface with c > 0', b['hmax_any'])]
    fig, ax = plt.subplots(2, 3, figsize=(15, 10.8), constrained_layout=True)
    ext = [0, dem.shape[1] * dx / 1000, 0, dem.shape[0] * dx / 1000]
    for a, (t, v) in zip(ax.ravel(), pan):
        a.imshow(shade, cmap='gray', vmin=0, vmax=1, extent=ext)
        a.imshow(np.ma.masked_where(~land | (v < 1), v), cmap=H_CMAP, norm=H_NORM, extent=ext, alpha=.9, interpolation='nearest')
        a.set_title(t, fontsize=11, loc='left'); a.set_xticks([]); a.set_yticks([])
    fig.legend(handles=[Patch(color=H_CMAP(i), label=f'{H_EDGES[i]:g}+') for i in range(H_CMAP.N)], title='metres',
               loc='outside lower center', ncol=8, fontsize=9)
    fig.suptitle(f'{Path(new).stem}: {dem.shape[1] * dx / 1000:.0f} km across, {dx:.0f} m grid', fontsize=13)
    fig.savefig(png, dpi=95); plt.close(fig)

    # checks: must all be zero / exact
    p0 = c > 0
    t = 1e-3
    out = dict(check=dict(
        creq_vs_before_max_diff=float(np.max(np.abs(c - o['creq']))),
        h_a_vs_before_max_diff=float(np.max(np.abs(b['h_a'] - o['h_a']))),
        painted_without_winner=int((p0 & (b['h_win'] <= 0)).sum()),
        h_win_below_h_a=int((p0 & (b['h_win'] < b['h_a'] - t)).sum()),
        h_any_below_h_win=int((b['h_any'] < b['h_win'] - t).sum()),
        hmax_win_below_hmax_a=int((p0 & (b['hmax_win'] < b['hmax_a'] - t)).sum()),
        hmax_any_below_hmax_win=int((b['hmax_any'] < b['hmax_win'] - t).sum())))
    p = land & (c >= 25)
    tpi = dem - uniform_filter(dem, 2 * int(round(3000 / dx)) + 1)
    for name in ('h_a', 'h_win', 'h_any', 'hmax_a', 'hmax_win', 'hmax_any'):
        v = b[name][p]
        q = np.percentile(v, [10, 50, 90, 99])
        top = p & (b[name] >= q[2])
        out[name] = dict(p10=round(float(q[0])), p50=round(float(q[1])), p90=round(float(q[2])), p99=round(float(q[3])),
                         tpi_top10=round(float(np.median(tpi[top]))), r_c=round(float(np.corrcoef(v, c[p])[0, 1]), 2))
    out['raised'] = dict(h_win_gt_h_a_pct=round(100 * float(np.mean(b['h_win'][p] > b['h_a'][p] * 1.01)), 1),
                         h_any_gt_h_win_pct=round(100 * float(np.mean(b['h_any'][p] > b['h_win'][p] * 1.01)), 1))
    out['tpi_all'] = round(float(np.median(tpi[p])))
    Path(png).with_suffix('.stats.json').write_text(json.dumps(out, indent=1))
    print(json.dumps(out))


if __name__ == '__main__':
    main(*sys.argv[1:4])
