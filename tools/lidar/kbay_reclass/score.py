"""Score KBay DTM candidates against Grewingk 2021 (offset-corrected) by canopy height."""
import sys, rasterio, numpy as np
OFF = 0.37                      # KBay - Grewingk on bare ground (README)
def rd(p):
    with rasterio.open(p) as s: a = s.read(1).astype(float); nd = s.nodata
    a[a == nd] = np.nan; return a
ref = rd('dtm/grewingk_2021.tif')
from rasterio.windows import from_bounds
with rasterio.open('grids/kbay_patch_max.tif') as s:   # 1501x1501, origin 1 m higher: read the DTM's window
    top = s.read(1, window=from_bounds(603016, 6604764, 604516, 6606264, s.transform)).astype(float)
top[top == -9999] = np.nan
canopy = top - OFF - ref         # KBay canopy height over Grewingk ground
m = 30; edge = np.zeros_like(ref, bool); edge[m:-m, m:-m] = True   # drop 30 m edge (no filter buffer)
bands = [(-9, 0.5), (0.5, 2), (2, 5), (5, 10), (10, 60)]
print('%-12s' % 'surface' + ''.join('  canopy %-9s' % f'{a if a>-9 else 0}-{b}m' for a, b in bands) + '   ALL')
for name in sys.argv[1:]:
    d = rd(f'dtm/{name}.tif') - OFF - ref
    row, allm = [], edge & np.isfinite(d) & np.isfinite(canopy)
    for a, b in bands:
        s = allm & (canopy >= a) & (canopy < b)
        row.append('%+.2f %4.1f/%4.1f' % (np.median(d[s]), 100*np.mean(d[s] > 0.5), 100*np.mean(d[s] < -0.5)))
    s = allm
    row.append('%+.2f %4.1f/%4.1f' % (np.median(d[s]), 100*np.mean(d[s] > 0.5), 100*np.mean(d[s] < -0.5)))
    print('%-12s' % name + ''.join('  %-18s' % r for r in row))
print('\neach cell: median dz, then %% of cells >0.5 m HIGH / >0.5 m LOW vs Grewingk (after -%.2f m offset); 30 m edge excluded' % OFF)
