#!/opt/anaconda3/bin/python3
"""Long-wavelength versions of the spread rasters of landform.py (Hig, 2026-11-19:
the sharp cuts between one slope strip and the next are not the signal).

Each spread raster is averaged with a Gaussian of standard deviation SIGMA m,
counting only cells that have a value (so the sea, and ground with no crest or
floor, neither pull the mean nor get filled from far away: a cell is kept only
where at least MIN_W of the Gaussian's weight had data). Radius and F are
averaged as logarithms, i.e. a geometric mean; n, a plain mean.

    landform_blur.py DIR NAME [SIGMA=1500]  ->  NAME_<key>_blur<SIGMA>.tif (40 m), NAME_blur<SIGMA>.png
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.colors import LightSource, LogNorm
from rasterio.transform import Affine
from scipy.ndimage import gaussian_filter

d, name = Path(sys.argv[1]), sys.argv[2]
SIGMA = float(sys.argv[3]) if len(sys.argv) > 3 else 1500.0
F, MIN_W = 4, 0.25
KEYS = [('ridge_R500', True, 'radius of the ridges around, m (disc 500 m)', LogNorm(150, 700), 'turbo_r'),
        ('ridge_n500', False, 'n of the ridges around: 0 round, 1 edge, >1 cusp', plt.Normalize(.6, 1.8), 'turbo'),
        ('valley_F', True, 'F = S_v x W of the floors around, m', LogNorm(3, 60), 'turbo')]

dem = np.load(d / f'{name}_dem.npy')
shade = LightSource(315, 40).hillshade(dem[::F, ::F], dx=40, dy=40)
fig, axs = plt.subplots(1, 3, figsize=(30, 10.5))
for ax, (key, log, lab, norm, cm) in zip(axs, KEYS):
    with rasterio.open(d / f'{name}_{key}.tif') as t:
        a, prof = t.read(1), t.profile
    if key == 'valley_F':
        with rasterio.open(d / f'{name}_valley_ice.tif') as t:
            a = np.where(t.read(1) > 0.3, np.nan, a)          # glacier-floored reaches are another population: left out
        a = np.maximum(a, 0.1)
    if log:
        a = np.log(a)
    ny, nx = a.shape[0] // F * F, a.shape[1] // F * F
    blk = a[:ny, :nx].reshape(ny // F, F, nx // F, F)
    ok = np.isfinite(blk)
    cnt = ok.sum(axis=(1, 3))
    val = np.where(ok, blk, 0).sum(axis=(1, 3))
    s = SIGMA / (prof['transform'].a * F)
    w = gaussian_filter(cnt.astype(np.float64), s, mode='constant')
    v = gaussian_filter(val.astype(np.float64), s, mode='constant')
    full = gaussian_filter(np.full(cnt.shape, float(F * F)), s, mode='constant')
    with np.errstate(divide='ignore', invalid='ignore'):
        out = np.where((w / full >= MIN_W) & (dem[:ny:F, :nx:F] > 0.5), v / w, np.nan)
    if log:
        out = np.exp(out)
    prof.update(width=out.shape[1], height=out.shape[0], transform=prof['transform'] * Affine.scale(F), blockxsize=256, blockysize=256)
    with rasterio.open(d / f'{name}_{key}_blur{SIGMA:.0f}.tif', 'w', **prof) as t:
        t.write(out.astype(np.float32), 1)
    ext = [0, nx * 10 / 1000, ny * 10 / 1000, 0]
    im = ax.imshow(out, extent=ext, norm=norm, cmap=cm)
    ax.imshow(shade, cmap='gray', extent=ext, vmin=0, vmax=1, alpha=.4)
    plt.colorbar(im, ax=ax, shrink=.7, label=lab)
    ax.set_title(f'{name}: {lab}, Gaussian sd {SIGMA / 1000:g} km'); ax.set_xlabel('km'); ax.set_ylabel('km')
    print(key, 'p10/50/90', np.round(np.nanpercentile(out, [10, 50, 90]), 2))
fig.tight_layout(); fig.savefig(d / f'{name}_blur{SIGMA:.0f}.png', dpi=110)
