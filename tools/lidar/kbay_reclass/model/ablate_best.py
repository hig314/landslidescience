"""Best-stage ablation of the 2026-09-26 solver rules, six Grewingk sites."""
import sys, os; os.chdir('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); sys.path.insert(0, 'model')
import numpy as np, rasterio, v2, sitekit as K
from scipy import ndimage
for s in v2.SITES:
    d = f'site/{s}'; S = K.site_info(s); x0, x1, y0, y1 = S['bounds']; NX, NY = int(x1-x0), int(y1-y0)
    T, _ = K.rd(f'{d}/dtm_tinslope.tif'); Z0, _ = K.rd(f'{d}/dtm_s_e.tif')
    with rasterio.open(f'{d}/zone_s_e.tif') as src: zone = src.read(1).astype(bool)
    wgt = np.clip(1 - ndimage.distance_transform_edt(~zone)/3, 0, 1)
    P = np.load(f'{d}/pts.npz'); cls = P['cls']; land = ~np.isin(cls, (7, 18, 21, 22)) & (cls != 9)
    cid = np.clip((y1-P['y']).astype(int), 0, NY-1)*NX + np.clip((P['x']-x0).astype(int), 0, NX-1)
    nsing = np.bincount(cid[land], weights=(P['nr'][land] == 1).astype(float), minlength=NX*NY)
    ntot = np.bincount(cid[land], minlength=NX*NY).astype(float)
    single = ndimage.median_filter((nsing/np.maximum(ntot, 1)).reshape(NY, NX), 3)
    fl = K.return_floor(s)
    out = []
    for lab, floor, sing in [('none', 0, 0), ('floor', 1, 0), ('single', 0, 1), ('both', 1, 1)]:
        Z = Z0.copy()
        if floor: Z[Z < fl - K.FLOOR_TOL] = np.nan
        if sing: Z[(single >= 0.95) & (Z < T - 0.3)] = np.nan
        C = np.where(np.isfinite(Z), wgt*Z + (1-wgt)*T, T)
        sc = v2.score(s, C)['ALL']; out.append(f"{lab} {sc['high']}/{sc['low']}")
    print(f'{s:13s} ' + ' | '.join(out), flush=True)
