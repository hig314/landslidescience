"""The three axes of an observation — time, surface type, data quality —
and the one place they are combined: `state_rules`.

Per layer (see TOPOBATHY_PLAN.md, Revision 2):
  date          one scalar (ISO date or year)
  class         8-bit categorical raster: what surface was observed
  sigma         float32 raster, metres: expected vertical error

Combined for a product (target surface, as_of date):
  gate  ->  sigma_total = sqrt(sigma_q^2 + (rate[class] * years)^2)  ->  winner
"""
from __future__ import annotations
import datetime as _dt
import numpy as np
from scipy import ndimage

CLASSES = {"void": 0, "ground": 1, "water": 2, "bed": 3, "ice": 4,
           "subglacial": 5, "canopy": 6, "built": 7, "modelled": 8, "snow": 9}
CLASS_NAMES = {v: k for k, v in CLASSES.items()}
CLASS_RGB = {0: (40, 40, 40), 1: (160, 120, 80), 2: (70, 130, 220), 3: (30, 60, 140),
             4: (220, 240, 255), 5: (120, 90, 160), 6: (60, 150, 60), 7: (200, 60, 60), 8: (200, 160, 220),
             9: (245, 245, 250)}

# which observed classes can stand for a target surface, by the class *now* at the cell
# (the class of the newest credible observation).  1 = compatible, 0.5 = fallback
# (used only where nothing compatible exists), 0 = never.
TARGETS = {
    "ground_current": {            # bare earth as of now: ground, bed under water, bed under ice
        # class now -> {observed class: gate}.  1 = the surface we want; 0.5 = stands in until
        # something better arrives (a water surface until the bed is known, a snow surface until a
        # snow-free ground); 0.25 = last-rank stand-in (an ice surface where no bed exists).
        "water": {"bed": 1, "subglacial": 1, "modelled": 0.5, "water": 0.5},
        "bed":   {"bed": 1, "subglacial": 1, "modelled": 0.5, "water": 0.5},
        "ground": {"ground": 1, "snow": 0.5, "bed": 0.5},
        "snow":  {"ground": 1, "snow": 0.5},
        "ice":   {"subglacial": 1, "modelled": 0.5, "ice": 0.25},
        "subglacial": {"subglacial": 1, "modelled": 0.5, "ice": 0.25},
        "canopy": {"ground": 1, "snow": 0.5}, "built": {"ground": 1},
    },
    "top_surface": {c: {"ground": 1, "water": 1, "ice": 1, "canopy": 1, "built": 1, "snow": 1, "bed": 0}
                    for c in CLASSES},
}
DEFAULT_RATES = {"ground": 0.01, "water": 0.0, "bed": 0.02, "ice": 3.0,
                 "subglacial": 0.0, "canopy": 0.3, "built": 0.1, "void": 0.0, "modelled": 0.0, "snow": 0.0}
SNOW_SIGMA_M = 1.5             # a snow surface is ground plus an unknown 0..few m: treated as this much error


def years(d) -> float:
    """'2020-10-05' / 2020 / '2020' -> decimal year."""
    if d is None:
        return float("nan")
    if isinstance(d, (int, float)):
        return float(d) + 0.5 if float(d).is_integer() else float(d)
    s = str(d)
    if len(s) == 4:
        return float(s) + 0.5
    t = _dt.date.fromisoformat(s[:10])
    return t.year + (t.timetuple().tm_yday - 0.5) / 365.25


# ------------------------------------------------------------- class

def water_level_auto(z, valid, bin_m=0.1, min_frac=0.02):
    """A water body is the one elevation a lot of cells share: the modal
    bin of z.  Returns None unless that bin holds at least min_frac of the
    valid cells (no lake in view -> no level, no false water)."""
    if z is None:
        return None
    zz = z[valid & np.isfinite(z)]
    if zz.size < 100:
        return None
    lo, hi = np.percentile(zz, [0.5, 99.5])
    h, e = np.histogram(zz, np.arange(lo, hi + bin_m, bin_m))
    i = int(np.argmax(h))
    if h[i] < min_frac * zz.size:
        return None
    return float(0.5 * (e[i] + e[i + 1]))


def roughness(z, valid, res_m, win_m=15.0):
    """Local residual from a plane: std of z minus its box mean, in a window."""
    k = max(int(win_m / res_m), 3) | 1
    zz = np.where(valid, z, np.nan); zf = np.nan_to_num(zz, nan=0.0); w = valid.astype(np.float32)
    m = ndimage.uniform_filter(zf, k) / np.maximum(ndimage.uniform_filter(w, k), 1e-6)
    v = ndimage.uniform_filter((zf - m) ** 2 * w, k) / np.maximum(ndimage.uniform_filter(w, k), 1e-6)
    return np.sqrt(np.maximum(v, 0)).astype(np.float32)


def snow_detect(z, valid, ref, ref_valid, res_m, dz_m=0.5, smooth_m=50.0, rough_ratio=0.7, min_area_m2=2000.0):
    """Snow guess: higher than a reference surface by more than dz over a smooth_m window AND
    smoother than it.  Region scale on purpose: we are not mapping every patch, only what is
    plainly a snow surface.  Returns a bool raster."""
    both = valid & ref_valid & np.isfinite(z) & np.isfinite(ref)
    if both.sum() < 100:
        return np.zeros(z.shape, bool)
    k = max(int(smooth_m / res_m), 3) | 1
    w = both.astype(np.float32)
    dz = ndimage.uniform_filter(np.where(both, z - ref, 0.0).astype(np.float32), k) / np.maximum(ndimage.uniform_filter(w, k), 1e-3)
    r_this = roughness(z, valid, res_m); r_ref = roughness(ref, ref_valid, res_m)
    snow = both & (dz > dz_m) & (r_this < rough_ratio * r_ref + 0.02)
    snow = ndimage.binary_opening(snow, iterations=2)
    lab, n = ndimage.label(snow)
    if n:
        px = int(min_area_m2 / (res_m * res_m))
        sizes = ndimage.sum(np.ones_like(lab), lab, np.arange(1, n + 1))
        snow[np.isin(lab, np.flatnonzero(sizes < px) + 1)] = False
    return snow


def class_raster(spec, z, valid, below, res_m, info, others=None, grid=None, win=None):
    """8-bit class raster from spec["class"]: a name, or a list of steps
    [{"default": "ground"}, {"water_level": {"tol_m": .5}}, {"below_level": {}}]."""
    cs = spec.get("class", "ground")
    if isinstance(cs, str):
        cs = [{"default": cs}]
    c = np.zeros(z.shape, np.uint8)
    level = None
    for step in cs:
        (name, kw), = step.items()
        if name == "default":
            c[valid] = CLASSES[kw]
        elif name in ("water_level", "below_level"):
            level = kw.get("level", spec.get("water_level_m", "auto"))
            if level == "auto":
                level = water_level_auto(z if name == "water_level" else below, valid,
                                         min_frac=kw.get("min_frac", 0.02))
            if level is None:
                continue
            info["water_level_m"] = float(level)
            if name == "water_level":       # topo lidar: flat at the level -> water surface
                flat = valid & (np.abs(z - level) <= kw.get("tol_m", 0.5))
                c[flat] = CLASSES[kw.get("as", "water")]
            else:                           # sonar: beneath the level -> bed, else not a surface
                c[valid & (z < level)] = CLASSES[kw.get("as", "bed")]
                c[valid & (z >= level)] = CLASSES[kw.get("else", "void")]
        elif name == "snow_vs":
            # reference: "below" (the composite so far) or a named snow-free layer
            refn = kw.get("ref", "below")
            if refn == "below":
                ref, rv = below, (np.isfinite(below) if below is not None else None)
            else:
                ref, rv = (others or {}).get(refn, (None, None))
            if ref is None or rv is None:
                continue
            sn = snow_detect(z, valid, ref, rv, res_m, kw.get("dz_m", 0.5), kw.get("smooth_m", 50.0),
                             kw.get("rough_ratio", 0.7), kw.get("min_area_m2", 2000.0))
            c[sn & (c == CLASSES["ground"])] = CLASSES["snow"]
            info["snow_fraction"] = float(sn[valid].mean()) if valid.any() else 0.0
        elif name == "polygons":
            # class from a vector file (glacier outlines, drawn lakes): {"polygons": {"path": ..., "as": "ice"}}
            import json as _json, subprocess as _sp
            from rasterio import features as _feat
            if grid is None or win is None:
                continue
            gj = _json.loads(_sp.run(["/opt/homebrew/bin/ogr2ogr", "-f", "GeoJSON", "-t_srs", str(grid.crs), "/vsistdout/", kw["path"]],
                                     capture_output=True, text=True).stdout)
            geoms = []
            for f in gj["features"]:
                g = f["geometry"]
                if g["type"] == "LineString":
                    g = {"type": "Polygon", "coordinates": [g["coordinates"] + [g["coordinates"][0]]]}
                geoms.append(g)
            sub = grid.sub(win)
            m = _feat.rasterize(geoms, out_shape=(sub.height, sub.width), transform=sub.transform, fill=0, default_value=1, dtype="uint8").astype(bool)
            only = kw.get("only")                  # e.g. only where currently ground
            sel = m & valid & ((c == CLASSES[only]) if only else True)
            c[sel] = CLASSES[kw.get("as", "ice")]
        else:
            raise ValueError(f"unknown class step {name}")
    c[~valid] = 0
    return c


# ------------------------------------------------------------- sigma

def facets(z, valid, res_m, flat_tol=1e-3, min_area_m2=400.0, k=0.1):
    """Quality proxy for a delivered TIN or a sparsely sampled DTM: inside a
    planar facet the Laplacian is ~0; the size of the facet is how far the
    cell is from real data.  Returns an extra sigma (m) ~ k * sqrt(area)
    for facets larger than min_area, 0 elsewhere."""
    zz = np.where(valid, z, np.nan)
    zz = np.nan_to_num(zz, nan=0.0)
    lap = np.abs(ndimage.laplace(zz.astype(np.float64)))
    flat = valid & (lap < flat_tol)
    lab, n = ndimage.label(flat)
    if n == 0:
        return np.zeros(z.shape, np.float32)
    area = ndimage.sum(np.ones_like(lab), lab, np.arange(1, n + 1)) * res_m * res_m
    extra = np.zeros(n + 1, np.float32)
    big = area >= min_area_m2
    extra[1:][big] = k * np.sqrt(area[big])
    out = extra[lab]
    # facet edges themselves are not flat but are just as far from data: dilate a little
    out = ndimage.grey_dilation(out, size=3)
    out[~valid] = 0
    return out.astype(np.float32)


def sigma_raster(spec, z, valid, res_m, info):
    """float32 sigma (m) from spec["sigma_m"] plus modifiers in spec["sigma"]:
    [{"facets": {...}}, ...]."""
    s0 = float(spec.get("sigma_m", 1.0))
    s = np.full(z.shape, s0, np.float32)
    for step in spec.get("sigma", []):
        (name, kw), = step.items()
        if name == "facets":
            s = np.sqrt(s * s + facets(z, valid, res_m, **kw) ** 2).astype(np.float32)
        elif name == "raster":     # an observation package's own sigma raster (e.g. from grid_bathy.py)
            from .stack import read_geo
            sr, sv = read_geo(kw["path"], kw["_grid"], kw["_win"], "bilinear")
            s = np.where(sv & np.isfinite(sr), sr, s).astype(np.float32)
        elif name == "edge":       # near the footprint edge the DTM is interpolated from fewer points
            d = ndimage.distance_transform_edt(valid) * res_m
            w = float(kw.get("width_m", 10)); add = float(kw.get("sigma_m", 1.0))
            s = np.sqrt(s * s + (add * np.clip(1 - d / w, 0, 1)) ** 2).astype(np.float32)
        else:
            raise ValueError(f"unknown sigma step {name}")
    cls = info.get("_cls")
    if cls is not None:
        sn = cls == CLASSES["snow"]
        if sn.any():
            add = float(spec.get("snow_sigma_m", SNOW_SIGMA_M))
            s[sn] = np.sqrt(s[sn] ** 2 + add * add)
    s[~valid] = np.inf
    info["sigma_median_m"] = float(np.median(s[valid])) if valid.any() else None
    return s


# -------------------------------------------------------- state rules

def state_rules_sequential(obs, target="ground_current", as_of=None, rates=None, sigma_max=5.0,
                           lowest=None, margin_m=0.0):
    """Hig's sequential model (2026-09-21): walk the stack bottom-up keeping a running
    composite (z, class shown, sigma, dates).  Layer i's mask is where observation i beats
    the composite SO FAR - not where it beats every layer.  Later layers overwrite.

    Per cell, observation i replaces the running composite when
      1. it is dated/credible and its class is compatible with the class-now (the newest
         credible class between the running composite and i), and
      2. the running composite's shown class is no longer compatible with class-now
         (a newer observation says water/bed where the composite shows old ground), or
      3. its expected error is smaller than the running one by more than `margin_m`, or
      4. `lowest` is on for this layer and the two are within its tolerance and i is lower
         by more than the tolerance (canopy / snow penetration; opt-in, region-scale).
    Returns the same dict as state_rules.
    """
    rates = {**DEFAULT_RATES, **(rates or {})}
    table = TARGETS[target]
    n = len(obs); shape = obs[0]["z"].shape
    as_of = years(as_of) if as_of is not None else max(o["date"] for o in obs if np.isfinite(o["date"]))
    lowest = lowest or {}
    def compat(class_now, cls):
        g = np.zeros(shape, np.float32)
        for now_name, allowed in table.items():
            sel = class_now == CLASSES[now_name]
            if not sel.any(): continue
            for obs_name, w in allowed.items():
                m = sel & (cls == CLASSES[obs_name])
                if m.any(): g[m] = w
        return g
    def sig_total(o):
        rate = np.zeros(shape, np.float32)
        for name, code in CLASSES.items(): rate[o["cls"] == code] = rates.get(name, 0.0)
        return np.sqrt(o["sigma"] ** 2 + (rate * max(as_of - o["date"], 0.0)) ** 2).astype(np.float32)
    # running composite state
    run_z = np.full(shape, np.nan, np.float32); run_sig = np.full(shape, np.inf, np.float32)
    run_cls = np.zeros(shape, np.uint8); run_gate = np.zeros(shape, np.float32)
    class_now = np.zeros(shape, np.uint8); date_now = np.full(shape, -np.inf, np.float32)
    winner = np.full(shape, -1, np.int16); masks = []; sts = []; gates = []
    fallback = np.zeros(shape, bool)
    for i, o in enumerate(obs):
        dated = o["valid"] & (o["cls"] > 0) & (o["date"] <= as_of + 1e-6)
        cred = dated & (o["sigma"] < o.get("class_sigma_max", sigma_max))
        st = sig_total(o); sts.append(st)
        # class-now: the newer of (running newest, this observation) among credible ones
        newer = cred & (o["date"] >= date_now)
        class_now = np.where(newer, o["cls"], class_now).astype(np.uint8)
        date_now = np.where(newer, o["date"], date_now).astype(np.float32)
        gate_i = compat(class_now, o["cls"]); gate_i[~dated] = 0
        gate_run = compat(class_now, run_cls); gate_run[~np.isfinite(run_z)] = 0
        gates.append(gate_i)
        take = dated & (gate_i > 0) & (
            (gate_i > gate_run) |                                       # 1/2: better compatibility (or old surface obsolete)
            ((gate_i == gate_run) & (st < run_sig - margin_m)))         # 3: better expected error
        if o.get("name") in lowest:                                     # 4: opt-in lowest, region scale
            tol = float(lowest[o["name"]].get("tol_m", 0.3)); win_m = float(lowest[o["name"]].get("smooth_m", 50))
            close = dated & (gate_i == gate_run) & (gate_i > 0) & (np.abs(st - run_sig) <= tol)
            dz = np.where(np.isfinite(run_z) & o["valid"], o["z"] - run_z, 0.0).astype(np.float32)
            k = max(int(win_m), 3)
            dz_s = ndimage.uniform_filter(dz, k) / np.maximum(ndimage.uniform_filter((np.isfinite(run_z) & o["valid"]).astype(np.float32), k), 1e-3)
            take |= close & (dz_s < -tol)
        # last resort: nothing compatible shown yet and this observation is the only thing here
        last = dated & ~np.isfinite(run_z) & (gate_i == 0)
        take |= last; fallback |= last
        masks.append(take.astype(np.float32))
        run_z = np.where(take, o["z"], run_z); run_sig = np.where(take, st, run_sig)
        run_cls = np.where(take, o["cls"], run_cls).astype(np.uint8); run_gate = np.where(take, gate_i, run_gate)
        winner = np.where(take, i, winner).astype(np.int16)
    return dict(masks=masks, class_now=class_now, date_now=date_now, sigma_total=np.stack(sts),
                gate=np.stack(gates), winner=winner, as_of=as_of, fallback=fallback)


def state_rules(obs, target="ground_current", as_of=None, rates=None, sigma_max=5.0,
                lowest_tol_m=0.2):
    """obs: list of dicts in fold order with z, valid, cls (uint8), sigma
    (float32), date (decimal year).  Returns dict with per-layer winner
    masks (list of float32 0/1), class_now, date_now, sigma_total (stack),
    gate (stack), winner (int16, -1 = nothing usable)."""
    rates = {**DEFAULT_RATES, **(rates or {})}
    table = TARGETS[target]
    n = len(obs); shape = obs[0]["z"].shape
    as_of = years(as_of) if as_of is not None else max(o["date"] for o in obs if np.isfinite(o["date"]))
    # Two credibilities.  For CLASS (what is here now) an observation counts whenever it is
    # valid, classified and dated, unless its own class_sigma_max voids it (a TIN hull far from
    # soundings is not evidence of bed).  For ELEVATION an observation competes when sigma is
    # below sigma_max; where nothing competes, the best compatible observation wins anyway (flagged).
    dated = np.stack([o["valid"] & (o["cls"] > 0) & (o["date"] <= as_of + 1e-6) for o in obs])
    cred = dated & np.stack([o["sigma"] < o.get("class_sigma_max", sigma_max) for o in obs])
    elig = dated & np.stack([o["sigma"] < sigma_max for o in obs])
    dates = np.array([o["date"] for o in obs], np.float32)
    dstack = np.where(cred, dates[:, None, None], -np.inf)
    inew = np.argmax(dstack, axis=0)
    any_cred = cred.any(axis=0)
    cls_stack = np.stack([o["cls"] for o in obs])
    class_now = np.take_along_axis(cls_stack, inew[None], 0)[0]
    class_now[~any_cred] = 0
    date_now = np.take_along_axis(dstack, inew[None], 0)[0].astype(np.float32)
    gate = np.zeros((n,) + shape, np.float32)
    for now_name, allowed in table.items():
        sel_now = class_now == CLASSES[now_name]
        if not sel_now.any():
            continue
        for i, o in enumerate(obs):
            for obs_name, w in allowed.items():
                m = sel_now & (o["cls"] == CLASSES[obs_name])
                if m.any():
                    gate[i][m] = w
    gate[~dated] = 0
    gate_full = gate.copy()                      # compatible regardless of sigma (the fallback pool)
    gate[~elig] = 0
    # expected error
    st = np.zeros((n,) + shape, np.float32)
    for i, o in enumerate(obs):
        rate = np.zeros(shape, np.float32)
        for name, code in CLASSES.items():
            rate[o["cls"] == code] = rates.get(name, 0.0)
        sig_t = rate * max(as_of - o["date"], 0.0)
        st[i] = np.sqrt(o["sigma"] ** 2 + sig_t ** 2)
    # winner: full-gate first, then fallback gates; within that, min sigma; ties -> later layer
    winner = np.full(shape, -1, np.int16)
    best = np.full(shape, np.inf, np.float32)
    for level in (1.0, 0.5, 0.25):
        undecided = winner < 0
        for i in range(n):                    # later layers overwrite on exact ties
            ok = undecided & (np.abs(gate[i] - level) < 1e-6)
            better = ok & (st[i] <= best)
            winner[better] = i; best[better] = st[i][better]
    # fallback: nothing eligible here -> the best compatible observation whatever its sigma
    nothing = winner < 0
    if nothing.any():
        for level in (1.0, 0.5, 0.25):
            for i in range(n):
                ok = nothing & (winner < 0) & (np.abs(gate_full[i] - level) < 1e-6)
                better = ok & (st[i] <= best)
                winner[better] = i; best[better] = st[i][better]
    # last resort: no compatible observation at all (e.g. water with no bed known) -> the newest
    # dated observation whatever its class (the water surface), flagged; never a stale base by default
    still = winner < 0
    if still.any():
        dn = np.where(dated, dates[:, None, None], -np.inf)
        inew2 = np.argmax(dn, axis=0); has = dated.any(axis=0)
        sel = still & has
        winner[sel] = inew2[sel].astype(np.int16)
    fallback = nothing & (winner >= 0)
    # lowest tie-break among same-class ground observations within lowest_tol of the best sigma
    zs = np.stack([o["z"] for o in obs])
    for i in range(n):
        cand = (winner >= 0) & (winner != i) & (gate[i] > 0) & (st[i] <= best + lowest_tol_m) \
               & (obs[i]["cls"] == CLASSES["ground"])
        if not cand.any():
            continue
        zw = np.take_along_axis(zs, np.clip(winner, 0, n - 1)[None], 0)[0]
        cw = np.take_along_axis(cls_stack, np.clip(winner, 0, n - 1)[None], 0)[0]
        lower = cand & (cw == CLASSES["ground"]) & (zs[i] < zw - lowest_tol_m)
        winner[lower] = i
    masks = [(winner == i).astype(np.float32) for i in range(n)]
    return dict(masks=masks, class_now=class_now, date_now=date_now, sigma_total=st,
                gate=gate_full, winner=winner, as_of=as_of, fallback=fallback)
