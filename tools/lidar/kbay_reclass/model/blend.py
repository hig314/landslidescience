"""Grade between BEST (aggressive lump removal) and v2a (resolves real highs), cell by cell.

Hig (2026-09-25): v2a resolves real topographic highs (a ~12 m summit at 59.52435 -151.15522
that Grewingk shows and Vendor/Best truncate by 2.3 m) but let lone shrub tops through as
cones (fixed: isolated survivors are now tested); Best removes understory lumps and could be
more aggressive (-> drop threshold 0.9). Blend = B + w * (A - B), w = P(v2a is the closer
surface), learned leave-one-site-out from KBay-only cell features:
  d            A - B
  nA3, nB3     returns within 0.15 m of each surface, 3x3 m (ground is supported by many
               returns at the surface; a shrub top by few)
  spread5      height spread of the 5 lowest returns in the cell (ground clusters tightly:
               the summit 3 cm; the cone's shrub 0.9 m)
  alive5       share of v2a skeleton cells alive in 5x5 (a supported patch vs a lone point)
  brush, slope, curvA, curvB, roughA (A minus its 2 m smooth)
Target, cells where |A-B| > 0.25 m: A closer to Grewingk (+ offset field) than B.
w is smoothed over 3 m so the surface does not flicker between the two.
"""
import json, os, sys
import numpy as np
EXCL = (7, 18, 21, 22)   # noise, high noise, SNOW, TEMPORAL EXCLUSION: never ground or surface (stress test 2026-09-26)
from scipy import ndimage
os.chdir('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); sys.path.insert(0, 'model'); import v2, sitekit as K

B_TAG = os.environ.get('B_TAG', 'bestslope')
ALIVE_MIN = float(os.environ.get('ALIVE_MIN', '0.7'))
V2A = dict(cell=2.0, tol_up_dense=0.3, tol_up_open=0.6, keep_runs=0, tol_add_dense=-1, tol_add_open=-1, slope_k=0)


def features(s):
    S = v2.Site(s); none = np.zeros(len(S.z), bool)
    sel, sk, alive, idx, brush = S.run(**V2A)
    A = S.dtm(none, sk, idx, 'v2a_fix')
    Bs, _ = K.rd(f'site/{s}/dtm_{B_TAG}.tif')
    NX, NY = S.NX, S.NY
    row = (S.y1 - S.y) - 0.5; col = (S.x - S.x0) - 0.5
    fill = lambda a: np.where(np.isfinite(a), a, np.nanmedian(a))
    hA = S.z - ndimage.map_coordinates(fill(A), [row, col], order=1, mode='nearest')
    hB = S.z - ndimage.map_coordinates(fill(Bs), [row, col], order=1, mode='nearest')
    lnd = S.land
    cnt = lambda m: ndimage.uniform_filter(np.bincount(S.cid[m], minlength=NX*NY).reshape(NY, NX).astype(float), 3) * 9
    fe = {'d': A - Bs, 'nA3': cnt(lnd & (np.abs(hA) < 0.15)), 'nB3': cnt(lnd & (np.abs(hB) < 0.15)), 'brush': brush}
    # spread of the 5 lowest returns per 1 m cell
    order = np.lexsort((S.z, S.cid)); cs = S.cid[order]; zs = S.z[order]
    start = np.r_[0, np.flatnonzero(cs[1:] != cs[:-1]) + 1]
    end = np.r_[start[1:], len(cs)]
    k5 = np.minimum(start + 4, end - 1)
    sp = np.full(NX*NY, np.nan); sp[cs[start]] = zs[k5] - zs[start]
    fe['spread5'] = ndimage.generic_filter(sp.reshape(NY, NX), np.nanmedian, size=3, mode='nearest')
    m, n = sk['grid'].shape
    al = ndimage.uniform_filter(alive.astype(float), 5)
    have = ndimage.uniform_filter((sk['grid'] >= 0).astype(float), 5)
    fe['alive5'] = ndimage.zoom(al / np.maximum(have, 1e-6), (NY / m, NX / n), order=1)[:NY, :NX]
    Af, Bf = fill(A), fill(Bs)
    gy, gx = np.gradient(ndimage.uniform_filter(Bf, 5)); fe['slope'] = np.degrees(np.arctan(np.hypot(gx, gy)))
    fe['curvA'] = Af - ndimage.uniform_filter(Af, 15); fe['curvB'] = Bf - ndimage.uniform_filter(Bf, 15)
    fe['roughA'] = Af - ndimage.gaussian_filter(Af, 2)
    # Shape of what v2a keeps above its surroundings (Hig's tree, 59.58686 -151.17293: a 10 m
    # crown, 3-4 m up, walls on every side; his summit, 59.52435 -151.15522: 12 m, gentle sides).
    # base = grey opening with a 15 m disk (removes anything narrower); per protrusion blob:
    # area, steepness of its rim, peak height.
    disk = np.hypot(*np.mgrid[-7:8, -7:8]) <= 7.5
    base = ndimage.grey_opening(Af, footprint=disk)
    prot = Af - base; fe['prot'] = prot
    blob = prot > 0.75; lab, nb = ndimage.label(blob)
    area = np.zeros(NY * NX); edge = np.zeros(NY * NX); peak = np.zeros(NY * NX)
    if nb:
        gyA, gxA = np.gradient(Af); gA = np.degrees(np.arctan(np.hypot(gxA, gyA)))
        ring = ndimage.binary_dilation(blob, iterations=2) & ~blob
        ring_lab = ndimage.grey_dilation(lab, size=5) * ring
        idxs = np.arange(1, nb + 1)
        a_ = ndimage.sum(blob, lab, idxs); e_ = ndimage.mean(gA, ring_lab, idxs); p_ = ndimage.maximum(prot, lab, idxs)
        a_ = np.r_[0, a_]; e_ = np.r_[0, np.nan_to_num(e_)]; p_ = np.r_[0, p_]
        area = a_[lab]; edge = e_[lab]; peak = p_[lab]
    fe['blob_area'] = np.asarray(area).reshape(NY, NX); fe['blob_edge_slope'] = np.asarray(edge).reshape(NY, NX)
    fe['blob_peak'] = np.asarray(peak).reshape(NY, NX)
    names = sorted(fe); X = np.stack([np.nan_to_num(fe[k], nan=0.0) for k in names], -1).reshape(-1, len(names)).astype(np.float32)
    G, _ = K.rd(f'site/{s}/grewingk_dtm.tif'); Fo = v2.field(s)
    e = np.zeros((NY, NX), bool); e[30:-30, 30:-30] = True
    ok = e & np.isfinite(G) & np.isfinite(A) & np.isfinite(Bs) & ~K.flat_mask(G) & (np.abs(A - Bs) > 0.25)
    y = np.full((NY, NX), -1, np.int8)
    y[ok] = (np.abs(A - Fo - G) < np.abs(Bs - Fo - G))[ok]
    Pp = np.load(f'site/{s}/pts.npz'); nr_k = Pp['nr'][~np.isin(Pp['cls'], EXCL)]   # same order as Site's points
    nsing = np.bincount(S.cid[lnd], weights=(nr_k[lnd] == 1).astype(float), minlength=NX*NY)
    ntot = np.bincount(S.cid[lnd], minlength=NX*NY).astype(float)
    single = ndimage.median_filter((nsing / np.maximum(ntot, 1)).reshape(NY, NX), 3)
    single[(ntot.reshape(NY, NX)) < 6] = 0
    lo = np.full(NX*NY, np.inf); np.minimum.at(lo, S.cid, S.z); lo = lo.reshape(NY, NX); lo[np.isinf(lo)] = np.nan
    return dict(X=X, y=y.ravel(), A=A, B=Bs, shape=(NY, NX), names=names, single=single, lo=lo, slope=fe['slope'])


def guard(D, w):
    """Hard limits on how far v2a may move the surface away from Best.
    Raise > 0.5 m: only where the raised AREA is supported (mean alive5 >= ALIVE_MIN) -- the tree.
    Lower > 0.5 m: never in cells with no canopy layering (>= 95% single-return pulses) -- Hig's
    boulder pile (59.50074 -151.00479), where v2a pruned boulders as lumps, 2.1 m below every return."""
    al = D['X'][:, D['names'].index('alive5')].reshape(D['shape'])
    rz = (D['A'] - D['B']) > 0.5; labr, nr_ = ndimage.label(rz)
    if nr_:
        mean_al = np.r_[1.0, ndimage.mean(al, labr, np.arange(1, nr_ + 1))]
        w = np.where(ndimage.binary_dilation(rz & (mean_al[labr] < ALIVE_MIN), iterations=1), 0.0, w)
    lw = ((D['A'] - D['B']) < -0.5) & (D['single'] >= 0.95)
    w = np.where(ndimage.binary_dilation(lw, iterations=1), 0.0, w)
    w = ceiling(D, w)
    return feather_edges(D['A'], D['B'], w)


CEIL_M = 5.0
BELOW_TOL, BELOW_ON = 1.0, 1.0
MARGIN = 0.2
def ceiling(D, w):
    """Physical plausibility of each branch, per cell:
      above = height over the lowest return in the cell (ground cannot be above it -- that return
              would be underground), less 0.15 m + the rise over half a cell diagonal at the slope;
      below = depth under the lowest return within 5 m, less 0.5 m (truth at the six Grewingk sites
              is > 1 m under that in <= 0.23% of cells).
    Where one branch is implausible by MARGIN more than the other, the other wins, ramped over
    CEIL_M m so the rim of a corrected patch does not stay behind as a ridge. Snow patch,
    2026-09-26: the vendor TIN bridged ground-less snow holes 0.8-1.2 m above every return (and the
    single-return guard kept the bridge); v2a sat 2.4 m up on vegetation, or 1-2 m under the snow."""
    tol = 0.15 + 0.71 * np.tan(np.radians(np.clip(D['slope'], 0, 80)))
    lo = D['lo']; fl = ndimage.minimum_filter(np.where(np.isfinite(lo), lo, np.inf), 5); fl[np.isinf(fl)] = np.nan
    # 'above' only counts where the branch is UNSUPPORTED -- no return within 0.15 m of it in its
    # 3x3 cells (the snow bridge floats over everything). On a boulder pile the returns between
    # boulders are far below the boulder tops, and without this the rule cut the boulders away.
    nm = D['names']; nX = lambda k: D['X'][:, nm.index(k)].reshape(D['shape'])
    def bad(X, n3):
        with np.errstate(invalid='ignore'):
            b = (n3 < 1) * np.maximum(X - lo - tol, 0) + BELOW_ON * np.maximum(fl - BELOW_TOL - X, 0)
        return np.where(np.isfinite(b), b, 0.0)
    bA, bB = bad(D['A'], nX('nA3')), bad(D['B'], nX('nB3'))
    both = np.isfinite(D['A']) & np.isfinite(D['B'])
    toA = np.clip(1 - ndimage.distance_transform_edt(~(both & (bB > bA + MARGIN))) / CEIL_M, 0, 1)
    toB = np.clip(1 - ndimage.distance_transform_edt(~(both & (bA > bB + MARGIN))) / CEIL_M, 0, 1)
    w = np.maximum(w, toA)
    return np.minimum(w, 1 - toB)

EDGE_M = 5.0
def feather_edges(A, B, w):
    """Best (B) ends where the kept vendor ground ends -- its edge cells come from long, thin TIN
    triangles -- and beyond it the blend falls back to v2a (A) outright. That hard switch left
    ridges and spikes along the edge (Hig, snow patch 59.37384 -151.53443, 2026-09-26). Ramp w to 1
    (all v2a) over the last EDGE_M m of B's coverage; applied after the guards, which compare A with
    B and so cannot be trusted where B itself is not."""
    okB = np.isfinite(B)
    if okB.all() or not np.isfinite(A).any(): return w
    dist = ndimage.distance_transform_edt(okB)                      # 1 = edge cell of B's coverage
    ramp = np.clip(1 - (dist - 1) / EDGE_M, 0, 1)
    return np.where(np.isfinite(A), np.maximum(w, ramp), w)


if __name__ == '__main__':
    from sklearn.ensemble import HistGradientBoostingClassifier
    data = {}
    for s in v2.SITES:
        data[s] = features(s); print('features', s, flush=True)
    rng = np.random.default_rng(0); res = {}
    for held in v2.SITES:
        Xs, ys = [], []
        for s in v2.SITES:
            if s == held: continue
            idx = np.flatnonzero(data[s]['y'] >= 0); idx = rng.choice(idx, min(300_000, len(idx)), replace=False)
            Xs.append(data[s]['X'][idx]); ys.append(data[s]['y'][idx])
        mdl = HistGradientBoostingClassifier(max_iter=300, max_leaf_nodes=31, l2_regularization=1.0).fit(np.vstack(Xs), np.concatenate(ys))
        D = data[held]; p = mdl.predict_proba(D['X'])[:, 1].reshape(D['shape'])
        w = ndimage.uniform_filter(p, 3)
        w = guard(D, w)
        A, Bs = D['A'], D['B']
        C = np.where(np.isfinite(A) & np.isfinite(Bs), Bs + w * (A - Bs), np.where(np.isfinite(Bs), Bs, A))
        v2.write_like(held, 'dtm_blend.tif', C); v2.write_like(held, 'blend_w.tif', w)
        res[held] = {k: v2.score(held, X_) for k, X_ in [('vendor', K.rd(f'site/{held}/vendor_dtm.tif')[0]), ('best90', Bs), ('v2a_fix', A), ('blend', C)]}
        res[held]['w_gt_half_pct'] = round(100*float(np.mean(w[np.isfinite(C)] > 0.5)), 1)
        r = res[held]; f = lambda k: '%4.1f/%4.1f cr %4.1f/%4.1f' % (r[k]['ALL']['high'], r[k]['ALL']['low'], r[k]['crest']['high'], r[k]['crest']['low'])
        print('%-13s vendor %s | best90 %s | v2a_fix %s | BLEND %s | v2a-weighted cells %s%%' % (held, f('vendor'), f('best90'), f('v2a_fix'), f('blend'), r['w_gt_half_pct']), flush=True)
    json.dump(res, open('site/blend_results.json', 'w'), indent=1)
