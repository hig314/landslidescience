"""Build the two v2 variants on every site, score them against the offset field.
  v2c conservative: skeleton 2 m + quadratic lump test (0.15 dense / 0.5 open), flagged runs
      of >= 5 cells restored (ridges and knobs are runs; shrubs are 1-2 cells)
  v2a aggressive:   same, tolerances 0.3 / 0.6, NO restore -- removes brush blankets, but
      also cuts ridges; shown so the crest question can be judged by eye.
No densification (it added nothing), no crest fill; water returns are the water surface.
Settings were picked on alder + patch1 -- not held out; the other four sites are the test."""
import json, os, sys
import numpy as np
os.chdir('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); sys.path.insert(0, 'model'); import v2, sitekit as K
VAR = {'v2c': dict(cell=2.0, tol_up_dense=0.15, tol_up_open=0.5, keep_runs=5),
       'v2a': dict(cell=2.0, tol_up_dense=0.3, tol_up_open=0.6, keep_runs=0)}
out = {}
for s in v2.SITES:
    S = v2.Site(s); none = np.zeros(len(S.z), bool); V, _ = K.rd(f'site/{s}/vendor_dtm.tif')
    out[s] = {'vendor': v2.score(s, V)}
    for tag, kw in VAR.items():
        sel, sk, alive, idx, b = S.run(tol_add_dense=-1, tol_add_open=-1, slope_k=0, **kw)
        D = S.dtm(none, sk, idx, tag)
        sc = v2.score(s, D); sc['pruned_pct'] = round(100*float(((sk['grid'] >= 0) & ~alive).sum() / (sk['grid'] >= 0).sum()), 1)
        out[s][tag] = sc
    r = out[s]
    fmt = lambda k: '%4.1f/%4.1f cr %4.1f/%4.1f' % (r[k]['ALL']['high'], r[k]['ALL']['low'], r[k]['crest']['high'], r[k]['crest']['low'])
    print('%-13s vendor %s | v2c %s | v2a %s' % (s, fmt('vendor'), fmt('v2c'), fmt('v2a')), flush=True)
json.dump(out, open('site/v2_scores.json', 'w'), indent=1)
