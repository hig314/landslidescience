#!/opt/anaconda3/bin/python3
"""Do the downscaled layers add skill? Compare against Pastick 2015 in Alaska.

Pastick (30 m probability of near-surface permafrost, field-trained,
independent of both 1 km products) is averaged onto the 60 m grid
(src/pastick60.tif, made by prep_pastick.sh). For each of:
  Gruber PZI 1 km (nearest to 60 m)  vs  Gruber PZI 60 m downscaled
  Obu prob   1 km (nearest to 60 m)  vs  Obu prob   60 m downscaled
report, over a random sample of cells where all are valid: Pearson r
with Pastick, mean absolute difference, and the same split by relief
(the 1 km cell's elevation range), because the downscale can only add
skill where there is relief to add it from.

PZI and the two probabilities are different quantities (zonation index,
permafrost probability, near-surface permafrost probability), so the
numbers answer "does the fine layer track Pastick's pattern better than
the coarse one", not "are they equal".
"""
import subprocess
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

B = Path('/Volumes/Nunatak/permafrost_build')
GDAL = '/opt/homebrew/bin/'
TE = ['-1041000', '443000', '1665000', '2622000']
N = 2_000_000


def warp(src, dst, resamp):
    if dst.exists():
        return
    subprocess.run([GDAL + 'gdalwarp', '-q', '-overwrite', '-te', *TE, '-tr', '60', '60', '-tap', '-r', resamp,
                    '-ot', 'Float32', '-srcnodata', '-9999', '-dstnodata', '-9999',
                    '-co', 'COMPRESS=ZSTD', '-co', 'PREDICTOR=3', '-co', 'TILED=YES', '-co', 'BIGTIFF=YES',
                    str(src), str(dst)], check=True, env={'PATH': GDAL})


def main():
    P = B / 'params60'
    warp(B / 'src' / 'gruber_pzi_3338_1km.tif', P / 'gruber_pzi_1km_at60.tif', 'near')
    warp(B / 'src' / 'obu_perprob_3338_1km.tif', P / 'obu_prob_1km_at60.tif', 'near')
    # relief of the 1 km cell: max - min of the 60 m elevation, via a 1 km
    # max and min warp, then back to 60 m nearest
    for r in ('max', 'min'):
        if not (P / f'elev_1km_{r}.tif').exists():
            subprocess.run([GDAL + 'gdalwarp', '-q', '-overwrite', '-te', *TE, '-tr', '1000', '1000', '-tap', '-r', r,
                            '-ot', 'Float32', '-srcnodata', '-9999', '-dstnodata', '-9999', '-co', 'COMPRESS=ZSTD',
                            str(B / 'elev60.tif'), str(P / f'elev_1km_{r}.tif')], check=True, env={'PATH': GDAL})
        warp(P / f'elev_1km_{r}.tif', P / f'elev_1km_{r}_at60.tif', 'near')
    names = {
        'pastick': B / 'src' / 'pastick60.tif',
        'gruber_1km': P / 'gruber_pzi_1km_at60.tif', 'gruber_60': B / 'out' / 'gruber_pzi60.tif',
        'obu_1km': P / 'obu_prob_1km_at60.tif', 'obu_60': B / 'out' / 'obu_prob60.tif',
        'emax': P / 'elev_1km_max_at60.tif', 'emin': P / 'elev_1km_min_at60.tif',
    }
    ds = {k: rasterio.open(v) for k, v in names.items()}
    ref = ds['pastick']
    rng = np.random.default_rng(3)
    # sample rows in bands so the reads stay windowed
    samp = {k: [] for k in names}
    H, W = ref.height, ref.width
    per_band = N // (H // 512)
    for r0 in range(0, H, 512):
        win = Window(0, r0, W, min(512, H - r0))
        pa = ds['pastick'].read(1, window=win)
        ok = np.argwhere(pa != -9999)
        if not len(ok):
            continue
        pick = ok[rng.choice(len(ok), min(per_band, len(ok)), replace=False)]
        for k, d in ds.items():
            a = d.read(1, window=win)
            samp[k].append(a[pick[:, 0], pick[:, 1]])
    s = {k: np.concatenate(v).astype(float) for k, v in samp.items()}
    for k in s:
        s[k][s[k] == -9999] = np.nan
    m = np.all([~np.isnan(s[k]) for k in s], axis=0)
    relief = s['emax'][m] - s['emin'][m]
    print(f'{m.sum():,} cells with every layer valid (Alaska, Pastick extent)')
    bands = [('all', relief >= 0), ('relief < 50 m', relief < 50), ('50-200 m', (relief >= 50) & (relief < 200)),
             ('200-500 m', (relief >= 200) & (relief < 500)), ('>= 500 m', relief >= 500)]
    print(f"{'relief band':14s} {'n':>9s}  {'Gruber 1km r':>12s} {'Gruber 60m r':>12s}  {'Obu 1km r':>10s} {'Obu 60m r':>10s}   {'MAD G1k':>7s} {'G60':>6s} {'O1k':>6s} {'O60':>6s}")
    for label, sel in bands:
        p = s['pastick'][m][sel]
        if len(p) < 1000:
            continue
        def r(k): return np.corrcoef(p, s[k][m][sel])[0, 1]
        def mad(k): return np.mean(np.abs(p - s[k][m][sel]))
        print(f'{label:14s} {len(p):9,d}  {r("gruber_1km"):12.3f} {r("gruber_60"):12.3f}  {r("obu_1km"):10.3f} {r("obu_60"):10.3f}   '
              f'{mad("gruber_1km"):7.3f} {mad("gruber_60"):6.3f} {mad("obu_1km"):6.3f} {mad("obu_60"):6.3f}')


if __name__ == '__main__':
    main()
