"""Leave-one-site-out training and scoring of the stage-1 ground model.

    python model/loso.py            # train 6 models, write site/<s>/prob.npy, DTMs, scores

For each held-out site: train on the other five (equal-sized samples per site, so
the 57 M-point patch 1 does not dominate), predict every point of the held-out
site, then build TIN DTMs from (a) vendor ground and (b) model ground at a few
thresholds, and score both against Grewingk with that site's own offset.
Water/hydroflattened points carry no label (sitekit) and are never scored.
"""
import json, os, sys
import numpy as np
EXCL = (7, 18, 21, 22)   # noise, high noise, SNOW, TEMPORAL EXCLUSION: never ground or surface (stress test 2026-09-26)
import pyarrow as pa, pyarrow.feather as feather
from sklearn.ensemble import HistGradientBoostingClassifier
sys.path.insert(0, os.path.dirname(__file__))
import sitekit as K

SITES = ['patch1', 'forest_tall', 'alder', 'meadow_shrub', 'bare_gentle', 'island']
PER_SITE = 700_000
THRESH = (0.7, 0.85)


def load(s):
    P = np.load(f'site/{s}/pts.npz'); F = dict(np.load(f'site/{s}/feat.npz'))
    names = sorted(F)
    X = np.column_stack([F[k] for k in names])
    tol = 0.20 + 0.25 * np.tan(np.radians(np.minimum(P['slope'], 70)))
    dz = P['dz']
    lab = np.where(np.abs(dz) <= tol, 1, np.where(dz > 0.5 + tol, 0, -1))
    lab[~np.isfinite(dz)] = -1
    return P, X, lab, names, F['vendor_ground'] == 1


def dtm_from(P, sel, S, d, tag):
    t = pa.table({'X': P['x'][sel], 'Y': P['y'][sel], 'Z': P['z'][sel],
                  'Classification': np.full(int(sel.sum()), 2, np.uint8)})
    fp = f'{d}/ground_{tag}.feather'; feather.write_feather(t, fp)
    K.tin({"type": "readers.arrow", "filename": fp}, f'{d}/dtm_{tag}.tif', S, d, f'dtm_{tag}')
    os.remove(fp)


def score(d, tag, off):
    G, _ = K.rd(f'{d}/grewingk_dtm.tif'); D, _ = K.rd(f'{d}/dtm_{tag}.tif' if tag != 'vendor' else f'{d}/vendor_dtm.tif')
    M, _ = K.rd(f'{d}/kbay_max.tif')
    from scipy import ndimage
    e = np.zeros_like(G, bool); e[30:-30, 30:-30] = True
    ok = e & np.isfinite(G) & np.isfinite(D) & ~K.flat_mask(G)
    Fo = K.offset_grid(d)                     # offset field; the scalar `off` is no longer used
    can = M - Fo - G; err = D - Fo - G
    Gs = ndimage.uniform_filter(np.nan_to_num(G, nan=np.nanmedian(G)), 3)
    cu = Gs - ndimage.uniform_filter(Gs, 15)
    out = {}
    for lab, m in [('ALL', ok), ('canopy<0.5', ok & (can < 0.5)), ('canopy0.5-2', ok & (can >= 0.5) & (can < 2)),
                   ('canopy2-5', ok & (can >= 2) & (can < 5)), ('canopy5-10', ok & (can >= 5) & (can < 10)),
                   ('canopy>10', ok & (can >= 10)), ('crest', ok & (cu > 1)), ('channel', ok & (cu < -1))]:
        if m.sum() < 200:
            continue
        out[lab] = dict(n=int(m.sum()), high=round(100 * float(np.mean(err[m] > 0.5)), 1),
                        low=round(100 * float(np.mean(err[m] < -0.5)), 1), med=round(float(np.median(err[m])), 3))
    out['holes_pct'] = round(100 * float(np.mean(np.isnan(D[e]))), 2)
    return out


def main():
    data = {s: load(s) for s in SITES}
    names = data[SITES[0]][3]
    rng = np.random.default_rng(0)
    results = {}
    for held in SITES:
        Xs, ys = [], []
        for s in SITES:
            if s == held:
                continue
            P, X, lab, _, _ = data[s]
            idx = np.flatnonzero(lab >= 0)
            idx = rng.choice(idx, min(PER_SITE, len(idx)), replace=False)
            Xs.append(X[idx]); ys.append(lab[idx])
        m = HistGradientBoostingClassifier(max_iter=400, learning_rate=0.08, max_leaf_nodes=63, l2_regularization=1.0)
        m.fit(np.vstack(Xs), np.concatenate(ys))
        P, X, lab, _, vend = data[held]
        prob = m.predict_proba(X)[:, 1].astype(np.float32)
        d = f'site/{held}'; np.save(f'{d}/prob.npy', prob)
        S = K.site_info(held)
        strict = json.load(open('site/offsets_strict.json'))[held]['median']
        # point level (labelled points only)
        L = lab >= 0
        pt = {'vendor': dict(false_ground=round(100 * float(np.mean(lab[vend & L] == 0)), 1), true_ground_pts=int(np.sum(vend & (lab == 1))))}
        res = {'vendor': score(d, 'vendor', strict)}
        for t in THRESH:
            sel = prob >= t
            sel &= ~np.isin(P['cls'], EXCL)
            pt[f'p{t}'] = dict(false_ground=round(100 * float(np.mean(lab[sel & L] == 0)), 1), true_ground_pts=int(np.sum(sel & (lab == 1))))
            dtm_from(P, sel, S, d, f'p{int(t*100)}')
            res[f'p{t}'] = score(d, f'p{int(t*100)}', strict)
        results[held] = dict(points=pt, dtm=res)
        print(held, json.dumps(pt), flush=True)
    json.dump(results, open('site/loso_results.json', 'w'), indent=1)


if __name__ == '__main__':
    main()
