#!/opt/anaconda3/bin/python3
"""Figures for landform.py:  landform_figure.py DIR NAME
Writes NAME_ridges.png, NAME_valleys.png, NAME_valley_scatter.png into DIR."""
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LightSource, LogNorm

d, name = Path(sys.argv[1]), sys.argv[2]
q = np.load(d / f'{name}.npz')
dem = np.load(d / f'{name}_dem.npy')
ice = np.load(d / f'{name}_ice.npy')
dx, n = float(q['dx']), int(q['n'])
f = 4
dm = dem[:n // f * f, :n // f * f].reshape(n // f, f, n // f, f).mean(axis=(1, 3))
shade = LightSource(315, 40).hillshade(dm, dx=dx * f, dy=dx * f, vert_exag=1)
ext = [0, n * dx / 1000, n * dx / 1000, 0]
icem = np.ma.masked_equal(ice[::f, ::f], 0)


def base(ax):
    ax.imshow(shade, cmap='gray', extent=ext, vmin=0, vmax=1, alpha=.55)
    ax.imshow(icem, cmap='Blues', extent=ext, vmin=0, vmax=1.6, alpha=.45)
    ax.set_xlabel('km'); ax.set_ylabel('km')


# ridges: radius at two scales, and how the radius changes with scale
fig, axs = plt.subplots(1, 3, figsize=(30, 10.5))
for ax, (L, key, lab, norm, cm) in zip(axs, [
        (500, 'R', 'ridge radius R, m, disc 500 m', LogNorm(60, 2000), 'turbo_r'),
        (2000, 'R', 'ridge radius R, m, disc 2 km', LogNorm(250, 8000), 'turbo_r'),
        (1000, 'n', 'n = log2(R at 1 km / R at 500 m): 0 round, 1 straight flanks, >1 cusped', plt.Normalize(-.5, 2), 'turbo')]):
    base(ax)
    s = ax.scatter(q[f'r{L}_x'] / 1000, q[f'r{L}_y'] / 1000, c=q[f'r{L}_{key}'], s=1.2 if L < 1000 else 5, norm=norm, cmap=cm, lw=0)
    plt.colorbar(s, ax=ax, shrink=.7, label=lab)
    ax.set_title(f'{name}: {lab}')
fig.tight_layout(); fig.savefig(d / f'{name}_ridges.png', dpi=110); plt.close(fig)

x, y, A, Sv, W, opn, icef = (q[f'v_{k}'] for k in ('x', 'y', 'A', 'Sv', 'W', 'open', 'ice'))
F = np.maximum(Sv, 1e-4) * W
gl = icef > 0.3
walled = ~opn      # a wall was found on both sides; the rest are streams on open slopes or across a bigger floor
print('walled', walled.sum(), 'of', len(W))
fig, axs = plt.subplots(1, 3, figsize=(30, 10.5))
for ax, (val, lab, norm) in zip(axs, [(F, 'F = S_v x W, m (fall across one floor width)', LogNorm(.3, 100)),
                                      (W, 'floor width W, m', LogNorm(20, 3000)),
                                      (np.maximum(Sv, 1e-4), 'valley gradient S_v', LogNorm(.002, .5))]):
    base(ax)
    ax.scatter(x[~walled] / 1000, y[~walled] / 1000, c='.45', s=1, lw=0)
    o = np.argsort(val); o = o[walled[o]]
    s = ax.scatter(x[o] / 1000, y[o] / 1000, c=val[o], s=4, norm=norm, cmap='turbo', lw=0)
    plt.colorbar(s, ax=ax, shrink=.7, label=lab)
    ax.set_title(f'{name}: {lab}')
fig.tight_layout(); fig.savefig(d / f'{name}_valleys.png', dpi=110); plt.close(fig)

fig, axs = plt.subplots(1, 3, figsize=(24, 7.5))
land = ~gl & (Sv > 1e-4) & walled
gl = gl & walled
for ax, (xx, yy, xl, yl) in zip(axs, [(Sv, W, 'valley gradient S_v', 'floor width W, m'),
                                      (A, F, 'drainage area inside the window, km2', 'F = S_v x W, m'),
                                      (A, W, 'drainage area inside the window, km2', 'floor width W, m')]):
    ax.scatter(xx[land], yy[land], s=2, c='k', alpha=.25, lw=0, label='ice-free floor')
    ax.scatter(xx[gl], yy[gl], s=2, c='tab:blue', alpha=.4, lw=0, label='glacier on the floor')
    ax.set_xscale('log'); ax.set_yscale('log'); ax.set_xlabel(xl); ax.set_ylabel(yl); ax.legend(markerscale=5)
    ok = land & (yy > 0) & (xx > 0)
    if ok.sum() > 50:
        p = np.polyfit(np.log10(xx[ok]), np.log10(yy[ok]), 1)
        ax.set_title(f'{name}: ice-free slope of the cloud {p[0]:.2f}')
fig.tight_layout(); fig.savefig(d / f'{name}_valley_scatter.png', dpi=100); plt.close(fig)
print('ok')
