"""drop50 + targeted convexity fill, scored on held-out sites.
Promote a non-ground point only where the vendor TIN is cutting across a convexity:
its 1 m cell has no vendor ground, it stands 0.3-3 m above the vendor TIN, it is at the
slope-aware local low reference (h_low3 < 0.15 m), and the model gives p >= P_MIN."""
import json, sys
import numpy as np
EXCL = (7, 18, 21, 22)   # noise, high noise, SNOW, TEMPORAL EXCLUSION: never ground or surface (stress test 2026-09-26)
sys.path.insert(0, 'model'); import sitekit as K, loso as L
out = json.load(open('site/loso_results.json'))
for s in L.SITES:
    P, X, lab, names, vend = L.load(s); F = dict(np.load(f'site/{s}/feat.npz')); prob = np.load(f'site/{s}/prob.npy')
    S = K.site_info(s); x0, x1, y0, y1 = S['bounds']; NX, NY = int(x1-x0), int(y1-y0)
    cid = np.clip((y1-P['y']).astype(int), 0, NY-1)*NX + np.clip((P['x']-x0).astype(int), 0, NX-1)
    vcell = np.zeros(NX*NY, bool); vcell[cid[vend]] = True
    ok = ~np.isin(P['cls'], EXCL) & (P['cls'] != 9)
    base = vend & (prob >= 0.5) & ok
    off = json.load(open('site/offsets_strict.json'))[s]['median']; d = f'site/{s}'
    for pmin in (0.5, 0.7):
        n = f'drop50_crest{int(pmin*100)}'
        prom = ok & ~vend & ~vcell[cid] & (F['h_vendor'] > 0.3) & (F['h_vendor'] < 3) & (F['h_low3'] < 0.15) & (prob >= pmin)
        sel = base | prom
        L.dtm_from(P, sel, S, d, n); out[s]['dtm'][n] = L.score(d, n, off)
        out[s]['points'][n] = dict(false_ground=round(100*float(np.mean(lab[sel & (lab >= 0)] == 0)), 1),
                                   promoted=int(prom.sum()), promoted_correct=round(100*float(np.mean(lab[prom & (lab >= 0)] == 1)), 1),
                                   demoted=int(np.sum(vend & ~sel)))
    print(s, json.dumps({k: out[s]['points'][k] for k in out[s]['points'] if 'crest' in k}), flush=True)
json.dump(out, open('site/loso_results.json', 'w'), indent=1)
cols = ['ALL', 'canopy2-5', 'canopy5-10', 'crest', 'channel']
print('\n%-13s %-17s' % ('site', 'rule') + ''.join('%-12s' % c for c in cols))
for s in L.SITES:
    for v in ['vendor', 'drop50', 'drop50_crest50', 'drop50_crest70']:
        dd = out[s]['dtm'][v]
        print('%-13s %-17s' % (s if v == 'vendor' else '', v) + ''.join('%-12s' % ('%4.1f/%4.1f' % (dd[c]['high'], dd[c]['low']) if c in dd else '  -') for c in cols))
