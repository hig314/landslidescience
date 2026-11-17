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
import argparse, csv, json, os, re, sys, time
from pathlib import Path
import numpy as np
from scipy import ndimage

CLS = {"void": 0, "ground": 1, "water": 2, "bed": 3, "ice": 4}
DEFAULTS = dict(res_m=1.0, pool_years={"ground": 5.0, "bed": 30.0, "ice": 0.5}, rates={"ground": 0.01, "bed": 0.02, "ice": 3.0}, tol_m=0.15, tol_low_m=1.0, xc_sigma_m=2.0, xc_minw=1.5,
                over_water_m=50.0, over_water_dry_m=10.0, over_water_tol_m=0.5, bed_over_water_tol_m=0.3, over_water_max_m=30.0, tide_max_m=4.5, stand_flat_m=0.3, stand_frac=0.05, stand_min_cells=10, stand_dz_m=1.0, bed_stand_dz_m=2.0, derived_stand_dz_m=4.0, derived_stand_frac=0.5, derived_stand_slope=0.01, sheet_level_tol_m=0.5, sheet_level_slope=0.0015, low_water_stand_m=1.0, derived_under_sheet_m=6.0, low_water_level_m=-1.87, low_water_edge_m=30.0, rock_near_m=40.0, mesa_level_m=1.0, mesa_level_frac=0.8, mesa_rise_m=1.0, mesa_ring_m=6.0, mesa_ring_frac=0.6, mesa_min_cells=1, mesa_slope=0.05, speck_near_m=300.0, speck_sparse_frac=0.25, derived_ground_gap_m=0.0, derived_ground_dense_frac=0.5, sonar_min_depth_m=1.5, sparse_bed_min_n=5, coarse_factor=4.0, derived_gap_m=0.0, derived_adjust_m=150.0, derived_adjust_coarse=4, derived_fine_m=0.0, derived_fine_frac=0.05, surf_near_m=30.0, speck_cast_m=150.0, derived_ground_bed_dz_m=3.0, derived_ground_bed_top_m=0.0,
                sigma_floor_m=0.03, gap_m=2.0, wet_m=15.0, structure_m=30.0, object_max_m2=3000.0, object_ring_m=6.0, object_ring_frac=0.6, object_dz_m=2.0, steep_deg=20.0, prior_slope_res_m=5.0, hole_rim_tol_m=1.0, grid_wc=0.1, grid_better=0.75,
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
        self.adjust_global_m = d.get("adjust_global_m"); self.adjust_stable_m = d.get("adjust_stable_m")   # see 2b
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


def ground_edits(o, win, res, epsg, cfg, log=print, name=""):
    """A reviewer's GROUND polygons over one lidar source's observations (tools/lidar/water/edits.geojson, kind "ground"; 2026-11-05, Hig, Hoen's Lagoon's inner flat: nearly perfectly flat mud at 0.8 m whose returns
    are classed water at its own level -- "genuinely a little hard to tell whether it's mud or water even when you're standing there"; the lidar height is right, so it is fixed in the classification, as an edit, not by a
    rule).  Inside one, the cell's water returns ARE its ground: where no ground stands they become it (height and count), and the cell carries no water.  Returns the hectares changed."""
    p = Path(cfg.get("edits") or (Path(__file__).resolve().parent.parent / "water" / "edits.geojson"))
    if o is None or "z_w" not in o or not p.exists():
        return 0.0
    from rasterio import features
    from rasterio.transform import from_origin
    from rasterio.warp import transform_geom
    G = []
    for f in json.loads(p.read_text()).get("features", []):
        q = f.get("properties") or {}
        if q.get("kind") == "ground" and not q.get("withdrawn") and f["geometry"]["type"] in ("Polygon", "MultiPolygon"):
            G.append(transform_geom("EPSG:4326", f"EPSG:{epsg}", f["geometry"]))
    if not G:
        return 0.0
    H, W = o["z_w"].shape
    m = features.rasterize([(g, 1) for g in G], out_shape=(H, W), transform=from_origin(win[0], win[3], res, res), fill=0, dtype="uint8") > 0
    k = m & np.isfinite(o["z_w"])
    if not k.any():
        return 0.0
    t = k & ~np.isfinite(o["z_g"])
    o["z_g"][t] = o["z_w"][t]; o["zmin_g"][t] = o["z_w"][t]; o["n_g"][t] = np.maximum(o["n_w"][t], 1.0)
    o["z_w"][k] = np.nan; o["n_w"][k] = 0.0
    ha = float(k.sum()) * res * res / 1e4
    log(f"  ground edits: {name}: {ha:.2f} ha of water returns inside a reviewer's ground polygon are ground ({float(t.sum()) * res * res / 1e4:.2f} ha had no ground of their own)")
    return ha


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
    coarse_ = D > float(cfg.get("coarse_factor", 4.0)) * res
    z = read_to(s.src, win, res, epsg, "average" if D <= res else ("cubic_spline" if coarse_ else "bilinear")) + s.shift_m
    if coarse_:
        # A COARSE grid (2026-10-31, Hig: "square flat plates" off the south coast -- CGOA's 100 m cells at its own coastline):
        # a resampler has no neighbour on the land side of an edge cell, so each comes through as a flat square; and
        # bilinear across 100-500 m cells is a field of kinks. Cubic spline inside, and the ring of source cells with a
        # no-data neighbour is dropped: the fill bridges from the shore to the grid's interior.
        vD = ndimage.binary_erosion(np.isfinite(read_to(s.src, win, D, epsg, "nearest")), np.ones((3, 3), bool), border_value=1)
        f_ = max(int(round(D / res)), 1); k_ = np.kron(vD, np.ones((f_, f_), bool)); vz = np.zeros(z.shape, bool)
        h_, w_ = min(k_.shape[0], z.shape[0]), min(k_.shape[1], z.shape[1]); vz[:h_, :w_] = k_[:h_, :w_]
        z = np.where(vz, z, np.nan); del vD, k_, vz
    z = np.where(s.valid(z), z, np.nan).astype(np.float32)
    # a MARINE SONAR sounding (marine_sonar: true) lies under at least a hull's draft of water at the highest tide (2026-10-29, Hig's review: the
    # 2009 multibeam's swath edges in Seldovia Bay carry 138 blobs ~30 m across at +4.0..+4.8 m -- the sea surface at
    # high water read as bed -- where its own bed and the green lidar's are at -3.4 m). Judged on the source's own
    # cells (the max of those in ours), so a working cell averaging a blob with the bed below goes too.
    if s.d.get("marine_sonar"):
        ceil_ = float(cfg.get("tide_max_m", np.inf)) - float(cfg.get("sonar_min_depth_m", 1.5))
        zx = read_to(s.src, win, res, epsg, "max" if D <= res else "bilinear") + s.shift_m
        hi_ = np.isfinite(z) & (np.nan_to_num(zx, nan=-1e9) > ceil_)
        if hi_.any():
            print(f"  {s.name:14s} sonar ceiling {ceil_:.2f} m: {float(hi_.sum()) * res * res / 1e4:.2f} ha of soundings above it dropped")
        z = np.where(hi_, np.nan, z).astype(np.float32); del zx
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
    sbs = s.d.get("shift_by_survey") or {}                   # a mixed file's per-survey shift (the fill's points_dz_m)
    if sbs: zz = zz + np.array([float(sbs.get(r.get("survey", ""), 0.0)) for r in rows])
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


def _solid_z(obs, srcs, order, shape):
    """First finite solid value among the named sources, in order (all NaN where none of them is in the box: a box of
    open water has no lidar at all, and None here crashed the beach rule on 21 of the bay's 48 boxes, run2)."""
    z = None
    for n in order:
        o = obs.get(n)
        if o is None: continue
        zk = o["z_g"] if "z_g" in o else o["z"]
        z = zk.copy() if z is None else np.where(np.isfinite(z), z, zk)
    return np.full(shape, np.nan, np.float32) if z is None else z


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
    zb = _solid_z(obs, srcs, ([sv] if sv else []) + [n for n in lid if n != sv] + [s.name for s in srcs if s.name != sv and s.name not in lid], chan.shape)
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
    zb = _solid_z(obs, srcs, ([p["survey"]] if p.get("survey") else []) + lid, sea.shape)
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
        if s.kind == "lidar":
            o = obs_lidar(s, win, res, cfg)
            ge_ = ground_edits(o, win, res, epsg, cfg, log, s.name)                  # a reviewer's ground polygons (water/edits.geojson), before anything judges the water
            if ge_: rep.setdefault("ground_edits_ha", {})[s.name] = round(ge_, 2)
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
    if not names:
        # AN EMPTY BOX (2026-11-06, kj_r1c4 and the r0 row: open water beyond every lidar and grid -- the NOS casts and CRM's
        # centres are the fill's points, not the layer's): the layer is written void so the chain can build the sea there
        # from the prior and the points (max() of no dates stopped the stage and the worker moved to the next box)
        log("  no source has a cell in this window: the layer is void (the fill's points make the sea)")
        for name, a, dt, nd in (("joint_z", np.full((H, W), np.nan, np.float32), "float32", -9999), ("joint_n", np.zeros((H, W), np.float32), "float32", -9999),
                                ("joint_src", np.zeros((H, W), np.uint32), "uint32", 0), ("joint_rej", np.zeros((H, W), np.uint32), "uint32", 0),
                                ("joint_dist", np.full((H, W), np.nan, np.float32), "float32", -9999), ("joint_sigma", np.full((H, W), np.nan, np.float32), "float32", -9999),
                                ("joint_nland", np.zeros((H, W), np.uint8), "uint8", 255), ("joint_cls", np.zeros((H, W), np.uint8), "uint8", 255),
                                ("vintage", np.zeros((H, W), np.uint8), "uint8", 255), ("water_z", np.full((H, W), np.nan, np.float32), "float32", -9999)):
            write(out / f"{name}.tif", a, win, res, epsg, dt, nd)
        rep["coverage"] = dict(supported=0.0, void_km2=round(H * W * res * res / 1e6, 3), water_cells_km2=0.0, by_class_km2={}); rep["src_bits"] = {}; rep["seconds"] = round(time.time() - t0)
        (out / "joint_report.json").write_text(json.dumps(rep, indent=1)); log(json.dumps(rep["coverage"])); return rep
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
    # THE ZONE'S DAY (2026-11-06, Hig's review of cm18, the Wosnesenski delta front at 59.55261,-151.39426): a zone is where the surface
    # moves, and what an older survey saw there is no evidence of the sea on the allowed survey's day.  KBay 2023's ground slopes from
    # -0.8 to -2.9 m down the front, dense and dry, over the 2009 multibeam's -4 to -9 m and a 1978 NOS cast line at -6 to -10 m: the
    # delta has built out, and the sheet rule below condemned the 2023 ground as the sea, piecemeal as the flat test flickered along the
    # 3 % slope.  Per cell, the date of the zone's one allowed survey (NaN where pooled); the sheet rule drops beds, water and casts
    # older than it inside the zone.  Outside the zones nothing changes: Taylor Bay's sea sheet over the 1978 casts is pooled ground
    ZD = np.full((H, W), np.nan, np.float32)
    for k, e in enumerate(vint, 1):
        al_ = e.get("allow") if isinstance(e.get("allow"), list) else []
        if len(al_) == 1 and al_[0] in S and np.isfinite(S[al_[0]].date):
            ZD[V == k] = S[al_[0]].date
    def fresh(m):                                            # not older than the zone's day where zoned (see ZD above)
        return ~(np.isfinite(ZD) & (ZD > (S[m].date if np.isfinite(S[m].date) else -np.inf)))
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
                # a surface model years apart from the lidar: only ground that has not changed calibrates it (2026-11-04, Hig: ArcticDEM's edges; 15-27 m of glacier thinning in
                # kj_r4c4's overlap shifted all its ArcticDEM -8.62 m, where stable ground across the bay asks for 0.0)
                if S[n].adjust_stable_m is not None:
                    both &= np.abs(np.nan_to_num(Z[m] - Z[n], nan=1e9)) <= float(S[n].adjust_stable_m)
                if not both.any():
                    continue
                w_ = np.where(both, 1.0 / np.maximum(np.nan_to_num(SG[m], nan=S[m].sigma_m), cfg["sigma_floor_m"]) ** 2, 0.0).astype(np.float32)
                num += w_ * np.nan_to_num(Z[m] - Z[n]); den += w_
            if not (den > 0).any():
                continue
            g = float(num.sum() / den.sum()) if S[n].adjust_global_m is None else float(S[n].adjust_global_m)   # the global weighted offset, or the bay's own
            r2 = np.where(den > 0, (num / np.maximum(den, 1e-12)) ** 2 * den, 0.0).astype(np.float32)
            nc, dc, qc = _blk(num), _blk(den), _blk(r2)
            ns, ds, qs = (ndimage.gaussian_filter(a, sg_c) for a in (nc, dc, qc))
            eps = 1e-4 * float(dc.max())                                      # a weak pull to the global offset beyond the overlap's reach
            F_c = (ns + eps * g) / (ds + eps)
            var_c = np.maximum(qs / np.maximum(ds, 1e-12) - (ns / np.maximum(ds, 1e-12)) ** 2, 0.0)
            s_c = np.sqrt(var_c)
            F = _up(F_c.astype(np.float32)); s_ = _up(s_c.astype(np.float32))
            # 2c. the FINE stage (2026-10-29, Bluff Point: Homer 2018's green-lidar bed in 12 % of cells, 2 points each,
            # stood 1-1.5 m proud of NCEI's 10 m cells between them -- spikes at the survey's margin; the 150 m field
            # cannot fit a residual that changes over tens of metres). What remains at the measured cells after the
            # coarse field is spread over derived_fine_m (normalised Gaussian at full resolution) and applied with a
            # weight that is 1 among the measured cells and fades to 0 a few sigma away: the compilation passes
            # THROUGH sparse measured points instead of standing beside them, and is untouched far from them.
            # ...a CASCADE (2026-10-29, Hig's review of Bluff Point run 2: circular mesas -- NCEI lifted 2-3 m in a 10 m disc
            # round each cluster of Homer 2018's bed, 50-100 m apart, and left at its own level between them): scales
            # halving from derived_adjust_m / 2 down to derived_fine_m, each fitting what the coarser ones left, so the
            # residual between sparse beds is carried at the scale of their spacing and the finest stage adds only what
            # dense measurements resolve. Coarse stages run on a block grid (scale / 4 res per block).
            kf = float(cfg.get("derived_fine_m", 0.0))
            if kf > 0:
                wf = (den > 0).astype(np.float32); fr = max(float(cfg.get("derived_fine_frac", 0.05)), 1e-6)
                mz = np.where(den > 0, num / np.maximum(den, 1e-12), 0.0).astype(np.float32)
                scales = []; sc_ = ka / 2.0
                while sc_ > kf * 1.0001:
                    scales.append(sc_); sc_ /= 2.0
                scales.append(kf)
                Ft = np.zeros((H, W), np.float32); stages = []
                for sc_ in scales:
                    bf = max(int(sc_ / (4 * res)), 1)
                    rres = np.where(den > 0, mz - F - Ft, 0.0).astype(np.float32)
                    if bf > 1:
                        Hp, Wp = -(-H // bf) * bf, -(-W // bf) * bf
                        def _b(a):
                            b = np.zeros((Hp, Wp), np.float32); b[:H, :W] = a
                            return b.reshape(Hp // bf, bf, Wp // bf, bf).mean(axis=(1, 3))
                        nf = ndimage.gaussian_filter(_b(rres * wf), sc_ / (res * bf)); df = ndimage.gaussian_filter(_b(wf), sc_ / (res * bf))
                        Fs = np.where(df > 1e-6, nf / np.maximum(df, 1e-6), 0.0) * np.clip(df / fr, 0.0, 1.0)
                        Fs = ndimage.zoom(Fs, bf, order=1)[:H, :W].astype(np.float32)
                        if Fs.shape != (H, W):
                            pad = np.zeros((H, W), np.float32); pad[:Fs.shape[0], :Fs.shape[1]] = Fs; Fs = pad
                    else:
                        nf = ndimage.gaussian_filter(rres * wf, sc_ / res); df = ndimage.gaussian_filter(wf, sc_ / res)
                        Fs = (np.where(df > 1e-6, nf / np.maximum(df, 1e-6), 0.0) * np.clip(df / fr, 0.0, 1.0)).astype(np.float32)
                    Ft += Fs; stages.append(round(sc_, 1))
                    del rres, nf, df, Fs
                F = (F + Ft).astype(np.float32)
                big = np.abs(Ft) > 0.05
                rep.setdefault("derived_fine", {})[n] = dict(scales_m=stages, applied_km2=round(float(big.sum()) * res * res / 1e6, 3),
                                                            p5_95=[round(float(v), 2) for v in np.percentile(Ft[big], [5, 95])] if big.any() else [0, 0])
                del wf, mz, Ft, big
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
    # ---- 2c. CHANGE BETWEEN TWO TRUSTED SURVEYS, GRADED OUT OVER THE OLDER ONE (2026-11-10, Hig, the Woz delta front, "real change
    # 2008-2023": the 2023 lidar's front at -1.6 m beside the 2009 multibeam at -8.4 m, a 7 m step wherever the joint swapped from one
    # to the other -- a bar above a pool at sea level, the pool no evidence of the bed, the multibeam the next best thing).  For each
    # pair named in cfg change_pairs ({"new", "old", "taper_m"}), and only inside the vintage zones where `new` alone is allowed (where
    # the surface is known to move; elsewhere a lidar's misread sea surface over the multibeam would tilt it), the change new - old is
    # measured where both have a cell (new's ground), smoothed (change_smooth_m); beyond, the older survey takes the change of the
    # nearest measured cell, tapering linearly to zero at taper_m from it.  Changes under change_min_m are survey noise and none.
    rep["change_pairs"] = {}
    for cp_ in (cfg.get("change_pairs") or []):
        nw_, od_ = cp_.get("new"), cp_.get("old")
        if nw_ not in S or od_ not in S or Z.get(nw_) is None or Z.get(od_) is None:
            continue
        zk_ = [k for k, e in enumerate(vint, 1) if isinstance(e.get("allow"), list) and e["allow"] == [nw_]]
        zone_ = np.isin(V, zk_) if zk_ else np.zeros((H, W), bool)
        both_ = zone_ & np.isfinite(Z[nw_]) & np.isfinite(Z[od_])
        if both_.sum() < int(cp_.get("min_cells", 50)):
            rep["change_pairs"][f"{nw_}-{od_}"] = dict(cells=int(both_.sum()), applied=False); continue
        # ...and only on GENTLE ground (2026-11-15, Hig's review, Tutka Bay 59.43498,-151.33746, "two different versions of the bathymetry
        # with a hard margin between them": on a steep rock shore the lidar's ground and the multibeam differ by tens of metres with no
        # change at all -- r4c3: 3,629 cells, median 18 m, p90 41 m, carried 120 m out over 150 ha of multibeam).  A horizontal misfit of
        # a few metres is a small height only where the ground is gentle, so a cell counts where the older surface's slope (the newer's
        # where the older has no neighbour to take one from) is change_slope_max or less.  Worked on the overlap's bounding box only.
        rr_, cc_ = np.nonzero(both_.any(1))[0], np.nonzero(both_.any(0))[0]
        r0_, r1_, c0_, c1_ = max(rr_[0] - 2, 0), min(rr_[-1] + 3, H), max(cc_[0] - 2, 0), min(cc_[-1] + 3, W)
        def _slope(z):
            gy, gx = np.gradient(z[r0_:r1_, c0_:c1_].astype(np.float32), res); return np.hypot(gx, gy)
        slo_, sln_ = _slope(Z[od_]), _slope(Z[nw_])
        sl_ = np.where(np.isfinite(slo_), slo_, sln_); del slo_, sln_
        steep_ = np.zeros((H, W), bool); steep_[r0_:r1_, c0_:c1_] = ~(sl_ <= float(cp_.get("change_slope_max", 0.6))); del sl_
        n_all_ = int(both_.sum()); both_ &= ~steep_; del steep_
        # ...and NOT THE NEWER SURVEY'S SEA SURFACE (2026-11-16, Hig's review of s2, the mouth of Jakolof Bay 59.46848,-151.53619: "a sloped
        # surface with a sharp dropoff in the shoreward direction").  KBay 2023 flew there at a water level of -2.9 m; "ground" returns
        # far from its classified water (beyond the per-cell and surf_near_m rules) stood at that level over the multibeam at -8 to -12,
        # a flat and so "gentle", "coherent" change of +6 m, and the multibeam was lifted to +3 m out to 90 m.  A newer cell within
        # over_water_tol_m of the level of the survey's nearest own water return (within change_surface_near_m), over an older bed
        # deeper by change_min_m and more, is the water's surface, not a change of the bed.
        wl_ = None
        o_ = obs.get(nw_) or {}
        if o_.get("z_w") is not None and o_.get("n_w") is not None:
            hw_ = np.isfinite(o_["z_w"]) & (o_["n_w"] > 0)
            if hw_.any():
                dw_, (wy_, wx_) = ndimage.distance_transform_edt(~hw_, sampling=res, return_indices=True)
                wl_ = np.where(dw_ <= float(cp_.get("change_surface_near_m", 500.0)), o_["z_w"][wy_, wx_], np.nan).astype(np.float32)
                del dw_, wy_, wx_
            del hw_
        # (OFF by default, change_surface_cells: at the Woz front it took 33,254 cells of the real bar, which stands at the water's edge, and
        # cut the change from 70.5 to 40.9 ha; the flat-sheet test below is what catches a sea surface.  wl_ still caps the graded change.)
        if wl_ is not None and cp_.get("change_surface_cells", False):
            sf_ = both_ & np.isfinite(wl_) & (np.abs(Z[nw_] - wl_) <= cfg["over_water_tol_m"]) & (Z[od_] < Z[nw_] - float(cp_.get("change_min_m", 0.5)))
            if sf_.any():
                log(f"  change {nw_} - {od_}: {int(sf_.sum())} cells of {nw_} at its own water level over a deeper {od_} are its sea surface, no change")
                rep.setdefault("change_surface_cells", {})[f"{nw_}-{od_}"] = int(sf_.sum())
            both_ &= ~sf_; del sf_
        if both_.sum() < int(cp_.get("min_cells", 50)):
            rep["change_pairs"][f"{nw_}-{od_}"] = dict(cells=n_all_, gentle=int(both_.sum()), applied=False, note="too few cells on gentle ground")
            log(f"  change {nw_} - {od_} inside {nw_}'s zones: {n_all_} cells measured, {int(both_.sum())} on ground gentler than {float(cp_.get('change_slope_max', 0.6)):g}: none applied")
            continue
        d_ = np.where(both_, Z[nw_] - Z[od_], 0.0).astype(np.float32)
        # ...only where the change is REAL: a connected patch of the overlap (joined over 10 m) whose MEDIAN change is change_min_m or more,
        # on min_cells (2026-11-10, selhead: scattered disagreements of 0.08-1.28 m about a median 0.38 between multibeam lines were
        # graded out as streaks; the Woz front's patch is +5.65 m)
        lp_, np_ = ndimage.label(ndimage.binary_dilation(both_, iterations=max(int(round(5.0 / res)), 1)) , structure=np.ones((3, 3), bool))
        lp_ = np.where(both_, lp_, 0)
        cnt_ = np.bincount(lp_.ravel(), minlength=np_ + 1)
        med_ = np.asarray(ndimage.median(d_, lp_, np.arange(1, np_ + 1))) if np_ else np.zeros(0)
        okp_ = np.zeros(np_ + 1, bool)
        okp_[1:] = (cnt_[1:] >= int(cp_.get("min_cells", 50))) & (np.abs(med_) >= float(cp_.get("change_min_m", 0.5)))
        # ...and COHERENT (2026-11-15, the same review): one event moves a patch by about one amount; a patch whose cells scatter about
        # their median by more than max(1 m, half the median) (NMAD) is two surfaces that do not describe the same ground
        if np_:
            mf_ = np.concatenate([[0.0], med_]).astype(np.float32)
            nmad_ = 1.4826 * np.asarray(ndimage.median(np.abs(d_ - mf_[lp_]), lp_, np.arange(1, np_ + 1)))
            okp_[1:] &= nmad_ <= np.maximum(float(cp_.get("change_nmad_m", 1.0)), float(cp_.get("change_nmad_frac", 0.5)) * np.abs(med_))
        # ...and NOT TENS OF METRES (2026-11-16, crop c16_tutka): 234 cells on a "gentle", "coherent" patch beside Tutka's rock shore read a
        # median change of 31 m (p90 82 m) and moved 14.85 ha of the multibeam; the Woz front's real change is 0.3-6.8 m (p10-p90).  A patch
        # whose median exceeds change_max_m is two surveys that do not describe the same ground, not a change between them.
        if np_:
            okp_[1:] &= np.abs(med_) <= float(cp_.get("change_max_m", 12.0))
            del mf_, nmad_
        # ...and NOT A SHEET OF WATER (2026-11-16, Hig's review of s2, the mouth of Jakolof Bay): three patches 200 m apart, each dead level
        # at +0.08 +- 0.05 m over the multibeam at -5 to -8 -- the sea at another day's stand (KBay 2023 flew it at -2.9 too), a "change" of
        # +6.6 m that lifted 11.5 ha of the multibeam 5-6 m.  A patch whose newer surface spans less than change_flat_m (p10-p90) while
        # the older one under it spans change_flat_old_m or more is a water surface: no change.  (A bar or a delta front has relief.)
        if np_ and okp_[1:].any():
            ix_ = np.nonzero(okp_[1:])[0] + 1
            sp_n = ndimage.labeled_comprehension(Z[nw_], lp_, ix_, lambda v: float(np.percentile(v, 90) - np.percentile(v, 10)), np.float64, np.nan)
            sp_o = ndimage.labeled_comprehension(Z[od_], lp_, ix_, lambda v: float(np.percentile(v, 90) - np.percentile(v, 10)), np.float64, np.nan)
            flat_ = (np.asarray(sp_n) < float(cp_.get("change_flat_m", 0.15))) & (np.asarray(sp_o) >= float(cp_.get("change_flat_old_m", 0.5)))
            if flat_.any():
                okp_[ix_[flat_]] = False
                log(f"  change {nw_} - {od_}: {int(flat_.sum())} patches level within {float(cp_.get('change_flat_m', 0.15)):g} m over a varied {od_} are a water surface, no change")
            del ix_, sp_n, sp_o, flat_
        real_ = okp_[lp_] & both_; del lp_, cnt_, med_
        if not real_.any():
            rep["change_pairs"][f"{nw_}-{od_}"] = dict(cells=int(both_.sum()), applied=False, note="no coherent patch with a median change >= change_min_m")
            log(f"  change {nw_} - {od_} inside {nw_}'s zones: {int(both_.sum())} cells measured, no coherent patch changed by >= {float(cp_.get('change_min_m', 0.5)):g} m (median): none applied")
            continue
        both_ = real_; d_ = np.where(both_, d_, 0.0).astype(np.float32)
        # ...a SMOOTH field, not the nearest cell's (2026-11-10, selhead: 2,105 overlap cells, median 0.38 m, and the few over 0.5 m
        # carried out by nearest neighbour over 11 ha drew diagonal streaks): the change averaged over change_smooth_m (normalised
        # Gaussian of the overlap), so a field is only as strong as the overlap around it agrees; under change_min_m it is none
        tm_ = float(cp_.get("taper_m", 120.0)); sg_ = float(cp_.get("change_smooth_m", tm_ / 3.0)) / res
        num_ = ndimage.gaussian_filter(d_, sg_); den_ = ndimage.gaussian_filter(both_.astype(np.float32), sg_)
        dist_ = ndimage.distance_transform_edt(~both_, sampling=res)
        ds_ = np.where(den_ > 1e-4, num_ / np.maximum(den_, 1e-4), 0.0).astype(np.float32)
        # (2026-11-15: no hard cut and no kink -- the field ramps in between half of change_min_m and change_min_m, and the taper is
        # a smooth step, level at both ends, so the older survey's surface meets its unadjusted self without an edge)
        cm_ = float(cp_.get("change_min_m", 0.5))
        ds_ *= np.clip((np.abs(ds_) - 0.5 * cm_) / (0.5 * cm_), 0.0, 1.0)
        tap_ = np.clip(1.0 - dist_ / tm_, 0.0, 1.0); tap_ = tap_ * tap_ * (3.0 - 2.0 * tap_)
        F_ = np.where(both_, d_, ds_ * tap_).astype(np.float32)          # in the overlap the measured change itself (old = new there)
        # ...and NEVER ABOVE THE WATER THE NEWER SURVEY SAW (2026-11-16, the same review): beyond the overlap the newer survey has no
        # ground, so at its flight the bed there lay under its water (or out of its sight); a raised older bed stops change_below_level_m
        # under the level of the newer survey's nearest water return (never below where it already was).
        if wl_ is not None:
            cap_ = ~both_ & (F_ > 0) & np.isfinite(wl_) & np.isfinite(Z[od_])
            lim_ = np.maximum(Z[od_], wl_ - float(cp_.get("change_below_level_m", 0.1)))
            cut_ = cap_ & (Z[od_] + F_ > lim_)
            if cut_.any():
                F_ = np.where(cut_, lim_ - Z[od_], F_).astype(np.float32)
                log(f"  change {nw_} - {od_}: {float(cut_.sum()) * res * res / 1e4:.2f} ha of the graded change capped under {nw_}'s water level")
                rep.setdefault("change_capped_ha", {})[f"{nw_}-{od_}"] = round(float(cut_.sum()) * res * res / 1e4, 2)
            del cap_, lim_, cut_
        hit_ = np.isfinite(Z[od_]) & (F_ != 0)
        Z[od_] = np.where(hit_, Z[od_] + F_, Z[od_]).astype(np.float32)
        rep["change_pairs"][f"{nw_}-{od_}"] = dict(cells=n_all_, gentle_coherent=int(both_.sum()), change_p10_50_90=np.round(np.percentile(d_[both_], [10, 50, 90]), 2).tolist(),
                                                   adjusted_ha=round(float(hit_.sum()) * res * res / 1e4, 2), beyond_ha=round(float((hit_ & ~both_).sum()) * res * res / 1e4, 2))
        log(f"  change {nw_} - {od_} inside {nw_}'s zones: {int(both_.sum())} cells measured, p10/50/90 {rep['change_pairs'][f'{nw_}-{od_}']['change_p10_50_90']} m; "
            f"{od_} adjusted on {rep['change_pairs'][f'{nw_}-{od_}']['adjusted_ha']} ha ({rep['change_pairs'][f'{nw_}-{od_}']['beyond_ha']} beyond the overlap, tapering to 0 at {float(cp_.get('taper_m', 120.0)):g} m)")
        del d_, num_, den_, ds_, dist_, tap_, F_, hit_, both_, zone_
    # ---- 2d. A DERIVED GROUND WHERE A LIDAR SAW WATER (2026-11-10, Hig, 59.55909,-151.27770: "we should not substitute ArcticDEM or
    # 3DEP where we know from lidar there's water - whatever data they have is wrong").  ArcticDEM in r7c4 stood alone on 815 ha of cells
    # holding a lidar's water returns, within 0.03 m of that water (median): it is the water surface, read as ground.  A derived ground
    # source (ArcticDEM, Chugachmiut's DTM) yields in every cell a lidar saw water in, and inside that water closed over
    # derived_water_close_m.  (3DEP's prior is withdrawn wherever a lidar flew already: layer_flown.)
    dwc_ = float(cfg.get("derived_water_close_m", 10.0)); rep["derived_in_lidar_water_cells"] = {}
    if dwc_ >= 0 and any(S[n].derived and S[n].cls == "ground" for n in names):
        # ...only where it stands AT or above that water (within derived_water_tol_m under it): ArcticDEM in r7c4 sat on the water's
        # level, the water surface itself; 3DEP across the Grewingk Creek delta's 2021 gap (59.65371,-151.18883) stands 0.1-0.5 m under
        # the 2021 water at +0.6 m, a bed seen at a lower tide and the only one there (Hig: "something pretty smooth").
        wz_ = np.full((H, W), np.inf, np.float32)
        for m in names:
            if S[m].kind == "lidar" and obs.get(m) is not None and "z_w" in obs[m]:
                wz_ = np.fmin(wz_, np.where(np.isfinite(obs[m]["z_w"]) & (obs[m]["n_w"] > 0), obs[m]["z_w"], np.inf))
        lw_ = np.isfinite(wz_)
        if lw_.any():
            ci_ = int(round(dwc_ / res / 2))
            if ci_ > 0:
                lc_ = ndimage.binary_closing(lw_, structure=np.ones((3, 3), bool), iterations=ci_) & ~lw_
                if lc_.any():
                    _, (iy_, ix_) = ndimage.distance_transform_edt(~lw_, return_indices=True)
                    wz_ = np.where(lc_, wz_[iy_, ix_], wz_); del iy_, ix_
                lw_ = lw_ | lc_
            wtol_ = float(cfg.get("derived_water_tol_m", 0.3))
            for n in names:
                if S[n].derived and S[n].cls == "ground":
                    dr_ = allow[n] & np.isfinite(Z[n]) & lw_ & (Z[n] >= np.where(lw_, wz_, np.inf) - wtol_)
                    rep["derived_in_lidar_water_cells"][n] = int(dr_.sum())
                    if dr_.any():
                        allow[n] = allow[n] & ~dr_
                        log(f"  {n:14s} derived ground where a lidar saw water (closed over {dwc_:g} m): {float(dr_.sum()) * res * res / 1e4:.2f} ha dropped")
        del lw_, wz_
    # ---- 3. epoch: an older source yields wherever a source newer by > pool_years has a local surface
    xs = cfg["xc_sigma_m"] / res; minw = cfg["xc_minw"]
    # a lidar's SMALL DEEP PATCH above the tide's reach goes before anything is judged by date (2026-11-10, Hig,
    # 59.70416,-151.42943: pits in the 2008 lidar's ground in an E/W band -- Homer 2019 (DGGS) returns at -90 to -775 m
    # in 300-400 m upland, one or two side by side.  Each superseded Kenai 2008 round it by date (a void cross), its
    # own-surface guard spared it (a lone return has no own surface; a pair vouches for itself), and the fill dug the
    # cross down to it: 1,223 pits in r6c1-r7c3).  Judged against every other source's surface, supersession not yet
    # applied; only where that surface stands above lone_above_m, so no sea surface of another flight is the judge.
    lone_low = float(cfg.get("lone_low_m", 15.0)); lone_max = int(cfg.get("lone_max_cells", 4)); lone_above = float(cfg.get("lone_above_m", 6.0))
    rep["lone_deep_cells"] = {}
    for n in names:
        if S[n].kind != "lidar" or S[n].cls == "bed":
            continue
        zo_ = np.zeros((H, W), np.float32); wo_ = np.zeros((H, W), np.float32)
        for m in names:
            if m == n or S[m].cls == "bed":
                continue
            w_ = np.where(allow[m] & np.isfinite(Z[m]), np.minimum(N[m], 5.0) if S[m].kind == "lidar" else cfg["grid_wc"] * N[m], 0.0).astype(np.float32)
            zo_ += np.where(w_ > 0, Z[m], 0).astype(np.float32) * w_; wo_ += w_
        if not (wo_ > 0).any():
            continue
        Ro_, _ = local_surface(np.where(wo_ > 0, zo_ / np.maximum(wo_, 1e-9), np.nan), wo_, 2 * xs, minw)
        deep_ = allow[n] & np.isfinite(Z[n]) & np.isfinite(Ro_) & (Ro_ > lone_above) & (Z[n] < Ro_ - lone_low)
        if deep_.any():
            lab_, nl_ = ndimage.label(deep_, structure=np.ones((3, 3), bool))
            small_ = np.zeros(nl_ + 1, bool); small_[1:] = np.bincount(lab_.ravel(), minlength=nl_ + 1)[1:] <= lone_max
            drop_ = deep_ & small_[lab_]
            allow[n] = allow[n] & ~drop_
            rep["lone_deep_cells"][n] = int(drop_.sum())
            if drop_.any():
                log(f"  {n:14s} small deep patches above the tide's reach (<= {lone_max} cells, > {lone_low:g} m under the others): {int(drop_.sum())} cells dropped, by p10/50/90 {np.round(np.percentile((Ro_ - Z[n])[drop_], [10, 50, 90]), 1).tolist()} m")
        del zo_, wo_, Ro_
    # A DERIVED GROUND OVER A DEEP BED (2026-11-10, Hig's cm18 review, Seldovia 59.42358,-151.87706: "an artifact" -- a 30 m strip
    # sloping 0 to -0.9 m off the beach, then a 6 m drop to the 2009 multibeam).  Chugachmiut's DTM (a grid with no points, one value
    # a cell on land and sea alike) interpolated across the water there, pooled with NCEI's -4.6..-6.4 m under it.  A derived ground
    # surface has nothing that saw the ground: below derived_ground_bed_top_m, where a bed in the same cell lies beyond the lowest
    # tide (low_water_level_m - 1) and more than derived_ground_bed_dz_m under it, it yields to the bed.  The shallowest bed judges.
    dgb_dz = float(cfg.get("derived_ground_bed_dz_m", 0.0)); rep["derived_ground_over_bed_cells"] = {}
    if dgb_dz > 0:
        dgb_top = float(cfg.get("derived_ground_bed_top_m", 0.0)); dgb_below = float(cfg.get("low_water_level_m", -1.87)) - 1.0
        zb_ = np.full((H, W), -np.inf, np.float32)
        for m in names:
            if S[m].cls == "bed":
                zb_ = np.fmax(zb_, np.where(allow[m] & np.isfinite(Z[m]), Z[m], -np.inf))
        for n in names:
            if not (S[n].derived and S[n].cls == "ground"):
                continue
            drop_ = allow[n] & np.isfinite(Z[n]) & (Z[n] <= dgb_top) & np.isfinite(zb_) & (zb_ < dgb_below) & (zb_ < Z[n] - dgb_dz)
            rep["derived_ground_over_bed_cells"][n] = int(drop_.sum())
            if drop_.any():
                allow[n] = allow[n] & ~drop_
                log(f"  {n:14s} derived ground below {dgb_top:g} m over a bed beyond the lowest tide, > {dgb_dz:g} m under it: {float(drop_.sum()) * res * res / 1e4:.2f} ha dropped, "
                    f"by p10/50/90 {np.round(np.percentile((Z[n] - zb_)[drop_], [10, 50, 90]), 1).tolist()} m")
        del zb_
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
            # a DERIVED product is no newer observation of a measured survey's ground (2026-11-05, Hig, 59.68841,-151.34924: "we're not successfully overriding the 3DEP or ArcticDEM with the 2008 lidar"): ArcticDEM, dated 2016,
            # superseded the 2008 lidar by its date wherever it had cells, then yielded to that same lidar as a derived product (below) -- both gone, the cell void, and the composite took 3DEP there: a patchwork of the
            # lidar (where ArcticDEM had no cell), ArcticDEM and 3DEP.  It supersedes only other derived products
            if S[m].derived and not S[n].derived:
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
                bed_any |= ndimage.binary_dilation(allow[m] & fresh(m) & np.isfinite(Z[m]), iterations=max(1, int(np.ceil(2 * xs))))
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
    # ...and the same sheet over a MEASURED BED standing more than bed_stand_dz_m below it, with no water returns of
    # anyone's needed (2026-10-29, Hig's review of the bay crops: Homer 2019's "ground" at -0.57 m over 11 m of water
    # off Bluff Point, beside Homer 2018's green-lidar bed -- a noisy patch of the two; KBay 2023's at -0.04 m over the
    # 2009 multibeam's -14 m in Seldovia Bay, one cell of which survived as a pock): a bed survey saw water over those
    # cells, so ground metres above it on a flat sheet within the tide's reach is the sea that day.
    # (NOT a sheet among the survey's OWN water returns at its level with no measured bed under it: tried the same day
    # for Hoen's Lagoon, and Hig: its inner part IS a horizontal mud flat at 0.8 m -- a wet flat carries water returns at
    # its own level too.  Seldovia Lagoon's surface at 2.28 m, with the same signature, has the green lidar's bed under
    # it and goes by the rule above: the measured bed is what tells the water from the mud.)
    # ...and a SPECK too small to be judged as a sheet (under stand_min_cells), most of it over the measured bed, is the
    # sea when it stands at the stand (within over_water_tol_m) of a sheet of the same survey found above within
    # speck_near_m: a rock top over the bed keeps its returns, a stray return of the sea surface does not.
    stand = {}
    zwmin = np.full((H, W), np.inf, np.float32)
    for m in names:
        if "z_w" in obs[m]:
            zwmin = np.fmin(zwmin, np.where(np.isfinite(obs[m]["z_w"]) & allow[m] & fresh(m), obs[m]["z_w"], np.inf))
    zbmin = np.full((H, W), np.inf, np.float32)
    for m in names:
        if S[m].cls == "bed" and not S[m].derived:
            zbmin = np.fmin(zbmin, np.where(np.isfinite(Z[m]) & allow[m] & fresh(m), Z[m], np.inf))
    zbmax = np.full((H, W), -np.inf, np.float32)             # the SHALLOWEST measured bed, for the mesa rule below
    for m in names:
        if S[m].cls == "bed" and not S[m].derived:
            zbmax = np.fmax(zbmax, np.where(np.isfinite(Z[m]) & allow[m] & fresh(m), Z[m], -np.inf))
    # the SHALLOWEST of the derived beds (the compilations: CUDEM, NCEI's 1/3", OCM), for the derived-bed sheet rule below
    zdmax = np.full((H, W), -np.inf, np.float32)
    for m in names:
        if S[m].cls == "bed" and S[m].derived:
            zdmax = np.fmax(zdmax, np.where(np.isfinite(Z[m]) & allow[m], Z[m], -np.inf))
    zbpmin = np.full((H, W), np.inf, np.float32)             # the sparse measured beds alone (soundings)
    for m in names:
        if S[m].kind == "points" and S[m].cls == "bed" and not S[m].derived:
            zbpmin = np.fmin(zbpmin, np.where(np.isfinite(Z[m]) & allow[m] & fresh(m), Z[m], np.inf))
    # ...and the fill's points files (cfg sheet_points: the NOS soundings, CRM's centres, 2026-10-31), evidence for the sheet
    # rule alone, never cells of the layer: with the compilations gone from the layer the KBay lidar's sea surface over Taylor
    # Bay (2.9 m that day) stood as ground with nothing measured under it (plates_t fix2 pass 6)
    nsp_ = nst_ = 0
    yr_ = lambda t: max((float(y) for y in re.findall(r"(?<!\d)(19\d\d|20\d\d)(?!\d)", t or "")), default=None)
    # ...and every cast with its year, for the SPECK / FLOATING rules' reach (hc_, below): the shallowest cast within speck_cast_m of a
    # cell, judged by the zone's day AT THAT CELL -- a cast outside the zone, 50 m off the Woz delta front, is older than the front's
    # survey and no evidence against it (2026-11-06 woz_t: the first cut read it through the zone's edge and condemned 11 ha of the
    # 2023 front the zone's-day rule had just held)
    cast_z = np.full((H, W), -np.inf, np.float32); cast_y = np.full((H, W), -np.inf, np.float32); cast_sg = float(cfg.get("cast_sigma_max_m", 5.0))
    for pp_ in (cfg.get("sheet_points") or []):
        fy_ = yr_(os.path.basename(pp_)); cache_ = {}
        for r_ in csv.DictReader(open(pp_)):
            if r_.get("bound") or r_.get("z") in (None, ""):
                continue
            c_ = int((float(r_["x"]) - win[0]) // res); rr_ = int((win[3] - float(r_["y"])) // res)
            if 0 <= rr_ < H and 0 <= c_ < W:
                sv_ = r_.get("survey") or ""
                if sv_ not in cache_: cache_[sv_] = yr_(sv_) if yr_(sv_) is not None else fy_
                py_ = cache_[sv_]
                zz_ = float(r_["z"])
                try: sg_ = float(r_.get("sigma") or 1.0)
                except ValueError: sg_ = 1.0
                if sg_ <= cast_sg and zz_ > cast_z[rr_, c_]:   # a measured cast (a compilation's centre at sigma 10 is not one)
                    cast_z[rr_, c_] = zz_; cast_y[rr_, c_] = py_ if py_ is not None else -np.inf
                if np.isfinite(ZD[rr_, c_]):                   # in a zone: the point's year from its survey's name, else its file's; undated is old
                    if py_ is None or py_ + 1.0 < ZD[rr_, c_]:
                        nst_ += 1; continue
                zbpmin[rr_, c_] = min(zbpmin[rr_, c_], zz_); nsp_ += 1
    if nsp_ or nst_:
        log(f"  sheet rule: {nsp_} points from the fill's files as a sparse measured bed; {nst_} older than the zone's survey where zoned, no evidence there")
    if np.isfinite(zwmin).any() or np.isfinite(zbmin).any() or np.isfinite(zbpmin).any() or any("z_w" in obs[n] for n in names):
        tm_ = float(cfg.get("tide_max_m", np.inf)); smc = float(cfg["stand_min_cells"]); tol_ = float(cfg["over_water_tol_m"])
        # the SHALLOWEST cast within speck_cast_m of each cell (a square, separable), for the speck / floating rules below: -inf =
        # none.  Zone-aware: on a zoned cell only casts not older than that zone's day count (one filter per distinct day).
        # ...except under a cell of a LONE RETURN (n_g <= float_sparse_n): a zone protects a surveyed surface (the Woz front, 40
        # returns a cell), not a stray of the sea surface that happens to be the newest lidar's only return there (Anchor Point:
        # Kenai 2008's strays made their own beach zone, and inside it the 1977-84 casts were "older" and no evidence)
        rc_ = int(round(float(cfg.get("speck_cast_m", 0.0)) / res)); hc_ = hc_all = None
        if rc_ > 0 and np.isfinite(cast_z).any():
            hc_all = ndimage.maximum_filter(cast_z, size=2 * rc_ + 1); hc_ = hc_all
            for d_ in np.unique(ZD[np.isfinite(ZD)]):
                hz_d = np.where(cast_y + 1.0 >= d_, cast_z, -np.inf).astype(np.float32)
                hc_ = np.where(ZD == d_, ndimage.maximum_filter(hz_d, size=2 * rc_ + 1), hc_)
        del cast_z, cast_y
        # GROUND IN THE MAPPED LOW-TIDE WATER (2026-11-09, Hig: "Woz does have several high rocks -- these should be shown in the rocks and
        # shorelines data.  Anything not in these is not a real protrusion"; Anchor Point, 59.7287-59.7327,-151.847..-151.851: Kenai 2008's
        # 'ground' at +1..+2 m, a 1 km tongue into the water Hig mapped at low tide, rough, so no sheet rule took it -- "a large artifact").
        # A ground cell inside the low-tide water polygon, more than low_water_stand_m above its level, beyond low_water_edge_m of its edge
        # and rock_near_m of every mapped rock and island, is the sea.
        lw_area = None
        sea_free = np.full((H, W), np.nan, np.float32)
        lwp_ = cfg.get("low_water", str(Path(__file__).resolve().parent.parent / "water" / "tide_low_water.geojson"))
        if float(cfg.get("low_water_stand_m", 0.0)) > 0 and lwp_ and os.path.exists(lwp_) and not os.environ.get("JOINT_LW_OFF"):
            from rasterio.features import rasterize
            from rasterio.transform import from_origin as _fo
            Tw_ = _fo(win[0], win[3], res, res)
            gj_ = json.load(open(lwp_))["features"]
            inw_ = rasterize([(f_["geometry"], 1) for f_ in gj_], out_shape=(H, W), transform=Tw_, fill=0, dtype="uint8").astype(bool)
            if inw_.any():
                ed_ = int(round(float(cfg.get("low_water_edge_m", 30.0)) / res))
                lw_area = ndimage.binary_erosion(inw_, iterations=max(ed_, 1)) if ed_ > 0 else inw_
                prot_ = np.zeros((H, W), bool)
                rp_ = cfg.get("rocks", str(Path(__file__).resolve().parent.parent / "water" / "rocks.geojson"))
                if rp_ and os.path.exists(rp_):
                    for f_ in json.load(open(rp_))["features"]:
                        if (f_["properties"].get("type") or "") == "Deep":
                            continue
                        x_, y_ = f_["geometry"]["coordinates"][:2]; c_ = int((x_ - win[0]) // res); r_ = int((win[3] - y_) // res)
                        if 0 <= r_ < H and 0 <= c_ < W: prot_[r_, c_] = True
                ip_ = Path(lwp_).parent / "tide_islands.geojson"
                if ip_.exists():
                    prot_ |= rasterize([(f_["geometry"], 1) for f_ in json.load(open(ip_))["features"]], out_shape=(H, W), transform=Tw_, fill=0, dtype="uint8").astype(bool)
                if prot_.any():
                    prot_ = ndimage.distance_transform_edt(~prot_, sampling=res) <= float(cfg.get("rock_near_m", 40.0))
                    lw_area &= ~prot_
                del inw_, prot_
                if not lw_area.any(): lw_area = None
        lw_lev = float(cfg.get("low_water_level_m", -1.87)) + float(cfg.get("low_water_stand_m", 0.0))
        def _tilted_pieces(mask_, Zn_):
            """Pieces (8-connected) of mask_ whose plane slopes more than sheet_level_slope: the sea is level, a tidal flat slopes
            (2026-11-09, Woz: KBay 2023's flat at 0.37-0.55 %, 40 returns a cell)."""
            sls_ = 0.0 if os.environ.get("JOINT_AB_OFF") else float(cfg.get("sheet_level_slope", 0.0))
            if sls_ <= 0 or not mask_.any():
                return np.zeros(mask_.shape, bool)
            l2_, n2_ = ndimage.label(mask_, structure=np.ones((3, 3), bool))
            rr_, cc_ = np.nonzero(l2_ > 0); li_ = l2_[rr_, cc_]; xk_ = cc_ * res / 1000.0; yk_ = rr_ * res / 1000.0; zk_ = Zn_[rr_, cc_].astype(np.float64)
            bc_ = lambda v_: np.bincount(li_, weights=v_, minlength=n2_ + 1)
            N0 = bc_(np.ones_like(xk_)); Sx, Sy, Sz = bc_(xk_), bc_(yk_), bc_(zk_)
            Nn = np.maximum(N0, 1); cxx = bc_(xk_ * xk_) - Sx * Sx / Nn; cxy = bc_(xk_ * yk_) - Sx * Sy / Nn; cyy = bc_(yk_ * yk_) - Sy * Sy / Nn
            cxz = bc_(xk_ * zk_) - Sx * Sz / Nn; cyz = bc_(yk_ * zk_) - Sy * Sz / Nn
            det_ = cxx * cyy - cxy * cxy; ok_ = det_ > 1e-12; dd_ = np.where(ok_, det_, 1.0)
            slope_ = np.hypot(np.where(ok_, (cyy * cxz - cxy * cyz) / dd_, 0.0), np.where(ok_, (cxx * cyz - cxy * cxz) / dd_, 0.0)) / 1000.0
            rise_ = slope_ * 4000.0 * np.sqrt((cxx + cyy) / Nn)                       # the plane's rise across the piece (+-2 sd of its extent), m
            if os.environ.get("JOINT_TILT_DEBUG"):
                for j_ in np.argsort(N0)[::-1][:6]:
                    if j_ > 0: log(f"    tilt dbg piece {j_}: {N0[j_] * res * res / 1e4:.1f} ha slope {slope_[j_] * 100:.3f} % rise {rise_[j_]:.2f} m ok {ok_[j_]}")
            t_ = ok_ & (N0 * res * res >= float(cfg.get("sheet_tilt_min_m2", 1e4))) & (slope_ > sls_) & (rise_ >= float(cfg.get("sheet_tilt_rise_m", 1.0))); t_[0] = False   # noise on a small piece is no slope
            return t_[l2_]
        for n in names:
            # ...and a DTM with no point cloud is judged the same way (2026-11-03, Hig: a coastal flat dropping abruptly to the bathymetry at
            # 59.20377,-151.46453: Chugachmiut's published DTM carries the sea at -0.8..+0.3 m as ground over 163 ha, with casts of -8 and -13 m under it)
            if not (S[n].kind == "lidar" or (S[n].kind == "grid" and S[n].cls == "ground")) or S[n].cls == "bed":
                continue
            zf_ = np.isfinite(Z[n]) & allow[n]
            mx_ = ndimage.maximum_filter(np.where(zf_, Z[n], -1e9), 5); mn_ = ndimage.minimum_filter(np.where(zf_, Z[n], 1e9), 5)
            # every flat sheet; the SEA rules below take only those within the tide's reach (<= tide_max_m), the sparse-bed
            # rule any height (a lake's surface sheet over its soundings, 2026-10-31: Upper Hazel Lake at 528 m)
            flat_ = zf_ & ((mx_ - mn_) <= float(cfg["stand_flat_m"]))
            del mx_, mn_
            if not flat_.any():
                continue
            lab_, nl_ = ndimage.label(flat_, structure=np.ones((3, 3), bool))
            cnt_ = np.bincount(lab_.ravel(), minlength=nl_ + 1)
            cnt = lambda m_: np.bincount(lab_.ravel(), weights=m_.ravel(), minlength=nl_ + 1)
            tide_ = np.zeros(nl_ + 1, bool); tide_[1:] = np.asarray(ndimage.maximum(Z[n], lab_, np.arange(1, nl_ + 1))) <= tm_
            und_n = cnt(flat_ & np.isfinite(zwmin) & (zwmin < Z[n] - float(cfg["stand_dz_m"])))
            bad_w = tide_ & (und_n >= smc) & (und_n >= float(cfg["stand_frac"]) * cnt_)
            ub_n = cnt(flat_ & np.isfinite(zbmin) & (zbmin < Z[n] - float(cfg["bed_stand_dz_m"])))
            bad_b = tide_ & (ub_n >= smc) & (ub_n >= float(cfg["stand_frac"]) * cnt_)
            if np.isfinite(zbpmin).any():
                # ...or over a SPARSE measured bed (2026-10-31, Upper Hazel Lake: KBay 2023's 'ground' sheet at 528.95 m over
                # Riordan's soundings at 491-528): sparse_bed_min_n soundings under a sheet are enough, whatever its size or height
                ubp_n = cnt(flat_ & np.isfinite(zbpmin) & (zbpmin < Z[n] - float(cfg["bed_stand_dz_m"])))
                bad_b |= (ubp_n >= float(cfg["sparse_bed_min_n"])) & (cnt_ >= smc)
            # ...and a SHEET ABOVE THE TIDE'S REACH among the survey's OWN water returns at its level is that water's surface
            # (2026-10-31, Hig's review of the fix1 crops: the 61 ha lake at 59.31234,-151.86813 -- KBay 2023 saw water on a
            # third of it and 'ground' 2-3 cm over its 12.86 m level on the rest; the shore stage, offered both hypotheses at
            # the level, kept the class).  Judged by the sheet: flat cells within two cells of the survey's water returns and
            # within own_sheet_tol_m of them, stand_min_cells of them.  Never within the tide's reach: a wet tidal flat
            # carries water returns at its own level too (Hoen's Lagoon, above), and there the measured bed decides.
            # ...OF SUCH A SHEET, ONLY THE CELLS AT THE WATER'S LEVEL (2026-11-05, Hig, 59.68841,-151.34924, the bench east of Homer: the 2008 lidar gone from half the ground).  A "sheet" is every connected cell that is flat
            # over 10 m, so a gently sloping bench or fen is one sheet spanning metres of height, and ten cells of it beside a pond took all of it: 71.6 ha of Kenai 2008's bare ground in a 9 km2 window.  A lake is level: the
            # sheet's cells within own_sheet_tol_m of the nearest of the survey's water returns are its surface, the rest of the sheet is ground
            bad_o = np.zeros(nl_ + 1, bool); own_ = None
            if "z_w" in obs[n]:
                hw0 = (obs[n]["n_w"] > 0) & np.isfinite(obs[n]["z_w"])
                if hw0.any() and (~tide_[1:]).any():
                    dw0, (iy0, ix0) = ndimage.distance_transform_edt(~hw0, sampling=res, return_indices=True)
                    own_ = flat_ & (np.abs(Z[n] - obs[n]["z_w"][iy0, ix0]) <= float(cfg.get("own_sheet_tol_m", 0.15)))
                    at_ = own_ & (dw0 <= 2.0 * res)
                    bad_o = ~tide_ & (cnt(at_) >= smc)
                    del dw0, iy0, ix0, at_
                del hw0
            bad_ = bad_w | bad_b; bad_[0] = False; bad_o[0] = False
            st_ = bad_[lab_]
            # ...OF SUCH A SHEET, ONLY THE CELLS AT THE LEVEL OF ITS PART OVER THE WATER / BED (2026-11-09, Hig's review: the Woz delta,
            # 59.54718,-151.40109 -- KBay 2023's flat is one 'sheet' from -2.3 to +3.4 m, and the 5% of it over the 2009 multibeam at the
            # front took all 241 ha; "the 2023 lidar is very good for the Woz delta -- it should be used essentially with no modification").
            # The sea is level: the level is the median of the offending cells, and the sheet's cells within sheet_level_tol_m of it go.
            slt_ = 0.0 if os.environ.get("JOINT_AB_OFF") else float(cfg.get("sheet_level_tol_m", 0.0))
            if slt_ > 0 and st_.any():
                # only a sheet within the tide's reach that SLOPES as a whole (> sheet_level_slope, >= 1 ha, rising >= 1 m: a tidal flat, not a
                # noisy sea surface -- Bluff Point's Homer 2018 patch is level and goes whole, as before; above the tide nothing changes: Grewingk's
                # ice over the lake soundings, Upper Hazel's surface)
                off_ = st_ & ((np.isfinite(zwmin) & (zwmin < Z[n] - float(cfg["stand_dz_m"]))) | (np.isfinite(zbmin) & (zbmin < Z[n] - float(cfg["bed_stand_dz_m"])))
                               | (np.isfinite(zbpmin) & (zbpmin < Z[n] - float(cfg["bed_stand_dz_m"]))))
                ids_ = np.unique(lab_[off_]); ids_ = ids_[ids_ > 0]
                lv_ = np.full(nl_ + 1, np.nan)
                if ids_.size: lv_[ids_] = np.asarray(ndimage.median(Z[n], np.where(off_, lab_, 0), ids_))
                # the sheet's water level (its part over water / bed) within the tide's reach -- the sheet itself may run up onto land (Woz)
                tsh_ = _tilted_pieces(st_ & (np.nan_to_num(lv_, nan=np.inf)[lab_] <= tm_), Z[n])
                if tsh_.any():
                    off_ = tsh_ & ((np.isfinite(zwmin) & (zwmin < Z[n] - float(cfg["stand_dz_m"]))) | (np.isfinite(zbmin) & (zbmin < Z[n] - float(cfg["bed_stand_dz_m"])))
                                   | (np.isfinite(zbpmin) & (zbpmin < Z[n] - float(cfg["bed_stand_dz_m"]))))
                    at_lv = tsh_ & (np.abs(Z[n] - lv_[lab_]) <= slt_)                 # the sheet's cells at the level of its part over water / bed...
                    l2_, n2_ = ndimage.label(at_lv, structure=np.ones((3, 3), bool))
                    hit_ = np.zeros(n2_ + 1, bool); hit_[np.unique(l2_[at_lv & off_])] = True; hit_[0] = False   # ...connected to that part...
                    hit_m = hit_[l2_] & ~_tilted_pieces(hit_[l2_], Z[n])               # ...and level themselves
                    keep_ = tsh_ & ~hit_m
                    log(f"  {n:14s} sea sheets that slope (> {float(cfg.get('sheet_level_slope', 0)) * 100:g} %, a tidal flat): {float(keep_.sum()) * res * res / 1e4:.2f} ha stay ground, "
                        f"{float(hit_m.sum()) * res * res / 1e4:.2f} ha at the water's level go")
                    rep.setdefault("sheet_tilted_ha", {})[n] = round(float(keep_.sum()) * res * res / 1e4, 2)
                    st_ = st_ & ~keep_
                    del at_lv, l2_, hit_, hit_m, keep_
                del tsh_, off_, lv_
            if own_ is not None and bad_o.any():
                o_ = bad_o[lab_] & own_
                to_ = _tilted_pieces(o_, Z[n])                  # ...a wet tidal flat has water returns at its own height everywhere, and slopes (Woz 2026-11-09)
                if to_.any():
                    log(f"  {n:14s} own-water sheets that slope: {float(to_.sum()) * res * res / 1e4:.2f} ha stay ground")
                    rep.setdefault("own_sheet_tilted_ha", {})[n] = round(float(to_.sum()) * res * res / 1e4, 2)
                st_ = st_ | (o_ & ~to_)
                del o_, to_
            bad_ = bad_ | bad_o; del own_
            sp_ = np.zeros((H, W), bool)
            # ...or over a COMPILATION standing bed_stand_dz_m below (2026-11-09, Kasitsna 59.47827,-151.56736: 22 cells of KBay 2023 at -0.07 m,
            # the stand of its own 43 ha sea sheet 200 m off, over OCM/NCEI at -2.2 m; Hig: "I don't think there is any positive relief here").
            # Only at the stand of a sheet already judged the sea (below): the compilation alone condemns nothing.
            ud_n = cnt(flat_ & np.isfinite(zdmax) & (zdmax < Z[n] - float(cfg.get("speck_derived_dz_m", 1.0))))   # Kasitsna: OCM 1.9 m under them
            spk = tide_ & (((cnt_ < smc) & (ub_n >= 0.5 * cnt_)) | ((cnt_ < float(cfg.get("speck_derived_max_cells", 50))) & (ud_n >= 0.5 * cnt_))) & ~bad_; spk[0] = False   # pieces of 11-13 cells there
            if spk.any() and st_.any():
                dn_, (iy_, ix_) = ndimage.distance_transform_edt(~st_, sampling=res, return_indices=True)
                lev_ = Z[n][iy_, ix_]; del iy_, ix_
                sp_ = spk[lab_] & (dn_ <= float(cfg["speck_near_m"])) & (np.abs(Z[n] - lev_) <= tol_)
                del dn_, lev_
            # ...and a speck at the survey's OWN water level, its water returns within surf_near_m (2026-10-29, Hig's review
            # of Bluff Point run 4: Kenai 2008's single returns at -0.55..-1.06 m in 4 m of water, among its water returns
            # at -0.69 m, over nothing measured -- pimples): a flat of the survey's own is a sheet, judged by the bed above;
            # a few cells at that day's water level are its surface
            # (labelled on its own, without the flat test: strays of the sea at -0.55..-1.06 m are not "flat" against each
            # other, and their level is what tells them, not their spread)
            if "z_w" in obs[n]:
                hw_ = (obs[n]["n_w"] > 0) & np.isfinite(obs[n]["z_w"])
                lab2_, nl2_ = ndimage.label(zf_ & (Z[n] <= tm_) & ~st_, structure=np.ones((3, 3), bool))
                spo = np.bincount(lab2_.ravel(), minlength=nl2_ + 1) < smc; spo[0] = False
                # ...where the survey's RETURNS of any kind are SPARSE: a stray return of the sea sits alone among cells the
                # water swallowed (Kenai 2008 off Bluff Point: any return in 3 % of cells); a wet mud flat reflects densely,
                # its ground broken into specks by its water-class returns at the same level (KBay 2023 on Hoen's inner flat:
                # ground in 11 % of cells, any return in 83 % -- a test on the ground's density took the flat out, run 6)
                dens_ = ndimage.uniform_filter(np.isfinite(obs[n]["zmax"]).astype(np.float32), 11) if "zmax" in obs[n] else ndimage.uniform_filter(zf_.astype(np.float32), 11)
                if hw_.any() and (spo.any() or hc_ is not None):
                    dw_, (iy_, ix_) = ndimage.distance_transform_edt(~hw_, sampling=res, return_indices=True)
                    wl_ = obs[n]["z_w"][iy_, ix_]; del iy_, ix_
                    sp_ = sp_ | (spo[lab2_] & (dens_ <= float(cfg["speck_sparse_frac"])) & (dw_ <= float(cfg["speck_near_m"])) & (np.abs(Z[n] - wl_) <= tol_))
                    # ...or, dense or sparse, OVER A CAST: a speck at the survey's own water level with the shallowest sounding within
                    # speck_cast_m standing bed_stand_dz_m or more below it is the sea (2026-11-06, Hig: "patches of bad coastal data ...
                    # in the nearshore ocean near Anchor Point", 59.72032,-151.83655 -- Kenai 2008's single returns at +1.7..2.2 m, the
                    # level of its own water returns within 1-4 m of them, in a cloud where any return falls in ~45 % of cells (the sea's
                    # water class is dense there, so the sparse test above passed them), over NOS h10033's casts at -4..-6 m, 4 within
                    # 100 m; the fill honoured them as ground and raised pimples of +2 m in 6 m of water).  Hoen's mud flat has no cast
                    # under it and keeps its specks.
                    if hc_ is not None:
                        hcn_ = np.where(N[n] <= float(cfg.get("float_sparse_n", 2.0)), hc_all, hc_) if S[n].kind == "lidar" else hc_
                        at_ = (dw_ <= float(cfg["speck_near_m"])) & (np.abs(Z[n] - wl_) <= tol_)
                        deep_ = (hcn_ > -1e8) & (hcn_ <= Z[n] - float(cfg["bed_stand_dz_m"]))
                        sp_ = sp_ | (spo[lab2_] & at_ & deep_)
                        # ...and a FLOATING COMPONENT of any size: one whose cells are (nearly) all at the survey's own water level with
                        # a cast that far below most of them -- Anchor Point's strays join into pieces of 10-2,700 cells too big for the
                        # speck test and too porous (water-class cells between) for the 5x5 flat test.  A beach rises out of the water
                        # and its component holds cells above the level; a mud flat has no cast under it.
                        cnt_ = np.bincount(lab2_.ravel(), minlength=nl2_ + 1).astype(np.float64); cnt_[0] = 1.0
                        fa_ = np.bincount(lab2_.ravel(), weights=at_.ravel(), minlength=nl2_ + 1) / cnt_
                        fd_ = np.bincount(lab2_.ravel(), weights=deep_.ravel(), minlength=nl2_ + 1) / cnt_
                        flo_ = (fa_ >= float(cfg.get("float_at_frac", 0.9))) & (fd_ >= float(cfg.get("float_cast_frac", 0.5))); flo_[0] = False
                        if flo_.any():
                            add_ = flo_[lab2_] & ~sp_
                            log(f"  {n:14s} floating components at the survey's own water level over a cast: {int(flo_.sum())} pieces, {float(add_.sum()) * res * res / 1e4:.2f} ha")
                            sp_ = sp_ | add_
                        del at_, deep_
                    del dw_, wl_
                del dens_, lab2_
            # ...and what a DTM's sea sheet leaves behind: bits of its surface too rough for the flat test, at the sheet's level beside it, a small component of their own
            # (2026-11-03: 1 ha of Chugachmiut specks in the water off Windy Bay; the shore's own low cells are one piece with the land and stay)
            if S[n].kind == "grid" and st_.any():
                dn_, (iy_, ix_) = ndimage.distance_transform_edt(~st_, sampling=res, return_indices=True)
                lev_ = Z[n][iy_, ix_]; del iy_, ix_
                lab3_, nl3_ = ndimage.label(zf_ & ~st_, structure=np.ones((3, 3), bool))
                sm3 = np.bincount(lab3_.ravel(), minlength=nl3_ + 1) < smc; sm3[0] = False
                sp_ = sp_ | (sm3[lab3_] & (dn_ <= float(cfg["speck_near_m"])) & (np.abs(Z[n] - lev_) <= 2.0 * tol_))
                del dn_, lev_, lab3_
            # ...or a SHEET OVER THE COMPILATIONS ALONE, by a wide margin (2026-11-08, Hig's cm18 review: "bits of flat land protruding
            # into the ocean" at 59.69273,-151.77838 and 59.72965,-151.84653 -- Kenai 2008's flight lines read the sea at +2.5 and +3.8 m
            # as ground, straight-edged strips over CUDEM's and NCEI's -6 m, no measured bed or cast under them).  Those sheets are flat
            # to 0.1-0.2 m but pocked with dropouts 0.5-1 m low, so the 5x5 range above (median 1.06 m) never called them flat: here a
            # cell is flat when within stand_flat_m of its 5x5 median and the median is level (under derived_stand_slope over
            # 10 m).  A sheet within the tide's reach standing more than derived_stand_dz_m over the SHALLOWEST compilation on
            # derived_stand_frac of its cells is the sea, with the pocks it encloses.  A compilation is no measurement, hence the
            # wide margin and the majority: a real flat is in the compilations at its own height and never trips it.
            dfr_ = float(cfg.get("derived_stand_frac", 0.0))
            if dfr_ > 0 and np.isfinite(zdmax).any():
                zin_ = zf_ & (Z[n] <= tm_)
                if zin_.any():
                    ii_ = ndimage.distance_transform_edt(~zf_, return_distances=False, return_indices=True)
                    zn_ = Z[n][ii_[0], ii_[1]]; del ii_
                    md_ = ndimage.median_filter(zn_, size=5); del zn_
                    sm_ = ndimage.gaussian_filter(md_, 5.0 / res)
                    gy_, gx_ = np.gradient(sm_, res); lv_ = np.hypot(gx_, gy_) <= float(cfg.get("derived_stand_slope", 0.01)); del gy_, gx_, sm_
                    fD = zin_ & (np.abs(Z[n] - md_) <= float(cfg["stand_flat_m"])) & lv_ & ~st_; del md_, lv_
                    lD, nD = ndimage.label(fD, structure=np.ones((3, 3), bool))
                    if nD:
                        cD = np.bincount(lD.ravel(), minlength=nD + 1)
                        uD = np.bincount(lD.ravel(), weights=(fD & np.isfinite(zdmax) & (zdmax < Z[n] - float(cfg.get("derived_stand_dz_m", 4.0)))).ravel(), minlength=nD + 1)
                        bD = (cD >= smc) & (uD >= dfr_ * cD); bD[0] = False
                        if bD.any():
                            stD = ndimage.binary_fill_holes(bD[lD]) & zin_ & ~st_
                            log(f"  {n:14s} flat ground over the compilations alone (> {float(cfg.get('derived_stand_dz_m', 4.0)):g} m above the shallowest on "
                                f"{dfr_:.0%} of the sheet): {int(bD.sum())} sheets, {float(stD.sum()) * res * res / 1e4:.2f} ha with the pocks they enclose")
                            sp_ = sp_ | stD
                            del stD
                    del lD, fD
                del zin_
            # ...or a LEVEL PIECE RINGED BY A DEEPER BED (2026-11-09, Hig's cm18 review: "isolated artifacts ... correlated with the seam
            # between multibeam and lidar", 19 spots).  Chugachmiut's DTM is a plane at the sea surface (-1 m) far offshore; the sheet
            # rule above takes it where the 2009 multibeam lies UNDER it, and where the multibeam has a hole the plane stood as a mesa
            # at -1 m in a bed at -6..-14 (59.40905,-151.89192; a 150 m hook at 59.36553,-151.83973); KBay 2023 and Kenai 2008 leave the
            # same pieces of their sea surface beside the swaths (59.43588,-151.39171; 59.75478,-151.15913).  Judged by the piece: its
            # cells within the tide's reach, level (mesa_level_frac within mesa_level_m of its median, nothing above it by mesa_rise_m:
            # a beach rises out of the water, a rock top is rough), and its RING (mesa_ring_m outside it) mostly a bed far below:
            # a measured bed bed_stand_dz_m under its level, or a compilation derived_stand_dz_m under it; any other survey's ground in
            # the ring counts against.  A real low flat beside a channel has its land in the ring and survives.
            mr_ = int(round(float(cfg.get("mesa_ring_m", 0.0)) / res))
            if mr_ > 0:
                cand_ = zf_ & (Z[n] <= tm_) & ~st_ & ~sp_
                if cand_.any():
                    # level CELLS only (within mesa_level_m of the 5x5 median of the survey's nearest cells, that median under mesa_slope
                    # over 5 m): a piece of the sea surface joined to the shore by its own sloping beach cells is cut from them, and those
                    # beach cells then sit in its ring as the survey's own ground, against it
                    ii_ = ndimage.distance_transform_edt(~zf_, return_distances=False, return_indices=True)
                    md_ = ndimage.median_filter(Z[n][ii_[0], ii_[1]], size=5); del ii_
                    gy_, gx_ = np.gradient(ndimage.gaussian_filter(md_, 2.5 / res), res)
                    cand_ &= (np.abs(Z[n] - md_) <= float(cfg.get("mesa_level_m", 0.3))) & (np.hypot(gx_, gy_) <= float(cfg.get("mesa_slope", 0.05)))
                    del md_, gy_, gx_
                if cand_.any():
                    lM, nM = ndimage.label(cand_, structure=np.ones((3, 3), bool))
                    cM = np.bincount(lM.ravel(), minlength=nM + 1)
                    medM = np.zeros(nM + 1, np.float32); medM[1:] = np.asarray(ndimage.median(Z[n], lM, np.arange(1, nM + 1)))
                    mxM = np.zeros(nM + 1, np.float32); mxM[1:] = np.asarray(ndimage.maximum(Z[n], lM, np.arange(1, nM + 1)))
                    lvM = np.bincount(lM.ravel(), weights=(cand_ & (np.abs(Z[n] - medM[lM]) <= float(cfg.get("mesa_level_m", 0.3)))).ravel(), minlength=nM + 1)
                    okM = (cM >= float(cfg.get("mesa_min_cells", 4))) & (lvM >= float(cfg.get("mesa_level_frac", 0.8)) * cM) & (mxM - medM <= float(cfg.get("mesa_rise_m", 1.0)))
                    okM[0] = False
                    if okM.any():
                        dR, (iyR, ixR) = ndimage.distance_transform_edt(lM == 0, sampling=res, return_indices=True)
                        ring_ = (lM == 0) & (dR <= mr_ * res); lR = np.where(ring_, lM[iyR, ixR], 0); del dR, iyR, ixR
                        LR = medM[lR]
                        deep_ = ring_ & ((np.isfinite(zbmax) & (zbmax <= LR - float(cfg["bed_stand_dz_m"])))
                                         | (~np.isfinite(zbmax) & np.isfinite(zdmax) & (zdmax <= LR - float(cfg.get("derived_stand_dz_m", 4.0)))))
                        gnd_ = np.zeros((H, W), bool)
                        for m2 in names:
                            if m2 != n and S[m2].cls != "bed" and S[m2].kind in ("lidar", "grid") and not S[m2].derived:
                                gnd_ |= np.isfinite(Z[m2]) & allow[m2] & (Z[m2] > LR - float(cfg["bed_stand_dz_m"]))
                        gnd_ &= ring_ & ~deep_
                        own_r = ring_ & zf_ & ~st_ & ~sp_ & ~deep_     # the survey's own cells not level or above the tide's reach (its sea, cut off above, is neutral)
                        nD_ = np.bincount(lR.ravel(), weights=deep_.ravel(), minlength=nM + 1)
                        nA_ = np.bincount(lR.ravel(), weights=(deep_ | gnd_ | own_r).ravel(), minlength=nM + 1)
                        bM = okM & (nA_ > 0) & (nD_ >= float(cfg.get("mesa_ring_frac", 0.6)) * np.maximum(nA_, 1)); bM[0] = False
                        if bM.any():
                            stM = bM[lM]
                            log(f"  {n:14s} level pieces ringed by a deeper bed (mesas): {int(bM.sum())} pieces, {float(stM.sum()) * res * res / 1e4:.2f} ha")
                            rep.setdefault("mesas", {})[n] = dict(pieces=int(bM.sum()), ha=round(float(stM.sum()) * res * res / 1e4, 2))
                            sp_ = sp_ | stM
                            del stM
                        del ring_, lR, LR, deep_, gnd_, own_r
                    del lM
                del cand_
            if lw_area is not None:
                lwb_ = zf_ & lw_area & (Z[n] > lw_lev)
                # ...over a MEASURED bed or a cast standing bed_stand_dz_m below it: the measured bed tells the water from the mud (Hoen's Lagoon's
                # flat at 0.8 m lies inside the low-tide water polygon with nothing measured under it; Anchor Point's tongue has the 1977-84 casts)
                und_ = np.isfinite(zbmin) & (zbmin < Z[n] - float(cfg["bed_stand_dz_m"]))
                if hc_ is not None:
                    und_ |= np.isfinite(hc_) & (hc_ > -1e8) & (hc_ < Z[n] - float(cfg["bed_stand_dz_m"]))
                # ...or where the survey saw the surface only SPARSELY (returns in under derived_ground_dense_frac of the cells about): strays of the
                # sea, as Kenai 2008's off Anchor Point -- a flat reflects densely (Hoen's, 83 %)
                if "zmax" in obs[n]:
                    und_ |= ndimage.uniform_filter(np.isfinite(obs[n]["zmax"]).astype(np.float32), 11) < float(cfg["derived_ground_dense_frac"])
                lwb_ &= und_; del und_
                if lwb_.any():
                    log(f"  {n:14s} ground in the mapped low-tide water, > {lw_lev:+.2f} m and off every mapped rock: {float(lwb_.sum()) * res * res / 1e4:.2f} ha")
                    rep.setdefault("low_water_ground_ha", {})[n] = round(float(lwb_.sum()) * res * res / 1e4, 2)
                    sp_ = sp_ | lwb_
                del lwb_
            st_ = st_ | sp_
            if st_.any() and float(cfg.get("derived_under_sheet_m", 0.0)) > 0:
                # the sea surface this survey saw, within the tide's reach, with NOTHING measured under it (no measured bed, no cast within
                # speck_cast_m): the compilations under it yield below (2026-11-09, Kasitsna Bay head: KBay 2023's sea at -0.08 m, level over
                # 43 ha, and under it only OCM's pocked -3.4 m -- Hig: "a low-fi source filling in where there should be water fill")
                fr_ = st_ & (Z[n] <= tm_) & ~(np.isfinite(zbmin) & (zbmin < Z[n] - float(cfg["bed_stand_dz_m"])))
                if hc_ is not None:
                    fr_ &= ~(np.isfinite(hc_) & (hc_ > -1e8) & (hc_ < Z[n] - float(cfg["bed_stand_dz_m"])))   # a cast deep under it is a measured bed
                sea_free = np.where(fr_ & ~np.isfinite(sea_free), Z[n], sea_free)
                del fr_
            if st_.any():
                stand[n] = st_; Wt[n][st_] = 0.0; Wc[n][st_] = 0.0
                log(f"  {n:14s} flat ground that is the sea: {int(bad_w.sum())} sheets over others' lower water, {int(bad_b.sum())} over a measured bed; "
                    f"{int(bad_o.sum())} lake sheets at the survey's own water level above the tide's reach; {int(sp_.sum())} speck cells at their stand; {float(st_.sum()) * res * res / 1e4:.2f} ha")
                rep.setdefault("sea_sheets", {})[n] = dict(over_water=int(bad_w.sum()), over_bed=int(bad_b.sum()), own_lake=int(bad_o.sum()),
                                                           speck_cells=int(sp_.sum()), ha=round(float(st_.sum()) * res * res / 1e4, 2))
            del lab_, flat_, sp_
    del zwmin, zbmin, zbpmin, zbmax
    # A DERIVED bed in a GAP OF MEASURED GROUND (2026-10-29, Hig's review of Hoen's Lagoon: OCM's -2.4 m stood in 20-40 m
    # patches of KBay 2023's horizontal mud flat at 0.8 m, where the lidar's returns there were water at the flat's own
    # level -- "pits"): the compilation is no independent evidence, and inside a measured survey's ground, closed over
    # derived_ground_gap_m, it yields like it does to a measured bed; the fill takes the gap from its rim and the water
    # the lidar saw.  The ground counted is what survives the sea-surface rules above (a lidar's sea at the stand would
    # otherwise push the compilation out of the open water).  A tidal channel narrower than the gap is flattened with it
    # (the slough in Hoen's outer lagoon, as in v8): telling a channel from a wet flat is still to do.
    ggc = int(round(float(cfg.get("derived_ground_gap_m", 0.0)) / res / 2))
    if ggc > 0:
        gnd = np.zeros((H, W), bool)
        for m in names:
            if S[m].kind == "lidar" and S[m].cls == "ground" and not S[m].derived:
                g_ = allow[m] & np.isfinite(Z[m])
                if m in stand: g_ &= ~stand[m]
                if m in own_surf: g_ &= ~own_surf[m]
                # ...and only where the survey saw the surface DENSELY (returns of any kind in derived_ground_dense_frac of
                # the cells about): a flat reflects (Hoen's inner flat, 83 %); open water swallows the laser, and a few
                # strays of the sea left there must not push the compilation out (off Bluff Point, 3-20 %: 13 ha of NCEI
                # yielded to them on the first cut)
                if "zmax" in obs[m]:
                    g_ &= ndimage.uniform_filter(np.isfinite(obs[m]["zmax"]).astype(np.float32), 11) >= float(cfg["derived_ground_dense_frac"])
                gnd |= g_
        if gnd.any():
            gcl = ndimage.binary_closing(gnd, structure=np.ones((3, 3), bool), iterations=ggc) & ~gnd
            for n in names:
                if S[n].derived and S[n].cls == "bed":
                    add_ = np.isfinite(Z[n]) & gcl & ~sup[n]
                    if add_.any():
                        sup[n] |= add_; Wc[n][add_] = 0.0
                        if n in WcB: WcB[n][add_] = 0.0
                        rep["superseded_km2"][n] = round(float(sup[n].sum()) * res * res / 1e6, 3)
                        log(f"  {n:14s} yields in gaps of measured ground (<= {2 * ggc * res:.0f} m): {float(add_.sum()) * res * res / 1e4:.2f} ha")
            del gcl
        del gnd
    dus_ = float(cfg.get("derived_under_sheet_m", 0.0))
    if dus_ > 0 and "sea_free" in locals() and np.isfinite(sea_free).any():
        sf_ = ndimage.grey_dilation(np.nan_to_num(sea_free, nan=-1e9), size=3); sf_ = np.where(sf_ > -1e8, sf_, np.nan)
        for n in names:
            if S[n].derived and S[n].cls == "bed":
                add_ = np.isfinite(Z[n]) & np.isfinite(sf_) & (Z[n] >= sf_ - dus_) & ~sup[n]
                if add_.any():
                    sup[n] |= add_; Wc[n][add_] = 0.0
                    if n in WcB: WcB[n][add_] = 0.0
                    rep["superseded_km2"][n] = round(float(sup[n].sum()) * res * res / 1e6, 3)
                    log(f"  {n:14s} yields under a sea surface with nothing measured below (within {dus_:g} m of it): {float(add_.sum()) * res * res / 1e4:.2f} ha")
        del sf_
    rej = np.zeros((H, W), np.uint32); kept = {}; why = {}  # 32 bits: the Homer 2019/2021 NCMP pairs took kj_r6c3 to 17 sources (2026-11-08); why = {}
    # TWO PASSES (2026-10-26): the first only finds every source's cells OVER WATER (a lidar's sea surface classed
    # ground, at its own stand or at another survey's) and takes their weight away; the second judges with those
    # cells gone, so a bed is never rejected low against a lidar's sea surface (Grewingk 2019's "ground" at 3.85 m over
    # Halibut Cove's ridge rejected the 2009 multibeam under it, and the ridge became an island)
    ow_pre = {}
    for passno, (k, n) in [(p_, kn_) for p_ in (0, 1) for kn_ in enumerate(names)]:
        others = [m for m in names if m != n]
        z0 = np.zeros((H, W), np.float32)        # sum()'s start: a box with ONE source has no others (an empty sum is 0)
        wo = sum((np.where(sup[m], 0, Wt[m]) for m in others), z0)
        wc = sum(((WcB[m] if (S[n].kind == "grid" and S[m].kind == "grid" and S[m].sigma_m <= cfg["grid_better"] * S[n].sigma_m) else Wc[m]) for m in others), z0)
        zo = sum((np.where(sup[m] | ~np.isfinite(Z[m]), 0, Z[m] * Wt[m]) for m in others), z0)
        zo = np.where(wo > 0, zo / np.maximum(wo, 1e-12), np.nan)
        so = sum((np.where(sup[m] | ~np.isfinite(Z[m]), 0, np.nan_to_num(SG[m], nan=1.0) * Wt[m]) for m in others), z0)
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
        # the sea-surface verdicts found above (its own water over a measured bed, the sea at a stand) are rejections
        # whether or not the OTHERS saw water nearby (2026-10-29: inside that test until now, so off Bluff Point, where only
        # Kenai 2008 saw water, its sea-surface specks lost their weight but stood as the only ground -- pimples)
        if n in own_surf:
            ow |= own_surf[n]
        if n in stand:
            ow |= stand[n]
        if passno == 0:
            ow_pre[n] = ow
            if n == names[-1]:                              # the pre-pass is complete: take the over-water cells' weight away
                for m in names:
                    Wt[m][ow_pre[m]] = 0.0; Wc[m][ow_pre[m]] = 0.0
                    if m in WcB: WcB[m][ow_pre[m]] = 0.0
            continue
        ow |= ow_pre[n]
        bad = hi | lo | ow | sup[n] | ~allow[n]
        rej[hi | lo | ow | sup[n]] |= np.uint32(1 << k)
        kept[n] = np.where(bad, np.nan, Z[n]).astype(np.float32)
        if dump:                                  # per-source verdicts for the review point cloud (points.npz)
            why[n] = (hi.astype(np.uint8) | (lo.astype(np.uint8) << 1) | (ow.astype(np.uint8) << 2) | (sup[n].astype(np.uint8) << 3)
                      | ((~allow[n]).astype(np.uint8) << 4) | ((own_surf[n] if n in own_surf else np.zeros((H, W), bool)).astype(np.uint8) << 5) | ((stand[n] if n in stand else np.zeros((H, W), bool)).astype(np.uint8) << 6))
        rep["sources"][n].update(rejected_high_km2=round(float(hi.sum()) * res * res / 1e6, 3), rejected_low_km2=round(float(lo.sum()) * res * res / 1e6, 3),
                                 over_water_km2=round(float(ow.sum()) * res * res / 1e6, 3))
        log(f"  {n:14s} high {rep['sources'][n]['rejected_high_km2']:.3f}  low {rep['sources'][n]['rejected_low_km2']:.3f}  over water {rep['sources'][n]['over_water_km2']:.3f}  superseded {rep['superseded_km2'][n]:.3f} km2")
    # ---- 6. pool
    ws = {n: np.where(np.isfinite(kept[n]), N[n] / np.maximum(SG[n], cfg["sigma_floor_m"]) ** 2, 0.0).astype(np.float32) for n in names}
    # A MEASURED BED weighs by its MEASURED scatter, not its return count over a stated sigma (2026-10-29, Hig's review of
    # Seldovia run 3: "green lidar that might be better with only multibeam" -- over 7.7 km2 of overlap Seldovia 2019's
    # bed and the 2009 multibeam agree to +0.05 m (NMAD 0.12) but the lidar scatters 0.06 m about its own 10 m mean, the
    # multibeam 0.01; at 13 returns a cell over sigma 0.3 against one cell over 0.5 the lidar outweighed it 36:1). The
    # returns of a cell on the sea bed are not independent measurements of it. Scatter: the NMAD of the kept cells'
    # residual about their own 5x5 mean, one number per source in the window, floored at sigma_floor_m.
    rep["bed_scatter_m"] = {}
    ws_stated = dict(ws)                 # the stated sigmas still set the joint sigma: scatter is noise, not the datum's uncertainty
    for n in names:
        if S[n].cls != "bed" or S[n].derived or not np.isfinite(kept[n]).any():
            continue
        f_ = np.isfinite(kept[n]); ff_ = f_.astype(np.float32)
        nb_ = ndimage.uniform_filter(ff_, 5) * 25.0
        mu_ = ndimage.uniform_filter(np.nan_to_num(kept[n]), 5) * 25.0 / np.maximum(nb_, 1e-6)
        m_ = f_ & (nb_ >= 9)
        if m_.sum() < int(cfg.get("align_min_cells", 1000)):
            continue
        r_ = (kept[n] - mu_)[m_]; sc_ = max(float(1.4826 * np.median(np.abs(r_ - np.median(r_)))), float(cfg["sigma_floor_m"]))
        ws[n] = np.where(f_, 1.0 / sc_ ** 2, 0.0).astype(np.float32)
        rep["bed_scatter_m"][n] = round(sc_, 3)
        log(f"  {n:14s} bed weight by its measured scatter: {sc_:.3f} m")
        del f_, ff_, nb_, mu_, m_, r_
    wsum = sum(ws.values())
    ZJ = np.where(wsum > 0, sum(np.nan_to_num(kept[n]) * ws[n] for n in names) / np.maximum(wsum, 1e-12), np.nan).astype(np.float32)
    NJ = sum(np.where(np.isfinite(kept[n]), N[n], 0) for n in names).astype(np.float32)
    SRC = sum(np.isfinite(kept[n]).astype(np.uint32) << np.uint32(k) for k, n in enumerate(names)).astype(np.uint32)
    wsum_s = sum(ws_stated.values())
    SIG = np.where(wsum_s > 0, np.maximum(1 / np.sqrt(np.maximum(wsum_s, 1e-12)), cfg["sigma_floor_m"]), np.nan).astype(np.float32)
    del ws_stated, wsum_s
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
    for name, a, dt, nd in (("joint_z", ZJ, "float32", -9999), ("joint_n", NJ, "float32", -9999), ("joint_src", SRC, "uint32", 0),
                            ("joint_rej", rej, "uint32", 0), ("joint_dist", DIST, "float32", -9999), ("joint_sigma", SIG, "float32", -9999),
                            ("joint_nland", NL, "uint8", 255), ("joint_cls", CLSJ, "uint8", 255), ("vintage", V, "uint8", 255), ("water_z", WZ, "float32", -9999),
                            ("zone_day", ZD, "float32", -9999)):     # the zone's day, for the fill's casts (fill._point_stale)
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
            # footprint_close_m (default 50): a surface model gated cell by cell (ArcticDEM under canopy, 2026-10-31) closes
            # over small gaps only -- its gaps are where it is not to be trusted, and beyond them the prior stands (tip_t
            # fix2: closed over 50 m its gaps became a 15 km2 hole that swallowed the lagoon's low void)
            cm_ = float(s.d.get("footprint_close_m", 50.0))
            F |= ndimage.binary_closing(obs_any[s.name], iterations=max(1, int(cm_ / res))) if cm_ > 0 else obs_any[s.name]
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
    Z = rd("joint_z"); Wz = rd("water_z"); CJ = rd("joint_cls", np.uint8); rep = {}
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
    # WHERE A SURVEY FLEW (layer_flown.tif, for the composite's fallback footprint; 2026-11-06, Perl Island in kj_r1c2 under
    # cm18: no lidar there, ArcticDEM in patches, and with layer_class as the footprint every gap between the patches
    # was "flown" within near_m, the 3DEP fallback withdrawn from it, the gaps holes inside the sea body around the
    # island, "water", and the fill carved 8.6 km2 of the island to -27 m median, -116 m at worst).  Flown means a
    # lidar's return of any class (zmax), or a non-derived grid's cell (the multibeam, the sonar: the gaps between
    # their lines are the sea), or water this layer decided; a DERIVED grid (ArcticDEM, the compilations, the cleaned
    # vendor DTM) observes nothing where it has no cell, and the prior stands there.
    flown = np.isfinite(zmax)
    for s in srcs:
        if s.kind == "grid" and not s.derived and s.name in obs_any:
            flown |= obs_any[s.name]
    g = np.isfinite(Z); w = np.isfinite(Wz) & ~g
    noret = ~np.isfinite(zmax) & ~g & ~w
    dg = ndimage.distance_transform_edt(~g, sampling=res); dw = ndimage.distance_transform_edt(~w, sampling=res)
    lev = nearest_fill(smooth(Wz, w, 3.0 / res))
    wet = w | (~g & (dw < dg) & (dw <= cfg["wet_m"]) & (noret | (np.nan_to_num(zmax, nan=-1e9) < lev + 1.0)))
    wet = ndimage.binary_opening(wet, iterations=1) | w
    # A STRUCTURE OVER WATER IS NOT GROUND (Hig's cm18 review, 2026-11-12, Homer small-boat harbour: "the docks are not well
    # filtered ... accurately classify non-water as structures").  Over the floats and the deep-water dock both green
    # lidars (2019, 2021 NCMP) returned at +1..+10 m and classed none of it ground, water or bed; the cell was a void, the
    # composite's class-now fell back to the DGGS 2019 vendor DTM's "ground" -- the decks -- and the fill stood the harbour
    # floor on them.  A cell with returns but no ground, water or bed, nearer the survey's water or bed than any dry ground
    # and within structure_m of it, is a structure over water: water at the local level (its bed unknown -- the fill's,
    # from the measured floor around it), so no older surface can stand on it.  layer_structure.tif says where.
    if float(cfg.get("structure_m", 0) or 0) > 0:
        wb_ = w | (g & (CJ == CLS["bed"])); dry_ = g & (CJ != CLS["bed"])
        dwb_ = ndimage.distance_transform_edt(~wb_, sampling=res) if wb_.any() else np.full(Z.shape, np.inf)
        ddr_ = ndimage.distance_transform_edt(~dry_, sampling=res) if dry_.any() else np.full(Z.shape, np.inf)
        struct = ~g & ~wet & np.isfinite(zmax) & (dwb_ <= float(cfg["structure_m"])) & (dwb_ < ddr_) & (np.nan_to_num(lev, nan=1e9) <= float(cfg.get("structure_level_max_m", 4.5)))
        del dwb_, ddr_, wb_, dry_
        wet |= struct
        rep["structure_over_water_km2"] = round(float(struct.sum()) * res * res / 1e6, 4)
        write(out / f"layer{tag}_structure.tif", struct.astype(np.uint8), win, res, epsg, "uint8", 255)
    # AN OBJECT ON THE WATER IS NOT GROUND (Hig's cm18 review, 2026-11-13, Homer small-boat harbour: "still some serious
    # problems").  Homer 2021 NCMP classed the boats and the floats' boxes as ground at +2..+5 m on the dredged floor at
    # -5.5; rough, so the mesa rule (level pieces) never took them, and the fill's interior between the floats bent up to
    # them (-2.5 m ridges).  A small piece of ground (object_max_m2) whose ring (object_ring_m outside it) is mostly a
    # measured bed (object_ring_frac of the ring's bed and ground) lying object_dz_m and more below the piece's foot (its
    # 10th percentile), the foot itself at or above the water seen about (within 1 m), off every mapped rock and island
    # (rock_near_m), is a thing afloat or on piles: water at the local level, its bed the fill's from the floor about.
    # A rock or an islet rises out of its own flanks (a bed within reach of its foot); a boat stands on the water.
    # ...and the same for a deck SOUNDED AS BED (2026-11-16, Hig's review of s2, Seldovia's small-boat harbour: "clear out the dock
    # topography"): the OCM 2023 and Seldovia 2019 green lidars put "bed" on the floats at +0.2 m (p50) over a floor at -4 to -6, with the
    # water at -0.07 beside them, and the solve held them (bumps of 3-5 m).  A bed cell at or above the local water level less
    # object_bed_level_m is no bed under water; it joins the pieces tested here, and the ring is the deeper bed about them.
    # ...and a SUBMERGED rock protects nothing that stands well above it (2026-11-16, the mouth of Jakolof Bay 59.46861,-151.53422: a
    # boat, 16 m2 at +0.0..+0.6 m over the multibeam at -10.9, 40 m from a charted "Sub" rock at -1.38, was kept as ground and the solve
    # held it): a mapped rock with a charted height (z_chart) protects a piece only if the piece's foot is within rock_top_m of it.
    if float(cfg.get("object_max_m2", 0) or 0) > 0 and g.any():
        hb_ = g & (CJ == CLS["bed"]) & np.isfinite(lev) & (Z >= np.nan_to_num(lev, nan=1e9) - float(cfg.get("object_bed_level_m", 0.5)))
        bedc_ = g & (CJ == CLS["bed"]) & ~hb_; gl_ = (g & (CJ != CLS["bed"])) | hb_
        rr_ = max(int(round(float(cfg.get("object_ring_m", 6.0)) / res)), 1)
        lO_, nO_ = ndimage.label(gl_, structure=np.ones((3, 3), bool))
        if nO_ and bedc_.any():
            idx_ = np.arange(1, nO_ + 1)
            cO_ = np.bincount(lO_.ravel(), minlength=nO_ + 1)
            nearb_ = ndimage.binary_dilation(bedc_, iterations=rr_)
            cand_ = (cO_[1:] * res * res <= float(cfg["object_max_m2"])) & (np.asarray(ndimage.maximum(nearb_, lO_, idx_)) > 0)
            del nearb_
            prot_ = np.zeros(Z.shape, bool); ptop_ = np.full(Z.shape, np.inf, np.float32)   # the charted top of the protecting rock (inf: none charted)
            rp_ = cfg.get("rocks", str(Path(__file__).resolve().parent.parent / "water" / "rocks.geojson"))
            if rp_ and os.path.exists(rp_):
                for f_ in json.load(open(rp_))["features"]:
                    if (f_["properties"].get("type") or "") == "Deep":
                        continue
                    x_, y_ = f_["geometry"]["coordinates"][:2]; c_ = int((x_ - win[0]) // res); r_ = int((win[3] - y_) // res)
                    if 0 <= r_ < Z.shape[0] and 0 <= c_ < Z.shape[1]:
                        prot_[r_, c_] = True; zc_ = f_["properties"].get("z_chart")
                        ptop_[r_, c_] = float(zc_) if zc_ is not None and (f_["properties"].get("type") or "") == "Sub" else np.inf
            ip_ = Path(cfg.get("low_water", str(Path(__file__).resolve().parent.parent / "water" / "tide_low_water.geojson"))).parent / "tide_islands.geojson"
            if ip_.exists():
                from rasterio.features import rasterize
                from rasterio.transform import from_origin as _fo
                prot_ |= rasterize([(f_["geometry"], 1) for f_ in json.load(open(ip_))["features"]], out_shape=Z.shape,
                                   transform=_fo(win[0], win[3], res, res), fill=0, dtype="uint8").astype(bool)
            if prot_.any():
                dp_, (py_, px_) = ndimage.distance_transform_edt(~prot_, sampling=res, return_indices=True)
                ptop_ = ptop_[py_, px_]; prot_ = dp_ <= float(cfg.get("rock_near_m", 40.0)); del dp_, py_, px_
            obj_ = np.zeros(Z.shape, bool); nobj_ = 0
            sl_ = ndimage.find_objects(lO_); H_, W_ = Z.shape
            dz_ = float(cfg.get("object_dz_m", 2.0)); fr_ = float(cfg.get("object_ring_frac", 0.6))
            for i_ in np.nonzero(cand_)[0]:
                s_ = sl_[i_]
                if s_ is None: continue
                r0, r1 = max(s_[0].start - rr_, 0), min(s_[0].stop + rr_, H_); c0, c1 = max(s_[1].start - rr_, 0), min(s_[1].stop + rr_, W_)
                P_ = lO_[r0:r1, c0:c1] == i_ + 1
                R_ = ndimage.binary_dilation(P_, iterations=rr_) & ~P_
                zb_ = Z[r0:r1, c0:c1][R_ & bedc_[r0:r1, c0:c1]]
                ng_ = int((R_ & gl_[r0:r1, c0:c1]).sum())
                if zb_.size == 0 or zb_.size < fr_ * (zb_.size + ng_): continue
                zp_ = Z[r0:r1, c0:c1][P_]; foot_ = float(np.percentile(zp_, 10))
                pp_ = P_ & prot_[r0:r1, c0:c1]
                if pp_.any() and foot_ <= float(np.min(ptop_[r0:r1, c0:c1][pp_])) + float(cfg.get("rock_top_m", 1.0)): continue
                if float(np.percentile(zb_, 90)) > foot_ - dz_: continue
                if foot_ < float(np.median(lev[r0:r1, c0:c1][P_])) - 1.0: continue
                obj_[r0:r1, c0:c1] |= P_; nobj_ += 1
            del lO_, sl_, prot_
            if nobj_:
                g = g & ~obj_; wet = wet | obj_
                rep["objects_on_water"] = dict(pieces=int(nobj_), ha=round(float(obj_.sum()) * res * res / 1e4, 3))
                log(f"  objects on the water (small ground ringed by a deep measured bed, off mapped rocks): {nobj_} pieces, {float(obj_.sum()) * res * res / 1e4:.2f} ha")
            write(out / f"layer{tag}_objects.tif", obj_.astype(np.uint8), win, res, epsg, "uint8", 255)
            del obj_
        del bedc_, gl_
    level = np.where(wet, lev, np.nan)
    # returns but no ground (a bush) within gap_m of GROUND -- not of a bed (2026-10-29, Bluff Point run 4: Homer 2019's
    # sea-surface returns, their "ground" judged the sea, beside Homer 2018's green-lidar bed became 0.26 km2 of land at
    # -11 m): a bush stands beside dry ground, the sea's returns beside its bed
    dgl = ndimage.distance_transform_edt(~(g & (CJ != CLS["bed"])), sampling=res) if (g & (CJ != CLS["bed"])).any() else np.full(Z.shape, np.inf)
    gap = ~g & ~wet & ~noret & (dgl <= cfg["gap_m"])     # ...interpolated
    del dgl
    F = footprint(srcs, obs_any, win, res, epsg)
    steep = prior_slope(prior, win, res, epsg, cfg) > cfg["steep_deg"] if prior else np.zeros(Z.shape, bool)
    hole = noret & ~wet & ~gap
    lab, n = ndimage.label(hole, structure=np.ones((3, 3)))
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
    flown |= cls == CLS["water"]
    write(out / f"layer{tag}_flown.tif", flown.astype(np.uint8), win, res, epsg, "uint8", 255)
    a = res * res / 1e6
    rep.update(flown_km2=round(float(flown.sum()) * a, 3), ground_km2=round(float((cls == 1).sum()) * a, 3), bed_km2=round(float((cls == 3).sum()) * a, 3), ice_km2=round(float((cls == 4).sum()) * a, 3),
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
    try:
        import timing as _tm                                          # run as a script: topobathy/ is sys.path[0]
    except ImportError:
        from topobathy import timing as _tm
    _tm.install("joint")
    preflight(a.recipe)
    if a.stage in ("fuse", "all"): fuse(a.recipe, a.win, a.out, a.res, dump=a.dump)
    if a.stage in ("layer", "all"): layer(a.recipe, a.win, a.out, a.res, tag=a.tag)


if __name__ == "__main__":
    main()
