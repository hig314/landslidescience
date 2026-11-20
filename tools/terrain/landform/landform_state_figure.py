#!/opt/anaconda3/bin/python3
"""Statewide overview of the blurred landform maps:  landform_state_figure.py STATE_DIR
Reads <key>_160m.tif (gdal_translate -r average -outsize 25% of the VRTs), writes state_overview.png."""
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LogNorm, Normalize
import rasterio
from rasterio.enums import Resampling

d = Path(sys.argv[1])
P = [('ridge_R500', 'ridge radius R, m (disc 500 m)', LogNorm(100, 1600), 'turbo_r'),
     ('ridge_n500', 'n = log2(R at 1 km / R at 500 m)', Normalize(.6, 2), 'turbo'),
     ('valley_F', 'F = S_v x W, m', LogNorm(.3, 100), 'turbo'),
     ('valley_W', 'floor width W, m', LogNorm(20, 3000), 'turbo'),
     ('valley_Sv', 'valley gradient S_v', LogNorm(.002, .5), 'turbo')]
fig, axs = plt.subplots(3, 2, figsize=(26, 22))
axs = axs.ravel()
for ax, (k, lab, norm, cm) in zip(axs, P):
    with rasterio.open(d / f'{k}_160m.tif') as ds:
        a = ds.read(1, out_shape=(ds.height // 5, ds.width // 5), resampling=Resampling.average)
    v = a[np.isfinite(a)]
    q = np.percentile(v, [1, 10, 50, 90, 99])
    print(k, 'cells %.2e' % v.size, 'p1/10/50/90/99', ' '.join('%.3g' % x for x in q))
    im = ax.imshow(np.ma.masked_invalid(a), norm=norm, cmap=cm, interpolation='nearest')
    ax.set_facecolor('.85'); ax.set_xticks([]); ax.set_yticks([])
    plt.colorbar(im, ax=ax, shrink=.75, label=lab)
    ax.set_title(f'{lab}; blur 1 km; median {q[2]:.3g}')
axs[-1].axis('off')
fig.tight_layout(); fig.savefig(d / 'state_overview.png', dpi=80); plt.close(fig)
