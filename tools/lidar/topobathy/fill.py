"""Hole interpolation: a context-aware first guess, then the soundings (Hig, 2026-10-10).

"Where we're aiming here is a context-aware first-guess. This is particularly relevant along
beaches ... first create a synthetic DEM for the interpolation - in many cases it might be a
simple smooth interpolation, but for shorelines we want to have a shore-like transition: smooth
shore-parallel contours where there are beaches, and steep cliffs extending down to low-tide
where there is bedrock."

The composite leaves a HOLE wherever no observation the gate admits exists -- a lake, a lagoon
between its sounding lines, a tidal channel -- with class `water` on the class axis. This fills
those holes in four steps, each written out so the provenance of every filled cell is visible:

  DOMAIN    the hole mask, closed over the sounding lines that cross it (`close_m`), so a lagoon
            is one domain and its lines are observations inside it, not rim.  Domains touching
            the grid edge are margin and are left alone.  Every finite cell adjacent to a domain
            is RIM.  A rim cell is LAND if its class is ground / built / snow / canopy, else it
            is a bed (bathymetry hull) edge whose value is simply held.
  RIM SLOPE the slope of the surface in the `inland_m` behind each land-rim cell (normalised
            box mean of the slope map over finite cells), clamped to [slope_min, slope_max]
            degrees.  That number IS the shore character: a few degrees is a beach, tens of
            degrees is a cliff.  Roughness is not used yet; slope separates the two here.
  PRIOR     the synthetic DEM.  Each hole cell takes its nearest land-rim cell's elevation and
            slope and extends them along the distance to shore: z = z_rim - tan(slope) * d, no
            lower than the FLOOR (the low-tide level for marine domains, `lake_depth_m` under the
            rim for lakes).  Nearest-rim extension is then smoothed with a distance-growing
            kernel so adjacent rim points of different slope blend rather than terrace -- the
            contours are iso-distance curves, shore-parallel by construction.  That is the shore
            BAND (where the extension is still above the floor); beyond it the interior is a
            harmonic surface from the band's inner edge and the bed rim.  Off a beach the band
            is wide and gentle; off bedrock it is a steep face to the low-tide level.
  RESIDUAL  the soundings inside the domain condition the prior: r = sounding - prior at
            sounding cells, r = 0 on the rim, harmonic in between; fill = prior + r.  Sparse
            POINT depths (`points`, a CSV) are not observations: a weighted thin-plate smoothing
            spline of their residual (r = 0 on the boundary) is added afterwards, passing within
            ~points_sigma_m of them rather than through them (_spline_residual, Portage's wtps).

  SIGMA     for the fill: sigma_fill = sigma_prior * (1 - w) + sigma_obs * w where w decays with
            distance to the nearest sounding (`cond_m`), and sigma_prior grows with distance from
            the rim (sigma_rim_m per 100 m, from `sigma_prior_m`).  Lakes carry `lake_sigma_m`
            extra, because the floor there is a guess.

ICE FACE (ice_face "free", 2026-10-13): rim cells classed ice are no boundary at all -- not shore (no
slope extension, no basin taper toward them) and not fixed values (residual and spline free there).
LOWER BOUNDS (a points row with `bound` set): held out of the fit, then one active-set pass -- any
the surface violates joins the points at its bound and its domain is refitted.
SHORE GRADIENT (shore_grad_m, 2026-10-14): depths measured near a shore rescale the lake's basin prior
there (rho = measured / prior depth, smoothed along the lake, fading out by 2 x shore_grad_m from shore),
so a shore whose soundings say gentle gets a gentle prior in the gaps between them.
LAKE OVERRIDES (lake_dmax: [{x, y, dmax_m, level_m}]): the lake holding (x, y) takes that deepest depth
and water level instead of the estimates.

Marine vs lake: a domain is marine when the median elevation of its land rim is below
`marine_rim_max_m`; its floor is `low_tide_m`.  A lake's floor is its rim median minus
`lake_depth_m`.  Both are first guesses that the soundings override wherever they exist.

Outputs (in the composite's out/): fill.tif (the filled surface, NaN outside domains),
fill_mask.tif (0 none, 1 band, 2 interior prior, 3 conditioned by soundings, 4 margin hole left
empty, 5 lake shape prior, 6 channel section -- see channel_beds), fill_sigma.tif, fill_prior.tif (the synthetic DEM alone), and <id>_filled.tif (the
composite with holes filled) as a COG.
"""
from __future__ import annotations
import numpy as np
from scipy import ndimage, sparse
from scipy.sparse.linalg import cg, spsolve

DEFAULTS = dict(fill_margin=True, close_m=60, island_max_m2=2000, inland_m=10, slope_min_deg=1.0, slope_max_deg=60.0, low_tide_m=-2.9,
                marine_rim_max_m=6.0, lake_depth_m=5.0, sigma_prior_m=1.0, sigma_rim_m=2.0, lake_sigma_m=5.0,
                cond_m=30.0, cg_iters=300, coarse=16,
                lake_min_m2=100, lake_water_frac=0.5, lake_vd=0.5, hollister_k=0.5, huggel_a=0.104, huggel_b=1.42, lake_sigma_frac=0.5, channel_elong=3.0, lake_surface_tol_m=0.2, lake_islet_m2=50, void_shore_m=2.0, points=None,
                channel_slope=0.002, channel_drop_m=1.0, channel_ws_smooth_m=20.0, channel_min_m2=2000, channel_wd=15.0, channel_dmax_cap=2.0,
                channel_surface_tol_m=0.3, channel_tidal_wmax=120.0,
                points_sigma_m=1.0, points_rim_sigma_m=0.2, points_rim_step_m=10.0, points_grid_m=4.0, points_sigma_per_m=0.15,
                ice_face="shore", lake_dmax=None, shore_grad_m=None, shore_grad_r_m=150.0, shore_grad_n0=3.0, shore_grad_smooth_m=120.0,
                rim_widen=True, points_dz_m=0.0)
LAND_CLASSES = ("ground", "built", "snow", "canopy", "ice")


def _nmean(v, has, size):
    s = ndimage.uniform_filter(np.where(has, v, 0.0).astype(np.float32), size)
    n = ndimage.uniform_filter(has.astype(np.float32), size)
    return np.where(n > 1e-6, s / np.maximum(n, 1e-6), np.nan)


def _gsmooth(a, sig):
    """Gaussian filter of `a` (sigma in cells); past ~8 cells done on a block-averaged grid and zoomed
    back (a 400-cell kernel at full resolution is ~1e11 operations on a lake box)."""
    if sig <= 8:
        return ndimage.gaussian_filter(a, sig, truncate=3.0)
    f = int(sig // 4); H, W = a.shape; Hp, Wp = -(-H // f) * f, -(-W // f) * f
    b = np.zeros((Hp, Wp), np.float64); b[:H, :W] = a
    c = b.reshape(Hp // f, f, Wp // f, f).mean(axis=(1, 3))
    c = ndimage.gaussian_filter(c, sig / f, truncate=3.0)
    return ndimage.zoom(c, f, order=1)[:H, :W]


def _widening(val, wgt, d, scales, n0=0.0, floor=None):
    """Weighted mean of `val` (with weights `wgt`, both on the grid, 0 where none) whose window WIDENS with
    `d` (metres-from-shore, in cells): computed at each of `scales` (sigma, cells) and, cell by cell,
    interpolated in log-scale at sigma = max(d, scales[0]).  n0 sites' worth of weight on 0 at each scale
    (the field relaxes to 0 where there is little).  Hig (2026-10-14): "as the extrapolation proceeds, it
    should be dependent on an increasingly broad section of shoreline"."""
    out = np.zeros(val.shape, np.float64)
    ls = np.log(np.asarray(scales, float)); x = np.log(np.clip(d, scales[0], scales[-1]))
    prev = None
    for k, sc in enumerate(scales):
        num = _gsmooth(val * wgt, sc); den = _gsmooth(wgt, sc) + n0 / (2 * np.pi * sc * sc)
        cur = np.where(den > 1e-12, num / np.maximum(den, 1e-12), 0.0)
        if floor is not None:
            cur = np.where(_gsmooth(wgt, sc) > 1e-9, cur, floor)
        if k == 0:
            out[:] = cur
        else:
            t = np.clip((x - ls[k - 1]) / (ls[k] - ls[k - 1]), 0, 1)
            sel = x > ls[k - 1]
            out[sel] = ((1 - t) * prev + t * cur)[sel]
        prev = cur
    return out


def slope_deg(z, res):
    """Slope of a surface with holes: gradient of a nearest-filled copy, kept only where
    all four neighbours are finite."""
    fin = np.isfinite(z)
    if not fin.any():
        return np.full(z.shape, np.nan, np.float32)
    _, (ri, ci) = ndimage.distance_transform_edt(~fin, return_indices=True)
    zf = z[ri, ci]
    gy, gx = np.gradient(zf, res)
    s = np.degrees(np.arctan(np.hypot(gx, gy))).astype(np.float32)
    ok = ndimage.minimum_filter(fin.astype(np.uint8), 3).astype(bool)
    s[~ok] = np.nan
    return s


def harmonic(fixed_val, fixed, domain, coarse=16, iters=300):
    """Solve Laplace on `domain` cells with Dirichlet values on `fixed` cells.  Geometric
    multilevel: a direct solve at `coarse` m (block means of the fixed values as the coarse
    Dirichlet data), then conjugate gradients at each halving down to 1 m, warm-started from the
    level above.  A harmonic surface is smooth, so each level only removes what its parent could
    not represent, and `iters` per level converges where a cold start at 1 m did not (2.4 M
    unknowns stayed at their mean after 300 iterations, 2026-10-10)."""
    unk = domain & ~fixed
    out = np.where(fixed, fixed_val, np.nan).astype(np.float64)
    if not unk.any():
        return out
    H, W = domain.shape
    levels = []
    f = 1
    while f < coarse:
        f *= 2
        levels.append(f)
    guess = None
    for f in list(reversed(levels)) + [1]:
        Hc, Wc = -(-H // f), -(-W // f)
        if f > 1:
            pad = np.full((Hc * f, Wc * f), np.nan); pad[:H, :W] = np.where(fixed, fixed_val, np.nan)
            with np.errstate(invalid="ignore"):
                cval = np.nanmean(pad.reshape(Hc, f, Wc, f), axis=(1, 3))
            up = np.zeros((Hc * f, Wc * f), bool); up[:H, :W] = unk
            unk_c = up.reshape(Hc, f, Wc, f).any(axis=(1, 3)) & ~np.isfinite(cval)
            fix_c = np.isfinite(cval); val_c = np.nan_to_num(cval)
        else:
            unk_c, fix_c, val_c = unk, fixed, np.nan_to_num(fixed_val)
        x0 = None
        if guess is not None:
            g = np.repeat(np.repeat(guess, 2, 0), 2, 1)[:Hc, :Wc]
            x0 = np.where(np.isfinite(g), g, np.nanmean(val_c[fix_c]))
        sol = _solve(val_c, fix_c, unk_c, x0, iters, direct=(guess is None))
        guess = np.where(np.isfinite(sol), sol, np.nan)
    out[unk] = guess[unk]
    return out


def _solve(val, fixed, unk, x0, iters, direct):
    idx = -np.ones(unk.shape, np.int64); n = int(unk.sum())
    if n == 0:
        return np.where(fixed, val, np.nan)
    idx[unk] = np.arange(n)
    rows, cols, vals = [], [], []; b = np.zeros(n); diag = np.zeros(n)
    r, c = np.nonzero(unk); k = idx[r, c]
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        rr, cc = r + dr, c + dc
        inb = (rr >= 0) & (rr < unk.shape[0]) & (cc >= 0) & (cc < unk.shape[1])
        rr2, cc2 = rr[inb], cc[inb]; k2 = k[inb]
        nb_unk = unk[rr2, cc2]; nb_fix = fixed[rr2, cc2]
        diag[k2[nb_unk | nb_fix]] += 1
        rows.append(k2[nb_unk]); cols.append(idx[rr2[nb_unk], cc2[nb_unk]]); vals.append(-np.ones(int(nb_unk.sum())))
        np.add.at(b, k2[nb_fix], val[rr2[nb_fix], cc2[nb_fix]])
    rows.append(np.arange(n)); cols.append(np.arange(n)); vals.append(np.maximum(diag, 1e-9))
    A = sparse.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n))
    out = np.full(unk.shape, np.nan)
    if direct or n < 200_000:
        x = spsolve(A.tocsc(), b)
    else:
        x, _ = cg(A, b, x0=x0[unk] if x0 is not None else None, maxiter=iters, rtol=1e-6)
    out[unk] = x
    out[fixed] = val[fixed]
    return out


def _half_width(cells, dt, w, res):
    """Continuous half-width field: the local maximum of distance-to-bank over ~w, smoothed.  NOT
    the nearest skeleton cell's half-width: that jumps where the nearest cell switches branch and
    drew straight spokes across the bed (2026-10-12)."""
    k = max(int(round(w / res)) | 1, 3)
    hw = ndimage.maximum_filter(np.where(cells, dt, 0), size=k)
    sgh = max(w / 2 / res, 1.0)
    hw = (ndimage.gaussian_filter(np.where(cells, hw, 0), sgh)
          / np.maximum(ndimage.gaussian_filter(cells.astype(np.float64), sgh), 1e-3))
    return np.maximum(np.maximum(hw, dt), res)


def _keep_big(ch, res, min_m2):
    ch = ndimage.binary_opening(ch, iterations=2)
    cl, cn = ndimage.label(ch)
    if cn:
        csz = ndimage.sum(ch, cl, np.arange(1, cn + 1)) * res * res
        ch &= np.isin(cl, np.flatnonzero(csz >= min_m2) + 1)
    return ch


def channel_beds(lab, nw, wz, res, P, marine_ids=(), log=print):
    """CHANNELS (Hig, 2026-10-12: "recognize sloping water surfaces as flowing water channels and
    build bathymetry that is river-aware ... a constant cross-sectional area with a simple curve,
    such that there are wide shallow areas and narrow deep spots").  Per wet domain (labels 1..nw):

      WATER SURFACE  harmonic over the domain from the observed water returns (`wz`), smoothed
                     over channel_ws_smooth_m.
      RIVER          a non-marine domain whose surface drops more than channel_drop_m: channel
                     cells where the surface slopes more than channel_slope (m/m), opened, pieces
                     under channel_min_m2 dropped; over half the domain -> the whole domain (pools
                     in a river are the river).  The flat rest of a mixed domain stays a lake.
                     Neither bed nor surface is forced to descend: on average they do, locally not.
      TIDAL          a MARINE domain (2026-10-12: the tidal channel through the head of China Poot
                     Bay became "flat water-surface" -- its water is the sea at the flight's tide,
                     flat, so the slope test never fired and the marine floor was the low-tide
                     level under it).  The channel is told by SHAPE: cells whose local width is
                     under channel_tidal_wmax; depth tapers to 0 as the width reaches it, so the
                     channel meets the open water's floor without a step.
      SECTION        half-width hw = a continuous local-max field of distance-to-bank (_half_width),
                     offset y = hw - (distance to bank).  Parabolic: depth = dmax (1 - (y/hw)^2),
                     area 2/3 w dmax held constant along the channel: dmax = 1.5 A / w, so wide
                     reaches are shallow and narrow ones deep.  A = w_med^2 / channel_wd (mean
                     depth w_med / channel_wd at the median width).  dmax capped at
                     channel_dmax_cap x its value at the median width, smoothed over ~w_med.
      BED            water surface - depth.

    Returns (chan mask, bed, water surface, table).  A first take: no flow model, no bars, braid
    threads each get the whole section -- soundings (points) still condition it like the lakes."""
    from skimage.morphology import skeletonize
    chan = np.zeros(lab.shape, bool); bed = np.full(lab.shape, np.nan, np.float32)
    wsurf = np.full(lab.shape, np.nan, np.float32); tab = []
    if wz is None or nw == 0:
        return chan, bed, wsurf, tab
    sg = max(P["channel_ws_smooth_m"] / res, 1.0); pad = int(3 * sg) + 2
    for i, sl in enumerate(ndimage.find_objects(np.where(lab <= nw, lab, 0)), 1):
        if sl is None:
            continue
        sl = tuple(slice(max(s.start - pad, 0), min(s.stop + pad, n)) for s, n in zip(sl, lab.shape))
        cells = lab[sl] == i
        A_dom = cells.sum() * res * res
        if A_dom < P["channel_min_m2"]:
            continue
        w = wz[sl]; known = cells & np.isfinite(w)
        if known.sum() < 50:
            continue
        ws = harmonic(np.where(known, w, np.nan), known, cells, coarse=P["coarse"], iters=P["cg_iters"])
        num = ndimage.gaussian_filter(np.where(cells, np.nan_to_num(ws), 0), sg)
        den = ndimage.gaussian_filter(cells.astype(np.float64), sg)
        ws = np.where(cells & (den > 1e-3), num / np.maximum(den, 1e-3), np.nan)
        v = ws[cells & np.isfinite(ws)]
        if v.size == 0:
            continue
        drop = float(np.percentile(v, 98) - np.percentile(v, 2))
        dt = ndimage.distance_transform_edt(cells, sampling=res)
        tidal = i in marine_ids
        if tidal:
            wmax = P["channel_tidal_wmax"]
            hwt = _half_width(cells, dt, wmax, res)            # over wmax: is it narrow at all?
            ch = _keep_big(cells & (2 * hwt < wmax), res, P["channel_min_m2"])
            if not ch.any():
                continue
            slope = np.zeros(cells.shape)
            sk = skeletonize(ch) & ch                            # its own width, from its centreline
            w_med = max(float(np.median(2 * dt[sk])) if sk.any() else float(np.median(2 * hwt[ch])), 2 * res)
            hw = _half_width(cells, dt, w_med, res)
            taper = np.clip((wmax - 2 * hwt) / (0.3 * wmax), 0, 1)
        else:
            if drop < P["channel_drop_m"]:
                continue
            # slope of the surface: outside the domain filled with the nearest inside value, so the
            # gradient at the bank is the surface's own, not a cliff to NaN
            _, (ii, jj) = ndimage.distance_transform_edt(~np.isfinite(ws), return_indices=True)
            gy, gx = np.gradient(ws[ii, jj], res)
            slope = np.hypot(gx, gy)
            ch = _keep_big(cells & (slope > P["channel_slope"]), res, P["channel_min_m2"])
            if not ch.any():
                continue
            if ch.sum() > 0.5 * cells.sum():
                ch = cells.copy()
            sk = skeletonize(cells) & cells
            if not sk.any():
                continue
            w_med = float(np.median(2 * dt[sk & ch])) if (sk & ch).any() else float(np.median(2 * dt[sk]))
            w_med = max(w_med, 2 * res)
            hw = _half_width(cells, dt, w_med, res)
            taper = 1.0
        y = np.clip(hw - dt, 0, hw)
        A = w_med ** 2 / P["channel_wd"]
        dmax_ref = 1.5 * A / w_med
        dmax = np.minimum(1.5 * A / (2 * hw), P["channel_dmax_cap"] * dmax_ref)
        sgw = max(w_med / res, 1.0)
        dmax = (ndimage.gaussian_filter(np.where(cells, dmax, 0), sgw)
                / np.maximum(ndimage.gaussian_filter(cells.astype(np.float64), sgw), 1e-3))
        depth = taper * dmax * (1 - (y / hw) ** 2)
        b = (ws - depth).astype(np.float32)
        sub = ch & np.isfinite(b)
        chan[sl] |= sub
        bed[sl][sub] = b[sub]; wsurf[sl][sub] = ws[sub].astype(np.float32)
        tab.append(dict(id=int(i), tidal=bool(tidal), area_m2=int(sub.sum() * res * res), domain_m2=int(A_dom),
                        w_med_m=round(w_med, 1), area_section_m2=round(A, 1), dmax_med_m=round(dmax_ref, 2),
                        depth_max_m=round(float(np.nanmax(depth[sub])), 2), slope_med=round(float(np.median(slope[sub])), 4),
                        drop_m=round(drop, 1)))
    if tab:
        log(f"  channels: {len(tab)} ({sum(t['tidal'] for t in tab)} tidal) in wet domains, {int(chan.sum()) * res * res / 1e6:.3f} km2; "
            f"width p50 {np.median([t['w_med_m'] for t in tab]):.0f} m, section {np.median([t['area_section_m2'] for t in tab]):.1f} m2 p50")
    return chan, bed, wsurf, tab


def _spline_residual(S, base, cells, dom, bnd_ok, res, P, T=None, log=print, name=0):
    """Weighted thin-plate smoothing spline of the soundings' residual against `base` over one
    domain's hole `cells`, with residual 0 on the cells' boundary (finite neighbours: shore,
    islands).  Smoothing by the discrepancy principle: the
    soundings' sigma-weighted misfit RMS = 1.  S rows: (row, col, x, y, z, sigma) -- sigma from the
    points file's `sigma` column (export_soundings writes it), else points_sigma_m.  Returns the
    residual on `cells` (NaN elsewhere)."""
    if T is None or not cells.any():
        return None
    from scipy.ndimage import map_coordinates
    sg_rim = float(P["points_rim_sigma_m"])
    rr, cc = S[:, 0].astype(int), S[:, 1].astype(int)
    b = base[rr, cc].astype(np.float64); ok = np.isfinite(b)
    if ok.sum() < 3:
        return None
    # one site per cell (a sounding run logs several fixes per metre)
    key = rr[ok] * base.shape[1] + cc[ok]
    uk, inv = np.unique(key, return_inverse=True); cnt = np.bincount(inv).astype(float)
    sx = np.bincount(inv, S[ok, 2]) / cnt; sy = np.bincount(inv, S[ok, 3]) / cnt
    sr = np.bincount(inv, S[ok, 4] - b[ok]) / cnt
    sgp = np.sqrt(np.bincount(inv, S[ok, 5] ** 2) / cnt) / np.sqrt(cnt)     # several in a cell: their mean's sigma
    # boundary: finite cells 8-adjacent to the domain's holes, one per points_rim_step_m block
    bnd = ndimage.binary_dilation(cells, structure=np.ones((3, 3), bool)) & ~cells & bnd_ok
    br, bc = np.nonzero(bnd)
    k = max(int(round(P["points_rim_step_m"] / res)), 1)
    _, first = np.unique((br // k) * 1_000_003 + bc // k, return_index=True)
    br, bc = br[first], bc[first]
    bx = T.c + (bc + 0.5) * res; by = T.f - (br + 0.5) * res
    X = np.column_stack([np.r_[sx, bx], np.r_[sy, by]]); zv = np.r_[sr, np.zeros(len(bx))]
    sg = np.r_[sgp, np.full(len(bx), sg_rim)]
    m = len(X); ns = len(sx)
    c0 = X.mean(axis=0); xy = (X - c0) / 1000.0                           # km: keeps the system conditioned
    r2 = ((xy[:, None, :] - xy[None, :, :]) ** 2).sum(-1)
    K = np.where(r2 > 0, 0.5 * r2 * np.log(np.maximum(r2, 1e-12)), 0.0)
    Pm = np.column_stack([np.ones(m), xy])
    A0 = np.zeros((m + 3, m + 3)); A0[:m, :m] = K; A0[:m, m:] = Pm; A0[m:, :m] = Pm.T
    rhs = np.r_[zv, np.zeros(3)]

    def solve(lam):
        A = A0.copy(); A[np.arange(m), np.arange(m)] += lam * sg ** 2
        sol = np.linalg.solve(A, rhs); w = sol[:m]
        chi = np.sqrt(np.mean((lam * sgp * w[:ns]) ** 2))                  # misfit / sigma at the soundings
        return sol, chi

    lo, hi = -9.0, 3.0                                                    # log10 lam; chi rises with lam
    for _ in range(40):
        mid = (lo + hi) / 2
        if solve(10 ** mid)[1] < 1.0: lo = mid
        else: hi = mid
    lam = 10 ** lo; sol, chi = solve(lam); w, cp = sol[:m], sol[m:]
    # evaluate on a points_grid_m lattice over the domain's box, bilinear to the cells
    g = max(int(round(P["points_grid_m"] / res)), 1)
    rs_, cs_ = np.nonzero(cells)
    r0, r1, c0_, c1 = rs_.min(), rs_.max(), cs_.min(), cs_.max()
    gr = np.arange(r0, r1 + g, g); gc = np.arange(c0_, c1 + g, g)
    GR, GC = np.meshgrid(gr, gc, indexing="ij")
    Q = (np.column_stack([T.c + (GC.ravel() + 0.5) * res, T.f - (GR.ravel() + 0.5) * res]) - c0) / 1000.0
    val = np.empty(len(Q))
    for s in range(0, len(Q), 4096):
        q = Q[s:s + 4096]; d2 = ((q[:, None, :] - xy[None, :, :]) ** 2).sum(-1)
        Kq = np.where(d2 > 0, 0.5 * d2 * np.log(np.maximum(d2, 1e-12)), 0.0)
        val[s:s + 4096] = Kq @ w + cp[0] + q @ cp[1:]
    out = np.full(base.shape, np.nan, np.float32)
    out[rs_, cs_] = map_coordinates(val.reshape(GR.shape), [(rs_ - r0) / g, (cs_ - c0_) / g], order=1, mode="nearest")
    fit = sr - lam * sgp ** 2 * w[:ns]
    log(f"  soundings spline, domain {name}: {ns} sounding cells, {len(bx)} boundary sites; residual to the prior "
        f"p50 {np.median(sr):+.2f} m (|p90| {np.percentile(np.abs(sr), 90):.2f}); misfit RMS {np.sqrt(np.mean((sr - fit) ** 2)):.2f} m "
        f"(lam {lam:.1e}); correction field p5/p95 {np.nanpercentile(out[rs_, cs_], [5, 95]).round(2)} m")
    return out


def fill_holes(z, cls, res, classes, p=None, log=print):
    """z: composite (NaN = hole); cls: class_now codes; classes: name -> code.  Returns dict of
    arrays: fill, prior, mask, sigma."""
    P = dict(DEFAULTS); P.update(p or {})
    H, W = z.shape
    hole = ~np.isfinite(z)
    outside = P.get("_outside")
    if outside is not None:
        hole &= ~outside                                       # no survey here: not a hole, left empty
    land_codes = [classes[n] for n in LAND_CLASSES if n in classes]
    # DOMAIN: close over sounding lines
    # closing by a disc of close_m, as two distance transforms (a structuring element that
    # size on this many cells is hopeless): dilate = within close_m of a hole; erode = farther
    # than close_m from the dilation's outside
    dil = ndimage.distance_transform_edt(~hole, sampling=res) <= P["close_m"]
    dom = ndimage.distance_transform_edt(dil, sampling=res) > P["close_m"]
    # the closing must not swallow land -- except small islets of it inside the closed region
    # (< island_max_m2: a boat track or a seam of returns off calm water classed ground), which
    # become observations inside the domain and are judged there
    landfin = np.isfinite(z) & np.isin(cls, land_codes)
    inside_land = landfin & dom
    li, ln = ndimage.label(inside_land)
    if ln:
        sz = ndimage.sum(inside_land, li, np.arange(1, ln + 1)) * res * res
        big = np.isin(li, np.flatnonzero(sz >= P["island_max_m2"]) + 1)
        dom &= ~(landfin & big)
    else:
        dom &= ~landfin
    dom |= hole
    lab, n = ndimage.label(dom)
    edge = set(np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]]).tolist()) - {0}
    margin = np.isin(lab, list(edge)) if edge else np.zeros_like(dom)
    if P.get("fill_margin", True):
        # a composite's box edge is arbitrary (Hig, 2026-10-11: "our target should be entirely
        # continuous coverage"): domains touching it are filled too, the grid edge acting as a
        # no-flux boundary.  fill_margin: false keeps the reclass's rule (never fill the margin).
        margin = np.zeros_like(dom)
    dom &= ~margin
    # WET vs DRY (2026-10-11): a hole the class axis calls water and one it calls void (only
    # noise returns: glacier ice, steep ground the laser missed) are different problems; welded
    # into one domain by the closing, Halibut Cove lake (475 ha) shared a domain with the void
    # ground around it, failed the lake test and got no basin.  Separate labels: wet = water-
    # classed holes plus the observations inside the domain; dry = the rest.
    wet = dom & ((hole & (cls == classes["water"])) | np.isfinite(z))
    lw, nw = ndimage.label(wet); ld, nd = ndimage.label(dom & ~wet)
    # ...but a void patch OF a lake (no returns off calm water: China Poot Lake's east bay,
    # 2026-10-12) is wet: it touches a wet domain and its shore stands near that water (within
    # void_shore_m of the observed level).  Void ground above a lake (Halibut Cove) stays dry.
    wz = P.get("_water_z")
    if nw and nd and wz is not None:
        wlev = ndimage.labeled_comprehension(np.nan_to_num(wz, nan=-1e9), lw, np.arange(1, nw + 1),
                                             lambda v: np.median(v[v > -1e8]) if (v > -1e8).sum() >= 20 else np.nan, float, np.nan)
        land_obs = np.isfinite(z) & np.isin(cls, land_codes) & ~dom
        merge = np.zeros(z.shape, bool)
        for k, sl in enumerate(ndimage.find_objects(ld), 1):
            if sl is None:
                continue
            sl = tuple(slice(max(s.start - 1, 0), s.stop + 1) for s in sl)
            sub = ld[sl] == k
            ring = ndimage.binary_dilation(sub, structure=np.ones((3, 3), bool)) & ~sub
            wl = lw[sl][ring]; wl = wl[wl > 0]
            if not wl.size:
                continue
            lev = wlev[np.bincount(wl).argmax() - 1]
            rz = z[sl][ring & land_obs[sl]]
            # no shore of its own: a void hole enclosed by the water (2x2 void cells inside China Poot
            # Lake took the far shore's slope extension, 15-30 m above the bed around them)
            if not rz.size or (np.isfinite(lev) and np.median(rz) < lev + P["void_shore_m"]):
                merge[sl] |= sub
        if merge.any():
            log(f"  void patches of lakes joined to them: {int(merge.sum()) * res * res / 1e6:.4f} km2")
            wet |= merge
            lw, nw = ndimage.label(wet); ld, nd = ndimage.label(dom & ~wet)
    lab = np.where(wet, lw, np.where(ld > 0, ld + nw, 0)); n = nw + nd
    log(f"  wet domains {nw}, dry (void) domains {nd}")
    log(f"  domains: {n} (margin holes left empty: {int((margin & hole).sum()) * res * res / 1e6:.3f} km2); "
        f"domain {int(dom.sum()) * res * res / 1e6:.3f} km2, of which holes {int((dom & hole).sum()) * res * res / 1e6:.3f} km2")
    if n == 0:                                                       # nothing to fill
        nanf = np.full(z.shape, np.nan, np.float32)
        return dict(fill=nanf, prior=nanf.copy(), mask=np.zeros(z.shape, np.uint8), sigma=nanf.copy(),
                    domain=dom, marine=np.zeros(0, bool), lakes=[])
    inside_obs = dom & np.isfinite(z)                                # soundings inside a domain
    rim = ndimage.binary_dilation(dom, structure=np.ones((3, 3), bool)) & ~dom & np.isfinite(z)
    # ICE FACE (Hig, 2026-10-13, Grewingk): with ice_face "free" a rim cell classed ice is neither
    # shore nor boundary value -- the face is vertical and says nothing about the depth below it.  It
    # is taken out of the land rim (no slope extension, no taper of a lake basin toward it: distance to
    # shore ignores it) and out of `rim` (the harmonic residual and the soundings spline are free
    # there: the solver skips a neighbour that is neither fixed nor unknown, i.e. no flux).
    ice_rim = np.zeros(z.shape, bool)
    if P.get("ice_face") == "free" and "ice" in classes:
        # only where ice meets a WET domain (the lake): a hole inside the glacier (an SfM gap) ringed by
        # ice keeps the ice as its boundary -- freed, it had none and its solution was arbitrary (2026-10-13)
        wet_dom = (lab > 0) & (lab <= nw)
        ice_rim = rim & (cls == classes["ice"]) & ndimage.binary_dilation(wet_dom, structure=np.ones((3, 3), bool))
        rim &= ~ice_rim
        if ice_rim.any():
            log(f"  ice face free: {int(ice_rim.sum())} rim cells classed ice are no boundary")
    land_rim = rim & np.isin(cls, land_codes)
    bed_rim = rim & ~land_rim
    # RIM SLOPE
    S = slope_deg(z, res)
    fin_land = np.isfinite(S) & np.isin(cls, land_codes) & ~dom
    srim = _nmean(np.nan_to_num(S), fin_land, max(int(round(2 * P["inland_m"] / res)) + 1, 3))
    srim = np.clip(np.nan_to_num(srim, nan=P["slope_min_deg"]), P["slope_min_deg"], P["slope_max_deg"])
    # per-domain floor: marine or lake
    ids = np.arange(1, n + 1)
    # a rim cell beside both a lake and dry ground belongs to the lake (its shore)
    rlw = ndimage.maximum_filter(np.where(lab <= nw, lab, 0), 3); rl = np.where(rlw > 0, rlw, ndimage.maximum_filter(lab, 3))
    rim_lab = np.zeros_like(lab); rim_lab[land_rim] = rl[land_rim]
    zr = np.where(land_rim, z, np.nan)
    med = ndimage.labeled_comprehension(np.nan_to_num(zr, nan=1e9), rim_lab, ids, lambda v: np.median(v[v < 1e8]) if (v < 1e8).any() else np.nan, float, np.nan)
    marine = np.isfinite(med) & (med < P["marine_rim_max_m"])
    # floors only for the sea (low tide).  A non-marine domain has none (2026-10-11, Hig: "a short
    # steep slope down to a perfectly flat bottom"): a wet one gets the lake basin below, a dry one
    # (void: the laser missed the ground) the plain harmonic surface from its rim.
    floor_by = np.where(marine, P["low_tide_m"], np.nan)
    floor_by = np.where(np.isfinite(med), floor_by, P["low_tide_m"])
    floor = np.full(z.shape, np.nan, np.float32); floor[dom] = floor_by[lab[dom] - 1]
    log(f"  land rim {int(land_rim.sum())} cells, bed rim {int(bed_rim.sum())}, soundings inside {int(inside_obs.sum())}; "
        f"marine domains {int(marine.sum())} / lakes {int((~marine).sum())}; rim slope p10/50/90 "
        f"{np.percentile(srim[land_rim], [10, 50, 90]).round(1) if land_rim.any() else '-'} deg")
    # PRIOR: nearest land-rim extension
    prior = np.full(z.shape, np.nan, np.float32)
    band = np.zeros(z.shape, bool)
    if land_rim.any():
        d, (ri, ci) = ndimage.distance_transform_edt(~land_rim, return_indices=True, sampling=res)
        if P.get("rim_widen"):
            # the shore's slope read over a stretch of shore that WIDENS with distance out (Hig, 2026-10-14):
            # at the water's edge the slope of the inland_m behind the nearest rim cell, d out the mean tan(slope)
            # of the rim within ~d/2 of it -- one cliff patch no longer carries its slope far down
            tr = np.where(land_rim, np.tan(np.radians(srim)), 0.0); wr = land_rim.astype(np.float64)
            scl = [max(P["inland_m"] / res, 1.0) * 2 ** k for k in range(6)]          # 10 .. 320 m
            ls_ = np.log(scl); x_ = np.log(np.clip(d / res / 2, scl[0], scl[-1]))
            tan0 = np.tan(np.radians(srim[ri, ci])); tan_eff = tan0.copy(); prev = None
            for k, sc in enumerate(scl):
                den = _gsmooth(wr, sc)
                cur = np.where(den > 1e-9, _gsmooth(tr, sc) / np.maximum(den, 1e-9), np.nan)[ri, ci]
                cur = np.where(np.isfinite(cur), cur, tan0)
                if k == 0:
                    tan_eff[:] = cur
                else:
                    t_ = np.clip((x_ - ls_[k - 1]) / (ls_[k] - ls_[k - 1]), 0, 1); sel = x_ > ls_[k - 1]
                    tan_eff[sel] = ((1 - t_) * prev + t_ * cur)[sel]
                prev = cur
            ext = z[ri, ci] - tan_eff * d
            del prev, cur
        else:
            ext = z[ri, ci] - np.tan(np.radians(srim[ri, ci])) * d
        ext = ext.astype(np.float32)
        # smooth the extension with a kernel that grows with distance to shore (blend, not terrace)
        sm = ndimage.gaussian_filter(np.where(dom, ext, 0), sigma=max(8 / res, 1)) / np.maximum(ndimage.gaussian_filter(dom.astype(np.float32), sigma=max(8 / res, 1)), 1e-3)
        w = np.clip(0.3 + d / 40.0, 0, 1).astype(np.float32)     # some smoothing even at the shore: adjacent rim facets otherwise stair-step
        ext = (1 - w) * ext + w * sm
        band = dom & hole & (ext > floor)
        prior[band] = ext[band]
    # LAKES (2026-10-11, rough, for review): a basin shape instead of a flat floor.  Per lake
    # domain: L = greatest distance to shore, A = area, s = median rim slope.  Dmax two ways --
    # Hollister et al. 2011 (L * tan(s), times hollister_k ~0.5: basins are shallower than
    # their shores extrapolated) and the glacial-lake scaling of Huggel et al. 2002 / Cook &
    # Quincey 2015 (V = a*A^b, mean depth V/A, Dmax = mean / Vd) -- geometric mean, spread ->
    # sigma.  Shape: depth = Dmax*(l/L)^p with p from the target volume development Vd
    # (Hakanson: Vd = 2/((p+1)(p+2)); 0.5 is the flatter glacial norm, 1/3 a cone).  Shores
    # keep the band: the deeper of the rim-slope extension (capped at Dmax) and the shape.
    chan, chan_bed, chan_ws, chan_tab = channel_beds(lab, nw, P.get("_water_z"), res, P,
                                                     marine_ids={int(i) for i in ids[marine]}, log=log)
    lake_tab = []; lake_level = {}
    surface = np.zeros(z.shape, bool)              # observations judged to be the water surface (-> fill_surface.tif)
    lake_prior = np.full(z.shape, np.nan, np.float32); lake_sig = np.full(z.shape, np.nan, np.float32)
    if land_rim.any():
        dshore = d                                                  # distance to the nearest land rim cell
        if ice_rim.any():
            # a FREE ice face (2026-10-13): the basin is shaped as if the lake ran on under the glacier --
            # distance to shore is to any non-ice land, so across the face it is the valley's own width,
            # not a taper toward the face.  (Measured to the land rim only, the shape shallowed to the
            # face: the rim stops where rock meets ice, and the narrow end read as near the "shore".)
            land_noice = np.isfinite(z) & np.isin(cls, [classes[n] for n in LAND_CLASSES if n in classes and n != "ice"]) & ~dom
            dshore = ndimage.distance_transform_edt(~land_noice, sampling=res)
        vd = float(P["lake_vd"]); pexp = (-3 + np.sqrt(1 + 8 / vd)) / 2
        lake_ids = [i for i in ids if not marine[i - 1]]
        # per-lake overrides (lake_dmax: [{x, y, dmax_m, level_m?}]): the lake whose domain holds (x, y)
        # takes dmax_m as its deepest depth instead of the estimate, and level_m as its water level
        # (Grewingk, 2026-10-13: 150 m, Hig; the SfM water surface is too noisy to be a level)
        over = {}
        T_ = P.get("_transform")
        for e in (P.get("lake_dmax") or []):
            if T_ is None:
                break
            cc_, rr_ = ~T_ * (float(e["x"]), float(e["y"])); rr_, cc_ = int(rr_), int(cc_)
            if 0 <= rr_ < z.shape[0] and 0 <= cc_ < z.shape[1] and lab[rr_, cc_] > 0:
                over[int(lab[rr_, cc_])] = e
            else:
                log(f"  lake_dmax entry at {e['x']},{e['y']} is not in a fill domain")
        for i in lake_ids:
            cells = (lab == i) & ~chan                              # a channel's cells are the channel's
            n_c = int(cells.sum()); A = n_c * res * res
            if A < P["lake_min_m2"]:
                continue
            L = float(dshore[cells].max())
            rl = land_rim & (rim_lab == i)
            if not rl.any() or L <= 0:
                continue
            # a basin only where the class axis says water: a void domain (only noise returns --
            # glacier ice, steep high ground) is not a lake, whatever its rim (2026-10-11, the
            # lakes box carved 200-400 m "basins" into three of them)
            wfrac = float(np.mean(cls[cells & hole] == classes["water"])) if (cells & hole).any() else 0.0
            if wfrac < P["lake_water_frac"]:
                continue
            sbar = float(np.median(srim[rl])); level = float(med[i - 1])
            # the lake level: the median of the returns off its surface where there are any (the
            # land rim's median stands on the shore -- 0.76 m above China Poot Lake's water)
            # Best is the water surface itself (water_z: the water-classed observation that won each
            # cell, written by the build); the returns classed ground near the shore read ~1 m high on
            # China Poot Lake (2026-10-12), and then island ground within 1.5 m of the water was dropped
            wz = P.get("_water_z")
            wcell = cells & np.isfinite(wz) if wz is not None else None
            surf = cells & inside_obs & (cls != classes["bed"]) & (z > level - 3.0) & (z < level + 0.5)
            if wcell is not None and int(wcell.sum()) >= 20:
                level = float(np.median(wz[wcell]))
            elif int(surf.sum()) >= 20:
                level = float(np.median(z[surf]))
            if i in over and over[i].get("level_m") is not None:
                level = float(over[i]["level_m"])
            lake_level[i] = level
            d_h = P["hollister_k"] * L * np.tan(np.radians(sbar))
            dbar = P["huggel_a"] * A ** (P["huggel_b"] - 1)            # mean depth, m (A in m2, V in m3)
            d_g = dbar / vd
            dmax = float(np.sqrt(max(d_h, 0.5) * max(d_g, 0.5)))
            # a CHANNEL (A >> pi L^2: a river reach, a long narrow arm) is not a lake: the area
            # scaling gave a 1.2 km2, 180 m wide creek 74 m of depth (2026-10-11).  Width sets its
            # depth, so Hollister alone.
            elong = A / (np.pi * L * L)
            if elong > P["channel_elong"]:
                dmax = float(max(d_h, 0.5))
            sd = abs(d_h - d_g) / 2 + 5.0 if elong <= P["channel_elong"] else max(d_h, 1.0)
            if i in over:
                dmax = float(over[i]["dmax_m"])
                log(f"  lake {i}: Dmax {dmax:.0f} m and level {level:.2f} m set by the recipe "
                    f"(estimates: Hollister {d_h:.0f}, glacial {d_g:.0f} m)")
            # ONE smooth profile (2026-10-11, replaces max(shape, rim slope capped at Dmax), which on a
            # steep shore hit Dmax within metres and held it -- a wall and a flat floor):
            #   depth = Dmax * (1 - (1 - l/L)^q),  q = L tan(s) / Dmax  clipped to [1, 4]
            # The shore gradient is the rim slope's (q Dmax / L = tan s), the bed deepens all the way to
            # the deepest point and flattens only there.  q = 2 (Dmax = Hollister's 0.5 L tan s) is a
            # paraboloid, Vd = 0.5; q = 1 a cone, Vd 1/3.  Smoothed over ~L/8 to take the creases of
            # the distance transform (the medial axis of an irregular shore) out of the bed.
            lfrac = np.clip(dshore[cells] / L, 0, 1)
            q = float(np.clip(L * np.tan(np.radians(sbar)) / max(dmax, 0.1), 1.0, 4.0))
            frac = 1 - (1 - lfrac) ** q
            sg = max(L / 8 / res, 1.0); pad = int(3 * sg) + 1
            rs_, cs_ = np.nonzero(cells)
            r0, r1 = max(rs_.min() - pad, 0), min(rs_.max() + pad + 1, z.shape[0])
            c0, c1 = max(cs_.min() - pad, 0), min(cs_.max() + pad + 1, z.shape[1])
            cb = cells[r0:r1, c0:c1]
            fr = np.zeros(cb.shape, np.float32); fr[cb] = frac
            num = ndimage.gaussian_filter(fr, sg); den = ndimage.gaussian_filter(cb.astype(np.float32), sg)
            frs = np.where(den > 1e-3, num / np.maximum(den, 1e-3), 0)[cb]
            frac = np.minimum(frac, np.maximum(frs, 0))                 # smoothing never deepens the shore
            depth = dmax * frac
            hyp = over.get(i, {}).get("hypsometry")
            if hyp:
                # HYPSOMETRY (Hig, 2026-10-14, Emerald Lake: soundings too damaged to condition the bed, good for
                # its deepest depth and how much of the lake lies at each depth): the profile's depths are remapped
                # by RANK onto the given area-depth distribution -- depths at evenly spaced area fractions, shallow
                # to deep.  The shape (where it is deep, how the bed runs to the shore) stays the profile's;
                # the share of the lake at each depth becomes the lake's own, and the deepest is the last value.
                vals = np.asarray(hyp, np.float64)
                rk = (np.argsort(np.argsort(depth, kind="stable"), kind="stable") + 0.5) / depth.size
                depth = np.interp(rk, np.linspace(0, 1, vals.size), vals).astype(np.float32)
                dmax = float(vals[-1])
                log(f"  lake {i}: depths remapped to the recipe's hypsometry ({vals.size} quantiles, deepest {dmax:.1f} m)")
            lake_prior[cells] = (level - depth).astype(np.float32)
            lake_sig[cells] = (P["lake_sigma_frac"] * sd * frac + 1.0).astype(np.float32)
            rr, cc = ndimage.center_of_mass(cells)
            lake_tab.append(dict(id=int(i), area_m2=int(A), L_m=round(L, 1), rim_slope_deg=round(sbar, 1), level_m=round(level, 2),
                                 dmax_set=bool(i in over),
                                 dmax_hollister_m=round(d_h, 1), dmax_glacial_m=round(d_g, 1), dmax_m=round(dmax, 1), q=round(q, 2), elong=round(elong, 1), row=int(rr), col=int(cc)))
        # channels (channel_beds): their bed replaces band + interior like a lake's shape
        lake_prior[chan] = chan_bed[chan]
        lake_sig[chan] = (0.5 * np.clip(chan_ws[chan] - chan_bed[chan], 0, None) + 0.5).astype(np.float32)
        lk = np.isfinite(lake_prior) & dom
        prior[lk] = lake_prior[lk]; band &= ~lk                      # the lake shape replaces band + interior there
        # inside a lake a topo lidar cannot see the bed: an observation that is not bed (sonar,
        # bathymetry) and sits below level + lake_surface_tol_m is the water surface (a seam of returns
        # off calm water, an older survey's flight line classed ground -- Grewingk 2021 on China Poot
        # Lake, 2026-10-11, pinned the bed to the surface along straight strips), not evidence for the
        # fill.  Islands (higher) stay; the tolerance was 0.5 m against a level 1 m too high, which ate
        # their low ground (2026-10-12), and is 0.2 m now the level is the observed water.
        lvt = np.full(n + 1, np.nan, np.float32)
        for i, v in lake_level.items(): lvt[i] = v
        lvl = np.full(z.shape, np.nan, np.float32); lvl[lk] = lvt[lab[lk]]
        tol = np.full(z.shape, P["lake_surface_tol_m"], np.float32)
        lvl[chan] = chan_ws[chan]; tol[chan] = P["channel_surface_tol_m"]      # a channel's level is its own sloping surface
        at_surface = lk & inside_obs & np.isfinite(z) & (cls != classes["bed"]) & (z < lvl + tol)
        # and a speck of "ground" out on the water (< lake_islet_m2, the survey layers' islets rule) is
        # a stray return, not an island: one 2 m Grewingk cell 0.3 m above the water pulled the
        # bed up 30 m around it (2026-10-12).  Larger islands are kept.
        sp = lk & inside_obs & np.isfinite(z) & (cls != classes["bed"]) & ~at_surface
        sl, sn = ndimage.label(sp)
        if sn:
            ssz = ndimage.sum(sp, sl, np.arange(1, sn + 1)) * res * res
            specks = np.isin(sl, np.flatnonzero(ssz < P["lake_islet_m2"]) + 1)
            at_surface |= specks
        # ice inside a lake (bergs, the ragged fringe of a calving face that the closing wraps into the
        # domain) is not evidence of the bed: kept as an "island" at its surface height it pinned the bed
        # 20-30 m ABOVE the water along Grewingk's face (2026-10-13).  Dropped when the face is free.
        if P.get("ice_face") == "free" and "ice" in classes:
            ice_in = lk & inside_obs & np.isfinite(z) & (cls == classes["ice"])
            if ice_in.any():
                log(f"  ice inside lakes dropped as evidence (bergs, face fringe): {int(ice_in.sum())} cells")
                at_surface |= ice_in
        if at_surface.any():
            log(f"  lake observations at the water level or specks dropped as evidence: {int(at_surface.sum())} cells")
            inside_obs &= ~at_surface; hole |= at_surface
            surface |= at_surface
        # surface cells judged by the previous pass are water-classed holes now, invisible here;
        # carried over (inside a lake still) so the next build does not call them ground again
        prev = P.get("_surface_prev")
        if prev is not None:
            carried = prev & lk & ~surface
            surface |= carried
            log(f"  surface cells carried over from the previous fill: {int(carried.sum())}")
        if lake_tab:
            log(f"  lakes >= {P['lake_min_m2']} m2: {len(lake_tab)}; Dmax Hollister/glacial p50 "
                f"{np.median([t['dmax_hollister_m'] for t in lake_tab]):.1f}/{np.median([t['dmax_glacial_m'] for t in lake_tab]):.1f} m")
    else:
        lk = np.zeros(z.shape, bool)
    # SPARSE POINTS (Hig, 2026-10-11: "this method should be able to accept sparse point
    # depths"): a CSV with x,y and z (absolute) or depth (positive down from the domain's rim
    # level) becomes observations inside the domain, conditioning the prior like soundings.
    # They are NOT observations of the grid (2026-10-12, Hig: "allow some uncertainty ... ~1 m"): they
    # stay out of z and condition the fill afterwards through a smoothing spline (SOUNDINGS below).
    pts = np.zeros(z.shape, bool)                  # cells holding a sounding (bookkeeping only)
    sound = []                                     # (row, col, x, y, z)
    bounds = []                                    # lower bounds (row, col, x, y, z_max, sigma): bed at or below z
    if P.get("points"):
        import csv
        T = P.get("_transform")
        nbound = 0
        with open(P["points"]) as fh:
            for row in csv.DictReader(fh):
                # A lower bound (a lead line that ran out) says the bed is at or below z: never a
                # depth.  Held aside; after the spline any bound the surface violates becomes a
                # point AT the bound and the domain is refitted (one active-set pass, 2026-10-13).
                is_bound = bool(row.get("bound"))
                x, y = float(row["x"]), float(row["y"])
                if T is None:
                    continue
                cc, rr = ~T * (x, y); rr, cc = int(rr), int(cc)
                if not (0 <= rr < z.shape[0] and 0 <= cc < z.shape[1]) or not (dom[rr, cc] and hole[rr, cc]):
                    continue
                if row.get("z") not in (None, ""):
                    zz = float(row["z"]) + float(P.get("points_dz_m") or 0.0)
                else:
                    zz = float(med[lab[rr, cc] - 1]) - float(row["depth"])
                sg = float(row["sigma"]) if row.get("sigma") not in (None, "") else P["points_sigma_m"]
                if is_bound:
                    bounds.append((rr, cc, x, y, zz, sg)); nbound += 1
                    continue
                sound.append((rr, cc, x, y, zz, sg)); pts[rr, cc] = True
        log(f"  sparse points: {len(sound)} inside domain holes from {P['points']}"
            + (f"; {nbound} lower bounds held for the active-set pass" if nbound else ""))
    # SHORE GRADIENT (Hig, 2026-10-14, Grewingk's outlet: "use measured gradients from shore nearby to
    # inform the assumed gradient"): one basin shape per lake gives every shore the same profile, so a
    # shallow outlet bay got a 48 m hole between the sounding line across it and the river.  Each depth
    # measured near a shore (sparse points and bed observations within shore_grad_m of it) is a ratio
    # rho = measured depth / prior depth at its distance from shore; log rho is smoothed along the lake
    # (Gaussian, shore_grad_r_m, with shore_grad_n0 sites' worth of weight on rho = 1, so one sounding
    # cannot rescale a whole shore and the prior returns where there are none) and the lake prior's
    # depth is multiplied by it -- fully within shore_grad_m of shore, fading to none at twice that.
    # The deep middle keeps the basin shape; the soundings' spline still fits them afterwards.
    if P.get("shore_grad_m") and lk.any() and lake_tab:
        Ls = float(P["shore_grad_m"]); sgc = max(float(P["shore_grad_r_m"]) / res, 1.0)
        shp = lk & ~chan & np.isfinite(prior) & np.isfinite(lvl)
        dprior = np.where(shp, lvl - prior, np.nan)
        mr, mc, mz = [], [], []
        for (rr, cc, _x, _y, zz, _s) in sound:
            mr.append(rr); mc.append(cc); mz.append(zz)
        bed_in = shp & inside_obs & (cls == classes["bed"]) if "bed" in classes else np.zeros(z.shape, bool)
        k_ = max(int(round(10.0 / res)), 1)                      # bed observations: one per 10 m block
        bm = np.zeros(z.shape, bool); bm[::k_, ::k_] = True; br, bc = np.nonzero(bed_in & bm)
        mr += br.tolist(); mc += bc.tolist(); mz += z[br, bc].tolist()
        mr, mc, mz = np.array(mr, int), np.array(mc, int), np.array(mz, np.float64)
        use = shp[mr, mc] & (dshore[mr, mc] <= Ls) if len(mr) else np.zeros(0, bool)
        if use.any():
            dm_ = np.maximum(lvl[mr, mc] - mz, 0.1)[use]; dp_ = np.maximum(dprior[mr, mc], 0.5)[use]
            lr = np.log(np.clip(dm_ / dp_, 0.05, 4.0))
            acc = np.zeros(z.shape, np.float64); cnt = np.zeros(z.shape, np.float64)
            np.add.at(acc, (mr[use], mc[use]), lr); np.add.at(cnt, (mr[use], mc[use]), 1.0)
            val = np.where(cnt > 0, acc / np.maximum(cnt, 1), 0.0)
            # the window WIDENS with distance from shore (Hig, 2026-10-14): a cell d from shore takes the mean
            # log rho over sigma ~ max(d, shore_grad_r_m) -- local at the water's edge, an ever broader stretch
            # of shore farther out, so a few soundings by one cliff no longer set the bed far below it.
            scl = [sgc * 2 ** k for k in range(5)]                  # 150 .. 2400 m
            F = _widening(val, cnt, dshore / res, scl, n0=float(P["shore_grad_n0"]))
            # the blend in distance from shore, smooth (smoothstep), and the log-factor then smoothed over the
            # lake (shore_grad_smooth_m): distance to shore is creased along the medial axis and wherever the
            # nearest shore switches, and multiplying the basin by a function of it printed those creases
            # into the deep bed as sharp seams (v2, 2026-10-14, Hig: "hard seams in the deepest parts")
            t_ = np.clip((2 * Ls - dshore) / Ls, 0, 1); b = t_ * t_ * (3 - 2 * t_)
            G = np.where(shp, F * b, 0.0)
            ss = max(float(P["shore_grad_smooth_m"]) / res, 1.0)
            G = ndimage.gaussian_filter(G, ss) / np.maximum(ndimage.gaussian_filter(shp.astype(np.float64), ss), 1e-3)
            fac = np.exp(G)
            newp = (lvl - dprior * fac).astype(np.float32)
            ch = shp & (np.abs(newp - prior) > 0.5)
            prior[shp] = newp[shp]
            log(f"  shore gradient: {int(use.sum())} depths within {Ls:.0f} m of shore, rho p10/50/90 "
                f"{np.round(np.exp(np.percentile(lr, [10, 50, 90])), 2).tolist()}; prior moved > 0.5 m on "
                f"{int(ch.sum()) * res * res / 1e6:.3f} km2 (factor p10/50/90 there "
                f"{np.round(np.percentile(fac[ch], [10, 50, 90]), 2).tolist() if ch.any() else '-'})")
            P["_shore_grad_factor"] = np.where(shp, fac, np.nan).astype(np.float32)
    # interior: harmonic from band edge + rim + floor-band... the interior sits at the floor
    # where the extension reached it, carried smoothly between band edge and bed rim.
    interior = dom & ~band & ~lk                                 # holes AND the sounding cells: the prior ignores soundings
    fixed_val = np.where(np.isfinite(z) & rim, z, np.nan).astype(np.float64)
    fixed_val = np.where(band | lk, prior, fixed_val)                # a lake's shape bounds the dry ground beside it
    fixed = np.isfinite(fixed_val)
    if interior.any():
        # the band's inner edge sits at the floor, so the interior is harmonic between the
        # floor and whatever bed rim (bathymetry hull) bounds the domain.  The sounding cells
        # are unknowns here too: left out, a pocket of hole ringed by sounding lines had no
        # boundary at all (a singular system, and the solve wandered to +30 m, 2026-10-10)
        sol = harmonic(fixed_val, fixed, interior, coarse=P["coarse"], iters=P["cg_iters"])
        prior[interior] = sol[interior].astype(np.float32)
    interior &= hole
    # RESIDUAL conditioning by the soundings inside the domain
    fill = prior.copy()
    mask = np.zeros(z.shape, np.uint8); mask[band] = 1; mask[interior] = 2; mask[margin & hole] = 4; mask[lk & hole] = 5
    mask[chan & hole] = 6
    cond = np.zeros(z.shape, bool)
    # the residual at the RIM is the rim's own misfit to the prior beside it (2026-10-11; it was 0,
    # so the fill met the rim at the prior's level -- a step wherever the shore stood off the lake
    # level or the nearest-rim extension): harmonic, the misfit fades smoothly into the domain
    pin = dom & np.isfinite(prior)
    rim_res = np.zeros(z.shape, np.float64)
    if rim.any() and pin.any():
        _, (pi, pj) = ndimage.distance_transform_edt(~pin, return_indices=True)
        rr_, cc_ = np.nonzero(rim)
        zr_ = z[rr_, cc_].astype(np.float64)
        if lk.any():
            # a lake's shore is AT the water: a cliff standing 20-30 m over Grewingk's south-east corner
            # (v4, 2026-10-14) pinned the bed beside it 5-15 m above the lake.  Rim cells beside a lake count
            # at most at its level (lvl: the lake's own level, a channel's sloping surface).
            lv_ = lvl[pi[rr_, cc_], pj[rr_, cc_]]
            zr_ = np.where(np.isfinite(lv_), np.minimum(zr_, lv_), zr_)
        rim_res[rr_, cc_] = zr_ - prior[pi[rr_, cc_], pj[rr_, cc_]]
        del pi, pj
    if inside_obs.any() or np.abs(rim_res).max() > 0:
        rz = np.zeros(z.shape, np.float64)
        rz[inside_obs] = z[inside_obs] - np.nan_to_num(prior[inside_obs], nan=0.0)   # the prior is defined at sounding cells
        rfix = inside_obs | rim
        rval = np.where(inside_obs, rz, np.nan_to_num(rim_res))
        r = harmonic(rval, rfix, dom & hole, coarse=P["coarse"], iters=P["cg_iters"])
        ok = (dom & hole) & np.isfinite(r)
        fill[ok] = (prior[ok] + r[ok]).astype(np.float32)
        if inside_obs.any():
            dso = ndimage.distance_transform_edt(~inside_obs, sampling=res)
            cond = ok & (dso <= 3 * P["cond_m"])
            mask[cond] = 3
    # SOUNDINGS (2026-10-12, replaces "honoured exactly, harmonic residual in between"): a harmonic
    # residual is a cone at each sounding and a ridge along a sounding line (China Poot Lake: the
    # bed fell back to the prior a few tens of metres either side of every line).  What Portage
    # settled (TOPOBATHY_PLAN, grid_bathy.py wtps): a thin-plate SMOOTHING spline, global per domain,
    # per-site sigma on the diagonal.  Here it fits the residual r = sounding - fill, with r = 0 at
    # the domain's boundary (shore, islands; sigma points_rim_sigma_m).  The minimum-curvature
    # surface spreads a line's correction across the basin instead of a ridge, and the smoothing
    # is set by the discrepancy principle: the soundings' misfit RMS = points_sigma_m (~1 m, the
    # measurements are not absolute truth), so the surface passes NEAR them, not through them.
    sdist = None
    if sound:
        S_ = np.array(sound, np.float64)
        base = fill.copy()
        def spline_domain(i, Sd, tag=""):
            r = _spline_residual(Sd, base, (lab == i) & hole, dom, inside_obs | rim, res, P,
                                 T=P.get("_transform"), log=log, name=f"{int(i)}{tag}")
            if r is not None:
                m_ = np.isfinite(r)
                fill[m_] = (base[m_] + r[m_]).astype(np.float32)
        slab = lab[S_[:, 0].astype(int), S_[:, 1].astype(int)]
        for i in np.unique(slab):
            spline_domain(i, S_[slab == i])
        # LOWER BOUNDS, one active-set pass: a bound the surface violates (bed shallower than the line
        # that ran out) joins its domain's points AT the bound, and that domain is refitted.
        if bounds:
            B_ = np.array(bounds, np.float64)
            br_, bc_ = B_[:, 0].astype(int), B_[:, 1].astype(int)
            viol = fill[br_, bc_] > B_[:, 4]
            log(f"  lower bounds: {len(B_)}, violated after the fit: {int(viol.sum())} "
                f"(by {np.round(fill[br_, bc_][viol] - B_[viol, 4], 1).tolist()} m)")
            for i in np.unique(lab[br_[viol], bc_[viol]]):
                add = B_[viol & (lab[br_, bc_] == i)]
                spline_domain(i, np.vstack([S_[slab == i], add]), tag=" +bounds")
            still = fill[br_, bc_] > B_[:, 4] + 1.0
            if still.any():
                log(f"  lower bounds still violated by > 1 m after the pass: {int(still.sum())}")
        sdist = ndimage.distance_transform_edt(~pts, sampling=res)
        mask[(dom & hole) & (sdist <= 3 * P["cond_m"])] = 3
    # A LAKE'S MODELLED BED NEVER STANDS ABOVE ITS WATER (2026-10-14, Grewingk's SE corner, v5): the shore residuals
    # pull the bed up to the level at the edge and the conditioning surfaces (harmonic, then the thin-plate spline,
    # which has no maximum principle) overshoot them -- 0.5-5.6 m above the water on a 10-30 m fringe below a cliff.
    # Only modelled cells: islands are observations and stay.
    if lake_level:
        lv_ = np.full(n + 1, np.nan, np.float32)
        for i, v in lake_level.items():
            lv_[i] = v
        L_ = lv_[lab]
        up = dom & hole & ~chan & np.isfinite(L_) & np.isfinite(fill) & (fill > L_ - 0.1)
        if up.any():
            log(f"  lake bed held below the water: {int(up.sum()) * res * res / 1e6:.4f} km2 (up to {float(np.max(fill[up] - L_[up])):.1f} m above)")
            fill[up] = L_[up] - 0.1
    # SIGMA
    drim = ndimage.distance_transform_edt(~rim, sampling=res) if rim.any() else np.full(z.shape, 1e4)
    s_prior = P["sigma_prior_m"] + P["sigma_rim_m"] * drim / 100.0
    s_prior = s_prior + np.where(np.isfinite(floor) & ~np.isin(lab, ids[marine]) & dom, P["lake_sigma_m"], 0)
    s_prior = np.where(np.isfinite(lake_sig), lake_sig, s_prior)
    if inside_obs.any():
        wgt = np.exp(-dso / P["cond_m"])
        sig = s_prior * (1 - wgt) + 0.3 * wgt
    else:
        sig = s_prior
    if sdist is not None:                          # the soundings: points_sigma_m at them, growing ~0.15 m/m
        sig = np.minimum(sig, np.sqrt(P["points_sigma_m"] ** 2 + (P["points_sigma_per_m"] * sdist) ** 2))
    sigma = np.where(dom & hole & np.isfinite(fill), sig, np.nan).astype(np.float32)
    fill[~(dom & hole)] = np.nan; prior[~(dom & hole)] = np.nan
    log(f"  filled {int(np.isfinite(fill).sum()) * res * res / 1e6:.3f} km2: band {int(band.sum()) * res * res / 1e6:.3f}, "
        f"interior {int(interior.sum()) * res * res / 1e6:.3f}, conditioned {int(cond.sum()) * res * res / 1e6:.3f} km2; "
        f"sigma p50 {np.nanmedian(sigma):.2f} m")
    return dict(fill=fill, prior=prior, mask=mask, sigma=sigma, domain=dom, marine=marine, lakes=lake_tab, surface=surface,
                channels=chan_tab)
