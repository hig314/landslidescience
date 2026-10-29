"""Water bodies (stage B): what each survey saw, the bodies they add up to, and the hand facts.

Hig, 2026-10-15: "start building vector water body maps to support this topobathy creation ...
a simple basis for manual review -- e.g. assigning depths to lakes that are known to be different
from the algorithmic inference."  The design is in TOPOBATHY_PLAN.md ("Water bodies (stage B)");
the short form:

  OBSERVATIONS  one feature per connected water body PER SURVEY (water_obs.geojson), never merged:
                level (median, p10/p90, flat or sloped), date, season, sigma, method, area.  A
                survey's sea level is its largest edge-touching body below `tide_max_m`.
  BODIES        one feature per body, the union of every survey's water (water_bodies.geojson and
                the id raster water_bodies.tif), stable ids across builds (matched by overlap), a
                KIND from the observations -- sea | tidal | perched | lake | river | ephemeral --
                per-survey levels, a consensus level where the kind has one, edits applied.
  EDITS         tools/lidar/water/edits.geojson: Point or Polygon with kind / level_m / dmax_m /
                hypsometry / who / date / why, matched BY LOCATION (ids may renumber), reported.

The rule the consumers follow: a water observation is a ceiling, not a surface.  The class step
`below_level` with "bodies" takes a lake's or a perched lagoon's own level as the bed cutoff
instead of the global low tide; the fill takes the kind (a perched body is not marine: no tidal
channel-by-width, no low-tide floor) and the edit's depth.

Kinds, first version (the sea level per survey is the reference; `tol` = perched_tol_m):
  sea        the survey's reference body itself (touches the grid edge, the largest below tide_max_m)
  tidal      every survey that saw it put its level within tol of that survey's sea level, or a
             sloped surface below tide_max_m (a creek draining across the flat on a falling tide)
  river      any survey saw a sloped surface (p90 - p10 > flat_tol_m) above tide_max_m
  ephemeral  some survey observed ground or bed over more than dry_frac of it (and it is flat)
  perched    above the sea in every survey that saw a sea, but below tide_max_m (the tide reaches it)
  lake       everything else (also any flat body where no survey saw a sea)
Flags: ice_contact (touches ice in class_now), touches_edge.  A body the observations call perched
that Hig's mapped source calls a lake is a lake (the mapped kind as a prior for the kind, never a stamp).

THE TIDAL LENS (Hig, 2026-10-16, Seldovia v6: "low-relief intertidal areas tend to have a lot of
sediment that moves around and makes for quite flat surfaces. I would rather back off and make
conservatively flat assumptions to start with, and then measure or otherwise generate data to
describe the depth of sites that are overly flat"): water the tide reaches -- kinds sea | tidal |
perched (TIDAL_KINDS) -- is read differently from a lake.  Its observed surface IS the surface until
a measurement says deeper: the fill puts the bed at the water surface (no low-tide floor, no channel
by width, no basin, no perched cap), measured beds under it (multibeam, green-lidar returns, soundings,
an edit's depth) still condition it, and a gridded PRIOR (a below_level step flagged "intertidal":
"void") is void inside the INTERTIDAL ZONE, intertidal.tif = every tidal-kind body plus everything
within intertidal_near_m of GROUND any survey observed between low_tide_m (the fill's, MLLW) and
tide_max_m, plus the tidal / perched bodies touching that (the sea body only within the band) -- the
return holes on a mudflat a prior's bed showed through, the sheet of water along a beach; not the open
bay under a lidar's sea-surface sheet, nor a detached spot of water far from any shore (Kenai 2008 off
Bluff Point), under which a prior stands.  A lake
(above tide_max_m, or mapped as one) keeps its basin.

Memory: everything on the build grid, read once per layer; `coarse` > 1 reads a decimated lattice
(auto when the grid's longer side exceeds max_px).  No window is ever derived from metres without
the grid's own resolution (2026-10-15: a 30 m window on a degree-gridded VRT took the machine down).
"""
from __future__ import annotations
import json
import time
import datetime as _dt
from pathlib import Path
import numpy as np
import rasterio
from rasterio import features
from rasterio.transform import Affine
from rasterio.warp import transform_geom
from scipy import ndimage
from .grid import Grid
from .stack import read_geo, apply_shift
from .axes import CLASSES, years

DEFAULTS = dict(min_m2=400.0, flat_tol_m=0.3, perched_tol_m=0.3, tide_max_m=4.5, dry_frac=0.5,
                match_frac=0.3, max_px=6000, coarse=None, level_split_m=0.3,
                intertidal_near_m=100.0, low_tide_m=None)          # low_tide_m: the fill's when None
FLAT_KINDS = ("lake", "perched", "ephemeral")      # kinds with one level, used as the bed ceiling
TIDAL_KINDS = ("sea", "tidal", "perched")          # the tidal lens: water the tide reaches is a flat until measured
S4 = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool)   # 4-connectivity: one polygon per label in features.shapes


def _season(d):
    try:
        m = _dt.date.fromisoformat(str(d)[:10]).month
    except ValueError:
        return None
    return ("winter", "winter", "spring", "spring", "spring", "summer", "summer", "summer", "autumn", "autumn", "autumn", "winter")[m - 1]


def _coarse(grid: Grid, f: int) -> Grid:
    return Grid(grid.crs, grid.transform * Affine.scale(f), -(-grid.width // f), -(-grid.height // f))


def _methods(spec):
    """How this layer's class step finds water, for the record."""
    cs = spec.get("class")
    names = []
    for step in (cs if isinstance(cs, list) else []):
        (name, kw), = step.items()
        if name == "raster" and kw.get("codes"):
            names.append("vendor class")
        elif name == "water_level":
            names.append("level flat")
        elif name == "flat_below":
            names.append("flat below")
        elif name == "flat_lakes":
            names.append("flat lakes")
        elif name == "snow_vs" and kw.get("as") == "water":
            names.append("vs reference")
        elif name == "islets":
            names.append("islets")
    return ", ".join(names) or ("default water" if cs == "water" else "")


def _pct(v, qs):
    return [float(x) for x in np.percentile(v, qs)] if v.size else [float("nan")] * len(qs)


def _polygons(lab, transform, crs, ids):
    """One (Multi)Polygon per label id, in EPSG:4326."""
    out = {}
    for geom, val in features.shapes(lab.astype(np.int32), mask=lab > 0, connectivity=4, transform=transform):
        val = int(val)
        if val not in ids:
            continue
        if val in out:                       # 4-connected labels give one shape each; keep a guard
            out[val]["coordinates"] = (out[val]["coordinates"] if out[val]["type"] == "MultiPolygon" else [out[val]["coordinates"]]) + [geom["coordinates"]]
            out[val]["type"] = "MultiPolygon"
        else:
            out[val] = geom
    return {k: transform_geom(crs, "EPSG:4326", g) for k, g in out.items()}


_MAPPED_CACHE = {}


def load_mapped(sources_path, crs):
    """The mapped-water sources (tools/lidar/water/sources.json): [{id, who, period, kind, subkind, name,
    geom (in `crs`)}], water kinds lake / river and islands.  Cached per (path, crs) -- the detector asks
    once per tile."""
    key = (str(sources_path), str(crs))
    if key in _MAPPED_CACHE:
        return _MAPPED_CACHE[key]
    sp = Path(sources_path)
    out = []
    if sp.exists():
        reg = json.loads(sp.read_text())
        for src in reg.get("sources", []):
            gp = Path(src["path"])
            gp = gp if gp.is_absolute() else sp.parent / gp
            if not gp.exists():
                continue
            gj = json.loads(gp.read_text())
            mine = []
            for f in gj.get("features", []):
                pr = f.get("properties") or {}
                if f.get("geometry") is None:
                    continue
                # a REVIEWED source keeps only the features the reviewer marked (status_field = status_value): the
                # automatic polygons copied into it stay out until someone has looked at them
                if src.get("status_field") and pr.get(src["status_field"]) != src.get("status_value"):
                    continue
                kind = src.get("kind_map", {}).get(pr.get(src.get("kind_field", "")), None) if src.get("kind_field") else "lake"
                if kind is None:
                    continue
                sub = src.get("subkind_map", {}).get(pr.get(src.get("subkind_field", "")), None) if src.get("subkind_field") else None
                lv = pr.get(src["level_field"]) if src.get("level_field") else None
                mine.append(dict(id=src["id"], who=src.get("who"), period=src.get("period"), kind=kind, subkind=sub, edge=src.get("edge"),
                                 name=pr.get(src.get("name_field", ""), None) if src.get("name_field") else None,
                                 level_m=float(lv) if lv not in (None, "") else None, fid=pr.get("id"),
                                 geom=transform_geom("EPSG:4326", crs, f["geometry"])))
            # A REVIEWED polygon REPLACES the older mapping it covers (`replaces`: source ids): an older polygon at least
            # half under the reviewed ones is dropped, so the reviewer's redraw is the one shoreline there, not one of two
            water_mine = [m for m in mine if m["kind"] != "island"]
            if src.get("replaces") and water_mine:
                from shapely.geometry import shape
                from shapely.ops import unary_union
                cover = unary_union([shape(m["geom"]).buffer(0) for m in water_mine])
                keep = []
                for o in out:
                    if o["id"] in src["replaces"]:
                        g = shape(o["geom"])
                        if g.area > 0 and g.intersection(cover).area >= 0.5 * g.area:
                            continue
                    keep.append(o)
                out[:] = keep
            out.extend(mine)
    _MAPPED_CACHE[key] = out
    return out


def mapped_rasters(sources_path, grid: Grid, win, only=("lake", "river")):
    """(water id raster int32 -- index+1 into load_mapped's list, 0 none; island mask) on the window."""
    feats = load_mapped(sources_path, grid.crs)
    sub = grid.sub(win); shape = (sub.height, sub.width)
    water = [(f["geom"], i + 1) for i, f in enumerate(feats) if f["kind"] in only]
    isl = [(f["geom"], 1) for f in feats if f["kind"] == "island"]
    wr = features.rasterize(water, out_shape=shape, transform=sub.transform, fill=0, dtype="int32") if water else np.zeros(shape, np.int32)
    ir = features.rasterize(isl, out_shape=shape, transform=sub.transform, fill=0, dtype="uint8").astype(bool) if isl else np.zeros(shape, bool)
    return wr, ir


def load_edits(path):
    p = Path(path)
    if not p.exists():
        return []
    gj = json.loads(p.read_text())
    eds = []
    for k, f in enumerate(gj.get("features", [])):
        pr = dict(f.get("properties") or {})
        pr.setdefault("id", f.get("id", k + 1))
        if pr.get("withdrawn"):                      # the record stays in the file; the fact no longer applies
            continue
        eds.append(dict(geom=f["geometry"], props=pr))
    return eds


def run(recipe, grid: Grid, edits_path=None, log=print):
    """The stage: observations, bodies, edits -> out/water_obs.geojson, water_bodies.{geojson,tif}, water.json."""
    cfg = dict(DEFAULTS); cfg.update(recipe.d.get("water", {}))
    t0 = time.time()
    f = cfg["coarse"] or max(1, -(-max(grid.width, grid.height) // int(cfg["max_px"])))
    cg = _coarse(grid, int(f)); res = cg.res; cell_m2 = res * res
    out = recipe.out_dir
    min_px = max(int(round(cfg["min_m2"] / cell_m2)), 1)
    layers = []                                                  # per survey: name, lab, zw, table
    missing = []
    for spec in recipe.layers:
        if spec.get("optional") or spec.get("_prior"):
            continue
        cp = out / f"class_{spec['name']}.tif"
        if not cp.exists():
            missing.append(spec["name"]); continue
        cls, cv = read_geo(str(cp), cg, cg.full(), "nearest")
        cls = np.where(cv, np.rint(cls), 0).astype(np.uint8)
        water = cls == CLASSES["water"]
        # a MEASURED BED with the layer's own water returns standing above it is water too, at the returns' level
        # (rule 3; 2026-10-26: once a bed was no longer superseded by a lidar's sea surface, the 2009 multibeam kept its
        # cells along the box's south-west edge, the joint layer classed them bed, and the water stage lost the sea --
        # every pool on the flats then had no sea to be judged against and became a lake).  The layer names the raster
        # of its water returns as `water_returns`.
        wr_, bedw = None, np.zeros(cls.shape, bool)
        if spec.get("water_returns") and Path(str(spec["water_returns"])).exists():
            wr_, wrv = read_geo(str(spec["water_returns"]), cg, cg.full(), "nearest")
            wr_ = np.where(wrv, wr_, np.nan).astype(np.float32)
        if not water.any() and wr_ is None:
            continue
        z, v = read_geo(str(recipe.source_path(spec)), cg, cg.full(), spec.get("resample", "bilinear"))
        z = apply_shift(spec, z); z = np.where(v, z, np.nan).astype(np.float32)
        if wr_ is not None:
            bedw = (cls == CLASSES["bed"]) & np.isfinite(wr_) & np.isfinite(z) & (wr_ > z + float(cfg.get("bed_water_min_m", 0.3)))
            if bedw.any():
                water = water | bedw
                log(f"  {spec['name']:12s} water returns over its measured bed: {int(bedw.sum()) * cg.res * cg.res / 1e6:.3f} km2 are water")
        if not water.any():
            continue
        lab, n = ndimage.label(water, structure=S4)
        if n == 0:
            continue
        ids = np.arange(1, n + 1)
        cnt = ndimage.sum(water, lab, ids)
        keep = cnt >= min_px
        edge = np.zeros(n + 1, bool); edge[np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]])] = True
        zw = np.where(water, np.where(bedw, wr_ if wr_ is not None else z, z), np.nan)
        table = {}
        for i in ids[keep]:
            m = lab == i
            vals = zw[m]; vals = vals[np.isfinite(vals)]
            p2, p10, p50, p90, p98 = _pct(vals, [2, 10, 50, 90, 98])
            sloped = (p90 - p10) > cfg["flat_tol_m"]
            table[int(i)] = dict(area_m2=int(cnt[i - 1] * cell_m2), level_m=round(p50, 2), level_p10_m=round(p10, 2), level_p90_m=round(p90, 2),
                                 level_std_m=round(float(np.std(vals)), 3) if vals.size else None,
                                 level_kind="sloped" if sloped else "flat",
                                 level_up_m=round(p98, 2) if sloped else None, level_down_m=round(p2, 2) if sloped else None,
                                 touches_edge=bool(edge[i]), n_cells=int(cnt[i - 1]))
        lab[~keep[np.maximum(lab - 1, 0)] | (lab == 0)] = 0
        # the sea that day: the largest edge-touching body below tide_max_m
        sea = [i for i, t in table.items() if t["touches_edge"] and t["level_m"] < cfg["tide_max_m"]]
        sea_id = max(sea, key=lambda i: table[i]["area_m2"]) if sea else None
        date = recipe.layer_date(spec)
        sea_rng = None
        if sea_id:
            # the sea's RANGE that day (flight lines at different tides): from the sea body's level histogram,
            # the modal 0.1 m bin extended both ways while bins stay populated (>= 0.5 % of its cells) -- a
            # pool that the water class joins to the sea (a lagoon at +0.79 over a TIN ramp) is a separate
            # mode beyond an empty gap and must not widen the range (it did: sea_hi 0.85, Seldovia 2026-10-15)
            sv = zw[lab == sea_id]; sv = sv[np.isfinite(sv)]
            h, e = np.histogram(sv, np.arange(np.floor(sv.min() * 10) / 10, sv.max() + 0.1, 0.1))
            i0 = int(np.argmax(h)); thr = 0.005 * sv.size
            lo = i0
            while lo > 0 and h[lo - 1] >= thr: lo -= 1
            hi = i0
            while hi < len(h) - 1 and h[hi + 1] >= thr: hi += 1
            sea_rng = [float(e[lo]), float(e[hi + 1])]
        layers.append(dict(name=spec["name"], spec=spec, lab=lab, zw=zw, zl=np.where(cls > 0, z, np.nan).astype(np.float32), cls=cls, table=table, date=date, yr=years(date),
                           season=_season(date), sigma=float(spec.get("sigma_m", 1.0)), sea_id=sea_id,
                           sea_level_m=table[sea_id]["level_m"] if sea_id else None, sea_range=sea_rng, method=_methods(spec)))
        log(f"  {spec['name']:<12} water {water.sum() * cell_m2 / 1e6:.3f} km2 in {int(keep.sum())} bodies >= {cfg['min_m2']:.0f} m2"
            f"{'; sea ' + format(layers[-1]['sea_level_m'], '.2f') + ' m' if sea_id else '; no sea seen'}  ({spec.get('name')}: {layers[-1]['method']})")
    if missing:
        log(f"  no per-layer class raster for {missing} (build --diag writes them)")
    if not layers:
        log("  no water observed by any survey"); return None
    # ---- observations file
    obs_feats = []; obs_key = {}
    for L in layers:
        polys = _polygons(L["lab"], cg.transform, cg.crs, set(L["table"]))
        for i, t in L["table"].items():
            oid = f"{L['name']}:{i}"; obs_key[(L["name"], i)] = oid
            props = dict(id=oid, survey=L["name"], source=str(L["spec"].get("source")), date=L["date"], season=L["season"],
                         sigma_m=L["sigma"], method=L["method"], is_sea=(i == L["sea_id"]), sea_level_m=L["sea_level_m"], **t)
            obs_feats.append(dict(type="Feature", id=oid, geometry=polys.get(i), properties=props))
    # ---- bodies: the union of every survey's water
    union = np.zeros((cg.height, cg.width), bool)
    for L in layers:
        union |= L["lab"] > 0
    # a hole inside a body that no survey classed as LAND is water, not an island (the Seldovia lagoon,
    # 2026-10-15: 2-4 cell gaps where the lidar got no returns off the pool lay outside the body, so the
    # regional grid's -1.6/-2.5 m there escaped the body's ceiling and floor and the fill spread them)
    # MAPPED WATER (Hig's hand-mapped lakes, water/sources.json; recipe "water": {"mapped": <sources.json>}):
    # islands feed the hole rule, every mapped polygon is checked against the bodies (report), each body
    # learns whether it was mapped.  Never a stamp: a polygon has no level and may predate a shoreline.
    mp_w = np.zeros(union.shape, np.int32); mp_i = np.zeros(union.shape, bool); mapped = []
    if cfg.get("mapped"):
        mapped = load_mapped(cfg["mapped"], cg.crs)
        mp_w, mp_i = mapped_rasters(cfg["mapped"], cg, cg.full())
        log(f"  mapped water: {len(mapped)} features loaded, {int((mp_w > 0).sum()) * cell_m2 / 1e4:.1f} ha of water polygons and "
            f"{int(mp_i.sum()) * cell_m2 / 1e4:.1f} ha of islands on this grid")
    holes = ndimage.binary_fill_holes(union) & ~union
    if holes.any():
        land_codes = [CLASSES[k] for k in ("ground", "built", "canopy", "ice", "snow")]
        land_any = mp_i.copy()                                   # a mapped island is land
        for L in layers:
            land_any |= np.isin(L["cls"], land_codes)
        hl, hn = ndimage.label(holes, structure=S4)
        hidx = np.arange(1, hn + 1)
        n_h = ndimage.sum(holes, hl, hidx); n_land = ndimage.sum(holes & land_any, hl, hidx)
        wet_holes = np.isin(hl, hidx[n_land < 0.5 * n_h])
        if wet_holes.any():
            union |= wet_holes
            log(f"  holes inside bodies that no survey saw as land, taken as water: {int((n_land < 0.5 * n_h).sum())} "
                f"({wet_holes.sum() * cell_m2 / 1e4:.2f} ha); kept as islands: {int((n_land >= 0.5 * n_h).sum())}")
    # SPLIT AT LEVEL STEPS: connected water is not one body where its surface steps -- a lagoon pool at +0.79
    # against the tidal channel at -0.02 (Seldovia, 2026-10-15: joined, the pool's edit re-kinded the sea).
    # The newest survey's water surface per cell; a 3x3 range over level_split_m is a barrier (a sill, a
    # fall); barrier cells go to the nearest body afterwards.  A river's slope is far below it per cell.
    # The sea is exempt from the step test among its own cells (a lidar holds it at several tides on
    # different lines: the first cut shattered Seldovia Bay into 400 bodies).  Cells at or below the
    # sea's range that day (+ perched_tol_m) are the SEA SET, one connectivity; cells above it are the
    # other, split further at steps; the two never join (the pool's TIN ramp down to the channel is cut
    # where it leaves the sea's range).
    # A survey that OBSERVED a cell supersedes older ones there: where it saw land and no water, an
    # older survey's level is cleared and the cell takes its nearest current water below (2026-10-21, Grewingk
    # Lake: 2021 saw bergs as land, the 2012 3DEP hydroflat level 41.3 m showed through on them, stepped 7.6 m
    # against the 48.9 m lake, and every berg became a "lake" of its own that no lake rule in the fill judged).
    zs = np.full(union.shape, np.nan, np.float32)
    _land_now = [CLASSES[k] for k in ("ground", "built", "canopy", "ice", "snow")]
    for L in sorted(layers, key=lambda L: L["yr"]):               # oldest first: the newest water wins
        m = np.isfinite(L["zw"]); zs[m] = L["zw"][m]
        zs[np.isin(L["cls"], _land_now) & ~m] = np.nan            # land only: a sonar's bed lies UNDER the water
    fin = np.isfinite(zs)
    sea_hi = max([L["sea_range"][1] for L in layers if L.get("sea_range")] or [None], key=lambda v: -np.inf if v is None else v)
    sea_cell = np.zeros(union.shape, bool)
    if fin.any():
        _, (fi, fj) = ndimage.distance_transform_edt(~fin, return_indices=True)
        zf = zs[fi, fj]
        if sea_hi is not None:
            sea_cell = union & (zf <= sea_hi + cfg["perched_tol_m"])
        above = union & ~sea_cell
        step = (ndimage.maximum_filter(zf, 3) - ndimage.minimum_filter(zf, 3)) > float(cfg["level_split_m"])
        barrier = above & step
        lab_a, n_a = ndimage.label(above & ~barrier, structure=S4)
        if barrier.any() and n_a:
            # a barrier cell joins the body it borders (within 3 cells); one with none that close is noise at
            # a shore or in the sea (two cells in Seldovia Bay joined a lake 500 m off) and is no body
            bd, (bi, bj) = ndimage.distance_transform_edt(lab_a == 0, return_indices=True)
            near = barrier & (bd <= 3)
            lab_a[near] = lab_a[bi, bj][near]
        lab_s, n_s = ndimage.label(sea_cell, structure=S4)
        lab = np.where(sea_cell, lab_s, np.where(lab_a > 0, lab_a + n_s, 0)).astype(np.int32); n = n_s + n_a
        union = lab > 0
        log(f"  sea set (<= {sea_hi:.2f} + {cfg['perched_tol_m']} m): {n_s} bodies, {sea_cell.sum() * cell_m2 / 1e6:.3f} km2; above it {n_a} bodies"
            f" (split at steps > {cfg['level_split_m']} m: {int(barrier.sum()) * cell_m2 / 1e4:.2f} ha of barrier cells)" if sea_hi is not None else
            f"  no sea seen: {n_a} bodies, split at steps > {cfg['level_split_m']} m ({int(barrier.sum()) * cell_m2 / 1e4:.2f} ha of barrier cells)")
    else:
        lab, n = ndimage.label(union, structure=S4)
    # bodies under the observation minimum are specks (barrier remnants, single noisy cells): dropped, ids packed
    area_px = ndimage.sum(lab > 0, lab, np.arange(1, n + 1))
    small = np.flatnonzero(area_px < min_px) + 1
    if small.size:
        lab[np.isin(lab, small)] = 0
        u = np.unique(lab); remap = np.zeros(int(lab.max()) + 1, np.int32); remap[u] = np.arange(len(u)); lab = remap[lab]; n = len(u) - 1
        log(f"  bodies under {cfg['min_m2']:.0f} m2 dropped: {small.size}; {n} remain")
    union = lab > 0
    ids = np.arange(1, n + 1); area_px = ndimage.sum(union, lab, ids)
    in_sea_set = np.zeros(n + 1, bool); in_sea_set[np.unique(lab[sea_cell & union])] = True
    edge = np.zeros(n + 1, bool); edge[np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]])] = True
    ice = np.zeros(union.shape, bool)
    cn = out / "class_now.tif"
    if cn.exists():
        c, cv = read_geo(str(cn), cg, cg.full(), "nearest")
        ice = cv & (np.rint(c) == CLASSES["ice"])
        ice = ndimage.binary_dilation(ice, structure=np.ones((3, 3), bool))
    ice_touch = np.zeros(n + 1, bool); ice_touch[np.unique(lab[ice])] = True
    # per body per survey: which observations, the level over the body, the dry fraction
    per = {int(i): dict(obs=[], levels=[]) for i in ids}
    # each survey's water level over each body first (the dry test below needs the OTHER surveys' levels)
    P50 = {}
    for L in layers:
        wm_ = (L["lab"] > 0) & (lab > 0)
        P50[L["name"]] = (ndimage.labeled_comprehension(np.nan_to_num(L["zw"], nan=-1e9), np.where(wm_, lab, 0), ids,
                                                       lambda v: np.median(v[v > -1e8]) if (v > -1e8).any() else np.nan, float, np.nan)
                          if wm_.any() else np.full(n, np.nan))
    for L in layers:
        others = [P50[k] for k in P50 if k != L["name"]]
        with np.errstate(all="ignore"):
            lv_other = np.nanmean(np.stack(others), axis=0) if others else np.full(n, np.nan)
        m = L["lab"] > 0
        step = int(L["lab"].max() + 1)
        pairs = np.unique(lab[m].astype(np.int64) * step + L["lab"][m])
        for pv in pairs:
            b, o = int(pv // step), int(pv % step)
            if b and o in L["table"]:
                per[b]["obs"].append(obs_key[(L["name"], o)])
        wm = m & (lab > 0)
        if wm.any():
            p50 = ndimage.labeled_comprehension(np.nan_to_num(L["zw"], nan=-1e9), np.where(wm, lab, 0), ids,
                                                lambda v: np.median(v[v > -1e8]) if (v > -1e8).any() else np.nan, float, np.nan)
            p10 = ndimage.labeled_comprehension(np.nan_to_num(L["zw"], nan=-1e9), np.where(wm, lab, 0), ids,
                                                lambda v: np.percentile(v[v > -1e8], 10) if (v > -1e8).any() else np.nan, float, np.nan)
            p90 = ndimage.labeled_comprehension(np.nan_to_num(L["zw"], nan=-1e9), np.where(wm, lab, 0), ids,
                                                lambda v: np.percentile(v[v > -1e8], 90) if (v > -1e8).any() else np.nan, float, np.nan)
        else:
            p50 = p10 = p90 = np.full(n, np.nan)
        seen = (L["cls"] > 0) & (lab > 0)
        # DRY = ground or bed observed AWAY from the water level: a survey's ground returns off the water (NIR
        # returns at the lake's own level, classed ground) are not evidence that the lake was dry (Bluff Point,
        # 2026-10-15: Homer 2018's ground at Hig's lake's level made it "ephemeral").  The reference level is
        # this survey's own water level over the body where it saw water, else the body's level from the others.
        land = seen & np.isin(L["cls"], [CLASSES["ground"], CLASSES["bed"]]) & np.isfinite(L["zl"])
        lv_own = p50 if wm.any() else np.full(n, np.nan)
        lv_ref = np.where(np.isfinite(lv_own), lv_own, lv_other)
        ref_r = np.full(n + 1, np.nan, np.float32); ref_r[1:] = lv_ref
        # ...and only ground or bed standing ABOVE the level is dry: a bed below it is the bed under the water (rule 3,
        # 2026-10-25: the multibeam's bed 65 m under Halibut Cove Lagoon made every sheet of its surface "ephemeral",
        # and the lagoon's stands at other tides were then judged as raised land)
        dz_ = L["zl"] - ref_r[lab]
        dry = land & (~np.isfinite(ref_r[lab]) | (dz_ > cfg["flat_tol_m"]))
        n_seen = ndimage.sum(seen, lab, ids); n_dry = ndimage.sum(dry, lab, ids); n_wet = ndimage.sum(wm, lab, ids)
        for k, i in enumerate(ids):
            if n_seen[k] == 0:
                continue
            per[int(i)]["levels"].append(dict(survey=L["name"], date=L["date"], season=L["season"], sigma_m=L["sigma"],
                                              level_m=None if not np.isfinite(p50[k]) else round(float(p50[k]), 2),
                                              level_p10_m=None if not np.isfinite(p10[k]) else round(float(p10[k]), 2),
                                              level_p90_m=None if not np.isfinite(p90[k]) else round(float(p90[k]), 2),
                                              sea_level_m=L["sea_level_m"], water_frac=round(float(n_wet[k] / n_seen[k]), 3),
                                              dry_frac=round(float(n_dry[k] / n_seen[k]), 3)))
    # stable ids: the previous build's bodies, matched by overlap
    prev_id = np.zeros(union.shape, np.int32); prev_max = 0
    pb = out / "water_bodies.tif"
    if pb.exists():
        pr, pv = read_geo(str(pb), cg, cg.full(), "nearest")
        prev_id = np.where(pv, np.rint(pr), 0).astype(np.int32); prev_max = int(prev_id.max())
    new_id = {}; taken = set()
    order = sorted(ids, key=lambda i: -area_px[i - 1])
    for i in order:
        m = lab == i
        cand = prev_id[m]; cand = cand[cand > 0]
        if cand.size:
            u, c = np.unique(cand, return_counts=True); j = int(u[np.argmax(c)]); ov = int(c.max())
            prev_px = int((prev_id == j).sum())
            if j not in taken and ov >= cfg["match_frac"] * min(prev_px, int(area_px[i - 1])):
                new_id[int(i)] = j; taken.add(j); continue
        prev_max += 1; new_id[int(i)] = prev_max
    if len(set(new_id.values())) != len(new_id):
        dup = [v for v in set(new_id.values()) if list(new_id.values()).count(v) > 1]
        log(f"  WARNING: duplicate body ids {dup[:10]} -- stable-id matching is wrong")
    # kinds
    bodies = {}
    for i in ids:
        i = int(i); P = per[i]
        lv = [x for x in P["levels"] if x["level_m"] is not None]
        sloped = any(x["level_p90_m"] is not None and x["level_p10_m"] is not None and (x["level_p90_m"] - x["level_p10_m"]) > cfg["flat_tol_m"] for x in lv)
        with_sea = [x for x in lv if x["sea_level_m"] is not None]
        is_sea = any(obs_key.get((L["name"], L["sea_id"])) in P["obs"] for L in layers if L["sea_id"])
        tidal = bool(with_sea) and all(abs(x["level_m"] - x["sea_level_m"]) <= cfg["perched_tol_m"] for x in with_sea)
        above = bool(with_sea) and all(x["level_m"] > x["sea_level_m"] + cfg["perched_tol_m"] for x in with_sea)
        max_dry = max([x["dry_frac"] for x in P["levels"]] or [0.0])
        levels = [x["level_m"] for x in lv]
        # sea / tidal by the SEA SET (cells at the sea's level that day), not by a survey's connected component:
        # the vendor class joins a lagoon pool to the sea across a TIN ramp, and the pool is not the sea
        if in_sea_set[i]: kind = "sea" if (is_sea or edge[i]) else "tidal"
        elif tidal: kind = "tidal"
        elif sloped and levels and max(levels) < cfg["tide_max_m"]: kind = "tidal"   # a creek draining across the flat: sloped, within the tide's reach
        elif sloped: kind = "river"
        elif max_dry > cfg["dry_frac"]: kind = "ephemeral"
        elif above and levels and max(levels) < cfg["tide_max_m"]: kind = "perched"
        else: kind = "lake"
        cons = None; rule = None
        if kind in FLAT_KINDS and lv:
            w = np.array([1.0 / x["sigma_m"] ** 2 for x in lv]); vals = np.array([x["level_m"] for x in lv])
            cons = float(np.sum(w * vals) / np.sum(w)); rule = f"1/sigma^2-weighted mean of {len(lv)} survey level{'s' if len(lv) > 1 else ''}"
        elif kind == "river" or (kind == "tidal" and sloped):
            rule = "per survey (a sloped surface, water_z.tif)"
        elif kind in ("sea", "tidal"):
            rule = "per date (the tide that day)"
        latest = max(lv, key=lambda x: years(x["date"])) if lv else None
        rr, cc = ndimage.center_of_mass(lab == i)
        x, y = cg.transform * (cc + 0.5, rr + 0.5)
        bodies[i] = dict(id=new_id[i], kind=kind, area_m2=int(area_px[i - 1] * cell_m2), n_surveys=len(P["levels"]),
                         touches_edge=bool(edge[i]), ice_contact=bool(ice_touch[i]),
                         level_lo_m=round(min(levels), 2) if levels else None, level_hi_m=round(max(levels), 2) if levels else None,
                         spread_m=round(max(levels) - min(levels), 2) if levels else None,
                         consensus_level_m=None if cons is None else round(cons, 2), consensus_rule=rule,
                         latest_level_m=latest["level_m"] if latest else None, latest_survey=latest["survey"] if latest else None,
                         sea_level_latest_m=latest["sea_level_m"] if latest else None,
                         dmax_m=None, dmax_method=None, dmax_cap_m=None,
                         hypsometry=None, edits=[], notes=[],
                         levels=P["levels"], observations=P["obs"], x=round(x), y=round(y))
    # ---- edits, by location
    edits = load_edits(edits_path) if edits_path else []
    used = []; unmatched = []
    if edits:
        _, (ni, nj) = ndimage.distance_transform_edt(lab == 0, return_indices=True)
        for e in edits:
            g = transform_geom("EPSG:4326", cg.crs, e["geom"]); pr = e["props"]
            hit = []
            gx = np.array(g["coordinates"][:2] if g["type"] == "Point" else [p for ring in (g["coordinates"] if g["type"] == "Polygon" else g["coordinates"][0]) for p in ring])
            gx = gx.reshape(-1, 2)
            x0_, y1_ = cg.transform * (0, 0); x1_, y0_ = cg.transform * (cg.width, cg.height)
            if gx[:, 0].max() < x0_ or gx[:, 0].min() > x1_ or gx[:, 1].max() < y0_ or gx[:, 1].min() > y1_:
                continue                                     # another region's edit: not this grid's business
            if g["type"] == "Point":
                c_, r_ = ~cg.transform * tuple(g["coordinates"][:2]); r_, c_ = int(r_), int(c_)
                if 0 <= r_ < lab.shape[0] and 0 <= c_ < lab.shape[1]:
                    j = int(lab[r_, c_])
                    if j == 0:                                   # on the shore: the nearest body within 50 m
                        rn, cn_ = ni[r_, c_], nj[r_, c_]
                        if np.hypot(rn - r_, cn_ - c_) * res <= 50.0:
                            j = int(lab[rn, cn_])
                    if j: hit = [j]
            else:
                m = features.rasterize([g], out_shape=lab.shape, transform=cg.transform, fill=0, default_value=1, dtype="uint8").astype(bool)
                u, c = np.unique(lab[m], return_counts=True)
                hit = [int(j) for j, k in zip(u, c) if j and k * cell_m2 >= 0.5 * min(bodies[int(j)]["area_m2"], int(m.sum() * cell_m2))]
            want = pr.get("kind")
            if not hit:
                unmatched.append(pr); continue
            # ...a contradiction is MARINE against FRESH (the pool in the sea); between fresh kinds -- a lake whose noisy
            # surface read sloped ("river"), Grewingk 2026-10-18 -- the edit is the better fact and applies
            def _clash(k_):
                return k_ != want and ((k_ in TIDAL_KINDS) != (want in TIDAL_KINDS))
            if want and any(_clash(bodies[j]["kind"]) for j in hit) and pr.get("force") is not True:
                # THE KIND GUARD: an edit whose kind contradicts the observations is a misplaced edit or a
                # changed world until a person says `force: true` -- it is reported, not applied (Seldovia,
                # 2026-10-15: the pool's Point landed in a body that had merged with the sea, and would have
                # re-kinded 105 ha of sea as a 1 m perched lagoon)
                for j in hit:
                    bodies[j]["notes"].append(f"edit {pr['id']} says {want}, the observations said {bodies[j]['kind']}: NOT applied (force: true to insist)")
                unmatched.append(dict(pr, conflict=[dict(body=bodies[j]["id"], kind=bodies[j]["kind"]) for j in hit])); continue
            for j in hit:
                b = bodies[j]
                if want: b["kind"] = want
                if pr.get("level_m") is not None:
                    b["consensus_level_m"] = float(pr["level_m"]); b["consensus_rule"] = f"edit {pr['id']}"
                if pr.get("dmax_m") is not None:
                    b["dmax_m"] = float(pr["dmax_m"]); b["dmax_method"] = f"edit {pr['id']}"
                if pr.get("hypsometry") is not None:
                    b["hypsometry"] = pr["hypsometry"]
                b["edits"].append(pr["id"])
                if pr.get("why"): b["notes"].append(f"{pr.get('who', '?')} {pr.get('date', '')}: {pr['why']}")
            used.append(dict(id=pr["id"], bodies=[bodies[j]["id"] for j in hit]))
    # ---- mapped water against the bodies: each body's mapped share, mapped polygons no body covers
    mapped_missed = []; unmapped = []
    if mapped:
        for i, b in bodies.items():
            m = lab == i
            u, c = np.unique(mp_w[m], return_counts=True)
            frac = float(c[u > 0].sum() / max(int(m.sum()), 1))
            b["mapped_frac"] = round(frac, 2)
            if frac >= 0.5:
                j = int(u[u > 0][np.argmax(c[u > 0])]); f = mapped[j - 1]
                b["mapped_kind"] = f["subkind"] or f["kind"]; b["mapped_name"] = f["name"]; b["mapped_by"] = f["id"]; b["_mapped_idx"] = j
                if b["kind"] in ("perched", "river") and b["mapped_kind"] in ("lake", "reservoir"):
                    # a mapped lake is a lake: "perched" (flat within the tide's reach) or "river" (the lidar's water
                    # surface not level within flat_tol_m -- Lower and Upper Hazel Lake, 2026-10-17: returns off a
                    # 50-90 ha lake spread 0.3 m, and a "river" gets no lake basin)
                    was = b["kind"]
                    b["notes"].append(f"kind lake by the mapped source {f['id']} (the observations said {was}: "
                                      + (f"flat at {b['level_hi_m']} m, within the tide's reach)" if was == "perched" else f"a surface sloped beyond flat_tol_m, levels {b['level_lo_m']}..{b['level_hi_m']} m)"))
                    b["kind"] = "lake"
                    if b.get("consensus_level_m") is None:
                        lv_ = [x for x in b.get("levels", []) if x.get("level_m") is not None]
                        if lv_:
                            w_ = np.array([1.0 / float(x.get("sigma_m") or 1.0) ** 2 for x in lv_]); v_ = np.array([float(x["level_m"]) for x in lv_])
                            b["consensus_level_m"] = round(float(np.sum(w_ * v_) / np.sum(w_)), 2)
                            b["consensus_rule"] = f"1/sigma^2-weighted mean of {len(lv_)} survey level{'s' if len(lv_) > 1 else ''} (a mapped lake)"
            elif b["kind"] not in ("sea", "tidal"):
                unmapped.append(dict(body=b["id"], kind=b["kind"], area_m2=b["area_m2"], x=b["x"], y=b["y"]))
        # ONE MAPPED LAKE, ONE LEVEL (2026-10-23, joint_v3 runs 10 and 14; Hig: "each lake one domain at its level,
        # berg tops folded in"): every flat body inside a mapped lake takes the level of the lake's largest surveyed
        # body. The others are the same lake at another stand (a pooled survey layer holds several dates: the 2021 and
        # June 2023 water at 49.4-50.9 m beside the August 2023 48.91), berg tops a water mask recoloured (52-65 m), or
        # a prior's hydroflattening where no survey returned from the water (3DEP's 41.3 m). Run 14 left body 18 at
        # 50.29 as its own pool: the fill modelled a shallow pool there and the sonar's lower bounds inside it pulled
        # 22 cells to -254 m. A lake only the prior saw keeps the prior's level; a body with edits keeps its own.
        from .axes import is_prior
        prior_names = {sp["name"] for sp in recipe.layers if is_prior(sp)}
        by_poly = {}
        for i, b in bodies.items():
            if b.get("_mapped_idx") is not None and b["mapped_kind"] in ("lake", "reservoir") and b["kind"] in FLAT_KINDS:
                by_poly.setdefault(b["_mapped_idx"], []).append(i)
        n_one = n_pri = 0
        for j, members in by_poly.items():
            def surveyed(b):
                return [x for x in b.get("levels", []) if x.get("level_m") is not None and x.get("survey") not in prior_names]
            lead = [i for i in members if surveyed(bodies[i]) and bodies[i].get("consensus_level_m") is not None]
            if not lead:
                continue
            li = max(lead, key=lambda i: bodies[i]["area_m2"]); L = bodies[li]
            for i in members:
                b = bodies[i]
                if i == li or b.get("consensus_level_m") is None or b.get("edits") or b["consensus_level_m"] == L["consensus_level_m"]:
                    continue
                was = b["consensus_level_m"]
                why = "a prior's alone" if not surveyed(b) else "another stand, a berg top or a hole's rim in a survey"
                b["consensus_level_m"] = L["consensus_level_m"]
                b["consensus_rule"] = f"the mapped lake's surveyed level (body {L['id']}; this body's own level, {was} m, was {why})"
                b["notes"].append(f"level {was} -> {L['consensus_level_m']} m: one mapped lake ({L.get('mapped_name')}), one level")
                n_one += 1; n_pri += 0 if surveyed(b) else 1
        if n_one:
            log(f"  one mapped lake, one level: {n_one} bodies take their lake's surveyed level ({n_pri} were a prior's alone)")
        for b in bodies.values():
            b.pop("_mapped_idx", None)
        pidx = np.unique(mp_w); pidx = pidx[pidx > 0]
        n_p = ndimage.sum(mp_w > 0, mp_w, pidx); n_cov = ndimage.sum(union, mp_w, pidx)
        for j, npx, ncv in zip(pidx, n_p, n_cov):
            if npx * cell_m2 >= 100.0 and ncv / npx < 0.2:
                f = mapped[j - 1]; rr_, cc_ = np.nonzero(mp_w == j); x_, y_ = cg.transform * (cc_.mean() + 0.5, rr_.mean() + 0.5)
                mapped_missed.append(dict(source=f["id"], kind=f["subkind"] or f["kind"], name=f["name"], area_m2=int(npx * cell_m2),
                                          covered=round(float(ncv / npx), 2), x=round(x_), y=round(y_)))
        log(f"  mapped vs bodies: {len(mapped_missed)} mapped polygons >= 100 m2 with < 20 % in any body "
            f"({sum(m_['area_m2'] for m_ in mapped_missed) / 1e4:.2f} ha); {len(unmapped)} lake/perched/river bodies not mapped")
    # ---- write
    id_r = np.zeros(lab.shape, np.int32)
    for i, b in bodies.items():
        id_r[lab == i] = b["id"]
    prof = dict(driver="GTiff", dtype="int32", count=1, width=cg.width, height=cg.height, crs=cg.crs, transform=cg.transform,
                compress="zstd", nodata=0, tiled=True)
    with rasterio.open(pb, "w", **prof) as ds:
        ds.write(id_r, 1)
    # ---- the INTERTIDAL ZONE (the tidal lens, module docstring): tidal-kind bodies, plus everything within
    # intertidal_near_m of ground or water any survey observed between low_tide_m and tide_max_m -- ground and
    # the composite from the diag build (class_now / <id>.tif), water from each survey's own surface (the
    # composite holds no value under water).  Read by below_level "intertidal" (a prior is void there).
    low_tide = cfg.get("low_tide_m")
    if low_tide is None:
        low_tide = float(recipe.d.get("fill", {}).get("low_tide_m", -2.9))
    # Anchored to observed GROUND (Hig's v7 review, 2026-10-16: a round patch of Kenai 2008 sea-surface returns
    # 300 m off Bluff Point had voided OCM's bed around itself): the zone is the ground any survey observed
    # between low_tide_m and tide_max_m, dilated by intertidal_near_m, plus every tidal-kind body that TOUCHES
    # that dilation -- the pool, a creek, the sheet along a beach.  A detached sheet of water far from any
    # exposed ground says nothing about flatness, and a prior under its ceiling stands.
    tidal_ids = [i for i, b in bodies.items() if b["kind"] in TIDAL_KINDS]
    near_src = np.zeros(union.shape, bool)
    zc_path = out / f"{recipe.id}.tif"
    if cn.exists() and zc_path.exists():
        c_, cv_ = read_geo(str(cn), cg, cg.full(), "nearest"); zc, zv = read_geo(str(zc_path), cg, cg.full(), "bilinear")
        c_ = np.where(cv_, np.rint(c_), 0)
        near_src = zv & np.isfinite(zc) & (c_ == CLASSES["ground"]) & (zc > low_tide) & (zc < cfg["tide_max_m"])
    # ...and only ground that is EXPOSED: ground observed inside a body the tide reaches is a bed or water-surface
    # return of another survey (Bluff Point v8: Homer 2018 "ground" at -1.25 m, above MLLW, scattered under Kenai
    # 2008's sea sheet, anchored the zone across the whole open bay)
    near_src &= ~np.isin(lab, tidal_ids)
    # ...and not ground LOWER than a tidal body's water surface observed within intertidal_near_m of it (Bluff Point
    # v8c, 2026-10-16: 15,000 cells of Homer 2018 and Kenai 2008 "ground" at -1.3..-0.8 m in the gaps and along the
    # edge of Kenai's -0.85 m sea sheet -- the sea is level, so ground below a surface seen 50 m away is under it,
    # a bed or a water-surface return; the beach above the sheet's level still anchors the zone).
    if near_src.any() and tidal_ids:
        lev_r = np.full(union.shape, np.nan, np.float32)
        for i in tidal_ids:
            lv = bodies[i].get("level_hi_m", bodies[i].get("latest_level_m"))
            if lv is not None and np.isfinite(lv):
                lev_r[lab == i] = lv
        has = np.isfinite(lev_r)
        if has.any():
            d, (iy, ix) = ndimage.distance_transform_edt(~has, sampling=res, return_indices=True)
            under = near_src & (d <= float(cfg["intertidal_near_m"])) & (zc < lev_r[iy, ix] + cfg["perched_tol_m"])
            if under.any():
                log(f"  ground below a tidal body's level within {float(cfg['intertidal_near_m']):.0f} m of it dropped as a zone anchor: "
                    f"{int(under.sum())} cells ({int(under.sum()) * cell_m2 / 1e4:.2f} ha)")
                near_src &= ~under
    # ...and an anchor is a PATCH of exposed ground, not a speck: patches under anchor_min_m2 (default the body
    # minimum, 400 m2) are dropped -- Bluff Point v9 (2026-10-16): 290 specks of Kenai 2008 "ground" at -0.5 m
    # (wave-crest returns above its -0.85 m sheet), 4-200 m2 each, scattered over the shelf, and each made a
    # 100 m disc of zone in which the lens flattened OCM's -4 m bed to the sheet: Hig's "very circular lump".
    if near_src.any():
        amin = float(cfg.get("anchor_min_m2", cfg["min_m2"]))
        la, na = ndimage.label(near_src, structure=S4)
        if na:
            sz = np.bincount(la.ravel(), minlength=na + 1) * cell_m2
            speck = np.isin(la, np.flatnonzero(sz < amin))
            speck &= near_src
            if speck.any():
                log(f"  zone anchors below {amin:g} m2 dropped as specks: {int((sz[1:] < amin).sum())} patches, {int(speck.sum())} cells")
                near_src &= ~speck
    with rasterio.open(out / "intertidal_anchors.tif", "w", **dict(prof, dtype="uint8", nodata=0)) as ds:
        ds.write(near_src.astype(np.uint8), 1)                 # the zone's anchors, for checking (2026-10-16)
    inter = np.zeros(union.shape, bool)
    if near_src.any() and float(cfg["intertidal_near_m"]) > 0:
        inter = ndimage.distance_transform_edt(~near_src, sampling=res) <= float(cfg["intertidal_near_m"])
    # ...the SEA body counts only through that dilation (Bluff Point v8: Kenai 2008's water_level sheet is 5.5 km2
    # of open bay touching the beach, and as a whole-body member it voided the priors under all of it); a tidal or
    # perched body touching the dilation counts whole (a pool, a creek, a lagoon -- small, and on the flat)
    # ...and "touching" means MOSTLY INSIDE (attach_frac, default 0.5): a sheet of sea-surface returns that does not
    # reach the box edge is "tidal", and at Bluff Point (v9f, 2026-10-16) eleven such sheets touching the beach
    # dilation attached whole and carried the zone offshore; a pool, a creek or a slough on the flat lies within
    # intertidal_near_m of ground over most of its area and still attaches.
    attach_frac = float(cfg.get("attach_frac", 0.5))
    # ...and a FLAT body within the tide's reach that lies mostly inside the zone is TIDAL water, not a lake (joint
    # pilot run 6, 2026-10-22, body 62: a 2.9 ha pond at 2.99 m on the Grewingk tide flat, seen by one survey, kinded
    # lake, took a lake basin prior and dug an 8 m hole with a sharp rim). Its level is the tide that day.
    for i, b in bodies.items():
        lv_hi = b.get("level_hi_m")
        if b["kind"] in ("lake", "ephemeral") and lv_hi is not None and low_tide < lv_hi < cfg["tide_max_m"]:
            cells = lab == i
            if cells.any() and float(inter[cells].mean()) >= attach_frac:
                b["notes"].append(f"kind {b['kind']} -> tidal: level {lv_hi} m within the tide's reach, {float(inter[cells].mean()):.0%} inside the intertidal zone")
                b["kind"] = "tidal"; b["consensus_level_m"] = None; b["consensus_rule"] = "per date (the tide that day)"
                if i not in tidal_ids: tidal_ids.append(i)
    attached = []
    for i in tidal_ids:
        if bodies[i]["kind"] == "sea":
            continue
        cells = lab == i
        if cells.any() and float(inter[cells].mean()) >= attach_frac:
            attached.append(i)
    inter |= np.isin(lab, attached)
    tidal_ids = attached
    with rasterio.open(out / "intertidal.tif", "w", **dict(prof, dtype="uint8", nodata=0)) as ds:
        ds.write(inter.astype(np.uint8), 1)
    inter_km2 = round(float(inter.sum()) * cell_m2 / 1e6, 4)
    log(f"  intertidal zone: {inter_km2} km2 = within {float(cfg['intertidal_near_m']):.0f} m of ground observed in ({low_tide}, "
        f"{cfg['tide_max_m']}) m + the {len(tidal_ids)} tidal/perched bodies touching that (the sea only within the band) -> intertidal.tif")
    polys = _polygons(lab, cg.transform, cg.crs, set(bodies))
    feats = [dict(type="Feature", id=b["id"], geometry=polys.get(i), properties=b) for i, b in sorted(bodies.items(), key=lambda kv: kv[1]["id"])]
    meta = dict(composite=recipe.id, built=_dt.datetime.now().isoformat(timespec="seconds"), res_m=res, grid_crs=str(cg.crs),
                params={k: v for k, v in cfg.items()}, surveys={L["name"]: dict(date=L["date"], season=L["season"], sigma_m=L["sigma"],
                                                                                 sea_level_m=L["sea_level_m"], method=L["method"],
                                                                                 n_bodies=len(L["table"])) for L in layers})
    (out / "water_bodies.geojson").write_text(json.dumps(dict(type="FeatureCollection", name="water_bodies", meta=meta, features=feats)))
    (out / "water_obs.geojson").write_text(json.dumps(dict(type="FeatureCollection", name="water_obs", meta=meta, features=obs_feats)))
    kinds = {}
    for b in bodies.values():
        k = kinds.setdefault(b["kind"], dict(n=0, km2=0.0)); k["n"] += 1; k["km2"] += b["area_m2"] / 1e6
    summ = dict(meta, kinds={k: dict(n=v["n"], km2=round(v["km2"], 4)) for k, v in kinds.items()}, bodies=len(bodies),
                edits_used=used, edits_unmatched=unmatched, mapped_missed=mapped_missed, bodies_unmapped=unmapped,
                intertidal_km2=inter_km2, tidal_bodies=len(tidal_ids),
                seconds=round(time.time() - t0, 1))
    (out / "water.json").write_text(json.dumps(summ, indent=1))
    log(f"  bodies {len(bodies)}: " + ", ".join(f"{k} {v['n']} ({v['km2']:.3f} km2)" for k, v in sorted(kinds.items()))
        + f"; edits used {len(used)}, unmatched {len(unmatched)}; {summ['seconds']}s -> {out / 'water_bodies.geojson'}")
    for b in bodies.values():
        for note in b["notes"]:
            log(f"    body {b['id']} ({b['kind']}, {b['area_m2'] / 1e4:.1f} ha): {note}")
    for pr in unmatched:
        log(f"    edit {pr.get('id')} " + (f"conflicts with the observed kind, not applied: {pr['conflict']}" if pr.get("conflict") else "matched no body"))
    return summ


# ------------------------------------------------------------- consumers

def load_bodies(out_dir, overrides=True):
    """(id raster path, {id: properties}) or None when the stage has not run.  With `overrides` the shoreline stage's
    corrections to the kinds and levels (shore_bodies.json: the sill test, a reviewed polygon's level) are applied, so
    the fill, build 3 and the water surface read the bodies as the shoreline stage judged them; the shoreline stage
    itself reads them raw (2026-10-28, the flat-water rule: WATER_REVIEW_PLAN.md section 1)."""
    out = Path(out_dir)
    if not (out / "water_bodies.tif").exists() or not (out / "water_bodies.geojson").exists():
        return None
    gj = json.loads((out / "water_bodies.geojson").read_text())
    tab = {int(f["properties"]["id"]): f["properties"] for f in gj["features"]}
    ov = out / "shore_bodies.json"
    if overrides and ov.exists():
        for k, o in (json.loads(ov.read_text()).get("overrides") or {}).items():
            if int(k) in tab:
                tab[int(k)] = dict(tab[int(k)], **{kk: vv for kk, vv in o.items() if kk in ("kind", "consensus_level_m")})
    return str(out / "water_bodies.tif"), tab


def intertidal_raster(out_dir, grid: Grid, win):
    """The intertidal zone (intertidal.tif, the tidal lens) on the caller's window as a bool array; None
    when the stage has not run."""
    p = Path(out_dir) / "intertidal.tif"
    if not p.exists():
        return None
    a, v = read_geo(str(p), grid, win, "nearest")
    return v & (np.rint(a) > 0)


def fresh_raster(out_dir, grid: Grid, win):
    """Cells inside a body that is NOT marine (lake / river / ephemeral: its level is its own, above the tide):
    a regional topobathy PRIOR (NCEI, OCM) holds its hydroflattened surface there, never a bed (2026-10-17,
    whole-bay v3: NCEI's flat won 90 % of China Poot Lake as "bed" a few cm under the lidar's water, Turquoise
    and Wosnesenski got a shelf with holes).  None when there are no bodies."""
    B = load_bodies(out_dir)
    if B is None:
        return None
    path, tab = B
    ids, v = read_geo(path, grid, win, "nearest")
    ids = np.where(v, np.rint(ids), 0).astype(np.int32)
    fresh_ids = [i for i, p in tab.items() if p["kind"] not in TIDAL_KINDS]
    return np.isin(ids, fresh_ids) if fresh_ids else np.zeros(ids.shape, bool)


def level_raster(out_dir, grid: Grid, win, default):
    """(ceiling, floor): the bed ceiling is a flat body's consensus level inside it, `default` elsewhere
    (the sea's low tide); the floor is level - dmax - slack where an EDIT set the body's depth (NaN
    elsewhere) -- a hand fact about a lake's depth outranks a poor observation under it (the Seldovia
    lagoon: OCM's interpolated -2.4 m under a pool Hig knows to be ~1 m), never a good one: the caller
    applies the floor only to layers whose sigma is at least `floor_sigma_m`."""
    shape = (win.height, win.width)
    lv = np.full(shape, np.nan if default is None else float(default), np.float32)
    fl = np.full(shape, np.nan, np.float32)
    B = load_bodies(out_dir)
    if B is None:
        return lv, fl
    path, tab = B
    ids, v = read_geo(path, grid, win, "nearest")
    ids = np.where(v, np.rint(ids), 0).astype(np.int32)
    for i, p in tab.items():
        if p["kind"] in FLAT_KINDS and p.get("consensus_level_m") is not None:
            m = ids == i
            lv[m] = float(p["consensus_level_m"])
            d = p.get("dmax_m") if p.get("dmax_m") is not None else p.get("dmax_cap_m")   # an edit's depth (dmax_cap_m: older files)
            if d is not None:
                d = float(d); fl[m] = float(p["consensus_level_m"]) - d - max(0.5, 0.5 * d)
    return lv, fl
