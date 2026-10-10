"""Stage 1: which REAL points are ground. Spatial 3-fold CV on 500 m blocks."""
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
P = np.load('model/pts.npz'); F = dict(np.load('model/feat.npz'))
x, y, dz, sl = P['x'], P['y'], P['dz'], P['slope']
names = list(F); Xf = np.column_stack([F[k] for k in names])
tol = 0.20 + 0.25*np.tan(np.radians(np.minimum(sl, 70)))
at = np.abs(dz) <= tol; above = dz > 0.5 + tol
lab = np.where(at, 1, np.where(above, 0, -1))
bi = np.clip(((x - 603016)//500).astype(int), 0, 2); bj = np.clip(((6606264 - y)//500).astype(int), 0, 2)
fold = (bi + bj) % 3
inner = (x > 603046) & (x < 604486) & (y > 6604794) & (y < 6606234)
N = 1500; cid = (np.clip((6606264 - y).astype(int), 0, N-1))*N + np.clip((x - 603016).astype(int), 0, N-1)
vend = F['vendor_ground'] == 1
prob = np.zeros(len(x), np.float32)
rng = np.random.default_rng(0)
for k in range(3):
    tr = np.flatnonzero((fold != k) & (lab >= 0))
    tr = rng.choice(tr, min(3_000_000, len(tr)), replace=False)
    m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.1, max_leaf_nodes=63, l2_regularization=1.0)
    m.fit(Xf[tr], lab[tr])
    te = np.flatnonzero(fold == k); prob[te] = m.predict_proba(Xf[te])[:, 1]
    print('fold', k, 'trained on', len(tr), flush=True)
np.save('model/prob_cv.npy', prob)
def report(sel, name):
    s = sel & inner
    n = s.sum(); fg = above[s].mean()*100; tg = (at & s).sum()
    # cell level: mean dz of selected points per cell
    cnt = np.bincount(cid[s], minlength=N*N); sm = np.bincount(cid[s], weights=dz[s], minlength=N*N)
    cov = cnt > 0; e = sm[cov]/cnt[cov]
    print('%-22s pts %9d  falseG %5.1f%%  true-ground pts %9d  cells covered %5.1f%%  cells >0.5 m high %5.1f%%  >0.5 m low %4.1f%%'
          % (name, n, fg, tg, 100*cov.reshape(N,N)[30:-30,30:-30].mean(), 100*np.mean(e > 0.5), 100*np.mean(e < -0.5)))
report(vend, 'vendor ground')
for thr in (0.5, 0.7, 0.85, 0.95):
    report(prob >= thr, 'model p>=%.2f' % thr)
