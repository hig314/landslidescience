#!/usr/bin/env python3
"""solve.py -- PROTOTYPE of the single solve for the water margin (2026-11-14; $W/review_1114/REVIEW.md section 4, Hig: go ahead).

    /opt/anaconda3/bin/python3 topobathy/solve.py <box> <name> x0 y0 x1 y1 [--tag cm18] [--holdout 0.2]

Reads a finished box's saved rasters over a window and solves the bed there ONCE, instead of the fill's cascade of masked models:

  fixed      confident measurements, exactly as measured: lidar cells with >= fix_min_n returns and gridded surveys, more than fix_edge_m
             inside their own coverage; land of the prior above the tide; lakes and ice as the present product has them (out of scope)
  soft data  w = 1/sigma^2: measured cells at the edge of coverage or thinly seen (sigma grows toward the edge: x(1 + edge_gain e^(-d/edge_len))),
             soundings, traced tide lines (z = level on the line), the prior's ground inside the tide's reach (sigma prior3dep_sigma_m)
  bound      z <= the smoothed lower envelope of the water a lidar saw - bound_gap_m; active only where the fit would rise above it
  prior z0   the SHAPE between measurements, never a measurement: compilations (OCM / NCEI / CUDEM) where they are the only source, and
             under seen water a depth profile by distance from dry ground (learned from the measured bed in the window; a minimum depth
             where the water slopes).  Its LEVEL is corrected first by the smooth long-range field of (data - z0) (stage C), then the fit
             leans on it only at long range (prior_len_m)
  smoothness thin plate in light tension, one lambda, no zones, no buffers.  Natural edges (the window is not tied to the product).

Labels are not read to choose a model.  Writes <out>/<name>_{z,kind,active}.tif, a figure and a report.  Not wired into the chain."""
from __future__ import annotations
import argparse, csv, json, os, re, sys, time
from pathlib import Path
import numpy as np, rasterio
from rasterio.windows import from_bounds
from rasterio import features
from scipy import ndimage

W = Path("/Volumes/Nunatak/lidar_build/composite"); WATER = Path("/Users/Hig/Claude_projects/landslidescience-topobathy/tools/lidar/water")
EDITS = W / "review_1114" / "solve_edits.geojson"       # a reviewer's typing of evidence: polygons, kind "not_bed" (optionally only cells deeper_than_m)
COMP = ("ocm2023", "ncei2010", "cudem2022", "crm")
DOUBT = ("kenai2008",)          # surveys whose ground class does not outrank Hig's low-tide mapping
P = dict(lam=20.0, tau=0.05, tide_max_m=4.5, fix_min_n=3, fix_sigma_max_m=1e9, fix_edge_m=6.0, edge_gain=6.0, edge_len_m=5.0, prior3dep_sigma_m=1.5, deep_prior_m=-6.0, scope_deep_m=-15.0, scope_dry_m=30.0, low_level_m=-1.87, low_tol_m=1.0, low_line_m=30.0, low_rock_m=40.0, comp_len_m=6.0, low_stale_m=250.0, prior_near_m=200.0, prior_fill_m=80.0, level_soft=0.01, level_soft_comp=0.1, still_gap_m=0.1, berg_max_m2=50000.0, sloped_sigma_m=0.3, step_m=0.8, lone_deep_m=0.0, lone_max_m2=10000.0, taper_comp=0, lone_ref_m=40.0, deep_near_m=30.0, sound_len_m=16.0, sound_n0=4.0, a_level=0, hinge_beta=0.03, hinge_cells=2, deep_len_m=10.0, converge_m=0.02, bound_gap_m=0.15, bound_w=100.0,
         line_sigma_m=0.5, sounding_sigma_min_m=0.2, prof_slope=0.01, prof_dmax_m=3.0, prof_min_cells=2000, sloped_water=0.0015, slope_len_m=60.0, sloped_depth_m=0.3, prior_len_m=40.0,
         level_len_m=1500.0, level_cell=8, audit_len_m=120.0, audit_n0=60.0, audit_sd_m=0.5, huber_k=2.5, w_fix=50.0, rounds=1, iters=150, max_rounds=24)


def read(path, win):
    with rasterio.open(path) as s:
        w = from_bounds(*win, s.transform).round_offsets().round_lengths()
        a = s.read(1, window=w, boundless=True, fill_value=(s.nodata if s.nodata is not None else 0))
        if a.dtype.kind == "f":
            a = a.astype(np.float64)
            if s.nodata is not None: a[a == s.nodata] = np.nan
            a[a < -9000] = np.nan
        return a, s.res[0], s.window_transform(w)


def lap(z, ex=None, ey=None):
    """Graph Laplacian with natural (Neumann) edges: sum over the neighbours of (z - z_nb).  Symmetric, positive semidefinite.
    ex / ey (0 or 1 per link) CUT links: across a cut the surface is not continued -- the ice face, where a cliff is the truth."""
    g = np.zeros_like(z)
    d = z[:, 1:] - z[:, :-1]
    if ex is not None: d = d * ex
    g[:, 1:] += d; g[:, :-1] -= d
    d = z[1:] - z[:-1]
    if ey is not None: d = d * ey
    g[1:] += d; g[:-1] -= d
    return g


def links(cut):
    """Link weights for a cut mask: a link between a cut cell (ice) and any other cell is 0."""
    if cut is None or not cut.any(): return None, None
    return (~(cut[:, 1:] ^ cut[:, :-1])).astype(float), (~(cut[1:] ^ cut[:-1])).astype(float)


def _operator(shape, free, lt, lb, ex, ey, beta):
    """The smoothness operator  lt G + lb G diag(beta) G  assembled sparse, reduced to the FREE cells: (rows of the free cells over all cells,
    free x free block, its diagonal, the free cells' indices).  The fit then costs the free cells, not the tile."""
    import scipy.sparse as sp
    h, w_ = shape; N = h * w_
    def D(n): return sp.diags([-np.ones(n - 1), np.ones(n - 1)], [0, 1], shape=(n - 1, n), format="csr")
    Dx = sp.kron(sp.identity(h, format="csr"), D(w_), format="csr"); Dy = sp.kron(D(h), sp.identity(w_, format="csr"), format="csr")
    Wx = sp.diags(np.ones(h * (w_ - 1)) if ex is None else ex.ravel()); Wy = sp.diags(np.ones((h - 1) * w_) if ey is None else ey.ravel())
    G = (Dx.T @ Wx @ Dx + Dy.T @ Wy @ Dy).tocsr()
    fi = np.flatnonzero(free.ravel() > 0)
    Gf = G[fi]                                                                      # free rows
    M = lt * Gf
    if lb: M = M + lb * (Gf @ (G if beta is None else sp.diags(beta.ravel()) @ G))
    M = M.tocsr(); Mff = M[:, fi].tocsr()
    return M, Mff, Mff.diagonal(), fi


def pcg(x, free, wt, rhs, lt, lb, iters, ex=None, ey=None, beta=None, op=None):
    """Jacobi-preconditioned CG on the free cells of  (wt + lt G + lb G beta G) x = rhs,  x given on the others.  `op` caches the
    assembled operator between calls on the same level (the set of free cells, the cuts and the stiffness do not change)."""
    if op is None: op = {}
    if "M" not in op: op["M"], op["Mff"], op["d"], op["fi"] = _operator(x.shape, free, lt, lb, ex, ey, beta)
    M, Mff, dM, fi = op["M"], op["Mff"], op["d"], op["fi"]
    if not fi.size: return x, 0.0
    xf_all = x.ravel(); wf_ = wt.ravel()[fi]
    base = M @ np.where(free.ravel() > 0, 0.0, xf_all)                              # what the fixed cells contribute
    bf = rhs.ravel()[fi] - base; xf = xf_all[fi].copy(); Mi = 1.0 / (wf_ + dM + 1e-12)
    r = bf - (Mff @ xf + wf_ * xf); zv = Mi * r; pv = zv.copy(); rz = float(r @ zv); r0 = np.sqrt(float(r @ r)) + 1e-30
    for _ in range(iters):
        Ap = Mff @ pv + wf_ * pv; pAp = float(pv @ Ap)
        if pAp <= 0: break
        al = rz / pAp; xf += al * pv; r -= al * Ap
        if np.sqrt(float(r @ r)) < 1e-4 * r0: break
        zv = Mi * r; rz1 = float(r @ zv); pv = zv + (rz1 / rz) * pv; rz = rz1
    out = xf_all.copy(); out[fi] = xf
    return out.reshape(x.shape), np.sqrt(float(r @ r)) / r0


def coarsen(a, how):
    h, w_ = a.shape; b = a.reshape(h // 2, 2, w_ // 2, 2)
    if how == "sum": return b.sum((1, 3))
    if how == "all": return b.all((1, 3))
    if how == "any": return b.any((1, 3))
    with np.errstate(invalid="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore"); return np.nanmean(b, (1, 3))


def solve(fixed, zfix, w, b, mu, z1, ub, lam, tau, p, level=0, log=None, cut=None, stop_level=0, beta=None, ops=None):
    """The fit on one level, started from the level below (cascade): fixed cells hold zfix; soft data (w, b = sum w d); the weak pull mu
    toward z1; the bound ub (NaN = none).  Returns (z, active)."""
    h, w_ = fixed.shape
    if min(h, w_) > 48 and h % 2 == 0 and w_ % 2 == 0 and level < 7:
        ct = cut if cut is not None else np.zeros(fixed.shape, bool)
        ncut = coarsen(ct.astype(float), "sum"); cutc = ncut >= 2                       # a coarse cell is ice when half of it is
        fnc = fixed & ~ct; nf = coarsen(fnc.astype(float), "sum"); fx = cutc | ((nf + ncut) == 4)
        zf_nc = np.where(nf > 0, coarsen(np.where(fnc, zfix, 0.0), "sum") / np.maximum(nf, 1), 0.0)
        zf = np.where(cutc, coarsen(np.where(ct, zfix, 0.0), "sum") / np.maximum(ncut, 1), zf_nc)
        wc = coarsen(np.where(fixed, 0.0, w), "sum") + np.where(fx, 0.0, p["w_fix"] * nf)   # the ice's heights never weigh on the bed beside it
        bc = coarsen(np.where(fixed, 0.0, b), "sum") + np.where(fx, 0.0, p["w_fix"] * nf * zf_nc)
        muc = coarsen(mu, "sum"); z1c = np.where(muc > 0, coarsen(mu * z1, "sum") / np.maximum(muc, 1e-30), 0.0)
        zc, _ = solve(fx, zf, wc, bc, muc, z1c, coarsen(ub, "mean"), lam, tau, p, level + 1, log, cutc if cut is not None else None, stop_level, None if beta is None else coarsen(beta, "sum") / 4.0, ops)
        if cut is not None and cutc.any():                                              # carried up on each side of the cut separately
            zm = lambda q, m_: ndimage.zoom(q * m_, 2, order=1, mode="nearest", grid_mode=True) / np.maximum(ndimage.zoom(m_, 2, order=1, mode="nearest", grid_mode=True), 1e-6)
            zl = zm(zc, (~cutc).astype(float)); zi = zm(zc, cutc.astype(float)); has_l = ndimage.zoom((~cutc).astype(float), 2, order=1, mode="nearest", grid_mode=True) > 0.05
            z = np.where(ct, zi, np.where(has_l, zl, ndimage.zoom(zc, 2, order=0, mode="nearest", grid_mode=True)))
        else:
            z = ndimage.zoom(zc, 2, order=1, mode="nearest", grid_mode=True)
    else:
        ok = fixed | (w > 0); z = np.full(fixed.shape, float(np.where(fixed, zfix, b / np.maximum(w, 1e-30))[ok].mean()) if ok.any() else 0.0)
    z = np.where(fixed, zfix, z); free = (~fixed).astype(float)
    if level < stop_level: return z, np.zeros(fixed.shape, bool)                  # a coarse answer was asked for: carried up, not refined
    ex, ey = links(cut); op = (ops if ops is not None else {}).setdefault(level, {})
    lt, lb = lam * tau, lam * (1 - tau) / 4.0 ** level                 # tension is scale-free in 2-D; curvature scales with 1 / h^2
    hasb = np.isfinite(ub) & ~fixed; ubz = np.where(hasb, ub, 0.0); act = np.zeros(fixed.shape, bool); res = 0.0
    mv = 0.0
    for rnd in range(p["rounds"] if level else p["rounds"] + 1):
        act = hasb & (z > ub - 0.02)
        wb = np.where(act, p["bound_w"], 0.0); zp = z
        z, res = pcg(z, free, w + mu + wb, b + mu * z1 + wb * ubz, lt, lb, p["iters"] * (4 if min(h, w_) <= 96 else 1), ex, ey, beta, op)
        mv = float(np.percentile(np.abs(z - zp)[~fixed], 99.9)) if (~fixed).any() else 0.0
    for _ in range(int(p["max_rounds"])):                                           # every level runs until a round no longer moves it
        if mv < p["converge_m"]: break
        act = hasb & (z > ub - 0.02); wb = np.where(act, p["bound_w"], 0.0); zp = z
        z, res = pcg(z, free, w + mu + wb, b + mu * z1 + wb * ubz, lt, lb, p["iters"], ex, ey, beta, op); mv = float(np.percentile(np.abs(z - zp)[~fixed], 99.9)) if (~fixed).any() else 0.0
    if log: log(f"    level {level}: {h}x{w_}, free {int(free.sum())}, bound active {int(act.sum())}, last round moved p99.9 {mv:.3f} m")
    return z, act


def _box(free, pad=4, m=64):
    """The free cells' bounding box, padded and snapped to the cascade's 64-cell grid: the fit works there, not on the whole tile."""
    rr = np.flatnonzero(free.any(1)); cc = np.flatnonzero(free.any(0))
    if not rr.size: return None
    H, W_ = free.shape
    r0 = max((rr[0] - pad) // m * m, 0); r1 = min(-(-(rr[-1] + 1 + pad) // m) * m, H); c0 = max((cc[0] - pad) // m * m, 0); c1 = min(-(-(cc[-1] + 1 + pad) // m) * m, W_)
    return slice(r0, r1), slice(c0, c1)


def solve_in(fixed, zfix, w, b, mu, z1, ub, lam, tau, p, log=None, cut=None, stop_level=0, beta=None, ops=None):
    """solve() on the bounding box of the free cells (a tile with a strip of water along one side costs that strip)."""
    sl = _box(~fixed)
    if sl is None: return np.where(fixed, zfix, 0.0), np.zeros(fixed.shape, bool)
    q = lambda x: None if x is None else x[sl]
    zs, acts = solve(fixed[sl], zfix[sl], w[sl], b[sl], mu[sl], z1[sl], ub[sl], lam, tau, p, 0, log, q(cut), stop_level, q(beta), ops)
    z = np.where(fixed, zfix, 0.0); z[sl] = zs; act = np.zeros(fixed.shape, bool); act[sl] = acts
    return z, act


def profile(env, z, meas, dshore, sloped, res, p, log):
    """Depth under seen water by distance from dry ground: the median of what was measured in this window, never shallower seaward."""
    edges = np.array([0, 4, 8, 16, 32, 64, 128, 256, 512, 1e9]); mid = np.array([2, 6, 12, 24, 48, 96, 192, 384, 768.0])
    dflt = np.clip(p["prof_slope"] * mid, p["bound_gap_m"], p["prof_dmax_m"]); D = dflt.copy()
    k = meas & np.isfinite(env) & ~sloped; dep = env - z; k &= (dep > 0) & (dep < 30); n = int(k.sum()); src = "default"
    if n >= p["prof_min_cells"]:
        src = f"learned from {n} measured cells"
        for i in range(len(mid)):
            kk = k & (dshore >= edges[i]) & (dshore < edges[i + 1])
            if kk.sum() >= 200: D[i] = float(np.median(dep[kk]))
        D = np.maximum.accumulate(np.maximum(D, p["bound_gap_m"]))
    log(f"  depth profile ({src}): " + ", ".join(f"{int(m)} m: {d_:.2f}" for m, d_ in zip(mid, D)))
    return np.interp(dshore, mid, D)


_CACHE = {}


def _rows(path):
    """A points file's rows (x, y, z, sigma, bound, survey), read once per process (a box is solved in a hundred tiles)."""
    if path not in _CACHE:
        _CACHE[path] = [dict(x=float(r["x"]), y=float(r["y"]), z=r["z"], sigma=r["sigma"], bound=r.get("bound") or "", survey=r.get("survey") or "") for r in csv.DictReader(open(path))]
    return _CACHE[path]


def _feats(name):
    """A water/*.geojson layer as [(geometry, properties)], read once per process."""
    if name not in _CACHE:
        from shapely.geometry import shape as shp_
        _CACHE[name] = [(shp_(f["geometry"]), f["properties"]) for f in json.load(open(name if os.path.isabs(name) else WATER / name))["features"] if f.get("geometry")]
    return _CACHE[name]


def main(argv=None, ret=False):
    ap = argparse.ArgumentParser(); ap.add_argument("box"); ap.add_argument("name"); ap.add_argument("win", nargs=4, type=float)
    ap.add_argument("--lakes"); ap.add_argument("--nofig", action="store_true"); ap.add_argument("--nowrite", action="store_true"); ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--tag", default="cm18"); ap.add_argument("--out", default=str(W / "review_1114" / "solve")); ap.add_argument("--holdout", type=float, default=0.0)
    ap.add_argument("--set", nargs="*", default=[]); a = ap.parse_args(argv)
    p = dict(P); p.update({k: type(P[k])(v) for k, v in (s.split("=") for s in a.set)})
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True); rep = []
    def log(s):
        if not a.quiet: print(s, flush=True)
        rep.append(s)
    t0 = time.time(); d = W / a.box / f"out_{a.tag}"; R = json.load(open(d / "recipe.json")); J = Path(R["joint"]["out"]); win = a.win
    jz, res, T = read(J / "joint_z.tif", win); js, _, _ = read(J / "joint_sigma.tif", win); jn, _, _ = read(J / "joint_n.tif", win)
    src, _, _ = read(J / "joint_src.tif", win); cls, _, _ = read(J / "joint_cls.tif", win); wz, _, _ = read(J / "water_z.tif", win)
    cur, _, _ = read(d / f"{a.box}.tif", win); si, _, _ = read(d / "source_index.tif", win); lab, _, _ = read(d / "shore_label.tif", win)
    bid, rb, _ = read(d / "water_bodies.tif", win); lev, _, _ = read(d / "fill_water_level.tif", win)
    zday = read(J / "zone_day.tif", win)[0] if (J / "zone_day.tif").exists() else np.full(jz.shape, np.nan)
    objs = read(J / "layer_objects.tif", win)[0] if (J / "layer_objects.tif").exists() else np.zeros(jz.shape, np.uint8)   # what the layer stage typed an object on the water
    if bid.shape != jz.shape: bid = np.kron(bid, np.ones((int(round(rb / res)),) * 2, bid.dtype))[:jz.shape[0], :jz.shape[1]]
    assert cur.shape == jz.shape == wz.shape == si.shape, (cur.shape, jz.shape, wz.shape, si.shape)
    H0, W0 = jz.shape; m = 64; pad = ((0, (-H0) % m), (0, (-W0) % m))                                  # pad to a multiple of 64 for the cascade
    padf = lambda x, v: np.pad(x, pad, constant_values=v)
    jz, js, wz, cur, lev, zday = (padf(x, np.nan) for x in (jz, js, wz, cur, lev, zday)); jn, src, cls, si, lab, bid, objs = (padf(x, 0) for x in (jn, src.astype(np.int64), cls, si, lab, bid, objs))
    inwin = np.zeros(jz.shape, bool); inwin[:H0, :W0] = True; src = src.astype(np.int64)
    log(f"[{a.name}] {a.box} {a.tag} window {win} : {H0}x{W0} cells at {res:g} m")
    with rasterio.open(J / "joint_z.tif") as s_: crs_ = s_.crs
    bits = json.load(open(J / "joint_report.json"))["src_bits"]; kind = {s["name"]: s.get("kind") for s in R["joint"]["sources"]}
    mask = lambda names: sum(v for k, v in bits.items() if k in names)
    comp_b = mask(COMP); pts_b = mask([k for k in bits if kind.get(k) == "points"]); lidar_b = mask([k for k in bits if kind.get(k) == "lidar"])
    doubt_b = mask(DOUBT); grid_b = mask([k for k in bits if kind.get(k) == "grid" and k not in COMP and k != "arcticdem"])
    has = np.isfinite(jz) & np.isin(cls, (1, 3, 4)) & ((src & ~pts_b) != 0)
    # WHAT THE LAYER STAGE TYPED AN OBJECT ON THE WATER (a float, a boat: layer_objects, 720d30f) IS NO GROUND (Hig's review of 2026-11-15,
    # the Homer harbour: "clumps of bad points" -- the solve read the joint's cells and not that typing, held 68 float cells fixed 3-8 m
    # above the floor in one 200 m window and bent the bed up to them; and with floats as "confident ground" the low-water polygon was
    # set aside as stale over the whole basin).
    # ...nor is its fringe: the cells within 4 m of it, in the water, that are no bed return (a float's edge mixed with the water beside it
    # stood as soft "ground" half way up and left a mound where the float was taken out)
    objm = (objs > 0) | (ndimage.binary_dilation(objs > 0, iterations=max(int(round(4.0 / res)), 1)) & np.isin(lab, (2, 3)) & (cls != 3))
    n_obj = int((has & objm).sum()); has &= ~objm
    comp_only = has & ((src & ~(comp_b | pts_b)) == 0)
    meas = has & ~comp_only
    conf = meas & ((((src & lidar_b) != 0) & (jn >= p["fix_min_n"])) | ((src & grid_b) != 0)) & (js <= p["fix_sigma_max_m"])   # a gridded cell on a steep bed carries what gridding lost in its sigma: weighted, not fixed
    cov = ndimage.binary_closing(conf, np.ones((3, 3), bool)) | conf                                   # a survey posted wider than the grid is still coverage
    din = ndimage.distance_transform_edt(cov) * res                                                    # m inside coverage
    # --- fixed cells ---------------------------------------------------------------------------------------------------
    # STILL WATER above the tide (lakes, ponds, the proglacial lake) and the ICE: what the water and shoreline stages typed, taken as typing.
    #   - a cell they label water or floating, with a level, in a body that is no river: its bed is BELOW that level (a bound), and what
    #     stands in it at the surface -- an SfM model's water, a lidar's water read as ground, a berg -- is no evidence of the bed.  Only a
    #     bed measurement (sonar, a sounding) is.
    #   - a small river-kind patch of water standing ABOVE the still water that rings it is water on something floating (a berg top): it
    #     joins the lake at the lake's level (the stage's own berg rule, applied to what it kinded a river: the 51 m pillar at the face).
    #   - glacier ice is a surface of its own: fixed as the product has it, and CUT from the bed (ice_face "free": the face is a cliff, the
    #     bed beside it is not a continuation of the ice).
    kinds = {}; tab = {}
    try:
        tab = {int(f["properties"]["id"]): f["properties"] for f in json.load(open(d / "water_bodies.geojson"))["features"]}
        ov = d / "shore_bodies.json"
        if ov.exists():
            for k, o in (json.load(open(ov)).get("overrides") or {}).items():
                if int(k) in tab and "kind" in o: tab[int(k)]["kind"] = o["kind"]
        kinds = {k: v.get("kind") for k, v in tab.items()}
    except Exception as e: log(f"  (no body table: {e})")
    rivers = [k for k, v in kinds.items() if v in ("river", "tidal", "sea")]                            # water.py FLAT_KINDS (lake, perched, ephemeral) have one level: still water
    wetl = np.isin(lab, (2, 3)) & np.isfinite(lev) & (lev > p["tide_max_m"] + 1.0) & inwin
    still = wetl & ~np.isin(bid, rivers); n_berg = 0; bergm = np.zeros(jz.shape, bool)
    cand = wetl & ~still
    if cand.any() and still.any():
        L_, n_ = ndimage.label(cand)
        for i_, sl_ in enumerate(ndimage.find_objects(L_), 1):
            sl2 = tuple(slice(max(s.start - 4, 0), s.stop + 4) for s in sl_); c_ = L_[sl2] == i_
            if c_.sum() * res * res > p["berg_max_m2"]: continue
            ring = ndimage.binary_dilation(c_, iterations=3) & ~c_; rs = ring & still[sl2]
            if ring.sum() and rs.sum() >= 0.6 * ring.sum():                              # a "river" ringed by a lake is the lake (its water returns stood on a berg)
                lev[sl2][c_] = float(np.median(lev[sl2][rs])); still[sl2] |= c_; cur[sl2][c_] = np.nan; bergm[sl2] |= c_; n_berg += int(c_.sum())   # its "bed" in the product is the pillar: no prior from it
    ice = (lab == 4) & np.isfinite(cur) & inwin
    notbed = still & has & (cls != 3)                                                                   # the surface of the water, or something on it
    n_edit = 0; editp = np.zeros(jz.shape, bool)                                                         # ...the product's own modelled cells there are not its shape either
    if EDITS.exists():                                                                                   # ...and what a reviewer has said is not bed (the 59.47321 multibeam pit)
        from shapely.geometry import box as sbox_
        for g_, pr_ in _feats(str(EDITS)):
            if pr_.get("kind") != "not_bed" or not g_.intersects(sbox_(*win)): continue
            ep = np.pad(features.rasterize([(g_, 1)], out_shape=(H0, W0), transform=T).astype(bool), pad); em = ep & has
            if pr_.get("deeper_than_m") is not None: em &= jz < float(pr_["deeper_than_m"]); ep &= np.isfinite(cur) & (cur < float(pr_["deeper_than_m"]))
            notbed |= em; n_edit += int(em.sum()); editp |= ep
    has &= ~notbed; meas &= ~notbed; conf &= ~notbed; comp_only &= ~notbed
    lake = still
    # A BODY OF ONE LEVEL WITHIN THE TIDE'S REACH (a lagoon, a pond: no river, whose one level is not its surface, and not the open sea).
    # A body a MAP calls a lake is one too, whatever the box edge made of its kind: the same lagoon is "tidal" in the box that holds all of it
    # and "sea" in the three that cut it (59.14455,-151.50240; Gore Lake in r3c5), and the mosaic then blends a bounded bed with bridged ones.
    one_level = [k for k, v in kinds.items() if v in ("lake", "perched", "ephemeral", "tidal")
                 or (v != "river" and tab.get(k, {}).get("mapped_kind") == "lake" and (tab.get(k, {}).get("mapped_frac") or 0) >= 0.5)]
    pond = np.isin(lab, (2, 3)) & np.isfinite(lev) & np.isin(bid, one_level) & ~still & inwin
    # A LAKE WITH NO SOUNDING IN IT IS OUT OF SCOPE (Hig: "the small lakes are ok for now"): its bed is the lake model's, fixed here.  The box
    # driver passes the lakes that hold soundings (--lakes, decided on the whole box so that a lake is not solved in one tile and kept in the next).
    if a.lakes:
        lk_, _, _ = read(a.lakes, win); lake_solve = padf(lk_, 0) > 0
    else:
        Ls_, ns_ = ndimage.label(still, structure=np.ones((3, 3), bool)); hit = np.zeros(ns_ + 1, bool)
        for path in R["fill"]["points"]:
            for row in _rows(path):
                if win[0] < row["x"] < win[2] and win[1] < row["y"] < win[3]:
                    c_, r_ = ~T * (row["x"], row["y"]); hit[Ls_[int(r_), int(c_)]] = True
        hit[0] = False; lake_solve = hit[Ls_]
    lake_keep = still & ~lake_solve & np.isfinite(cur) & (cur < lev) & ~bergm
    land3 = np.isin(si, (0, 1)) & np.isfinite(cur) & ~has & inwin & ~still & ~objm                    # (an object the product still shows from another layer is no prior land either)
    fix_meas = conf & (din > p["fix_edge_m"]) & ~ice
    # GROUND ABOVE THE TIDE'S REACH IS OUT OF SCOPE: as the product has it.  That holds for what the product MODELLED there too (a hole in the
    # land it bridged from a ground prior, the icefield's gaps: 2026-11-15, 97,000 cells at 1,200 m re-bridged 30 m off), unless the cell is
    # water to the stages or to a lidar, or stands within scope_dry_m of the margin (a bank, a bluff toe: solved with it, so no seam at the tide's reach).
    wetc = np.isin(lab, (2, 3)) | np.isfinite(wz); margin = (np.isfinite(cur) & (cur <= p["tide_max_m"] + 1.0)) | wetc | still
    dryfill = (si == 3) & ~wetc & (ndimage.distance_transform_edt(~margin) * res > p["scope_dry_m"])
    high = ((si != 3) | dryfill) & np.isfinite(cur) & (cur > p["tide_max_m"] + 1.0) & inwin & ~still
    # SCOPE: the water margin.  The modelled sea deeper than scope_deep_m is as the product has it (its basin spline between soundings).
    # ...of the open sea: in a one-level body the product is a flat sheet with a cone at each sounding (Gore Lake, Hig 2026-11-15: "a nearly
    # flat bottom with circular basins around the soundings"), no shape to keep.
    scope = (si == 3) & np.isfinite(cur) & (cur < p["scope_deep_m"]) & ~still & inwin & ~editp & ~pond
    fix_other = (ice | high | scope | lake_keep) & ~fix_meas
    # A fixed measured cell stands AS THE PRODUCT SHOWS IT where the product shows a measured layer (its composite of the layers: 0.94 km2 of
    # the Spit box differs from the joint's own cell by a median 0.26 m), and as the joint measured it where the product had MODELLED over it
    # (a gravel bar the shore stage labelled water and the river-bed rule dug 1.5 m into: Hig 2026-11-15, the Woz flat, "false rocks ... not
    # present in the lidar" -- the solve held the bar, the box driver kept the product's cell).  The driver writes these cells as returned here.
    fixed = fix_meas | fix_other; zfix = np.where(fix_meas, np.where((si != 3) & np.isfinite(cur), cur, jz), np.where(fix_other, cur, 0.0))
    if ret and int((~fixed & inwin).sum()) < 20: return None                                             # nothing to solve in this tile
    if still.any(): log(f"  still water above the tide: {still[inwin].mean():.1%} of the window (level p50 {np.nanmedian(lev[still]):.2f} m); {int(notbed.sum())} cells standing at its surface are no bed evidence; {n_berg} cells of berg-top water joined to it; ice {ice[inwin].mean():.1%} (cut from the bed); lakes with no sounding left as the product has them: {lake_keep[inwin].mean():.1%}; reviewer's edits: {n_edit} cells not bed")
    # --- soft data -----------------------------------------------------------------------------------------------------
    w = np.zeros(jz.shape); b = np.zeros(jz.shape)
    soft = meas & ~fixed & np.isfinite(js) & ~ice
    sg = np.maximum(np.where(np.isfinite(js), js, 1.0), 0.03) * (1.0 + p["edge_gain"] * np.exp(-din / p["edge_len_m"]))
    w[soft] = 1 / sg[soft] ** 2; b[soft] = jz[soft] / sg[soft] ** 2; lidar_soft = soft.copy()
    s3 = land3 & ~fixed; w[s3] += 1 / p["prior3dep_sigma_m"] ** 2; b[s3] += cur[s3] / p["prior3dep_sigma_m"] ** 2
    rng = np.random.default_rng(1); held = []; ns = nb_ = n_stale = 0; snd = np.zeros(jz.shape, bool); ws = np.zeros(jz.shape); bs = np.zeros(jz.shape); mins = []; dzp = R["fill"].get("points_dz_m") or {}; dzp = dzp if isinstance(dzp, dict) else {}
    for path in R["fill"]["points"]:
        for row in _rows(path):
            px, py = row["x"], row["y"]
            if not (win[0] < px < win[2] and win[1] < py < win[3]): continue
            c, r = ~T * (px, py); r, c = int(r), int(c)
            if fixed[r, c]: continue
            # a cast older than the survey of the moving zone it lies in (the joint's zone_day), or a sea cast in still water above the tide,
            # is no evidence of today's bed (fill.py _point_stale: the Woz delta's 1978 casts under 2023's built-up flat)
            if np.isfinite(zday[r, c]):
                ys_ = [float(y_) for y_ in re.findall(r"(?:19|20)\d\d", row.get("survey") or "")] or [float(y_) for y_ in re.findall(r"(?:19|20)\d\d", os.path.basename(path))]
                if not ys_ or max(ys_) + 1.0 < zday[r, c]: n_stale += 1; continue
            if still[r, c] and re.search(r"nos_|crm_", os.path.basename(path)): n_stale += 1; continue
            zz = float(row["z"]) + float(dzp.get(row.get("survey"), 0.0)); s_ = max(float(row["sigma"] or 0.5), p["sounding_sigma_min_m"])
            if row.get("bound"):                                                    # "min": the line did not reach the bed -- the bed is at least this deep
                nb_ += 1; mins.append((r, c, zz, s_)); continue
            if a.holdout and rng.random() < a.holdout: held.append((r, c, zz, s_)); continue
            ws[r, c] += 1 / s_ ** 2; bs[r, c] += zz / s_ ** 2; ns += 1; snd[r, c] = True
    nl = nlf = 0; linecells = np.zeros(jz.shape, bool)
    from shapely.geometry import shape as shp, box as sbox
    wb_ = sbox(*win)
    for g, pr_ in _feats("tide_lines.geojson"):
        if not g.intersects(wb_): continue
        lv = float(pr_["level_m"]); s_ = max(float(pr_.get("level_sigma_m") or 0.3), p["line_sigma_m"])
        mk = features.rasterize([(g, 1)], out_shape=(H0, W0), transform=T, all_touched=True).astype(bool); mk = np.pad(mk, pad)
        nlf += int((mk & fixed).sum()); mk &= ~fixed; nl += int(mk.sum()); linecells |= mk
        w[mk] += 1 / s_ ** 2; b[mk] += lv / s_ ** 2
    if n_obj: log(f"  objects on the water (the layer stage's typing): {n_obj} cells are no ground")
    log(f"  fixed: measured {fix_meas[inwin].mean():.1%}, lakes/ice/high prior {fix_other[inwin].mean():.1%}; soft measured {soft[inwin].mean():.1%} (of them compilations excluded: {comp_only[inwin].mean():.1%} of the window are prior only)")
    log(f"  soundings {ns} (+{len(held)} held out; {nb_} depth bounds; {n_stale} stale in a moving zone, not used); tide-line cells {nl} free, {nlf} on fixed cells (not honoured)")
    # --- the bound: the water a lidar saw ----------------------------------------------------------------------------------
    wf = np.isfinite(wz); env = np.full(jz.shape, np.nan); sloped = np.zeros(jz.shape, bool)
    if wf.any():
        lo = ndimage.minimum_filter(np.where(wf, wz, np.inf), size=5); lo = np.where(np.isfinite(lo), lo, 0.0); k_ = ndimage.binary_dilation(wf, iterations=2).astype(float)
        num = ndimage.gaussian_filter(lo * k_, 3.0); den = ndimage.gaussian_filter(k_, 3.0); env = np.where(wf & (den > 0.05), num / np.maximum(den, 1e-9), np.nan)
        sgs = p["slope_len_m"] / res                                                                                     # the water's slope as a whole (rising over ~100 m), not its ripples
        e2n = ndimage.gaussian_filter(np.where(wf, wz, 0.0), sgs); e2d = ndimage.gaussian_filter(wf.astype(float), sgs); e2 = np.where(e2d > 0.2, e2n / np.maximum(e2d, 1e-9), np.nan)
        gy, gx = np.gradient(e2, res); sl = np.hypot(gx, gy); sloped = wf & np.isfinite(sl) & (sl > p["sloped_water"])
        # ...but ONE surface running downhill, not two stands of the tide side by side (a survey flown over several tides -- Kenai 2008 --
        # shows the sea at +3.9 beside the sea at -0.2): where the water seen steps by more than step_m within 10 m it is two surfaces, and
        # nothing within slope_len_m of such a step is called sloping
        rng_ = ndimage.maximum_filter(np.where(wf, wz, -np.inf), size=5) - ndimage.minimum_filter(np.where(wf, wz, np.inf), size=5)
        step = wf & np.isfinite(rng_) & (rng_ > p["step_m"])
        if step.any(): sloped &= ndimage.distance_transform_edt(~step) * res > p["slope_len_m"]
    wf &= ~still; sloped &= ~still; env = np.where(still, np.nan, env)                 # in still water the body's LEVEL is the bound, not the returns off its surface and its bergs
    # Hig's low-tide water (water/tide_low_water.geojson, the -1.87 m line): inside it, away from its own line and from every mapped rock and
    # island, the bed is below low water.  A bound like the seen water -- and a measured cell standing above it there is not ground.
    lowb = np.zeros(jz.shape, bool); n_low = 0
    try:
        gl = [g.intersection(wb_.buffer(100)) for g, _p in _feats("tide_low_water.geojson") if g.intersects(wb_)]
        if gl:
            ins = np.pad(features.rasterize([(g, 1) for g in gl if not g.is_empty], out_shape=(H0, W0), transform=T).astype(bool), pad)
            keep = np.ones(jz.shape, bool)
            pts = [g for g, _p in _feats("rocks.geojson") if g.intersects(wb_.buffer(60))]
            isl = [g for g, _p in _feats("tide_islands.geojson") if g.intersects(wb_.buffer(60))]
            if pts or isl:
                rk = np.pad(features.rasterize([(g, 1) for g in pts + isl], out_shape=(H0, W0), transform=T, all_touched=True).astype(bool), pad)
                keep &= ndimage.distance_transform_edt(~rk) * res > p["low_rock_m"]
            lowb = ins & keep & (ndimage.distance_transform_edt(ins) * res > p["low_line_m"])
    except Exception as e: log(f"  (low-water polygon not read: {e})")
    if lowb.any():
        # the polygon is a traced line, and on a moving bed it is stale (Hig, the Grewingk delta): where CONFIDENT measurements (fixed: dense,
        # inside their coverage) stand above it, they win and the polygon is not used within low_stale_m of them.  Elsewhere it outvotes
        # thin or edge "ground" (Anchor Point's offshore patches).
        # WHICH measurements outrank the mapping is a property of the survey, not of the place: a survey listed in DOUBT (Kenai 2008: vendor
        # ground class, patches of "ground" on the sea -- Hig 2026-11-14: bad points, to be reclassified) never does.
        trust = fix_meas & ((src & (lidar_b | grid_b) & ~doubt_b) != 0)
        stale = lowb & trust & (jz > p["low_level_m"] + p["low_tol_m"]); n_st = float(lowb.sum())
        if stale.any(): lowb &= ndimage.distance_transform_edt(~stale) * res > p["low_stale_m"]
        badm = lowb & meas & ~trust & (jz > p["low_level_m"] + p["low_tol_m"]); n_low = int(badm.sum()); sb = badm & soft
        w[sb] -= 1 / sg[sb] ** 2; b[sb] -= jz[sb] / sg[sb] ** 2; w = np.maximum(w, 0.0); b[w == 0] = 0.0
        fixed &= ~badm; fix_meas &= ~badm; zfix = np.where(badm, 0.0, zfix); soft &= ~badm; meas &= ~badm
        # ...and so does it outvote PRIOR LAND there -- a cell the product shows from a fallback DTM where the joint holds nothing (the DGGS
        # 2019 bare-earth grid carries the harbour's floats as ground at +1.6..+4.5 m; weaker evidence than a thin lidar cell)
        badl = lowb & s3 & (cur > p["low_level_m"] + p["low_tol_m"]); n_lowl = int(badl.sum())
        w[badl] -= 1 / p["prior3dep_sigma_m"] ** 2; b[badl] -= cur[badl] / p["prior3dep_sigma_m"] ** 2; w = np.maximum(w, 0.0); b[w == 0] = 0.0
        log(f"  low-tide water: used on {lowb[inwin].mean():.1%} of the window ({1 - lowb.sum() / max(n_st, 1):.0%} of it set aside as stale: confident ground stands above it); {n_low} measured cells (thin, at an edge, or of a doubted survey) above {p['low_level_m'] + p['low_tol_m']:g} m there dropped as evidence, and {n_lowl} cells of prior land")
    ub = np.where(fixed, np.nan, env - p["bound_gap_m"])
    ub = np.where(lowb & ~fixed, np.fmin(ub, p["low_level_m"]), ub)
    # a thin or edge "ground" cell AT OR ABOVE the water the lidar saw in that same cell is the water (or something on it), not bed:
    # it contradicts the bound, so it is no evidence.  A rock standing out of the water has no water return on it and keeps its cells.
    wet_g = soft & np.isfinite(ub) & (jz > ub)
    w[wet_g] -= 1 / sg[wet_g] ** 2; b[wet_g] -= jz[wet_g] / sg[wet_g] ** 2; w = np.maximum(w, 0.0); b[w == 0] = 0.0; soft &= ~wet_g; lidar_soft = soft.copy()
    log(f"  soft measured cells at or above the water seen in their own cell: {int(wet_g.sum())} dropped as evidence")
    ub = np.where(still & ~fixed, lev - p["still_gap_m"], ub)
    # A BODY OF ONE LEVEL WITHIN THE TIDE'S REACH (a lagoon, a pond: no river, whose one level is not its surface, and not the open sea) has
    # its bed below that level whether a lidar saw the water or a map did (2026-11-15: the outer coast's lagoons, known only from 3DEP's lake
    # polygons, were bridged over at the level of the ground around them).  The product holds them under the same sheet.
    ub = np.where(pond & ~fixed, np.fmin(ub, lev - p["still_gap_m"]), ub)
    if pond.any(): log(f"  one-level water within the tide's reach (lagoons, ponds): {pond[inwin].mean():.1%} of the window bounded by its level")
    # SLOPED WATER HAS GROUND JUST BENEATH IT (Hig 2026-11-14, the Woz delta): where a lidar saw water running downhill and nothing measured
    # its bed, the bed is sloped_depth_m under that water -- data (sigma sloped_sigma_m), not a prior the fit may wander from.
    sd = sloped & ~fixed & np.isfinite(env) & ~lowb & ~pond                                                      # inside Hig's low-tide water the water seen is the sea at some stand, not a stream
    # ...and not in a body of one level: the returns off a pond's or a high tide's surface, rough at the edge of a patch, are no stream
    # (Anchor Point, 59.74080,-151.85055: 262 cells of the 2008 sea at +3.7 m on the beach face took a bed at +3.4, a 3.5 m knob; Hig 2026-11-15)
    # ...and a shallow stream only where nothing says the water is deep: not within 20 m of a compilation cell or a modelled sea bed more than
    # 2 m under the water seen (Halibut Cove's narrows: tide running over a 30 m channel that NCEI has)
    dp_ = (comp_only & np.isfinite(env) & (jz < env - 2.0)) | ((si == 3) & np.isfinite(cur) & np.isfinite(env) & (cur < env - 2.0) & (cur < p["deep_prior_m"]))
    if dp_.any(): sd &= ~ndimage.binary_dilation(dp_, iterations=int(round(20.0 / res)))
    w[sd] += 1 / p["sloped_sigma_m"] ** 2; b[sd] += (env[sd] - p["sloped_depth_m"]) / p["sloped_sigma_m"] ** 2
    # LONE DEEP RETURNS: a thin or edge measured cell more than lone_deep_m below the DEEPEST confident measurement or sounding within lone_ref_m is
    # noise in the water column, not bed (the 59.47321,-151.68254 pit: green-lidar "bed" at -23 m beside multibeam at -5 and soundings at -4)
    k_ = int(round(p["lone_ref_m"] / res)) * 2 + 1
    ref = ndimage.minimum_filter(np.minimum(np.where(fix_meas, jz, np.inf), np.where(ws > 0, bs / np.maximum(ws, 1e-30), np.inf)), size=k_)
    lone = soft & np.isfinite(ref) & (jz < ref - p["lone_deep_m"]) & (p["lone_deep_m"] > 0)             # OFF by default (lone_deep_m 0): on the Woz box it flattened Halibut Cove's real 30 m channel; a wrong hole is a reviewer's edit (EDITS)
    # ...and only a CLOSED, small hole: a steep wall is also "deeper than the flat above it", but it runs on into deeper measurements.  A
    # patch is dropped when it is under lone_max_m2 and nothing measured within 6 m of it lies deeper than it does.
    if lone.any():
        Ll, nl_ = ndimage.label(ndimage.binary_closing(lone, np.ones((3, 3), bool)) | lone, structure=np.ones((3, 3), bool)); keep_l = np.zeros(nl_ + 1, bool)
        allm = np.isfinite(jz) & (fixed | soft)
        for i_, sl_ in enumerate(ndimage.find_objects(Ll), 1):
            sl2 = tuple(slice(max(s.start - 4, 0), s.stop + 4) for s in sl_); c_ = Ll[sl2] == i_
            if c_.sum() * res * res > p["lone_max_m2"]: continue
            ring = ndimage.binary_dilation(c_, iterations=3) & ~c_ & allm[sl2] & ~lone[sl2]
            zc_ = float(np.median(jz[sl2][c_ & lone[sl2]]))
            if not ring.any() or float(np.min(jz[sl2][ring])) > zc_ - 1.0: keep_l[i_] = True
        lone &= keep_l[Ll]
    w[lone] -= 1 / sg[lone] ** 2; b[lone] -= jz[lone] / sg[lone] ** 2; w = np.maximum(w, 0.0); b[w == 0] = 0.0; soft &= ~lone; meas &= ~lone; lidar_soft = soft.copy()
    log(f"  sloped water: {int(sd.sum())} cells take a bed {p['sloped_depth_m']:g} m under it as data; lone deep bed cells dropped: {int(lone.sum())}")
    log(f"  water seen over {wf[inwin].mean():.1%} of the window ({sloped[inwin].mean():.1%} sloping)")
    # --- the prior z0 and its level ------------------------------------------------------------------------------------------
    dry = (meas & ~wf) | (high & ~lake) | (land3 & (cur > p["tide_max_m"]))
    dshore = ndimage.distance_transform_edt(~dry) * res if dry.any() else np.full(jz.shape, 1e4)
    z0 = np.full(jz.shape, np.nan)
    if wf.any():
        D = profile(env, jz, meas, dshore, sloped, res, p, log)
        z0 = np.where(wf & ~sloped, env - D, np.nan)
    # THE AUDIT OF THE COMPILATIONS (Hig 2026-11-14: "an audit to make sure we're getting a similar result when we do our own gridding";
    # 2026-11-15, the Woz flat's "strange shallow basin" and Halibut Cove's "unrealistic basins").  A compilation is a prior, and it is
    # believed as far as it agrees with what we measured: its own grid is read on the cells we hold a confident measurement of in the tide's
    # reach and below, and the residual there -- averaged over audit_len_m, counted on audit_n0 cells -- is taken off it where it is the
    # only source; where the residuals scatter (audit_sd_m) its pull fades and the gap is bridged by the measurements round it instead.
    # (OCM on the Woz flat reads 2.3 +- 0.2 m under 8,861 lidar cells, so the 1,057 cells of it that passed as "below low water" in a
    # pool the lidar saw at +0.8 m were a 2.4 m pit; NCEI in Halibut Cove's lagoon reads a median 2.3 m, up to 17 m, under 37,632.)
    caud = np.zeros(jz.shape); cdis = np.ones(jz.shape); n_aud = 0
    if comp_only.any():
        from rasterio.warp import reproject, Resampling
        fa = 8; hca, wca = jz.shape[0] // fa, jz.shape[1] // fa; blka = lambda x: x.reshape(hca, fa, wca, fa).sum((1, 3)); sga = p["audit_len_m"] / (res * fa)
        up = lambda x: ndimage.zoom(x, fa, order=1, mode="nearest", grid_mode=True)
        aud = conf & np.isfinite(jz) & (jz < p["tide_max_m"] + 1.0) & ~ice & inwin
        numc = np.zeros(jz.shape); denc = np.zeros(jz.shape); sdc = np.zeros(jz.shape)
        for s_ in R["joint"]["sources"]:
            nm_ = s_["name"]
            if nm_ not in COMP or nm_ not in bits or not s_.get("src"): continue
            hb = comp_only & ((src & bits[nm_]) != 0)
            if not hb.any(): continue
            raw = np.full((H0, W0), np.nan, np.float32)
            with rasterio.open(s_["src"]) as ds_: reproject(rasterio.band(ds_, 1), raw, dst_transform=T, dst_crs=crs_, resampling=Resampling.bilinear, src_nodata=ds_.nodata, dst_nodata=np.nan)
            raw = padf(raw.astype(np.float64), np.nan) + float(s_.get("shift_m") or 0.0); raw[raw < -9000] = np.nan
            ok = aud & np.isfinite(raw)
            if ok.sum() < p["audit_n0"]: continue
            r_ = np.clip(np.where(ok, raw - jz, 0.0), -30.0, 30.0); gk = ndimage.gaussian_filter(blka(ok.astype(float)), sga); nk = gk * (2 * np.pi * sga * sga)
            mk_ = ndimage.gaussian_filter(blka(r_), sga) / np.maximum(gk, 1e-9); m_ = up(mk_)
            vk = ndimage.gaussian_filter(blka(np.where(ok, (r_ - m_) ** 2, 0.0)), sga) / np.maximum(gk, 1e-9)
            t_ = up(np.clip(nk / p["audit_n0"], 0.0, 1.0)); wk_ = hb / float(s_.get("sigma_m") or 1.0) ** 2
            numc += wk_ * t_ * m_; denc += wk_; sdc += wk_ * t_ * np.sqrt(np.maximum(up(vk), 0.0)); n_aud += int(ok.sum())
            log(f"  audit of {nm_}: its grid read on {int(ok.sum())} cells we measured: compilation - measured p10/p50/p90 {np.percentile((raw - jz)[ok], [10, 50, 90]).round(2)} m; "
                f"where it is the only source ({int(hb.sum())} cells) it is corrected by p5/p50/p95 {np.percentile((-t_ * m_)[hb], [5, 50, 95]).round(2)} m, local scatter p50 {np.median((t_ * np.sqrt(np.maximum(up(vk), 0.0)))[hb]):.2f} m")
        caud = np.where(denc > 0, numc / np.maximum(denc, 1e-30), 0.0)
        cdis = np.where(denc > 0, 1.0 / (1.0 + (sdc / np.maximum(denc, 1e-30) / p["audit_sd_m"]) ** 2), 1.0)
    z0 = np.where(comp_only, np.where(np.isfinite(ub), np.minimum(jz - caud, ub), jz - caud), z0)
    # the deep basin between soundings, and a lake's bed, keep the present model as their SHAPE (a prior, not a fixed surface)
    deep = (si == 3) & np.isfinite(cur) & ~comp_only & ~editp & ~pond & (((cur < p["deep_prior_m"]) & ~still) | (still & (cur < lev - 0.5)))
    z0 = np.where(deep, cur, z0)
    # the prior's small holes (a compilation cell the joint set aside under a lidar's water or "ground") take the prior around them
    h0 = np.isfinite(z0); gn = ndimage.gaussian_filter(np.where(h0, z0, 0.0), 10.0); gd = ndimage.gaussian_filter(h0.astype(float), 10.0)
    near = ~h0 & ~fixed & (gd > 0.02) & (ndimage.distance_transform_edt(~h0) * res <= p["prior_fill_m"])
    z0 = np.where(near, gn / np.maximum(gd, 1e-9), z0)
    has0 = np.isfinite(z0)
    # the prior's LEVEL, one field per kind of prior (a guessed profile is corrected freely by the beds measured under the same water; a
    # compilation is corrected by the soundings and beds inside and beside it; the deep basin's spline already passes through its soundings).
    # Kept apart so the profile's large corrections do not leak into a compilation beside it.
    ptype = np.where(deep, 3, np.where(comp_only | (near & ~np.isfinite(env)), 2, np.where(has0, 1, 0))).astype(np.uint8)
    cz = np.zeros(jz.shape); f = int(p["level_cell"]); hc, wc_ = jz.shape[0] // f, jz.shape[1] // f; blk = lambda x: x.reshape(hc, f, wc_, f).sum((1, 3))
    dobs = np.where(fixed, zfix, np.where(ws > 0, bs / np.maximum(ws, 1e-30), np.where(w > 0, b / np.maximum(w, 1e-30), np.nan)))
    z0n = z0.copy()                                                    # measured cells beside a prior read the prior next to them
    if has0.any():
        idx = ndimage.distance_transform_edt(~has0, return_distances=True, return_indices=True); nearp = (idx[0] * res <= 2 * res * f) & ~has0
        z0n[nearp] = z0[idx[1][0][nearp], idx[1][1][nearp]]; ptn = ptype.copy(); ptn[nearp] = ptype[idx[1][0][nearp], idx[1][1][nearp]]
        for kind_, soft_ in ((1, p["level_soft"]), (2, p["level_soft_comp"])):
            isd = (ptn == kind_) & ((meas & (cls == 3)) | snd) & ~lake & ~ice & np.isfinite(dobs) & np.isfinite(z0n)
            if not isd.any() or not (ptype == kind_).any(): continue
            rr = np.where(isd, dobs - z0n, 0.0); wr = np.where(isd, np.where(fixed, p["w_fix"], np.minimum(w + ws, p["w_fix"])), 0.0)
            Wc = blk(wr); Bc = blk(wr * rr); lt = 1.0; muc = lt / (p["level_len_m"] / (res * f)) ** 2
            c0, rs = pcg(np.zeros((hc, wc_)), np.ones((hc, wc_)), Wc * soft_ + muc, Bc * soft_, lt, 0.0, 800)
            ck = ndimage.zoom(c0, f, order=1, mode="nearest", grid_mode=True); cz = np.where(ptype == kind_, ck, cz)
            log(f"  prior kind {kind_} ({'profile' if kind_ == 1 else 'compilation'}) on {(ptype == kind_)[inwin].mean():.1%} of the window; level correction from {int(isd.sum())} bed / sounding cells: p5/p50/p95 {np.percentile(ck[ptype == kind_], [5, 50, 95]).round(2)} m")
    z1 = np.where(has0, z0 + cz, 0.0)
    if np.isfinite(ub).any(): z1 = np.where(np.isfinite(ub), np.minimum(z1, ub), z1)
    mu = np.where(has0 & ~fixed, p["lam"] * (1 - p["tau"]) / (np.where(deep, p["deep_len_m"], np.where(comp_only, p["comp_len_m"], p["prior_len_m"])) / res) ** 4, 0.0)
    # beside confident measurements a prior adds nothing they do not say better: its pull fades in within prior_near_m of them, so a gap
    # narrower than that is bridged by the measurements on its two sides (the -26 m compilation pit between lidar and multibeam)
    fxn = fixed & ~ice                                                                    # the ice is no measurement of the bed: a prior does not fade beside it
    dfx = ndimage.distance_transform_edt(~fxn) * res if fxn.any() else np.full(jz.shape, 1e4)
    mu *= np.where(comp_only, cdis, 1.0)                                                # a compilation that scatters about our measurements pulls less (the audit)
    mu *= np.where((ptype == 2) & (p["taper_comp"] == 0), 1.0, np.minimum(1.0, (dfx / np.where(deep, p["deep_near_m"], p["prior_near_m"])) ** 4))   # a compilation is NOT faded: in a narrow inlet it is all there is (Woz box: a -6.5 m inlet rose to -0.2)
    # --- the solve -----------------------------------------------------------------------------------------------------------
    # SOUNDINGS are sparse points with noise: fitted one by one they dimple the surface (Hig: "localized high curvature").  They adjust a
    # smooth surface at THEIR OWN SPACING instead:  (A) the fit WITHOUT them;  (B) their residuals to A, averaged by a kernel never
    # narrower than what holds sound_n0 of them (8 .. 128 m), shrinking to nothing where there are none;  (C) the fine fit leans on A + that.
    # A "bed at least this deep" row the surface stands above joins them at its depth.
    t1 = time.time(); cutm = ice if ice.any() else None
    # ...a DENSE run of soundings (a sonar track: sound_n0 of them within 8 m) is a measured profile, not sparse points: those enter as data
    dense = (ws > 0) & (ndimage.gaussian_filter((ws > 0).astype(float), 8.0 / res) * (2 * np.pi * (8.0 / res) ** 2) >= p["sound_n0"])
    w = w + np.where(dense, ws, 0.0); b = b + np.where(dense, bs, 0.0); n_dense = int(dense.sum()); ws = np.where(dense, 0.0, ws); bs = np.where(dense, 0.0, bs); wsd = np.where(dense, w, 0.0)
    # THE SHORELINE OF STILL WATER IS A BREAK OF SLOPE (waves cut a bench there: the lake's shore soundings stand 1-2 m above the land's
    # slope carried down): the bed is continuous across it but need not be smooth -- the bending stiffness is hinge_beta in a band of
    # hinge_cells either side of the line
    beta = None
    if still.any():
        shore_ = ndimage.binary_dilation(still, iterations=int(p["hinge_cells"])) & ndimage.binary_dilation(~still & ~ice, iterations=int(p["hinge_cells"]))
        if shore_.any(): beta = np.where(shore_, p["hinge_beta"], 1.0)
    ops = {}                                                                                              # the assembled operators, one per level, shared by both fits
    A, _ = solve_in(fixed, zfix, w, b, mu, z1, ub, p["lam"], p["tau"], p, None, cutm, int(p["a_level"]), beta, ops)   # a_level 0: at full resolution (a lake's shoals are lost at 8 m, and their soundings then read as 20 m residuals)
    n_min = 0
    for r, c, zz, s_ in mins:
        if A[r, c] > zz + s_: ws[r, c] += 1 / s_ ** 2; bs[r, c] += zz / s_ ** 2; n_min += 1   # ...by more than its own sigma
    # one sounding far from the fit (a wall, a mislocated cast) does not drag its neighbourhood: its residual counts for at most 3 sigma
    # ...about what the soundings AROUND it say, not about the fit without them: where a lake's casts all read 10-25 m under a fit that had
    # only its shore to go on, each was cut to 3 sigma and the lake stayed 3 m deep (Gore Lake, 2026-11-15).  The consensus is the casts'
    # own mean residual over 256 m, counted on sound_n0 of them, found by stepping towards it 3 sigma at a time.
    r_raw = np.where(ws > 0, bs / np.maximum(ws, 1e-30) - A, 0.0); sg_s = 1.0 / np.sqrt(np.maximum(ws, 1e-30)); m_s = np.zeros(jz.shape)
    if (ws > 0).any():
        fq = int(min(max(256.0 / (8.0 * res), 1), 32)); sq = 256.0 / (res * fq); hq, wq = jz.shape[0] // fq, jz.shape[1] // fq
        bq = lambda x: x.reshape(hq, fq, wq, fq).sum((1, 3)); uq = lambda x: ndimage.zoom(x, fq, order=1, mode="nearest", grid_mode=True)
        gw = ndimage.gaussian_filter(bq(ws), sq); tq = np.clip(ndimage.gaussian_filter(bq((ws > 0).astype(float)), sq) * (2 * np.pi * sq * sq) / p["sound_n0"], 0.0, 1.0)
        for _ in range(16):
            r_c = np.where(ws > 0, m_s + np.clip(r_raw - m_s, -3.0 * sg_s, 3.0 * sg_s), 0.0)
            m_n = uq(tq * ndimage.gaussian_filter(bq(ws * r_c), sq) / np.maximum(gw, 1e-12)); dm_ = float(np.abs(m_n - m_s)[ws > 0].max()); m_s = m_n
            if dm_ < 0.1: break
    r_s = np.where(ws > 0, m_s + np.clip(r_raw - m_s, -3.0 * sg_s, 3.0 * sg_s), 0.0)
    NUM = ws * r_s; anch = fix_meas & (cls == 3); CNT = (ws > 0).astype(float) + anch; DEN = ws + anch * 0.2; csum = np.zeros(jz.shape); rem = np.ones(jz.shape)
    if (ws > 0).any():
        # ...however wide that spacing is (to 512 m: a lake's dozen casts 200 m apart drew a round basin under each, Gore Lake, Hig 2026-11-15);
        # the wide kernels on blocks of cells (a 512 m Gaussian at 2 m is 2,000 taps a cell)
        for s_m in (8.0, 16.0, 32.0, 64.0, 128.0, 256.0, 512.0):
            fb = int(min(max(s_m / (8.0 * res), 1), 32)); s = s_m / (res * fb)
            if fb > 1:
                hb_, wb_ = jz.shape[0] // fb, jz.shape[1] // fb; bk_ = lambda x: x.reshape(hb_, fb, wb_, fb).sum((1, 3)); up_ = lambda x: ndimage.zoom(x, fb, order=1, mode="nearest", grid_mode=True)
                Ns = up_(ndimage.gaussian_filter(bk_(CNT), s)) * (2 * np.pi * s * s); cs = up_(ndimage.gaussian_filter(bk_(NUM), s) / np.maximum(ndimage.gaussian_filter(bk_(DEN), s), 1e-12))
            else:
                Ns = ndimage.gaussian_filter(CNT, s) * (2 * np.pi * s * s); cs = ndimage.gaussian_filter(NUM, s) / np.maximum(ndimage.gaussian_filter(DEN, s), 1e-12)
            sel = np.clip(Ns / p["sound_n0"], 0.0, 1.0) * rem; csum += sel * cs; rem = rem * (1.0 - np.clip(Ns / p["sound_n0"], 0.0, 1.0))
    tot = 1.0 - rem if (ws > 0).any() else np.zeros(jz.shape)
    mus = np.where(~fixed, p["lam"] * (1 - p["tau"]) / (p["sound_len_m"] / res) ** 4, 0.0) * tot * ((ws > 0).any())
    ts = A + csum
    if np.isfinite(ub).any(): ts = np.where(np.isfinite(ub), np.minimum(ts, ub), ts)
    mut = mu + mus; z1t = np.where(mut > 0, (mu * z1 + mus * ts) / np.maximum(mut, 1e-30), 0.0)
    log(f"  soundings: {n_dense} cells in dense runs fitted as data; {int((ws > 0).sum())} sparse cells ({n_min} of {len(mins)} depth bounds joined them); their smooth adjustment of the fit without them: p5/p50/p95 {np.percentile(csum[(tot > 0.5) & ~fixed], [5, 50, 95]).round(2) if ((tot > 0.5) & ~fixed).any() else 'none'} m over {((tot > 0.5) & ~fixed)[inwin].mean():.1%} of the window")
    z, act = solve_in(fixed, zfix, w, b, mut, z1t, ub, p["lam"], p["tau"], p, log, cutm, 0, beta, ops)
    mu, z1 = mut, z1t; slh = _box(~fixed); qh = (lambda x: None if x is None else x[slh]); ex_, ey_ = links(qh(cutm))
    if p["huber_k"] > 0 and lidar_soft.any():                                                                        # the consensus outvotes a stray soft cell
        r_ = np.abs(z - jz) * np.sqrt(np.where(lidar_soft, 1 / sg ** 2, 1.0)); dn = lidar_soft & (r_ > p["huber_k"])
        fac = np.where(dn, p["huber_k"] / np.maximum(r_, 1e-9), 1.0)
        w2 = w.copy(); b2 = b.copy(); w2[dn] -= (1 - fac[dn]) / sg[dn] ** 2; b2[dn] -= (1 - fac[dn]) * jz[dn] / sg[dn] ** 2
        free = (~fixed).astype(float); hasb = np.isfinite(ub); ubz = np.where(hasb, ub, 0.0)
        for _ in range(2):
            act = hasb & (z > ub - 0.02); wb = np.where(act, p["bound_w"], 0.0)
            zs_, rs = pcg(z[slh], free[slh], (w2 + mu + wb)[slh], (b2 + mu * z1 + wb * ubz)[slh], p["lam"] * p["tau"], p["lam"] * (1 - p["tau"]), p["iters"], ex_, ey_, qh(beta), ops.get(0)); z[slh] = zs_
        log(f"  robust pass: {int(dn.sum())} soft measured cells down-weighted (|r| > {p['huber_k']:g} sigma), {int((dn & (fac < 0.25)).sum())} to under a quarter")
    # the bounds are held by a penalty; what is left above one after the fit (shore cells, a few cm to a metre or two) is put on it.  A guard,
    # reported so that it is seen if it ever does real work.
    over = np.isfinite(ub) & ~fixed & (z > ub)
    if over.any(): log(f"  guard: {int(over.sum())} cells still above a bound after the fit (p50 {np.median((z - ub)[over]):.2f} m, max {(z - ub)[over].max():.2f} m) put on it; the worst at x {(T * (np.unravel_index(np.argmax(np.where(over, z - ub, -1)), z.shape)[1] + .5, 0))[0]:.0f} y {(T * (0, np.unravel_index(np.argmax(np.where(over, z - ub, -1)), z.shape)[0] + .5))[1]:.0f}; {int((over & (z - ub > 1.0)).sum())} by more than 1 m"); z = np.where(over, ub, z)
    sec = time.time() - t1
    # --- report ----------------------------------------------------------------------------------------------------------------
    def scarps(zz, sel):
        n = 0.0
        for ax in (0, 1):
            q = zz if ax else zz.T; s_ = sel if ax else sel.T; g = np.abs(np.diff(q, axis=1)); d0 = g[:, 1:-1]; aa, bb = q[:, 1:-2], q[:, 2:-1]
            with np.errstate(invalid="ignore"):
                n += float(((d0 > 0.5) & (g[:, :-2] < d0 / 4) & (g[:, 2:] < d0 / 4) & (aa > -6) & (aa < 6) & (bb > -6) & (bb < 6) & (s_[:, 1:-2] | s_[:, 2:-1])).sum())
        return n * res / 1000.0
    inner = np.zeros(jz.shape, bool); e = int(100 / res); inner[e:H0 - e, e:W0 - e] = True; modl = (si == 3) & inner; mine = ~fixed & inner
    log(f"  solve: {sec:.1f} s for {int((~fixed & inwin).sum())} free cells ({(~fixed)[inwin].mean():.0%} of the window); bound active on {act[inwin].mean():.1%}")
    log(f"  one-cell scarps in the tide zone (interior, > 100 m from the window edge): present {scarps(cur, modl):.2f} km with a modelled side; this solve {scarps(z, mine):.2f} km with a free side")
    def curv(q): return np.abs(lap(q))
    ks_ = ndimage.binary_dilation(ws > 0, iterations=4) & inner & ~fixed
    if ks_.any(): log(f"  curvature within 8 m of a sounding (|Laplacian|, m per cell^2) p90/p99: present {np.nanpercentile(curv(np.where(np.isfinite(cur), cur, 0.0))[ks_], [90, 99]).round(3)}; solve {np.percentile(curv(z)[ks_], [90, 99]).round(3)}")
    dz = (z - cur)[inner & np.isfinite(cur)]; log(f"  solve - present (interior): p1/p10/p50/p90/p99 {np.percentile(dz, [1, 10, 50, 90, 99]).round(2)} m; unchanged (|dz| < 5 cm) on {(np.abs(dz) < 0.05).mean():.0%}")
    if (soft & inner).any(): log(f"  soft measured cells ({int((soft & inner).sum())}): |solve - measured| p50/p90/p99 {np.percentile(np.abs(z - jz)[soft & inner], [50, 90, 99]).round(2)} m; fixed measured cells are returned as measured")
    for nm_s, ww_, bb_ in (("sparse", ws, bs), ("dense-run", np.where(dense, w, 0.0), np.where(dense, b, 0.0))):
        if not (ww_ > 0).any(): continue
        ws_r, bs_r = ws, bs; ws, bs = ww_, bb_
        k_ = (ws > 0) & inner & ~fixed; rr_ = (z - bs / np.maximum(ws, 1e-30))[k_]; sn_ = rr_ * np.sqrt(ws[k_])
        if not k_.any(): ws, bs = ws_r, bs_r; continue
        log(f"  {nm_s} soundings used ({int(k_.sum())} cells, interior): solve - sounding median {np.median(rr_):+.2f} m, NMAD {1.4826 * np.median(np.abs(rr_ - np.median(rr_))):.2f} m, RMS / sigma {np.sqrt(np.mean(sn_ ** 2)):.2f}; present product: median {np.nanmedian((cur - bs / np.maximum(ws, 1e-30))[k_]):+.2f}, NMAD {1.4826 * np.nanmedian(np.abs((cur - bs / np.maximum(ws, 1e-30))[k_] - np.nanmedian((cur - bs / np.maximum(ws, 1e-30))[k_]))):.2f} m")
        ws, bs = ws_r, bs_r
    if mins:
        mv_ = np.array([z[r, c] - zz for r, c, zz, s_ in mins]); log(f"  depth bounds ({len(mins)}): the solve stands above {int((mv_ > 1.0).sum())} of them by > 1 m (worst {mv_.max():.1f} m)")
    if held:
        hr = np.array([(z[r, c] - zz, cur[r, c] - zz) for r, c, zz, s_ in held]); nm = lambda v: 1.4826 * np.median(np.abs(v - np.median(v)))
        log(f"  held-out soundings ({len(held)}): solve median {np.median(hr[:, 0]):+.2f} NMAD {nm(hr[:, 0]):.2f} m; present product (which was fitted to them) median {np.nanmedian(hr[:, 1]):+.2f} NMAD {nm(hr[:, 1][np.isfinite(hr[:, 1])]):.2f} m")
    kindr = np.zeros(jz.shape, np.uint8); kindr[~fixed] = 5; kindr[~fixed & has0] = 4; kindr[~fixed & (w > 0)] = 3; kindr[soft] = 2; kindr[fix_other] = 1; kindr[fix_meas] = 0
    if ret and a.nowrite:
        return dict(z=z[:H0, :W0].astype(np.float32), kind=kindr[:H0, :W0], typing=np.where(ice, 3, np.where(bergm, 4, np.where(notbed, 2, np.where(still, 1, 0)))).astype(np.uint8)[:H0, :W0], log=rep, free=int((~fixed & inwin).sum()), seconds=round(time.time() - t0, 1))
    prof = dict(driver="GTiff", height=H0, width=W0, count=1, crs="EPSG:6334", transform=T, compress="deflate")
    for nm_, arr, dt, nd in ((f"{a.name}_z.tif", z, "float32", -9999.0), (f"{a.name}_kind.tif", kindr, "uint8", 255), (f"{a.name}_active.tif", act.astype(np.uint8), "uint8", 255),
                             (f"{a.name}_data.tif", np.where(fixed, zfix, np.where(w > 0, b / np.maximum(w, 1e-30), -9999.0)), "float32", -9999.0), (f"{a.name}_w.tif", np.where(fixed, 1e6, w), "float32", -9999.0),
                             (f"{a.name}_typing.tif", np.where(ice, 3, np.where(bergm, 4, np.where(notbed, 2, np.where(still, 1, 0)))).astype(np.uint8), "uint8", 255),
                             (f"{a.name}_A.tif", A, "float32", -9999.0), (f"{a.name}_c.tif", csum, "float32", -9999.0), (f"{a.name}_tot.tif", tot, "float32", -9999.0),
                             (f"{a.name}_mu.tif", mu, "float32", -9999.0), (f"{a.name}_ptype.tif", ptype, "uint8", 255),
                             (f"{a.name}_prior.tif", np.where(has0, z1, -9999.0), "float32", -9999.0), (f"{a.name}_bound.tif", np.where(np.isfinite(ub), ub, -9999.0), "float32", -9999.0)):
        with rasterio.open(out / nm_, "w", dtype=dt, nodata=nd, **prof) as ds: ds.write(arr[:H0, :W0].astype(dt), 1)
    log(f"  total {time.time() - t0:.0f} s")
    (out / f"{a.name}_report.txt").write_text("\n".join(rep) + "\n")
    if a.nofig: return
    # --- figure -----------------------------------------------------------------------------------------------------------------
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    from matplotlib.colors import LightSource, ListedColormap
    c_, z_, k_, a_ = cur[:H0, :W0], z[:H0, :W0], kindr[:H0, :W0], act[:H0, :W0]; ext = [win[0], win[2], win[1], win[3]]; ls = LightSource(315, 35)
    tz = np.isfinite(c_) & (c_ > -8) & (c_ < 8); lo, hi = (np.nanpercentile(np.r_[c_[tz], z_[tz]], [2, 98]) if tz.sum() > 100 else np.nanpercentile(c_, [2, 98]))
    fig, ax = plt.subplots(2, 3, figsize=(24, 16))
    for q, s_, t_ in ((ax[0, 0], c_, f"{a.name}: present {a.tag}"), (ax[0, 1], z_, f"{a.name}: single solve (prototype)")):
        q.imshow(ls.shade(np.where(np.isfinite(s_), s_, lo), cmap=plt.cm.turbo, vert_exag=4, dx=res, dy=res, vmin=lo, vmax=hi, blend_mode="soft"), extent=ext); q.set_title(t_)
    im = ax[0, 2].imshow(z_ - c_, cmap="RdBu_r", vmin=-3, vmax=3, extent=ext); plt.colorbar(im, ax=ax[0, 2], shrink=.6); ax[0, 2].set_title("solve - present (m)")
    cm = ListedColormap(["#2a6", "#777", "#fc4", "#e33", "#9cf", "#fff"]); im = ax[1, 0].imshow(k_, cmap=cm, vmin=-.5, vmax=5.5, extent=ext, interpolation="nearest"); cb = plt.colorbar(im, ax=ax[1, 0], shrink=.6, ticks=range(6))
    cb.ax.set_yticklabels(["fixed: measured", "fixed: lake/ice/prior land", "soft: measured edge / thin", "soundings, lines, low prior ground", "free, prior-guided", "free"]); ax[1, 0].contour(a_[::-1], levels=[.5], colors="k", linewidths=.5, extent=ext); ax[1, 0].set_title("what each cell is (black outline: bound active)")
    for q, s_, t_ in ((ax[1, 1], c_, "present, hillshade"), (ax[1, 2], z_, "single solve, hillshade")):
        q.imshow(ls.hillshade(np.where(np.isfinite(s_), s_, lo), vert_exag=4, dx=res, dy=res), cmap="gray", extent=ext); q.set_title(t_)
    plt.tight_layout(); plt.savefig(out / f"{a.name}.png", dpi=60); log(f"  -> {out / (a.name + '.png')}")


if __name__ == "__main__":
    main()
