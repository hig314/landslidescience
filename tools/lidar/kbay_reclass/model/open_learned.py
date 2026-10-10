"""Open-surface override, learned (Hig, 2026-09-25). Candidate cells: returns on one tight surface
(n >= 6, plane RMS <= 0.08 m, >= 80% within 0.10 m, >= 90% single returns). Among these, true open
ground (plane within 0.2 m of Grewingk + offset field) vs a herb/fern mat (plane > 0.35 m up) is
predicted leave-one-site-out from KBay-only cell features:
  rms, frac10, single, ctx7 (share of NON-candidate cells in 7x7 m: mats sit inside brush),
  ctx15, slope, curvature (both from the blend), plane - blend, n.
Measured: mats are tight but less so (RMS ~4.5 cm vs ~2.5 cm) and sit in brush (ctx7 ~0.6-0.7 vs
~0-0.3); intensity does not separate them. Override where P(ground) >= P_MIN: the returns within
0.15 m of those cell planes are ground, TIN, replacing the blend there, feathered over 2 m."""
import json, os, sys
import numpy as np
from scipy import ndimage
os.chdir('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); sys.path.insert(0, 'model'); import v2, sitekit as K, surfacefit as SF
import pyarrow as pa, pyarrow.feather as feather
from sklearn.ensemble import HistGradientBoostingClassifier
P_MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 0.8

data = {}
for s in v2.SITES:
    S, f, r, use = SF.features(s); d = f'site/{s}'
    cand = (f['n'] >= 6) & (f['rms'] <= 0.08) & (f['frac10'] >= 0.8) & (f['single'] >= 0.9)
    Bl, _ = K.rd(f'{d}/dtm_blend.tif'); Bf = np.where(np.isfinite(Bl), Bl, np.nanmedian(Bl))
    gy, gx = np.gradient(ndimage.uniform_filter(Bf, 3)); sl = np.degrees(np.arctan(np.hypot(gx, gy)))
    cu = Bf - ndimage.uniform_filter(Bf, 9)
    fe = dict(rms=f['rms'], frac10=f['frac10'], single=f['single'], n=f['n'],
              ctx7=1 - ndimage.uniform_filter(cand.astype(float), 7), ctx15=1 - ndimage.uniform_filter(cand.astype(float), 15),
              slope=sl, curv=cu, dplane=f['plane'] - Bf)
    names = sorted(fe); X = np.stack([np.nan_to_num(fe[k]) for k in names], -1).reshape(-1, len(names)).astype(np.float32)
    G, _ = K.rd(f'{d}/grewingk_dtm.tif'); F = v2.field(s); dz = f['plane'] - F - G
    e = np.zeros_like(G, bool); e[30:-30, 30:-30] = True
    y = np.full(G.shape, -1, np.int8); m = e & cand & np.isfinite(dz)
    y[m & (np.abs(dz) < 0.2)] = 1; y[m & (dz > 0.35)] = 0
    data[s] = dict(S=S, f=f, r=r, use=use, cand=cand, X=X, y=y.ravel(), Bl=Bl)
    print('features', s, flush=True)

rng = np.random.default_rng(0); res = {}
for held in v2.SITES:
    Xs, ys = [], []
    for s in v2.SITES:
        if s == held: continue
        idx = np.flatnonzero(data[s]['y'] >= 0); idx = rng.choice(idx, min(200_000, len(idx)), replace=False)
        Xs.append(data[s]['X'][idx]); ys.append(data[s]['y'][idx])
    mdl = HistGradientBoostingClassifier(max_iter=250, max_leaf_nodes=31, l2_regularization=1.0).fit(np.vstack(Xs), np.concatenate(ys))
    D = data[held]; S = D['S']; d = f'site/{held}'
    p = mdl.predict_proba(D['X'])[:, 1].reshape(S.NY, S.NX)
    openc = D['cand'] & (p >= P_MIN)
    idx = np.flatnonzero(D['use']); on = openc.ravel()[S.cid[idx]] & (np.abs(D['r']) <= 0.15); sel = idx[on]
    X_ = np.r_[S.x[sel], S.x[S.water]]; Y_ = np.r_[S.y[sel], S.y[S.water]]; Z_ = np.r_[S.z[sel], S.z[S.water]]
    fp = f'{d}/open.feather'
    feather.write_feather(pa.table({'X': X_, 'Y': Y_, 'Z': Z_, 'Classification': np.full(len(X_), 2, np.uint8)}), fp)
    K.tin({"type": "readers.arrow", "filename": fp}, f'{d}/dtm_open.tif', S.S, d, 'dtm_open'); os.remove(fp)
    O, _ = K.rd(f'{d}/dtm_open.tif'); Bl = D['Bl']
    wgt = np.clip(1 - ndimage.distance_transform_edt(~openc) / 2.0, 0, 1)
    C = np.where(np.isfinite(O), wgt * O + (1 - wgt) * Bl, Bl)
    v2.write_like(held, 'dtm_final2.tif', C); v2.write_like(held, 'open_mask.tif', openc.astype(float))
    G, _ = K.rd(f'{d}/grewingk_dtm.tif'); M, _ = K.rd(f'{d}/kbay_max.tif'); GM, _ = K.rd(f'{d}/grewingk_max.tif'); F = v2.field(held)
    Gs = ndimage.uniform_filter(np.nan_to_num(G, nan=np.nanmedian(G)), 3); gy, gx = np.gradient(Gs); slg = np.degrees(np.arctan(np.hypot(gx, gy)))
    e = np.zeros_like(G, bool); e[30:-30, 30:-30] = True
    ost = e & np.isfinite(G) & (M - F - G < 0.3) & (GM - G < 0.3) & (slg > 30)
    out = {}
    for n_, Dm in [('vendor', K.rd(f'{d}/vendor_dtm.tif')[0]), ('blend', Bl), ('final2', C)]:
        sc = v2.score(held, Dm); err = Dm - F - G; v = err[ost & np.isfinite(err)]
        out[n_] = dict(all=(sc['ALL']['high'], sc['ALL']['low']), open_steep=(round(100*float(np.mean(v > 0.5)), 1), round(100*float(np.mean(v < -0.5)), 1)) if v.size > 100 else None)
    out['override_cells_pct'] = round(100*float(openc[e].mean()), 1)
    res[held] = out
    print('%-13s override %5.1f%% | ALL hi/lo  vendor %s  blend %s  final2 %s | open steep  vendor %s  blend %s  final2 %s' % (
        held, out['override_cells_pct'], out['vendor']['all'], out['blend']['all'], out['final2']['all'],
        out['vendor']['open_steep'], out['blend']['open_steep'], out['final2']['open_steep']), flush=True)
json.dump(res, open(f'site/open_learned_{P_MIN}.json', 'w'), indent=1)
