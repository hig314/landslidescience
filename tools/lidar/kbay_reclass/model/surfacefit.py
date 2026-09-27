"""Surface consistency per 1 m cell (Hig, 2026-09-25): open steep, curved ground puts every
return on ONE surface -- heights vary a lot across the cell but cluster tightly around a fitted
surface -- while canopy scatters returns through a volume.

Per 1 m cell, from all non-noise, non-water returns: least-squares PLANE z = a + b*u + c*v
(u, v relative to the cell centre; a 1 m patch of even strongly curved ground is close to planar):
  n        returns in the cell
  rms      RMS residual about the plane
  frac10   share of returns within 0.10 m of the plane
  single   share of single-return pulses (canopy splits pulses into several returns)
Moments are accumulated with np.bincount, so the whole site is vectorised.
"""
import os, sys
import numpy as np
EXCL = (7, 18, 21, 22)   # noise, high noise, SNOW, TEMPORAL EXCLUSION: never ground or surface (stress test 2026-09-26)
os.chdir('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); sys.path.insert(0, 'model'); import v2, sitekit as K


def cell_plane(S, use):
    x, y, z, cid = S.x[use], S.y[use], S.z[use], S.cid[use]
    NX, NY = S.NX, S.NY; N = NX * NY
    cx = S.x0 + (cid % NX) + 0.5; cy = S.y1 - (cid // NX) - 0.5
    u, v = x - cx, y - cy
    zc = np.bincount(cid, weights=z, minlength=N) / np.maximum(np.bincount(cid, minlength=N), 1)
    w = z - zc[cid]                                        # centre z for conditioning
    B = lambda a: np.bincount(cid, weights=a, minlength=N)
    n = np.bincount(cid, minlength=N).astype(float)
    Su, Sv, Suu, Suv, Svv = B(u), B(v), B(u*u), B(u*v), B(v*v)
    Sw, Suw, Svw = B(w), B(u*w), B(v*w)
    ATA = np.stack([np.stack([n, Su, Sv], -1), np.stack([Su, Suu, Suv], -1), np.stack([Sv, Suv, Svv], -1)], -2)
    ATb = np.stack([Sw, Suw, Svw], -1)
    ok = n >= 6
    coef = np.zeros((N, 3))
    coef[ok] = np.linalg.solve(ATA[ok] + np.eye(3) * 1e-9, ATb[ok][..., None])[..., 0]
    pred = zc[cid] + coef[cid, 0] + coef[cid, 1] * u + coef[cid, 2] * v
    r = z - pred
    rms = np.sqrt(B(r * r) / np.maximum(n, 1))
    frac10 = B((np.abs(r) <= 0.10).astype(float)) / np.maximum(n, 1)
    rms[~ok] = np.nan; frac10[~ok] = np.nan
    plane_z = zc + coef[:, 0]                              # plane value at the cell centre
    plane_z[~ok] = np.nan
    return dict(n=n.reshape(NY, NX), rms=rms.reshape(NY, NX), frac10=frac10.reshape(NY, NX),
                plane=plane_z.reshape(NY, NX)), r


def features(s):
    S = v2.Site(s)
    P = np.load(f'site/{s}/pts.npz'); keep = ~np.isin(P['cls'], EXCL)
    rn, nr = P['rn'][keep], P['nr'][keep]
    use = S.land
    f, r = cell_plane(S, use)
    N = S.NX * S.NY
    single = np.bincount(S.cid[use], weights=(nr[use] == 1).astype(float), minlength=N) / np.maximum(np.bincount(S.cid[use], minlength=N), 1)
    f['single'] = single.reshape(S.NY, S.NX)
    return S, f, r, use


if __name__ == '__main__':
    for s in ['patch1', 'alder', 'meadow_shrub', 'island']:
        S, f, r, use = features(s)
        d = f'site/{s}'; G, _ = K.rd(f'{d}/grewingk_dtm.tif'); M, _ = K.rd(f'{d}/kbay_max.tif'); GM, _ = K.rd(f'{d}/grewingk_max.tif'); F = v2.field(s)
        V, _ = K.rd(f'{d}/vendor_dtm.tif')
        from scipy import ndimage
        Gs = ndimage.uniform_filter(np.nan_to_num(G, nan=np.nanmedian(G)), 3); gy, gx = np.gradient(Gs); sl = np.degrees(np.arctan(np.hypot(gx, gy)))
        e = np.zeros_like(G, bool); e[30:-30, 30:-30] = True
        openc = e & (M - F - G < 0.3) & (GM - G < 0.3) & np.isfinite(f['rms'])
        brush = e & (M - V > 1.0) & np.isfinite(f['rms'])
        print(s)
        for lab, m in [('open, slope<30', openc & (sl < 30)), ('open, slope>=30', openc & (sl >= 30)), ('brush (KBay >1 m over ground)', brush)]:
            if m.sum() < 50: continue
            print('   %-30s n=%7d  rms p50 %.3f p90 %.3f | frac<=0.1 m p10 %.2f p50 %.2f | single-return share p50 %.2f'
                  % (lab, m.sum(), *np.percentile(f['rms'][m], [50, 90]), *np.percentile(f['frac10'][m], [10, 50]), np.median(f['single'][m])))
