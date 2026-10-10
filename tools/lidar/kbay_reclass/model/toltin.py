"""Tolerance TIN: build a surface coarse-to-fine, inserting a point only when it departs from the
current surface by more than the measurement error, measured PERPENDICULAR to the local facet.

Why (Hig, 2026-09-26): a TIN through EVERY ground return honours each one exactly. On a steep
face a horizontal error of 0.1-0.2 m (footprint, flight-line misfit) is 0.3-0.6 m of vertical
scatter, and forcing the surface through all of it makes tight crenulations. Measured
perpendicular, those returns are within error of a smooth face and need not become vertices;
a real edge (cliff lip, base, boulder) exceeds the error and is inserted.

    surface = tol_tin(x, y, z, grid=(x0, y1, NX, NY), eps=0.15)

Levels 16, 8, 4, 2, 1 m. Seeds: the median-height point of each 16 m cell (robust to noise on
either side). At each finer level, per cell, the point with the largest normal error against the
current TIN is inserted if that error > eps. Delaunay is rebuilt per level (scipy).
"""
import numpy as np
from scipy.spatial import Delaunay


def _normal_error(tri, vx, vy, vz, x, y, z):
    """Vertical residual and |residual| * cos(facet slope) for points against the TIN."""
    s = tri.find_simplex(np.c_[x, y])
    ok = s >= 0
    e = np.full(len(x), np.inf); r = np.full(len(x), np.nan)
    v = tri.simplices[s[ok]]
    x1, y1, z1 = vx[v[:, 0]], vy[v[:, 0]], vz[v[:, 0]]
    x2, y2, z2 = vx[v[:, 1]], vy[v[:, 1]], vz[v[:, 1]]
    x3, y3, z3 = vx[v[:, 2]], vy[v[:, 2]], vz[v[:, 2]]
    # plane z = a + b x + c y through the facet
    ux, uy, uz = x2 - x1, y2 - y1, z2 - z1; wx, wy, wz = x3 - x1, y3 - y1, z3 - z1
    nx = uy * wz - uz * wy; ny = uz * wx - ux * wz; nz = ux * wy - uy * wx
    nz = np.where(np.abs(nz) < 1e-12, 1e-12, nz)
    zp = z1 - (nx * (x[ok] - x1) + ny * (y[ok] - y1)) / nz
    rr = z[ok] - zp
    cosn = np.abs(nz) / np.sqrt(nx * nx + ny * ny + nz * nz)     # cos of the facet's slope
    r[ok] = rr; e[ok] = np.abs(rr) * cosn
    return r, e


MIN_SUPPORT = 3


def tol_tin(x, y, z, grid, eps=0.15, levels=(16, 8, 4, 2, 1), return_vertices=False, force=None):
    """force: optional (fx, fy, fz) vertices that are ALWAYS in the TIN -- breakline vertices."""
    x0, y1, NX, NY = grid
    nf = 0
    if force is not None and len(force[0]):
        nf = len(force[0])
        x = np.r_[force[0], x]; y = np.r_[force[1], y]; z = np.r_[force[2], z]
    n = len(x)
    ins = np.zeros(n, bool); ins[:nf] = True
    # seeds: median-height point per coarsest cell
    L0 = levels[0]
    k = (((y1 - y) // L0).astype(np.int64) * (NX // L0 + 2) + ((x - x0) // L0).astype(np.int64))
    order = np.lexsort((z, k)); ks = k[order]
    start = np.r_[0, np.flatnonzero(ks[1:] != ks[:-1]) + 1]; end = np.r_[start[1:], len(ks)]
    ins[order[(start + end - 1) // 2]] = True
    # the four grid corners from the nearest points, so the hull covers the grid
    for cx, cy in [(x0, y1), (x0 + NX, y1), (x0, y1 - NY), (x0 + NX, y1 - NY)]:
        ins[np.argmin((x - cx) ** 2 + (y - cy) ** 2)] = True
    for L in levels[1:]:
        tri = Delaunay(np.c_[x[ins], y[ins]])
        vx, vy, vz = x[ins], y[ins], z[ins]
        cand = ~ins
        r, e = _normal_error(tri, vx, vy, vz, x[cand], y[cand], z[cand])
        idx = np.flatnonzero(cand)
        kk = (((y1 - y[idx]) // L).astype(np.int64) * (NX // L + 2) + ((x[idx] - x0) // L).astype(np.int64))
        # Per cell: a real edge puts MANY returns off the surface on the same side; a stray return
        # puts one. Insert only if >= MIN_SUPPORT returns exceed eps on the dominant side, and
        # insert the MEDIAN of those (greedy 'worst point' insertion chases outliers into spikes).
        # Points outside the current hull (e = inf) always qualify, so the hull grows.
        off = e > eps
        up = off & (np.nan_to_num(r, nan=1.0) > 0); dn = off & ~up
        cu = np.bincount(np.searchsorted(np.unique(kk), kk), weights=up.astype(float))
        cd = np.bincount(np.searchsorted(np.unique(kk), kk), weights=dn.astype(float))
        uk = np.unique(kk); ci = np.searchsorted(uk, kk)
        side_up = cu[ci] >= cd[ci]
        sel = off & ((up & side_up) | (dn & ~side_up))
        cnt = np.where(side_up, cu[ci], cd[ci])
        sel &= (cnt >= MIN_SUPPORT) | ~np.isfinite(e)
        s_idx = np.flatnonzero(sel)
        if len(s_idx):
            o = np.lexsort((e[s_idx], kk[s_idx])); ks_ = kk[s_idx][o]
            st = np.r_[0, np.flatnonzero(ks_[1:] != ks_[:-1]) + 1]; en = np.r_[st[1:], len(ks_)]
            med = s_idx[o[(st + en - 1) // 2]]
            ins[idx[med]] = True
    tri = Delaunay(np.c_[x[ins], y[ins]])
    gx = x0 + np.arange(NX) + 0.5; gy = y1 - np.arange(NY) - 0.5
    XX, YY = np.meshgrid(gx, gy)
    from scipy.interpolate import LinearNDInterpolator
    S = LinearNDInterpolator(tri, z[ins])(XX, YY)
    return (S, ins) if return_vertices else S
