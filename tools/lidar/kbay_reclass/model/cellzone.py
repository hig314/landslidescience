"""Cell-level 'dense-understory' zones: where is the edited (drop50) ground surface likely
>0.5 m too high? KBay-only features, leave-one-site-out. Output per site:
site/<s>/zone_prob.tif (P(too high)) and the cell features (cellfeat.npz).

Features per 1 m cell (all from KBay, never Grewingk):
  n_g3, n_g5        drop50 ground points in 3x3 / 5x5 m
  d_ground          distance (m) to the nearest cell holding a drop50 ground point
  low_gap3, low_gap5  how far the lowest nearby return sits BELOW the drop50 TIN
                    (TIN minus min(z) over 3x3 / 5x5 m; the TIN carries the slope, so this
                    is slope-aware -- the steep-slope trap is not repeated)
  can_max, can_p50  canopy: max / median height of returns above the TIN (3x3)
  near_frac         share of returns within 0.3 m of the TIN (3x3)
  g_prob            mean model ground-probability of the drop50 points in the cell (3x3)
  slope, curv       from the drop50 TIN
"""
import json, sys
import numpy as np
EXCL = (7, 18, 21, 22)   # noise, high noise, SNOW, TEMPORAL EXCLUSION: never ground or surface (stress test 2026-09-26)
from scipy import ndimage
sys.path.insert(0, 'model'); import sitekit as K, loso as L


def cell_features(s):
    d = f'site/{s}'; S = K.site_info(s); x0, x1, y0, y1 = S['bounds']; NX, NY = int(x1-x0), int(y1-y0)
    P = np.load(f'{d}/pts.npz'); F = dict(np.load(f'{d}/feat.npz')); prob = np.load(f'{d}/prob.npy')
    x, y, z, cls = P['x'], P['y'], P['z'], P['cls']
    vend = F['vendor_ground'] == 1; ok = ~np.isin(cls, EXCL) & (cls != 9)
    g = vend & (prob >= 0.5) & ok
    T, _ = K.rd(f'{d}/dtm_drop50.tif'); Tf = np.where(np.isnan(T), np.nanmedian(T), T)
    cid = np.clip((y1-y).astype(int), 0, NY-1)*NX + np.clip((x-x0).astype(int), 0, NX-1)
    h = z - ndimage.map_coordinates(Tf, [(y1-y)-0.5, (x-x0)-0.5], order=1, mode='nearest')   # height above TIN at the point
    fe = {}
    cnt_g = np.bincount(cid[g], minlength=NX*NY).reshape(NY, NX).astype(float)
    fe['n_g3'] = ndimage.uniform_filter(cnt_g, 3, mode='constant')*9
    fe['n_g5'] = ndimage.uniform_filter(cnt_g, 5, mode='constant')*25
    fe['d_ground'] = ndimage.distance_transform_edt(cnt_g == 0)
    hmin = np.full(NX*NY, np.inf); np.minimum.at(hmin, cid[ok], h[ok]); hmin = hmin.reshape(NY, NX)
    hmin[np.isinf(hmin)] = 0
    fe['low_gap3'] = -ndimage.minimum_filter(hmin, 3)
    fe['low_gap5'] = -ndimage.minimum_filter(hmin, 5)
    hmax = np.full(NX*NY, -np.inf); np.maximum.at(hmax, cid[ok], h[ok]); hmax = hmax.reshape(NY, NX); hmax[np.isinf(hmax)] = 0
    fe['can_max'] = ndimage.maximum_filter(hmax, 3)
    # median height above TIN per cell (approx: mean of returns within 0..40 m)
    sw = np.bincount(cid[ok], weights=np.clip(h[ok], 0, 40), minlength=NX*NY); cn = np.bincount(cid[ok], minlength=NX*NY)
    fe['can_p50'] = ndimage.uniform_filter((sw/np.maximum(cn, 1)).reshape(NY, NX), 3)
    near = np.bincount(cid[ok], weights=(np.abs(h[ok]) < 0.3).astype(float), minlength=NX*NY).reshape(NY, NX)
    fe['near_frac'] = ndimage.uniform_filter(near, 3)/np.maximum(ndimage.uniform_filter(cn.reshape(NY, NX).astype(float), 3), 1e-6)
    gp = np.bincount(cid[g], weights=prob[g], minlength=NX*NY).reshape(NY, NX)
    fe['g_prob'] = ndimage.uniform_filter(gp, 3)/np.maximum(ndimage.uniform_filter(cnt_g, 3), 1e-6)
    Ts = ndimage.uniform_filter(Tf, 5); gy, gx = np.gradient(Ts)
    fe['slope'] = np.degrees(np.arctan(np.hypot(gx, gy))); fe['curv'] = Ts - ndimage.uniform_filter(Ts, 15)
    names = sorted(fe); Xc = np.stack([fe[k] for k in names], -1).reshape(-1, len(names)).astype(np.float32)
    # target
    G, _ = K.rd(f'{d}/grewingk_dtm.tif'); err = T - K.offset_grid(d) - G
    e = np.zeros((NY, NX), bool); e[30:-30, 30:-30] = True
    valid = e & np.isfinite(err) & ~K.flat_mask(G) & np.isfinite(T)
    y_ = np.full((NY, NX), -1, np.int8); y_[valid & (err > 0.5)] = 1; y_[valid & (np.abs(err) < 0.25)] = 0
    np.savez(f'{d}/cellfeat.npz', X=Xc, y=y_.ravel(), names=np.array(names), shape=np.array([NY, NX]))
    return Xc, y_.ravel(), (NY, NX)


if __name__ == '__main__':
    from sklearn.ensemble import HistGradientBoostingClassifier
    import rasterio
    data = {s: cell_features(s) for s in L.SITES}
    rng = np.random.default_rng(0)
    for held in L.SITES:
        Xs, ys = [], []
        for s in L.SITES:
            if s == held: continue
            X, y, _ = data[s]; idx = np.flatnonzero(y >= 0); idx = rng.choice(idx, min(400_000, len(idx)), replace=False)
            Xs.append(X[idx]); ys.append(y[idx])
        m = HistGradientBoostingClassifier(max_iter=300, max_leaf_nodes=63, l2_regularization=1.0).fit(np.vstack(Xs), np.concatenate(ys))
        X, y, shp = data[held]; p = m.predict_proba(X)[:, 1].reshape(shp).astype(np.float32)
        with rasterio.open(f'site/{held}/dtm_drop50.tif') as src: prof = src.profile
        prof.update(nodata=None)
        with rasterio.open(f'site/{held}/zone_prob.tif', 'w', **prof) as dst: dst.write(p, 1)
        lab = y.reshape(shp)
        for t in (0.6, 0.7, 0.8):
            z = p >= t; L_ = lab >= 0
            prec = np.mean(lab[z & L_] == 1) if (z & L_).any() else float('nan')
            rec = np.sum(z & (lab == 1))/max(1, np.sum(lab == 1))
            print('%-13s P>=%.1f: zone %5.1f%% of cells, precision %4.0f%%, catches %4.0f%% of too-high cells'
                  % (held, t, 100*z.mean(), 100*prec, 100*rec), flush=True)
