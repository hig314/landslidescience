"""LOSO blend with the 2026-09-26 guard additions switched on/off: old guards only, + edge feather,
+ plausibility (ceiling/floor), + both."""
import sys, os, json; os.chdir('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); sys.path.insert(0, 'model')
import numpy as np, v2, blend as BL
from scipy import ndimage
from sklearn.ensemble import HistGradientBoostingClassifier
data = {s: BL.features(s) for s in v2.SITES}
rng = np.random.default_rng(0)
fe, ce = BL.feather_edges, BL.ceiling
res = {}
for held in v2.SITES:
    Xs, ys = [], []
    for s in v2.SITES:
        if s == held: continue
        idx = np.flatnonzero(data[s]['y'] >= 0); idx = rng.choice(idx, min(300_000, len(idx)), replace=False)
        Xs.append(data[s]['X'][idx]); ys.append(data[s]['y'][idx])
    mdl = HistGradientBoostingClassifier(max_iter=300, max_leaf_nodes=31, l2_regularization=1.0).fit(np.vstack(Xs), np.concatenate(ys))
    D = data[held]; w0 = ndimage.uniform_filter(mdl.predict_proba(D['X'])[:, 1].reshape(D['shape']), 3)
    out = []
    for lab, F, C, bt, bo in [('old', 0, 0, 1.0, 1.0), ('both below0.5', 1, 1, 0.5, 1.0), ('both below1.0', 1, 1, 1.0, 1.0), ('both above-only', 1, 1, 1.0, 0.0)]:
        BL.BELOW_TOL, BL.BELOW_ON = bt, bo
        BL.feather_edges = fe if F else (lambda A, B, w: w)
        BL.ceiling = ce if C else (lambda D, w: w)
        w = BL.guard(D, w0.copy()); A, B = D['A'], D['B']
        Cb = np.where(np.isfinite(A) & np.isfinite(B), B + w*(A - B), np.where(np.isfinite(B), B, A))
        sc = v2.score(held, Cb)['ALL']; out.append(f"{lab} {sc['high']}/{sc['low']}"); res.setdefault(held, {})[lab] = sc
    print(f'{held:13s} ' + ' | '.join(out), flush=True)
json.dump(res, open('site/ablate_blend.json', 'w'), indent=1)
