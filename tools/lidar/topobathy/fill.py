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
`marine_rim_max_m` (or the water bodies call it sea / tidal / perched).  THE TIDAL LENS (`tidal_lens`,
default, Hig 2026-10-16, water.py): a marine domain has no floor and no channel model -- its bed is the
observed water surface (per cell where a survey saw it, else its level), sigma `tidal_sigma_m` growing
with distance from shore, conditioned by whatever was measured under it (multibeam, green-lidar bed,
soundings); a hand-set depth (an edit) still makes a basin.  `tidal_lens: false` restores the first
conception: floor `low_tide_m` and the tidal channel by width.  A lake's floor is its rim median minus
`lake_depth_m`.  Both are first guesses that the soundings override wherever they exist.

Outputs (in the composite's out/): fill.tif (the filled surface, NaN outside domains),
fill_mask.tif (0 none, 1 band, 2 interior prior, 3 conditioned by soundings, 4 margin hole left, 8 the ground prior
(3DEP) inside a large void, above the tide's reach, 7 tidal lens:
at the observed water surface,
empty, 5 lake shape prior, 6 channel section -- see channel_beds), fill_sigma.tif, fill_prior.tif (the synthetic DEM alone), and <id>_filled.tif (the
composite with holes filled) as a COG.
"""
from __future__ import annotations
import numpy as np
from scipy import ndimage, sparse
from scipy.sparse.linalg import cg, spsolve

DEFAULTS = dict(shore_model=None, tide_lines=None, rocks=None, sea_prior="local", intertidal_band=False, tide_max_m=4.5, band_ground=True, band_ground_sigma_m=1.0, band_ground_min_m=0.3, band_edge_deg=3.0, band_blend_max_m=500.0, ground_water=None, sea_profile_bins=20, sea_profile_min_n=30, sea_profile_local_m=1500.0, sea_profile_anchor_m=200.0, sea_profile_min_casts=40, sea_profile_cap_k=32, sea_profile_local_min_n=8, floor_sound_smooth_m=20.0, sound_prior_gap_mult=1.0, fill_margin=True, close_m=60, island_max_m2=2000, inland_m=10, slope_min_deg=1.0, slope_max_deg=60.0, low_tide_m=-2.9,
                marine_rim_max_m=6.0, lake_depth_m=5.0, sigma_prior_m=1.0, sigma_rim_m=2.0, lake_sigma_m=5.0,
                cond_m=30.0, cg_iters=300, coarse=16,
                lake_min_m2=100, lake_water_frac=0.5, lake_vd=0.5, hollister_k=0.5, huggel_a=0.104, huggel_b=1.42, lake_sigma_frac=0.5, channel_elong=3.0, lake_surface_tol_m=0.2, lake_islet_m2=50, lake_island_dz_m=0.65, floating_ice=True, floating_ice_corr=0.25, floating_ice_max_m2=20000, mapped_edge_near_m=200.0, mapped_edge_dz_m=1.0, points_despike_m=5.0, points_despike_r_m=40.0, points_despike_n=6, void_shore_m=2.0, dry_max_km2=1.0, points=None,
                channel_slope=0.002, channel_drop_m=1.0, channel_ws_smooth_m=20.0, channel_min_m2=2000, channel_wd=15.0, channel_dmax_cap=2.0,
                channel_surface_tol_m=0.3, channel_tidal_wmax=120.0, channel_braid_win=4.0, channel_wd_min=5.0,
                points_sigma_m=1.0, points_rim_sigma_m=0.2, points_rim_step_m=10.0, points_grid_m=4.0, points_sigma_per_m=0.15,
                points_decay_m=150.0, points_decay_scale_m=150.0, points_chi=1.0, lake_chi=0.2, lone_pin_max_m=20.0, ceiling_margin_m=0.5, ceiling_passes=2,
                ice_face="shore", lake_dmax=None, shore_grad_m=None, shore_grad_r_m=150.0, shore_grad_n0=3.0, shore_grad_smooth_m=120.0,
                rim_widen=True, points_dz_m=0.0, tidal_lens=True, tidal_sigma_m=1.0, lens_max_m=None, sound_hyps_min_n=30,
                points_max_sites=2500, points_max_eval=4_000_000, points_free_min=30, ground_prior=None, void_ground_m=4.5,
                gap_fit_w0_m=6.0, gap_fit_w1_m=25.0, gap_fit_min_cells=25, slope_water_depth_m=0.0, slope_water_min=0.0005, slope_water_smooth_m=25.0, flat_fit_min_ha=5.0, flat_fit_zlo_m=-4.5, flat_fit_ring_m=200.0, flat_fit_obs_slope=0.05, flat_fit_gmax=0.02, flat_fit_mad_m=1.2, low_water_cap=1.0, low_water_level_m=-1.87, low_water_edge_m=30.0, low_water_gap_m=48.0, rock_near_m=40.0)
LAND_CLASSES = ("ground", "built", "snow", "canopy", "ice")


def _points_files(P):
    """The fill's points file(s): one path or a list (2026-10-31: the lake soundings export, the NOS hydrographic surveys
    by vintage, CRM's cell centres -- one file a provenance, each with its own sigma column)."""
    p = P.get("points")
    return [p] if isinstance(p, str) else list(p or [])


def _read_on_grid(path, T, crs, shape, rows, cols, nearest=False):
    """A raster (any CRS) bilinear onto this grid's cells rows[0]:rows[1], cols[0]:cols[1]: the window only, never the
    whole grid (HAZARDS: the 4326 3DEP VRT).  NaN where the raster has nothing."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT
    from rasterio.transform import Affine
    r0, r1 = rows; c0, c1 = cols
    t = T * Affine.translation(c0, r0)
    with rasterio.open(path) as s:
        with WarpedVRT(s, crs=crs, transform=t, width=c1 - c0, height=r1 - r0, resampling=Resampling.nearest if nearest else Resampling.bilinear,
                       src_nodata=s.nodata, nodata=-9999.0) as v:
            a = v.read(1).astype(np.float32)
    out = np.full(shape, np.nan, np.float32)
    out[r0:r1, c0:c1] = np.where(a > -9000, a, np.nan)
    return out


def _dem_body_ground(pz, P, res, lo=0.3):
    """Cells of the ground prior inside a tidal water body that stand above its flattened water at lo, in patches of at least shore_island_min_m2: an island the
    body's polygon swallowed (2026-11-03, Hig's Windy Bay review: rocks at 19 and 11 m in 3DEP, modelled 14 m under the sea)."""
    from .water import TIDAL_KINDS
    bid, btab = P["_bodies"]
    tid = [int(k) for k, b in btab.items() if b.get("kind") in TIDAL_KINDS]
    keep = np.zeros(pz.shape, bool)
    if tid:
        up = np.isin(bid, tid) & np.isfinite(pz) & (pz > lo)
        pl, pn = ndimage.label(up, structure=np.ones((3, 3), bool))
        if pn:
            ok = np.bincount(pl.ravel(), minlength=pn + 1) * res * res >= float(P.get("shore_island_min_m2", 200.0)); ok[0] = False
            keep = ok[pl]
    return keep


def _dem_band(fp_, P, shape, res, islands=False):
    """The ground prior's cells inside `fp_` (a floor-path sea domain's holes) that are the intertidal: above low tide +
    band_ground_min_m and below tide_max_m + 1, not the DEM's hydroflattened water (ground_water: 1 a lake, 2 the sea),
    not a body's cells, not exactly flat.  Returns (eligible mask, the DEM on the grid, n eligible by height)."""
    rs_, cs_ = np.nonzero(fp_); rws = (int(rs_.min()), int(rs_.max()) + 1); cls_ = (int(cs_.min()), int(cs_.max()) + 1); del rs_, cs_
    pz = _read_on_grid(P["ground_prior"], P["_transform"], P["_crs"], shape, rws, cls_)
    el_ = fp_ & np.isfinite(pz) & (pz > float(P["low_tide_m"]) + float(P.get("band_ground_min_m", 0.3))) & (pz < float(P.get("tide_max_m", 4.5)) + 1.0)
    n_el = int(el_.sum())
    if P.get("ground_water"):
        wm_ = _read_on_grid(P["ground_water"], P["_transform"], P["_crs"], shape, rws, cls_, nearest=True)
        el_ &= ~(np.nan_to_num(wm_, nan=0.0) > 0.5); del wm_
    if P.get("_bodies") is not None:
        el_ &= (P["_bodies"][0] <= 0) | (_dem_body_ground(pz, P, res) if islands else False)
    pz0 = np.nan_to_num(pz, nan=0.0)
    el_ &= (ndimage.maximum_filter(pz0, 5) - ndimage.minimum_filter(pz0, 5)) >= 0.02; del pz0
    return el_, pz, n_el


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


def channel_beds(lab, nw, wz, res, P, marine_ids=(), skip_ids=(), whole_ids=(), log=print):
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
                     w is the TOTAL wetted width over channel_braid_win x w_med (parallel braid
                     threads share A), and depth <= own width / channel_wd_min (2026-10-19).
      BED            water surface - depth.

    Returns (chan mask, bed, water surface, table).  A first take: no flow model, no bars; braid
    threads share the section -- soundings (points) still condition it like the lakes."""
    from skimage.morphology import skeletonize
    chan = np.zeros(lab.shape, bool); bed = np.full(lab.shape, np.nan, np.float32)
    wsurf = np.full(lab.shape, np.nan, np.float32); tab = []
    if wz is None or nw == 0:
        return chan, bed, wsurf, tab
    sg = max(P["channel_ws_smooth_m"] / res, 1.0); pad = int(3 * sg) + 2
    for i, sl in enumerate(ndimage.find_objects(np.where(lab <= nw, lab, 0)), 1):
        if sl is None or i in skip_ids:             # skip_ids: the tidal lens's domains (no channel model)
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
            if i in whole_ids:                 # a river domain (split_by_level): the channel over all of it
                pass
            elif drop < P["channel_drop_m"]:
                continue
            # slope of the surface: outside the domain filled with the nearest inside value, so the
            # gradient at the bank is the surface's own, not a cliff to NaN
            _, (ii, jj) = ndimage.distance_transform_edt(~np.isfinite(ws), return_indices=True)
            gy, gx = np.gradient(ws[ii, jj], res)
            slope = np.hypot(gx, gy)
            ch = _keep_big(cells & (slope > P["channel_slope"]), res, P["channel_min_m2"])
            if i in whole_ids or ch.sum() > 0.5 * cells.sum():
                ch = cells.copy()
            if not ch.any():
                continue
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
        # BRAIDS SHARE THE SECTION (2026-10-19, the Woz delta: every side thread and pool took the whole river's
        # area, so the narrowest were carved deepest -- to the 2x cap, ~10 m -- and the delta read as hard-edged
        # pits).  The width that carries A is the TOTAL wetted width across the flow, estimated as wet area per
        # unit length over a window of channel_braid_win x w_med (a single thread: its own width, unchanged);
        # and no channel is deeper than its own width / channel_wd_min.
        if not tidal:
            kb = max(int(round(P["channel_braid_win"] * w_med / res)) | 1, 3)
            wtot = ndimage.uniform_filter(cells.astype(np.float32), size=kb) * kb * res
            w_eff = np.maximum(2 * hw, wtot)
        else:
            w_eff = 2 * hw
        dmax = np.minimum(np.minimum(1.5 * A / w_eff, P["channel_dmax_cap"] * dmax_ref), 2 * hw / P["channel_wd_min"])
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


def _points_in_grid(P, T, shape, med, lab, with_sigma=False):
    """The soundings file's points (no bounds) that land in a domain: (rows, cols) and z -- the lake loop's hypsometry sample.
    with_sigma: also each point's sigma (the file's `sigma` column, else points_sigma_m)."""
    import csv
    rr_, cc_, zz_, sg_ = [], [], [], []
    dz_ = P.get("points_dz_m") or 0.0
    sg0 = float(P.get("points_sigma_m", 1.0))
    for path in _points_files(P):
      with open(path) as fh:
        for row in csv.DictReader(fh):
            if row.get("bound"):
                continue
            cc, rr = ~T * (float(row["x"]), float(row["y"])); rr, cc = int(rr), int(cc)
            if not (0 <= rr < shape[0] and 0 <= cc < shape[1]) or lab[rr, cc] == 0:
                continue
            d_ = float(dz_.get(row.get("survey", ""), 0.0)) if isinstance(dz_, dict) else float(dz_)
            if row.get("z") not in (None, ""):
                zz = float(row["z"]) + d_
            elif row.get("depth") not in (None, "") and np.isfinite(med[lab[rr, cc] - 1]):
                zz = float(med[lab[rr, cc] - 1]) - float(row["depth"])
            else:
                continue
            rr_.append(rr); cc_.append(cc); zz_.append(zz)
            sg_.append(float(row["sigma"]) if row.get("sigma") not in (None, "") else sg0)
    if not rr_:
        return (None, None, None) if with_sigma else (None, None)
    if with_sigma:
        return (np.array(rr_), np.array(cc_)), np.array(zz_, np.float64), np.array(sg_, np.float64)
    return (np.array(rr_), np.array(cc_)), np.array(zz_, np.float64)


def _sea_profile(l_dom, l_k, dep_k, nb_max=20, min_n=30):
    """A sea domain's prior profile from its casts (2026-11-01): depth below low tide against distance from the shore.
    The casts are cut into up to nb_max quantile bins of distance (at least min_n a bin), each bin its median distance and
    median depth; the medians are made non-decreasing with distance by pool-adjacent-violators (a flat bottom is allowed;
    a trough is left to the spline); the profile is 0 at the shore, linear between the bin medians, flat beyond the last.
    Returns (depth read at l_dom, [(l, D)...])."""
    dep_k = np.asarray(dep_k, np.float64); l_k = np.asarray(l_k, np.float64)
    ok = np.isfinite(dep_k) & np.isfinite(l_k); dep_k, l_k = dep_k[ok], l_k[ok]
    if dep_k.size == 0:
        return np.zeros(l_dom.shape, np.float32), []
    nb = int(np.clip(dep_k.size // max(min_n, 1), 1, max(nb_max, 1)))
    o = np.argsort(l_k, kind="stable"); edges = np.linspace(0, dep_k.size, nb + 1).astype(int)
    lc, dc, wc = [], [], []
    for b in range(nb):
        s = o[edges[b]:edges[b + 1]]
        if s.size:
            lc.append(float(np.median(l_k[s]))); dc.append(float(max(np.median(dep_k[s]), 0.0))); wc.append(float(s.size))
    # pool adjacent violators: non-decreasing depth with distance
    blocks = [[lc[j], dc[j], wc[j]] for j in range(len(lc))]
    j = 0
    while j < len(blocks) - 1:
        if blocks[j][1] > blocks[j + 1][1]:
            w = blocks[j][2] + blocks[j + 1][2]
            blocks[j] = [(blocks[j][0] * blocks[j][2] + blocks[j + 1][0] * blocks[j + 1][2]) / w,
                         (blocks[j][1] * blocks[j][2] + blocks[j + 1][1] * blocks[j + 1][2]) / w, w]
            del blocks[j + 1]; j = max(j - 1, 0)
        else:
            j += 1
    prof = [(0.0, 0.0)] + [(b[0], b[1]) for b in blocks if b[0] > 0]
    xs = np.array([p[0] for p in prof]); ys = np.array([p[1] for p in prof])
    if xs.size < 2 or xs[-1] <= 0:                    # no shore in the window: the casts' median depth everywhere
        return np.full(l_dom.shape, float(np.median(dep_k)), np.float32), [(0.0, float(np.median(dep_k)))]
    return np.interp(np.asarray(l_dom, np.float64), xs, ys).astype(np.float32), prof


def _first_cast_env(cells, rr_, cc_, dep_k, res, P):
    """The first cast in every direction (2026-11-02; the local profiles' bound, shared with the kernel prior since v8): at
    each cell of a 4-cell lattice over `cells`, the nearest cast in each of 8 sectors among the sea_profile_cap_k nearest.
    Returns (env_hi, env_lo) on the cells' grid -- the deepest and the shallowest of those first casts (depth below low
    tide), NaN where none -- or (None, None) with fewer than two casts."""
    from scipy.spatial import cKDTree
    kc = int(P.get("sea_profile_cap_k", 32)); H, Wd = cells.shape; c = 4
    dep_k = np.asarray(dep_k, np.float64)
    if kc <= 0 or dep_k.size < 2:
        return None, None
    rr_ = np.asarray(rr_); cc_ = np.asarray(cc_)
    tk = cKDTree(np.column_stack([cc_, rr_]).astype(np.float64))
    gr, gc = np.mgrid[0:H:c, 0:Wd:c]; pts = np.column_stack([gc.ravel(), gr.ravel()]).astype(np.float64)
    kq = int(min(kc, dep_k.size))
    env_hi = np.full(len(pts), np.nan); env_lo = np.full(len(pts), np.nan)
    for a0 in range(0, len(pts), 250000):
        pp = pts[a0:a0 + 250000]; dq, iq = tk.query(pp, k=kq); iq = iq.reshape(len(pp), kq)
        ang = np.arctan2(rr_[iq] - pp[:, 1:2], cc_[iq] - pp[:, 0:1]); sec = ((ang + np.pi) / (np.pi / 4)).astype(int) % 8
        dk = dep_k[iq]; hi = np.full(len(pp), -np.inf); lo = np.full(len(pp), np.inf)
        for s8 in range(8):
            m8 = sec == s8; has = m8.any(axis=1); first = np.argmax(m8, axis=1)
            v8 = dk[np.arange(len(pp)), first]
            hi = np.where(has, np.maximum(hi, v8), hi); lo = np.where(has, np.minimum(lo, v8), lo)
        env_hi[a0:a0 + 250000] = np.where(np.isfinite(hi), hi, np.nan); env_lo[a0:a0 + 250000] = np.where(np.isfinite(lo), lo, np.nan)
    up = lambda v: np.repeat(np.repeat(v.reshape(gr.shape), c, axis=0), c, axis=1)[:H, :Wd].astype(np.float32)
    return up(env_hi), up(env_lo)


def _sea_profile_local(l_dom, cells, rr_, cc_, l_k, dep_k, res, P):
    """A sea domain's prior from LOCAL profiles (2026-11-02; Hig's Taylor Bay review of fix2g: one bay-wide profile put a
    7-17 m fjord basin over the delta apron, where the 1931 lead line had stopped at a 2 ft line 400 m out and nothing
    inside it says deep -- "local profiles are a good idea").  One profile per anchor of a lattice (sea_profile_anchor_m)
    over the domain, each fitted by _sea_profile to the casts within sea_profile_local_m of the anchor (the radius doubled,
    twice, when fewer than sea_profile_min_casts; the domain's own profile when still too few); a cell's prior depth is the
    Gaussian blend (sigma = the anchor spacing) of its four nearest anchors' profiles, each read at the cell's own distance
    from the shore, on a 4-cell lattice (the caller smooths over half the casts' spacing anyway).  Shoreward of an anchor's
    nearest casts its profile runs linearly to 0 at the shore, so a line of 2 ft casts caps the apron inside it: the
    profile's non-decreasing-seaward assumption, applied locally, is what says the flat is no deeper than its edge.
    Returns (depth read at l_dom, the domain's own profile [(l, D)...], stats)."""
    from scipy.spatial import cKDTree
    R = float(P.get("sea_profile_local_m", 1500.0)) / res; sp = max(float(P.get("sea_profile_anchor_m", 200.0)) / res, 4.0)
    nmin = int(P.get("sea_profile_min_casts", 40)); nb = int(P.get("sea_profile_bins", 20)); mn = int(P.get("sea_profile_min_n", 30))
    dep_k = np.asarray(dep_k, np.float64); l_k = np.asarray(l_k, np.float64)
    Dg, prof_g = _sea_profile(l_dom, l_k, dep_k, nb, mn)                     # the domain's own profile: the fallback
    xs_g = np.array([q[0] for q in prof_g]); ys_g = np.array([q[1] for q in prof_g])
    st = dict(anchors=0, widened=0, global_=0, capped=0)
    if dep_k.size < nmin or xs_g.size < 2:
        return Dg, prof_g, st, None
    H, Wd = cells.shape; c = 4
    ar, ac = np.mgrid[sp / 2:H:sp, sp / 2:Wd:sp]; ar = ar.astype(int).ravel(); ac = ac.astype(int).ravel()
    keep = cells[ar, ac]; ar, ac = ar[keep], ac[keep]
    if ar.size == 0:
        return Dg, prof_g, st, None
    tk = cKDTree(np.column_stack([cc_, rr_]).astype(np.float64))
    prof_a = []
    for a in range(ar.size):
        sel = None
        for k, r_ in enumerate((R, 2 * R, 4 * R)):
            s_ = tk.query_ball_point([ac[a], ar[a]], r_)
            if len(s_) >= nmin:
                sel = np.asarray(s_); st["widened"] += int(k > 0); break
        if sel is None:
            prof_a.append((xs_g, ys_g)); st["global_"] += 1; continue
        _, pr = _sea_profile(np.zeros(1), l_k[sel], dep_k[sel], nb, int(P.get("sea_profile_local_min_n", 8)))
        xs = np.array([q[0] for q in pr]); ys = np.array([q[1] for q in pr])
        prof_a.append((xs, ys) if xs.size >= 2 else (xs_g, ys_g))
    st["anchors"] = int(ar.size)
    gr, gc = np.mgrid[0:H:c, 0:Wd:c]; gl = np.asarray(l_dom)[::c, ::c]
    pts = np.column_stack([gc.ravel(), gr.ravel()]).astype(np.float64)
    K = int(min(4, ar.size)); ta = cKDTree(np.column_stack([ac, ar]).astype(np.float64))
    d, idx = ta.query(pts, k=K); d = d.reshape(len(pts), K); idx = idx.reshape(len(pts), K)
    w = np.exp(-0.5 * (d / sp) ** 2) + 1e-9
    acc = np.zeros(len(pts)); lflat = gl.ravel().astype(np.float64)
    for k in range(K):
        ik = idx[:, k]; o = np.argsort(ik, kind="stable"); u, s0 = np.unique(ik[o], return_index=True)
        ends = list(s0[1:]) + [len(o)]; vk = np.zeros(len(pts))
        for a, i0, i1 in zip(u, s0, ends):
            cc = o[i0:i1]; xs, ys = prof_a[a]; vk[cc] = np.interp(lflat[cc], xs, ys)
        acc += w[:, k] * vk
    D = acc / w.sum(1)
    # THE FIRST CAST IN EVERY DIRECTION BOUNDS THE PRIOR (2026-11-02): a profile by distance from the shore cannot tell a
    # delta's apron from the channel beside it when both run away from the shore -- at one distance a 2 ft cast and a
    # 30 m cast both exist -- but the first cast met from the apron in any direction is shallow: a line of shallow casts
    # shields what lies behind it.  So at each cell the nearest cast in each of 8 sectors (among its sea_profile_cap_k
    # nearest, within the search) is found; the prior depth never exceeds the deepest of them (the surveyed edge of an
    # unsounded flat says it is a flat; between lines of deep casts nothing changes; a trough no cast saw is the
    # spline's, which has no cast to put it there), and the shallowest of them relaxes the ceiling's 1-degree cone
    # (`relax`, read by the cap): a flat sounded at 2 ft 400 m out is not under 7 m of water.  (A plain k-nearest maximum
    # failed 150 m from the apron's edge: one channel cast among the eight nearest and the cap was gone.)
    kc = int(P.get("sea_profile_cap_k", 32)); relax = None
    if kc > 0 and dep_k.size >= 2:
        kq = int(min(kc, dep_k.size)); lt_ = float(P["low_tide_m"])
        env_hi = np.full(len(pts), np.inf); env_lo = np.full(len(pts), np.inf)
        for a0 in range(0, len(pts), 250000):
            pp = pts[a0:a0 + 250000]; dq, iq = tk.query(pp, k=kq); dq = dq.reshape(len(pp), kq); iq = iq.reshape(len(pp), kq)
            ang = np.arctan2(rr_[iq] - pp[:, 1:2], cc_[iq] - pp[:, 0:1]); sec = ((ang + np.pi) / (np.pi / 4)).astype(int) % 8
            dk = dep_k[iq]; hi = np.full(len(pp), -np.inf); lo = np.full(len(pp), np.inf)
            for s8 in range(8):
                m8 = sec == s8; has = m8.any(axis=1); first = np.argmax(m8, axis=1)      # the nearest in the sector (query is sorted)
                v8 = dk[np.arange(len(pp)), first]
                hi = np.where(has, np.maximum(hi, v8), hi); lo = np.where(has, np.minimum(lo, v8), lo)
            env_hi[a0:a0 + 250000] = hi; env_lo[a0:a0 + 250000] = lo
        ok_ = np.isfinite(env_hi)
        st["capped"] = int((D[ok_] > env_hi[ok_] + 1e-6).sum()); D = np.where(ok_, np.minimum(D, env_hi), D)
        rx = np.where(np.isfinite(env_lo), lt_ - np.maximum(env_lo, 0.1), np.nan).reshape(gr.shape)
        relax = np.repeat(np.repeat(rx, c, axis=0), c, axis=1)[:H, :Wd].astype(np.float32)
    D = D.reshape(gr.shape)
    return np.repeat(np.repeat(D, c, axis=0), c, axis=1)[:H, :Wd].astype(np.float32), prof_g, st, relax


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
    # one site per cell (a sounding run logs several fixes per metre) -- and when a domain holds more sounding cells than
    # points_max_sites (2026-10-31: the NOS surveys and CRM's centres, tens of thousands in an open-water box; the dense thin
    # plate is m^2 memory and m^3 time), one site per k x k block of cells, the block's mean with the mean's sigma
    k = 1
    while True:
        key = (rr[ok] // k) * (base.shape[1] // k + 1) + cc[ok] // k
        uk, inv = np.unique(key, return_inverse=True)
        if uk.size <= int(P["points_max_sites"]) or k >= 4096:
            break
        k = int(np.ceil(k * np.sqrt(uk.size / float(P["points_max_sites"])))) + 1
    if k > 1:
        log(f"  soundings spline, domain {name}: {int(ok.sum())} soundings in {len(np.unique(rr[ok] * base.shape[1] + cc[ok]))} cells, "
            f"aggregated to {uk.size} sites of {k * res:.0f} m")
    cnt = np.bincount(inv).astype(float)
    sx = np.bincount(inv, S[ok, 2]) / cnt; sy = np.bincount(inv, S[ok, 3]) / cnt
    sr = np.bincount(inv, S[ok, 4] - b[ok]) / cnt
    sgp = np.sqrt(np.bincount(inv, S[ok, 5] ** 2) / cnt) / np.sqrt(cnt)     # several in a cell: their mean's sigma
    # boundary: finite cells 8-adjacent to the domain's holes, one per points_rim_step_m block
    bnd = ndimage.binary_dilation(cells, structure=np.ones((3, 3), bool)) & ~cells & bnd_ok
    br, bc = np.nonzero(bnd)
    k = max(int(round(P["points_rim_step_m"] / res)), 1)
    while True:                                           # ...thinned the same way when the shore is long (an open-water box's coast)
        _, first = np.unique((br // k) * 1_000_003 + bc // k, return_index=True)
        if first.size <= int(P["points_max_sites"]) or k >= 4096:
            break
        k = int(np.ceil(k * np.sqrt(first.size / float(P["points_max_sites"])))) + 1
    br, bc = br[first], bc[first]
    bx = T.c + (bc + 0.5) * res; by = T.f - (br + 0.5) * res
    # THE FREE BOUNDARY: the domain's edge where nothing finite stands -- the grid's margin, an ice face, the open sea at the
    # window's edge.  The correction is tapered toward it below; between soundings inside a closed shore it is not.
    S3 = np.ones((3, 3), bool)
    free = ndimage.binary_dilation(cells, structure=S3) & ~cells & ~bnd_ok
    free[0, :] |= cells[0, :]; free[-1, :] |= cells[-1, :]; free[:, 0] |= cells[:, 0]; free[:, -1] |= cells[:, -1]
    if len(bx) == 0:
        # no boundary site pins the correction to zero anywhere: the field is a free thin plate through the soundings,
        # and a domain whose prior sits far from its soundings (run 14: a pool at the water level over 22 lower bounds
        # 130 m down) is dragged wholesale -- 22 cells to -254 m. Unanchored, the soundings are left to the log...
        # ...unless there are points_free_min of them (2026-10-31: an open-water box has no shore at all, and its bed is
        # the NOS soundings' alone): a plate through a hundred soundings is its own anchor, and the taper toward the free
        # edge below holds its extrapolation.
        if len(sx) < int(P["points_free_min"]):
            log(f"  soundings spline, domain {name}: {len(sx)} sounding cells, no boundary sites -- unanchored, not applied "
                f"(residual to the prior p50 {np.median(sr):+.2f} m)")
            return np.full(base.shape, np.nan, np.float32)
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
        if solve(10 ** mid)[1] < float(P.get("points_chi", 1.0)): lo = mid
        else: hi = mid
    lam = 10 ** lo; sol, chi = solve(lam); w, cp = sol[:m], sol[m:]
    # evaluate on a points_grid_m lattice over the domain's box, bilinear to the cells
    g = max(int(round(P["points_grid_m"] / res)), 1)
    rs_, cs_ = np.nonzero(cells)
    r0, r1, c0_, c1 = rs_.min(), rs_.max(), cs_.min(), cs_.max()
    g = max(g, int(np.ceil(np.sqrt((r1 - r0 + 1) * (c1 - c0_ + 1) / float(P["points_max_eval"])))))   # a 20 km domain: a coarser lattice
    gr = np.arange(r0, r1 + g, g); gc = np.arange(c0_, c1 + g, g)
    GR, GC = np.meshgrid(gr, gc, indexing="ij")
    Q = (np.column_stack([T.c + (GC.ravel() + 0.5) * res, T.f - (GR.ravel() + 0.5) * res]) - c0) / 1000.0
    val = np.empty(len(Q))
    for s in range(0, len(Q), 4096):
        q = Q[s:s + 4096]; d2 = ((q[:, None, :] - xy[None, :, :]) ** 2).sum(-1)
        Kq = np.where(d2 > 0, 0.5 * d2 * np.log(np.maximum(d2, 1e-12)), 0.0)
        val[s:s + 4096] = Kq @ w + cp[0] + q @ cp[1:]
    # THE CORRECTION FADES AWAY FROM ITS DATA (2026-10-28, WATER_REVIEW_PLAN.md section 2): a thin plate extrapolates
    # without bound -- with no boundary at Grewingk's ice face (free) the correction ran to +45 m 150-260 m beyond the
    # nearest sounding and lifted the bed through the water.  Beyond points_decay_m of the nearest data site (a sounding,
    # a lower bound or a ceiling site) it is tapered to zero over points_decay_scale_m (Gaussian), and the prior stands.
    # ...ONLY TOWARD A FREE BOUNDARY (Hig, 2026-10-31, Lower Hazel Lake: between sounding lines 300 m apart inside a closed
    # shore the taper let the prior reassert -- "a localized basin interrupted by isolated measurements"): a lattice node
    # whose nearest free-boundary cell is nearer than its nearest sounding is extrapolation toward nothing and is tapered;
    # a node with a sounding nearer than any free edge is interpolation between data and keeps its correction.
    D0 = float(P.get("points_decay_m") or 0.0)
    if D0 > 0 and free.any():
        from scipy.spatial import cKDTree
        dd, _ = cKDTree(xy[:ns]).query(Q, k=1)
        fr_, fc_ = np.nonzero(free)
        fxy = (np.column_stack([T.c + (fc_ + 0.5) * res, T.f - (fr_ + 0.5) * res]) - c0) / 1000.0
        df, _ = cKDTree(fxy).query(Q, k=1)
        fac = np.exp(-(np.maximum(dd * 1000.0 - D0, 0.0) / float(P["points_decay_scale_m"])) ** 2)
        val *= np.where(df < dd, fac, 1.0)
    out = np.full(base.shape, np.nan, np.float32)
    out[rs_, cs_] = map_coordinates(val.reshape(GR.shape), [(rs_ - r0) / g, (cs_ - c0_) / g], order=1, mode="nearest")
    fit = sr - lam * sgp ** 2 * w[:ns]
    log(f"  soundings spline, domain {name}: {ns} sounding cells, {len(bx)} boundary sites; residual to the prior "
        f"p50 {np.median(sr):+.2f} m (|p90| {np.percentile(np.abs(sr), 90):.2f}); misfit RMS {np.sqrt(np.mean((sr - fit) ** 2)):.2f} m "
        f"(lam {lam:.1e}); correction field p5/p95 {np.nanpercentile(out[rs_, cs_], [5, 95]).round(2)} m")
    return out


def _mapped_lake_sides(ml, labels, idx, res, P, z=None, wz=None):
    """Raised patches (labels, idx) against the TRUSTED mapped shorelines `ml` (water/sources.json "edge": "trusted"):
    (inside, outside, tall) -- inside: more than half the patch within a polygon; outside: not inside, more than half
    within mapped_edge_near_m of one (neither where no trusted polygon is near: the old rules); tall: the patch's median
    stands more than mapped_edge_dz_m above the water observed around it (`wz` on its ring; no water seen: tall)."""
    near = P.get("_ml_near")
    if near is None:
        near = ndimage.distance_transform_edt(~ml, sampling=res) <= P["mapped_edge_near_m"]; P["_ml_near"] = near
    fin = np.asarray(ndimage.mean(ml.astype(np.float32), labels, idx)); fnr = np.asarray(ndimage.mean(near.astype(np.float32), labels, idx))
    inside = fin > 0.5; outside = (~inside) & (fnr > 0.5)
    tall = np.ones(len(idx), bool)
    if z is not None and wz is not None and inside.any():
        own = ndimage.grey_dilation(labels, size=5); ring = (own > 0) & (labels == 0) & np.isfinite(wz)
        n_ = len(idx) + 1
        nw = np.bincount(own[ring], minlength=n_)[1:]; sw = np.bincount(own[ring], weights=wz[ring], minlength=n_)[1:]
        lev = np.where(nw > 0, sw / np.maximum(nw, 1), np.nan)
        zmed = np.asarray(ndimage.median(np.where(labels > 0, z, 0.0), labels, idx))
        tall = ~np.isfinite(lev) | (zmed - lev > P["mapped_edge_dz_m"])
    return inside, outside, tall


def split_by_level(lw, nw, wz, res, P, bodies=None, log=print):
    """Split wet domains by the WATER STAGE'S BODIES (2026-10-23, after a slope-based split cut Grewingk Lake into
    pieces on the noise of its water returns): each flat body (lake, perched, ephemeral) at its judged level is a
    domain of its own -- bodies within channel_drop_m of each other are one domain whatever their connectivity (a
    lake's parts between berg fields); a small flat ABOVE a far larger one takes its level (berg tops classed water);
    a small flat BELOW the dominant one is a pool of the river. A sloped body (river), the pools, and body-less
    water standing off its nearest flat's level form RIVER domains (the channel model over all of it, see
    channel_beds whole_ids); a tidal body is its own (marine). Body-less wet cells with no level (void: no returns)
    go with the nearest body. Returns (labels, n, domains split, the river domains' labels)."""
    out = lw.copy(); n = nw; nsplit = 0; rivers = set()
    if bodies is None or nw == 0:
        return out, n, nsplit, rivers
    from .water import FLAT_KINDS, TIDAL_KINDS
    bid, btab = bodies; mx = int(bid.max()) if bid.size else 0
    lev_b = np.full(mx + 1, np.nan); kind_b = np.zeros(mx + 1, np.int8)          # 1 flat, 2 river, 3 tidal
    prior_layers = set(P.get("_prior_layers") or ())
    for k, b in btab.items():
        k = int(k)
        if not (0 < k <= mx):
            continue
        if b.get("kind") in FLAT_KINDS:
            kind_b[k] = 1; v = b.get("consensus_level_m")
            # a level resting on a PRIOR alone (3DEP's hydroflattening where no survey returned from the water) is no
            # level: the body goes with the dominant flat around it (2026-10-23, run 10: 3DEP's 41.3 m "lake" inside
            # Grewingk's outlet arm at 48.9 became a pool, then a river domain, 7.6 m under the lake at pit 1)
            lv = [l for l in (b.get("levels") or []) if l.get("level_m") is not None]
            if prior_layers and lv and all(l.get("survey") in prior_layers for l in lv) and not str(b.get("consensus_rule", "")).startswith("the measured rim"):
                v = None                                     # ...unless the rim rule (water.py, 2026-11-06) set it from the lidar's ground at the water's edge: that is measured
                                                             # (2026-11-08, Hig's review of cm18, Petrof Lake at 59.38199,-150.81980: its 12.04 m from the rim was dropped here, the
                                                             # 286 ha lake had no level, went with its outlet's river domain and took the channel model -- a 6 m bed with a pit at each sounding)
            lev_b[k] = float(v) if v is not None else np.nan
        elif b.get("kind") in TIDAL_KINDS:
            kind_b[k] = 3
        else:
            kind_b[k] = 2
    drop = P["channel_drop_m"]
    for i, sl in enumerate(ndimage.find_objects(lw), 1):
        if sl is None:
            continue
        cells = lw[sl] == i
        if cells.sum() * res * res < 2 * P["channel_min_m2"]:
            continue
        b = np.where(cells, bid[sl], 0)
        present = [int(k) for k in np.unique(b[b > 0])]
        if len(present) < 2:
            continue
        has = b > 0
        _, (ii, jj) = ndimage.distance_transform_edt(~has, return_indices=True)
        ba = np.where(cells, b[ii, jj], 0)
        area = {k: int((ba == k).sum()) for k in present}
        flats = [k for k in present if kind_b[k] == 1]
        grp = {}; glev = []; garea = []
        for k in sorted(flats, key=lambda k: -area[k]):
            L = lev_b[k]; g = None
            if np.isfinite(L):
                for gi, gl in enumerate(glev):
                    if np.isfinite(gl) and abs(gl - L) <= drop:
                        g = gi; break
            if g is None:
                glev.append(L); garea.append(0); g = len(glev) - 1
            grp[k] = g; garea[g] += area[k]
        lev_g = [gi for gi, gl in enumerate(glev) if np.isfinite(gl)]         # the dominant flat is a LEVELLED one
        dom_g = (max(lev_g, key=lambda gi: garea[gi]) if lev_g else int(np.argmax(garea))) if glev else -1
        Ld = glev[dom_g] if glev else np.nan
        piece = np.zeros(cells.shape, np.int64)                        # 2 river, 3 tidal, 10 + group: flat
        w = wz[sl] if wz is not None else None
        G_ = np.array([gl if np.isfinite(gl) else np.inf for gl in glev], np.float64) if glev else None
        for k in present:
            m = ba == k
            if kind_b[k] == 3:
                piece[m] = 3
            elif kind_b[k] == 2:
                piece[m] = 2
                # ONLY RIVERS SLOPE (the flat-water rule; run 20i): a "river" whose water stands at one flat group's level
                # -- 90 % of its levelled cells within 0.1 m -- is that flat's water.  Bodies 136 and 432 at Grewingk's
                # face held the lake's 48.91 m, were river domains outside the soundings fit and its ceiling, and the
                # guard's clip left 1,060 cells of flat shelf 0.1 m under the water.
                if w is not None and G_ is not None and np.isfinite(G_).any():
                    wk = w[m & np.isfinite(w)]
                    if wk.size >= 20:
                        dv = np.abs(wk[:, None] - G_[None, :]); gi_ = np.argmin(dv, axis=1)
                        at_ = dv[np.arange(len(gi_)), gi_] <= min(drop, 0.1)
                        g0 = int(np.bincount(gi_[at_], minlength=len(G_)).argmax()) if at_.any() else -1
                        if g0 >= 0 and (at_ & (gi_ == g0)).mean() >= 0.9:
                            piece[m] = 10 + g0
            else:
                g = grp[k]; small = g != dom_g and garea[g] * 5 <= garea[dom_g]
                if small and np.isfinite(glev[g]) and np.isfinite(Ld) and glev[g] > Ld + drop:
                    piece[m] = 10 + dom_g
                elif small and np.isfinite(glev[g]) and np.isfinite(Ld) and glev[g] < Ld - drop:
                    piece[m] = 2
                elif not np.isfinite(glev[g]) and dom_g >= 0:
                    piece[m] = 10 + dom_g
                else:
                    piece[m] = 10 + g
        # water observed BELOW its flat's level by more than channel_drop_m is the river: between the pools (body-less),
        # and a flat body's own tail (the water stage's p10-p90 test reads a body 90 % lake, 10 % outlet as flat, and
        # its level is the lake's; the river mouth 3 km away was in it, crop test 2026-10-23). Above the level is not
        # water (berg tops classed water): it stays with the lake.
        w = wz[sl] if wz is not None else None
        off = np.zeros(cells.shape, bool)
        if w is not None:
            off = cells & np.isfinite(w) & (kind_b[ba] == 1) & np.isfinite(lev_b[ba]) & (w < lev_b[ba] - drop) & (piece >= 10)
            piece[off] = 2
        # EVIDENCE DECIDES, THE NEAREST EVIDENCE FOR THE REST (2026-10-23, run 14, Hig: "hills of water" on the creek's
        # banks 5 km below Grewingk Lake with pits 20 m under them): a body-less cell (a bank, a bar, void water) took
        # the nearest body's KIND, and a flat body with no level took the dominant flat's level -- the lake's 48.91 m --
        # so patches of the river's bank became the lake's domain and were dug to its basin. Decided cells are a river's,
        # a tidal body's, a levelled flat's, and water observed below its flat (off); every other cell of the domain
        # goes with the nearest decided cell's piece.
        dec = (has & ((kind_b[b] != 1) | np.isfinite(lev_b[b]))) | off
        und = cells & ~dec
        # ...and THE LEVEL DECIDES FIRST (2026-10-25, run 19): a body-less wet cell whose observed level -- the shoreline
        # stage's, routed through the water -- is a flat group's level (within 0.1 m, the stage's flatness) is that flat's water,
        # whatever body lies nearest. The water the stage took beyond the bodies at Grewingk's face stood at the lake's
        # 48.91 but lay nearest a marginal stream (a river body at 65 m): a river domain, so no lake level held its bed,
        # and the harmonic residual lifted it to the 2012 ice beside it (55-114 m under the water).
        if und.any() and w is not None and glev:
            G = np.array([gl if np.isfinite(gl) else np.inf for gl in glev], np.float64)
            uw = und & np.isfinite(w)
            if uw.any() and np.isfinite(G).any():
                dv = np.abs(w[uw][:, None] - G[None, :]); gi_ = np.argmin(dv, axis=1); ok_ = dv[np.arange(len(gi_)), gi_] <= min(drop, 0.1)   # a routed level IS the body's, to the cm; a sea-surface pool near a flat's level stays with the nearest evidence
                ri, ci = np.nonzero(uw)
                piece[ri[ok_], ci[ok_]] = 10 + gi_[ok_]
                und[ri[ok_], ci[ok_]] = False
        if und.any() and dec.any():
            _, (i2, j2) = ndimage.distance_transform_edt(~dec, return_indices=True)
            piece = np.where(und, piece[i2, j2], piece)
        # the river is one thing: its cells closed across the gaps between returns, and a speck under channel_min_m2
        # that is still on its own goes back to the flat it lies in (587 river domains of a few cells, crop test)
        riv = piece == 2
        if riv.any():
            riv = (ndimage.binary_closing(riv, structure=np.ones((3, 3), bool), iterations=2) & cells & (piece != 3)) | riv
            cl, cn = ndimage.label(riv, structure=np.ones((3, 3), bool))
            if cn:
                sz = np.bincount(cl.ravel(), minlength=cn + 1)[1:] * res * res
                speck = np.isin(cl, np.flatnonzero(sz < P["channel_min_m2"]) + 1)
                riv &= ~speck
            flat_of = np.where((kind_b[ba] == 1) & (ba > 0), 10 + np.array([grp.get(int(k), 0) for k in range(mx + 1)])[ba], 0)
            piece = np.where(riv, 2, np.where((piece == 2) & (flat_of > 0), flat_of, piece))
        labels = np.zeros(cells.shape, np.int64); k_ = 0; flat_codes = sorted(set(int(v) for v in np.unique(piece[piece >= 10])))
        for code in flat_codes:
            k_ += 1; labels[piece == code] = k_
        nflat = k_; riv_lab = []
        for code in (2, 3):
            cl, cn = ndimage.label(piece == code, structure=np.ones((3, 3), bool))
            for j in range(1, cn + 1):
                k_ += 1; labels[cl == j] = k_
                if code == 2:
                    riv_lab.append(k_)
        if k_ < 2:
            continue
        cnt = np.bincount(labels.ravel(), minlength=k_ + 1); cnt[0] = 0; big = int(cnt.argmax())
        remap = np.zeros(k_ + 1, np.int64); nxt = n
        for j in range(1, k_ + 1):
            if j == big:
                remap[j] = i
            else:
                nxt += 1; remap[j] = nxt
        sub = out[sl]; sub[cells] = remap[labels[cells]]
        for j in riv_lab:
            rivers.add(int(remap[j]))
        n = nxt; nsplit += 1
        if cells.sum() * res * res > 1e6:
            log(f"    split domain {i} ({cells.sum() * res * res / 1e6:.2f} km2): flats " +
                ", ".join(f"{cnt[j] * res * res / 1e6:.2f} km2" + (f" @ {glev[code - 10]:.2f}" if np.isfinite(glev[code - 10]) else "") for j, code in zip(range(1, nflat + 1), flat_codes)) +
                f"; river domains {len(riv_lab)} ({sum(int(cnt[j]) for j in riv_lab) * res * res / 1e6:.3f} km2); tidal {k_ - nflat - len(riv_lab)}")
    return out, n, nsplit, rivers


def fill_holes(z, cls, res, classes, p=None, log=print):
    """z: composite (NaN = hole); cls: class_now codes; classes: name -> code.  Returns dict of
    arrays: fill, prior, mask, sigma."""
    P = dict(DEFAULTS); P.update(p or {})
    H, W = z.shape
    hole = ~np.isfinite(z)
    # THE SHORELINE STAGE DECIDES (topobathy/shore.py, 2026-10-24): where its label and level exist, the land / water /
    # floating / glacier label replaces the fill's own shore rules below (the domain-closure judgement, the surface,
    # speck, low-island and floating-ice tests), and the observed water surface the fill reads IS the stage's level
    # over every water cell -- one level a body, a monotone level down a river, the tide's observed surface.
    shore_lab = P.get("_shore_label"); shore_lev = P.get("_shore_level"); shore_slp = P.get("_shore_slope")
    shore = shore_lab is not None and shore_lev is not None
    if shore:
        wet_l = np.isin(shore_lab, (2, 3))
        wzs = P.get("_water_z")
        wzs = np.full(z.shape, np.nan, np.float32) if wzs is None else wzs
        P["_water_z"] = np.where(wet_l & np.isfinite(shore_lev), shore_lev, np.where(wet_l, wzs, np.nan)).astype(np.float32)
        prior_won = P.get("_water_prior")                      # a prior's cells: inside the water they are never evidence
        P["_water_prior"] = None                               # (the level raster holds no prior's level to mask)
        log(f"  shoreline stage: labels land {int((shore_lab == 1).sum()) * res * res / 1e6:.2f}, water {int((shore_lab == 2).sum()) * res * res / 1e6:.2f}, "
            f"floating {int((shore_lab == 3).sum()) * res * res / 1e4:.2f} ha, glacier {int((shore_lab == 4).sum()) * res * res / 1e6:.2f} km2; its level is the water surface")
        # a PRIOR's cell, or a water-blind source's, inside the water is a HOLE, not an observation of the bed (3DEP's 2012
        # ice at 110 m inside the 2023 lake stood as the bed at the ice face; the 2023 SfM's surface 1-3 m under Emerald
        # Lake's level was its bed, 1.7 m deep -- run 18)
        drop_ = np.zeros(z.shape, bool)
        if prior_won is not None:
            drop_ |= prior_won
        if P.get("_water_blind") is not None:
            drop_ |= P["_water_blind"]
        drop_ &= wet_l
        if drop_.any():
            z = np.where(drop_, np.nan, z).astype(np.float32); hole = ~np.isfinite(z)
            log(f"  shoreline stage: {int(drop_.sum()) * res * res / 1e4:.2f} ha of a prior's or a water-blind source's cells inside the water are holes")
        # ...and so is a GROUND return inside a LAKE at or below its level (Hig's review of the fix2f boxes, 2026-11-01, Upper
        # Hazel / Kinnikinnick and Lower Hazel: patches of 0.3-0.6 ha classed ground 0.2-1.6 m under the lake, shelves in the
        # product).  A topographic laser does not see a lake's bed: a return inside the water that is not a bathymetric
        # (bed) class is the surface, or noise under it, whatever its height -- and a patch too large to be closed into the
        # domain stood as observed bed.  Ground ABOVE the level is an islet or the shore and stays.  Lakes only: the sea's
        # label holds tide flats a low-tide flight saw as ground under a later flight's surface, and a river's bars are
        # its own model's.  (A survey that caught a lake LOWER and saw real shore under the body's level would lose that
        # shore here; the body carries one level today -- the multi-level lake is the open item that would keep it.)
        # (a ground return inside a lake at or below its level is dropped by the SHORE stage, which writes water_blind.tif for
        # the build and the fill alike -- the rule lived here for one evening, fix2g, and never reached the product)
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
    if shore:
        # every OBSERVED cell the stage labelled land or glacier stands, whatever class its return carried (returns
        # classed water that stand above the water, 20 m up the ice margin, were dug to the sonar bed in run 17)
        cut_ = np.isfinite(z) & dom & np.isin(shore_lab, (1, 4))
        stay_ = np.isfinite(z) & dom & (shore_lab == 3)
        dom &= ~cut_
        log(f"  shoreline stage: {int(cut_.sum()) * res * res / 1e4:.2f} ha of land and glacier inside the closed water cut from the domain; "
            f"{int(stay_.sum()) * res * res / 1e4:.2f} ha floating stays inside")
    elif ln:
        sz = ndimage.sum(inside_land, li, np.arange(1, ln + 1)) * res * res
        bigl = sz >= P["island_max_m2"]
        # ...and a larger one the lake closes around that no second survey saw as land, nor a mapped island
        # touches, is judged in there too (2026-10-20, Grewingk Lake): bergs up to ~1.8 ha seen once by
        # Grewingk 2021 were cut out of the domain as shore, and the bed rose to just under the water around
        # each -- the floating-ice rule below only sees what is inside the domain
        ln_ = P.get("_land_n"); mi_ = P.get("_mapped_island"); ml_ = P.get("_mapped_lake")
        ml_in = ml_out = ml_tall = None
        if P.get("floating_ice", True) and (ln_ is not None or mi_ is not None or ml_ is not None) and bigl.any():
            idl = np.arange(1, ln + 1)
            corr = np.zeros(ln, bool); corr_isl = np.zeros(ln, bool)
            if ln_ is not None:
                corr |= np.asarray(ndimage.sum(inside_land & (ln_ >= 2), li, idl)) >= P["floating_ice_corr"] * (sz / (res * res))
            if mi_ is not None:
                corr_isl = np.asarray(ndimage.maximum(ndimage.binary_dilation(mi_, iterations=2), li, idl)) > 0
                corr |= corr_isl
            judge = bigl & ~corr & (sz <= P["floating_ice_max_m2"])
            if ml_ is not None:
                wzz = P.get("_water_z")
                if wzz is not None and P.get("_water_prior") is not None:
                    wzz = np.where(P["_water_prior"], np.nan, wzz)
                ml_in, ml_out, ml_tall = _mapped_lake_sides(ml_, li, idl, res, P, z, wzz)
                if ml_out.any() and "ice" in classes:
                    # ice outside the shoreline is the glacier only where it hangs together with ice beyond the shoreline's
                    # near-zone; an ice patch on its own within the zone is a berg the water has drifted past the drawn line
                    # (the lake grows, the line is older: crop test 2026-10-23, bergs 40-170 m past Hig's 2017-22 shoreline
                    # stood as pillars in water filled to -30..-100 m) and floats like one inside
                    ice_f = landfin & (cls == classes["ice"])
                    il, inn = ndimage.label(ice_f, structure=np.ones((3, 3), bool))
                    if inn:
                        far = np.unique(il[ice_f & ~P["_ml_near"]]); far = far[far > 0]
                        glacier = np.isin(il, far)
                        icy = np.asarray(ndimage.mean((cls == classes["ice"]).astype(np.float32), li, idl)) > 0.5
                        anchored = np.asarray(ndimage.maximum(glacier, li, idl)) > 0
                        berg = ml_out & icy & ~anchored
                        if berg.any():
                            log(f"  ice outside the shoreline not joined to the glacier: {int(berg.sum())} patches ({float(sz[berg].sum()) / 1e4:.2f} ha) float")
                            ml_out &= ~berg; ml_in |= berg; ml_tall |= berg
            # ...and only out on a LAKE, above it: a berg floats, so the patch must be ringed mostly by a body the water
            # stage calls a lake and stand above that body's level (2026-10-21, joint pilot run 5: with one pooled lidar
            # layer and no second survey over the outwash, 108 patches -- the creek's gravel bars and banks, metres BELOW
            # Grewingk Lake -- were judged inside, the lake's domain ran down the creek to the tide flats, and the bed
            # there was modelled as the lake's 150 m basin). No bodies: the old test alone.
            if judge.any() and P.get("_bodies") is not None:
                bid_, btab_ = P["_bodies"]
                mx = int(bid_.max()) if bid_.size else 0
                lev_ = np.full(mx + 1, np.nan)
                for k_, b_ in btab_.items():
                    if b_.get("kind") == "lake" and 0 <= int(k_) <= mx:
                        v_ = b_.get("consensus_level_m") if b_.get("consensus_level_m") is not None else b_.get("latest_level_m")
                        if v_ is not None:
                            lev_[int(k_)] = float(v_)
                jm = np.isin(li, np.flatnonzero(judge) + 1)
                own_ = ndimage.grey_dilation(np.where(jm, li, 0), size=5)
                ring_ = (own_ > 0) & ~jm
                pid_ = own_[ring_]; bl_ = lev_[np.clip(bid_[ring_], 0, mx)]
                inl = np.isfinite(bl_)
                n_r = np.bincount(pid_, minlength=ln + 1)[1:]
                n_l = np.bincount(pid_, weights=inl, minlength=ln + 1)[1:]
                s_l = np.bincount(pid_[inl], weights=bl_[inl], minlength=ln + 1)[1:]
                lev_p = np.where(n_l > 0, s_l / np.maximum(n_l, 1), np.nan)
                zmed_ = np.asarray(ndimage.median(np.where(inside_land, z, np.nan), li, idl))
                on_lake = (n_l >= 0.5 * np.maximum(n_r, 1)) & np.isfinite(lev_p) & (zmed_ > lev_p)
                n_off = int((judge & ~on_lake).sum())
                if n_off:
                    log(f"  ...of which not out on a lake above its level (river, tide flat, shore below the lake): {n_off} kept as land")
                judge &= on_lake
            if judge.any():
                log(f"  large raised land inside the closed water seen by one survey only, judged inside the domain: "
                    f"{int(judge.sum())} patches (m2 {np.sort(np.round(sz[judge]).astype(int))[::-1][:8].tolist()}); "
                    f"over {P['floating_ice_max_m2']:g} m2 kept as land: {int((bigl & ~corr & ~judge).sum())}")
            if ml_in is not None:
                # A TRUSTED MAPPED SHORELINE ARBITRATES (2026-10-23, run 14, Hig: "big negative and positive sharp-bounded
                # features in the upper lake -- not just pits, also pillars"): along Grewingk's face the closing wrapped a
                # ragged fringe of 2023 ice and SfM 'ground' (bergs, ice-cored debris) into the lake; patch by patch some
                # were judged inside and filled to the basin (-70 m), their neighbours kept as surface at 60-90 m, and a
                # second survey's agreement kept others (ice-cored debris persists for years). Inside a polygon whose
                # source trusts its edge, raised land standing more than mapped_edge_dz_m above the water is in the lake
                # unless a mapped island touches it; outside it, within mapped_edge_near_m, raised land is the shore or
                # the glacier -- never floating, whatever its size, and cut from the domain so the face is one line.
                add_ = bigl & ml_in & ml_tall & ~corr_isl & ~judge; drop_ = judge & ml_out
                judge = (judge & ~ml_out) | (bigl & ml_in & ml_tall & ~corr_isl)
                log(f"  trusted mapped shoreline: {int(add_.sum())} raised patches inside judged in the lake ({float(sz[add_].sum()) / 1e4:.2f} ha), "
                    f"{int(drop_.sum())} outside kept as shore/ice; {int((ml_out & ~bigl).sum())} small patches outside cut from the domain")
                bigl |= ml_out
            bigl &= ~judge
        big = np.isin(li, np.flatnonzero(bigl) + 1)
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
    if shore:
        wet = dom & np.isin(shore_lab, (2, 3))         # the stage decides what is wet: a class-water hole it gave no level is no water
    else:
        wet = dom & ((hole & (cls == classes["water"])) | np.isfinite(z))
    if P.get("tide_sea"):                     # mapped high-tide water is water, not dry void, or it is solved apart from the sea beside it (a 5 m step along the void's edge, Port Chatham)
        from .shoreface import _ground_sea
        tide_wet = dom & hole & _ground_sea(P, P["_transform"], z.shape) & ~wet; wet = wet | tide_wet
    else:
        tide_wet = None
    lw, nw = ndimage.label(wet); ld, nd = ndimage.label(dom & ~wet)
    # ...but a void patch OF a lake (no returns off calm water: China Poot Lake's east bay,
    # 2026-10-12) is wet: it touches a wet domain and its shore stands near that water (within
    # void_shore_m of the observed level).  Void ground above a lake (Halibut Cove) stays dry.
    wz = P.get("_water_z")
    # a PRIOR's water level is no evidence (2026-10-23): the cells a fallback layer won (3DEP's hydroflattening,
    # 41.3 m inside Grewingk Lake at 48.9) are masked out of every judgement of a domain's own water below
    wz_s = wz
    if wz is not None and P.get("_water_prior") is not None:
        wz_s = np.where(P["_water_prior"], np.nan, wz)
    prior_layers = set(P.get("_prior_layers") or ())
    # ...but NOT when the shoreline stage ran (2026-10-26, Hig's review of run 20: "small basin-cuts into good 2021
    # lidar with no water surface"): the stage already judged every void patch beside water (its level reaches it, or
    # it is flat at its prior, or it is no water), and a patch it called no water joined here was cut as river channel
    # with no level over it -- 31.5 ha in run 20c.  The stage decides what is wet.
    if nw and nd and wz is not None and not shore:
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
    # ONE LEVEL A DOMAIN (2026-10-23, joint_v2 run 7): with the point-first joint layer a river reads as water, not
    # as ground interpolated across it, so Grewingk Lake (48.9 m), its outlet river and the tide flat (0.6 m) became
    # ONE wet domain of 7.95 km2 -- kinded a lake, given the lake's basin and its soundings spline, and dug 5-15 m
    # under the tide flat and the river mouth. A wet domain whose observed surface drops more than channel_drop_m is
    # split where the water slopes: sloping water (> channel_slope, pieces over channel_min_m2) is its own domain
    # (a river: the channel model), and each flat piece between is its own (a lake at its level, or the sea's tide
    # flat, kinded by its own water body). Flat pieces under channel_min_m2 are pools of the river.
    rivers = set()
    if nw and P.get("level_split", True):
        lw, nw, nsplit, rivers = split_by_level(lw, nw, wz_s, res, P, bodies=P.get("_bodies"), log=log)
        if nsplit:
            log(f"  wet domains split by their bodies' levels: {nsplit} (lakes, rivers and tidal water now their own domains; river domains {len(rivers)})")
    # A LARGE dry void is not a hole to model (2026-10-16, whole-bay run: the mountains the KBay 2023 survey
    # never reached, 100 km2 a box, were "filled" as one dry domain each with the lake machinery -- a smooth
    # blob 300 m off the real ground at the box seams).  Dry domains above dry_max_km2 are left void: the
    # context fallback (3DEP) is the honest surface there, and a hole says so where even that is missing.
    dryvoid = np.zeros(z.shape, bool)                            # the dry domains left void (fill_dryvoid.tif: the fallback shows there)
    prior_ground = np.zeros(z.shape, bool)                       # a large void's cells the ground prior stands on above the tide's reach (below)
    prior_gz = np.full(z.shape, np.nan, np.float32)              # ...and its heights there (carried out as the fill, fill_mask 8)
    if nd:
        szd = np.bincount(ld.ravel(), minlength=nd + 1)[1:] * res * res / 1e6
        big = np.flatnonzero(szd > float(P.get("dry_max_km2", 1.0))) + 1
        gp = P.get("ground_prior")
        big_voids = np.isin(ld, big)
        if tide_wet is not None and tide_wet.any():      # sized with the mapped water still in: it must not cut a large void into small ones the prior then skips (domes on the headlands, Taylor and Port Chatham)
            l0_, n0_ = ndimage.label((dom & ~wet) | tide_wet)
            b0_ = np.flatnonzero(np.bincount(l0_.ravel(), minlength=n0_ + 1)[1:] * res * res / 1e6 > float(P.get("dry_max_km2", 1.0))) + 1
            big_voids |= np.isin(l0_, b0_) & dom & ~wet; del l0_, b0_
        if big_voids.any() and gp and P.get("_transform") is not None and P.get("_crs") is not None:
            # THE GROUND PRIOR STANDS INSIDE A LARGE VOID (Hig's review of fix1, 2026-10-31, the Koyuktoolik lagoon: a 6.9 km2
            # void no survey flew was bridged as one plate from the land rim to the sea, over the beach and the lagoon's shores
            # 3DEP had right -- "the shoreline might have gotten blurred out so the tide would misrepresent it"; and in fix2,
            # with ArcticDEM's canopy gaps as holes, the lagoon's void merged with the forest's into 11 km2 whose rim was high
            # ground, so nothing was bridged at all).  Inside any void above dry_max_km2 the ground prior (3DEP) at or above
            # void_ground_m (the tide's reach) is ground, read in as an observation of class ground: it bounds what is left,
            # becomes its rim, and only the LOW remainder (the flats, a lagoon 3DEP flattened at 0) is judged below -- bridged
            # as the sea's when its rim is low.  The prior's heights are carried out as the fill (fill_mask 8) so the product
            # holds them where the composite's fallback would not show (under a flattened body).
            bigm = big_voids; rs_, cs_ = np.nonzero(bigm)
            pz = _read_on_grid(gp, P["_transform"], P["_crs"], z.shape, (int(rs_.min()), int(rs_.max()) + 1), (int(cs_.min()), int(cs_.max()) + 1))
            hi_g = bigm & np.isfinite(pz) & (pz >= float(P["void_ground_m"]))
            if P.get("tide_sea"):                 # ...but not in mapped high-tide water: there 3DEP's reading is the smear of the cliff behind it (tide_vectors.py, 2026-11-04)
                from .shoreface import _ground_sea
                hi_g &= ~_ground_sea(P, P["_transform"], z.shape)
            del rs_, cs_, bigm
            if hi_g.any():
                z = z.copy(); cls = cls.copy()
                z[hi_g] = pz[hi_g]; cls[hi_g] = classes.get("ground", cls[hi_g]); prior_gz[hi_g] = pz[hi_g]
                hole[hi_g] = False; dom[hi_g] = False; prior_ground = hi_g
                ld, nd = ndimage.label(dom & ~wet)
                szd = np.bincount(ld.ravel(), minlength=nd + 1)[1:] * res * res / 1e6
                big = np.flatnonzero(szd > float(P.get("dry_max_km2", 1.0))) + 1
                log(f"  dry (void) domains above {float(P.get('dry_max_km2', 1.0)):g} km2: the ground prior at or above {float(P['void_ground_m']):g} m inside them "
                    f"is ground, not hole: {int(hi_g.sum()) * res * res / 1e6:.3f} km2; {big.size} large voids remain below it")
            del pz
        if big.size:
            # ...unless the void is LOW (the ring of finite cells round it has a median under marine_rim_max_m): the head-of-bay
            # flats no survey saw (2026-10-31, Hig: "a smooth transition across a data gap, rather than a void or terrace")
            # are the sea's -- kept as a domain, marine below, the shore descending into it and bridging to the bed
            ring = np.where((ld == 0) & np.isfinite(z), ndimage.grey_dilation(ld, size=3), 0)
            medr = np.asarray(ndimage.labeled_comprehension(np.nan_to_num(z, nan=1e9), ring, big,
                                                            lambda v: np.median(v[v < 1e8]) if (v < 1e8).any() else np.nan, float, np.nan))
            low_ = np.isfinite(medr) & (medr < float(P["marine_rim_max_m"])); del ring
            if P.get("tide_sea"):                 # a void that is mostly mapped high-tide water is the sea's whatever its cliffs stand at (tide_vectors.py: Port Chatham's shore band left as holes)
                from .shoreface import _ground_sea
                low_ |= np.asarray(ndimage.mean(_ground_sea(P, P["_transform"], z.shape), ld, big)) >= 0.25
            if low_.any():
                log(f"  ...of which LOW (rim median < {P['marine_rim_max_m']:g} m), kept as the sea's to bridge: {int(low_.sum())}, {szd[big[low_] - 1].sum():.3f} km2")
            big = big[~low_]
        if big.size:
            drop = np.isin(ld, big)
            log(f"  dry (void) domains above {float(P.get('dry_max_km2', 1.0)):g} km2 left void: {big.size}, {szd[big - 1].sum():.3f} km2")
            dryvoid |= drop; dom &= ~drop; ld, nd = ndimage.label(dom & ~wet)
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
    if shore and shore_slp is not None:
        ok_ = rim & np.isfinite(shore_slp)
        srim[ok_] = np.clip(shore_slp[ok_], P["slope_min_deg"], P["slope_max_deg"])
    # per-domain floor: marine or lake
    ids = np.arange(1, n + 1)
    # a rim cell beside both a lake and dry ground belongs to the lake (its shore)
    rlw = ndimage.maximum_filter(np.where(lab <= nw, lab, 0), 3); rl = np.where(rlw > 0, rlw, ndimage.maximum_filter(lab, 3))
    rim_lab = np.zeros_like(lab); rim_lab[land_rim] = rl[land_rim]
    zr = np.where(land_rim, z, np.nan)
    med = ndimage.labeled_comprehension(np.nan_to_num(zr, nan=1e9), rim_lab, ids, lambda v: np.median(v[v < 1e8]) if (v < 1e8).any() else np.nan, float, np.nan)
    marine = np.isfinite(med) & (med < P["marine_rim_max_m"])
    # WATER BODIES (stage B, water.py, 2026-10-15): a wet domain whose body the observations call a perched
    # lagoon, a lake or an ephemeral pond is NOT marine whatever its rim (the Seldovia lagoon: rim < 6 m, so
    # it took the low-tide floor and a 5 m tidal channel under water ponded at +0.8); one the observations
    # call the sea or tidal IS marine whatever its rim (a tidal channel under a cliff).  bover carries the
    # body's consensus level and the edits' depth into the lake loop below.
    bover = {}; tover = {}
    lens = bool(P.get("tidal_lens", True))
    from .water import TIDAL_KINDS
    if P.get("_bodies") is not None:
        bid, btab = P["_bodies"]; n_offlevel = 0
        in_body = np.bincount(lab[bid > 0], minlength=int(lab.max()) + 1) > 0   # which domains hold a body's cells at all, in one pass (as n_off below)
        for i in range(1, nw + 1):
            if i in rivers:                      # a river domain: the channel model, never a body's level or basin
                continue
            if not in_body[i]:
                continue
            v = bid[lab == i]; v = v[v > 0]
            if not v.size:
                continue
            u, c = np.unique(v, return_counts=True); b = btab.get(int(u[np.argmax(c)]))
            if b is None:
                continue
            has_fact = b.get("dmax_m") is not None or bool(b.get("hypsometry"))
            if lens and b["kind"] in TIDAL_KINDS and not has_fact:
                # THE TIDAL LENS (Hig, 2026-10-16): water the tide reaches is a flat at its observed surface until
                # measured -- marine, and marine below means the water surface itself.  An edit's depth still makes
                # a basin (the lake loop).
                marine[i - 1] = True; tover[i] = dict(kind=b["kind"], body=b["id"], level_m=b.get("consensus_level_m"))
                # a sea NO survey saw (water.py's compilation_sea: the bed below low tide at the grid edge, from the joint layer
                # or the fill's points, 2026-10-31): its level is low tide by construction, not an observed surface, so the
                # lens has nothing to hold -- the domain takes the floor path (nolens below) and its soundings shape the basin
                from .water import compilation_body
                if compilation_body(b):
                    tover[i]["unobserved"] = True
                    if tover[i].get("level_m") is None:
                        tover[i]["level_m"] = float(P["low_tide_m"])     # the body carries none: low tide by construction
            elif b["kind"] in ("sea", "tidal"):
                marine[i - 1] = True
            elif b["kind"] in ("lake", "perched", "ephemeral"):
                marine[i - 1] = False
                e = dict(kind=b["kind"], body=b["id"])
                # the body's level and depth only where this domain's own water stands at that level: a piece split
                # off the body (a pool of its outlet river 28 m below Grewingk Lake, 2026-10-23) keeps its own
                # ...judged on the domain's p25 AND p75 of its observed water: a prior's hydroflattened values low in
                # the same raster (3DEP's 41.3 m in Grewingk Lake at 48.9) pulled a median to 45.4 and the lake's
                # own piece off its level (test crop 2026-10-23)
                wz_ = wz_s; own = (np.nan, np.nan)
                if wz_ is not None:
                    v_ = wz_[lab == i]; v_ = v_[np.isfinite(v_)]
                    if v_.size >= 20: own = tuple(np.percentile(v_, [25, 75]).tolist())
                blev = b.get("consensus_level_m")
                # a level resting on a PRIOR alone stands only where no survey saw water in the domain or on its rim:
                # with survey water there, THAT is the level (2026-10-23, run 10: two 3DEP "ephemeral" patches at
                # 41.3 m, each its own hole inside Grewingk Lake's arm, kept 41.3 m beds under lidar water at 49.1)
                lv_ = [l for l in (b.get("levels") or []) if l.get("level_m") is not None]
                if blev is not None and wz_ is not None and prior_layers and lv_ and all(l.get("survey") in prior_layers for l in lv_):
                    sl_ = ndimage.find_objects(lab == i, max_label=1)[0]
                    sl_ = tuple(slice(max(s_.start - 3, 0), s_.stop + 3) for s_ in sl_)
                    near_ = ndimage.binary_dilation(lab[sl_] == i, iterations=3)
                    v_ = wz_[sl_][near_]; v_ = v_[np.isfinite(v_)]
                    if v_.size >= 20:
                        blev = float(np.median(v_)); e["level_from"] = "survey water on the rim"; own = (blev, blev)
                if blev is not None and np.isfinite(own[0]) and min(abs(own[0] - float(blev)), abs(own[1] - float(blev))) > P["channel_drop_m"]:
                    n_offlevel += 1
                    bover[i] = e
                    continue
                if blev is not None: e["level_m"] = float(blev)
                if b.get("dmax_m") is not None: e["dmax_m"] = float(b["dmax_m"])
                if b.get("hypsometry"): e["hypsometry"] = b["hypsometry"]
                if "dmax_m" not in e and b.get("dmax_cap_m") is not None:
                    e["dmax_cap_m"] = float(b["dmax_cap_m"])
                bover[i] = e
        if bover:
            log(f"  water bodies: {len(bover)} wet domains take their body's kind/level/depth "
                f"({', '.join(sorted({e['kind'] for e in bover.values()}))}); own water off the body's level, kind only: {n_offlevel}")
    # floors only for the sea (low tide).  A non-marine domain has none (2026-10-11, Hig: "a short
    # steep slope down to a perfectly flat bottom"): a wet one gets the lake basin below, a dry one
    # (void: the laser missed the ground) the plain harmonic surface from its rim.
    # ...and under the tidal lens a marine domain has no floor either: its bed is the water surface (below)
    # ...except a marine domain with NO observed water surface and no tidal body (2026-10-31: the low voids above, a bay
    # no survey flew): nothing for the lens to hold, so the first conception -- the shore descends to low tide and the
    # interior bridges to whatever bed bounds it (Hig: "the ocean is a basin that drops below the land")
    nwz_ = np.bincount(lab[dom & np.isfinite(wz_s)].ravel(), minlength=n + 1)[1:] if wz_s is not None else np.zeros(n, int)
    # ...a compilation body (unobserved) takes the floor path even where a lidar saw the sea's SURFACE over it (plates_t fix2
    # pass 12: KBay 2023's water returns at 2.9 m over Taylor Bay became the lens's bed on the flanks the soundings' spline
    # did not reach -- a surface over 40 m of water is no flat)
    nolens = marine & np.array([((i not in tover) and nwz_[k] < 20) or bool(tover.get(i, {}).get("unobserved")) for k, i in enumerate(ids)], bool)
    if lens and nolens.any():
        log(f"  marine domains with no observed water surface and no tidal body: {int(nolens.sum())} take the low-tide floor, not the lens")
    # ...and under the contour model a marine domain that touches the floor-path sea is that sea (2026-11-03, c4's evidence test,
    # Chatham: a 7.6 km2 domain where a lidar saw the surface kept the lens path and its own spline, and met the contour model's
    # basin in a 15 m step in 20 m along the coverage edge, with casts on both sides asking for 10 degrees)
    if lens and nolens.any() and P.get("shore_model") == "contour" and (marine & ~nolens).any():
        mc_, _ = ndimage.label(np.isin(lab, ids[marine]), structure=np.ones((3, 3), bool))
        sea_ = np.unique(mc_[np.isin(lab, ids[nolens])])
        join_ = marine & ~nolens & np.isin(np.asarray(ndimage.maximum(mc_, lab, ids), int), sea_)
        if join_.any():
            nolens = nolens | join_
            log(f"  contour model: {int(join_.sum())} lens-path marine domains touching the floor-path sea join it, {np.isin(lab, ids[join_]).sum() * res * res / 1e6:.3f} km2")
    floor_by = np.where(marine & (~lens | nolens), P["low_tide_m"], np.nan)
    floor_by = np.where(np.isfinite(med) | lens, floor_by, P["low_tide_m"])
    # ONE SEA ACROSS A COVERAGE EDGE (2026-11-02, Hig's Taylor Bay review of fix2g): the delta apron was a marine void with
    # no cast inside it, beside a "water body" (a DEM that saw the sea's surface) holding every cast 380 m away -- two
    # domains, so the apron got no profile at all (the rim's descent and the harmonic bridge: a 5 m basin) and the two
    # splines met in a 4 m step along a dead-straight seam, the DEM's coverage edge.  The sea does not know where a
    # survey stopped: floor-path marine domains that touch are ONE domain -- one profile, one spline, one ceiling (the
    # tidal-lens domains keep their own path; a lake is never merged).  Each merged group takes the smallest of its old
    # ids, so the per-id tables (marine, nolens, floor) stay valid for it.
    if int(nolens.sum()) > 1:
        U_ = np.isin(lab, ids[nolens]); comp_, nc_ = ndimage.label(U_, structure=np.ones((3, 3), bool))
        if nc_ < int(nolens.sum()):
            rep_ = np.asarray(ndimage.minimum(lab, comp_, np.arange(1, nc_ + 1)), lab.dtype)
            lab[U_] = rep_[comp_[U_] - 1]
            gone_ = np.isin(ids, np.setdiff1d(ids[nolens], rep_)); nolens[gone_] = False
            log(f"  one sea across coverage edges: {int(gone_.sum()) + nc_} floor-path marine domains that touch merged into {nc_}")
    nolens_ids = {int(i) for i in ids[nolens]}
    floor = np.full(z.shape, np.nan, np.float32); floor[dom] = floor_by[lab[dom] - 1]
    # A FLOOR-PATH SEA DOMAIN'S CEILING IS LOW TIDE (Hig's review of fix2c, 2026-11-01, Taylor Bay: a 180 m plateau at
    # -0.5..-0.9 m, 130-300 m from 1930 soundings at -8..-58, where the prior stood at -25).  The stage's level over such a
    # domain is a lidar's tide-time sea SURFACE (KBay 2023 at 2.9 m over Taylor Bay), and that level was the ceiling in the
    # soundings' fit and the guard: one shallow sigma-10 sounding lifted the spline over 2.8 m across 8 ha, the ceiling pass
    # then set 949 sites at 2.4 m -- 2.4 m ABOVE low tide -- which held the refit near them, and the guard cut what remained
    # to a 1-degree cone under 2.9 m.  The domain is the sea below low tide by construction (the water stage's compilation
    # sea, the low voids), so its bed lies under low tide everywhere: the ceiling here is low_tide_m, never a tide-time
    # surface.  A sounding above low tide - ceiling_margin_m in such a domain is dropped like one above its observed water.
    lt_ceil = np.full(z.shape, np.nan, np.float32)
    if nolens.any():
        lt_ceil[dom & np.isin(lab, list(nolens_ids))] = float(P["low_tide_m"])
    P["_lt_ceil"] = lt_ceil
    # THE FLOOR DESCENDS TO THE SOUNDINGS (Hig's review of fix2, 2026-11-01, Taylor Bay: "broad low-angle areas" -- a 28 deg
    # shore extended down only to low tide, then a near-flat at -1.4 m until the first 1930 line 150-300 m out, where the bed
    # drops to -12 and -58 within 100 m; the soundings there carry sigma 10, so the spline let the prior stand between them).
    # In a floor-path sea domain that holds soundings, the floor is lowered cell by cell to the soundings' KERNEL MEAN:
    # the shore's slope keeps descending to the soundings' level instead of stopping at low tide, and the spline refines
    # what the band and the interior then carry.  A domain with no soundings keeps the low-tide floor: a flat the tide
    # reaches is a flat until measured.
    # ...and that kernel mean IS the interior prior there (Hig's review of fix2c, 2026-11-01, Taylor Bay: the first cut
    # smoothed the NEAREST sounding's depth over a fixed 50 m and let the interior be a harmonic blend of the band's
    # edge -- the prior stood at -36 m under the 1930 line's -2.6 and -3.8 m soundings 250 m off the shore, the spline
    # had to lift +33 m at sigma 10 and carried the lift to the shore, through low tide, into the guard: a 180 m plateau).
    # The surface between soundings is their Gaussian mean with a bandwidth that GROWS with the distance to the nearest
    # sounding (floor_sound_smooth_m at a sounding -- 20 m, so a line's prior is the line; 50 m smeared a flank where the
    # bed drops 40 m in 100 m -- the distance itself beyond it): at a line it is the line's local mean, between two
    # lines a smooth ramp, far out the regional mean -- never a step at the midline, and never a depth
    # no sounding supports.  Equal weights: a point's sigma is its vertical uncertainty, for the spline's fit, not a
    # reason to prefer a neighbour's LOCATION (weighted 1/sigma^2, a sigma-2.5 line 100 m off outweighed a lone sigma-10
    # sounding on its own cell sixteenfold and the prior under a -2.6 m sounding stood at -16 m).  The spline then fits
    # the residual scatter about this surface, not a 30 m misfit.
    # ...BUT NEVER NARROWER THAN THE SOUNDINGS' OWN SPACING (Hig's review of the fix2e boxes, 2026-11-01, Taylor Bay:
    # "flat-bottomed circular basins around them, with the sounding at the center").  With the bandwidth 20 m AT a cast
    # and the cast's distance beyond, every cast owned a disc at its own depth out to a third of the way to its
    # neighbours, the ramp between two casts crowded into the middle third, and the spline -- which fits the residual to
    # the prior -- saw none at the casts and nothing to smooth.  The lakes have no discs because their prior is a smooth
    # basin and the thin-plate spline carries the soundings (Grewingk).  So the bandwidth's floor is the LOCAL HALF-GAP
    # between soundings (the largest empty-circle radius around the cell, read from the distance transform's maxima over
    # a window of the domain's typical gap, times sound_prior_gap_mult): a lone cast among casts 100 m apart is averaged
    # with its neighbours, a line is its local mean, lines 300 m apart ramp across the whole gap -- and the residual the
    # spline then fits is the real point-to-point scatter, spread as a minimum-curvature surface, not a plateau.
    sound_prior = np.full(z.shape, np.nan, np.float32)      # the kernel mean on floor-path sea domains with soundings
    sea_relax = np.full(z.shape, np.nan, np.float32)        # the ceiling the nearest casts allow (local profiles, 2026-11-02)
    contour_model = P.get("shore_model") == "contour" and nolens.any() and P.get("_transform") is not None      # shoreface.py (2026-11-03)
    if nolens.any() and P.get("points") and P.get("_transform") is not None and not contour_model:
        rc_, zs_ = _points_in_grid(P, P["_transform"], z.shape, med, lab)
        if rc_ is not None:
            mode_ = str(P.get("sea_prior", "profile"))
            s0 = max(float(P.get("floor_sound_smooth_m", 20.0)) / res, 1.0)
            scl = [s0 * 2 ** k for k in range(6)]                       # bandwidths in cells: 50 .. 1600 m at 2 m
            ls_ = np.log(scl)
            objs_ = ndimage.find_objects(lab)
            dl_ = lab[rc_[0], rc_[1]]; n_low = 0; a_low = 0; n_sp = 0; hg_m = []
            w_ = np.ones(zs_.shape, np.float64)
            # THE SEA'S PRIOR IS A LAKE'S (Hig's review of the fix2f boxes, 2026-11-01: "the surface shape is complicated,
            # still involving basins around the points, and totally unlike the way the soundings are interpolated in lakes").
            # A kernel mean of the casts, however wide its bandwidth, is a sum of bumps -- one at every cast, each the cast's
            # own anomaly spread over the bandwidth -- and the spline, which fits the residual to the prior, carries those
            # bumps through.  A lake's prior has none: one smooth profile of depth against distance from the shore, and the
            # thin-plate spline alone carries the soundings' structure as a minimum-curvature surface (Grewingk, the Hazels).
            # So the sea takes the same two parts: the profile is FITTED to the domain's casts -- the median depth of the casts
            # in each of up to sea_profile_bins quantile bins of distance from the shore, made non-decreasing (pool-adjacent-
            # violators: a flat bottom is allowed, a trough is the spline's), zero at the shore, flat beyond the last bin --
            # read at every cell's distance from the shore and smoothed over half the casts' spacing.  It is the floor and
            # the interior prior; the band's rim-slope extension descends to it; the spline fits the casts about it.
            # sea_prior "kernel" keeps the fix2f kernel mean (bandwidth max(20 m, the local half-gap, the distance to the
            # nearest cast)) for comparison.
            dsh_m = None
            if mode_ in ("profile", "local") and land_rim.any():
                # THE PROFILE'S SHORE IS THE LOW-TIDE LINE, NOT THE LAND (v6, 2026-11-02, Taylor Bay: measured from the land
                # rim, the casts' profile put the delta front's depth 300 m out onto the mud 3DEP flattened between the grass
                # flat and the first cast, and the spline rode its ceiling to hide it).  The 3DEP ground above MLLW in the
                # floor-path domains (the intertidal band's DEM cells, found here and reused by the band) joins the land rim
                # as the profile's origin for the casts and the cells alike: distance counts from where the flat meets the sea.
                shore0 = land_rim
                if P.get("intertidal_band") and P.get("band_ground", True) and P.get("ground_prior") and P.get("_transform") is not None and P.get("_crs") is not None:
                    fp0_ = dom & hole & np.isin(lab, list(nolens_ids))
                    if fp0_.any():
                        el0_, pz0_, n_el0 = _dem_band(fp0_, P, z.shape, res)
                        P["_dem_band0"] = (el0_, pz0_, n_el0); shore0 = land_rim | el0_
                        log(f"  the sea profile's shore: the land rim and the 3DEP ground above low tide in the floor-path domains ({int(el0_.sum()) * res * res / 1e4:.1f} ha)")
                        del el0_, pz0_
                    del fp0_
                dsh_m = ndimage.distance_transform_edt(~shore0, sampling=res).astype(np.float32)
            prof_log = []; loc_log = []
            for i in nolens_ids:
                sel = dl_ == i
                if not sel.any() or i - 1 >= len(objs_) or objs_[i - 1] is None:
                    continue
                sl = objs_[i - 1]; cells = lab[sl] == i
                rr_ = rc_[0][sel] - sl[0].start; cc_ = rc_[1][sel] - sl[1].start
                if mode_ in ("profile", "local"):
                    dep_k = float(P["low_tide_m"]) - zs_[sel]
                    l_k = dsh_m[sl][rr_, cc_] if dsh_m is not None else np.zeros(dep_k.shape, np.float32)
                    l_dom = dsh_m[sl] if dsh_m is not None else np.zeros(cells.shape, np.float32)
                    if mode_ == "local":                         # LOCAL profiles (2026-11-02): see _sea_profile_local
                        D_, prof_, st_, rx_ = _sea_profile_local(l_dom, cells, rr_, cc_, l_k, dep_k, res, P)
                        loc_log.append((int(i), st_))
                        if rx_ is not None:
                            sr_ = sea_relax[sl]; sr_[cells] = rx_[cells]; sea_relax[sl] = sr_
                    else:
                        D_, prof_ = _sea_profile(l_dom, l_k, dep_k, int(P.get("sea_profile_bins", 20)), int(P.get("sea_profile_min_n", 30)))
                    # smoothed over half the casts' spacing (the distance transform's creases, the bins' corners)
                    xy_k = np.column_stack([cc_, rr_]).astype(np.float64)
                    if len(xy_k) >= 2:
                        from scipy.spatial import cKDTree
                        gap_c = float(np.median(cKDTree(xy_k).query(xy_k, k=2)[0][:, 1]))
                    else:
                        gap_c = 0.0
                    sg_ = max(gap_c / 2.0, 30.0 / res)
                    ks = np.where(cells, float(P["low_tide_m"]) - D_, 0.0).astype(np.float64)
                    ks = _gsmooth(ks, sg_) / np.maximum(_gsmooth(cells.astype(np.float64), sg_), 1e-6)
                    hg_m.append(gap_c * res)
                    prof_log.append((int(i), int(sel.sum()), prof_))
                else:
                    num0 = np.zeros(cells.shape, np.float64); den0 = np.zeros(cells.shape, np.float64)
                    np.add.at(num0, (rr_, cc_), w_[sel] * zs_[sel]); np.add.at(den0, (rr_, cc_), w_[sel])
                    dn_ = ndimage.distance_transform_edt(den0 <= 0)          # cells to the nearest sounding
                    # the local half-gap: the distance transform's maxima (the midpoints between casts and between lines)
                    # over a window of twice the domain's typical gap, on an 8-cell lattice, smoothed to a field
                    gm_ = float(P.get("sound_prior_gap_mult", 1.0)); hg_ = None
                    if gm_ > 0:
                        c8 = 8; dc_ = dn_[::c8, ::c8]                              # distances stay in grid cells
                        near_ = dc_[(dc_ <= scl[-1]) & cells[::c8, ::c8]]        # domain cells within the widest bandwidth of a cast
                        gap_ = float(np.median(near_)) if near_.size else 0.0   # the typical half-gap
                        wn_ = int(max(3, round(4 * gap_ / c8))) | 1               # the window: two typical gaps either side
                        hg_ = ndimage.maximum_filter(dc_, size=wn_, mode="nearest")
                        hg_ = _gsmooth(hg_, max(gap_ / c8, 1.0))                   # a field, not steps between windows
                        hg_ = np.repeat(np.repeat(hg_, c8, axis=0), c8, axis=1)[:dn_.shape[0], :dn_.shape[1]] * gm_
                        hg_m.append(float(np.median(hg_[cells])) * res if cells.any() else 0.0)
                    x_ = np.log(np.clip(np.maximum(dn_, np.maximum(hg_, s0) if hg_ is not None else s0), scl[0], scl[-1]))
                    ks = np.full(cells.shape, np.nan, np.float64); prev = None
                    for k, sc in enumerate(scl):
                        dk = _gsmooth(den0, sc); nk = _gsmooth(num0, sc)
                        cur = np.where(dk > 1e-12, nk / np.maximum(dk, 1e-12), np.nan)
                        if k == 0:
                            ks[:] = cur
                        else:
                            t_ = np.clip((x_ - ls_[k - 1]) / (ls_[k] - ls_[k - 1]), 0, 1); use = x_ > ls_[k - 1]
                            mix = np.where(np.isfinite(prev), (1 - t_) * prev + t_ * cur, cur)
                            ks[use] = mix[use]
                        prev = cur
                ks = np.where(cells & np.isfinite(ks), ks, np.nan).astype(np.float32)
                if mode_ == "kernel":
                    # v8 (2026-11-02): the kernel mean of contradictory casts (a 1931 line's 2 ft and 15 fathoms within 100 m at
                    # a delta front) is a compromise wrong on both sides -- 17 m of water on the flat west of Taylor's island.
                    # The FIRST CAST IN EVERY DIRECTION bounds it as it bounds the local profile: a line of shallow casts says
                    # the flat inside it is no deeper than its edge; the shallowest first cast relaxes the ceiling (the cap).
                    eh_, el_ = _first_cast_env(cells, rr_, cc_, float(P["low_tide_m"]) - zs_[sel], res, P)
                    if eh_ is not None:
                        okb = cells & np.isfinite(ks) & np.isfinite(eh_)
                        n_kb = int((okb & (ks < float(P["low_tide_m"]) - eh_ - 1e-6)).sum())
                        ks = np.where(okb, np.maximum(ks, float(P["low_tide_m"]) - eh_), ks).astype(np.float32)
                        sr_ = sea_relax[sl]; okr = cells & np.isfinite(el_)
                        sr_[okr] = (float(P["low_tide_m"]) - np.maximum(el_[okr], 0.1)).astype(np.float32); sea_relax[sl] = sr_
                        log(f"    domain {int(i)}: kernel prior bounded by the first casts in every direction on {n_kb * res * res / 1e4:.1f} ha")
                fl = floor[sl]; low_ = cells & (ks < fl)
                if low_.any():
                    fl[low_] = ks[low_]; floor[sl] = fl; n_low += 1; a_low += int(low_.sum())
                sp = sound_prior[sl]; ok_ = cells & np.isfinite(ks)
                sp[ok_] = np.minimum(ks[ok_], fl[ok_]); sound_prior[sl] = sp; n_sp += int(ok_.sum())
            if mode_ == "kernel" and n_sp:
                # THE SUBTIDAL IS THE BASIN CODE'S (v7, 2026-11-02, Hig's review of v6 at Taylor Bay: "the soundings are
                # substantially off the bottom -- often deeper -- and there are clear near-sounding artifacts where the model
                # tried to bring the bottom into better alignment ... the profile extrapolation is leading to strong seams ...
                # use the existing basin code for deeper areas ... smooth contours rather than profiles"): the prior is the
                # casts' kernel mean at a bandwidth no narrower than sound_prior_gap_mult times their local half-gap -- smooth
                # contours through the casts, no disc at any cast -- and the ceiling's cone is relaxed to it (plus
                # kernel_relax_m of slack, the casts' own scatter), so a bed the casts sound shallow is never cut to the cone.
                # The intertidal band (its own fit) blends into this surface across the gap (band_blend_max_m).
                kr_ = float(P.get("kernel_relax_m", 1.0))
                sea_relax = np.where(np.isfinite(sound_prior), np.fmax(sound_prior + kr_, np.nan_to_num(sea_relax, nan=-np.inf)), sea_relax).astype(np.float32)
                log(f"  the sea's prior is the casts' kernel mean (smooth contours; bandwidth >= {float(P.get('sound_prior_gap_mult', 1.0)):g} x the local half-gap, "
                    f"floor {float(P.get('floor_sound_smooth_m', 20.0)):g} m); the ceiling's cone relaxed to it + {kr_:g} m on {int(np.isfinite(sea_relax).sum()) * res * res / 1e4:.1f} ha")
            if n_sp and mode_ in ("profile", "local"):
                log(f"  floor-path sea domains with soundings: the interior prior is a lake's -- the casts' median depth by distance from the shore, "
                    f"non-decreasing, smoothed over half their spacing (median {np.median(hg_m) if hg_m else 0:.0f} m)"
                    + (f", fitted LOCALLY: one profile per {float(P.get('sea_profile_anchor_m', 200.0)):g} m anchor from the casts within "
                       f"{float(P.get('sea_profile_local_m', 1500.0)):g} m (doubled, twice, below {int(P.get('sea_profile_min_casts', 40))} casts), "
                       f"blended over the four nearest anchors" if mode_ == "local" else "")
                    + f" -- on {n_sp * res * res / 1e6:.3f} km2; floor lowered to it in {n_low} domains ({a_low * res * res / 1e6:.3f} km2)")
                for i_, n_, pr_ in prof_log:
                    st_ = dict(next((s for j_, s in loc_log if j_ == i_), {}))
                    log(f"    domain {i_}: {n_} casts; "
                        + (f"{st_.get('anchors', 0)} anchors ({st_.get('widened', 0)} widened, {st_.get('global_', 0)} on the domain's profile); "
                           f"prior capped by the {int(P.get('sea_profile_cap_k', 8))} nearest casts on {st_.get('capped', 0) * 16 * res * res / 1e4:.1f} ha; domain profile " if st_ else "depth by shore distance ")
                        + ", ".join(f"{l_:.0f} m: {d_:.1f}" for l_, d_ in pr_))
            elif n_sp:
                log(f"  floor-path sea domains with soundings: the interior prior is the soundings' kernel mean (bandwidth max({float(P.get('floor_sound_smooth_m', 20.0)):g} m, "
                    f"the local half-gap between soundings x {float(P.get('sound_prior_gap_mult', 1.0)):g} (median {np.median(hg_m) if hg_m else 0:.0f} m), distance to the nearest sounding)) "
                    f"on {n_sp * res * res / 1e6:.3f} km2; floor lowered to it in {n_low} domains ({a_low * res * res / 1e6:.3f} km2)")
        del rc_, zs_
    log(f"  land rim {int(land_rim.sum())} cells, bed rim {int(bed_rim.sum())}, soundings inside {int(inside_obs.sum())}; "
        f"marine domains {int(marine.sum())}{' (tidal lens: bed at the observed water surface)' if lens else ''} / lakes {int((~marine).sum())}; rim slope p10/50/90 "
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
        # THE INTERTIDAL BAND (2026-11-02, Hig's review of fix2h: "cliff steps between flat upper intertidal and elevated
        # land"; the decomposition of the tidal band's steps in kj_r3c4 / r2c3 / r2c2: 108 / 38 / 43 km of terrace at exactly
        # +4.5 m and 74 / 28 / 32 km of rim cliff, both made by one rule -- a floor-path sea domain's ceiling was low tide
        # EVERYWHERE, so the bed was forced under MLLW at the land itself, whatever height the land or the DEM showed there,
        # and the DEM's ground between MLLW and void_ground_m inside the void was thrown away).  The band of such a domain IS
        # the intertidal: the rim extension's reach, and wherever the ground prior (3DEP) stands above low tide +
        # band_ground_min_m and below tide_max_m + 1 -- not its hydroflattened water (ground_water: 1 a lake, 2 the sea),
        # not a body's cells, not exactly flat -- where the prior is the DEM itself (data beats extrapolation).  Its ceiling
        # is the land it hangs from: the nearest land-rim cell's own height, capped at tide_max_m and never below low tide,
        # under an observed tide-time sheet where a survey saw one (a compilation body's level is low tide by construction
        # and says nothing), and never above the DEM + its sigma where the DEM stands -- with no slope demanded of it (a
        # flat is a flat).  The interior keeps low tide, and the guard's cone starts at the band's outer edge, not at the
        # land.  The DEM cells join the points below (band_ground), so the spline passes near them, never through them.
        P["_band_ground"] = None
        if nolens.any() and P.get("intertidal_band"):                      # off by default: fix2h's behaviour (the tide1 run turns it on)
            fp_ = dom & hole & np.isin(lab, list(nolens_ids))
            unobs_ = [int(i) for i, e in tover.items() if e.get("unobserved")]
            rim_top = np.fmax(float(P["low_tide_m"]), np.fmin(z[ri, ci], float(P.get("tide_max_m", 4.5)))).astype(np.float32)
            wz0 = P.get("_water_z")
            if wz0 is not None:
                obs_ = np.isfinite(wz0) & (~np.isin(lab, unobs_) if unobs_ else True)
                rim_top = np.where(obs_, np.fmin(rim_top, wz0), rim_top).astype(np.float32)
            n_rimband = int((band & fp_).sum()); n_el = 0; n_dem = 0
            if P.get("band_ground", True) and P.get("ground_prior") and P.get("_transform") is not None and P.get("_crs") is not None and fp_.any():
                rs_, cs_ = np.nonzero(fp_); rws = (int(rs_.min()), int(rs_.max()) + 1); cls_ = (int(cs_.min()), int(cs_.max()) + 1); del rs_, cs_
                pz = _read_on_grid(P["ground_prior"], P["_transform"], P["_crs"], z.shape, rws, cls_)
                el_ = fp_ & np.isfinite(pz) & (pz > float(P["low_tide_m"]) + float(P.get("band_ground_min_m", 0.3))) & (pz < float(P.get("tide_max_m", 4.5)) + 1.0)
                n_el = int(el_.sum())
                if P.get("ground_water"):
                    wm_ = _read_on_grid(P["ground_water"], P["_transform"], P["_crs"], z.shape, rws, cls_, nearest=True)
                    el_ &= ~(np.nan_to_num(wm_, nan=0.0) > 0.5); del wm_
                if P.get("_bodies") is not None:
                    el_ &= P["_bodies"][0] <= 0
                pz0 = np.nan_to_num(pz, nan=0.0)
                el_ &= (ndimage.maximum_filter(pz0, 5) - ndimage.minimum_filter(pz0, 5)) >= 0.02; del pz0
                n_dem = int(el_.sum())
                if n_dem:
                    band |= el_; prior[el_] = pz[el_]
                    rim_top = np.where(el_, np.fmin(rim_top, pz + float(P.get("band_ground_sigma_m", 1.0))), rim_top).astype(np.float32)
                    P["_band_ground"] = (el_, np.where(el_, pz, np.nan).astype(np.float32))
                del pz
            bfp = band & fp_
            lt_ceil[bfp] = rim_top[bfp]
            # ...and the INTERIOR's ceiling hangs from the band's outer edge (v3, 2026-11-02: with low tide as the interior's
            # ceiling the terrace only moved to the DEM's last cell -- 3DEP flattens the sea at 0 m, above MLLW, so beyond its
            # ground the flat was still forced under low tide at once): the guard's cone descends from the nearest band or
            # rim cell's own ceiling, so the drop from the band to the subtidal is the cone's, never a step.  Low tide is no
            # level of the ceiling any more; it remains the prior's floor.
            edge_ = bfp | land_rim
            if edge_.any():
                dE, (er_, ec_) = ndimage.distance_transform_edt(~edge_, return_indices=True, sampling=res)
                hv_ = np.where(bfp, lt_ceil, rim_top)[er_, ec_]
                inter_ = fp_ & ~bfp
                # v4: a RAMP from the edge's ceiling down to low tide at band_edge_deg (the subtidal cone below that is
                # the guard's, 1 deg): with the cone alone from the edge (v3) the spline's overshoot between a deep profile
                # prior and contradictory 1931 casts rode a ceiling at +1 m across 3DEP's flattened sea; the ramp lets the
                # interior ride where it did (under low tide) without a step at the band's edge.
                lt_ceil[inter_] = np.fmax(float(P["low_tide_m"]), hv_ - dE * np.tan(np.radians(float(P.get("band_edge_deg", 3.0)))))[inter_].astype(np.float32)
                P["_lt_dsh"] = np.where(bfp, 0.0, dE).astype(np.float32)
                del dE, er_, ec_, hv_, inter_
            P["_lt_ceil"] = lt_ceil
            n_up = int((bfp & (lt_ceil > float(P["low_tide_m"]) + 0.05)).sum())
            log(f"  intertidal band: {len(nolens_ids)} floor-path sea domains -- the rim extension's reach {n_rimband * res * res / 1e4:.1f} ha, the ground prior above low tide "
                f"+ {float(P.get('band_ground_min_m', 0.3)):g} m {n_dem * res * res / 1e4:.1f} ha (of {n_el * res * res / 1e4:.1f} ha eligible: its water, bodies and exact flats out); "
                f"the band hangs from the land rim's own height (capped at {float(P.get('tide_max_m', 4.5)):g} m, under an observed sheet, under the DEM + "
                f"{float(P.get('band_ground_sigma_m', 1.0)):g} m) on {int(bfp.sum()) * res * res / 1e4:.1f} ha, above low tide on {n_up * res * res / 1e4:.1f} ha (ceiling p10/50/90 "
                f"{np.round(np.percentile(lt_ceil[bfp], [10, 50, 90]), 2).tolist() if bfp.any() else '-'} m); the guard's cone starts at the band's outer edge")
            del rim_top, fp_
        # THE INTERTIDAL BAND (2026-11-02, Hig's review of fix2h: "cliff steps between flat upper intertidal and elevated
        # land"; the decomposition of the tidal band's steps in kj_r3c4 / r2c3 / r2c2: 108 / 38 / 43 km of terrace at exactly
        # +4.5 m and 74 / 28 / 32 km of rim cliff, both made by one rule -- a floor-path sea domain's ceiling was low tide
        # EVERYWHERE, so the bed was forced under MLLW at the land itself, whatever height the land or the DEM showed there,
        # and the DEM's ground between MLLW and void_ground_m inside the void was thrown away).  The band of such a domain IS
        # the intertidal: the rim extension's reach, and wherever the ground prior (3DEP) stands above low tide +
        # band_ground_min_m and below tide_max_m + 1 -- not its hydroflattened water (ground_water: 1 a lake, 2 the sea),
        # not a body's cells, not exactly flat -- where the prior is the DEM itself (data beats extrapolation).  Its ceiling
        # is the land it hangs from: the nearest land-rim cell's own height, capped at tide_max_m and never below low tide,
        # under an observed tide-time sheet where a survey saw one (a compilation body's level is low tide by construction
        # and says nothing), and never above the DEM + its sigma where the DEM stands -- with no slope demanded of it (a
        # flat is a flat).  The interior keeps low tide, and the guard's cone starts at the band's outer edge, not at the
        # land.  The DEM cells join the points below (band_ground), so the spline passes near them, never through them.
        P["_band_ground"] = None
        if nolens.any() and P.get("intertidal_band"):                      # off by default: fix2h's behaviour (the tide1 run turns it on)
            fp_ = dom & hole & np.isin(lab, list(nolens_ids))
            unobs_ = [int(i) for i, e in tover.items() if e.get("unobserved")]
            rim_top = np.fmax(float(P["low_tide_m"]), np.fmin(z[ri, ci], float(P.get("tide_max_m", 4.5)))).astype(np.float32)
            wz0 = P.get("_water_z")
            if wz0 is not None:
                obs_ = np.isfinite(wz0) & (~np.isin(lab, unobs_) if unobs_ else True)
                rim_top = np.where(obs_, np.fmin(rim_top, wz0), rim_top).astype(np.float32)
            n_rimband = int((band & fp_).sum()); n_el = 0; n_dem = 0
            if P.get("band_ground", True) and P.get("ground_prior") and P.get("_transform") is not None and P.get("_crs") is not None and fp_.any():
                if P.get("_dem_band0") is not None:
                    el_, pz, n_el = P["_dem_band0"]; el_ = el_ & fp_; P["_dem_band0"] = None
                else:
                    el_, pz, n_el = _dem_band(fp_, P, z.shape, res)
                n_dem = int(el_.sum())
                if n_dem:
                    band |= el_; prior[el_] = pz[el_]
                    rim_top = np.where(el_, np.fmin(rim_top, pz + float(P.get("band_ground_sigma_m", 1.0))), rim_top).astype(np.float32)
                    P["_band_ground"] = (el_, np.where(el_, pz, np.nan).astype(np.float32))
                del pz
            bfp = band & fp_
            lt_ceil[bfp] = rim_top[bfp]
            # ...and the INTERIOR's ceiling hangs from the band's outer edge (v3, 2026-11-02: with low tide as the interior's
            # ceiling the terrace only moved to the DEM's last cell -- 3DEP flattens the sea at 0 m, above MLLW, so beyond its
            # ground the flat was still forced under low tide at once): the guard's cone descends from the nearest band or
            # rim cell's own ceiling, so the drop from the band to the subtidal is the cone's, never a step.  Low tide is no
            # level of the ceiling any more; it remains the prior's floor.
            edge_ = bfp | land_rim
            if edge_.any():
                dE, (er_, ec_) = ndimage.distance_transform_edt(~edge_, return_indices=True, sampling=res)
                hv_ = np.where(bfp, lt_ceil, rim_top)[er_, ec_]
                inter_ = fp_ & ~bfp
                # v4: a RAMP from the edge's ceiling down to low tide at band_edge_deg (the subtidal cone below that is
                # the guard's, 1 deg): with the cone alone from the edge (v3) the spline's overshoot between a deep profile
                # prior and contradictory 1931 casts rode a ceiling at +1 m across 3DEP's flattened sea; the ramp lets the
                # interior ride where it did (under low tide) without a step at the band's edge.
                lt_ceil[inter_] = np.fmax(float(P["low_tide_m"]), hv_ - dE * np.tan(np.radians(float(P.get("band_edge_deg", 3.0)))))[inter_].astype(np.float32)
                P["_lt_dsh"] = np.where(bfp, 0.0, dE).astype(np.float32)
                del dE, er_, ec_, hv_, inter_
            P["_lt_ceil"] = lt_ceil
            n_up = int((bfp & (lt_ceil > float(P["low_tide_m"]) + 0.05)).sum())
            log(f"  intertidal band: {len(nolens_ids)} floor-path sea domains -- the rim extension's reach {n_rimband * res * res / 1e4:.1f} ha, the ground prior above low tide "
                f"+ {float(P.get('band_ground_min_m', 0.3)):g} m {n_dem * res * res / 1e4:.1f} ha (of {n_el * res * res / 1e4:.1f} ha eligible: its water, bodies and exact flats out); "
                f"the band hangs from the land rim's own height (capped at {float(P.get('tide_max_m', 4.5)):g} m, under an observed sheet, under the DEM + "
                f"{float(P.get('band_ground_sigma_m', 1.0)):g} m) on {int(bfp.sum()) * res * res / 1e4:.1f} ha, above low tide on {n_up * res * res / 1e4:.1f} ha (ceiling p10/50/90 "
                f"{np.round(np.percentile(lt_ceil[bfp], [10, 50, 90]), 2).tolist() if bfp.any() else '-'} m); the guard's cone starts at the band's outer edge")
            del rim_top, fp_
    # LAKES (2026-10-11, rough, for review): a basin shape instead of a flat floor.  Per lake
    # domain: L = greatest distance to shore, A = area, s = median rim slope.  Dmax two ways --
    # Hollister et al. 2011 (L * tan(s), times hollister_k ~0.5: basins are shallower than
    # their shores extrapolated) and the glacial-lake scaling of Huggel et al. 2002 / Cook &
    # Quincey 2015 (V = a*A^b, mean depth V/A, Dmax = mean / Vd) -- geometric mean, spread ->
    # sigma.  Shape: depth = Dmax*(l/L)^p with p from the target volume development Vd
    # (Hakanson: Vd = 2/((p+1)(p+2)); 0.5 is the flatter glacial norm, 1/3 a cone).  Shores
    # keep the band: the deeper of the rim-slope extension (capped at Dmax) and the shape.
    mids = {int(i) for i in ids[marine]}
    # a body the water stage (or Hig's map) calls a LAKE never gets the channel model, however elongated
    # (Wosnesenski Lake, 2026-10-18: 4 km by 1 km, elong 8.8 -- channel sections ran down its middle and left
    # ridges and plateaus around its islands)
    lake_body_ids = {i for i, e in bover.items() if e.get("kind") == "lake"} if bover else set()
    chan, chan_bed, chan_ws, chan_tab = channel_beds(lab, nw, P.get("_water_z"), res, P,
                                                     marine_ids=set() if lens else mids, skip_ids=((mids if lens else set()) | lake_body_ids) - rivers, whole_ids=rivers, log=log)
    lake_tab = []; lake_level = {}; lake_floor = {}
    tidal_cells = np.zeros(z.shape, bool)          # the tidal lens's cells (fill_mask 7)
    surface = np.zeros(z.shape, bool)              # observations judged to be the water surface (-> fill_surface.tif)
    lake_prior = np.full(z.shape, np.nan, np.float32); lake_sig = np.full(z.shape, np.nan, np.float32)
    lens_hi = np.full(z.shape, np.nan, np.float32)       # the observed surface where the lens was capped (above lens_max_m): the ceiling there
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
        lake_ids = [i for i in ids if not marine[i - 1] and i not in rivers]
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
        for i, e in bover.items():                 # the water bodies' level / depth (edits via water.py); a recipe's lake_dmax wins
            over[i] = {**e, **over.get(i, {})}
        snd_rc, snd_z = _points_in_grid(P, T_, z.shape, med, lab) if (P.get("points") and T_ is not None) else (None, None)
        # EVERY DOMAIN'S CELL COUNT OFF THE CHANNELS, IN ONE PASS (2026-11-06): the loop formed each domain's mask over the whole
        # grid before its area test, and lake_ids holds the dry voids too -- 65,284 domains in kj_r6c4 for 66 lakes, two
        # hours of a fill.  The count is the same number the mask's sum gave; a domain under lake_min_m2 is skipped before it.
        n_off = np.bincount(lab[~chan], minlength=int(lab.max()) + 1)
        for i in lake_ids:
            if int(n_off[i]) * res * res < P["lake_min_m2"]:
                continue
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
            if shore:
                # the STAGE's label says what is water here, not the class axis: a water-blind source's cells and a
                # prior's are classed ground / whatever they were, and Emerald Lake's domain was no lake for it (crop, run 19)
                wfrac = float(np.mean(np.isin(shore_lab[cells & hole], (2, 3)))) if (cells & hole).any() else 0.0
            else:
                wfrac = float(np.mean(cls[cells & hole] == classes["water"])) if (cells & hole).any() else 0.0
            if wfrac < P["lake_water_frac"]:
                continue
            sbar = float(np.median(srim[rl])); level = float(med[i - 1])
            # the lake level: the median of the returns off its surface where there are any (the
            # land rim's median stands on the shore -- 0.76 m above China Poot Lake's water)
            # Best is the water surface itself (water_z: the water-classed observation that won each
            # cell, written by the build); the returns classed ground near the shore read ~1 m high on
            # China Poot Lake (2026-10-12), and then island ground within 1.5 m of the water was dropped
            wz = wz_s
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
            is_lake_body = i in over and over[i].get("kind") == "lake"      # a known lake takes the lake estimate whatever its shape
            if elong > P["channel_elong"] and not is_lake_body:
                dmax = float(max(d_h, 0.5))
            sd = abs(d_h - d_g) / 2 + 5.0 if (elong <= P["channel_elong"] or is_lake_body) else max(d_h, 1.0)
            if snd_rc is not None:
                # THE SOUNDINGS SET THE FLOOR, NOT THE SHAPE (Hig, 2026-10-31, Lower Hazel Lake).  fix1 remapped the profile's
                # depths by rank onto the soundings' own quantiles and got a 198-step staircase: a 2.8 m drop at the waterline
                # (the shallowest sounding), terraces where the soundings cluster (24-27 m), and a lone 36 m sounding whose
                # 7 m gap was carried on to 43 m at the medial axis -- between 25 m lines.  "Primarily this should be a fit
                # to the points and an extrapolation of the shoreline with smooth surfaces where unconstrained": the smooth
                # profile keeps its shape and its shore gradient, the soundings say only how deep its bottom is -- the deepest
                # sounding plus a tail, the median spacing of the deepest tenth (0 for a flat bottom sampled many times, 0.3 m
                # for Upper Hazel; never the last gap) -- and the spline below fits the points themselves.  Dmax from a hundred
                # soundings replaces the Hollister / glacial estimate (an estimate for lakes nobody sounded).
                in_ = cells[snd_rc[0], snd_rc[1]]
                if int(in_.sum()) >= int(P["sound_hyps_min_n"]):
                    dep = np.sort(np.clip(level - snd_z[in_], 0.0, None))
                    top = dep[-max(int(np.ceil(dep.size * 0.1)), 3):]
                    tail = float(np.median(np.diff(top)))
                    d_s = float(dep[-1] + tail)
                    if d_s > 0.5:
                        log(f"  lake {i}: Dmax {d_s:.1f} m from its {dep.size} soundings (deepest {dep[-1]:.1f} + tail {tail:.2f}, the median spacing of "
                            f"the deepest {top.size}; median depth {np.median(dep):.1f}; estimates Hollister {d_h:.0f}, glacial {d_g:.0f})")
                        dmax = d_s; sd = max(tail, 0.5) + 1.0
            if i in over and over[i].get("dmax_m") is not None:
                dmax = float(over[i]["dmax_m"])
                log(f"  lake {i}: Dmax {dmax:.0f} m and level {level:.2f} m set by {'the recipe' if 'body' not in over[i] else 'water body ' + str(over[i]['body']) + ' (' + over[i]['kind'] + ')'} "
                    f"(estimates: Hollister {d_h:.0f}, glacial {d_g:.0f} m)")
            elif i in over and over[i].get("dmax_cap_m") is not None:
                # a PERCHED lagoon (water.py): the shore slope extrapolated, but no deeper than perched_dmax_m --
                # the tide reaches it, its sill is at its level, and nothing says it is a basin (Hig, 2026-10-15,
                # Seldovia: "very shallow - likely ~1 m ... consistent with extrapolating the shoreline")
                dmax = float(min(max(d_h, 0.3), over[i]["dmax_cap_m"])); sd = max(dmax, 0.5)
                log(f"  lake {i}: perched (water body {over[i]['body']}), Dmax {dmax:.1f} m = min(shore slope {d_h:.1f}, cap {over[i]['dmax_cap_m']:.1f}), level {level:.2f} m")
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
            hyp = over.get(i, {}).get("hypsometry")          # a recipe's or an edit's (the soundings' own was tried in fix1: terraces; see Dmax above)
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
            # a hand-set depth (an edit via water.py) or a perched body's cap is also a FLOOR for the modelled
            # bed: level - dmax - slack, applied after the conditioning below (the same floor water.level_raster
            # gives the class step) -- an outlet whose rim is the tidal channel's bed is a sill, not a ramp
            if i in over and (over[i].get("dmax_m") is not None or over[i].get("dmax_cap_m") is not None):
                lake_floor[i] = level - dmax - max(0.5, 0.5 * dmax)
            lake_sig[cells] = (P["lake_sigma_frac"] * sd * frac + 1.0).astype(np.float32)
            rr, cc = ndimage.center_of_mass(cells)
            lake_tab.append(dict(id=int(i), area_m2=int(A), L_m=round(L, 1), rim_slope_deg=round(sbar, 1), level_m=round(level, 2),
                                 dmax_set=bool(i in over),
                                 dmax_hollister_m=round(d_h, 1), dmax_glacial_m=round(d_g, 1), dmax_m=round(dmax, 1), q=round(q, 2), elong=round(elong, 1), row=int(rr), col=int(cc)))
        # THE TIDAL LENS: a marine domain's bed is its observed water surface -- per cell where a survey saw it
        # (water_z, smoothed over channel_ws_smooth_m: a creek's surface slopes), else the body's level, else the
        # median of the surface seen, else the rim median -- with sigma tidal_sigma_m growing with distance from
        # shore.  The observations under it (multibeam, green-lidar bed, soundings) condition it below like any
        # lake's; nothing else deepens it (Hig, 2026-10-16: "conservatively flat assumptions to start with").
        if lens and mids:
            wz_ = P.get("_water_z")
            sgt = max(P["channel_ws_smooth_m"] / res, 1.0)
            objs = ndimage.find_objects(lab)
            n_t = 0; n_hi = 0; n_deep = 0
            # A SURFACE OVER A CAST IS NO FLAT (2026-11-06, Hig: "patches of bad coastal data ... in the nearshore ocean near Anchor
            # Point", 59.72032,-151.83655): the lens is a tide flat's -- a bed at the surface a survey saw -- and off Anchor Point
            # Kenai 2008's water class at its 1.9 m stand lies over NOS casts at -4..-6 m, 32 within 300 m; once the joint's strays
            # were gone the whole sea there was a lens domain and the fill put the bed at +1.7 m in 6 m of water.  Per cell: where
            # the shallowest cast within lens_cast_m stands lens_cast_dz_m or more under the surface, the surface is water over a
            # bed, a ceiling, and the cell takes the floor path with the casts' conditioning (Taylor Bay's unobserved body, pass
            # 12, is the same judgement for a whole domain).  All the files' casts count, inside the domains or on the bed beside.
            hc_ = None
            if P.get("points") and T_ is not None and float(P.get("lens_cast_m", 150.0)) > 0:
                import csv as _csv
                hz_ = np.full(z.shape, -np.inf, np.float32); dzp_ = P.get("points_dz_m") or 0.0; n_pt = 0
                for path_ in _points_files(P):
                    with open(path_) as fh_:
                        for row_ in _csv.DictReader(fh_):
                            if row_.get("bound") or row_.get("z") in (None, ""):
                                continue
                            cc_, rr_ = ~T_ * (float(row_["x"]), float(row_["y"])); rr_, cc_ = int(rr_), int(cc_)
                            if 0 <= rr_ < z.shape[0] and 0 <= cc_ < z.shape[1]:
                                d__ = float(dzp_.get(row_.get("survey", ""), 0.0)) if isinstance(dzp_, dict) else float(dzp_)
                                hz_[rr_, cc_] = max(hz_[rr_, cc_], float(row_["z"]) + d__); n_pt += 1
                if n_pt:
                    rc_ = int(round(float(P.get("lens_cast_m", 150.0)) / res))
                    hc_ = ndimage.maximum_filter(hz_, size=2 * rc_ + 1)
                del hz_
            for i in sorted(mids):
                sl = objs[i - 1] if i - 1 < len(objs) else None
                if sl is None:
                    continue
                cells = (lab[sl] == i) & ~chan[sl]
                if not cells.any() or i in nolens_ids:
                    continue                             # nolens: the floor path (above), not a plate at a level nobody observed
                w_ = wz_[sl] if wz_ is not None else None
                v_ = w_[cells] if w_ is not None else np.zeros(0); v_ = v_[np.isfinite(v_)]
                lv_ = tover.get(i, {}).get("level_m")
                if lv_ is None and v_.size < 20:
                    continue                             # no observed surface, no tidal body: the floor path (nolens, above)
                if lv_ is None:
                    lv_ = float(np.median(v_))
                if w_ is not None and v_.size >= 20:
                    known = cells & np.isfinite(w_)
                    num = ndimage.gaussian_filter(np.where(known, w_, 0.0).astype(np.float64), sgt)
                    den = ndimage.gaussian_filter(known.astype(np.float64), sgt)
                    s_ = np.where(den > 1e-3, num / np.maximum(den, 1e-3), lv_).astype(np.float32)
                else:
                    s_ = np.full(cells.shape, lv_, np.float32)
                if P.get("lens_max_m") is not None:
                    # a surface above lens_max_m (MHHW) is high water over everything and says nothing of the bed (2026-10-31,
                    # Hig: a flat at 4.88 m in the upper bay's intertidal): those cells are left to the prior between the rest
                    hi_ = cells & (s_ > float(P["lens_max_m"]))
                    if hi_.any():
                        # ...and the ceiling on those cells is THAT surface, not the domain's level (2026-10-31, Hig's review of
                        # fix1: the upper-bay flat at 4.5-4.9 m was left to the prior, then the guard clipped 4.5 km2 of it to one
                        # plane 0.1 m under the domain's 3.53 m median -- "a broad horizontal incised level ... with sharp edges")
                        lens_hi[sl][hi_] = s_[hi_]
                        n_hi += int(hi_.sum()); cells = cells & ~hi_
                        if not cells.any():
                            continue
                if hc_ is not None:
                    deep_ = cells & (hc_[sl] > -1e8) & (hc_[sl] <= s_ - float(P.get("lens_cast_dz_m", 2.0)))
                    if deep_.any():
                        lens_hi[sl][deep_] = s_[deep_]           # the surface stays a ceiling over the bed the floor path models
                        n_deep += int(deep_.sum()); cells = cells & ~deep_
                        if not cells.any():
                            continue
                lp = lake_prior[sl]; ls = lake_sig[sl]
                lp[cells] = s_[cells]
                ls[cells] = (P["tidal_sigma_m"] + P["sigma_rim_m"] * dshore[sl][cells] / 100.0).astype(np.float32)
                tidal_cells[sl] |= cells
                lake_level[i] = float(lv_); n_t += 1
            log(f"  tidal lens: {n_t} marine domains at their observed water surface, {int(tidal_cells.sum()) * res * res / 1e6:.3f} km2 "
                f"(sigma {P['tidal_sigma_m']} m at the shore + {P['sigma_rim_m']} m per 100 m)"
                + (f"; {int(n_hi) * res * res / 1e4:.2f} ha of surface above lens_max_m {P['lens_max_m']} m left to the prior" if n_hi else "")
                + (f"; {int(n_deep) * res * res / 1e4:.2f} ha of surface over a cast {float(P.get('lens_cast_dz_m', 2.0)):g} m or more below it within {float(P.get('lens_cast_m', 150.0)):g} m: water over a bed, the floor path" if n_deep else ""))
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
        lvl[tidal_cells] = lake_prior[tidal_cells]                              # the tidal lens: the surface itself (a creek slopes)
        at_surface = lk & inside_obs & np.isfinite(z) & (cls != classes["bed"]) & (z < lvl + tol)
        if shore:
            # the surface: at or above the level (a solid return BELOW the level inside the water is the bed, kept)
            at_surface = lk & inside_obs & np.isfinite(z) & (cls != classes["bed"]) & np.isin(shore_lab, (2, 3)) & ~(z < lvl - tol)
            if prior_won is not None:
                at_surface |= lk & inside_obs & prior_won & np.isin(shore_lab, (2, 3))      # a prior's value under the water is no bed
            log(f"  shoreline stage: {int(at_surface.sum())} observed cells inside the water are its surface or float on it -- not evidence of the bed")
        # ...but ground at the level that runs on to dry land beyond the lake is SHORE, not water (Hig, 2026-10-14:
        # ponds "carved" into Grewingk's outwash -- flat gravel with good returns, within tol of a creek pond's
        # level, went surface -> water class at the next build -> a larger pond, every pass).  Stray returns off calm
        # water lie out on the water: a patch whose edge borders more dry land (beyond the domain) than water is
        # shore and stays ground.  (A seam of calm-water returns across a lake touches the shore only at its ends.)
        if "water" in classes and not shore:
            cand_ = at_surface & (cls != classes["water"])
            dry = np.isfinite(z) & ~lk & (cls != classes["water"]) & (cls != classes.get("ice", -1))
            wet = lk & ~cand_ & ~(inside_obs & (z >= lvl + tol))
            cl_, cn_ = ndimage.label(cand_)
            if cn_:
                ring = ndimage.binary_dilation(cand_) & ~cand_
                own = ndimage.grey_dilation(cl_, size=3)            # the patch each ring cell borders
                idx = np.arange(1, cn_ + 1)
                n_dry = ndimage.sum(ring & dry, own, idx); n_wet = ndimage.sum(ring & wet, own, idx)
                kept_ = np.isin(cl_, idx[n_dry > n_wet])         # (was named `shore`: it shadowed the stage flag, 2026-11-02)
                if kept_.any():
                    log(f"  ground at the water level joined to dry land kept as shore: {int(kept_.sum())} cells")
                at_surface &= ~kept_
        # and a speck of "ground" out on the water (< lake_islet_m2, the survey layers' islets rule) is
        # a stray return, not an island: one 2 m Grewingk cell 0.3 m above the water pulled the
        # bed up 30 m around it (2026-10-12).  Larger islands are kept.
        sp = lk & inside_obs & np.isfinite(z) & (cls != classes["bed"]) & ~at_surface
        sl, sn = ndimage.label(sp) if not shore else (None, 0)
        if sn:
            ssz = ndimage.sum(sp, sl, np.arange(1, sn + 1)) * res * res
            specks = np.isin(sl, np.flatnonzero(ssz < P["lake_islet_m2"]) + 1)
            at_surface |= specks
            # ...and an "island" whose median stands less than lake_island_dz_m clear of the water is the water (Hig, 2026-10-14:
            # a 112 m2 sliver of 2021 surface 0.4 m above Grewingk's water pinned a 9,000 m2 flat shallow bed below a
            # cliff that should run deep) -- the threshold make_nohf_dtm uses to find islands in the returns.  Not a
            # patch that runs on to dry land (shore).
            idx_ = np.arange(1, sn + 1)
            top = ndimage.median(np.where(sp, z - lvl, np.nan), sl, idx_)    # median: one noisy 1.1 m cell kept the SE sliver (v6)
            low_ = np.asarray(top) < P["lake_island_dz_m"]
            if low_.any():
                ring_ = ndimage.binary_dilation(sp) & ~sp
                own_ = ndimage.grey_dilation(sl, size=3)
                dry_ = np.isfinite(z) & ~lk & (cls != classes.get("water", -1)) & (cls != classes.get("ice", -1))   # a glacier is not a shore
                n_dry_ = ndimage.sum(ring_ & dry_, own_, idx_); n_wet_ = ndimage.sum(ring_ & lk & ~sp, own_, idx_)
                low_ &= ~(n_dry_ > n_wet_)
                lowi = np.isin(sl, idx_[low_]) & ~specks
                if lowi.any():
                    log(f"  low 'islands' (< {P['lake_island_dz_m']} m above the water) taken as surface: {int(low_.sum())}, {int(lowi.sum())} cells")
                    at_surface |= lowi
        # FLOATING ICE (Hig, 2026-10-20, Grewingk Lake): what stands above a lake's water is not always an island.
        # Grewingk 2021's returns off icebergs -- mostly classed water, some ground -- stood 0.3-15 m above the
        # lake, passed every rule above as "islands", and the bed was then held just under the water at each:
        # 123 needles rising a median 14 m off the lake floor, 113 under a lump in the water surface. Two rules,
        # for lakes only (a channel's or the tidal lens's level is a sloping surface of its own):
        #   * a water-classed observation out on a lake is the surface or something floating on it, never bed;
        #   * a raised LAND patch is an island only when corroborated -- a second survey layer saw land on at
        #     least floating_ice_corr of it, or a hand-mapped island touches it. A berg drifts: it is seen once.
        if P.get("floating_ice", True) and not shore:
            lake_only = lk & ~chan & ~tidal_cells
            raised = lake_only & inside_obs & np.isfinite(z) & (cls != classes["bed"]) & ~at_surface
            if "ice" in classes:
                raised &= cls != classes["ice"]                      # the ice rule below has its own say
            wat_r = raised & (cls == classes.get("water", -1))
            land_r = raised & ~wat_r
            drop = wat_r.copy()
            n_land_drop = 0
            if land_r.any():
                ln_ = P.get("_land_n"); mi_ = P.get("_mapped_island")
                rl_, rn_ = ndimage.label(land_r, structure=np.ones((3, 3), bool))
                idr = np.arange(1, rn_ + 1)
                sz_ = ndimage.sum(land_r, rl_, idr)
                corr = np.zeros(rn_, bool); corr_isl = np.zeros(rn_, bool); ml_ = P.get("_mapped_lake")
                if ln_ is not None:
                    corr |= np.asarray(ndimage.sum(land_r & (ln_ >= 2), rl_, idr)) >= P["floating_ice_corr"] * np.asarray(sz_)
                if mi_ is not None:
                    corr_isl = np.asarray(ndimage.maximum(ndimage.binary_dilation(mi_, iterations=2), rl_, idr)) > 0
                    corr |= corr_isl
                if ln_ is None and mi_ is None and ml_ is None:
                    corr[:] = True                                   # no evidence to judge by: keep the old behaviour
                if ml_ is not None:
                    # the trusted mapped shoreline arbitrates here too (the domain closure above): inside, tall raised
                    # land is floating or ice-cored unless a mapped island; outside, it is the shore
                    in_, out_, tall_ = _mapped_lake_sides(ml_, rl_, idr, res, P, z, lvl)
                    corr = np.where(in_ & tall_, corr_isl, np.where(out_, True, corr))
                lost = np.isin(rl_, idr[~corr])
                drop |= lost; n_land_drop = int((~corr).sum())
                if n_land_drop:
                    big_ = np.sort(np.asarray(sz_)[~corr])[::-1][:5] * res * res
                    log(f"  raised land on lakes with no second survey or mapped island (floating ice): {n_land_drop} patches, "
                        f"{int(lost.sum())} cells (largest m2 {np.round(big_).astype(int).tolist()}); kept as islands: {int(corr.sum())}")
            if wat_r.any():
                log(f"  water-classed observations standing above a lake (the surface or floating ice) dropped as evidence: {int(wat_r.sum())} cells")
            at_surface |= drop
        # ice inside a lake (bergs, the ragged fringe of a calving face that the closing wraps into the
        # domain) is not evidence of the bed: kept as an "island" at its surface height it pinned the bed
        # 20-30 m ABOVE the water along Grewingk's face (2026-10-13).  Dropped when the face is free.
        if P.get("ice_face") == "free" and "ice" in classes and not shore:
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
    sound_sv = []                                  # each sounding's survey (the despike works per survey)
    bounds = []                                    # lower bounds (row, col, x, y, z_max, sigma): bed at or below z
    if P.get("points"):
        import csv
        T = P.get("_transform")
        nbound = 0; n_above = 0; above_sv = {}; wz_in = P.get("_water_z")
        if P.get("_lt_ceil") is not None and P.get("intertidal_band"):
            lc_ = P["_lt_ceil"]                                 # a floor-path sea domain's own ceiling: the intertidal band hangs from the land, the interior lies under low tide (2026-11-02)
            wz_in = np.where(np.isfinite(lc_), lc_, wz_in if wz_in is not None else np.nan).astype(np.float32)
        elif wz_in is not None and P.get("_lt_ceil") is not None:
            wz_in = np.fmin(wz_in, P["_lt_ceil"])              # a floor-path sea domain: low tide is the ceiling (above)
        for _pf in _points_files(P):
          with open(_pf) as fh:
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
                if contour_model and int(lab[rr, cc]) in nolens_ids:     # shoreface.py fits these
                    continue
                if row.get("z") not in (None, ""):
                    dz_ = P.get("points_dz_m") or 0.0
                    # a dict is per SURVEY (2026-10-18, the whole bay: the Grewingk sounding levels are tied to the
                    # unshifted 2021 lidar, +0.36 m; Riordan's are not) -- a number shifts every point, as at Grewingk
                    dz_ = float(dz_.get(row.get("survey", ""), 0.0)) if isinstance(dz_, dict) else float(dz_)
                    zz = float(row["z"]) + dz_
                else:
                    zz = float(med[lab[rr, cc] - 1]) - float(row["depth"])
                sg = float(row["sigma"]) if row.get("sigma") not in (None, "") else P["points_sigma_m"]
                if is_bound:
                    bounds.append((rr, cc, x, y, zz, sg)); nbound += 1
                    continue
                # A SOUNDING ABOVE THE WATER OBSERVED OVER IT IS NOT THE BED (2026-10-31, upbay_t fix2: the 1980 Bear Cove
                # survey's drying heights at 5.3-5.5 m fall in tidal creeks the lidar saw at 5.26 m -- the flat has moved
                # since -- and the spline lifted the creek beds through the water into the guard, 5,000 shelf cells): the
                # stage's level is an inequality on the bed (rule 3), so a point standing above level - ceiling_margin_m is
                # dropped here rather than fitted and clipped.  A point in a cell without a level is kept.
                if wz_in is not None and np.isfinite(wz_in[rr, cc]) and zz > float(wz_in[rr, cc]) - float(P["ceiling_margin_m"]):
                    n_above += 1; above_sv[row.get("survey", "")] = above_sv.get(row.get("survey", ""), 0) + 1
                    continue
                sound.append((rr, cc, x, y, zz, sg)); pts[rr, cc] = True; sound_sv.append(row.get("survey", ""))
        if n_above:
            log(f"  soundings above the water observed over them (level - {float(P['ceiling_margin_m']):g} m) dropped: {n_above} -- "
                + ", ".join(f"{k} {v}" for k, v in sorted(above_sv.items(), key=lambda kv: -kv[1])[:8]))
        # DESPIKE (Hig, 2026-10-20, Grewingk Lake): a single sounding far SHALLOWER than its own survey's
        # neighbours -- the sounder catching a berg's keel, a fish, a side lobe -- is not the bed; the spline
        # honoured each (misfit 1 m) and built a cone to it (17 of 35 deep bumps sat on a 2017 sounding 5-20 m
        # above its track). Judged against a plane through the same survey's soundings 5..points_despike_r_m
        # away, so a steady slope is not a spike; only the shallow side (a second-bottom echo is a different
        # problem). Needs points_despike_n neighbours; dropped, not down-weighted.
        if sound and P.get("points_despike_m"):
            from scipy.spatial import cKDTree
            S_ = np.array([(t[2], t[3], t[4]) for t in sound]); sv_ = np.array(sound_sv)
            keep_ = np.ones(len(sound), bool); per = {}
            for name in np.unique(sv_):
                ii = np.flatnonzero(sv_ == name)
                if ii.size < P["points_despike_n"] + 1:
                    continue
                tr_ = cKDTree(S_[ii, :2])
                for a_, nb in zip(ii, tr_.query_ball_point(S_[ii, :2], P["points_despike_r_m"])):
                    nb = ii[np.asarray(nb, int)]
                    dxy = S_[nb, :2] - S_[a_, :2]
                    nb = nb[np.hypot(dxy[:, 0], dxy[:, 1]) > 5.0]
                    if nb.size < P["points_despike_n"]:
                        continue
                    d_ = S_[nb, :2] - S_[a_, :2]
                    G = np.c_[np.ones(nb.size), d_]
                    cf, *_ = np.linalg.lstsq(G, S_[nb, 2], rcond=None)
                    res_n = S_[nb, 2] - G @ cf
                    scale = 1.4826 * np.median(np.abs(res_n - np.median(res_n)))
                    if S_[a_, 2] - cf[0] > max(P["points_despike_m"], 3.0 * scale):
                        keep_[a_] = False; per[name] = per.get(name, 0) + 1
                # ...and the DEEP side (Hig's review of fix2, 2026-11-01, Koyuktoolik lagoon: one 1980 sounding at -35.6 m with every
                # neighbour within 130 m at -1.8..-3.8, honoured at sigma 0.8 and spun into a cone): a lone sounding standing more
                # than points_despike_deep_m below the plane of its own survey's neighbours within points_despike_deep_r_m (a wider
                # ring: a 1980 line logs a fix every 20-50 m) is a blunder of the other sign -- a misread fathom, a position slip
                # onto a channel -- and is dropped the same way.
                dr_ = float(P.get("points_despike_deep_r_m", 150.0)); dm_ = float(P.get("points_despike_deep_m", 10.0))
                for a_, nb in zip(ii, tr_.query_ball_point(S_[ii, :2], dr_)):
                    if not keep_[a_]:
                        continue
                    nb = ii[np.asarray(nb, int)]
                    dxy = S_[nb, :2] - S_[a_, :2]
                    nb = nb[(np.hypot(dxy[:, 0], dxy[:, 1]) > 5.0) & keep_[nb]]
                    if nb.size < P["points_despike_n"]:
                        continue
                    d_ = S_[nb, :2] - S_[a_, :2]
                    G = np.c_[np.ones(nb.size), d_]
                    cf, *_ = np.linalg.lstsq(G, S_[nb, 2], rcond=None)
                    res_n = S_[nb, 2] - G @ cf
                    scale = 1.4826 * np.median(np.abs(res_n - np.median(res_n)))
                    if cf[0] - S_[a_, 2] > max(dm_, 3.0 * scale):
                        keep_[a_] = False; per[name + " (deep)"] = per.get(name + " (deep)", 0) + 1
            if (~keep_).any():
                for t in np.asarray(sound, dtype=object)[~keep_]:
                    pts[t[0], t[1]] = False
                sound = [t for t, k in zip(sound, keep_) if k]
                for t in sound:
                    pts[t[0], t[1]] = True
                log(f"  soundings shallower than their own track by > {P['points_despike_m']} m (a keel, a fish, a side lobe) dropped: "
                    f"{int((~keep_).sum())} -- " + ", ".join(f"{k} {v}" for k, v in sorted(per.items())))
        # THE INTERTIDAL BAND'S DEM CELLS ARE DATA (2026-11-02; the band above): one site per points_rim_step_m block at
        # band_ground_sigma_m.  A parameter never beats an observation here: the DEM is data with a sigma, the shore profile
        # only the prior.
        n_bg = 0
        if P.get("_band_ground") is not None and T is not None:
            el_, pzb = P["_band_ground"]
            k_b = max(int(round(P["points_rim_step_m"] / res)), 1)
            orr, occ = np.nonzero(el_ & hole)
            if orr.size:
                _, first = np.unique((orr // k_b) * 1_000_003 + occ // k_b, return_index=True)
                orr, occ = orr[first], occ[first]
                sgb = float(P.get("band_ground_sigma_m", 1.0))
                for rr, cc in zip(orr.tolist(), occ.tolist()):
                    sound.append((rr, cc, T.c + (cc + 0.5) * res, T.f - (rr + 0.5) * res, float(pzb[rr, cc]), sgb)); pts[rr, cc] = True; sound_sv.append("ground_prior_band")
                n_bg = int(orr.size)
            log(f"  the intertidal band's DEM cells as points: {n_bg} sites at sigma {float(P.get('band_ground_sigma_m', 1.0)):g} m, one per {float(P['points_rim_step_m']):g} m block")
            P["_band_ground"] = None; del el_, pzb
        log(f"  sparse points: {len(sound)} inside domain holes from {P['points']}" + (f" (of which {n_bg} the DEM in the intertidal band)" if n_bg else "")
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
    # a floor-path sea domain with soundings: the interior prior is their kernel mean (above), fixed for the harmonic
    sp_ = interior & np.isfinite(sound_prior)
    if sp_.any():
        prior[sp_] = sound_prior[sp_]; fixed_val[sp_] = sound_prior[sp_]
    fixed = np.isfinite(fixed_val)
    unk_ = interior & ~sp_
    if unk_.any():
        # the band's inner edge sits at the floor, so the interior is harmonic between the
        # floor and whatever bed rim (bathymetry hull) bounds the domain.  The sounding cells
        # are unknowns here too: left out, a pocket of hole ringed by sounding lines had no
        # boundary at all (a singular system, and the solve wandered to +30 m, 2026-10-10)
        sol = harmonic(fixed_val, fixed, unk_, coarse=P["coarse"], iters=P["cg_iters"])
        prior[unk_] = sol[unk_].astype(np.float32)
    interior &= hole
    # RESIDUAL conditioning by the soundings inside the domain
    fill = prior.copy()
    mask = np.zeros(z.shape, np.uint8); mask[band] = 1; mask[interior] = 2; mask[margin & hole] = 4; mask[lk & hole] = 5
    mask[chan & hole] = 6
    mask[tidal_cells & hole] = 7
    cond = np.zeros(z.shape, bool)
    # the residual at the RIM is the rim's own misfit to the prior beside it (2026-10-11; it was 0,
    # so the fill met the rim at the prior's level -- a step wherever the shore stood off the lake
    # level or the nearest-rim extension): harmonic, the misfit fades smoothly into the domain
    # ...and the prior a residual is taken against is the HOLE's, for the rim and the soundings inside alike (2026-10-29,
    # Hig's review of Seldovia: a one-cell hole in the 2009 multibeam at -14.1 m filled to -21.3 m -- the soundings round
    # it took their residual against the prior on their own cells, ~7 m above the hole's, and the hole added it to its
    # own: wherever the prior steps between a hole and the measurements beside it, the fill stepped by as much)
    pin = dom & hole & np.isfinite(prior)
    if not pin.any():
        pin = dom & np.isfinite(prior)
    pi = pj = None
    if pin.any() and (rim.any() or inside_obs.any()):
        _, (pi, pj) = ndimage.distance_transform_edt(~pin, return_indices=True)
    rim_res = np.zeros(z.shape, np.float64)
    if rim.any() and pi is not None:
        rr_, cc_ = np.nonzero(rim)
        zr_ = z[rr_, cc_].astype(np.float64)
        if lk.any():
            # a lake's shore is AT the water: a cliff standing 20-30 m over Grewingk's south-east corner
            # (v4, 2026-10-14) pinned the bed beside it 5-15 m above the lake.  Rim cells beside a lake count
            # at most at its level (lvl: the lake's own level, a channel's sloping surface).
            lv_ = lvl[pi[rr_, cc_], pj[rr_, cc_]]
            zr_ = np.where(np.isfinite(lv_), np.minimum(zr_, lv_), zr_)
        rim_res[rr_, cc_] = zr_ - prior[pi[rr_, cc_], pj[rr_, cc_]]
    if inside_obs.any() or np.abs(rim_res).max() > 0:
        rz = np.zeros(z.shape, np.float64)
        if pi is not None:
            oi_, oj_ = np.nonzero(inside_obs)
            rz[oi_, oj_] = z[oi_, oj_] - np.nan_to_num(prior[pi[oi_, oj_], pj[oi_, oj_]], nan=0.0)
            del oi_, oj_
        else:
            rz[inside_obs] = z[inside_obs] - np.nan_to_num(prior[inside_obs], nan=0.0)
        rfix = inside_obs | rim
        rval = np.where(inside_obs, rz, np.nan_to_num(rim_res))
        del pi, pj
        # AN OBSERVATION STANDS HALFWAY TO THE NEXT (Hig's review of cm18, 2026-11-06, upper Grewingk Lake: "little spikes beneath
        # them or holes above them ... given the sparse points in this area, our best guess should be that these points are
        # correct -- even if they imply cliffs").  A lone observed cell (no observed 8-neighbour: a sounding's cell, a sonar
        # grid's stray) pinned the harmonic residual at ONE cell -- a log cusp, a needle 20 % of the residual high in the next
        # cell (9.6 m beside the 18 higman 2019-21 soundings).  Its residual now holds over a disc out to half the distance to
        # the nearest other observed cell, at most lone_pin_max_m: along a sounding track the discs join the fixes into a
        # line; a lone sounding is a plateau of its own depth with the harmonic's bounded descent from its edge -- a crater, as
        # steep as the neighbours ask, never an overshoot (the thin plate through the same cells as sites overshot by 78 m on
        # Grewingk: the harmonic's maximum principle is what keeps a 50 m residual honest).  Only hole cells not otherwise
        # fixed; the rim and the other observations keep their own values.
        nb_ = ndimage.convolve(inside_obs.astype(np.int8), np.ones((3, 3), np.int8), mode="constant")
        lone = inside_obs & (nb_ <= 1); del nb_
        if lone.any() and float(P.get("lone_pin_max_m", 0.0)) > 0:
            from scipy.spatial import cKDTree
            oi_, oj_ = np.nonzero(inside_obs); li_, lj_ = np.nonzero(lone)
            d2_, _ = cKDTree(np.column_stack([oi_, oj_])).query(np.column_stack([li_, lj_]), k=2)
            rho_ = np.minimum(0.5 * d2_[:, 1], float(P["lone_pin_max_m"]) / res)        # cells
            free_ = dom & hole & ~rfix; n_disc = 0
            for i_, j_, r_ in zip(li_.tolist(), lj_.tolist(), rho_.tolist()):
                k_ = int(np.floor(r_))
                if k_ < 1:
                    continue
                a0, a1, b0, b1 = max(i_ - k_, 0), min(i_ + k_ + 1, z.shape[0]), max(j_ - k_, 0), min(j_ + k_ + 1, z.shape[1])
                yy, xx = np.mgrid[a0:a1, b0:b1]
                dsk = free_[a0:a1, b0:b1] & (((yy - i_) ** 2 + (xx - j_) ** 2) <= r_ * r_)
                if dsk.any():
                    rval[a0:a1, b0:b1][dsk] = rval[i_, j_]; rfix[a0:a1, b0:b1][dsk] = True; free_[a0:a1, b0:b1][dsk] = False
                    n_disc += int(dsk.sum())
            log(f"  lone observed cells: {int(lone.sum())}; their value held halfway to the next observation (<= {float(P['lone_pin_max_m']):g} m): "
                f"{n_disc} cells, disc radius p50/p90 {np.percentile(rho_ * res, [50, 90]).round(1)} m")
            del oi_, oj_, li_, lj_, d2_, rho_, free_
        r = harmonic(rval, rfix, dom & hole, coarse=P["coarse"], iters=P["cg_iters"])
        ok = (dom & hole) & np.isfinite(r)
        fill[ok] = (prior[ok] + r[ok]).astype(np.float32)
        if inside_obs.any():
            dso = ndimage.distance_transform_edt(~inside_obs, sampling=res)
            cond = ok & (dso <= 3 * P["cond_m"])
            mask[cond] = 3
    # THE CEILING (rule 3: a level is an inequality on the bed) on every modelled wet cell: its lake's level, else the
    # shoreline stage's level on that cell (a river's sloping surface, a pool's) ...
    lv_ = np.full(n + 1, np.nan, np.float32)
    for i, v in (lake_level or {}).items():
        lv_[i] = v
    L_ = lv_[lab]
    wzc = P.get("_water_z")
    if wzc is not None:
        L_ = np.where(np.isfinite(L_), L_, wzc).astype(np.float32)
    L_ = np.where(np.isfinite(lens_hi), lens_hi, L_).astype(np.float32)     # a lens-capped cell: its own observed surface (above)
    if P.get("intertidal_band"):
        L_ = np.where(np.isfinite(lt_ceil), lt_ceil, L_).astype(np.float32)  # a floor-path sea domain: its own ceiling -- the intertidal band hangs from the land rim, the interior lies under low tide (2026-11-02)
    else:
        L_ = np.fmin(L_, lt_ceil).astype(np.float32)                          # a floor-path sea domain: low tide (above)
    # ...and a CHANNEL's cells too, at the channel's own surface (2026-10-28, run 20h: a channel at Grewingk's ice face
    # sat in the lake's domain, the lake's soundings spline lifted it 40-70 m above its 48.9 m surface, and the hold
    # skipped channel cells -- 882 water cells with a bed at 87-117 m)
    L_ = np.where(chan & np.isfinite(chan_ws), chan_ws, L_).astype(np.float32)
    # THE CAP: the ceiling descends from the shore at slope_min_deg (the guard's rule, below) -- and since 2026-11-01 the
    # ceiling pass holds the bed under the SAME cap: its sites stood at level - margin on a flat plane while the guard then
    # cut at the cone, so the sites themselves built the shelf the guard was meant to prevent (Taylor Bay fix2c: 1,500
    # sites 0.5 m under low tide, a 180 m plate at the cone)
    dsh_ = dshore if land_rim.any() else np.zeros(z.shape, np.float32)
    if P.get("_lt_dsh") is not None:                                          # the cone starts at the intertidal band's outer edge in a floor-path sea domain (2026-11-02)
        dsh_ = np.where(np.isfinite(lt_ceil), P["_lt_dsh"], dsh_).astype(np.float32)
    cap_ = L_ - np.maximum(0.1, dsh_ * np.tan(np.radians(float(P["slope_min_deg"])))).astype(np.float32)
    # ...relaxed where the first cast in some direction is shallower than the cone (2026-11-02, the delta apron: the cone
    # demanded 7 m of water 400 m out over a flat sounded at 2 ft) -- never above low tide less 0.1 m
    rx_ok = np.isfinite(sea_relax)
    if rx_ok.any():
        n_rx = int((rx_ok & (np.fmin(sea_relax, L_ - 0.1) > cap_ + 1e-3)).sum())
        cap_ = np.where(rx_ok, np.fmax(cap_, np.fmin(sea_relax, L_ - 0.1)), cap_).astype(np.float32)
        log(f"  the cap relaxed by the nearest casts on {n_rx * res * res / 1e4:.1f} ha")
    # ...AND BY A MEASURED BED BESIDE THE CELL (2026-11-06, Hig's review of cm18, 59.60151,-151.49855, off the Homer Spit: gaps one to four cells
    # wide between the green lidar's bed, the multibeam and the compilations, all at -8 m under 7 m of water, filled as trenches at -45 m -- 14 ha
    # of them in kj_r6c2, to -128 m; cm16 had the same fill, and no_peaks, which then ran below the base contour too, closed them as pits).  The
    # cone is the SHORE's rule: how a bed nobody measured falls away from a bank.  2.5 km off the shore it asks for 44 m of water, and a bed
    # measured two metres from the cell says there are seven.  Where the nearest observation of any kind is a bed under the cell's own water,
    # the cap is no lower than that bed and cap_bed_margin_m more (a guard holds gross errors, and the fit meets the bed by its boundary) --
    # the margin never more than half that bed's own depth, so beside a bed just under the water the cap does not become the flat plane
    # 0.1 m under it that the cone was made to prevent (upbay_t: 358 more shelf cells with the full margin).  Only ever a relaxation:
    # where the cone already allows more it stands.
    sel_ = dom & hole & np.isfinite(cap_) & np.isfinite(L_)
    if sel_.any():
        iy_, ix_ = ndimage.distance_transform_edt(~np.isfinite(z), return_distances=False, return_indices=True)
        rr_, cc_ = np.nonzero(sel_); zn_ = z[iy_[rr_, cc_], ix_[rr_, cc_]]; del iy_, ix_
        new_ = np.fmin(zn_ + np.minimum(float(P.get("cap_bed_margin_m", 1.0)), 0.5 * (L_[rr_, cc_] - zn_)), L_[rr_, cc_] - 0.1)
        rl_ = np.isfinite(zn_) & (zn_ < L_[rr_, cc_] - 0.1) & (new_ > cap_[rr_, cc_] + 1e-3)
        if rl_.any():
            log(f"  the cap relaxed by a measured bed beside the cell on {int(rl_.sum()) * res * res / 1e4:.1f} ha (by up to {float((new_ - cap_[rr_, cc_])[rl_].max()):.1f} m)")
            cap_[rr_[rl_], cc_[rl_]] = new_[rl_].astype(np.float32)
        del rr_, cc_, zn_, new_, rl_
    del sel_
    ceil_mask = np.zeros(z.shape, np.uint8)        # 1 = held by a ceiling site in the soundings' fit, 2 = clipped by the guard
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
        # THE BAND IS ITS OWN FIT (2026-11-02, tide1 on kj_r3c4: 23,626 DEM sites at sigma 1 m in the sea's one spline re-tuned
        # its single smoothing, and a third of the subtidal moved by over 1 m with no change to its data).  In a floor-path
        # sea domain with the intertidal band on: the band's sites (the DEM, the casts inside it) fit the band's cells alone,
        # the land rim their zero boundary and the band's outer edge free (the taper); then the interior's prior is re-based
        # on the band's FITTED edge -- a blend from the nearest band or rim cell's value to the interior's own prior, weighted
        # by the cell's distance to that edge against its distance to the nearest interior cast (capped at band_blend_max_m):
        # the band's value at the edge, the cast's prior at a cast, a smooth descent across the gap 3DEP flattened; and the
        # interior's sites fit the interior's cells with the band as a further zero boundary (value continuity).  Lakes and
        # lens domains are fitted whole, as before.
        band_fp = (band & hole & np.isin(lab, list(nolens_ids))) if (P.get("intertidal_band") and nolens.any()) else np.zeros(z.shape, bool)
        slab = lab[S_[:, 0].astype(int), S_[:, 1].astype(int)]
        sband = band_fp[S_[:, 0].astype(int), S_[:, 1].astype(int)]
        fp_ids = {int(i) for i in np.unique(slab) if band_fp[lab == i].any()} if band_fp.any() else set()
        def part_of(i):
            return "interior" if int(i) in fp_ids else "all"
        def Sd_base(i):
            return S_[(slab == i) & ~sband] if int(i) in fp_ids else S_[slab == i]
        def spline_domain(i, Sd, tag="", part="all"):
            if part == "band":
                cells_ = (lab == i) & hole & band_fp; bnd_ = inside_obs | rim
            elif part == "interior":
                cells_ = (lab == i) & hole & ~band_fp; bnd_ = inside_obs | rim | band_fp
            else:
                cells_ = (lab == i) & hole; bnd_ = inside_obs | rim
            # a LAKE's soundings are fitted as the sea's casts are (Hig, 2026-11-06: "appropriately converging logic between our
            # marine soundings and lake soundings"): chi lake_chi, the floor path's shore_cast_chi, so the surface passes within
            # 0.2 sigma of them rather than 1 sigma.  A lens domain keeps points_chi: its sites are CRM's sigma-10 centres and the
            # casts, and at 0.2 plates_t moved 310 ha by up to 25 m -- its own question, not this one.
            Pd = P
            if lk.any() and bool(((lab == i) & lk & ~tidal_cells & ~chan).any()):
                Pd = dict(P); Pd["points_chi"] = float(P.get("lake_chi", 0.2))
            r = _spline_residual(Sd, base, cells_, dom, bnd_, res, Pd, T=P.get("_transform"), log=log, name=f"{int(i)}{tag}")
            if r is not None:
                m_ = np.isfinite(r)
                fill[m_] = (base[m_] + r[m_]).astype(np.float32)
        for i in np.unique(slab):
            if int(i) in fp_ids:
                spline_domain(i, S_[(slab == i) & sband], tag=" band", part="band")
            else:
                spline_domain(i, S_[slab == i])
        if fp_ids:
            edge_ = band_fp | rim
            dE, (er_, ec_) = ndimage.distance_transform_edt(~edge_, return_indices=True, sampling=res)
            ipts = pts & ~band_fp
            dC = ndimage.distance_transform_edt(~ipts, sampling=res) if ipts.any() else np.full(z.shape, np.inf)
            Lm = float(P.get("band_blend_max_m", 500.0)); dCc = np.minimum(dC, Lm)
            w_ = (dCc / np.maximum(dE + dCc, 1e-6)).astype(np.float32)
            ev_ = np.where(band_fp, fill, np.where(np.isfinite(z), z, np.nan))[er_, ec_]
            ok_ = dom & hole & ~band_fp & np.isin(lab, list(fp_ids)) & np.isfinite(fill) & np.isfinite(ev_)
            fill[ok_] = (w_[ok_] * ev_[ok_] + (1.0 - w_[ok_]) * fill[ok_]).astype(np.float32)
            base = fill.copy()
            log(f"  the band fitted alone in {len(fp_ids)} domains; the interior's prior re-based on the band's fitted edge on "
                f"{int(ok_.sum()) * res * res / 1e4:.1f} ha (blend weight p50 {float(np.median(w_[ok_])) if ok_.any() else float('nan'):.2f}, the cast distance capped at {Lm:g} m)")
            del dE, er_, ec_, dC, dCc, w_, ev_, ok_, ipts
            for i in sorted(fp_ids):
                spline_domain(i, Sd_base(i), tag=" interior", part="interior")
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
                spline_domain(i, np.vstack([Sd_base(i), add]), tag=" +bounds", part=part_of(i))
            still = fill[br_, bc_] > B_[:, 4] + 1.0
            if still.any():
                log(f"  lower bounds still violated by > 1 m after the pass: {int(still.sum())}")
        # THE CEILING IN THE FIT (2026-10-28, WATER_REVIEW_PLAN.md section 2): where the fitted bed stands within
        # 0.1 m of its water or above it (what the guard below would clip), sites at level - ceiling_margin_m (one per points_rim_step_m block,
        # sigma points_rim_sigma_m) join the domain's soundings and the domain is refitted, up to ceiling_passes times --
        # the surface bends down under its water instead of being cut flat at it.  Lower bounds already added stay in.
        mg_ = float(P["ceiling_margin_m"])
        k_ = max(int(round(P["points_rim_step_m"] / res)), 1)
        Sd_all = {int(i): Sd_base(i) for i in np.unique(slab)}
        if bounds:
            for i in np.unique(lab[br_[viol], bc_[viol]]):
                Sd_all[int(i)] = np.vstack([Sd_all.get(int(i), np.zeros((0, 6))), B_[viol & (lab[br_, bc_] == i)]])
        Tf = P.get("_transform")
        # only cells the guard below would clip (within 0.1 m of the water or above), and away from the domain's edge: at
        # the shoreline the bed meets the water by the boundary condition, and a site there would contradict it
        inner = ndimage.distance_transform_edt(hole, sampling=res) > float(P["points_rim_step_m"])
        for pass_ in range(int(P["ceiling_passes"])):
            over = (dom & hole & inner & ~tidal_cells & ~band_fp & np.isfinite(cap_) & np.isfinite(fill) & (fill > cap_ - 0.1)
                    & np.isin(lab, list(Sd_all)))
            if not over.any() or Tf is None:
                break
            orr, occ = np.nonzero(over)
            _, first = np.unique((orr // k_) * 1_000_003 + occ // k_, return_index=True)
            orr, occ = orr[first], occ[first]
            add = np.column_stack([orr, occ, Tf.c + (occ + 0.5) * res, Tf.f - (orr + 0.5) * res,
                                   cap_[orr, occ] - mg_, np.full(len(orr), float(P["points_rim_sigma_m"]))])
            dl = lab[orr, occ]
            log(f"  ceiling pass {pass_ + 1}: {int(over.sum())} cells within 0.1 m of their cap (the water, descending from the shore) or above it "
                f"(up to {float(np.max(fill[over] - cap_[over])):.1f} m above) in {len(np.unique(dl))} domains; {len(add)} ceiling sites")
            ceil_mask[over] = np.maximum(ceil_mask[over], 1)
            for i in np.unique(dl):
                Sd_all[int(i)] = np.vstack([Sd_all[int(i)], add[dl == i]])
                spline_domain(i, Sd_all[int(i)], tag=f" +ceiling{pass_ + 1}", part=part_of(i))
        sdist = ndimage.distance_transform_edt(~pts, sampling=res)
        mask[(dom & hole) & (sdist <= 3 * P["cond_m"])] = 3
    # A LAKE'S MODELLED BED NEVER STANDS ABOVE ITS WATER (2026-10-14, Grewingk's SE corner, v5): the shore residuals
    # pull the bed up to the level at the edge and the conditioning surfaces (harmonic, then the thin-plate spline,
    # which has no maximum principle) overshoot them -- 0.5-5.6 m above the water on a 10-30 m fringe below a cliff.
    # Only modelled cells: islands are observations and stay.
    # ...and the same inequality (rule 3) in EVERY wet domain, not only a lake's (2026-10-25, run 19c: Grewingk's
    # marginal stream, a 97 m river body under channel_min_m2 so without a channel bed, had its conditioned bed 1-6 m
    # above its 65 m level -- the ice beside it as its rim): a modelled wet cell with a level -- its lake's, else the
    # shoreline stage's level on that cell (a river's sloping surface, a pool's) -- never stands above it.
    # ...since 2026-10-28 (WATER_REVIEW_PLAN.md section 2) the ceiling is a constraint INSIDE the soundings' fit (above),
    # and this clip is the last-resort guard: a clipped surface is a plateau exactly at the constraint -- the flat
    # shelves 10 cm under Grewingk's 48.91 m lake at its ice face in run 20h2 were this clip -- so every cell it holds is
    # recorded (fill_ceiling.tif = 2) for the review's flags.
    # ...and the guard's ceiling DESCENDS FROM THE SHORE (2026-10-31, upbay_t fix2: tidal creeks 10-20 m wide on the flats,
    # their banks' gentle slope extended over them above their water, no sounding in them, cut to one plane 0.1 m under the
    # surface -- the review's shelf signature over 1,200 cells): a bed under water lies below it by at least the minimum
    # slope's drop from the nearest shore, slope_min_deg (a creek deepens away from its banks), never less than 0.1 m.
    up = dom & hole & ~tidal_cells & np.isfinite(L_) & np.isfinite(fill) & (fill > cap_)
    if up.any():
        nl_ = int((up & np.isfinite(lv_[lab])).sum())
        log(f"  bed held below the water (the guard, descending from the shore at {float(P['slope_min_deg']):g} deg): {int(up.sum()) * res * res / 1e6:.4f} km2 "
            f"(up to {float(np.max(fill[up] - L_[up])):.1f} m above the water; {nl_} cells in lakes, {int(up.sum()) - nl_} in rivers and pools at the stage's level)")
        fill[up] = cap_[up]
        ceil_mask[up] = 2
    del cap_, dsh_
    # the tidal lens: the water surface is a CEILING -- the conditioning (rim residual, spline) never lifts the bed above it
    upt = tidal_cells & hole & np.isfinite(fill) & np.isfinite(lake_prior) & (fill > lake_prior)
    if upt.any():
        log(f"  tidal lens: bed held at or below the water surface on {int(upt.sum()) * res * res / 1e4:.2f} ha (up to {float(np.max(fill[upt] - lake_prior[upt])):.2f} m above)")
        fill[upt] = lake_prior[upt]
    if lake_floor:
        fl_ = np.full(n + 1, np.nan, np.float32)
        for i, v in lake_floor.items():
            # a MEASURED bed inside the lake lowers its floor to the measurement (Trena Lake, Seldovia v6: the
            # perched cap's floor stood 2 m above the green lidar's bed and the fill made a plateau between the returns)
            ob = inside_obs & (lab == i) & (cls == classes.get("bed", -1))
            if ob.any():
                v = min(v, float(np.nanmin(z[ob])))
            fl_[i] = v
        F_ = fl_[lab]
        low = dom & hole & np.isfinite(F_) & np.isfinite(fill) & (fill < F_)
        if low.any():
            log(f"  lake bed held above its floor (hand-set / capped depth): {int(low.sum()) * res * res / 1e4:.2f} ha "
                f"(up to {float(np.max(F_[low] - fill[low])):.1f} m below)")
            fill[low] = F_[low]
    # THE SEA'S SHORE FROM TIDE-LEVEL CONTOURS (2026-11-03): see shoreface.py; it replaces the floor-path domains' surface outright
    shoreface_R = None
    if contour_model and (land_rim.any() or bool(nolens_ids)):   # ...or a floor-path sea with no land in the box at all (kj_r1c4, 2026-11-06): the casts' basin alone
        from .shoreface import contour_sea
        shoreface_R = contour_sea(z, hole, dom, lab, nolens_ids, rim, land_rim, srim, res, P, log=log, dry_from=nw)
        if shoreface_R is not None:
            c_ = shoreface_R["cells"]; ok_ = c_ & np.isfinite(shoreface_R["fill"])
            fill[ok_] = shoreface_R["fill"][ok_]; prior[c_] = np.nan; mask[c_] = shoreface_R["mask"][c_]; ceil_mask[c_] = 0
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
    if shoreface_R is not None:
        sigma[shoreface_R["cells"]] = shoreface_R["sigma"][shoreface_R["cells"]]
    fill[~(dom & hole)] = np.nan; prior[~(dom & hole)] = np.nan
    if prior_ground.any():                                        # the ground prior inside a large void (above): carried as the fill
        fill[prior_ground] = prior_gz[prior_ground]; mask[prior_ground] = 8
        sigma[prior_ground] = float(P.get("prior_ground_sigma_m", 3.0))
    log(f"  filled {int(np.isfinite(fill).sum()) * res * res / 1e6:.3f} km2: band {int(band.sum()) * res * res / 1e6:.3f}, "
        f"interior {int(interior.sum()) * res * res / 1e6:.3f}, conditioned {int(cond.sum()) * res * res / 1e6:.3f} km2; "
        f"sigma p50 {np.nanmedian(sigma):.2f} m")
    # A BODY'S LEVEL OVER ITS WHOLE FOOTPRINT (2026-10-23, run 14, Hig: the multibeam's bed inside Grewingk Lake "shown
    # as dry ground -- the water DEM has a hole"): the level was written on the lake DOMAIN only -- its holes and the
    # observations judged inside -- so a measured bed inside the body (the sonar, a green-lidar return) had no water
    # over it in the water-surface DEM. Every cell of a flat body with a level gets the level where the fill set none.
    lvl_ = locals().get("lvl")
    if shore:
        lvl_ = np.full(z.shape, np.nan, np.float32) if lvl_ is None else lvl_.copy()
        take_ = wet_l & np.isfinite(shore_lev)
        lvl_[take_] = shore_lev[take_]
        lvl_[~wet_l] = np.nan                                  # and nowhere else: a dry domain has no water surface
        log(f"  shoreline stage: the water surface is its level on {int(take_.sum()) * res * res / 1e6:.3f} km2 of water (flat per body, monotone down a river)")
    if lvl_ is not None and P.get("_bodies") is not None:
        from .water import FLAT_KINDS as _FK
        bid_, btab_ = P["_bodies"]; mx_ = int(bid_.max()) if bid_.size else 0
        lv_ = np.full(mx_ + 1, np.nan, np.float32)
        for k_, b_ in btab_.items():
            k_ = int(k_); v_ = b_.get("consensus_level_m")
            if 0 < k_ <= mx_ and b_.get("kind") in _FK and v_ is not None:
                lv_[k_] = float(v_)
        vals_ = lv_[np.clip(bid_, 0, mx_)]
        add_ = ~np.isfinite(lvl_) & (bid_ > 0) & np.isfinite(vals_)
        if shore:
            add_ &= wet_l                                      # a body's cells the stage judged land or floating carry no level
        if add_.any():
            lvl_ = lvl_.copy(); lvl_[add_] = vals_[add_]
            log(f"  water level written over the measured bed inside flat bodies: {int(add_.sum()) * res * res / 1e4:.2f} ha")
    # THE GAP BETWEEN MEASUREMENTS IS THE MEASUREMENTS' (2026-11-08, Hig's cm18 review: "multibeam edge seams are a widespread
    # issue - we need to figure out how to blend smoothly away from these"): a void a few cells wide between a multibeam's
    # swaths, or the ring a superseded compilation leaves round a multibeam hole, was filled by the domain's model -- a basin
    # rollover, the intertidal band, the guard's clip -- and stood beside the beds on both sides as grooves (Halibut Cove
    # Lagoon, 1.8 m deep), mesas (Coal Cove, -3.9 m between -7.6 m beds), a trough (59.358,-151.954, 8-13 m) and raised lines
    # (the Homer flats).  Within gap_fit_w0_m of an observed cell the fill is the HARMONIC through the observations round it
    # (a maximum principle: no groove, no mesa, nothing the rims do not ask for), fading to the model's own fill by
    # gap_fit_w1_m, where the harmonic is pinned to that fill so the two meet without a step.  Modelled wet cells only (the
    # shore stage's water or floating) of the band, interior, conditioned, margin and intertidal kinds; a lake's shape (5),
    # a channel (6), the tidal lens (7) and the ground prior (8) keep their own rules.  The ceiling still holds: a cell
    # already under its water stays under it.
    w0_, w1_ = float(P.get("gap_fit_w0_m", 0.0)), float(P.get("gap_fit_w1_m", 0.0))
    if w1_ > w0_ >= 0 and w1_ > 0 and shore_lab is not None:
        gf_ = np.isfinite(fill)
        obs_ = np.isfinite(z) & ~gf_
        # ...through real coverage only: a speck of observation (under gap_fit_min_cells, 8-connected) -- a stray return of the sea
        # standing in the bay (anchor_t, 2026-11-08: the harmonic made cones 25 m round Kenai 2008's +2 m specks in 6 m of water)
        # -- neither pins it nor draws a cell into its reach; the model's fill stands round it as before
        mc_ = int(P.get("gap_fit_min_cells", 0))
        if mc_ > 1 and obs_.any():
            ol_, on_ = ndimage.label(obs_, structure=np.ones((3, 3), bool))
            big_ = np.bincount(ol_.ravel(), minlength=on_ + 1) >= mc_; big_[0] = False
            obs_ = big_[ol_]; del ol_, big_
        tgt_ = gf_ & np.isin(mask, (1, 2, 3, 4, 9)) & np.isin(shore_lab, (2, 3))
        if obs_.any() and tgt_.any():
            d_ = ndimage.distance_transform_edt(~obs_, sampling=res)
            unk_ = tgt_ & (d_ <= w1_)
            if unk_.any():
                surf_ = np.where(gf_, fill, z)
                dm_ = ndimage.binary_dilation(unk_, iterations=2) & np.isfinite(surf_) & (gf_ | obs_)
                fx_ = dm_ & ~unk_
                Hh_ = harmonic(np.where(fx_, surf_, 0.0), fx_, dm_, coarse=P.get("coarse", 16), iters=P.get("cg_iters", 300))
                ok_ = unk_ & np.isfinite(Hh_)
                wt_ = np.clip((w1_ - d_) / max(w1_ - w0_, 1e-6), 0.0, 1.0)
                nv_ = (wt_ * Hh_ + (1.0 - wt_) * fill).astype(np.float32)
                if lvl_ is not None:
                    cp_ = ok_ & np.isfinite(lvl_)
                    nv_[cp_] = np.minimum(nv_[cp_], np.maximum(fill[cp_], lvl_[cp_] - 0.1))
                dz_ = np.where(ok_, nv_ - fill, 0.0)
                mv_ = np.abs(dz_) > 0.05
                fill[ok_] = nv_[ok_]
                log(f"  the gap fit (harmonic through the observations, {w0_:g} -> {w1_:g} m): {int(ok_.sum()) * res * res / 1e4:.1f} ha within reach, "
                    f"{int(mv_.sum()) * res * res / 1e4:.1f} ha moved > 5 cm"
                    + (f", |dz| p50/p99/max {np.percentile(np.abs(dz_[mv_]), [50, 99]).round(2).tolist()} / {float(np.abs(dz_).max()):.1f} m, up {int((dz_ > 0.05).sum()) * res * res / 1e4:.1f} ha" if mv_.any() else ""))
                del d_, unk_, surf_, dm_, fx_, Hh_, ok_, wt_, nv_, dz_, mv_
        del gf_, obs_, tgt_
    # A TIDAL FLAT IN A TRUE GAP IS A LOW-GRADIENT SURFACE (2026-11-09, Hig: "delta flats have a pretty simple gradient from high to low tide
    # ... closely approximated by a plane or cone with a very low constant gradient ... fit to whatever observations we have"; "only use simple
    # surface fits where there are true data gaps").  The shore model hangs the intertidal from the land down to its base contour, and on a wide
    # flat that contour lands inside it: basins at -3.4 m at the head of Seldovia Bay.  Per connected piece of MODELLED cells (not channels, not
    # lakes) within flat_fit_zlo_m..tide_max_m and >= flat_fit_min_ha: a robust trend (quadratic, else a plane where the quadratic would hold a
    # basin) through the observed cells on and within flat_fit_ring_m of it (in the band, slope < flat_fit_obs_slope); accepted when its gradient
    # is under flat_fit_gmax and its scatter under flat_fit_mad_m; the cells take trend + the harmonic of the observations' residual, never
    # lower than the fill (raise only), never above an observed water level - 0.1 m or tide_max_m.
    ffh_ = float(P.get("flat_fit_min_ha", 0.0))
    if ffh_ > 0:
        tm_f = float(P.get("tide_max_m", 4.5)); zlo_f = float(P.get("flat_fit_zlo_m", -4.5))
        obs_f = (mask == 0) & np.isfinite(z)
        surf_f = np.where(obs_f, z, fill).astype(np.float64)
        mod_f = np.isin(mask, (1, 2, 3, 4, 9)) & np.isfinite(fill) & (fill >= zlo_f) & (fill <= tm_f)
        mod_f = ndimage.binary_opening(mod_f, iterations=2)
        labf, nf_ = ndimage.label(mod_f, np.ones((3, 3), bool))
        gyf, gxf = np.gradient(ndimage.gaussian_filter(np.nan_to_num(surf_f), 3), res); slf = np.hypot(gxf, gyf); del gyf, gxf
        band_f = np.isfinite(surf_f) & (surf_f >= zlo_f) & (surf_f <= tm_f)
        ring_i = int(round(float(P.get("flat_fit_ring_m", 200.0)) / res)); n_ok = 0; ha_up = 0.0
        for i_, sl_ in enumerate(ndimage.find_objects(labf), 1):
            if sl_ is None: continue
            if (labf[sl_] == i_).sum() * res * res / 1e4 < ffh_: continue
            sl2 = tuple(slice(max(a.start - ring_i - 2, 0), min(a.stop + ring_i + 2, n)) for a, n in zip(sl_, labf.shape))
            dom_f = labf[sl2] == i_
            ring = ndimage.binary_dilation(dom_f, iterations=ring_i) & ~dom_f
            fit = ring & obs_f[sl2] & band_f[sl2] & (slf[sl2] < float(P.get("flat_fit_obs_slope", 0.05)))
            if fit.sum() < 500: continue
            rr_, cc_ = np.nonzero(fit); u_, v_ = cc_ * res / 1000.0, rr_ * res / 1000.0; u0, v0 = u_.mean(), v_.mean()
            cols = lambda U, V, q: [np.ones_like(U), U - u0, V - v0] + ([(U - u0) ** 2, (U - u0) * (V - v0), (V - v0) ** 2] if q else [])
            zf_ = surf_f[sl2][fit]
            def irls(q):
                X = np.stack(cols(u_, v_, q), 1); w = np.ones(len(zf_))
                for _ in range(8):
                    sw = np.sqrt(w); b, *_ = np.linalg.lstsq(X * sw[:, None], zf_ * sw, rcond=None)
                    r = zf_ - X @ b; a = np.abs(r); w = np.where(a < 0.3, 1.0, 0.3 / np.maximum(a, 1e-9))
                return b, r
            beta, r_ = irls(True); quad = True
            Hq = np.array([[2 * beta[3], beta[4]], [beta[4], 2 * beta[5]]])
            if np.all(np.linalg.eigvalsh(Hq) > 0):                     # a basin in the trend: the plane instead
                beta, r_ = irls(False); quad = False
            g_ = np.hypot(beta[1], beta[2]) / 1000.0; mad_ = 1.4826 * np.median(np.abs(r_ - np.median(r_)))
            if g_ > float(P.get("flat_fit_gmax", 0.02)) or mad_ > float(P.get("flat_fit_mad_m", 1.2)): continue
            RR, CC = np.mgrid[sl2[0], sl2[1]]; U, V = CC * res / 1000.0, RR * res / 1000.0
            Pz = sum(b_ * c_ for b_, c_ in zip(beta, cols(U, V, quad)))
            pin = ndimage.binary_dilation(dom_f, iterations=2) & obs_f[sl2]
            Rh = harmonic(np.where(pin, surf_f[sl2] - Pz, 0.0), pin, dom_f | pin, coarse=P.get("coarse", 16), iters=P.get("cg_iters", 300))
            new = Pz + np.nan_to_num(Rh)
            # ...never above the plain harmonic of the observations round the gap: the trend is pulled up by bars and land beside it, and a
            # trend above both banks of a channel is a ridge (Kasitsna 59.47827,-151.56736: -0.5 m between -1.5 and -2.5; Hig: "I don't think
            # there is any positive relief here").  The harmonic holds no interior maximum; the Seldovia head basins still rise to their rims.
            H0 = harmonic(np.where(pin, surf_f[sl2], 0.0), pin, dom_f | pin, coarse=P.get("coarse", 16), iters=P.get("cg_iters", 300))
            new = np.where(np.isfinite(H0), np.minimum(new, H0), new)
            new = np.minimum(new, tm_f)
            if lvl_ is not None:
                lv2 = lvl_[sl2]; new = np.where(np.isfinite(lv2), np.minimum(new, lv2 - 0.1), new)
            cur = fill[sl2]; up = dom_f & np.isfinite(cur) & (new > cur)
            cur[up] = new[up].astype(np.float32); n_ok += 1; ha_up += up.sum() * res * res / 1e4
        if n_ok:
            log(f"  tidal flats in true gaps (trend + harmonic of the observations, raise only): {n_ok} flats, {ha_up:.1f} ha raised")
        del labf, mod_f, surf_f, obs_f, slf, band_f
    # SLOPING WATER IS SHALLOW (2026-11-09, Hig on the Woz delta: "water that is sloped has direct implications about how shallow that water
    # is"; the 2023 lidar "should be used essentially with no modification, except for minor (<1.5 m, maybe 0.5 m would be good) incision in
    # channels").  The river through the delta was split into lake domains and took a lake's depth, 4-8 m under a surface falling 6 m in 3 km.
    # Where the fill's water level slopes more than slope_water_min (smoothed over slope_water_smooth_m), the bed stands at most
    # slope_water_depth_m below it.  A lake and the sea are level and are not touched.
    swd_ = float(P.get("slope_water_depth_m", 0.0))
    if swd_ > 0 and lvl_ is not None and np.isfinite(lvl_).any():
        has_ = np.isfinite(lvl_)
        # the RAW gradient of the level, then its MEDIAN over slope_water_smooth_m: a river slopes everywhere; a chain of level lakes has a
        # gradient only on the thin lines of its steps, and the median there is nil (a Gaussian smoothed the steps into slopes: 0.6 ha by 70 m
        # beside Hoen's Lagoon)
        gy_, gx_ = np.gradient(np.where(has_, lvl_, np.nan).astype(np.float64), res); wsl_ = np.hypot(gx_, gy_)
        k_ = max(int(round(float(P.get("slope_water_smooth_m", 25.0)) / res)) | 1, 3)
        wsl_ = ndimage.median_filter(np.nan_to_num(wsl_, nan=0.0), size=k_)
        num_ = den_ = ls_ = None
        mar_c = np.zeros(z.shape, bool)
        cap_ = has_ & ~mar_c & np.isfinite(fill) & np.isin(mask, (1, 2, 3, 4, 6, 9)) & (wsl_ > float(P.get("slope_water_min", 0.0005))) & (fill < lvl_ - swd_)
        if cap_.any():
            dz_ = (lvl_ - swd_ - fill)[cap_]
            fill[cap_] = (lvl_ - swd_)[cap_]
            log(f"  sloping water is shallow (surface slope > {float(P.get('slope_water_min', 0.0005)) * 100:g} %, bed <= {swd_:g} m under it): {int(cap_.sum()) * res * res / 1e4:.1f} ha raised, "
                f"by p50/p90/max {np.percentile(dz_, [50, 90]).round(2).tolist()} / {float(dz_.max()):.1f} m")
        del has_, num_, den_, ls_, gy_, gx_, wsl_, cap_, mar_c
    # THE MAPPED LOW-TIDE WATER IS WATER AT LOW TIDE (2026-11-09, Hig, Anchor Point: "anything not in [the rocks and shorelines] is not a real
    # protrusion"): a modelled cell inside the low-tide water polygon (beyond low_water_edge_m of its line, off rock_near_m of every mapped rock
    # and island) stands no higher than its level.  Not in a closed gap (<= low_water_gap_m) of observed ground above that level: Hoen's
    # Lagoon's mud flat at 0.8 m lies inside the polygon, and its gaps are the flat's.
    import os, json
    lwp_f = P.get("low_water") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "water", "tide_low_water.geojson")
    if float(P.get("low_water_cap", 0.0)) > 0 and P.get("_transform") is not None and os.path.exists(lwp_f):
        from rasterio.features import rasterize
        Lw = float(P.get("low_water_level_m", -1.87))
        inw = rasterize([(f_["geometry"], 1) for f_ in json.load(open(lwp_f))["features"]], out_shape=z.shape, transform=P["_transform"], fill=0, dtype="uint8").astype(bool)
        if inw.any():
            ed = int(round(float(P.get("low_water_edge_m", 30.0)) / res))
            inw = ndimage.binary_erosion(inw, iterations=max(ed, 1))
            prot = np.zeros(z.shape, bool); T_ = P["_transform"]
            rp_f = P.get("rocks")
            if rp_f and os.path.exists(rp_f):
                for f_ in json.load(open(rp_f))["features"]:
                    if (f_["properties"].get("type") or "") == "Deep": continue
                    x_, y_ = f_["geometry"]["coordinates"][:2]; c_, r_ = ~T_ * (x_, y_); r_, c_ = int(r_), int(c_)
                    if 0 <= r_ < z.shape[0] and 0 <= c_ < z.shape[1]: prot[r_, c_] = True
            ip_f = os.path.join(os.path.dirname(lwp_f), "tide_islands.geojson")
            if os.path.exists(ip_f):
                prot |= rasterize([(f_["geometry"], 1) for f_ in json.load(open(ip_f))["features"]], out_shape=z.shape, transform=T_, fill=0, dtype="uint8").astype(bool)
            if prot.any():
                inw &= ndimage.distance_transform_edt(~prot, sampling=res) > float(P.get("rock_near_m", 40.0))
            og = (mask == 0) & np.isfinite(z) & (z > Lw)
            gi = int(round(float(P.get("low_water_gap_m", 48.0)) / res / 2))
            if gi > 0 and og.any():
                inw &= ~ndimage.binary_closing(og, structure=np.ones((3, 3), bool), iterations=gi)
            cap = inw & (mask > 0) & np.isfinite(fill) & (fill > Lw)
            if cap.any():
                log(f"  the mapped low-tide water: {int(cap.sum()) * res * res / 1e4:.1f} ha of modelled bed held at its level {Lw:+.2f} m (by p50/max {np.percentile(fill[cap] - Lw, 50):.2f} / {float((fill[cap] - Lw).max()):.1f} m)")
                fill[cap] = Lw
            del prot, og
        del inw
    return dict(fill=fill, prior=prior, mask=mask, sigma=sigma, domain=dom, marine=marine, lakes=lake_tab, surface=surface, dryvoid=dryvoid, water_level=lvl_, ceiling=ceil_mask, sound_prior=sound_prior, tide_contours=(shoreface_R or {}).get("contours"), cast_review=(shoreface_R or {}).get("cast_review"), cast_lines=(shoreface_R or {}).get("cast_lines"), rock_review=(shoreface_R or {}).get("rock_review"), edit_check=(shoreface_R or {}).get("edit_check"),
                channels=chan_tab)
