"""Edit-the-vendor rules, scored on held-out sites (probabilities from loso.py)."""
import json, sys, os
import numpy as np
EXCL = (7, 18, 21, 22)   # noise, high noise, SNOW, TEMPORAL EXCLUSION: never ground or surface (stress test 2026-09-26)
sys.path.insert(0, 'model'); import sitekit as K, loso as L
RULES = {'drop30': lambda v, p: v & (p >= 0.30),
         'drop50': lambda v, p: v & (p >= 0.50),
         'drop30_add90': lambda v, p: (v & (p >= 0.30)) | (~v & (p >= 0.90)),
         'drop50_add95': lambda v, p: (v & (p >= 0.50)) | (~v & (p >= 0.95))}
out = json.load(open('site/loso_results.json'))
for s in L.SITES:
    P, X, lab, _, vend = L.load(s); prob = np.load(f'site/{s}/prob.npy')
    ok = ~np.isin(P['cls'], EXCL); d = f'site/{s}'; S = K.site_info(s)
    off = json.load(open('site/offsets_strict.json'))[s]['median']
    for n, f in RULES.items():
        sel = f(vend, prob) & ok
        L.dtm_from(P, sel, S, d, n)
        out[s]['dtm'][n] = L.score(d, n, off)
        out[s]['points'][n] = dict(false_ground=round(100*float(np.mean(lab[sel & (lab >= 0)] == 0)), 1),
                                   true_ground_pts=int(np.sum(sel & (lab == 1))),
                                   promoted=int(np.sum(sel & ~vend)), demoted=int(np.sum(vend & ~sel)))
    print(s, 'done', flush=True)
json.dump(out, open('site/loso_results.json', 'w'), indent=1)
