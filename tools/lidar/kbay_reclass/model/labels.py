"""Point-level teacher labels: dz = KBay z - (Grewingk surface + 0.37) at the point's
exact XY (bilinear on the 1 m TIN DTM -- a per-cell lookup errs by ~0.5*tan(slope) m).
Writes model/pts.npz with the columns the diagnostics and the model need."""
import numpy as np, pyarrow.feather as f, rasterio
from scipy import ndimage
OFF = 0.37
t = f.read_table('model/kbay_points.feather', columns=['xyz','Classification','ReturnNumber','NumberOfReturns','Intensity'])
xyz = np.asarray(t.column('xyz').combine_chunks().flatten()).reshape(-1, 3)
x, y, z = xyz[:,0], xyz[:,1], xyz[:,2]
with rasterio.open('dtm/grewingk_2021.tif') as s:
    G = s.read(1).astype(np.float64); G[G == s.nodata] = np.nan; T = s.transform
col = (x - T.c) / T.a - 0.5; row = (y - T.f) / T.e - 0.5          # fractional cell-centre coords
ref = ndimage.map_coordinates(np.nan_to_num(G, nan=-1e4), [row, col], order=1, mode='nearest')
ref[ref < -1000] = np.nan
# slope (deg) of the Grewingk surface, 3 m smoothing, sampled at the point
Gs = ndimage.uniform_filter(np.nan_to_num(G, nan=np.nanmedian(G)), 3)
gy, gx = np.gradient(Gs); slope = np.degrees(np.arctan(np.hypot(gx, gy)))
# curvature: Grewingk surface minus its 15 m mean (+ = convex / crest, - = concave / channel)
curv = Gs - ndimage.uniform_filter(Gs, 15)
S = ndimage.map_coordinates(slope, [row, col], order=1, mode='nearest')
C = ndimage.map_coordinates(curv, [row, col], order=1, mode='nearest')
np.savez('model/pts.npz', x=x, y=y, z=z, dz=(z - OFF - ref).astype(np.float32),
         cls=np.asarray(t.column('Classification')), rn=np.asarray(t.column('ReturnNumber')),
         nr=np.asarray(t.column('NumberOfReturns')), inten=np.asarray(t.column('Intensity')),
         slope=S.astype(np.float32), curv=C.astype(np.float32))
print('points', len(x), 'labelled', np.isfinite(ref).sum())
