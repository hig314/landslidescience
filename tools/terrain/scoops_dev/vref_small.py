#!/opt/anaconda3/bin/python3
"""vref_small.py SITE K TAG...: vlevels runs against the small-class fine-lattice reference
(v<K>_finerec_<site>), on the reference's own grid and extent."""
import glob, sys, warnings
import numpy as np, rasterio
from rasterio.warp import reproject, Resampling
warnings.filterwarnings('ignore')
P = '/Volumes/Nunatak/terrain_build/scoops_state/multi_pilot/'
site, k, tags = sys.argv[1], sys.argv[2], sys.argv[3:]
f = [p for p in glob.glob(P + f'v{k}_finerec_{site}/blocks/*.tif') if '.tmp' not in p]
if not f:
    sys.exit(f'no v{k} reference for {site} yet')
r = rasterio.open(f[0]); rc, rv = r.read(1), r.read(2); m = rc > 0
print(f'{site} 1e{k} fine reference: needs {m.mean():.3f} max {rc.max():.1f}' + (f' volume dex 5/50/95 {np.percentile(np.log10(rv[m]), [5, 50, 95]).round(2)}' if m.any() else ''))
for t in tags:
    d = rasterio.open(P + f'vlev_{site}_{t}/block.tif'); c = np.zeros_like(rc)
    reproject(d.read(1), c, src_transform=d.transform, src_crs=d.crs, dst_transform=r.transform, dst_crs=r.crs, resampling=Resampling.nearest)
    # the two blocks come from different tilings and need not nest (2026-11-20: a third of the
    # Chena reference lay outside the prototype's block and was counted as blank): compare the overlap only
    inside = np.zeros_like(rc)
    reproject(np.ones(d.shape, np.float32), inside, src_transform=d.transform, src_crs=d.crs, dst_transform=r.transform, dst_crs=r.crs, resampling=Resampling.nearest)
    m = (rc > 0) & (inside > 0)
    c = np.where(inside > 0, c, 0)
    print(f'    overlap {np.mean(inside > 0):.2f} of the reference block; reference needs {m.sum() / max((inside > 0).sum(), 1):.3f} there')
    if not m.any():
        print(f'  {t}: reference blank; prototype needs {(c > 0).mean():.3f}'); continue
    q = c[m] / rc[m]
    print(f'  {t:5s}: needs {(c > 0).sum() / max((inside > 0).sum(), 1):.3f} max {c.max():.1f} (ref {rc[inside > 0].max():.1f}) | median {np.median(q):.3f}  >30% low {np.mean(q < .7):.3f}  blank {np.mean(c[m] <= 0):.3f}  >2% high {np.mean(q > 1.02):.3f}  extra cells {np.sum((c > 0) & ~(rc > 0)) / max((inside > 0).sum(), 1):.3f}')
