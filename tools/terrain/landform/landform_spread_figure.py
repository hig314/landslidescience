#!/opt/anaconda3/bin/python3
"""The spread rasters of landform.py over shaded relief:  landform_spread_figure.py DIR NAME -> NAME_spread.png"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.colors import LightSource, LogNorm

d, name = Path(sys.argv[1]), sys.argv[2]
dem = np.load(d / f'{name}_dem.npy')
ice = np.load(d / f'{name}_ice.npy')
f, dx = 4, 10
n = dem.shape[0]
shade = LightSource(315, 40).hillshade(dem[::f, ::f], dx=dx * f, dy=dx * f)
ext = [0, n * dx / 1000, n * dx / 1000, 0]
fig, axs = plt.subplots(1, 3, figsize=(30, 10.5))
for ax, (key, lab, norm, cm) in zip(axs, [
        ('ridge_R500', 'radius of the ridge above, m (disc 500 m)', LogNorm(80, 1500), 'turbo_r'),
        ('ridge_n500', 'n of the ridge above: 0 round, 1 edge, >1 cusp', plt.Normalize(0, 2), 'turbo'),
        ('valley_F', 'F = S_v x W of the floor below, m', LogNorm(.5, 100), 'turbo')]):
    with rasterio.open(d / f'{name}_{key}.tif') as t:
        a = t.read(1)[::f, ::f]
    if key == 'valley_F':
        a = np.where(a > 0, a, np.nan)
    im = ax.imshow(a, extent=ext, norm=norm, cmap=cm, interpolation='nearest')
    ax.imshow(shade, cmap='gray', extent=ext, vmin=0, vmax=1, alpha=.45)
    ax.contour(np.linspace(0, n * dx / 1000, ice[::f, ::f].shape[1]), np.linspace(0, n * dx / 1000, ice[::f, ::f].shape[0]),
               ice[::f, ::f].astype(float), levels=[.5], colors='w', linewidths=.6)
    plt.colorbar(im, ax=ax, shrink=.7, label=lab)
    ax.set_title(f'{name}: {lab}  (white line: glacier outline)'); ax.set_xlabel('km'); ax.set_ylabel('km')
fig.tight_layout(); fig.savefig(d / f'{name}_spread.png', dpi=110)
print('ok')
