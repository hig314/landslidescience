"""Closed pits: cells lower than ALL 8 neighbours by > 0.3 m (on non-steep ground next to steep)."""
import sys, os, numpy as np
from scipy import ndimage
os.chdir('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); sys.path.insert(0, 'model'); import sitekit as K
def pits(D):
    Df = np.where(np.isfinite(D), D, np.nanmax(D))
    lowest_nb = ndimage.minimum_filter(Df, footprint=np.array([[1,1,1],[1,0,1],[1,1,1]], bool), mode='nearest')
    return (Df < lowest_nb - 0.3)
if __name__ == '__main__':
    s = sys.argv[1]; d = f'site/{s}'
    for f in sys.argv[2:]:
        D = K.rd(f'{d}/{f}')[0]; p = pits(D); e = np.zeros_like(p); e[30:-30, 30:-30] = True
        print('%-28s closed pits (> 0.3 m below all 8 neighbours): %d' % (f, int((p & e).sum())))
