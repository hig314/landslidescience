#!/opt/anaconda3/bin/python3
"""vcompare.py SITE TAG [TAG...]: a vproto run against the fine-lattice class references
(multi_pilot/v8_finerec_<site>, v9_finerec[_<site>]), per cell, on the prototype's grid."""
import glob, json, sys, warnings
import numpy as np, rasterio
from rasterio.warp import reproject, Resampling
warnings.filterwarnings('ignore')
P = '/Volumes/Nunatak/terrain_build/scoops_state/multi_pilot/'
site, tags = sys.argv[1], sys.argv[2:]
KIND = 'vproto'
if tags and tags[0] in ('vproto', 'vlev'):
    KIND, tags = tags[0], tags[1:]
for tag in tags:
    d = rasterio.open(P + f'{KIND}_{site}_{tag}/block.tif'); a = d.read(); c, vol = a[0], a[1]
    st = json.load(open(P + f'{KIND}_{site}_{tag}/stats.json'))
    def onto(path):
        s = rasterio.open(path); o = np.zeros_like(c)
        reproject(s.read(1), o, src_transform=s.transform, src_crs=s.crs, dst_transform=d.transform, dst_crs=d.crs, resampling=Resampling.nearest)
        return o
    refs = {}
    for k in (6, 7, 8, 9):
        f = [p for p in glob.glob(P + f'v{k}_finerec_{site}/blocks/*.tif') + (glob.glob(P + 'v9_finerec/blocks/*.tif') if (k == 9 and site == 'chugach') else []) if '.tmp' not in p]
        if f:
            refs[f'1e{k} fine'] = onto(f[0])
    print(f"{site} {tag}: cpu {st.get('cpu_search')} s, seeds {st['seeds']}, surfaces {st['surfaces']}, needs {np.mean(c > 0):.3f}, max {c.max():.0f}"
          + (f", volume dex 5/50/95 {np.percentile(np.log10(vol[c > 0]), [5, 50, 95]).round(2)}" if (c > 0).any() else ''))
    if len(refs) > 1:
        refs['best of all'] = np.max(list(refs.values()), axis=0)
    for n, r in refs.items():
        m = r > 0
        if n == 'best of both' or n.startswith('best'):
            pass
        if not m.any():
            print(f'   vs {n:13s}: reference blank; prototype cells needing c: {np.sum(c > 0)} (max {c.max():.0f})'); continue
        q = c[m] / r[m]
        print(f'   vs {n:13s}: ref needs {m.mean():.3f} max {r.max():.0f} | median {np.median(q):.3f}  >30% low {np.mean(q < .7):.3f}  blank {np.mean(c[m] <= 0):.3f}  >2% high {np.mean(q > 1.02):.3f}  extra cells {np.sum((c > 0) & ~m)}')
