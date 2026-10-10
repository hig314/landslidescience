"""Is there a sparse low return near the too-high cells? Uses dz (height above the
Grewingk surface AT each point's own XY), so slope does not bias the minimum."""
import json, sys
import numpy as np
EXCL = (7, 18, 21, 22)   # noise, high noise, SNOW, TEMPORAL EXCLUSION: never ground or surface (stress test 2026-09-26)
from scipy import ndimage
sys.path.insert(0, 'model'); import sitekit as K, loso as L
print('%-13s %8s  ' % ('site', 'n cells') + '  '.join('lowest return within %d m is <0.25 m above truth' % r if i == 0 else '%d m' % r for i, r in enumerate((1, 2, 3, 5))))
for s in L.SITES:
    d = f'site/{s}'; S = K.site_info(s); x0, x1, y0, y1 = S['bounds']; NX, NY = int(x1-x0), int(y1-y0)
    P = np.load(f'{d}/pts.npz'); dz = P['dz']; ok = np.isfinite(dz) & ~np.isin(P['cls'], EXCL) & (P['cls'] != 9)
    cid = np.clip((y1-P['y']).astype(int), 0, NY-1)*NX + np.clip((P['x']-x0).astype(int), 0, NX-1)
    cmin = np.full(NX*NY, np.inf); np.minimum.at(cmin, cid[ok], dz[ok]); cmin = cmin.reshape(NY, NX)
    off = json.load(open('site/offsets_strict.json'))[s]['median']
    G, _ = K.rd(f'{d}/grewingk_dtm.tif'); D, _ = K.rd(f'{d}/dtm_drop50.tif')
    e = np.zeros((NY, NX), bool); e[30:-30, 30:-30] = True
    bad = e & (D - off - G > 0.5) & ~K.flat_mask(G)
    row = []
    for r in (1, 2, 3, 5):
        fp = np.hypot(*np.mgrid[-r:r+1, -r:r+1]) <= r + 0.01
        nmin = ndimage.minimum_filter(cmin, footprint=fp, mode='nearest')
        row.append('%5.1f%%' % (100*np.mean(nmin[bad] < 0.25)))
    print('%-13s %8d  ' % (s, bad.sum()) + '      '.join(row))
