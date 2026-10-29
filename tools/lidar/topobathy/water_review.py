"""THE WATER REVIEW (2026-10-28, WATER_REVIEW_PLAN.md sections 3-4): what the automatic run leaves for a person to decide.

Hig (2026-10-28): "If the algorithm identifies above-water points that may be real, it should flag these in a way that
facilitates human review. The overall flow could be: initial water polygon identification based on available mapping and
point cloud interpretation, initial fully automated topobathy generation treating these as priors that haven't been
hand-checked, human review of water body polygons inviting revisions, final topobathy processing."

Reads a full run's out/ (after build 3: <id>.tif, shore_label, fill_water_level, class_now, source_index, water_bodies +
shore_bodies.json, fill_ceiling, fill_mask) and the recipe's mapped water.  Writes two files for QGIS, EPSG:4326:

  out/water_flags.geojson     one feature per thing to decide, a polygon around it.  Properties: id (stable: kind +
                              centroid to 10 m, so a decision survives the next run while the thing is still there), kind,
                              the numbers, `suggested` (what the automatic run did, and stands while nobody decides) and an
                              empty `disposition` for the reviewer.
  out/polygons_auto.geojson   W1: the water as the run used it, one polygon per body (and per levelled patch with no body
                              over min_m2): kind, level(s), the surveys' levels, sill, the mapped polygon it matches and how
                              well (`agree`), status "auto".

Flag kinds (first-pass thresholds, Hig 2026-10-28: "flag anything over 100 m^2 or 1 m vertical"):

  above_water_obs   observed cells inside the water (labelled water/floating, or inside a trusted mapped polygon or a
                    water stamp) standing more than sigma_m over the level, as clusters, not touching land outside the
                    water; flagged at min_m2 or more, or max_dz_m or more over the level
  ceiling_bound     modelled bed the ceiling had to hold (fill_ceiling: 1 by sites in the fit, 2 clipped by the guard)
  level_conflict    a flat body whose surveys' levels disagree by more than level_tol_m (the priors' levels left out):
                    a lake can stand at more than one level, and a survey that caught it low saw more of its shore
  sill_fail         a perched or low-lying body joined to the open sea below the lowest tide (shore.py's sill test)
  edge_mismatch     a trusted mapped polygon against the run: observed land inside it touching land outside, or water
                    outside it joined to its water, min_m2 or more
  ice_face          where water meets the glacier, per stretch of face_min_m or more

Dispositions the reviewer writes (the file saved as tools/lidar/water/review.geojson; shore.py reads it):
  island        the observation stands: the cells are land, an island inside the water
  noise         the observation is void inside the water: open space for the interpolation
  polygon_edit  the polygon was redrawn instead (tools/lidar/water/polygons.geojson)
  defer         left for later; the suggestion stands

    python topobathy/water_review.py RECIPE [--out-dir DIR]
"""
from __future__ import annotations
import json, sys, time, hashlib
from pathlib import Path
import numpy as np
import rasterio
from rasterio import features
from rasterio.warp import transform_geom
from scipy import ndimage

DEFAULTS = dict(sigma_m=0.5, min_m2=100.0, max_dz_m=1.0, min_cells=2, level_tol_m=0.3, face_min_m=50.0,
                poly_min_m2=400.0, simplify_m=2.0, agree_prior=0.5, prior_names=("ctx3dep",))
S8 = np.ones((3, 3), bool)
LBL = dict(none=0, land=1, water=2, floating=3, glacier=4)


def _read(p, dtype=np.float32):
    if not Path(p).exists():
        return None
    with rasterio.open(p) as s:
        a = s.read(1)
        if dtype is np.float32:
            a = a.astype(np.float32)
            if s.nodata is not None:
                a[a == s.nodata] = np.nan
            return a
        return a.astype(dtype)


def _fid(kind, x, y):
    return f"{kind}-" + hashlib.sha1(f"{kind}:{round(x, -1):.0f}:{round(y, -1):.0f}".encode()).hexdigest()[:10]


class Vec:
    """Cluster outlines in EPSG:4326 from a label raster's slices."""
    def __init__(self, prof, res, simplify_m):
        self.T = prof["transform"]; self.crs = prof["crs"]; self.res = res; self.tol = simplify_m

    def outline(self, mask, sl):
        from shapely.geometry import shape, mapping
        from shapely.ops import unary_union
        r0, c0 = sl[0].start, sl[1].start
        T = self.T * rasterio.Affine.translation(c0, r0)
        polys = [shape(g) for g, v in features.shapes(mask.astype(np.uint8), mask=mask, transform=T, connectivity=8) if v == 1]
        if not polys:
            return None
        g = unary_union(polys).simplify(self.tol, preserve_topology=True)
        return transform_geom(self.crs, "EPSG:4326", mapping(g))

    def centre(self, sl, mask):
        rr, cc = np.nonzero(mask)
        return self.T * (float(cc.mean()) + sl[1].start + 0.5, float(rr.mean()) + sl[0].start + 0.5)


def _clusters(mask, res, min_cells=1):
    lab, n = ndimage.label(mask, structure=S8)
    objs = ndimage.find_objects(lab)
    for i, sl in enumerate(objs, start=1):
        if sl is None:
            continue
        sl = tuple(slice(max(s.start - 1, 0), min(s.stop + 1, N)) for s, N in zip(sl, mask.shape))
        m = lab[sl] == i
        if m.sum() >= min_cells:
            yield i, sl, m, lab


def run(recipe, out_dir=None, log=print):
    cfg = dict(DEFAULTS); cfg.update((recipe.d.get("water_review") or {}))
    out = Path(out_dir) if out_dir else recipe.out_dir
    t0 = time.time()
    with rasterio.open(out / f"{recipe.id}.tif") as s:
        prof = s.profile; res = float(s.res[0])
        z = s.read(1).astype(np.float32); z[z == s.nodata] = np.nan
    lab = _read(out / "shore_label.tif", np.uint8)
    lev = _read(out / "fill_water_level.tif")
    if lev is None:
        lev = _read(out / "shore_level.tif")
    si = _read(out / "source_index.tif", np.int32)
    cls = _read(out / "class_now.tif", np.uint8)
    ceil = _read(out / "fill_ceiling.tif", np.uint8)
    from topobathy.water import load_bodies, load_mapped, FLAT_KINDS, TIDAL_KINDS
    from topobathy.shore import read_on_grid
    from topobathy.axes import CLASSES, is_prior
    B = load_bodies(out)
    bid = read_on_grid(B[0], prof, np.int32) if B else np.zeros(z.shape, np.int32)
    btab = B[1] if B else {}
    sb = json.loads((out / "shore_bodies.json").read_text()) if (out / "shore_bodies.json").exists() else {}
    raw = recipe.d["layers"]
    not_obs = [k for k, L in enumerate(raw) if L.get("optional") or is_prior(L)]
    names = {k: L.get("name") or L.get("id") or str(k) for k, L in enumerate(raw)}
    obs = np.isfinite(z) & ~np.isin(si, not_obs) if si is not None else np.isfinite(z)
    lo_t = (recipe.d.get("fill") or {}).get("low_tide_m")
    # the trusted mapped water, the water stamps (as the shoreline stage reads them)
    mp = np.zeros(z.shape, bool); mpid = np.zeros(z.shape, np.int32); mi = np.zeros(z.shape, bool); feats = []
    wcfg = recipe.d.get("water") or {}
    if wcfg.get("mapped"):
        feats = load_mapped(wcfg["mapped"], prof["crs"])
        tr = [(f["geom"], i + 1) for i, f in enumerate(feats) if f["kind"] in ("lake", "river") and f.get("edge") == "trusted"]
        isl = [(f["geom"], 1) for f in feats if f["kind"] == "island"]
        if tr:
            mpid = features.rasterize(tr, out_shape=z.shape, transform=prof["transform"], fill=0, dtype="int32"); mp = mpid > 0
        if isl:
            mi = features.rasterize(isl, out_shape=z.shape, transform=prof["transform"], fill=0, dtype="uint8").astype(bool)
    stamp = np.zeros(z.shape, bool)
    for sp_ in (recipe.d.get("water_stamps") or []):
        sp_ = Path(str(recipe.resolve(sp_)))
        if sp_.exists():
            from rasterio.warp import reproject, Resampling
            st_ = np.zeros(z.shape, np.uint8)
            with rasterio.open(sp_) as d_:
                reproject(rasterio.band(d_, 1), st_, dst_transform=prof["transform"], dst_crs=prof["crs"], resampling=Resampling.nearest, dst_nodata=0)
            stamp |= st_ == 1
    wet = np.isin(lab, (LBL["water"], LBL["floating"]))
    # THE SEA HAS NO LEVEL OF ITS OWN (the flat-water rule): what a flight saw over it is a stand of the tide, and ground
    # above that stand is a flat the tide covers at another hour -- the sea's cells take no above-water or edge flags
    tide_max = float((recipe.d.get("shore") or {}).get("tide_max_m", 4.5))
    tid_ids = [int(k) for k, b in btab.items() if b.get("kind") in TIDAL_KINDS]
    sea_w = wet & ((np.isin(bid, tid_ids) if tid_ids else False) | ((bid == 0) & np.isfinite(lev) & (lev <= tide_max)))
    inwater = (wet | (mp & ~mi) | stamp) & ~(lab == LBL["glacier"]) & ~sea_w
    V = Vec(prof, res, float(cfg["simplify_m"]))
    flags = []

    def add(kind, sl, m, props, suggested):
        g = V.outline(m, sl)
        if g is None:
            return
        x, y = V.centre(sl, m)
        lon, lat = transform_geom(prof["crs"], "EPSG:4326", dict(type="Point", coordinates=(x, y)))["coordinates"]
        flags.append(dict(type="Feature", geometry=g, properties=dict(
            id=_fid(kind, x, y), kind=kind, lat=round(lat, 6), lon=round(lon, 6), x=round(x), y=round(y),
            area_m2=int(m.sum() * res * res), **props, suggested=suggested, disposition="")))

    # the level that applies near water: the cell's own, else the nearest levelled water's
    has = np.isfinite(lev) & wet
    if has.any():
        _, (li, lj) = ndimage.distance_transform_edt(~has, return_indices=True)
        Ln = np.where(has, lev, lev[li, lj]).astype(np.float32); del li, lj
    else:
        Ln = np.full(z.shape, np.nan, np.float32)
    dz = z - Ln
    # ---- above_water_obs and edge_mismatch (land inside a polygon touching land outside)
    up = obs & inwater & np.isfinite(dz) & (dz > float(cfg["sigma_m"]))
    land_out = ~inwater & np.isin(lab, (LBL["land"], LBL["glacier"]))
    sm2 = float(cfg["min_m2"]); n_aw = n_em = 0
    for i, sl, m, L in _clusters(up, res, int(cfg["min_cells"])):
        a_m2 = m.sum() * res * res; dmax = float(np.nanmax(dz[sl][m]))
        touch = bool((ndimage.binary_dilation(m, S8) & land_out[sl]).any())
        lab_c = lab[sl][m]
        frac = {k: round(float((lab_c == v).mean()), 2) for k, v in LBL.items() if (lab_c == v).any()}
        srcs = np.unique(si[sl][m]) if si is not None else []
        icy = float((cls[sl][m] == CLASSES.get("ice", -1)).mean()) if cls is not None else 0.0
        props = dict(dz_max_m=round(dmax, 2), dz_p50_m=round(float(np.nanmedian(dz[sl][m])), 2),
                     level_m=round(float(np.nanmedian(Ln[sl][m])), 2), labels=frac, ice_frac=round(icy, 2),
                     sources=[names.get(int(k), str(k)) for k in srcs][:6],
                     in_mapped=bool((mp[sl] & m).any()), in_stamp=bool((stamp[sl] & m).any()))
        if touch:
            if a_m2 >= sm2 and (mp[sl] & m).any():
                add("edge_mismatch", sl, m, dict(props, side="land inside the mapped polygon"), "the run's cut (land where observed)")
                n_em += 1
            continue
        if a_m2 < sm2 and dmax < float(cfg["max_dz_m"]):
            continue
        if frac.get("land", 0) > 0.5:
            sug = "island (the run kept it as land)"
        elif frac.get("floating", 0) > 0.5 or icy > 0.5:
            sug = "noise (floating ice: no bed)"
        else:
            sug = "noise (labelled water: the run took the water over it)"
        add("above_water_obs", sl, m, props, sug); n_aw += 1
    log(f"  above-water observations: {n_aw} flagged (>= {sm2:g} m2 or >= {cfg['max_dz_m']} m over the level); "
        f"land inside a mapped polygon at its edge: {n_em}")
    # ---- edge_mismatch, the other side: water outside a trusted polygon joined to the polygon's water
    if mp.any():
        outw = wet & ~mp & ~stamp & ~sea_w
        near = ndimage.binary_dilation(mp & wet, S8, iterations=2)
        n2 = 0
        for i, sl, m, L in _clusters(outw, res):
            if m.sum() * res * res < sm2 or not (near[sl] & m).any():
                continue
            add("edge_mismatch", sl, m, dict(side="water outside the mapped polygon", level_m=round(float(np.nanmedian(lev[sl][m])), 2)
                                             if np.isfinite(lev[sl][m]).any() else None), "the run's water (the polygon is older)")
            n2 += 1
        log(f"  water outside a mapped polygon, joined to it: {n2}")
    # ---- ceiling_bound
    if ceil is not None and (ceil > 0).any():
        n_c = 0
        for i, sl, m, L in _clusters(ceil > 0, res):
            if m.sum() * res * res < sm2:
                continue
            c_ = ceil[sl][m]
            add("ceiling_bound", sl, m, dict(guard_frac=round(float((c_ == 2).mean()), 2),
                                             depth_p50_m=round(float(np.nanmedian((lev - z)[sl][m])), 2)),
                "the bed held under the water (check the polygon: land there? or the prior too shallow)")
            n_c += 1
        log(f"  ceiling held the bed: {n_c} areas")
    # ---- level_conflict, sill_fail: per body
    objs = ndimage.find_objects(bid) if bid.max() > 0 else []
    pri = set(cfg["prior_names"])
    def body_mask(k):
        if k - 1 >= len(objs) or objs[k - 1] is None:
            return None, None
        sl = tuple(slice(max(s.start - 1, 0), min(s.stop + 1, N)) for s, N in zip(objs[k - 1], bid.shape))
        return sl, bid[sl] == k
    n_l = 0
    for k, b in btab.items():
        if b.get("kind") not in FLAT_KINDS:
            continue
        lv = [l for l in (b.get("levels") or []) if l.get("survey") not in pri and l.get("level_m") is not None]
        if len(lv) < 2:
            continue
        v = [float(l["level_m"]) for l in lv]
        tol = max(float(cfg["level_tol_m"]), 2 * max(float(l.get("sigma_m") or 0) for l in lv))
        if max(v) - min(v) <= tol:
            continue
        sl, m = body_mask(int(k))
        if sl is None:
            continue
        add("level_conflict", sl, m, dict(body=int(k), kind=b.get("kind"), consensus_level_m=b.get("consensus_level_m"),
                                          levels=[dict(survey=l["survey"], date=l.get("date"), level_m=l["level_m"]) for l in lv],
                                          mapped_name=b.get("mapped_name")),
            f"one level, {b.get('consensus_level_m')} m ({b.get('consensus_rule') or 'consensus'}); a lake may hold more than one")
        n_l += 1
    n_s = 0
    for k, r in (sb.get("sills") or {}).items():
        if r.get("sill_below_m") is None or lo_t is None or r["sill_below_m"] > float(lo_t):
            continue
        sl, m = body_mask(int(k))
        if sl is None:
            continue
        add("sill_fail", sl, m, dict(body=int(k), was_kind=r.get("kind"), level_m=r.get("level_m"), mapped=r.get("mapped")),
            "the sea (re-kinded tidal)" if r.get("rekinded") else "kept (a mapped body): check its outlet")
        n_s += 1
    log(f"  levels that disagree: {n_l} bodies; bodies joined to the open sea below the lowest tide: {n_s}")
    # ---- ice_face
    gl = lab == LBL["glacier"]
    face = wet & ndimage.binary_dilation(gl, S8)
    n_f = 0
    for i, sl, m, L in _clusters(ndimage.binary_dilation(face, S8, iterations=2) & (wet | gl), res):
        if m.sum() * res < 3 * float(cfg["face_min_m"]):           # a band ~3 cells wide: area / width ~ length
            continue
        add("ice_face", sl, m, dict(length_m=int(m.sum() * res / 3)), "the run's face (2023 where observed)")
        n_f += 1
    log(f"  ice faces: {n_f} stretches")
    # ---- polygons_auto: the water as the run used it
    polys = []
    agree_by = {}
    for k, b in btab.items():
        k = int(k)
        sl, m = body_mask(k)
        if sl is None:
            continue
        m = m & wet[sl]
        if m.sum() * res * res < float(cfg["poly_min_m2"]):
            continue
        g = V.outline(m, sl)
        if g is None:
            continue
        lv_ = lev[sl][m]; lv_ = lv_[np.isfinite(lv_)]
        mid = mpid[sl][m]; mid = mid[mid > 0]
        agree = None; mname = None
        if mid.size:
            j = int(np.bincount(mid).argmax()); pm = mpid[sl] == j
            inter = float((pm & m).sum()); uni = float((pm | m).sum())
            agree = round(inter / uni, 2) if uni else None; mname = feats[j - 1].get("name") or feats[j - 1].get("id")
        polys.append(dict(type="Feature", geometry=g, properties=dict(
            body=k, kind=b.get("kind"), status="auto",
            level_m=round(float(np.median(lv_)), 2) if lv_.size and b.get("kind") in FLAT_KINDS else None,
            level_range_m=[round(float(lv_.min()), 2), round(float(lv_.max()), 2)] if lv_.size else None,
            levels=[dict(survey=l["survey"], date=l.get("date"), level_m=l.get("level_m")) for l in (b.get("levels") or [])
                    if l.get("survey") not in pri],
            area_m2=int(m.sum() * res * res), mapped=mname, agree=agree,
            basis="mapped polygon + lidar (a prior)" if (agree or 0) >= float(cfg["agree_prior"]) else ("lidar" if not mname else "lidar; the mapped polygon disagrees"),
            sill_below_m=(sb.get("sills") or {}).get(str(k), {}).get("sill_below_m"), note="")))
    log(f"  automatic polygons: {len(polys)} bodies ({sum(1 for p in polys if (p['properties']['agree'] or 0) >= cfg['agree_prior'])} "
        f"agree with a mapped polygon at IoU >= {cfg['agree_prior']})")
    for name, fs in (("water_flags.geojson", flags), ("polygons_auto.geojson", polys)):
        (out / name).write_text(json.dumps(dict(type="FeatureCollection", name=name.split(".")[0], features=fs)))
    by = {}
    for f in flags:
        by[f["properties"]["kind"]] = by.get(f["properties"]["kind"], 0) + 1
    rep = dict(flags=by, polygons=len(polys), seconds=round(time.time() - t0, 1), params={k: v for k, v in cfg.items()})
    (out / "water_review.json").write_text(json.dumps(rep, indent=1))
    log(f"  flags: {by}; {rep['seconds']} s -> {out / 'water_flags.geojson'}, {out / 'polygons_auto.geojson'}")
    return rep


if __name__ == "__main__":
    import argparse
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from topobathy.recipe import Recipe
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recipe"); ap.add_argument("--out-dir")
    a = ap.parse_args()
    run(Recipe(a.recipe), a.out_dir)
