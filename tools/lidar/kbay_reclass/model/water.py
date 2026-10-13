"""Water rules for the reclassification (Hig, 2026-10-10: "points that are far off the water
surface are clearly errors", but "I'd rather not over-filter the water surface").

The policy stays "what the data shows" -- no hydroflattening, water returns are never touched,
and real ground beside water is kept. What changes:

  LEVEL     W: a local water level on the 1 m grid from the kept water returns (classes 9 and
            20, which NV5 puts at the water's edge): per-cell mean, a sigma-clipped mean over
            LEVEL_WIN (cells 0.5 m off the local mean are dropped, then averaged again), extended
            by nearest neighbour up to WATER_REACH m from the nearest water return. NaN beyond
            that, so nothing here acts away from observed water. (A median needs most of its
            window to hold water; a 60 m tarn with 263 water returns got no level at all.)
  FLOATING  a candidate return (any class that can become ground) is FLOATING when it hangs
            over the water with nothing solid under it:
              (a) it is FLOAT_ABOVE m above W AND above the lowest kept return in its OWN 1 m
                  cell, and that lowest return is at the water (within WATER_TOL of W) -- the
                  laser saw water directly beneath it; or
              (b) it is FLOAT_ABOVE m above W and most (EMPTY_FRAC) of the 9x9 m around it is
                  water or has no kept return at all (the NIR laser gets nothing back from calm
                  water, so a return-free hole beside water IS water).
            (b) is iterated: once a cell's only returns are flagged it counts as empty, so a
            field of floating returns peels inward (site A: class 1 AND class 5 returns 1-4 m
            over a tarn, with 5 water returns among 600 cells).
            Measured at Hig's sites (2026-10-10): these returns are single, with ground-like
            intensity, 0.5-5 m up, in vendor classes 1 AND 2. A beach is safe from (a) because
            the ground under a return within 1 m is never 0.5 m lower, and from (b) because every
            cell of it has returns. What (a) costs: on a bank steeper than ~30 deg the upper
            returns of the one cell that straddles the waterline (a 3x3 reach was tried first
            and stripped a 3-5 m ring off every lake shore steeper than ~10 deg). What (b) loses: a rock under ~4 m across in open water whose
            cells hold no water return. What neither touches: a dense object over water -- a
            boat, a dock, a raft -- whose own cells have no water return (site E, Seldovia
            harbour); those remain, as a structure would. PERSISTENT rescues anything the other
            flight also saw at the same height (class 22 within PERSIST_TOL in the same cell):
            an islet, not spray. Floating returns become class 7 (noise), which every model
            excludes. Also caught, harmlessly: canopy overhanging water (class 5).
  VOID      after that, a water cell with no kept return within 3x3 m has no observation: it is
            NaN in the surface rather than a TIN facet or a solver value bridged across the hole
            (at Hig's site C a facet reached 592 m in a 528 m lake). The enclosed-hole fill
            then treats it like any hole, from a rim that is now at the water level, and
            fill_mask marks it modelled.
  WATER CELL  W defined and the lowest kept return in 5x5 is at the water, or there is none.

Class 22 (the other flight) is also held to W in sitekit.admit_low_tide RULE 2: its returns are
admitted as water only at or below the kept flight's level, so a higher tide in the other
flight cannot stand 3 m proud of this one's water (site F)."""
import numpy as np
from scipy import ndimage

WATER_REACH = 20
LEVEL_WIN = 31
LEVEL_CLIP = 0.5
WATER_TOL = 0.3
FLOAT_ABOVE = 0.5
EMPTY_FRAC = 0.6
PERSIST_TOL = 0.3
WATER_CLASSES = (9, 20)
ITER = 4


def _cid(x, y, bounds):
    x0, x1, y0, y1 = bounds; NX, NY = int(x1 - x0), int(y1 - y0)
    return np.clip((y1 - y).astype(int), 0, NY - 1) * NX + np.clip((x - x0).astype(int), 0, NX - 1), NX, NY


def _cell_min(z, cid, sel, N):
    F = np.full(N, np.inf); np.minimum.at(F, cid[sel], z[sel]); F[np.isinf(F)] = np.nan
    return F


def _nmean(v, has, win):
    """Normalised box mean of v over cells where `has`; NaN where the window holds none."""
    s = ndimage.uniform_filter(np.where(has, v, 0.0), win); n = ndimage.uniform_filter(has.astype(float), win)
    return np.where(n > 0, s / np.maximum(n, 1e-12), np.nan)


def level(x, y, z, cls, bounds, win=LEVEL_WIN, reach=WATER_REACH):
    """W (NY, NX): local water level, NaN beyond `reach` m of any water return."""
    cid, NX, NY = _cid(x, y, bounds); N = NX * NY
    w = np.isin(cls, WATER_CLASSES)
    if not w.any():
        return np.full((NY, NX), np.nan)
    S = np.bincount(cid[w], weights=z[w], minlength=N); n = np.bincount(cid[w], minlength=N)
    has = (n > 0).reshape(NY, NX); lvl = np.where(n > 0, S / np.maximum(n, 1), 0.0).reshape(NY, NX)
    m1 = _nmean(lvl, has, win)
    keep = has & (np.abs(lvl - m1) <= LEVEL_CLIP)
    if not keep.any():
        keep = has
    m2 = _nmean(lvl, keep, win)
    m2[~keep] = np.nan                                         # a level only AT water cells; the rest by nearest neighbour
    dist, (ri, ci) = ndimage.distance_transform_edt(~keep, return_indices=True)
    W = m2[ri, ci]; W[dist > reach] = np.nan
    return W


def apply(x, y, z, cls, bounds, excl):
    """-> (cls_out, W, water_cell, info). Floating returns -> class 7."""
    cid, NX, NY = _cid(x, y, bounds); N = NX * NY
    W = level(x, y, z, cls, bounds)
    wdef = np.isfinite(W); Wp = W.ravel()[cid]
    out = cls.copy()
    water = np.isin(cls, WATER_CLASSES)
    # persistence reference: the other flight's level in this cell
    e = cls == 22
    E = np.full(N, np.nan)
    if e.any():
        S = np.bincount(cid[e], weights=z[e], minlength=N); n = np.bincount(cid[e], minlength=N)
        E = np.where(n >= 3, S / np.maximum(n, 1), np.nan)
    pers = np.abs(z - E[cid]) <= PERSIST_TOL
    na = nb = 0
    for it in range(ITER):
        kept = ~np.isin(out, excl)
        F1 = _cell_min(z, cid, kept, N).reshape(NY, NX)
        inf = np.where(np.isfinite(F1), F1, np.inf)
        wet = wdef & (np.isnan(F1) | (np.abs(F1 - W) <= WATER_TOL))
        frac = ndimage.uniform_filter(wet.astype(float), 9)
        cand = kept & ~water & np.isfinite(Wp) & (z > Wp + FLOAT_ABOVE) & ~pers
        F1p = F1.ravel()[cid]
        a = cand & np.isfinite(F1p) & (np.abs(F1p - Wp) <= WATER_TOL) & (z > F1p + FLOAT_ABOVE)
        b = cand & (frac.ravel()[cid] >= EMPTY_FRAC)
        fl = a | b
        na += int(a.sum()); nb += int((b & ~a).sum())
        if not fl.any():
            break
        out[fl] = 7
    kept = ~np.isin(out, excl)
    F5 = ndimage.minimum_filter(np.where(np.isfinite(_cell_min(z, cid, kept, N).reshape(NY, NX)), _cell_min(z, cid, kept, N).reshape(NY, NX), np.inf), 5)
    F5[np.isinf(F5)] = np.nan
    water_cell = wdef & (np.isnan(F5) | (F5 <= W + WATER_TOL))
    nfl = int((out == 7).sum() - (cls == 7).sum())
    info = dict(water_level_cells=int(wdef.sum()), water_cells=int(water_cell.sum()), floating_a=na, floating_b=nb, floating=nfl,
                persistent_kept=int((pers & ~water & ~np.isin(cls, excl) & np.isfinite(Wp) & (z > Wp + FLOAT_ABOVE)).sum()), passes=it + 1)
    return out, W, water_cell, info


def void_mask(x, y, cls, bounds, water_cell, excl):
    """Water cells with no kept return within 3x3 m: no observation, so no surface."""
    cid, NX, NY = _cid(x, y, bounds)
    kept = ~np.isin(cls, excl)
    has = np.bincount(cid[kept], minlength=NX * NY).reshape(NY, NX) > 0
    return water_cell & ~ndimage.maximum_filter(has, 3)
