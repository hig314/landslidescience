"""Stress-test diagnostics for patches without Grewingk (Hig, 2026-09-26): tides, snow, marsh.

For a site: class make-up and flights; where the final surface departs from the vendor's, which
classes support it (returns within 0.15 m of the final surface); water-level spread by flight and
how rough the final surface is over water; closed pits; and a review image.
"""
import json, os, sys, warnings
import numpy as np, pandas as pd, rasterio, pyarrow.feather as feather
from scipy import ndimage
warnings.filterwarnings('ignore')
os.chdir('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); sys.path.insert(0, 'model'); import sitekit as K, pits as PT
NAMES = {1: 'unclassified', 2: 'ground', 5: 'veg', 6: 'building', 7: 'noise', 9: 'water', 17: 'bridge', 18: 'high noise',
         20: 'ignored ground', 21: 'SNOW', 22: 'TEMPORAL EXCL'}


def run(s):
    d = f'site/{s}'; S = K.site_info(s); x0, x1, y0, y1 = S['bounds']; NX, NY = int(x1 - x0), int(y1 - y0)
    t = feather.read_table(f'{d}/kbay.feather', columns=['xyz', 'Classification', 'PointSourceId', 'GpsTime'])
    xyz = np.asarray(t.column('xyz').combine_chunks().flatten()).reshape(-1, 3)
    cls = np.asarray(t.column('Classification')); src = np.asarray(t.column('PointSourceId')); gt = np.asarray(t.column('GpsTime'))
    day = (pd.to_datetime(gt + 1e9, unit='s', origin=pd.Timestamp('1980-01-06')) - pd.Timedelta(seconds=18)).date
    x, y, z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    print(f'\n===== {s}: {len(z)/1e6:.1f} M returns')
    u, c = np.unique(cls, return_counts=True)
    print('  classes: ' + ', '.join(f'{NAMES.get(k, k)} {v/len(z)*100:.1f}%' for k, v in zip(u, c)))
    fl = pd.DataFrame({'src': src, 'day': day}).groupby('src')['day'].agg(lambda v: sorted(set(v)))
    print('  flight lines: ' + '; '.join(f'{k}: {", ".join(str(d_) for d_ in v)}' for k, v in fl.items()))
    V, _ = K.rd(f'{d}/vendor_dtm.tif'); F, _ = K.rd(f'{d}/dtm_final.tif')
    row = (y1 - y) - 0.5; col = (x - x0) - 0.5
    cid = np.clip(row.round().astype(int), 0, NY - 1) * NX + np.clip(col.round().astype(int), 0, NX - 1)
    Ff = np.where(np.isfinite(F), F, np.nanmedian(F))
    h = z - ndimage.map_coordinates(Ff, [row, col], order=1, mode='nearest')
    at = np.abs(h) <= 0.15
    dF = F - V
    e = np.zeros((NY, NX), bool); e[20:-20, 20:-20] = True
    for lab, m in [('final HIGHER than vendor by > 0.5 m', dF > 0.5), ('final LOWER than vendor by > 0.5 m', dF < -0.5)]:
        m = m & e
        print(f'  {lab}: {100*m.sum()/e.sum():.2f}% of cells')
        if m.sum() < 20: continue
        pm = m.ravel()[cid] & at
        uu, cc = np.unique(cls[pm], return_counts=True)
        print('     returns AT the final surface there, by class: ' + ', '.join(f'{NAMES.get(k, k)} {v/pm.sum()*100:.0f}%' for k, v in zip(uu, cc)))
    for k in (21, 22):
        if (cls == k).sum() == 0: continue
        onsurf = at & (cls == k)
        cells = np.zeros(NX * NY, bool); cells[cid[onsurf]] = True
        print(f'  {NAMES[k]}: {(cls==k).sum()/1e6:.2f} M returns; the FINAL surface passes through them in {cells.reshape(NY,NX)[e].mean()*100:.1f}% of cells '
              f'(vendor: {np.mean(np.abs((z - ndimage.map_coordinates(np.where(np.isfinite(V), V, np.nanmedian(V)), [row, col], order=1, mode="nearest"))[cls==k]) <= 0.15)*100:.1f}% of those returns at the vendor surface)')
    w = cls == 9
    if w.sum() > 1000:
        per = pd.DataFrame({'src': src[w], 'z': z[w]}).groupby('src')['z'].agg(['count', 'median'])
        print('  water level by flight line: ' + '; '.join(f'{k}: {r["median"]:.2f} m (n {r["count"]})' for k, r in per.iterrows() if r['count'] > 500))
        wc = np.zeros(NX * NY, bool); wc[cid[w]] = True; wc = wc.reshape(NY, NX) & e
        lap = np.abs(ndimage.laplace(Ff))
        print(f'  roughness over water cells (|laplacian| p90): final {np.percentile(lap[wc], 90):.3f} m, '
              f'vendor {np.percentile(np.abs(ndimage.laplace(np.where(np.isfinite(V), V, np.nanmedian(V))))[wc & np.isfinite(V)], 90) if (wc & np.isfinite(V)).any() else float("nan"):.3f} m')
    pf, pv = PT.pits(F) & e, PT.pits(V) & e
    print(f'  closed single-cell pits > 0.3 m: final {pf.sum()}, vendor {pv.sum()}')
    return dict(site=s)


if __name__ == '__main__':
    for s in sys.argv[1:]:
        run(s)
