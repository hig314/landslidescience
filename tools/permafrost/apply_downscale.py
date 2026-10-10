#!/opt/anaconda3/bin/python3
"""Apply the kernel fits (kernel_fit.py) at 60 m.

The 60 m field is anchored on the PUBLISHED 1 km value and moved by the
local lapse, so the product's own values are preserved and only the
within-cell terrain variation is added:

    Gruber:  MAAT60 = MAAT1k + b (elev60 - elev1k)
             PZI60  = curve(MAAT60)          (kfit/pzi_curve.json)
    Obu:     MAGT60 = MAGT1k + b (elev60 - elev1k) + c (north60 - north1k)
             prob60 = Phi(-MAGT60 / STD1k)
where every 1 km field (value, b, c, elev1k, north1k, STD) is interpolated
BILINEARLY to 60 m (smooth; the lapse fields are already smooth at the
kernel scale), MAGT1k / STD1k are the inpainted versions (Obu's ice and
near-ice gaps filled from the kernel fit), and the land mask is Gruber's
MAAT validity (defined over every glacier) at the NEAREST 1 km cell.

Outputs, Float32 nodata -9999, in permafrost_build/out/:
    gruber_maat60.tif gruber_pzi60.tif obu_magt60.tif obu_prob60.tif
"""
import json, subprocess
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window
from scipy.stats import norm

B = Path('/Volumes/Nunatak/permafrost_build')
SRC, KF, OUT, P = B / 'src', B / 'kfit', B / 'out', B / 'params60'
TE = ['-1041000', '443000', '1665000', '2622000']
GDAL = '/opt/homebrew/bin/'
ROWS = 1024


def warp60(src, dst, resamp='bilinear'):
    if dst.exists():
        return
    subprocess.run([GDAL + 'gdalwarp', '-q', '-overwrite', '-te', *TE, '-tr', '60', '60', '-tap',
                    '-r', resamp, '-ot', 'Float32', '-srcnodata', '-9999', '-dstnodata', '-9999',
                    '-co', 'COMPRESS=ZSTD', '-co', 'PREDICTOR=3', '-co', 'TILED=YES', '-co', 'BIGTIFF=YES',
                    str(src), str(dst)], check=True, env={'PATH': GDAL})


def pzi_curve(curve):
    T0, s, Tz = curve['T0'], curve['sigma'], curve['T_zero']
    p0 = norm.cdf((T0 - Tz) / s)

    def f(t):
        return np.clip((norm.cdf((T0 - t) / s) - p0) / (1 - p0), 0, 1)
    return f


def main():
    OUT.mkdir(exist_ok=True); P.mkdir(exist_ok=True)
    fields = {
        'maat1k': (SRC / 'gruber_maat_3338_1km.tif', 'bilinear'),
        'gb': (KF / 'gruber_b.tif', 'bilinear'),
        'elev1k': (SRC / 'elev_1km.tif', 'bilinear'),
        'north1k': (SRC / 'northness_1km.tif', 'bilinear'),
        'magt1k': (KF / 'obu_magt_filled.tif', 'bilinear'),
        'std1k': (KF / 'obu_std_filled.tif', 'bilinear'),
        'ob': (KF / 'obu_b.tif', 'bilinear'),
        'oc': (KF / 'obu_c.tif', 'bilinear'),
        'land': (SRC / 'gruber_maat_3338_1km.tif', 'near'),
    }
    for k, (src, r) in fields.items():
        warp60(src, P / f'k_{k}.tif', r)
    curve = pzi_curve(json.loads((KF / 'pzi_curve.json').read_text()))
    ds = {k: rasterio.open(P / f'k_{k}.tif') for k in fields}
    ds['elev'] = rasterio.open(B / 'elev60.tif'); ds['north'] = rasterio.open(B / 'northness60.tif')
    ref = ds['elev']
    prof = ref.profile.copy()
    prof.update(dtype='float32', nodata=-9999, compress='ZSTD', predictor=3, tiled=True, bigtiff='YES', count=1)
    outs = {n: rasterio.open(OUT / f'{n}.tif', 'w', **prof)
            for n in ('gruber_maat60', 'gruber_pzi60', 'obu_magt60', 'obu_prob60')}
    H, W = ref.height, ref.width
    for r0 in range(0, H, ROWS):
        win = Window(0, r0, W, min(ROWS, H - r0))
        g = {k: d.read(1, window=win).astype(np.float64) for k, d in ds.items()}
        for k in g:
            g[k][g[k] == -9999] = np.nan
        land = np.isfinite(g['land']) & np.isfinite(g['elev'])
        de = g['elev'] - g['elev1k']
        dn = g['north'] - g['north1k']
        maat = g['maat1k'] + g['gb'] * de
        pzi = curve(maat)
        magt = g['magt1k'] + g['ob'] * de + g['oc'] * dn
        std = np.where(g['std1k'] > 0.05, g['std1k'], 0.05)
        prob = norm.cdf(-magt / std)
        for n, arr in (('gruber_maat60', maat), ('gruber_pzi60', pzi), ('obu_magt60', magt), ('obu_prob60', prob)):
            a = arr.astype(np.float32); a[~land | ~np.isfinite(a)] = -9999
            outs[n].write(a, 1, window=win)
        if (r0 // ROWS) % 8 == 0:
            print(f'  rows {r0}/{H}', flush=True)
    for d in outs.values():
        d.close()
    print('done:', ', '.join(f'{n}.tif' for n in outs))


if __name__ == '__main__':
    main()
