"""Fill ENCLOSED holes in the final DTM, never the margin (Hig, 2026-09-26).

"Interpolate holes in the DTM, but not convexities in the margin of good data." So:

  hole     = a connected no-data region that does not touch the edge of the grid. Anything
             connected to the outside -- a bay in the data margin, however narrow its mouth --
             is left empty: filling it would invent ground out past where the survey saw.
  water    = NO special case (Hig, 2026-09-26: "this is what the data shows -- if an
             interpolation is not flat on water that's ok"). A first version filled holes with
             water returns and a flat rim at the rim water level; removed. Water holes get the same
             harmonic fill as land.
  land     = a harmonic (Laplace) fill from the rim heights: smooth, and by the maximum
             principle never above the highest nor below the lowest rim cell, so it cannot make
             a bump or a pit of its own.

Returns the filled grid and a mask of filled cells (2 = harmonic fill; 1 is no longer written), which is
written beside dtm_final.tif so a filled cell is never mistaken for a measured one.

In the full-survey run this belongs on the MOSAIC, not per tile: a hole cut by a tile edge
touches that tile's edge and would wrongly count as margin.
"""
import numpy as np
from scipy import ndimage, sparse
from scipy.sparse.linalg import spsolve

FLAT_RIM_M = 0.2


def fill_enclosed(Z, water_cells=None):
    Z = Z.astype('f8').copy(); nan = ~np.isfinite(Z)
    mask = np.zeros(Z.shape, np.uint8)
    lab, n = ndimage.label(nan)
    if n == 0:
        return Z, mask
    edge = set(np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]]).tolist()) - {0}
    objs = ndimage.find_objects(lab)
    for i, sl in enumerate(objs, start=1):
        if i in edge or sl is None:
            continue
        # work in the hole's bounding box plus a 1-cell rim
        r0, r1 = max(sl[0].start - 1, 0), min(sl[0].stop + 1, Z.shape[0])
        c0, c1 = max(sl[1].start - 1, 0), min(sl[1].stop + 1, Z.shape[1])
        h = lab[r0:r1, c0:c1] == i; z = Z[r0:r1, c0:c1]
        rim = ndimage.binary_dilation(h, structure=np.ones((3, 3), bool)) & ~h & np.isfinite(z)
        if not rim.any():
            continue
        z[h] = _laplace(z, h); mask[r0:r1, c0:c1][h] = 2
    return Z, mask


def _laplace(z, h):
    """Solve the discrete Laplace equation on the cells of h, with the finite neighbours of the
    hole as fixed boundary values (4-neighbour stencil)."""
    idx = -np.ones(h.shape, np.int64); cells = np.argwhere(h); idx[h] = np.arange(len(cells))
    rows, cols, vals = [], [], []; b = np.zeros(len(cells))
    for k, (r, c) in enumerate(cells):
        nb = 0
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            rr, cc = r + dr, c + dc
            if not (0 <= rr < h.shape[0] and 0 <= cc < h.shape[1]):
                continue
            nb += 1
            if h[rr, cc]:
                rows.append(k); cols.append(idx[rr, cc]); vals.append(-1.0)
            else:
                b[k] += z[rr, cc]
        rows.append(k); cols.append(k); vals.append(float(nb))
    A = sparse.csr_matrix((vals, (rows, cols)), shape=(len(cells), len(cells)))
    return spsolve(A, b)
