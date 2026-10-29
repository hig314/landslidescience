#!/usr/bin/env python3
"""THE JOINT OBSERVATION STAGE: every measurement as support-bearing evidence on one working grid, before any
raster is composited (TOPOBATHY_PLAN.md "A point-first build", steps 1-6; built 2026-10-22 from the Grewingk
pilot's joint_fuse.py, which it replaces for the chain).

    /opt/anaconda3/bin/python3 topobathy/joint.py fuse  <recipe.json> [--win x0 y0 x1 y1] [--out DIR] [--res 1]
    /opt/anaconda3/bin/python3 topobathy/joint.py layer <recipe.json> [--win ...] [--out DIR]
    /opt/anaconda3/bin/python3 topobathy/joint.py all   <recipe.json> ...

The recipe's "joint" block lists SOURCES, each one of three kinds, all read onto the working grid (res_m):
  lidar   a survey's per-cell return summaries (joint_pilot.py units/mosaic: n_g, z_g, zmin_g, n_w, z_w, zmax --
          a cell with no return has NO value).  cls ground.  "src": the mosaic npz (or a directory of units).
  grid    a gridded product (multibeam, a BAG, NCEI, CGOA, an SfM bare-earth model): one observation per SOURCE
          cell, carried onto every working cell the source cell covers, with a sigma that says what gridding lost
          (Hig): sigma^2 = sigma_m^2 + (g*D/sqrt(12))^2 + (xy_sigma_m*g)^2, g the slope of the source at its own
          resolution D.  Steep, coarse bathymetry is weak evidence; a flat multibeam is strong.  cls bed | ground | ice.
  points  sparse x,y,z[,sigma] (soundings, lead lines): binned to the working cell.  cls bed.
A PRIOR (3DEP, a regional DTM) is NOT a source: it never enters the joint surface.  The composite takes it only
where the joint layer is void (the mountains, ground under forest the lidar did not reach); its slope decides
whether a hole in the surveys can be water (layer step 3).

FUSE, in order:
  1. vintage map   zones where the surface moves and ONE source is allowed (a river's floodplain, a beach, a
                   glacier): "vintage": [{"mask": raster(1 = zone) | {"floodplain": ...} | {"beach": ...}, "allow":
                   [names]}].  Elsewhere the sources POOL.
  2. alignment     each lidar source against the reference (the first lidar) on stable bare ground, outside the
                   zones; the median shift applied.  Grid/points sources carry "shift_m" from the recipe (datums).
  3. epoch         at each cell the NEWEST solid observation (ground or bed) sets the epoch; sources older than
                   pool_years before it are SUPERSEDED there -- and a contemporary that disagrees with the newest by
                   more than tol + 3 sigma is superseded too (the surface changed, or it is wrong): "newest wins
                   unless its contemporaries agree".
  4. cross-check   among the pooled solid sources: a cell standing above the others' local surface by more than
                   tol(+slope, +sigma) is rejected (vegetation read as ground, a TIN hull); one standing far below is
                   rejected only if it is a spike in its own source.
  5. over water    a solid cell standing above a water surface another source observed nearby (its water returns
                   within over_water_m, none of its ground within over_water_dry_m, nothing of its above the water)
                   is rejected: dry ground is not under water.  A BED cell above a water surface observed in the
                   same cell is rejected outright.
  6. pool          surviving values, 1/sigma^2 (lidar: n/sigma^2).  No interpolation: a cell with no survivor is
                   VOID.  Outputs joint_z, joint_sigma, joint_n, joint_src (bit k = source k), joint_rej (bit k =
                   source k rejected or superseded), joint_dist, joint_nland (sources with a surviving solid cell:
                   the composite's land_support), joint_cls (the class of the heaviest survivor: 1 ground, 3 bed,
                   4 ice), water_z (water-surface returns, vintage-aware), vintage, joint_report.json.
LAYER: joint + water_z -> layer_z / layer_class (codes: 1 ground, 2 water, 3 bed, 4 ice, 0 void) for the composite
chain, with the pilot's rules (joint_fuse.layer, Hig 2026-10-20): a void nearer water returns than ground is water
at their level; a small gap in ground is interpolated; a hole with NO return at all inside the surveys' footprint
is water (at the touching water's level, else its rim's low ground) unless the prior says the ground is steep;
everything else void -- the prior's, through the composite.
"""
from __future__ import annotations
import argparse, csv, json, re, sys, time
from pathlib import Path
import numpy as np
from scipy import ndimage

CLS = {"void": 0, "ground": 1, "water": 2, "bed": 3, "ice": 4}
DEFAULTS = dict(res_m=1.0, pool_years={"ground": 5.0, "bed": 30.0, "ice": 0.5}, rates={"ground": 0.01, "bed": 0.02, "ice": 3.0}, tol_m=0.15, tol_low_m=1.0, xc_sigma_m=2.0, xc_minw=1.5,
                over_water_m=50.0, over_water_dry_m=10.0, over_water_tol_m=0.5, bed_over_water_tol_m=0.3, over_water_max_m=30.0, tide_max_m=4.5, stand_flat_m=0.3, stand_frac=0.05, stand_min_cells=10, stand_dz_m=1.0, derived_gap_m=0.0, derived_adjust_m=150.0, derived_adjust_coarse=4, surf_near_m=30.0,
                sigma_floor_m=0.03, gap_m=2.0, wet_m=15.0, steep_deg=20.0, prior_slope_res_m=5.0, hole_rim_tol_m=1.0, grid_wc=0.1, grid_better=0.75,
                lidar_sigma_m=0.08, grid_xy_sigma_m=0.0, align_min_cells=1000)


# ----------------------------------------------------------------------------------------------- recipe
class Source:
    def __init__(self, d, base: Path):
        self.name = d["name"]; self.kind = d.get("kind", "grid"); self.cls = d.get("cls", "bed" if self.kind != "lidar" else "ground")
        self.date = _years(d.get("date")); self.sigma_m = float(d.get("sigma_m", DEFAULTS["lidar_sigma_m"] if self.kind == "lidar" else 1.0))
        self.shift_m = float(d.get("shift_m", 0.0)); self.xy_sigma_m = float(d.get("xy_sigma_m", DEFAULTS["grid_xy_sigma_m"]))
        self.src = _resolve(d["src"], base); self.footprint = [_resolve(p, base) for p in _aslist(d.get("footprint"))]
        self.res_m = d.get("res_m"); self.reference = bool(d.get("reference", False)); self.d = d
        self.valid_below_m = d.get("valid_below_m"); self.valid_above_m = d.get("valid_above_m")   # a merged product's own kind holds only on one side of a level
        self.derived = bool(d.get("derived", False))   # a compilation of other surveys (CUDEM/OCM, NCEI's 1/3", CGOA): not independent evidence

    def valid(self, z):
        """Cells of a grid/points source that count: within its stated range (a topobathy candidate's 'bed' is bed only
        below MLLW; above it the same file holds somebody's land, which it is not the source of)."""
        ok = np.isfinite(z)
        if self.valid_below_m is not None: ok &= z < float(self.valid_below_m)
        if self.valid_above_m is not None: ok &= z > float(self.valid_above_m)
        return ok

    def __repr__(self): return f"Source({self.name} {self.kind} {self.cls} {self.src})"


def _aslist(v): return [] if v is None else (v if isinstance(v, list) else [v])


def _resolve(p, base: Path):
    p = str(p)
    return p if p.startswith("/") else str((base / p).resolve())


def _years(d):
    if d is None: return np.nan
    s = str(d); y = int(s[:4]); m = int(s[5:7]) if len(s) >= 7 else 7; dd = int(s[8:10]) if len(s) >= 10 else 1
    return y + (m - 1) / 12 + (dd - 1) / 365


def load_recipe(path):
    r = json.loads(Path(path).read_text()); base = Path(path).resolve().parent
    j = r.get("joint", {}); cfg = dict(DEFAULTS); cfg.update({k: v for k, v in j.items() if k not in ("sources", "vintage", "prior")})
    if "glaciers" not in cfg:                                                  # the recipe's glacier outline (the stage's)
        cfg["glaciers"] = r.get("glaciers") or (r.get("shore") or {}).get("glaciers")
    srcs = [Source(d, base) for d in j["sources"]]
    if not any(s.reference for s in srcs):
        for s in srcs:
            if s.kind == "lidar": s.reference = True; break
    vint = j.get("vintage", []); prior = _resolve(j["prior"], base) if j.get("prior") else None
    epsg = r.get("grid", {}).get("epsg", 6334); bounds = r.get("grid", {}).get("bounds")
    return r, cfg, srcs, vint, prior, epsg, bounds


# ----------------------------------------------------------------------------------------------- reading
def win_shape(win, res): return int(round((win[3] - win[1]) / res)), int(round((win[2] - win[0]) / res))


def read_to(path, win, res, epsg, resampling="nearest", band=1):
    """A raster onto the working grid (NaN = no data). nearest keeps a grid's cells as cells; nothing is invented."""
    import rasterio
    from rasterio.vrt import WarpedVRT
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    H, W = win_shape(win, res)
    with rasterio.open(path) as s, WarpedVRT(s, crs=f"EPSG:{epsg}", transform=from_origin(win[0], win[3], res, res),
                                             width=W, height=H, resampling=getattr(Resampling, resampling)) as v:
        a = v.read(band, masked=True).astype(np.float32).filled(np.nan)
    return a


def native_res(path):
    import rasterio
    with rasterio.open(path) as s:
        r = abs(s.transform.a)
        if s.crs and s.crs.is_geographic:            # degrees: ~ metres at the raster's middle latitude
            lat = (s.bounds.top + s.bounds.bottom) / 2; r = r * 111320 * np.cos(np.radians(lat))
        return float(r)


def block_reduce(a, f, how):
    H, W = a.shape; Hc, Wc = H // f, W // f; b = a[:Hc * f, :Wc * f].reshape(Hc, f, Wc, f)
    with np.errstate(all="ignore"):
        if how == "sum": return np.nansum(b, axis=(1, 3))
        if how == "mean": return np.nanmean(b, axis=(1, 3))
        if how == "min": return np.nanmin(b, axis=(1, 3))
        if how == "max": return np.nanmax(b, axis=(1, 3))
    raise ValueError(how)


def obs_lidar(s: Source, win, res, cfg):
    """n_g, z_g, zmin_g, n_w, z_w, zmax over the window from the survey's mosaic npz (joint_pilot.py; 1 m, row 0 at
    the top), cropped/padded; block-reduced when res > 1."""
    p = Path(s.src); H, W = win_shape(win, res)
    o = {k: np.full((H, W), np.nan, np.float32) for k in ("n_g", "z_g", "zmin_g", "n_w", "z_w", "zmax")}
    if p.is_dir():                                                   # units directory (joint_pilot layout)
        files = sorted(p.glob("u_*/" + s.d.get("unit_file", f"{s.name}.npz")))
        if not files: return None
        for f in files:
            _, ux, uy = f.parent.name.split("_"); b = (int(ux) * 1000, int(uy) * 1000)
            if b[0] >= win[2] or b[0] + 1000 <= win[0] or b[1] >= win[3] or b[1] + 1000 <= win[1]:
                continue                                             # a unit of another box (the units cache is shared)
            g = np.load(f); buf = s.d.get("unit_buf", 30)
            _paste(o, {k: g[k][buf:buf + 1000, buf:buf + 1000] for k in o}, (b[0], b[1], b[0] + 1000, b[1] + 1000), win, res)
    else:
        g = np.load(p); gb = tuple(g["bounds"]) if "bounds" in g else tuple(s.d["bounds"])
        _paste(o, {k: g[k] for k in o}, gb, win, res)
    if not np.isfinite(o["z_g"]).any() and not np.isfinite(o["z_w"]).any(): return None
    for k in ("z_g", "zmin_g", "z_w", "zmax"): o[k] += s.shift_m
    for k in ("n_g", "n_w"): o[k] = np.nan_to_num(o[k], nan=0.0)
    # ground returns standing at the cell's own water-return level, outnumbered by the water returns, ARE the water
    # surface (2026-10-23, KBay 2023 over the bay: 0-1 "ground" returns/m2 at +0.81 among water returns at +0.80
    # superseded the 2009 multibeam and stood 52 m above the sea bed)
    surf = (o["n_w"] > 0) & np.isfinite(o["z_g"]) & (o["n_w"] >= o["n_g"]) & (np.abs(o["z_g"] - o["z_w"]) <= cfg["over_water_tol_m"])
    # ...and ground standing ABOVE the cell's own water returns, within the tide's reach, is no ground either
    # (2026-10-26, Hig's review of run 20, Halibut Cove's submarine ridge): the bed of a cell where the survey saw water
    # lies below that water, so "ground" over it is the sea at a higher stand from another flight line (KBay 2023:
    # ~57 "ground" returns a cell from -2.1 to 4.7 m with water returns at -2.4 in the same cells; Grewingk 2021 at
    # 3.9 m over its own water at 0.8).  Above the tide's reach (tide_max_m) nothing is judged: a river's cut bank
    # shares a cell with its water there.
    tm_ = float(cfg.get("tide_max_m", np.inf))
    above = ((o["n_w"] > 0) & np.isfinite(o["z_g"]) & np.isfinite(o["z_w"]) & (o["z_g"] > o["z_w"] + cfg["over_water_tol_m"])
             & (o["z_g"] <= tm_) & (o["z_w"] <= tm_))
    # (both apply: run 20e's edit left `surf` computed but unused, and China Poot Lake's surface stood as ground again)
    gone = surf | above
    o["z_g"][gone] = np.nan; o["zmin_g"][gone] = np.nan; o["n_g"][gone] = 0.0
    return o


def _paste(o, g, gb, win, res):
    """Paste a 1 m block with bounds gb into the working arrays (block-reduced to res if needed)."""
    f = int(round(res))
    if f > 1:
        g = dict(n_g=block_reduce(g["n_g"], f, "sum"), z_g=block_reduce(g["z_g"], f, "mean"), zmin_g=block_reduce(g["zmin_g"], f, "min"),
                 n_w=block_reduce(g["n_w"], f, "sum"), z_w=block_reduce(g["z_w"], f, "mean"), zmax=block_reduce(g["zmax"], f, "max"))
    x0, y0, x1, y1 = gb
    c0 = int(round((x0 - win[0]) / res)); r0 = int(round((win[3] - y1) / res)); H, W = o["z_g"].shape; h, w = g["z_g"].shape
    sr0, sc0 = max(0, -r0), max(0, -c0); dr0, dc0 = max(0, r0), max(0, c0)
    hh, ww = min(h - sr0, H - dr0), min(w - sc0, W - dc0)
    if hh <= 0 or ww <= 0: return
    for k in o: o[k][dr0:dr0 + hh, dc0:dc0 + ww] = g[k][sr0:sr0 + hh, sc0:sc0 + ww]


def obs_grid(s: Source, win, res, epsg, cfg):
    """A gridded product as observations: its value on every working cell its cell covers (nearest), sigma from what
    gridding lost at the source's own resolution D: hypot(sigma_m, g*D/sqrt(12), xy_sigma*g)."""
    D = float(s.res_m) if s.res_m else native_res(s.src)
    # at or finer than the working grid: the mean of its cells in ours (nearest picked every other column of a 1 m
    # multibeam and let its sprinkled no-data toggle the pool cell by cell); coarser: bilinear (nearest made a 10 m
    # product a staircase of 2 m cells) -- 2026-10-23, the "gridding artifacts" of run 7
    z = read_to(s.src, win, res, epsg, "average" if D <= res else "bilinear") + s.shift_m
    z = np.where(s.valid(z), z, np.nan).astype(np.float32)
    if not np.isfinite(z).any(): return None
    # slope at the source's resolution (read at D, or at res if the source is finer than the working grid)
    rs = max(D, res); zs = read_to(s.src, win, rs, epsg, "bilinear")
    gy, gx = np.gradient(np.nan_to_num(zs, nan=float(np.nanmedian(zs))), rs); g = np.hypot(gx, gy)
    sig_s = np.sqrt(s.sigma_m ** 2 + (g * D / np.sqrt(12)) ** 2 + (s.xy_sigma_m * g) ** 2).astype(np.float32)
    f = int(round(rs / res))
    sig = np.kron(sig_s, np.ones((f, f), np.float32))[:z.shape[0], :z.shape[1]] if f > 1 else sig_s
    if sig.shape != z.shape:
        pad = np.full(z.shape, np.nan, np.float32); pad[:sig.shape[0], :sig.shape[1]] = sig; sig = pad
    sig = np.where(np.isfinite(z), np.nan_to_num(sig, nan=s.sigma_m), np.nan)
    return dict(z=z, sigma=sig, n=np.isfinite(z).astype(np.float32), D=D)


def obs_points(s: Source, win, res, cfg):
    H, W = win_shape(win, res); z = np.full((H, W), np.nan, np.float32); sg = np.full((H, W), np.nan, np.float32); n = np.zeros((H, W), np.float32)
    rows = list(csv.DictReader(open(s.src)))
    if not rows: return None
    x = np.array([float(r["x"]) for r in rows]); y = np.array([float(r["y"]) for r in rows]); zz = np.array([float(r["z"]) for r in rows]) + s.shift_m
    sig = np.array([float(r.get("sigma") or s.sigma_m) for r in rows])
    keep = np.array([(r.get("bound") or "") == "" for r in rows]) if "bound" in rows[0] else np.ones(len(rows), bool)
    c = ((x - win[0]) // res).astype(int); r = ((win[3] - y) // res).astype(int)
    ok = keep & (c >= 0) & (c < W) & (r >= 0) & (r < H)
    if not ok.any(): return None
    cid = r[ok] * W + c[ok]; o = np.lexsort((zz[ok], cid)); cid, zs, ss = cid[o], zz[ok][o], sig[ok][o]
    u, st, ct = np.unique(cid, return_index=True, return_counts=True)
    zf, nf, sf = z.ravel(), n.ravel(), sg.ravel()
    zf[u] = zs[st + ct // 2]; nf[u] = ct; sf[u] = np.minimum.reduceat(ss, st)
    z, n, sg = zf.reshape(H, W), nf.reshape(H, W), sf.reshape(H, W)
    bad = ~s.valid(z); z[bad] = np.nan; n[bad] = 0; sg[bad] = np.nan
    return dict(z=z, sigma=sg, n=n)


# ----------------------------------------------------------------------------------------------- helpers
def smooth(v, m, sig):
    a = ndimage.gaussian_filter(np.where(m, v, 0).astype(np.float32), sig, truncate=3)
    b = ndimage.gaussian_filter(m.astype(np.float32), sig, truncate=3)
    return np.where(b > 1e-3, a / np.maximum(b, 1e-9), np.nan)


def local_surface(z, w, sig_cells, minw):
    """The others' surface from their own cells (weights w) within a few cells: a normalised Gaussian, trusted where
    the summed weight reaches minw. Returns the surface and its slope (per cell)."""
    w = np.where(np.isfinite(z), w, 0.0).astype(np.float32); zw = np.where(w > 0, z, 0).astype(np.float32) * w
    a = ndimage.gaussian_filter(zw, sig_cells, truncate=3); b = ndimage.gaussian_filter(w, sig_cells, truncate=3)
    R = np.where(b * 2 * np.pi * sig_cells ** 2 >= minw, a / np.maximum(b, 1e-9), np.nan)
    gy, gx = np.gradient(np.nan_to_num(R, nan=0)); s = np.hypot(gx, gy); s[~np.isfinite(R)] = np.nan
    return R.astype(np.float32), s.astype(np.float32)


def nearest_fill(a):
    f = np.isfinite(a)
    if f.all() or not f.any(): return a
    i = ndimage.distance_transform_edt(~f, return_distances=False, return_indices=True)
    return a[i[0], i[1]]


def write(path, a, win, res, epsg, dtype, nodata):
    import rasterio
    from rasterio.transform import from_origin
    H, W = a.shape
    prof = dict(driver="GTiff", height=H, width=W, count=1, crs=f"EPSG:{epsg}", transform=from_origin(win[0], win[3], res, res),
                tiled=True, blockxsize=512, blockysize=512, compress="zstd", dtype=dtype, nodata=nodata, BIGTIFF="IF_SAFER")
    if dtype == "float32": prof["predictor"] = 3
    with rasterio.open(path, "w", **prof) as d:
        d.write((np.where(np.isfinite(a), a, nodata) if dtype == "float32" else a).astype(dtype), 1)


# ----------------------------------------------------------------------------------------------- vintage
def vintage_map(vint, obs, srcs, win, res, epsg, cfg, rep):
    """uint8 zone code per cell (0 pooled, k = the k-th vintage entry); zone k allows only entry k's sources."""
    H, W = win_shape(win, res); V = np.zeros((H, W), np.uint8); rep["vintage_km2"] = {}
    byname = {s.name: i for i, s in enumerate(srcs)}
    out = []                                                     # the entries as applied (an "allow": "newest" entry expands)
    for e in vint:
        if "mask" in e:
            m = np.nan_to_num(read_to(e["mask"], win, res, epsg, "nearest"), nan=0) > 0
        elif "floodplain" in e:
            m = _floodplain(e["floodplain"], obs, srcs, byname, win, res, epsg)
        elif "beach" in e:
            m = _beach(e["beach"], obs, srcs, byname, win, res, epsg)
        else:
            raise ValueError(f"vintage entry {e.get('name')}: mask | floodplain | beach")
        if e.get("allow") == "newest":
            # (2026-10-29, the whole bay) the zone takes, cell by cell, the NEWEST lidar survey that has ground there:
            # one zone per survey in date order, each over the cells the newer ones left. Cells no lidar saw stay pooled.
            lid = sorted([s for s in srcs if s.kind == "lidar" and obs.get(s.name) is not None and np.isfinite(s.date)],
                         key=lambda s: -s.date)
            left = m & (V == 0)
            for s in lid:
                mk = left & np.isfinite(obs[s.name]["z_g"])
                if not mk.any(): continue
                k = len(out) + 1; V[mk] = k; left &= ~mk
                out.append(dict(e, name=f"{e.get('name', 'zone')}_{s.name}", allow=[s.name]))
                rep["vintage_km2"][out[-1]["name"]] = round(float(mk.sum()) * res * res / 1e6, 3)
        else:
            k = len(out) + 1; V[m] = k; out.append(e)
            rep["vintage_km2"][e.get("name", f"zone{k}")] = round(float(m.sum()) * res * res / 1e6, 3)
    return V, out


def _solid_z(obs, srcs, order):
    """First finite solid value among the named sources, in order."""
    z = None
    for n in order:
        o = obs.get(n)
        if o is None: continue
        zk = o["z_g"] if "z_g" in o else o["z"]
        z = zk.copy() if z is None else np.where(np.isfinite(z), z, zk)
    return z


def _floodplain(p, obs, srcs, byname, win, res, epsg):
    """The pilot's active-floodplain zone (joint_fuse.main step 2): channel cells grown over ground < hand_m above the
    creek's water surface that the survey sees bare (highest return < veg_m above ground) or that any survey sees as
    water, connected to the channel, within dist_m of it, below zmax_m; small bars inside join."""
    chan = np.nan_to_num(read_to(p["chan"], win, res, epsg), nan=0) > 0
    eta = read_to(p["eta"], win, res, epsg); eta[eta < -9000] = np.nan
    seed = chan & np.isfinite(eta)
    if not seed.any(): return np.zeros(chan.shape, bool)
    idx = ndimage.distance_transform_edt(~seed, return_distances=False, return_indices=True); eta_near = eta[idx[0], idx[1]]
    sv = p.get("survey"); S = obs.get(sv) if sv else None
    lid = [s.name for s in sorted((s for s in srcs if s.kind == "lidar" and np.isfinite(s.date)), key=lambda s: -s.date)]
    zb = _solid_z(obs, srcs, ([sv] if sv else []) + [n for n in lid if n != sv] + [s.name for s in srcs if s.name != sv and s.name not in lid])
    hand = zb - eta_near
    if S is not None:
        veg = S["zmax"] - S["z_g"]
    else:                                                        # no fixed survey ("allow": "newest"): bare if ANY lidar sees it bare
        veg = np.full(zb.shape, np.nan, np.float32)
        for n in lid:
            o = obs.get(n)
            if o is not None and "zmax" in o: veg = np.fmin(veg, o["zmax"] - o["z_g"])
    anyw = np.zeros(zb.shape, bool)
    for o in obs.values():
        if o is not None and "n_w" in o: anyw |= o["n_w"] > 0
    bare = (np.isfinite(veg) & (veg <= p.get("veg_m", 1.0))) | anyw
    near = ndimage.distance_transform_edt(~chan, sampling=res) <= p.get("dist_m", 600.0)
    cand = (hand < p.get("hand_m", 3.0)) & bare & near & (np.nan_to_num(zb, nan=1e9) < p.get("zmax_m", 1e9))
    cand = ndimage.binary_closing(cand, iterations=2) & near
    lab, n = ndimage.label(cand | chan, structure=np.ones((3, 3)))
    keep = np.unique(lab[chan]); keep = keep[keep > 0]; fp = np.isin(lab, keep)
    holes = ndimage.binary_fill_holes(fp) & ~fp; hl, hn = ndimage.label(holes)
    if hn:
        hs = ndimage.sum(holes, hl, np.arange(1, hn + 1)) * res * res
        fp |= np.isin(hl, np.flatnonzero(hs <= p.get("bar_m2", 5000)) + 1)
    return fp


def _beach(p, obs, srcs, byname, win, res, epsg):
    """Sea-connected ground (the water layer's sea raster, dilated) below zmax_m: one survey, steps allowed."""
    sea = np.isfinite(read_to(p["sea"], win, res, epsg))
    lid = [s.name for s in srcs if s.kind == "lidar"]
    zb = _solid_z(obs, srcs, ([p["survey"]] if p.get("survey") else []) + lid)
    it = max(1, int(round(p.get("dilate_m", 20.0) / res)))
    # the band of sea-connected GROUND: within dilate_m of the sea, lidar ground below zmax_m, and not the sea itself
    # (the open bay is no beach: 78 km2 of 'beach' over the full box before this)
    return ndimage.binary_dilation(sea, iterations=it) & ~sea & (np.nan_to_num(zb, nan=99) < p.get("zmax_m", 8.0))


# ----------------------------------------------------------------------------------------------- fuse
def _glacier_raster(gp, win, res, epsg, shape, log=print):
    """The recipe's glacier outline (any CRS) rasterized onto the fuse grid; None without one."""
    if not gp or not Path(str(gp)).exists():
        return None
    import fiona
    from rasterio import features
    from rasterio.transform import from_origin
    from rasterio.warp import transform_bounds, transform_geom
    T = from_origin(win[0], win[3], res, res); crs = f"EPSG:{epsg}"
    geoms = []
    with fiona.open(str(gp)) as src:
        src_crs = src.crs_wkt or src.crs
        bb = transform_bounds(crs, src_crs, win[0], win[1], win[2], win[3])
        for f in src.filter(bbox=bb):
            geoms.append((transform_geom(src_crs, crs, f["geometry"]), 1))
    if not geoms:
        return None
    return features.rasterize(geoms, out_shape=shape, transform=T, fill=0, dtype="uint8").astype(bool)


def fuse(recipe_path, win=None, out=None, res=None, log=print, dump=False):
    r, cfg, srcs, vint, prior, epsg, bounds = load_recipe(recipe_path)
    res = float(res or cfg["res_m"]); win = tuple(win or bounds)
    out = Path(out or r["joint"].get("out") or (Path(recipe_path).parent / "joint")); out.mkdir(parents=True, exist_ok=True)
    H, W = win_shape(win, res); t0 = time.time(); rep = dict(window=list(win), res_m=res, sources={})
    # ---- read
    obs = {}
    for s in srcs:
        if s.kind == "lidar": o = obs_lidar(s, win, res, cfg)
        elif s.kind == "grid": o = obs_grid(s, win, res, epsg, cfg)
        elif s.kind == "points": o = obs_points(s, win, res, cfg)
        else: raise ValueError(s.kind)
        obs[s.name] = o
        z = None if o is None else (o["z_g"] if "z_g" in o else o["z"])
        rep["sources"][s.name] = dict(kind=s.kind, cls=s.cls, date=round(s.date, 2) if np.isfinite(s.date) else None,
                                      cells_km2=round(float(np.isfinite(z).sum()) * res * res / 1e6, 3) if z is not None else 0.0)
        log(f"  {s.name:14s} {s.kind:6s} {s.cls:6s} {rep['sources'][s.name]['cells_km2']:8.3f} km2" + (f"  D {o['D']:.1f} m" if o and "D" in o else ""))
    names = [s.name for s in srcs if obs[s.name] is not None]; S = {s.name: s for s in srcs}
    # per-source solid value, weight, sigma on the working grid
    Z, SG, N = {}, {}, {}
    for n in names:
        o = obs[n]
        if "z_g" in o: Z[n] = o["z_g"]; N[n] = o["n_g"]; SG[n] = np.full((H, W), S[n].sigma_m, np.float32); o.pop("zmin_g", None)
        else: Z[n] = o["z"]; N[n] = o["n"]; SG[n] = o["sigma"]
    rates = cfg["rates"]; newest = max(S[n].date for n in names if np.isfinite(S[n].date))
    for n in names:
        age = max(0.0, newest - S[n].date) if np.isfinite(S[n].date) else 0.0
        if age > 0: SG[n] = np.hypot(SG[n], float(rates.get(S[n].cls, 0.01)) * age).astype(np.float32)
    rep["age_sigma_m"] = {n: round(float(rates.get(S[n].cls, 0.01)) * max(0.0, newest - S[n].date), 3) for n in names if np.isfinite(S[n].date)}
    # ---- 1. vintage map
    V, vint = vintage_map(vint, obs, srcs, win, res, epsg, cfg, rep)
    # a zone arbitrates among the sources that see the moving surface -- by default the lidar surveys. A beach zone
    # that excluded every source but KBay 2023 also excluded the multibeam across the bay (full box, 2026-10-22)
    allow = {n: np.ones((H, W), bool) for n in names}
    for k, e in enumerate(vint, 1):
        among = e.get("among") or [s.name for s in srcs if s.kind == "lidar"]
        for n in names:
            if n in among and n not in e["allow"]: allow[n] &= V != k
    # ---- 2. alignment of lidar sources to the reference on stable bare ground, outside the zones
    ref = next((s.name for s in srcs if s.reference and obs[s.name] is not None), None); rep["alignment"] = {}
    if ref is not None:
        A = obs[ref]; gy, gx = np.gradient(np.nan_to_num(A["z_g"], nan=0), res); slope = np.degrees(np.arctan(np.hypot(gx, gy)))
        for n in names:
            if S[n].kind != "lidar" or n == ref: continue
            o = obs[n]; dz = A["z_g"] - o["z_g"]
            st = ((A["n_g"] >= 2) & (o["n_g"] >= 2) & (np.abs(dz) < 1) & (slope < cfg.get("align_slope_deg", 3.0)) & (V == 0)
                  & ((A["zmax"] - A["z_g"]) < 0.5) & ((o["zmax"] - o["z_g"]) < 0.5))
            # where the two disagree, per km: one offset cannot hold a tilt or real change (Grewingk 2021 vs KBay 2023,
            # 2026-10-22: -0.01 m inside the pilot window, -0.24 m outside it) -- the map says which
            bk = int(round(1000 / res)); rows = []
            for r0 in range(0, H, bk):
                for c0 in range(0, W, bk):
                    m_ = st[r0:r0 + bk, c0:c0 + bk]
                    if m_.sum() >= 500:
                        d_ = dz[r0:r0 + bk, c0:c0 + bk][m_]
                        rows.append(f"{win[0] + c0 * res:.0f},{win[3] - (r0 + bk) * res:.0f},{int(m_.sum())},{float(np.median(d_)):.3f},{float(1.4826 * np.median(np.abs(d_ - np.median(d_)))):.3f}")
            (out / f"align_{n}_per_km.csv").write_text("x0,y0,cells,median_ref_minus,nmad\n" + "\n".join(rows) + "\n")
            if st.sum() >= cfg["align_min_cells"]:
                off = float(np.median(dz[st])); nmad = float(1.4826 * np.median(np.abs(dz[st] - off)))
            else: off, nmad = 0.0, 0.2
            rep["alignment"][n] = dict(cells=int(st.sum()), median_ref_minus=round(off, 3), nmad=round(nmad, 3), applied=round(off, 3))
            for k in ("z_g", "zmin_g", "z_w", "zmax"):
                if k in o: o[k] = o[k] + off
            Z[n] = o["z_g"]; SG[n] = np.hypot(SG[n], nmad / 2).astype(np.float32)
    # ---- 2b. a DERIVED compilation calibrated to the measured surveys it interfingers with (Hig, 2026-10-25: upper
    # Kachemak Bay is OCM / NCEI between sparse multibeam lines). Where both have cells, measured - derived is a smooth
    # field (normalised Gaussian over derived_adjust_m, on a derived_adjust_coarse x grid) that adjusts the compilation
    # wherever it stands -- between a multibeam's passes, beyond its last line -- and the residual spread about the
    # field goes into its sigma; far from any overlap the field settles to the global weighted offset. The measured
    # survey still wins where it has cells (3. below); between them the two now meet as one plausible surface.
    rep["derived_adjust"] = {}
    ka = float(cfg.get("derived_adjust_m", 0.0))
    if ka > 0:
        cf = max(int(cfg.get("derived_adjust_coarse", 4)), 1); sg_c = ka / (res * cf)
        def _blk(a):
            Hp, Wp = -(-H // cf) * cf, -(-W // cf) * cf
            b = np.zeros((Hp, Wp), np.float64); b[:H, :W] = a
            return b.reshape(Hp // cf, cf, Wp // cf, cf).sum(axis=(1, 3))
        def _up(a):
            return np.kron(a, np.ones((cf, cf), a.dtype))[:H, :W]
        for n in names:
            if not S[n].derived or not np.isfinite(Z[n]).any():
                continue
            meas = [m for m in names if not S[m].derived and S[m].cls == S[n].cls and np.isfinite(Z[m]).any()]
            if not meas:
                continue
            num = np.zeros((H, W), np.float32); den = np.zeros((H, W), np.float32)
            for m in meas:
                both = np.isfinite(Z[n]) & np.isfinite(Z[m]) & allow[m] & allow[n]
                if not both.any():
                    continue
                w_ = np.where(both, 1.0 / np.maximum(np.nan_to_num(SG[m], nan=S[m].sigma_m), cfg["sigma_floor_m"]) ** 2, 0.0).astype(np.float32)
                num += w_ * np.nan_to_num(Z[m] - Z[n]); den += w_
            if not (den > 0).any():
                continue
            g = float(num.sum() / den.sum())                                  # the global weighted offset
            r2 = np.where(den > 0, (num / np.maximum(den, 1e-12)) ** 2 * den, 0.0).astype(np.float32)
            nc, dc, qc = _blk(num), _blk(den), _blk(r2)
            ns, ds, qs = (ndimage.gaussian_filter(a, sg_c) for a in (nc, dc, qc))
            eps = 1e-4 * float(dc.max())                                      # a weak pull to the global offset beyond the overlap's reach
            F_c = (ns + eps * g) / (ds + eps)
            var_c = np.maximum(qs / np.maximum(ds, 1e-12) - (ns / np.maximum(ds, 1e-12)) ** 2, 0.0)
            s_c = np.sqrt(var_c)
            F = _up(F_c.astype(np.float32)); s_ = _up(s_c.astype(np.float32))
            has = np.isfinite(Z[n])
            Z[n] = np.where(has, Z[n] + F, np.nan).astype(np.float32)
            SG[n] = np.where(has, np.hypot(np.nan_to_num(SG[n], nan=S[n].sigma_m), s_), SG[n]).astype(np.float32)
            o = obs[n]
            for k in ("z", "z_g", "zmin_g"):
                if k in o and o[k] is not None: o[k] = np.where(np.isfinite(o[k]), o[k] + F, o[k])
            ov = float((den > 0).sum()) * res * res / 1e6
            rep["derived_adjust"][n] = dict(against=meas, overlap_km2=round(ov, 3), global_offset_m=round(g, 3),
                                           field_p10_50_90=[round(float(v), 2) for v in np.percentile(F[has], [10, 50, 90])],
                                           residual_rms_p50=round(float(np.median(s_[has])), 3))
            log(f"  {n:14s} calibrated to {', '.join(meas)}: overlap {ov:.3f} km2, offset {g:+.2f} m, field p10/50/90 "
                f"{rep['derived_adjust'][n]['field_p10_50_90']}, residual rms p50 {rep['derived_adjust'][n]['residual_rms_p50']:.2f} m")
            del num, den, r2, F, s_
    # ---- 3. epoch: an older source yields wherever a source newer by > pool_years has a local surface
    xs = cfg["xc_sigma_m"] / res; minw = cfg["xc_minw"]
    Wt = {n: np.where(np.isfinite(Z[n]) & allow[n], N[n] / np.maximum(SG[n], cfg["sigma_floor_m"]) ** 2, 0.0).astype(np.float32) for n in names}
    R1 = {n: local_surface(Z[n], np.minimum(N[n], 5.0) * (allow[n] & np.isfinite(Z[n])), xs, minw) for n in names if S[n].kind == "lidar"}   # own surface: the spike test
    near = {n: ndimage.binary_dilation(allow[n] & np.isfinite(Z[n]), iterations=max(1, int(np.ceil(2 * xs)))) for n in names}            # has cells within the cross-check reach
    # pooling window by the NEWER source's class: ground pools across ~5 yr, ice across months (it moves metres a
    # year: Dixon 2023 was rejected 'low' over 7.6 km2 against Grewingk 2021's thicker ice, 2026-10-22), a sea bed
    # across decades with its sigma grown by the rate instead (a 2019 merged product superseded the 2009 multibeam
    # wholesale under one 5 yr window)
    py = cfg["pool_years"]; py = py if isinstance(py, dict) else dict(DEFAULTS["pool_years"], ground=float(py))   # a bare number is the ground window only
    sup = {n: np.zeros((H, W), bool) for n in names}
    for n in names:
        for m in names:
            # a BED is superseded only by a newer bed: a lidar's ground over the sea (its surface misclassed, a bar at
            # the stand of that day) is judged against the bed by the cross-check, never by its date (2026-10-26: Grewingk
            # 2019's "ground" at the water level superseded the 2009 multibeam under Halibut Cove's ridge -- islands)
            if S[n].cls == "bed" and S[m].cls != "bed":
                continue
            if S[m].date - S[n].date > float(py.get(S[m].cls, 5.0)):
                sup[n] |= np.isfinite(Z[n]) & near[m]
    # ...and INSIDE THE GLACIER OUTLINE every source is ice, whatever its vendor class (2026-10-25, Hig: a jaggy seam on
    # Grewingk Glacier where the 2021 lidar's "ground" stood beside and against the 2023 SfM -- ice moves metres a year,
    # so the two epochs cross-rejected each other cell by cell): the ice window applies, and the older yields.
    gl = _glacier_raster(cfg.get("glaciers"), win, res, epsg, (H, W), log)
    if gl is not None and gl.any():
        ng = 0
        for n in names:
            for m in names:
                if S[m].date - S[n].date > float(py.get("ice", 0.5)) and S[m].date - S[n].date <= float(py.get(S[m].cls, 5.0)):
                    add = np.isfinite(Z[n]) & near[m] & gl & ~sup[n]
                    sup[n] |= add; ng += int(add.sum())
        log(f"  glacier outline: {float(gl.sum()) * res * res / 1e6:.2f} km2 under the ice window; {ng * res * res / 1e6:.3f} km2 of older cells yield there")
    # a DERIVED product -- a compilation built from the surveys it overlaps (OCM's 2 m candidate, NCEI's 1/3" grid, the
    # CGOA grid) -- is not independent evidence: it yields wherever a measured source of its class has cells within the
    # cross-check reach, and holds only beyond them (2026-10-23, run 14, Hig: low-relief seams around smooth-edged patches
    # in the multibeam -- OCM in and out of the pool cell by cell as it passed or failed the cross-check, 0.5-1 m steps)
    # ...and between a measured survey's PASSES (2026-10-25, Hig: a multibeam's swaths 50-100 m apart in Halibut Cove
    # with the compilation's flat interpolation standing between them as shelves): the measured survey's presence is
    # CLOSED over derived_gap_m, so a gap inside its coverage counts as its -- the compilation yields there and the
    # fill interpolates the passes -- while beyond its outer edge the compilation still holds.
    gapc = int(round(float(cfg.get("derived_gap_m", 0.0)) / res / 2))
    for n in names:
        if S[n].derived:
            for m in names:
                if not S[m].derived and S[m].cls == S[n].cls:
                    pres = near[m]
                    if gapc > 0:
                        pres = pres | ndimage.binary_closing(allow[m] & np.isfinite(Z[m]), iterations=gapc)
                    sup[n] |= np.isfinite(Z[n]) & pres
    del near
    rep["superseded_km2"] = {n: round(float(sup[n].sum()) * res * res / 1e6, 3) for n in names}
    # ---- 4./5. cross-check and over-water, each source against the others' surviving cells. Weights in CELLS (min(n, 5),
    # a grid cell 1) decide where the others' surface is trusted; 1/sigma^2 decides its value; the tolerance carries the
    # slope and BOTH sigmas, so a fine survey is not rejected against a coarse bathymetry it disagrees with by metres.
    # a GRID cell counts grid_wc (0.1), not 1 -- local_surface sums weights over its kernel (2 pi sigma^2 ~ 6.3 cells), so
    # 0.1 lets one or two products reach only 0.6-1.3 of minw 1.5: a gridded product alone, or two, cannot form the surface that rejects a
    # survey with return support, and two products that disagree cannot reject each other and leave the worst third
    # (run 7: OCM and the 2009 multibeam annihilated cell by cell and NCEI showed as a moire; a 2019 product's -4.6 m
    # rejected 6-14 lidar ground returns/m2 at -1.7 on the tide flat)
    Wc = {n: np.where(np.isfinite(Z[n]) & allow[n] & ~sup[n], np.minimum(N[n], 5.0) if S[n].kind == "lidar" else cfg["grid_wc"] * N[n], 0.0).astype(np.float32) for n in names}
    # ...except that a CLEARLY BETTER product (sigma <= grid_better x this one's) counts as a full cell against a grid
    # source: the 2009 multibeam (0.5 m) rejects the 2019 OCM product (1 m) where that stands 6-8 m off it (run 9:
    # OCM's artifacts pulled the pooled bed 1.7 m); OCM still cannot reject the multibeam, and nothing gridded a survey
    WcB = {n: np.where(Wc[n] > 0, np.minimum(N[n], 5.0), 0.0).astype(np.float32) for n in names if S[n].kind == "grid"}
    # a LIDAR's ground returns standing at its OWN water level nearby, over a MEASURED BED of another source, are its
    # water surface (2026-10-25/26: KBay 2023 at 3.85 m near high water and Grewingk 2019 classed ground over Halibut
    # Cove's submarine ridge -- islands in the stage, the 2009 multibeam rejected low under them; two "ground" returns
    # at 0.79 m in the lagoon's -49 m bed).  Found BEFORE the cross-check and given no weight in it, so the bed under
    # them is judged against nothing.  Over a river the banks' wet ground stands at the water level too, and no bed
    # survey lies under it: untouched (a first cut in obs_lidar without the bed test bridged the river to the lake
    # through its banks, and the water stage made them one lake).
    own_surf = {}
    if float(cfg.get("surf_near_m", 0.0)) > 0:
        bed_any = np.zeros((H, W), bool)
        for m in names:
            if S[m].cls == "bed" and not S[m].derived:
                bed_any |= ndimage.binary_dilation(allow[m] & np.isfinite(Z[m]), iterations=max(1, int(np.ceil(2 * xs))))
        for n in names:
            if S[n].kind != "lidar" or "z_w" not in obs[n] or not bed_any.any():
                continue
            hw_own = np.isfinite(obs[n]["z_w"]) & (obs[n]["n_w"] > 0)
            if not hw_own.any():
                continue
            d_own, (oy, ox) = ndimage.distance_transform_edt(~hw_own, sampling=res, return_indices=True)
            wl_own = obs[n]["z_w"][oy, ox]; del oy, ox
            os_ = (allow[n] & np.isfinite(Z[n]) & (d_own <= cfg["surf_near_m"]) & bed_any
                   & (np.abs(Z[n] - wl_own) <= cfg["over_water_tol_m"]))
            del d_own, wl_own
            if os_.any():
                own_surf[n] = os_; Wt[n][os_] = 0.0; Wc[n][os_] = 0.0
                log(f"  {n:14s} ground at its own water level over a measured bed: {float(os_.sum()) * res * res / 1e4:.1f} ha is its water surface")
    else:
        bed_any = np.zeros((H, W), bool)
    # A FLAT SHEET OF GROUND within the tide's reach, part of which stands more than stand_dz_m above water ANY survey
    # saw in the same cells, is the sea at another stand (2026-10-26, Hig's review of run 20: Grewingk 2019, with no
    # water returns of its own, read Halibut Cove's high water at 3.74-3.93 m as ground over 25 ha of the submarine
    # ridge, where KBay 2023 saw water at -2.4 and Grewingk 2021 at 0.8 in the same cells).  Judged by the sheet, not
    # the cell: the water returns fall on a part of it, the sheet is one surface.  A marsh or a bar beside a channel has
    # water beside it, not under it.
    stand = {}
    zwmin = np.full((H, W), np.inf, np.float32)
    for m in names:
        if "z_w" in obs[m]:
            zwmin = np.fmin(zwmin, np.where(np.isfinite(obs[m]["z_w"]) & allow[m], obs[m]["z_w"], np.inf))
    if np.isfinite(zwmin).any():
        tm_ = float(cfg.get("tide_max_m", np.inf))
        for n in names:
            if S[n].kind != "lidar":
                continue
            zf_ = np.isfinite(Z[n]) & allow[n]
            mx_ = ndimage.maximum_filter(np.where(zf_, Z[n], -1e9), 5); mn_ = ndimage.minimum_filter(np.where(zf_, Z[n], 1e9), 5)
            flat_ = zf_ & ((mx_ - mn_) <= float(cfg["stand_flat_m"])) & (Z[n] <= tm_)
            del mx_, mn_
            if not flat_.any():
                continue
            und_ = flat_ & np.isfinite(zwmin) & (zwmin < Z[n] - float(cfg["stand_dz_m"]))
            lab_, nl_ = ndimage.label(flat_, structure=np.ones((3, 3), bool))
            cnt_ = np.bincount(lab_.ravel(), minlength=nl_ + 1); und_n = np.bincount(lab_.ravel(), weights=und_.ravel(), minlength=nl_ + 1)
            bad_ = (und_n >= float(cfg["stand_min_cells"])) & (und_n >= float(cfg["stand_frac"]) * cnt_); bad_[0] = False
            st_ = bad_[lab_]
            if st_.any():
                stand[n] = st_; Wt[n][st_] = 0.0; Wc[n][st_] = 0.0
                log(f"  {n:14s} flat ground over others' lower water: {int(bad_.sum())} sheets, {float(st_.sum()) * res * res / 1e4:.2f} ha are the sea at another stand")
            del lab_, flat_, und_
    del zwmin
    rej = np.zeros((H, W), np.uint16); kept = {}; why = {}
    # TWO PASSES (2026-10-26): the first only finds every source's cells OVER WATER (a lidar's sea surface classed
    # ground, at its own stand or at another survey's) and takes their weight away; the second judges with those
    # cells gone, so a bed is never rejected low against a lidar's sea surface (Grewingk 2019's "ground" at 3.85 m over
    # Halibut Cove's ridge rejected the 2009 multibeam under it, and the ridge became an island)
    ow_pre = {}
    for passno, (k, n) in [(p_, kn_) for p_ in (0, 1) for kn_ in enumerate(names)]:
        others = [m for m in names if m != n]
        wo = sum(np.where(sup[m], 0, Wt[m]) for m in others)
        wc = sum((WcB[m] if (S[n].kind == "grid" and S[m].kind == "grid" and S[m].sigma_m <= cfg["grid_better"] * S[n].sigma_m) else Wc[m]) for m in others)
        zo = sum(np.where(sup[m] | ~np.isfinite(Z[m]), 0, Z[m] * Wt[m]) for m in others)
        zo = np.where(wo > 0, zo / np.maximum(wo, 1e-12), np.nan)
        so = sum(np.where(sup[m] | ~np.isfinite(Z[m]), 0, np.nan_to_num(SG[m], nan=1.0) * Wt[m]) for m in others)
        so = np.where(wo > 0, so / np.maximum(wo, 1e-12), np.nan)
        R, s = local_surface(zo, wc, xs, minw); sR, _ = local_surface(so, wc, xs, minw)
        sk = np.nan_to_num(SG[n], nan=S[n].sigma_m); sR = np.nan_to_num(sR, nan=0)
        tol = cfg["tol_m"] + 2.0 * np.nan_to_num(s, nan=0) + 2.0 * np.hypot(sk, sR)
        ok = allow[n] & ~sup[n] & np.isfinite(Z[n]) & np.isfinite(R)
        hi = ok & (Z[n] > R + tol)
        lowtol = cfg["tol_low_m"] + 2.0 * np.nan_to_num(s, nan=0) + 2.0 * np.hypot(sk, sR)
        if S[n].kind == "lidar":
            own = R1[n][0]; lo = ok & (Z[n] < R - lowtol) & (np.nan_to_num(own, nan=-1e9) - Z[n] > 0.5)
        else:
            lo = ok & (Z[n] < R - lowtol)
        # over open water seen by the others' water returns
        nw = sum(np.where(np.isfinite(obs[m].get("z_w", np.nan)), obs[m]["n_w"], 0) for m in others if "z_w" in obs[m]) if any("z_w" in obs[m] for m in others) else None
        ow = np.zeros((H, W), bool)
        if nw is not None and (nw > 0).any():
            zwo = sum(np.where(np.isfinite(obs[m]["z_w"]), obs[m]["z_w"] * obs[m]["n_w"], 0) for m in others if "z_w" in obs[m]) / np.maximum(nw, 1e-9)
            hw = nw > 0; hg = wo > 0
            dwo, iw = ndimage.distance_transform_edt(~hw, sampling=res, return_indices=True)
            # the dry-ground distance counts the others' GROUND and ICE sources only (2026-10-26): a bed source lies under
            # every cell of the bay and is not dry ground, and with it counted the test never fired where it mattered
            wo_dry = sum(np.where(sup[m], 0, Wt[m]) for m in others if S[m].cls != "bed")
            hg_dry = wo_dry > 0
            dgo = ndimage.distance_transform_edt(~hg_dry, sampling=res) if hg_dry.any() else np.full((H, W), np.inf)
            del wo_dry, hg_dry
            wlev = zwo[iw[0], iw[1]]
            zxo = np.full((H, W), np.nan, np.float32)
            for m in others:
                if "zmax" in obs[m]: zxo = np.fmax(zxo, obs[m]["zmax"])
            ow = (allow[n] & ~sup[n] & np.isfinite(Z[n]) & ~np.isfinite(R) & (dwo <= cfg["over_water_m"]) & (dgo > cfg["over_water_dry_m"])
                  & ~(np.nan_to_num(zxo, nan=-1e9) > wlev + cfg["over_water_tol_m"]) & (Z[n] > wlev + cfg["over_water_tol_m"]))
            # ...and nothing real stands over_water_max_m above the water with no ground of the others near: a tower of
            # bad returns (2026-10-25, Hig: Grewingk 2019 "ground" at 350-450 m over Halibut Cove, and the fill raised
            # every hole around it to that rim); a return the others saw above the water (zxo) does not save it
            # ...measured against the others' nearest ground too, so a cliff above the water stands (its top is the
            # others' ground, within over_water_max_m of the cell) and an outlier at the shoreline falls
            if hg.any():
                _, ig = ndimage.distance_transform_edt(~hg, return_indices=True)
                zg_near = np.where(dgo <= cfg["over_water_m"], zo[ig[0], ig[1]], -np.inf)
                del ig
            else:
                zg_near = np.full((H, W), -np.inf, np.float32)
            ow |= (allow[n] & ~sup[n] & np.isfinite(Z[n]) & (dwo <= cfg["over_water_m"]) & (Z[n] > wlev + cfg["over_water_max_m"])
                   & (Z[n] > zg_near + cfg["over_water_max_m"]))
            if S[n].cls == "bed":                 # a bed above the water surface observed in the same cell
                pass
            # ...and a LIDAR's ground returns standing at its OWN water level nearby, over a MEASURED BED of another
            # source, are its water surface (2026-10-25: KBay 2023 at 3.85 m, near high water, classed ground over
            # Halibut Cove's submarine ridge -- islands in the stage, the 2009 multibeam rejected low under them; two
            # "ground" returns at 0.79 m in the lagoon's -49 m bed).  Over a river the banks' wet ground stands at the
            # water level too, and no bed survey lies under it: untouched (the first cut, in obs_lidar without the bed
            # test, bridged the river to the lake through its banks and the water stage made them one lake).
            if n in own_surf:
                ow |= own_surf[n]
            if n in stand:
                ow |= stand[n]
            # ...and ground standing AT the others' water level (not only above it), over a measured bed, with none of
            # their ground near: another survey's sea surface at the same stand (2026-10-26: Grewingk 2019's ground at
            # 3.85 m over the ridge, with no water returns of its own, where KBay 2023's water returns read 3.85 m)
            # (the dry-ground distance here counts the others' GROUND and ICE sources only: a bed source lies under every
            # cell of the bay and is not dry ground)
            ow |= (allow[n] & ~sup[n] & np.isfinite(Z[n]) & (dwo <= cfg["over_water_m"]) & (dgo > cfg["over_water_dry_m"])
                   & bed_any & (np.abs(Z[n] - wlev) <= cfg["over_water_tol_m"]))
            if S[n].cls == "bed":                 # a bed above the water surface observed in the same cell
                zw_here = np.where(hw, zwo, np.nan)
                ow |= allow[n] & np.isfinite(Z[n]) & np.isfinite(zw_here) & (Z[n] > zw_here + cfg["bed_over_water_tol_m"])
        if passno == 0:
            ow_pre[n] = ow
            if n == names[-1]:                              # the pre-pass is complete: take the over-water cells' weight away
                for m in names:
                    Wt[m][ow_pre[m]] = 0.0; Wc[m][ow_pre[m]] = 0.0
                    if m in WcB: WcB[m][ow_pre[m]] = 0.0
            continue
        ow |= ow_pre[n]
        bad = hi | lo | ow | sup[n] | ~allow[n]
        rej[hi | lo | ow | sup[n]] |= np.uint16(1 << k)
        kept[n] = np.where(bad, np.nan, Z[n]).astype(np.float32)
        if dump:                                  # per-source verdicts for the review point cloud (points.npz)
            why[n] = (hi.astype(np.uint8) | (lo.astype(np.uint8) << 1) | (ow.astype(np.uint8) << 2) | (sup[n].astype(np.uint8) << 3)
                      | ((~allow[n]).astype(np.uint8) << 4) | ((own_surf[n] if n in own_surf else np.zeros((H, W), bool)).astype(np.uint8) << 5))
        rep["sources"][n].update(rejected_high_km2=round(float(hi.sum()) * res * res / 1e6, 3), rejected_low_km2=round(float(lo.sum()) * res * res / 1e6, 3),
                                 over_water_km2=round(float(ow.sum()) * res * res / 1e6, 3))
        log(f"  {n:14s} high {rep['sources'][n]['rejected_high_km2']:.3f}  low {rep['sources'][n]['rejected_low_km2']:.3f}  over water {rep['sources'][n]['over_water_km2']:.3f}  superseded {rep['superseded_km2'][n]:.3f} km2")
    # ---- 6. pool
    ws = {n: np.where(np.isfinite(kept[n]), N[n] / np.maximum(SG[n], cfg["sigma_floor_m"]) ** 2, 0.0).astype(np.float32) for n in names}
    wsum = sum(ws.values())
    ZJ = np.where(wsum > 0, sum(np.nan_to_num(kept[n]) * ws[n] for n in names) / np.maximum(wsum, 1e-12), np.nan).astype(np.float32)
    NJ = sum(np.where(np.isfinite(kept[n]), N[n], 0) for n in names).astype(np.float32)
    SRC = sum(np.isfinite(kept[n]).astype(np.uint16) << k for k, n in enumerate(names)).astype(np.uint16)
    SIG = np.where(wsum > 0, np.maximum(1 / np.sqrt(np.maximum(wsum, 1e-12)), cfg["sigma_floor_m"]), np.nan).astype(np.float32)
    DIST = ndimage.distance_transform_edt(~np.isfinite(ZJ), sampling=res).astype(np.float32)
    NL = sum(np.isfinite(kept[n]).astype(np.uint8) for n in names if S[n].cls in ("ground", "bed")).astype(np.uint8)
    # class of the heaviest survivor
    best = np.full((H, W), -1, np.int16); bw = np.zeros((H, W), np.float32)
    for k, n in enumerate(names):
        m = ws[n] > bw; best[m] = k; bw[m] = ws[n][m]
    CLSJ = np.zeros((H, W), np.uint8)
    for k, n in enumerate(names): CLSJ[best == k] = CLS[S[n].cls]
    # water-surface evidence: lidar water returns, vintage-aware, newest first
    WZ = np.full((H, W), np.nan, np.float32)
    for n in sorted([m for m in names if "z_w" in obs[m]], key=lambda m: -S[m].date):
        WZ = np.where(np.isnan(WZ) & allow[n], obs[n]["z_w"], WZ)
    rep["coverage"] = dict(supported=round(float(np.isfinite(ZJ).mean()), 3), void_km2=round(float((~np.isfinite(ZJ)).sum()) * res * res / 1e6, 3),
                           **{f"with_{n}": round(float(((SRC >> k) & 1).mean()), 3) for k, n in enumerate(names)},
                           water_cells_km2=round(float(np.isfinite(WZ).sum()) * res * res / 1e6, 3),
                           by_class_km2={c: round(float((CLSJ == v).sum()) * res * res / 1e6, 3) for c, v in CLS.items() if v and (CLSJ == v).any()})
    rep["src_bits"] = {n: 1 << k for k, n in enumerate(names)}; rep["seconds"] = round(time.time() - t0)
    for name, a, dt, nd in (("joint_z", ZJ, "float32", -9999), ("joint_n", NJ, "float32", -9999), ("joint_src", SRC, "uint16", 0),
                            ("joint_rej", rej, "uint16", 0), ("joint_dist", DIST, "float32", -9999), ("joint_sigma", SIG, "float32", -9999),
                            ("joint_nland", NL, "uint8", 255), ("joint_cls", CLSJ, "uint8", 255), ("vintage", V, "uint8", 255), ("water_z", WZ, "float32", -9999)):
        write(out / f"{name}.tif", a, win, res, epsg, dt, nd)
    (out / "joint_report.json").write_text(json.dumps(rep, indent=1)); log(json.dumps(rep["coverage"]))
    if dump:
        # REVIEW DUMP (2026-10-26, Hig: a point-cloud view of this step, coloured by source and verdict): every source's
        # 2 m observation with its verdict -- why bits: 1 high, 2 low, 4 over water, 8 superseded, 16 outside its
        # allowed area, 32 its own water surface -- plus the lidars' water returns and the pooled result
        d_ = dict(win=np.array(win, float), res=np.array(res, float), epsg=np.array(epsg), names=np.array(names),
                  dates=np.array([S[n].date for n in names], float), kinds=np.array([S[n].kind for n in names]),
                  classes=np.array([S[n].cls for n in names]), derived=np.array([bool(S[n].derived) for n in names]),
                  joint_z=ZJ, joint_cls=CLSJ, joint_src=SRC, water_z=WZ)
        for n in names:
            d_[f"z__{n}"] = Z[n].astype(np.float32); d_[f"n__{n}"] = N[n].astype(np.float32)
            d_[f"sg__{n}"] = np.asarray(SG[n], np.float32); d_[f"why__{n}"] = why.get(n, np.zeros((H, W), np.uint8))
            if "z_w" in obs[n]:
                d_[f"zw__{n}"] = obs[n]["z_w"].astype(np.float32); d_[f"nw__{n}"] = obs[n]["n_w"].astype(np.float32)
        np.savez_compressed(out / "points.npz", **d_); log(f"  review dump: {out / 'points.npz'}")
    return rep


# ----------------------------------------------------------------------------------------------- layer
def footprint(srcs, obs_any, win, res, epsg):
    """Where the surveys looked: per-source footprint files (json {name:{minx,miny,maxx,maxy}}, csv with WKT boxes,
    txt 'name minx miny maxx maxy ...'), else the cells with any return/value, closed over 50 m."""
    H, W = win_shape(win, res); F = np.zeros((H, W), bool); boxes = []
    for s in srcs:
        got = False
        for p in s.footprint:
            got = True; P = Path(p)
            if P.suffix == ".json":
                boxes += [(v["minx"], v["miny"], v["maxx"], v["maxy"]) for v in json.load(open(P)).values()]
            elif P.suffix == ".csv":
                for r in csv.DictReader(open(P)):
                    xs = [float(v) for v in re.findall(r"[-\d.]+", r["WKT"])]; boxes.append((min(xs[0::2]), min(xs[1::2]), max(xs[0::2]), max(xs[1::2])))
            elif P.suffix in (".tif", ".vrt"):
                F |= np.isfinite(read_to(str(P), win, res, epsg))
            else:
                boxes += [tuple(float(v) for v in l.split()[1:5]) for l in open(P) if len(l.split()) >= 5]
        if not got and s.name in obs_any:
            F |= ndimage.binary_closing(obs_any[s.name], iterations=max(1, int(50 / res)))
    for x0, y0, x1, y1 in boxes:
        c0, c1 = int(max((x0 - win[0]) / res, 0)), int(min((x1 - win[0]) / res, W)); r0, r1 = int(max((win[3] - y1) / res, 0)), int(min((win[3] - y0) / res, H))
        if c1 > c0 and r1 > r0: F[r0:r1, c0:c1] = True
    return F


def prior_slope(prior, win, res, epsg, cfg):
    rs = cfg["prior_slope_res_m"]; z = read_to(prior, win, rs, epsg, "bilinear")
    z = ndimage.gaussian_filter(np.nan_to_num(z, nan=float(np.nanmedian(z))), 2)
    gy, gx = np.gradient(z, rs); sl = np.degrees(np.arctan(np.hypot(gx, gy)))
    H, W = win_shape(win, res)
    return ndimage.zoom(sl, (H / sl.shape[0], W / sl.shape[1]), order=0)[:H, :W] if sl.shape != (H, W) else sl


def layer(recipe_path, win=None, out=None, res=None, log=print, tag=""):
    import rasterio
    r, cfg, srcs, vint, prior, epsg, bounds = load_recipe(recipe_path)
    res = float(res or cfg["res_m"]); win = tuple(win or bounds)
    out = Path(out or r["joint"].get("out") or (Path(recipe_path).parent / "joint"))
    def rd(n, dt=np.float32):
        with rasterio.open(out / f"{n}.tif") as s:
            a = s.read(1).astype(dt)
            if dt is np.float32: a[a == s.nodata] = np.nan
            return a
    Z = rd("joint_z"); Wz = rd("water_z"); CJ = rd("joint_cls", np.uint8)
    # any return at all, per lidar source (zmax), for the hole test and the footprint fallback
    zmax = np.full(Z.shape, np.nan, np.float32); obs_any = {}
    for s in srcs:
        if s.kind == "lidar":
            o = obs_lidar(s, win, res, cfg)
            if o is None: continue
            zmax = np.fmax(zmax, o["zmax"]); obs_any[s.name] = np.isfinite(o["zmax"])
        elif s.kind == "grid":
            o = obs_grid(s, win, res, epsg, cfg)
            if o is not None: obs_any[s.name] = np.isfinite(o["z"])
    g = np.isfinite(Z); w = np.isfinite(Wz) & ~g
    noret = ~np.isfinite(zmax) & ~g & ~w
    dg = ndimage.distance_transform_edt(~g, sampling=res); dw = ndimage.distance_transform_edt(~w, sampling=res)
    lev = nearest_fill(smooth(Wz, w, 3.0 / res))
    wet = w | (~g & (dw < dg) & (dw <= cfg["wet_m"]) & (noret | (np.nan_to_num(zmax, nan=-1e9) < lev + 1.0)))
    wet = ndimage.binary_opening(wet, iterations=1) | w
    level = np.where(wet, lev, np.nan)
    gap = ~g & ~wet & ~noret & (dg <= cfg["gap_m"])      # returns but no ground (a bush) within gap_m of ground: interpolated
    F = footprint(srcs, obs_any, win, res, epsg)
    steep = prior_slope(prior, win, res, epsg, cfg) > cfg["steep_deg"] if prior else np.zeros(Z.shape, bool)
    hole = noret & ~wet & ~gap
    lab, n = ndimage.label(hole, structure=np.ones((3, 3))); rep = {}
    if n:
        ring = ndimage.binary_dilation(hole, structure=np.ones((3, 3))) & ~hole
        rl = ndimage.grey_dilation(lab, size=3) * ring
        idx = np.arange(1, n + 1)
        open_edge = ndimage.maximum(ndimage.binary_dilation(~F, structure=np.ones((3, 3))), lab, idx) > 0
        touches_water = ndimage.maximum(ring & wet, rl, idx) > 0
        # the rim is SURFACE (ground, ice), never a measured bed: a no-return hole ringed by the sonar's bed is water
        # above it at an unknown level, not a lake at the bed's height (2026-10-23, Grewingk's outlet bay)
        m = (rl > 0) & g & (CJ != CLS["bed"]); ll, zz = rl[m], Z[m]; o = np.lexsort((zz, ll)); ll, zz = ll[o], zz[o]
        u, st, ct = np.unique(ll, return_index=True, return_counts=True)
        rim = np.full(n + 1, np.nan, np.float32); rim[u] = zz[st + (ct // 10)]
        # the rim must be LEVEL: a lake's shore stands at one height; a hole whose rim spans more than hole_rim_tol_m
        # (p90 - p10 of its ring's ground) is a void on a slope or a glacier, not water (run 7: glacier voids with rims
        # from 391 to 431 m and slope voids became lakes with basins)
        rim_hi = np.full(n + 1, np.nan, np.float32); rim_hi[u] = zz[st + ct - 1 - (ct // 10)]
        level_rim = np.zeros(n + 1, bool); level_rim[1:] = np.nan_to_num(rim_hi[1:] - rim[1:], nan=1e9) <= cfg["hole_rim_tol_m"]
        ok = np.zeros(n + 1, bool); ok[1:] = ~open_edge; inside = ok[lab] & hole
        hl = np.where(touches_water[np.maximum(lab - 1, 0)], lev, rim[lab])
        water_hole = inside & ~steep & np.isfinite(hl) & (touches_water[np.maximum(lab - 1, 0)] | level_rim[lab])
        wet |= water_hole; level = np.where(water_hole, hl, level)
        rep["holes"] = dict(n=int(n), open_to_edge=int(open_edge.sum()), water_km2=round(float(water_hole.sum()) * res * res / 1e6, 3),
                            steep_void_km2=round(float((inside & steep).sum()) * res * res / 1e6, 3), edge_void_km2=round(float((hole & ~inside).sum()) * res * res / 1e6, 3))
    gz = smooth(Z, g, 1.0 / res)
    zout = np.where(g, Z, np.where(wet, level, np.where(gap, gz, np.nan))).astype(np.float32)
    cls = np.where(g, CJ, np.where(gap & np.isfinite(gz), CLS["ground"], np.where(wet & np.isfinite(level), CLS["water"], 0))).astype(np.uint8)
    cls[g & (cls == 0)] = CLS["ground"]
    # the water levels the layer decided (returns, dropouts, holes): stable evidence for the composite's fallback rule
    # "a prior's ground below water seen nearby is void" (it read the build's own water_z.tif before -- a race)
    write(out / f"water_level{tag}.tif", np.where(cls == CLS["water"], zout, np.nan).astype(np.float32), win, res, epsg, "float32", -9999)
    # void placeholder (the class says not to show it) from the nearest SURFACE cell -- never a bed: a composite's water
    # mask may later call a void cell water and read this z as its level (run 10-13: the 2021 lake outline over the
    # sonar's bed gave the water stage "lakes" at 29-45 m inside Grewingk Lake)
    surf = np.isfinite(zout) & (cls != CLS["bed"])
    zout = np.where(np.isfinite(zout), zout, nearest_fill(np.where(surf, zout, np.nan))).astype(np.float32)
    write(out / f"layer{tag}_z.tif", zout, win, res, epsg, "float32", -9999); write(out / f"layer{tag}_class.tif", cls, win, res, epsg, "uint8", 255)
    a = res * res / 1e6
    rep.update(ground_km2=round(float((cls == 1).sum()) * a, 3), bed_km2=round(float((cls == 3).sum()) * a, 3), ice_km2=round(float((cls == 4).sum()) * a, 3),
               water_returns_km2=round(float(w.sum()) * a, 3), water_km2=round(float((cls == 2).sum()) * a, 3), void_km2=round(float((cls == 0).sum()) * a, 3),
               void_under_vegetation_km2=round(float((~g & ~wet & ~gap & ~noret).sum()) * a, 3), footprint_km2=round(float(F.sum()) * a, 3))
    (out / f"layer{tag}_report.json").write_text(json.dumps(rep, indent=1)); log(json.dumps(rep)); return rep


def preflight(recipe_path):
    """Refuse to start when a source file cannot be READ (not merely found): macOS revokes a terminal's access to a
    removable volume (TCC, 2026-10-23: Powder listed but every open failed 'Operation not permitted' two hours into a
    run).  One byte from each source up front, and the recovery in the message."""
    import os
    r = json.loads(Path(recipe_path).read_text())
    bad = []
    for s_ in (r.get("joint") or r.get("fuse") or {}).get("sources", []):
        for key in ("src", "footprint"):
            p = s_.get(key)
            if not p or not isinstance(p, str) or not os.path.isabs(p):
                continue
            try:
                if os.path.isdir(p):                                 # a units directory (joint_pilot.py --recipe): read one unit file
                    uf = s_.get("unit_file", f"{s_.get('name')}.npz")
                    files = sorted(Path(p).glob("u_*/" + uf))[:1]
                    if not files:                                    # nothing gridded yet (the units stage runs first): the dir must read
                        os.listdir(p); continue
                    p = str(files[0])
                with open(p, "rb") as f:
                    f.read(1)
            except OSError as e:
                bad.append((f"{s_.get('name')} {key}: {p} ({e.strerror})", e.errno == 1, p))
    if bad:
        msg = "cannot read these sources -- nothing started:\n  " + "\n  ".join(b[0] for b in bad)
        tcc = sorted({"/".join(b[2].split("/")[:3]) for b in bad if b[1]})
        if tcc:
            msg += ("\n'Operation not permitted' on a mounted volume: macOS has revoked this terminal's access to it (TCC). Run\n"
                    "  tccutil reset SystemPolicyRemovableVolumes com.apple.Terminal\nthen `ls` " + " ".join(tcc) + " and click Allow, then rerun.")
        sys.exit(msg)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("stage", choices=("fuse", "layer", "all")); ap.add_argument("recipe")
    ap.add_argument("--win", type=float, nargs=4, metavar=("X0", "Y0", "X1", "Y1")); ap.add_argument("--out"); ap.add_argument("--res", type=float); ap.add_argument("--tag", default="")
    ap.add_argument("--dump", action="store_true", help="write points.npz (per-source cells and verdicts) for the review point cloud")
    a = ap.parse_args()
    preflight(a.recipe)
    if a.stage in ("fuse", "all"): fuse(a.recipe, a.win, a.out, a.res, dump=a.dump)
    if a.stage in ("layer", "all"): layer(a.recipe, a.win, a.out, a.res, tag=a.tag)


if __name__ == "__main__":
    main()
