"""Roughness (crenulation) and accuracy of a surface in rock cells."""
import sys, os, numpy as np
from scipy import ndimage
os.chdir('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); sys.path.insert(0, 'model'); import sitekit as K, v2
def report(s, tag):
    d = f'site/{s}'; D = K.rd(f'{d}/dtm_final.tif')[0]; rm = K.rd(f'{d}/rock_mask.tif')[0] > 0.5
    Df = np.where(np.isfinite(D), D, np.nanmedian(D))
    lap = ndimage.laplace(Df)                                # curvature; crenulations are large |lap|
    e = np.zeros_like(rm); e[30:-30, 30:-30] = True; m = rm & e
    out = 'rock cells %6d | roughness |laplacian| p50 %.2f p90 %.2f m' % (m.sum(), np.median(np.abs(lap[m])), np.percentile(np.abs(lap[m]), 90))
    if s in v2.SITES:
        G = K.rd(f'{d}/grewingk_dtm.tif')[0] + v2.field(s); err = (D - G)[m & np.isfinite(G)]
        Gf = np.where(np.isfinite(G), G, np.nanmedian(G)); lg = ndimage.laplace(Gf)
        out += ' (Grewingk %.2f / %.2f) | vs truth within 0.5 m %.0f%%, median %+.2f' % (np.median(np.abs(lg[m])), np.percentile(np.abs(lg[m]), 90), 100*np.mean(np.abs(err) < 0.5), np.median(err))
    print('%-6s %-13s %s' % (tag, s, out), flush=True)
if __name__ == '__main__':
    for s in sys.argv[2:]: report(s, sys.argv[1])
