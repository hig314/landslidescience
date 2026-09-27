"""Stronger false-ground trimming for BEST: drop vendor ground where the ground model's
probability is below DROP (0.5 was BEST), then the same zone-e surface on top."""
import json, sys, os
import numpy as np, rasterio
EXCL = (7, 18, 21, 22)   # noise, high noise, SNOW, TEMPORAL EXCLUSION: never ground or surface (stress test 2026-09-26)
from scipy import ndimage
os.chdir('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); sys.path.insert(0, 'model'); import sitekit as K, loso as L, v2, sweep
res = {}
for s in v2.SITES:
    d = f'site/{s}'; S = K.site_info(s); x0, x1, y0, y1 = S['bounds']; NX, NY = int(x1-x0), int(y1-y0)
    P, X, lab, names, vend = L.load(s); prob = np.load(f'{d}/prob.npy'); x, y, z, cls = P['x'], P['y'], P['z'], P['cls']
    cid = np.clip((y1-y).astype(int), 0, NY-1)*NX + np.clip((x-x0).astype(int), 0, NX-1)
    land = ~np.isin(cls, EXCL) & (cls != 9); water = cls == 9
    if water.any():
        W = np.zeros(NX*NY); Wn = np.zeros(NX*NY); np.add.at(W, cid[water], z[water]); np.add.at(Wn, cid[water], 1)
        Wl = ndimage.median_filter(np.where(Wn > 0, W/np.maximum(Wn, 1), np.nan).reshape(NY, NX), 15)
        Wl = np.where(np.isnan(Wl), np.median(z[water]), Wl).ravel()
        raised = water & (z > Wl[cid] + 0.15); wsurf = water & ~raised; rel = raised & (prob >= 0.5)
    else:
        wsurf = rel = np.zeros(len(z), bool)
    res[s] = {'vendor': v2.score(s, K.rd(f'{d}/vendor_dtm.tif')[0]), 'best50': v2.score(s, K.rd(f'{d}/dtm_comp_e.tif')[0])}
    T0 = K.rd(f'{d}/dtm_final_tin.tif')[0]; C0 = K.rd(f'{d}/dtm_comp_e.tif')[0]
    for dp in (0.8, 0.9):
        sel = (vend & (prob >= dp) & land) | rel | (cls == 20) | wsurf
        tag = f'tin{int(dp*100)}'; L.dtm_from(P, sel, S, d, tag); T, _ = K.rd(f'{d}/dtm_{tag}.tif')
        # same zone surface on top: replace the drop50 TIN by this one outside/at the zone edge
        with rasterio.open(f'{d}/zone_s_e.tif') as src: zone = src.read(1).astype(bool)
        Z, _ = K.rd(f'{d}/dtm_s_e.tif')
        wgt = np.clip(1 - ndimage.distance_transform_edt(~zone)/3, 0, 1)
        C = np.where(np.isfinite(Z), wgt*Z + (1-wgt)*T, T)
        v2.write_like(s, f'dtm_best{int(dp*100)}.tif', C)
        res[s][f'best{int(dp*100)}'] = v2.score(s, C)
        res[s][f'best{int(dp*100)}']['demoted_pct'] = round(100*float(np.mean(prob[vend] < dp)), 1)
    r = res[s]; f = lambda k: '%4.1f/%4.1f' % (r[k]['ALL']['high'], r[k]['ALL']['low'])
    print('%-13s vendor %s  best50 %s  best80 %s (drops %s%%)  best90 %s (drops %s%%)' % (s, f('vendor'), f('best50'), f("best80"), r["best80"]["demoted_pct"], f("best90"), r["best90"]["demoted_pct"]), flush=True)
json.dump(res, open("site/best_strength_hi.json", "w"), indent=1)
