"""Certainty-weighted ground surface -- no fabricated points.

    python model/surface.py [--zone-p 0.7] [--tau 0.15] [--cand-w 0.3] [--lam-in 2.0] [--tag name]

Solves a 1 m grid z minimising
    sum_i w_i(r_i) * r_i^2  +  lambda(x) * |second differences of z|^2
where r_i = z(x_i, y_i) - z_i with z(.) bilinear between cell centres.

Points (all real returns):
  strong   drop50 ground + WATER RETURNS (class 9 at the water surface,
           no flattening; class 9 standing above it is released to the ground model)
  low candidates  only inside zones: non-ground returns that are the local low
           (h_low3 < 0.3 m); low certainty (cand_w)
Weights:
  outside zones  strong points symmetric weight 1; lambda small  -> behaves like interpolation
  inside zones   ASYMMETRIC: a point ABOVE the surface counts tau (strong) or tau*cand_w
                 (candidate), a point BELOW counts 1 (strong) or cand_w (candidate) -- so sparse
                 low returns can pull the surface down through a thick layer of herb returns;
                 lambda larger -> smoother there. Points more than 1.5 m below the surface are
                 down-weighted (x0.1) so one stray low return cannot dig a pit.
Zones = cells with leave-one-site-out zone probability >= zone_p, dilated 2 m.
Iteratively reweighted (the side a point falls on changes as the surface moves).
"""
import argparse, json, sys, os
import numpy as np
EXCL = (7, 18, 21, 22)   # noise, high noise, SNOW, TEMPORAL EXCLUSION: never ground or surface (stress test 2026-09-26)
import scipy.sparse as sp
from scipy.sparse.linalg import cg
from scipy import ndimage
sys.path.insert(0, 'model'); import sitekit as K, loso as L


def bilinear_matrix(x, y, x0, y1, NX, NY):
    fx = np.clip(x - x0 - 0.5, 0, NX - 1.0001); fy = np.clip(y1 - y - 0.5, 0, NY - 1.0001)
    i0 = fx.astype(int); j0 = fy.astype(int); ax = fx - i0; ay = fy - j0
    rows = np.repeat(np.arange(len(x)), 4)
    cols = np.stack([j0*NX+i0, j0*NX+i0+1, (j0+1)*NX+i0, (j0+1)*NX+i0+1], 1).ravel()
    vals = np.stack([(1-ax)*(1-ay), ax*(1-ay), (1-ax)*ay, ax*ay], 1).ravel()
    return sp.csr_matrix((vals, (rows, cols)), shape=(len(x), NX*NY))


def second_diff(NX, NY, lam):
    """Rows penalising d2z/dx2, d2z/dy2 and the cross term, each scaled by sqrt(lambda) at the centre cell."""
    idx = np.arange(NX*NY).reshape(NY, NX); sl = np.sqrt(lam)
    blocks = []
    c = idx[:, 1:-1].ravel(); s = sl[:, 1:-1].ravel()
    blocks.append(sp.csr_matrix((np.stack([s, -2*s, s], 1).ravel(),
                                 (np.repeat(np.arange(len(c)), 3), np.stack([c-1, c, c+1], 1).ravel())), shape=(len(c), NX*NY)))
    c = idx[1:-1, :].ravel(); s = sl[1:-1, :].ravel()
    blocks.append(sp.csr_matrix((np.stack([s, -2*s, s], 1).ravel(),
                                 (np.repeat(np.arange(len(c)), 3), np.stack([c-NX, c, c+NX], 1).ravel())), shape=(len(c), NX*NY)))
    c = idx[:-1, :-1].ravel(); s = sl[:-1, :-1].ravel()*np.sqrt(2)
    blocks.append(sp.csr_matrix((np.stack([s, -s, -s, s], 1).ravel(),
                                 (np.repeat(np.arange(len(c)), 4), np.stack([c, c+1, c+NX, c+NX+1], 1).ravel())), shape=(len(c), NX*NY)))
    return sp.vstack(blocks).tocsr()


def solve_site(s, a):
    d = f'site/{s}'; S = K.site_info(s); x0, x1, y0, y1 = S['bounds']; NX, NY = int(x1-x0), int(y1-y0)
    P = np.load(f'{d}/pts.npz'); F = dict(np.load(f'{d}/feat.npz')); prob = np.load(f'{d}/prob.npy')
    x, y, z, cls = P['x'], P['y'], P['z'], P['cls']
    vend = F['vendor_ground'] == 1; noise = np.isin(cls, EXCL)
    cid = np.clip((y1-y).astype(int), 0, NY-1)*NX + np.clip((x-x0).astype(int), 0, NX-1)
    vcell = np.zeros(NX*NY, bool); vcell[cid[vend]] = True
    land = ~noise & (cls != 9)
    crest = land & ~vend & ~vcell[cid] & (F['h_vendor'] > 0.3) & (F['h_vendor'] < 3) & (F['h_low3'] < 0.15) & (prob >= 0.5)
    lvl_ok = cls == 9
    if lvl_ok.any():                                  # water returns: surface as seen; raised ones -> ground model
        wl = np.median(z[lvl_ok]); raised = lvl_ok & (z > wl + 0.15)
        # a river slopes: 'raised' is judged against the local water surface, not one level
        W = np.zeros(NX*NY); Wn = np.zeros(NX*NY); np.add.at(W, cid[lvl_ok], z[lvl_ok]); np.add.at(Wn, cid[lvl_ok], 1)
        Wl = ndimage.median_filter(np.where(Wn > 0, W/np.maximum(Wn, 1), np.nan).reshape(NY, NX), 15)
        Wl = np.where(np.isnan(Wl), wl, Wl).ravel()
        raised = lvl_ok & (z > Wl[cid] + 0.15)
        water = lvl_ok & ~raised
        released_g = raised & (prob >= 0.5)
    else:
        water = released_g = np.zeros(len(z), bool)
    strong = (vend & (prob >= 0.5) & land) | released_g | (cls == 20)        # crest fill removed 2026-09-25 (vegetation at the outwash)
    zp = np.zeros((NY, NX), np.float32)
    if a.zone_p < 1:
        import rasterio
        with rasterio.open(f'{d}/zone_prob.tif') as src: zp = src.read(1)
    zone = ndimage.binary_dilation(zp >= a.zone_p, iterations=a.dilate) if a.dilate > 0 else (zp >= a.zone_p)
    zc = zone.ravel()[cid]
    cand = land & ~strong & zc & (F['h_low3'] < 0.3)
    use = strong | water | cand
    ux, uy, uz = x[use], y[use], z[use]
    is_c = cand[use]; in_z = zc[use]; is_w = water[use]
    A = bilinear_matrix(ux, uy, x0, y1, NX, NY)
    lam = np.where(zone, a.lam_in, a.lam_out)
    D = second_diff(NX, NY, lam)
    DtD = (D.T @ D).tocsr()
    T, _ = K.rd(f'{d}/dtm_drop50.tif'); zg = np.where(np.isnan(T), np.nanmedian(T), T).ravel()
    base_w = np.where(is_c, a.cand_w, 1.0)
    for it in range(a.iters):
        r = A @ zg - uz                              # >0: point below the surface
        w = base_w.copy()
        above = r < 0
        w[in_z & above] *= a.tau                     # in zones, points above the surface count little
        w[in_z & (r > 1.5)] *= 0.1                   # far below: probably not ground, don't dig
        w[is_w] = 1.0
        Aw = A.multiply(w[:, None]).tocsr()
        M = (A.T @ Aw) + DtD
        b = Aw.T @ uz
        diag = M.diagonal(); diag[diag == 0] = 1.0
        zg, info = cg(M, b, x0=zg, rtol=1e-6, maxiter=600, M=sp.diags(1.0/diag))
        print(f'  {s} iter {it} cg={info}', flush=True)
    Z = zg.reshape(NY, NX).astype(np.float32)
    # no data far from any real return (same 30 m rule as the TIN)
    has = np.zeros(NX*NY, bool); has[cid[use]] = True
    far = ndimage.distance_transform_edt(~has.reshape(NY, NX)) > 15
    Z[far] = np.nan
    import rasterio
    with rasterio.open(f'{d}/dtm_drop50.tif') as src: prof = src.profile
    prof.update(nodata=-9999)
    with rasterio.open(f'{d}/dtm_{a.tag}.tif', 'w', **prof) as dst: dst.write(np.where(np.isnan(Z), -9999, Z), 1)
    with rasterio.open(f'{d}/zone_{a.tag}.tif', 'w', **dict(prof, dtype='uint8', nodata=None)) as dst: dst.write(zone.astype(np.uint8), 1)
    return dict(zone_pct=round(100*float(zone.mean()), 2), strong=int(strong.sum()), cand=int(cand.sum()), water=int(water.sum()))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--zone-p', type=float, default=0.7); ap.add_argument('--tau', type=float, default=0.15)
    ap.add_argument('--cand-w', type=float, default=0.3); ap.add_argument('--lam-in', type=float, default=2.0)
    ap.add_argument('--lam-out', type=float, default=0.005); ap.add_argument('--iters', type=int, default=5)
    ap.add_argument('--dilate', type=int, default=2); ap.add_argument('--tag', default='surf'); ap.add_argument('--sites', default=','.join(L.SITES))
    a = ap.parse_args()
    res = json.load(open('site/loso_results.json'))
    for s in a.sites.split(','):
        info = solve_site(s, a)
        off = json.load(open('site/offsets_strict.json'))[s]['median']
        res[s]['dtm'][a.tag] = L.score(f'site/{s}', a.tag, off); res[s].setdefault('surf_info', {})[a.tag] = info
        dd = res[s]['dtm'][a.tag]; b = res[s]['dtm']['drop50']
        print('%-13s zone %5.2f%%  ALL %4.1f/%4.1f (drop50 %4.1f/%4.1f)  canopy2-5 %s  canopy5-10 %s' % (
            s, info['zone_pct'], dd['ALL']['high'], dd['ALL']['low'], b['ALL']['high'], b['ALL']['low'],
            '%4.1f/%4.1f' % (dd['canopy2-5']['high'], dd['canopy2-5']['low']) if 'canopy2-5' in dd else '-',
            '%4.1f/%4.1f' % (dd['canopy5-10']['high'], dd['canopy5-10']['low']) if 'canopy5-10' in dd else '-'), flush=True)
    json.dump(res, open('site/loso_results.json', 'w'), indent=1)
