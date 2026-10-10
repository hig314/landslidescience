"""No-hydroflattening repair on the island patch.
1. Lake level from the water returns themselves (median class-9 z).
2. Class-9 points more than 0.15 m above it are land returns the water polygon swallowed;
   they are released, and the ground model decides which are ground (p >= 0.5) --
   so island trees do not become ground. Class 20 (NV5 'ignored ground' at the water's
   edge) is ground again.
3. Ground = drop50 (vendor ground the model does not doubt) + released ground + class 20.
   TIN with max edge 30 m: open water with no land returns stays NODATA (no flattening).
4. A 'reflattened' variant fills that nodata with the measured lake level, but only
   where water returns exist nearby -- so the shoreline follows the points, not NV5's polygon."""
import json, sys
import numpy as np
sys.path.insert(0, 'model'); import sitekit as K, loso as L
s = 'island'; d = f'site/{s}'; S = K.site_info(s)
P, X, lab, names, vend = L.load(s); prob = np.load(f'{d}/prob.npy')
cls, z = P['cls'], P['z']
lvl = float(np.median(z[cls == 9]))
released = (cls == 9) & (z > lvl + 0.15)
rel_ground = released & (prob >= 0.5)
sel = (vend & (prob >= 0.5)) | rel_ground | (cls == 20)
print('lake level %.2f m; class-9 released %d, of which ground %d; class 20 restored %d' % (lvl, released.sum(), rel_ground.sum(), (cls == 20).sum()))
L.dtm_from(P, sel, S, d, 'hydro_noflat')
# reflattened: lake level where there are water returns within 5 m and no land surface
import rasterio
from scipy import ndimage
x0, x1, y0, y1 = S['bounds']; NX, NY = int(x1-x0), int(y1-y0)
wat = (cls == 9) & ~released
W = np.zeros((NY, NX), bool)
W[np.clip((y1-P['y'][wat]).astype(int), 0, NY-1), np.clip((P['x'][wat]-x0).astype(int), 0, NX-1)] = True
with rasterio.open(f'{d}/dtm_hydro_noflat.tif') as src:
    A = src.read(1); prof = src.profile; nd = src.nodata
empty = (A == nd) | np.isnan(A)
# the lake = every connected region of empty cells or at-lake-level water returns that
# contains a water return; land (anything the TIN covered) bounds it
comp, n = ndimage.label(empty | W)
keep = np.unique(comp[W]); keep = keep[keep > 0]
lake = np.isin(comp, keep) & (empty | (A < lvl))
B = np.where(lake, np.float32(lvl), A)
with rasterio.open(f'{d}/dtm_hydro_reflat.tif', 'w', **prof) as dst: dst.write(B, 1)
# island check against Grewingk (which kept the island): cells in the island cluster
G, _ = K.rd(f'{d}/grewingk_dtm.tif'); V, _ = K.rd(f'{d}/vendor_dtm.tif'); N, _ = K.rd(f'{d}/dtm_hydro_noflat.tif')
off = json.load(open('site/offsets_strict.json'))[s]['median']
isl = np.zeros((NY, NX), bool)
isl[np.clip((y1-P['y'][rel_ground]).astype(int), 0, NY-1), np.clip((P['x'][rel_ground]-x0).astype(int), 0, NX-1)] = True
isl = ndimage.binary_closing(isl, iterations=2)
for n, D in [('vendor TIN (class 2 only)', V), ('no-flatten repair', N)]:
    dd = (D - off - G)[isl & np.isfinite(G)]
    print('%-26s island cells %5d: covered %5.1f%%  median vs Grewingk %+.2f  |err|<0.5 m %5.1f%%' % (n, isl.sum(), 100*np.mean(np.isfinite(dd)), np.nanmedian(dd), 100*np.nanmean(np.abs(dd) < 0.5)))
