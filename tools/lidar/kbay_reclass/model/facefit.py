"""Face-frame surface fit for steep ground (Hig, 2026-09-26).

The flight-line check (flightlines.py) showed the returns on steep rock are GOOD: ~10 cm of scatter
across the face, with overlapping lines agreeing to ~3 cm. The cliff crenulations are made by
gridding: 10 cm across a 70-80 deg face is 30-60 cm vertically, and a surface through individual
returns sampled at 1 m jumps by that much from cell to cell. So average in the face's own frame first:

  per 1 m cell, neighbourhood = its 3x3 cells (~3 x 3 m of returns)
  plane      = PCA of those returns (normal = direction of least spread), i.e. minimising distance
               PERPENDICULAR to the face, not vertical distance
  robust     = one reweighting pass: returns more than 2.5 sigma (sigma = SIGMA_N, 0.10 m) off the
               plane get weight 0 (a shrub on a ledge, a stray return), then refit
  height     = where the vertical line through the cell centre meets the plane
  near-vertical (|normal_z| < 0.1, ~84 deg): the intersection is unstable -> neighbourhood median z
All sums are per-cell moments (np.bincount) convolved over 3x3, so the whole site is vectorised.
"""
import numpy as np
from scipy import ndimage

SIGMA_N = 0.10


def _moments(cid, x, y, z, w, N, shape):
    """Weighted per-cell sums, summed over each cell's 3x3 neighbourhood."""
    B = lambda a: ndimage.uniform_filter(np.bincount(cid, weights=a * w, minlength=N).reshape(shape), 3, mode='constant') * 9
    return dict(n=B(np.ones_like(x)), x=B(x), y=B(y), z=B(z), xx=B(x * x), yy=B(y * y), zz=B(z * z),
                xy=B(x * y), xz=B(x * z), yz=B(y * z))


def _planes(M):
    n = np.maximum(M['n'], 1e-9)
    mx, my, mz = M['x'] / n, M['y'] / n, M['z'] / n
    cxx = M['xx'] / n - mx * mx; cyy = M['yy'] / n - my * my; czz = M['zz'] / n - mz * mz
    cxy = M['xy'] / n - mx * my; cxz = M['xz'] / n - mx * mz; cyz = M['yz'] / n - my * mz
    C = np.stack([np.stack([cxx, cxy, cxz], -1), np.stack([cxy, cyy, cyz], -1), np.stack([cxz, cyz, czz], -1)], -2)
    w, V = np.linalg.eigh(C.reshape(-1, 3, 3))
    nrm = V[:, :, 0].reshape(C.shape[:-2] + (3,))
    nrm = np.where(nrm[..., 2:3] < 0, -nrm, nrm)
    return mx, my, mz, nrm


def face_fit(x, y, z, cid, grid, cells=None):
    """x, y, z: returns (coordinates relative to the grid origin recommended); cid: their 1 m cell
    index; grid = (x0, y1, NX, NY); cells: optional bool mask of cells to fit (others NaN).
    Returns (Z, normal_z) on the grid."""
    x0, y1, NX, NY = grid; N = NX * NY; shape = (NY, NX)
    xr, yr = x - x0, y - y1                                    # local coordinates: moments stay well conditioned
    w = np.ones_like(xr)
    for it in range(2):
        M = _moments(cid, xr, yr, z, w, N, shape)
        mx, my, mz, nrm = _planes(M)
        if it == 0:
            # residual of each return from ITS cell's plane, along the normal
            nc = nrm.reshape(-1, 3)[cid]
            r = (xr - mx.ravel()[cid]) * nc[:, 0] + (yr - my.ravel()[cid]) * nc[:, 1] + (z - mz.ravel()[cid]) * nc[:, 2]
            w = (np.abs(r) <= 2.5 * SIGMA_N).astype(float)
    cx = (np.arange(NX)[None, :] + 0.5) * np.ones((NY, 1)); cy = -(np.arange(NY)[:, None] + 0.5) * np.ones((1, NX))
    nz = nrm[..., 2]
    Z = mz - (nrm[..., 0] * (cx - mx) + nrm[..., 1] * (cy - my)) / np.where(np.abs(nz) < 1e-6, 1e-6, nz)
    # near-vertical: the vertical line barely meets the plane -> use the neighbourhood median height
    steep_v = np.abs(nz) < 0.1
    if steep_v.any():
        o = np.lexsort((z, cid)); cs = cid[o]; st = np.r_[0, np.flatnonzero(cs[1:] != cs[:-1]) + 1]; en = np.r_[st[1:], len(cs)]
        med = np.full(N, np.nan); med[cs[st]] = z[o][(st + en - 1) // 2]
        med = ndimage.generic_filter(med.reshape(shape), np.nanmedian, size=3, mode='nearest') if steep_v.sum() > 0 else med.reshape(shape)
        Z = np.where(steep_v, med, Z)
    Z[M['n'] < 12] = np.nan
    if cells is not None:
        Z = np.where(cells, Z, np.nan)
    return Z, nz

def face_fit_quad(x, y, z, cid, grid, cells, rad=1):
    """Quadratic in the face frame: w = a + b u + c v + d u^2 + e uv + f v^2, where (u, v) run along
    the face and w across it, in each cell's own frame (from the plane fit). Keeps curvature a plane
    flattens (a rounded lip, a buttress). Neighbourhood (2*rad+1)^2 cells. Height = the vertical
    line through the cell centre meets the quadric (root nearest the plane's intersection).
    Cells outside `cells`, with too few returns, or near-vertical fall back to NaN (caller keeps
    the plane / other surface)."""
    x0, y1, NX, NY = grid; N = NX * NY; shape = (NY, NX)
    xr, yr = x - x0, y - y1
    # frames from the (robust) plane fit
    w8 = np.ones_like(xr)
    M = _moments(cid, xr, yr, z, w8, N, shape); mx, my, mz, nrm = _planes(M)
    nc = nrm.reshape(-1, 3)[cid]
    r = (xr - mx.ravel()[cid]) * nc[:, 0] + (yr - my.ravel()[cid]) * nc[:, 1] + (z - mz.ravel()[cid]) * nc[:, 2]
    keep = np.abs(r) <= 3 * SIGMA_N
    xr, yr, z, cid = xr[keep], yr[keep], z[keep], cid[keep]
    # tangent basis e1 = normalise(k x n) (horizontal, along strike), e2 = n x e1 (up the face)
    n3 = nrm.reshape(-1, 3)
    e1 = np.stack([-n3[:, 1], n3[:, 0], np.zeros(N)], 1); l = np.linalg.norm(e1, axis=1, keepdims=True)
    e1 = np.where(l > 1e-6, e1 / np.maximum(l, 1e-6), np.array([[1.0, 0, 0]]))
    e2 = np.cross(n3, e1)
    MU = np.stack([mx.ravel(), my.ravel(), mz.ravel()], 1)
    tgt_ok = cells.ravel()
    ATA = np.zeros((N, 6, 6)); ATb = np.zeros((N, 6)); cnt = np.zeros(N)
    cj, ci = cid // NX, cid % NX
    for dj in range(-rad, rad + 1):
        for di in range(-rad, rad + 1):
            tj, ti = cj - dj, ci - di                        # the target cell whose neighbourhood holds this return
            ok = (tj >= 0) & (tj < NY) & (ti >= 0) & (ti < NX)
            t = np.where(ok, tj * NX + ti, 0); ok &= tgt_ok[t]
            if not ok.any(): continue
            t = t[ok]; P = np.stack([xr[ok], yr[ok], z[ok]], 1) - MU[t]
            u = np.einsum('ij,ij->i', P, e1[t]); v = np.einsum('ij,ij->i', P, e2[t]); w = np.einsum('ij,ij->i', P, n3[t])
            A = np.stack([np.ones_like(u), u, v, u * u, u * v, v * v], 1)
            for a in range(6):
                ATb[:, a] += np.bincount(t, weights=A[:, a] * w, minlength=N)
                for b in range(a, 6):
                    val = np.bincount(t, weights=A[:, a] * A[:, b], minlength=N)
                    ATA[:, a, b] += val
                    if b != a: ATA[:, b, a] += val
            cnt += np.bincount(t, minlength=N)
    ok = tgt_ok & (cnt >= 20) & (np.abs(n3[:, 2]) >= 0.1)
    coef = np.full((N, 6), np.nan)
    coef[ok] = np.linalg.solve(ATA[ok] + np.eye(6) * 1e-6, ATb[ok][..., None])[..., 0]
    # vertical line through the cell centre: p(t) = c + t k ; in frame: u = u0 + t k.e1, etc.
    cc = np.stack([(np.arange(N) % NX) + 0.5, -((np.arange(N) // NX) + 0.5), np.zeros(N)], 1)
    # start from the plane's intersection height
    z0 = MU[:, 2] - (n3[:, 0] * (cc[:, 0] - MU[:, 0]) + n3[:, 1] * (cc[:, 1] - MU[:, 1])) / np.where(np.abs(n3[:, 2]) < 1e-6, 1e-6, n3[:, 2])
    c0 = cc.copy(); c0[:, 2] = z0
    D0 = c0 - MU
    u0 = np.einsum('ij,ij->i', D0, e1); v0 = np.einsum('ij,ij->i', D0, e2); w0 = np.einsum('ij,ij->i', D0, n3)
    ku, kv, kw = e1[:, 2], e2[:, 2], n3[:, 2]
    a0, a1, a2, a3, a4, a5 = [coef[:, k] for k in range(6)]
    # w0 + t kw = a0 + a1 (u0 + t ku) + a2 (v0 + t kv) + a3 (u0 + t ku)^2 + a4 (u0 + t ku)(v0 + t kv) + a5 (v0 + t kv)^2
    A_ = a3 * ku * ku + a4 * ku * kv + a5 * kv * kv
    B_ = a1 * ku + a2 * kv + 2 * a3 * u0 * ku + a4 * (u0 * kv + v0 * ku) + 2 * a5 * v0 * kv - kw
    C_ = a0 + a1 * u0 + a2 * v0 + a3 * u0 * u0 + a4 * u0 * v0 + a5 * v0 * v0 - w0
    tt = np.full(N, np.nan)
    lin = np.abs(A_) < 1e-9
    tt[lin] = -C_[lin] / np.where(np.abs(B_[lin]) < 1e-12, np.nan, B_[lin])
    disc = B_ * B_ - 4 * A_ * C_
    q = ~lin & (disc >= 0)
    sq = np.sqrt(np.where(q, disc, 0))
    t1 = (-B_ + sq) / np.where(q, 2 * A_, 1); t2 = (-B_ - sq) / np.where(q, 2 * A_, 1)
    tt[q] = np.where(np.abs(t1[q]) < np.abs(t2[q]), t1[q], t2[q])
    Z = z0 + tt
    Z[~ok | (np.abs(tt) > 3)] = np.nan                      # no sensible intersection within 3 m of the plane's
    return Z.reshape(shape)
