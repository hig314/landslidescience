"""Residual-conditioned priors: a regional grid corrected toward the better observations around it.

Hig, 2026-10-15 (Bluff Point): "that broad regional grid probably has no legitimate basis - it's
likely an interpolation across an even larger data void ... evaluate the quality of this fallback DEM
where it overlaps better data and use that as a basis to weight interpolation of the better data more
heavily."  Measured (TOPOBATHY_REVIEW.md 8.1): the NCEI 1/3" grid IS the Hydropalooza multibeam where
that exists and old soundings interpolated elsewhere - 0.6-1.9 m too deep along the beach, 0.7-2.4 m
deeper than the H12146 survey lines it fills between; the OCM 2 m seamless grid is 81 % GMT-surface
interpolation.  Ranking does not help: the grid already ranks last, the gaps simply have nothing
better, so it shows at its own value with a 1.3 m step where the lidar ends and stripes where the
survey lines cross it.

So a layer marked `condition` is a PRIOR, not an observation: before the tiles are built, the whole
grid is read coarsely, the residual (reference - prior) is taken wherever a named reference layer has
a bed observation, and that residual is carried into the prior's domain as a screened-harmonic field
(Laplace with a decay length: div grad r = r / L^2, r pinned at the reference cells, Neumann at the
domain edge).  Near a reference the correction is the reference's; `length_m` away it has decayed to
nothing and the grid's own shape stands.  The prior's sigma follows: the nearest reference's sigma at
its cells (x 1.5: a copy never outranks its source), growing to the prior's own `sigma_m` over the same length.
Date the prior no later than its references (an OCM grid dated 2023 that carries 2019 lidar would beat the
lidar on the time axis once it is pinned to it).  The build then adds the residual
to the layer on read (stack.read_layer) and takes sigma from the raster (an ordinary sigma step), so the
rules see a corrected, honestly-weighted grid.  In the rules a prior is gated like a modelled surface (half
gate): any real observation of the class beats it whatever its sigma or date; priors rank among themselves
by error.  So it fills the gaps and only the gaps, continuously with what bounds them.

Written per build to $out/cond_<layer>_dz.tif and _sigma.tif on the coarse lattice (`res_m`, default
length/25), and $out/condition.json with the fit.  Recipe:

    {"name": "ncei2010", ..., "condition": {"against": ["bathy2009", "h12146", "homer2018"],
                                             "length_m": 500, "max_dz_m": 10}}

`against` lists the reference layers, used in order of their sigma_m (the best observation pins a
cell).  Only bed is compared: where the prior has a `below_level` class step its level bounds both
sides (a reference's beach is not a correction to a bathymetry grid).  Residuals beyond `max_dz_m` are
blunders (a misclassed source) and are dropped.
"""
from __future__ import annotations
import json
import time
import numpy as np
import rasterio
from rasterio.transform import Affine
from scipy import ndimage, sparse
from scipy.sparse.linalg import cg, spsolve
from .grid import Grid
from .stack import read_geo, apply_shift

DEFAULTS = dict(length_m=500.0, res_m=None, max_dz_m=10.0, cg_iters=3000)


def _coarse(grid: Grid, f: int) -> Grid:
    return Grid(grid.crs, grid.transform * Affine.scale(f), -(-grid.width // f), -(-grid.height // f))


def screened_harmonic(fixed_val, fixed, domain, lam, iters=3000):
    """Solve (div grad - lam) r = 0 on `domain` with r = fixed_val on `fixed` cells, Neumann on the
    domain edge; lam = (h / L)^2 in cell units.  Far from any fixed cell r -> 0."""
    unk = domain & ~fixed
    out = np.zeros(domain.shape, np.float64); out[fixed] = fixed_val[fixed]
    n = int(unk.sum())
    if n == 0:
        return out
    idx = -np.ones(unk.shape, np.int64); idx[unk] = np.arange(n)
    r, c = np.nonzero(unk); k = idx[r, c]
    rows, cols, vals = [], [], []; b = np.zeros(n); diag = np.full(n, lam)
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        rr, cc = r + dr, c + dc
        inb = (rr >= 0) & (rr < unk.shape[0]) & (cc >= 0) & (cc < unk.shape[1])
        rr2, cc2, k2 = rr[inb], cc[inb], k[inb]
        nb_unk = unk[rr2, cc2]; nb_fix = fixed[rr2, cc2]
        diag[k2[nb_unk | nb_fix]] += 1
        rows.append(k2[nb_unk]); cols.append(idx[rr2[nb_unk], cc2[nb_unk]]); vals.append(-np.ones(int(nb_unk.sum())))
        np.add.at(b, k2[nb_fix], fixed_val[rr2[nb_fix], cc2[nb_fix]])
    rows.append(np.arange(n)); cols.append(np.arange(n)); vals.append(diag)
    A = sparse.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n))
    if n < 300_000:
        x = spsolve(A.tocsc(), b)
    else:
        x, info = cg(A, b, maxiter=iters, rtol=1e-7)
    out[unk] = x
    return out


def condition_layers(recipe, grid: Grid, verbose=True):
    """The pre-pass.  Returns pins [(layer, "cond", dz_path, sigma_path)] for composite.apply_pins
    and writes $out/condition.json."""
    specs = [s for s in recipe.layers if s.get("condition")]
    if not specs:
        return []
    by_name = {s["name"]: s for s in recipe.layers}
    pins = []; report = {}
    for spec in specs:
        t = time.time()
        cfg = dict(DEFAULTS); cfg.update(spec["condition"])
        L = float(cfg["length_m"]); h = float(cfg["res_m"] or max(grid.res, round(L / 25)))
        f = max(1, int(round(h / grid.res))); h = f * grid.res
        cg_ = _coarse(grid, f)
        z, v = read_geo(recipe.source_path(spec), cg_, cg_.full(), "average"); z = apply_shift(spec, z)
        level = None
        for step in (spec.get("class") if isinstance(spec.get("class"), list) else []):
            if "below_level" in step and isinstance(step["below_level"].get("level"), (int, float)):
                level = float(step["below_level"]["level"])
        fixed = np.zeros(z.shape, bool); rval = np.zeros(z.shape, np.float64); rsig = np.zeros(z.shape, np.float32)
        used = []
        refs = sorted([by_name[n] for n in cfg["against"] if n in by_name], key=lambda s: float(s.get("sigma_m", 1.0)))
        for rs in refs:
            p = recipe.source_path(rs)
            if not p.exists():
                continue
            rz, rv = read_geo(p, cg_, cg_.full(), "average"); rz = apply_shift(rs, rz)
            m = rv & v & ~fixed
            if level is not None:
                m &= (rz < level) & (z < level + 2.0)       # bed against bed; the prior may sit a little high at the line
            d = rz - z
            m &= np.abs(np.nan_to_num(d, nan=np.inf)) <= float(cfg["max_dz_m"])
            if not m.any():
                continue
            rval[m] = d[m]; rsig[m] = float(rs.get("sigma_m", 1.0)); fixed |= m
            used.append(dict(layer=rs["name"], cells=int(m.sum()), median=round(float(np.median(d[m])), 3),
                             nmad=round(float(1.4826 * np.median(np.abs(d[m] - np.median(d[m])))), 3)))
        dom = v
        lam = (h / L) ** 2
        r = screened_harmonic(rval, fixed, dom, lam, iters=int(cfg["cg_iters"])) if fixed.any() else np.zeros(z.shape)
        dist, (ii, jj) = ndimage.distance_transform_edt(~fixed, sampling=h, return_indices=True) if fixed.any() else (np.full(z.shape, np.inf), (None, None))
        s_prior = float(spec.get("sigma_m", 1.0))
        if fixed.any():
            s_near = 1.5 * rsig[ii, jj].astype(np.float32)      # a conditioned copy never outranks the observation it was pinned to
            sig = s_near + (s_prior - s_near) * (1.0 - np.exp(-dist / L))
        else:
            sig = np.full(z.shape, s_prior, np.float32)
        dz = np.where(dom, r, 0.0).astype(np.float32); sig = np.where(dom, sig, s_prior).astype(np.float32)
        recipe.resolve("$out/x").parent.mkdir(parents=True, exist_ok=True)
        dz_path = recipe.resolve(f"$out/cond_{spec['name']}_dz.tif"); sg_path = recipe.resolve(f"$out/cond_{spec['name']}_sigma.tif")
        prof = dict(driver="GTiff", dtype="float32", count=1, width=cg_.width, height=cg_.height, crs=cg_.crs,
                    transform=cg_.transform, compress="zstd", predictor=3, nodata=-9999.0, tiled=True)
        for pth, arr in ((dz_path, dz), (sg_path, sig)):
            with rasterio.open(pth, "w", **prof) as ds:
                ds.write(arr, 1)
        pins.append((spec["name"], "cond", str(dz_path), str(sg_path)))
        within = float((dist[dom] <= L).mean()) if dom.any() else 0.0
        rep = dict(length_m=L, res_m=h, level=level, references=used, fixed_cells=int(fixed.sum()),
                   domain_km2=round(float(dom.sum()) * h * h / 1e6, 2), domain_within_length=round(within, 3),
                   dz_p05_p50_p95=[round(float(q), 2) for q in np.percentile(dz[dom], [5, 50, 95])] if dom.any() else None,
                   sigma_p50=round(float(np.median(sig[dom])), 2) if dom.any() else None, seconds=round(time.time() - t, 1))
        report[spec["name"]] = rep
        if verbose:
            print(f"  condition {spec['name']}: {rep['fixed_cells']} reference cells "
                  + ", ".join(f"{u['layer']} {u['median']:+.2f} ({u['nmad']:.2f}) n{u['cells']}" for u in used)
                  + f"; dz p05/50/95 {rep['dz_p05_p50_p95']}; {within:.0%} of {rep['domain_km2']} km2 within {L:.0f} m; "
                  f"sigma p50 {rep['sigma_p50']}; {h:.0f} m lattice, {rep['seconds']}s")
    json.dump(report, open(recipe.resolve("$out/condition.json"), "w"), indent=1)
    return pins
