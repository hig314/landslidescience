"""Per-point features that use ONLY KBay data (never Grewingk), so the model can run
anywhere in the survey.

SLOPE-AWARE LOW REFERENCE (the fix for the steep-slope holes): each cell's lowest point
is found in a frame DETRENDED by the local surface gradient (from the vendor TIN,
smoothed), and the neighbourhood minimum is taken by projecting each neighbour's lowest
point along that gradient to the target cell -- so on a 50 deg slope a neighbour 2 m
downslope does not read as 2.4 m 'lower ground'. h_low = height of a point above that
reference, evaluated at the point's own XY."""
import numpy as np, rasterio
from scipy import ndimage
d = dict(np.load('model/pts.npz'))
x, y, z = d['x'], d['y'], d['z']
X0, Y0, N = 603016.0, 6606264.0, 1500
ci = np.clip(((x - X0)).astype(int), 0, N-1); cj = np.clip(((Y0 - y)).astype(int), 0, N-1)
cid = cj * N + ci
cx = X0 + ci + 0.5; cy = Y0 - cj - 0.5
with rasterio.open('dtm/kbay_vendor.tif') as s:
    V = s.read(1).astype(np.float64); V[V == s.nodata] = np.nan
Vf = np.where(np.isnan(V), np.nanmedian(V), V)
Vs = ndimage.uniform_filter(Vf, 5)
gyr, gx = np.gradient(Vs); gy = -gyr                      # +y north
out = {}
# height above vendor TIN, bilinear at the point
row = (Y0 - y) - 0.5; col = (x - X0) - 0.5
out['h_vendor'] = z - ndimage.map_coordinates(Vf, [row, col], order=1, mode='nearest')
out['v_slope'] = np.degrees(np.arctan(np.hypot(gx, gy))).ravel()[cid]
out['v_curv'] = (Vs - ndimage.uniform_filter(Vs, 15)).ravel()[cid]
GX, GY = gx.ravel()[cid], gy.ravel()[cid]
def low_ref(win):
    # lowest point per cell in its own cell's detrended frame
    zd = z - (GX*(x - cx) + GY*(y - cy))
    order = np.lexsort((zd, cid)); first = np.r_[True, cid[order][1:] != cid[order][:-1]]
    lo = order[first]
    LX = np.full(N*N, np.nan); LY = LX.copy(); LZ = LX.copy()
    LX[cid[lo]] = x[lo]; LY[cid[lo]] = y[lo]; LZ[cid[lo]] = z[lo]
    LX, LY, LZ = [a.reshape(N, N) for a in (LX, LY, LZ)]
    ccx = X0 + np.arange(N)[None, :] + 0.5; ccy = Y0 - np.arange(N)[:, None] - 0.5
    ref = np.full((N, N), np.inf); r = win // 2
    for dj in range(-r, r+1):
        for di in range(-r, r+1):
            sx = np.roll(np.roll(LX, -dj, 0), -di, 1); sy = np.roll(np.roll(LY, -dj, 0), -di, 1); sz = np.roll(np.roll(LZ, -dj, 0), -di, 1)
            v = sz - (gx*(sx - ccx) + gy*(sy - ccy))          # neighbour's low point projected to this cell centre
            ref = np.fmin(ref, np.where(np.isnan(v), np.inf, v))
    ref[np.isinf(ref)] = np.nan
    return z - (ref.ravel()[cid] + GX*(x - cx) + GY*(y - cy))
out['h_low3'] = low_ref(3); out['h_low7'] = low_ref(7)
# column structure in the 3x3 m neighbourhood: point count, share near the low reference, canopy height
def nb_sum(a):
    return ndimage.uniform_filter(a.reshape(N, N), 3, mode='constant') * 9
cnt = np.bincount(cid, minlength=N*N).astype(float)
near = np.bincount(cid, weights=(out['h_low3'] < 0.3).astype(float), minlength=N*N)
top = np.full(N*N, -np.inf); np.maximum.at(top, cid, out['h_low3'])
top = ndimage.maximum_filter(np.where(np.isinf(top), 0, top).reshape(N, N), 3).ravel()
out['nb_count'] = nb_sum(cnt).ravel()[cid]
out['nb_near_frac'] = (nb_sum(near) / np.maximum(nb_sum(cnt), 1)).ravel()[cid]
out['canopy'] = top[cid]
out['is_last'] = (d['rn'] == d['nr']).astype(np.int8)
out['rn'] = d['rn']; out['nr'] = d['nr']; out['inten'] = d['inten']
out['vendor_ground'] = (d['cls'] == 2).astype(np.int8)
np.savez('model/feat.npz', **{k: np.asarray(v, dtype=np.float32) for k, v in out.items()})
for k, v in out.items(): print('%-14s p10 %8.2f  p50 %8.2f  p90 %8.2f' % (k, *np.nanpercentile(v, [10, 50, 90])))
