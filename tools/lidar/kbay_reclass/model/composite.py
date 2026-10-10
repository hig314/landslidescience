"""Final-surface composite: TIN of true returns everywhere (edited ground + sparse crest fill +
water returns as the water surface, no flattening), replaced INSIDE zones by the certainty-
weighted solver surface, feathered over FEATHER m at the zone edge. Outside the zone map the
surface is untouched by the solver -- the zone map is exactly where modelling was used."""
import json, sys
import numpy as np
EXCL = (7, 18, 21, 22)   # noise, high noise, SNOW, TEMPORAL EXCLUSION: never ground or surface (stress test 2026-09-26)
from scipy import ndimage
import rasterio
sys.path.insert(0, 'model'); import sitekit as K, loso as L
FEATHER = 3
res = json.load(open('site/loso_results.json'))
for s in L.SITES:
    d = f'site/{s}'; S = K.site_info(s); x0, x1, y0, y1 = S['bounds']; NX, NY = int(x1-x0), int(y1-y0)
    P, X, lab, names, vend = L.load(s); F = dict(np.load(f'{d}/feat.npz')); prob = np.load(f'{d}/prob.npy')
    x, y, z, cls = P['x'], P['y'], P['z'], P['cls']
    cid = np.clip((y1-y).astype(int), 0, NY-1)*NX + np.clip((x-x0).astype(int), 0, NX-1)
    vcell = np.zeros(NX*NY, bool); vcell[cid[vend]] = True
    land = ~np.isin(cls, EXCL) & (cls != 9)
    crest = land & ~vend & ~vcell[cid] & (F['h_vendor'] > 0.3) & (F['h_vendor'] < 3) & (F['h_low3'] < 0.15) & (prob >= 0.5)
    water = cls == 9
    if water.any():
        W = np.zeros(NX*NY); Wn = np.zeros(NX*NY); np.add.at(W, cid[water], z[water]); np.add.at(Wn, cid[water], 1)
        Wl = ndimage.median_filter(np.where(Wn > 0, W/np.maximum(Wn, 1), np.nan).reshape(NY, NX), 15)
        Wl = np.where(np.isnan(Wl), np.median(z[water]), Wl).ravel()
        raised = water & (z > Wl[cid] + 0.15); water_surf = water & ~raised; rel = raised & (prob >= 0.5)
    else:
        water_surf = rel = np.zeros(len(z), bool)
    sel = (vend & (prob >= 0.5) & land) | rel | (cls == 20) | water_surf     # crest fill removed 2026-09-25
    L.dtm_from(P, sel, S, d, 'final_tin')
    off = json.load(open('site/offsets_strict.json'))[s]['median']
    res[s]['dtm']['final_tin'] = L.score(d, 'final_tin', off)
    T, _ = K.rd(f'{d}/dtm_final_tin.tif')
    for v in []:                     # old composites retired; sweep.py makes them now
        Z, _ = K.rd(f'{d}/dtm_{v}.tif')
        with rasterio.open(f'{d}/zone_{v}.tif') as src: zone = src.read(1).astype(bool)
        wgt = np.clip(1 - ndimage.distance_transform_edt(~zone)/FEATHER, 0, 1)      # 1 inside, fades out over FEATHER m
        C = np.where(np.isfinite(Z), wgt*Z + (1-wgt)*T, T)
        with rasterio.open(f'{d}/dtm_final_tin.tif') as src: prof = src.profile
        tag = f'comp_{v[2:]}'
        with rasterio.open(f'{d}/dtm_{tag}.tif', 'w', **prof) as dst: dst.write(np.where(np.isnan(C), prof['nodata'], C).astype('float32'), 1)
        res[s]['dtm'][tag] = L.score(d, tag, off)
    r = res[s]['dtm']
    print('%-13s vendor %4.1f/%4.1f  drop50 %4.1f/%4.1f  final_tin %4.1f/%4.1f' % (
        s, *[r[k]['ALL'][h] for k in ['vendor', 'drop50', 'final_tin'] for h in ('high', 'low')]), flush=True)
json.dump(res, open('site/loso_results.json', 'w'), indent=1)
