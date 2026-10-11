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
            sounding cells, r = 0 on the rim, harmonic in between; fill = prior + r.  Soundings
            are honoured exactly; between them the prior's shape holds.

  SIGMA     for the fill: sigma_fill = sigma_prior * (1 - w) + sigma_obs * w where w decays with
            distance to the nearest sounding (`cond_m`), and sigma_prior grows with distance from
            the rim (sigma_rim_m per 100 m, from `sigma_prior_m`).  Lakes carry `lake_sigma_m`
            extra, because the floor there is a guess.

Marine vs lake: a domain is marine when the median elevation of its land rim is below
`marine_rim_max_m`; its floor is `low_tide_m`.  A lake's floor is its rim median minus
`lake_depth_m`.  Both are first guesses that the soundings override wherever they exist.

Outputs (in the composite's out/): fill.tif (the filled surface, NaN outside domains),
fill_mask.tif (0 none, 1 band, 2 interior prior, 3 conditioned by soundings, 4 margin hole left
empty, 5 lake shape prior), fill_sigma.tif, fill_prior.tif (the synthetic DEM alone), and <id>_filled.tif (the
composite with holes filled) as a COG.
"""
from __future__ import annotations
import numpy as np
from scipy import ndimage, sparse
from scipy.sparse.linalg import cg, spsolve

DEFAULTS = dict(fill_margin=True, close_m=60, island_max_m2=2000, inland_m=10, slope_min_deg=1.0, slope_max_deg=60.0, low_tide_m=-2.9,
                marine_rim_max_m=6.0, lake_depth_m=5.0, sigma_prior_m=1.0, sigma_rim_m=2.0, lake_sigma_m=5.0,
                cond_m=30.0, cg_iters=300, coarse=16,
                lake_min_m2=2000, lake_water_frac=0.5, lake_vd=0.5, hollister_k=0.5, huggel_a=0.104, huggel_b=1.42, lake_sigma_frac=0.5, points=None)
LAND_CLASSES = ("ground", "built", "snow", "canopy", "ice")


def _nmean(v, has, size):
    s = ndimage.uniform_filter(np.where(has, v, 0.0).astype(np.float32), size)
    n = ndimage.uniform_filter(has.astype(np.float32), size)
    return np.where(n > 1e-6, s / np.maximum(n, 1e-6), np.nan)


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
    lab = np.where(wet, lw, np.where(ld > 0, ld + nw, 0)); n = nw + nd
    log(f"  wet domains {nw}, dry (void) domains {nd}")
    log(f"  domains: {n} (margin holes left empty: {int((margin & hole).sum()) * res * res / 1e6:.3f} km2); "
        f"domain {int(dom.sum()) * res * res / 1e6:.3f} km2, of which holes {int((dom & hole).sum()) * res * res / 1e6:.3f} km2")
    inside_obs = dom & np.isfinite(z)                                # soundings inside a domain
    rim = ndimage.binary_dilation(dom, structure=np.ones((3, 3), bool)) & ~dom & np.isfinite(z)
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
    floor_by = np.where(marine, P["low_tide_m"], med - P["lake_depth_m"])
    floor_by = np.where(np.isfinite(floor_by), floor_by, P["low_tide_m"])
    floor = np.full(z.shape, np.nan, np.float32); floor[dom] = floor_by[lab[dom] - 1]
    log(f"  land rim {int(land_rim.sum())} cells, bed rim {int(bed_rim.sum())}, soundings inside {int(inside_obs.sum())}; "
        f"marine domains {int(marine.sum())} / lakes {int((~marine).sum())}; rim slope p10/50/90 "
        f"{np.percentile(srim[land_rim], [10, 50, 90]).round(1) if land_rim.any() else '-'} deg")
    # PRIOR: nearest land-rim extension
    prior = np.full(z.shape, np.nan, np.float32)
    band = np.zeros(z.shape, bool)
    if land_rim.any():
        d, (ri, ci) = ndimage.distance_transform_edt(~land_rim, return_indices=True, sampling=res)
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
    lake_tab = []
    lake_prior = np.full(z.shape, np.nan, np.float32); lake_sig = np.full(z.shape, np.nan, np.float32)
    if land_rim.any():
        dshore = d                                                  # distance to the nearest land rim cell
        vd = float(P["lake_vd"]); pexp = (-3 + np.sqrt(1 + 8 / vd)) / 2
        lake_ids = [i for i in ids if not marine[i - 1]]
        for i in lake_ids:
            cells = lab == i
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
            d_h = P["hollister_k"] * L * np.tan(np.radians(sbar))
            dbar = P["huggel_a"] * A ** (P["huggel_b"] - 1)            # mean depth, m (A in m2, V in m3)
            d_g = dbar / vd
            dmax = float(np.sqrt(max(d_h, 0.5) * max(d_g, 0.5)))
            sd = abs(d_h - d_g) / 2 + 5.0
            lfrac = np.clip(dshore[cells] / L, 0, 1)
            shape = dmax * lfrac ** pexp
            bandd = np.minimum(np.tan(np.radians(srim[ri, ci][cells])) * dshore[cells], dmax)
            depth = np.maximum(shape, bandd)
            lake_prior[cells] = (level - depth).astype(np.float32)
            lake_sig[cells] = (P["lake_sigma_frac"] * sd * lfrac ** pexp + 1.0).astype(np.float32)
            rr, cc = ndimage.center_of_mass(cells)
            lake_tab.append(dict(id=int(i), area_m2=int(A), L_m=round(L, 1), rim_slope_deg=round(sbar, 1), level_m=round(level, 2),
                                 dmax_hollister_m=round(d_h, 1), dmax_glacial_m=round(d_g, 1), dmax_m=round(dmax, 1), p=round(pexp, 2), row=int(rr), col=int(cc)))
        lk = np.isfinite(lake_prior) & dom
        prior[lk] = lake_prior[lk]; band &= ~lk                      # the lake shape replaces band + interior there
        # an observation inside a lake that sits at the water level is the water surface (a
        # boat track, a seam of returns off calm water), not the bed: not evidence for the fill
        lvl = np.full(z.shape, np.nan, np.float32); lvl[lk] = np.array([med[i - 1] for i in range(1, n + 1)], np.float32)[lab[lk] - 1]
        at_surface = lk & inside_obs & np.isfinite(z) & (z > lvl - 1.0)
        if at_surface.any():
            log(f"  lake observations at the water level dropped as evidence: {int(at_surface.sum())} cells")
            inside_obs &= ~at_surface; hole |= at_surface
        if lake_tab:
            log(f"  lakes >= {P['lake_min_m2']} m2: {len(lake_tab)}; Dmax Hollister/glacial p50 "
                f"{np.median([t['dmax_hollister_m'] for t in lake_tab]):.1f}/{np.median([t['dmax_glacial_m'] for t in lake_tab]):.1f} m")
    else:
        lk = np.zeros(z.shape, bool)
    # SPARSE POINTS (Hig, 2026-10-11: "this method should be able to accept sparse point
    # depths"): a CSV with x,y and z (absolute) or depth (positive down from the domain's rim
    # level) becomes observations inside the domain, conditioning the prior like soundings.
    if P.get("points"):
        import csv
        T = P.get("_transform")
        npts = 0
        with open(P["points"]) as fh:
            for row in csv.DictReader(fh):
                x, y = float(row["x"]), float(row["y"])
                if T is None:
                    continue
                cc, rr = ~T * (x, y); rr, cc = int(rr), int(cc)
                if not (0 <= rr < z.shape[0] and 0 <= cc < z.shape[1]) or not dom[rr, cc]:
                    continue
                if row.get("z") not in (None, ""):
                    zz = float(row["z"])
                else:
                    zz = float(med[lab[rr, cc] - 1]) - float(row["depth"])
                z[rr, cc] = zz; inside_obs[rr, cc] = True; hole[rr, cc] = False; npts += 1
        log(f"  sparse points: {npts} inside domains from {P['points']}")
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
    cond = np.zeros(z.shape, bool)
    if inside_obs.any():
        rz = np.zeros(z.shape, np.float64)
        rz[inside_obs] = z[inside_obs] - np.nan_to_num(prior[inside_obs], nan=0.0)   # the prior is defined at sounding cells
        rfix = inside_obs | rim
        rval = np.where(inside_obs, rz, 0.0)
        r = harmonic(rval, rfix, dom & hole, coarse=P["coarse"], iters=P["cg_iters"])
        ok = (dom & hole) & np.isfinite(r)
        fill[ok] = (prior[ok] + r[ok]).astype(np.float32)
        dso = ndimage.distance_transform_edt(~inside_obs, sampling=res)
        cond = ok & (dso <= 3 * P["cond_m"])
        mask[cond] = 3
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
    sigma = np.where(dom & hole & np.isfinite(fill), sig, np.nan).astype(np.float32)
    fill[~(dom & hole)] = np.nan; prior[~(dom & hole)] = np.nan
    log(f"  filled {int(np.isfinite(fill).sum()) * res * res / 1e6:.3f} km2: band {int(band.sum()) * res * res / 1e6:.3f}, "
        f"interior {int(interior.sum()) * res * res / 1e6:.3f}, conditioned {int(cond.sum()) * res * res / 1e6:.3f} km2; "
        f"sigma p50 {np.nanmedian(sigma):.2f} m")
    return dict(fill=fill, prior=prior, mask=mask, sigma=sigma, domain=dom, marine=marine, lakes=lake_tab)
