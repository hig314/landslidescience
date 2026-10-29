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


def is_prior(spec) -> bool:
    """A PRIOR layer: one that stands in only where no survey has a cell -- a `fallback` class step (3DEP), or
    `_prior` set on the layer. Its water level is no evidence anywhere (2026-10-23: three places tested the layer
    dict for a `fallback` KEY, which is a class step, so none of them ever fired)."""
    if spec.get("_prior"):
        return True
    cs = spec.get("class")
    return isinstance(cs, list) and any(isinstance(st, dict) and "fallback" in st for st in cs)
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
        # Hig, 2026-10-10: "water is not a real surface - it's topobathy after all".  A water
        # surface never stands in for the bed; where only water was observed the composite is a
        # hole (its class stays water: bed unknown, the place for the bathymetry interpolation).
        # A water-surface product is a separate thing, in the spirit of glacier-surface evolution.
        "water": {"bed": 1, "subglacial": 1, "modelled": 0.5},
        "bed":   {"bed": 1, "subglacial": 1, "modelled": 0.5},
        "void":  {"bed": 1, "subglacial": 1, "modelled": 0.5},   # the newest survey saw nothing (the laser got no returns): a bed observation is still the best there is, else a modelled surface stands in
        "ground": {"ground": 1, "snow": 0.5, "bed": 0.5, "built": 0.5},
        "snow":  {"ground": 1, "snow": 0.5},
        "ice":   {"subglacial": 1, "modelled": 0.5, "ice": 0.25},
        "subglacial": {"subglacial": 1, "modelled": 0.5, "ice": 0.25},
        # canopy (2026-10-15, Seldovia Bay): the newest survey saw a canopy -- trees over ground, or
        # KELP over the seabed in a green lidar -- so the bare surface under it is a ground or a bed
        # observation from any survey; without "bed" here the multibeam under kelp was gated out
        "canopy": {"ground": 1, "bed": 1, "snow": 0.5},
        # built (2026-10-10): the KBay reclass excludes roofs and planes the TIN under them, so a
        # 'built' observation's elevation is interpolated ground -- a half-rank stand-in, like snow
        "built": {"ground": 1, "built": 0.5},
    },
    "top_surface": {c: {"ground": 1, "water": 1, "ice": 1, "canopy": 1, "built": 1, "snow": 1, "bed": 0}
                    for c in CLASSES},
}
DEFAULT_RATES = {"ground": 0.01, "water": 0.0, "bed": 0.02, "ice": 3.0,
                 "subglacial": 0.0, "canopy": 0.3, "built": 0.1, "void": 0.0, "modelled": 0.0, "snow": 0.0}
SNOW_SIGMA_M = 1.5             # a snow surface is ground plus an unknown 0..few m: treated as this much error
CEIL_TOL_M = 0.2               # a water observation is a ceiling: a non-bed value within this of its surface, or above, is evicted
SIGMA_BANDS = [0.1, 0.5, 2.0, 99.0]      # hand quality paint: good / ok / poor / veto (metres)

# Hand corrections to an observation's class and quality are two-band paint
# rasters like the weight paint (value, alpha), stored beside the observation
# in <work_dir>/obs/<layer>/{class,sigma}_paint.tif.  Value encodes the class
# code / 8, or the SIGMA_BANDS index / 8, so the same 8-bit files and the same
# tools serve all three axes.  Both the build and the mask tool apply them
# through these two functions, so they cannot disagree.

def apply_class_paint(cls, value, alpha):
    on = alpha > 0.5
    if on.any():
        cls = cls.copy()
        cls[on] = np.rint(value[on] * 8).astype(np.uint8)
    return cls


def apply_sigma_paint(sigma, value, alpha):
    on = alpha > 0.5
    if on.any():
        sigma = sigma.copy()
        band = np.clip(np.rint(value[on] * 8), 0, len(SIGMA_BANDS) - 1).astype(int)
        sigma[on] = np.array(SIGMA_BANDS, np.float32)[band]
    return sigma


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


def class_raster(spec, z, valid, below, res_m, info, others=None, grid=None, win=None, others_cls=None):
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
                # "bodies": "$out/" (2026-10-15, the Seldovia lagoon): inside a lake or a perched lagoon
                # the cutoff is the BODY's own level (water.py, stage B), not the sea's low tide -- a
                # grid's -0.3 m under water ponded at +0.8 is bed, however far above MLLW it stands
                lv = level
                if kw.get("bodies") and grid is not None and win is not None:
                    from .water import level_raster
                    lv, fl = level_raster(kw["bodies"], grid, win, level)
                c[valid & (z < lv)] = CLASSES[kw.get("as", "bed")]
                c[valid & (z >= lv)] = CLASSES[kw.get("else", "void")]
                if kw.get("bodies") and grid is not None and win is not None and np.isfinite(fl).any() \
                        and float(spec.get("sigma_m", 1.0)) >= float(kw.get("floor_sigma_m", 1.0)):
                    # a hand-set depth is a FLOOR for poor data: this layer's values below it are not bed
                    # (void), a better survey's (sigma under floor_sigma_m) still stand
                    under = valid & np.isfinite(fl) & (z < fl) & (c == CLASSES[kw.get("as", "bed")])
                    if under.any():
                        c[under] = CLASSES["void"]
                        info["below_floor_fraction"] = float(under[valid].mean()) if valid.any() else 0.0
                if kw.get("fresh") and kw.get("bodies") and grid is not None and win is not None:
                    # inside a FRESH body (a lake: its level above the tide) a regional prior is no bed at all --
                    # its value there is its own hydroflattening (water.fresh_raster, 2026-10-17)
                    from .water import fresh_raster
                    fr = fresh_raster(kw["bodies"], grid, win)
                    if fr is not None:
                        sel = valid & fr & (c == CLASSES[kw.get("as", "bed")])
                        if sel.any():
                            c[sel] = CLASSES[kw["fresh"]]
                            info["fresh_fraction"] = float(sel[valid].mean()) if valid.any() else 0.0
                if kw.get("intertidal") and kw.get("bodies") and grid is not None and win is not None:
                    # THE TIDAL LENS (water.py, Hig 2026-10-16): inside the intertidal zone this layer -- a gridded
                    # PRIOR (OCM's satellite-derived bathymetry, NCEI's interpolation) -- is no bed: the observed flat
                    # is the surface until a measurement says deeper.  Seldovia v6: a prior's -3.3 m showed through a
                    # return hole on a mudflat at +0.66, -2.7 under a tidal creek at +0.3, -3.6 along a beach at -0.03.
                    from .water import intertidal_raster
                    im = intertidal_raster(kw["bodies"], grid, win)
                    if im is not None:
                        sel = valid & im & (c == CLASSES[kw.get("as", "bed")])
                        if sel.any():
                            c[sel] = CLASSES[kw["intertidal"]]
                            info["intertidal_fraction"] = float(sel[valid].mean()) if valid.any() else 0.0
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
            # the same detector classes a lidar's sea surface over a known bed: {"snow_vs": {"ref": "bathy",
            # "dz_m": 1.5, "rough_ratio": 1.0, "as": "water"}} - higher than the reference, flat
            as_ = CLASSES[kw.get("as", "snow")]
            # `only`: which class the detector may recolour (default ground).  Kelp in a green lidar
            # (2026-10-15, Seldovia Bay): {"snow_vs": {"ref": "bathy2009", "dz_m": 1, "rough_ratio": 99,
            # "only": "bed", "as": "canopy"}} -- a BED cell standing over the multibeam's bed is canopy
            c[sn & (c == CLASSES[kw.get("only", "ground")])] = as_
            info["snow_fraction" if as_ == CLASSES["snow"] else CLASS_NAMES[as_] + "_vs_fraction"] = float(sn[valid].mean()) if valid.any() else 0.0
        elif name == "islets":
            # specks of "ground" in open water are returns off its surface (2026-10-11: single
            # KBay cells over China Poot Lake stood as 50 m spikes from a 30 m-deep basin, and as
            # the newest survey's ground they kept the fill out).  A ground component under
            # max_m2 whose ring (ring_m wide) is >= ring_frac water or void becomes `as`.
            g = valid & (c == CLASSES["ground"])
            lab, nl = ndimage.label(g)
            if nl:
                cnt = np.bincount(lab.ravel(), minlength=nl + 1)
                small = cnt * res_m * res_m <= kw.get("max_m2", 50.0); small[0] = False
                cand = small[lab]
                r = max(int(round(kw.get("ring_m", 3.0) / res_m)), 1)
                ring = ndimage.binary_dilation(cand, iterations=r) & ~cand
                wetv = ring & ((c == CLASSES["water"]) | ~valid | (c == CLASSES["void"]) | (c == CLASSES["bed"]))   # bed = under water (2026-10-16)
                lr = ndimage.maximum_filter(np.where(cand, lab, 0), size=2 * r + 1)
                nr = np.bincount(lr[ring].ravel(), minlength=nl + 1); nw = np.bincount(lr[wetv].ravel(), minlength=nl + 1)
                ok = small & (nw >= kw.get("ring_frac", 0.8) * np.maximum(nr, 1)) & (nr > 0)
                hit = ok[lab] & g
                c[hit] = CLASSES[kw.get("as", "water")]
                info["islets_fraction"] = float(hit[valid].mean()) if valid.any() else 0.0
        elif name == "blind_water":
            # the shoreline stage's WATER-BLIND cells (water_blind.tif beside the bodies: cells of this layer that only
            # water-blind sources won, inside the water the stage labelled) are no observation: void, so the fill's bed
            # shows (2026-10-25: build 3 had written the SfM's noisy surface back over Emerald Lake's modelled bed --
            # the stage and the fill had dropped it, the build had not)
            if kw.get("bodies") and grid is not None and win is not None:
                from pathlib import Path as _P
                wb_ = _P(str(kw["bodies"])); wb_ = (wb_ if wb_.is_dir() else wb_.parent) / "water_blind.tif"
                if wb_.exists():
                    from .stack import read_geo
                    b_, bv = read_geo(str(wb_), grid, win, "nearest")
                    hit = valid & bv & (np.rint(np.nan_to_num(b_)).astype(np.int32) == 1)
                    c[hit] = CLASSES["void"]
                    info["blind_water_fraction"] = float(hit[valid].mean()) if valid.any() else 0.0
        elif name == "fallback":
            # a context layer (3DEP) that stands in only where NO survey of the stack has a cell,
            # whatever that survey's class (Hig, 2026-10-11, China Poot Lake): elsewhere it is void.
            # Without this a survey's water (excluded: water is not a surface) let the fallback's
            # hydroflattened lake through as a flat floor, and the hole fill never saw the lake.
            # {"fallback": {"except": ["fill"]}} -- layers that do not count as surveys.
            skip = set(kw.get("except", [])) | {spec.get("name")}
            seen = np.zeros(z.shape, bool)
            # ...a survey's cell counts only if its class is not VOID (2026-10-16, whole-bay run: NCEI's land cells,
            # classed void by its below_level, stood as "a survey has a cell" over 680 km2 of mountains the lidar never
            # reached, so the fallback never showed and the fill invented the ground).  others_cls: the other layers'
            # class rasters, computed first (composite.py defers a layer with a fallback step to the end of pass 2).
            for nm, (_, v) in (others or {}).items():
                if nm not in skip and v is not None:
                    cv = (others_cls or {}).get(nm)
                    seen |= v if cv is None else (v & (cv != CLASSES["void"]))
            # a survey's FOOTPRINT, not only its valid cells: inside it a missing cell means it flew
            # over and kept nothing (open water -- Halibut Cove Lagoon showed 3DEP's sea at 0 m
            # between the 2009 multibeam lines, 2026-10-11).  {"footprints": [raster, ...]}: any
            # raster whose valid cells mark where a survey flew (observe_points.py class.tif).
            # ...but only NEAR that survey's returns ("near_m", default 300): the KBay 2023 class tiles hold
            # void (0, not nodata) across every unflown remainder of a tile square and the mountains the
            # survey never reached, and "flown" there let the fill invent 680 km2 of smooth terrain over
            # them in the whole-bay run (2026-10-16).  Open water the survey flew lies within a few hundred
            # metres of its returns (a shore, a water-surface return, a sounding line); ground a kilometre
            # from any return was not flown, and the fallback stands there.  near_m: 0 = the whole footprint.
            # ...and a WATER STAMP ({"stamps": [rasters, 1 = water]}: make_ice's lake_2021 / retreat_2023, which a survey's
            # class step applies with a ceiling that VOIDS the survey's cells standing above the water) counts as seen:
            # the hole the ceiling made is the fill's, never the prior's (2026-10-25: 3DEP's 2012 ice stood as a 130 m
            # plateau in the lake where the ceiling had voided Dixon's 2023 cells)
            if kw.get("stamps") and grid is not None and win is not None:
                from .stack import read_geo
                import os as _os
                for sp_ in kw["stamps"]:
                    if _os.path.exists(str(sp_)):
                        sz_, sv_ = read_geo(str(sp_), grid, win, "nearest")
                        seen |= sv_ & (np.rint(np.nan_to_num(sz_)).astype(np.int32) == 1)
            if kw.get("footprints") and grid is not None and win is not None:
                from .stack import read_geo
                import os as _os
                near_m = float(kw.get("near_m", 300.0))
                for fp in kw["footprints"]:
                    if _os.path.exists(str(fp)):
                        fz, fv = read_geo(str(fp), grid, win, "nearest")
                        if near_m > 0:
                            ret = fv & np.isfinite(fz) & (np.rint(fz) != CLASSES["void"])
                            f = max(int(round(near_m / res_m / 25.0)), 1)      # coarse distance grid (~25 cells)
                            Hc, Wc = -(-ret.shape[0] // f), -(-ret.shape[1] // f)
                            pad = np.zeros((Hc * f, Wc * f), bool); pad[:ret.shape[0], :ret.shape[1]] = ret
                            rc = pad.reshape(Hc, f, Wc, f).any(axis=(1, 3))
                            dc = ndimage.distance_transform_edt(~rc) * f * res_m if rc.any() else np.full(rc.shape, np.inf)
                            nearc = dc <= near_m
                            near = np.repeat(np.repeat(nearc, f, axis=0), f, axis=1)[:ret.shape[0], :ret.shape[1]]
                            fv = fv & near
                        seen |= fv
            # ...and a dry void the fill left empty (fill.py dry_max_km2, fill_dryvoid.tif) is the fallback's after all:
            # {"dry_void": "$out/fill_dryvoid.tif"} -- absent until the fill has run (build 3 of the chain)
            # ...and never inside an observed WATER BODY, whatever its distance from a return: {"bodies":
            # "$out/water_bodies.tif"} (2026-10-17, whole-bay v3: Grewingk Lake's middle, > 300 m from any KBay
            # return, showed 3DEP's 2012 surface as ground; Wosnesenski's showed its hydroflattened flat)
            if kw.get("bodies") and grid is not None and win is not None:
                import os as _os
                if _os.path.exists(str(kw["bodies"])):
                    from .stack import read_geo
                    bi, bv = read_geo(str(kw["bodies"]), grid, win, "nearest")
                    seen |= bv & (np.nan_to_num(bi) > 0)
            # ...nor inside the water the SHORELINE STAGE labelled (shore_label.tif beside the bodies: water 2, floating 3),
            # which reaches past the bodies -- the unobserved cells it took as water, enclosed by water or inside a
            # trusted polygon (2026-10-25, run 19: the fill had made the prior's cells inside the stage's water holes and
            # modelled the bed, and build 3 put 3DEP's 2012 ice at 105-119 m back over 28,000 of them at Grewingk's
            # face, where no water body covers them).  The same rule the fill applies: a prior is no observation
            # inside the water.
            if kw.get("bodies") and grid is not None and win is not None:
                import os as _os
                from pathlib import Path as _P
                sl_ = _P(str(kw["bodies"])); sl_ = (sl_ if sl_.is_dir() else sl_.parent) / "shore_label.tif"
                if sl_.exists():
                    from .stack import read_geo
                    lb_, lbv = read_geo(str(sl_), grid, win, "nearest")
                    seen |= lbv & np.isin(np.rint(np.nan_to_num(lb_)).astype(np.int32), (2, 3))
            if kw.get("dry_void") and grid is not None and win is not None:
                import os as _os
                if _os.path.exists(str(kw["dry_void"])):
                    from .stack import read_geo
                    dv, dvv = read_geo(str(kw["dry_void"]), grid, win, "nearest")
                    seen &= ~(dvv & (np.nan_to_num(dv) > 0))
            # ...and what is withdrawn is the fallback's GROUND: its water (a hydroflattened lake found by
            # flat_water.py) is an observation of water, a ceiling, which no other survey's presence negates
            # (2026-10-17: voiding it too left a 3DEP-only lake with void cells and no basin)
            c[valid & seen & (c == CLASSES["ground"])] = CLASSES["void"]
            # ...and two places where a fallback is never the ground, read beside the bodies (2026-10-21, Hig's review of the
            # joint pilot run 5: "small isolated pits along the shoreline", "mesa artifacts in the ocean ... meaningless flat
            # areas between the multibeam and the lidar"):
            #   * a cell the fill judged to be the WATER SURFACE (fill_surface.tif): 3DEP's 2012 hydroflattened Grewingk Lake
            #     at 41.3 m stood as single-cell pits 7 m deep along the 48.9 m shore -- the fill had dropped those cells as
            #     returns at the water and modelled the bed, and the next build put 3DEP back on them;
            #   * GROUND IN THE INTERTIDAL ZONE (intertidal.tif) below the tide's reach: every gridded prior is void there (the
            #     tidal lens) and a fallback is one -- 3DEP's coast stood as 0-3 m tables on the Grewingk tide flats between
            #     the 2009 multibeam and the 2021 lidar. Above intertidal_max_m (default 4.5 m, water.py's tide_max_m) it stays.
            if kw.get("bodies") and grid is not None and win is not None:
                import os as _os
                from pathlib import Path as _P
                od = _P(str(kw["bodies"])); od = od if od.is_dir() else od.parent
                fs = od / "fill_surface.tif"
                if fs.exists():
                    from .stack import read_geo
                    sv_, svv = read_geo(str(fs), grid, win, "nearest")
                    hit = valid & svv & (np.nan_to_num(sv_) > 0) & (c == CLASSES["ground"])
                    c[hit] = CLASSES["void"]
                    info["fallback_surface_fraction"] = float(hit[valid].mean()) if valid.any() else 0.0
                from .water import intertidal_raster
                im = intertidal_raster(od, grid, win)
                if im is not None:
                    hit = valid & im & (c == CLASSES["ground"]) & (z < float(kw.get("intertidal_max_m", 4.5)))
                    c[hit] = CLASSES["void"]
                    info["fallback_intertidal_fraction"] = float(hit[valid].mean()) if valid.any() else 0.0
                #   * GROUND BELOW A WATER SURFACE OBSERVED NEARBY (water_z.tif: the surveys' water returns and levels):
                #     a prior's ground standing lower than water seen within water_near_m of it is under that water --
                #     the 2012 hydroflattened shore at a lake's old level, a flat 3DEP patch at +0.3 m on a tide flat
                #     where the lidar saw water at +0.6 m forty metres away (joint pilot run 6, 2026-10-22: both the
                #     shoreline pits and the ocean mesas Hig flagged, in one rule; the intertidal zone had missed the
                #     mesas, which lay more than intertidal_near_m from any exposed ground).
                # the recipe names the raster ({"water_z": path}): an observation-stage product, never the build's own
                # out/water_z.tif, which this very build is writing (2026-10-22, joint_v2 build 1: a read of the half-written file)
                wzp = kw.get("water_z")
                if wzp and _os.path.exists(str(wzp)):
                    wz, wv = read_geo(wzp, grid, win, "nearest")
                    have = wv & np.isfinite(wz)
                    if have.any():
                        d_, ix_ = ndimage.distance_transform_edt(~have, sampling=grid.res, return_indices=True)
                        lev_ = wz[ix_[0], ix_[1]]
                        hit = (valid & (c == CLASSES["ground"]) & (d_ <= float(kw.get("water_near_m", 100.0)))
                               & (z < lev_ - float(kw.get("water_below_tol_m", 0.3))))
                        c[hit] = CLASSES["void"]
                        info["fallback_under_water_fraction"] = float(hit[valid].mean()) if valid.any() else 0.0
            info["fallback_fraction"] = float((valid & ~seen).mean()) if valid.any() else 0.0
        elif name == "facets":
            # VENDOR INTERPOLATION in a DTM delivered without its point cloud (Homer 2019 at the south corner of
            # Beluga Lake, Hig 2026-10-18: a data gap bridged by a TIN, 0.7-4 m above the 2018 surface): the
            # bridge is made of exact planes, and real ground, however smooth, is not planar to the centimetre
            # over win_m.  Cells whose residual to a local plane (win_m square, least squares) is below resid_m,
            # in connected patches of at least min_area_m2, become `as` (default "modelled": the rules never
            # let it set the class and take it only where nothing real is known -- Homer 2018's ground wins).
            k = max(int(round(kw.get("win_m", 10.0) / res_m)), 3) | 1
            zf = np.where(valid, z, np.nan).astype(np.float32)
            fill_ = np.nanmedian(zf) if np.isfinite(zf).any() else 0.0
            zz = np.where(valid, z, fill_).astype(np.float32)
            yy, xx = np.mgrid[:k, :k] - (k // 2)
            sxx = float((xx ** 2).sum()); syy = float((yy ** 2).sum()); n = float(k * k)
            m = ndimage.uniform_filter(zz, k); gx = ndimage.convolve(zz, xx.astype(np.float32) / sxx); gy = ndimage.convolve(zz, yy.astype(np.float32) / syy)
            var = ndimage.uniform_filter(zz * zz, k) - m * m
            resid = np.sqrt(np.clip(var - (gx * gx * sxx + gy * gy * syy) / n, 0, None))
            # only where the whole window is valid (an edge against nodata is not a facet)
            full = ndimage.uniform_filter(valid.astype(np.float32), k) > 0.999
            f = valid & full & (resid < float(kw.get("resid_m", 0.02))) & (c == CLASSES[kw.get("only", "ground")])
            lab, nl = ndimage.label(f)
            if nl:
                px = int(float(kw.get("min_area_m2", 200.0)) / (res_m * res_m))
                sizes = np.bincount(lab.ravel(), minlength=nl + 1)
                f &= ~np.isin(lab, np.flatnonzero(sizes < px))
            c[f] = CLASSES[kw.get("as", "modelled")]
            info["facets_fraction"] = float(f[valid].mean()) if valid.any() else 0.0
        elif name == "flat_below":
            # water surfaces at SEVERAL levels (a lidar flown over the sea on different tides): flat
            # (roughness < rough_m over win_m) and below max_level_m, opened, blobs >= min_area_m2.
            # Kachemak 2026-10-10: KBay 2023 holds the sea at -3.0..-1.9 m on different lines, exactly
            # flat (roughness ~0.00-0.02); low land 1-4 m is ~0.6 m rough, so the two separate.
            r = roughness(z, valid, res_m, kw.get("win_m", 10.0))
            w = valid & (r < kw.get("rough_m", 0.1)) & (z < kw["max_level_m"])
            if kw.get("only", "ground"):
                w &= c == CLASSES[kw.get("only", "ground")]
            w = ndimage.binary_opening(w, iterations=int(kw.get("open_px", 2)))
            lab, n = ndimage.label(w)
            if n:
                px = int(kw.get("min_area_m2", 2000.0) / (res_m * res_m))
                sizes = ndimage.sum(np.ones_like(lab), lab, np.arange(1, n + 1))
                w[np.isin(lab, np.flatnonzero(sizes < px) + 1)] = False
            c[w] = CLASSES[kw.get("as", "water")]
            info["flat_below_fraction"] = float(w[valid].mean()) if valid.any() else 0.0
        elif name == "flat_lakes":
            # LAKES WITHOUT WATER RETURNS (TOPOBATHY_REVIEW 8.1, 2026-10-15: four shallow lakes flat to 2-5 cm
            # with not one vendor water return, so no modal level and no class-9 water -- "treated as having no
            # depth").  A patch of `only` (ground) flat to rough_m over win_m, opened, >= min_area_m2, that is a
            # LOCAL MINIMUM -- at least ring_frac of a ring_m-wide ring around it stands rim_dz_m above its level
            # (a terrace, a road or a car park is flat too, but drops away on a side) -- is water at its level.
            # Tested on the KBay and Bluff Point lakes at std 0.03 m / 14 m / 2000 m2 before the ring test.
            # flatness as |z - local MEDIAN| (not the std about the mean: a few stray returns off the water --
            # p90 roughness 0.14 m on a lake flat to 0.01 -- made every window rough and fragmented the patch
            # below min_area_m2, Bluff Point 2026-10-15); a 2 px closing absorbs those specks
            k = max(int(kw.get("win_m", 14.0) / res_m), 3) | 1
            med = ndimage.median_filter(np.nan_to_num(np.where(valid, z, np.nan), nan=0.0), size=k)
            # ...and no GRADIENT: the local median's range over the window (a plane passes the median test;
            # a 5 % hillside bench and a sloping tide flat came through as "lakes" and leaked uphill)
            # ...measured on the median smoothed once more: clusters of stray returns put bumps in the first
            # median that failed half the cells of two 0.35 ha lakes; a plane's range is unchanged by it
            # The gradient is LOCAL (the smoothed median's slope, m/m), not its range over the window: a
            # range over 14 m failed every cell within 14 m of a shore, i.e. most of a 60 m lake
            med2 = ndimage.median_filter(med, size=k)
            gy, gx = np.gradient(med2, res_m)
            slope = np.hypot(gx, gy)
            only = kw.get("only", "ground"); only = [only] if isinstance(only, str) else list(only)
            # `only` may list "void": a lake with no returns at all is void in a point classification, its DTM
            # value the vendor's flat TIN at the shore level (the 27.67 m lake, TOPOBATHY_REVIEW 8.1)
            # a VOID cell (no returns) skips the flatness and slope tests: its DTM value is the vendor's TIN
            # stretched across the gap, which says nothing about the water (a dammed reservoir's TIN slopes
            # 1 % from inlet to dam, Seldovia 2026-10-15); it is judged by the ring and a loose level range
            isvoid = valid & (c == CLASSES["void"]) & ("void" in only)
            w = valid & np.isin(c, [CLASSES[o] for o in only]) & (isvoid | ((np.abs(z - med) < kw.get("rough_m", 0.03)) & (slope < kw.get("slope_max", 0.01))))
            w = ndimage.binary_opening(w, iterations=int(kw.get("open_px", 1)))
            w = ndimage.binary_closing(w, iterations=int(kw.get("close_px", 2))) & valid
            # MAPPED WATER as a prior (Hig's hand-mapped lakes, 2026-10-15), its own pass: the flat / void cells
            # INSIDE a mapped lake or river form their own patches, which need only mapped_min_area_m2 (100) and
            # one level -- no ring test (the map says "basin") and no density test (the map outranks the
            # heuristic: a pond with vegetation at its surface returns like ground).  Done apart from the general
            # pass because there a small void lake merged with the flat shore around it into a ground-dominated
            # patch the density rule rejected.  Outside a polygon nothing changes.  {"mapped": <sources.json>}
            if kw.get("mapped") and grid is not None and win is not None:
                from .water import mapped_rasters
                mw, _ = mapped_rasters(kw["mapped"], grid, win)
                wm = w & (mw > 0)
                labm, nm = ndimage.label(wm)
                if nm:
                    idm = np.arange(1, nm + 1); szm = ndimage.sum(wm, labm, idm)
                    vfm = ndimage.sum(isvoid & wm, labm, idm) / np.maximum(szm, 1)
                    zq = np.nan_to_num(z, nan=1e9)
                    p10m = ndimage.labeled_comprehension(zq, labm, idm, lambda v: np.percentile(v[v < 1e8], 10) if (v < 1e8).any() else np.nan, float, np.nan)
                    p90m = ndimage.labeled_comprehension(zq, labm, idm, lambda v: np.percentile(v[v < 1e8], 90) if (v < 1e8).any() else np.nan, float, np.nan)
                    okm = (szm >= int(kw.get("mapped_min_area_m2", 100.0) / (res_m * res_m))) \
                        & ((p90m - p10m) < np.where(vfm >= 0.5, kw.get("range_void_m", 1.0), kw.get("range_m", 0.3)))
                    hitm = okm[labm - 1] & wm
                    c[hitm] = CLASSES[kw.get("as", "water")]
                    info["flat_lakes_mapped"] = float(okm.sum())
            lab, n = ndimage.label(w)
            if n:
                idx = np.arange(1, n + 1)
                px = int(kw.get("min_area_m2", 2000.0) / (res_m * res_m))
                sizes = ndimage.sum(w, lab, idx)
                big = sizes >= px
                lev = ndimage.labeled_comprehension(np.nan_to_num(z, nan=1e9), lab, idx, lambda v: np.median(v[v < 1e8]) if (v < 1e8).any() else np.nan, float, np.nan)
                p10 = ndimage.labeled_comprehension(np.nan_to_num(z, nan=1e9), lab, idx, lambda v: np.percentile(v[v < 1e8], 10) if (v < 1e8).any() else np.nan, float, np.nan)
                p90 = ndimage.labeled_comprehension(np.nan_to_num(z, nan=1e9), lab, idx, lambda v: np.percentile(v[v < 1e8], 90) if (v < 1e8).any() else np.nan, float, np.nan)
                # one level: a sheet of water that RETURNED is horizontal to a few cm (Bluff Point's lake: 0.05 m
                # p5-p95); 0.3 m let a wet mudflat with 0.3 m of relief over 100 m through as 14 ha of "lake" and
                # the fill carved 1.5 m into real lagoon floor.  A void-dominated patch (a TIN) gets the loose range.
                vfrac = ndimage.sum(isvoid & w, lab, idx) / np.maximum(sizes, 1)
                big &= (p90 - p10) < np.where(vfrac >= 0.5, kw.get("range_void_m", 1.0), kw.get("range_m", 0.3))
                # RETURN DENSITY (2026-10-15, the Seldovia mudflat): a NIR lidar gets few returns off water -- the
                # lakes' patches hold <= 8 returns per 2 m cell in 63-99 % of their cells, a wet mudflat with 0.2 m
                # of relief 24 % (it returns like dry ground, ~24 a cell) and on every surface measure the two are
                # twins.  {"density": {"path": cellstats, "band": 1, "sparse_n": 8, "min_frac": 0.5}}: the patch
                # must be mostly sparse.  Optional: a DTM-only survey has no counts.
                dn = kw.get("density"); dens_ok = np.ones(n, bool)
                if dn and grid is not None and win is not None:
                    import os as _os
                    if _os.path.exists(str(dn["path"])):
                        from .stack import read_geo
                        nall, nv = read_geo(str(dn["path"]), grid, win, "nearest", band=int(dn.get("band", 1)))
                        sparse = nv & (nall <= float(dn.get("sparse_n", 8)))
                        sfrac = ndimage.sum(sparse & w, lab, idx) / np.maximum(ndimage.sum(nv & w, lab, idx), 1)
                        dens_ok = (sfrac >= float(dn.get("min_frac", 0.5))) | (vfrac >= 0.5)
                        big &= dens_ok
                        info["flat_lakes_dense_rejected"] = float(((sfrac < float(dn.get("min_frac", 0.5))) & (vfrac < 0.5) & (sizes >= px)).sum())
                # a void-dominated patch's level is its LOW end: the TIN bows up between shores (the reservoir's
                # median stood 0.9 m over its dam end and the ring read "below" on that side)
                lev = np.where(vfrac >= 0.5, p10, lev)
                rpx = max(int(round(kw.get("ring_m", 10.0) / res_m)), 1)
                lr = ndimage.grey_dilation(lab, size=2 * rpx + 1)          # the patch each ring cell belongs to
                lvr = lev[np.maximum(lr - 1, 0)]
                # the ring judges only cells that DIFFER from the patch's level: the flat's own margin (water the
                # gradient test trimmed, a stray-return fringe) says nothing either way; what is left is shore
                # (above: a lake) or a drop-off (below: a terrace, a bench)
                ring = ndimage.binary_dilation(w, iterations=rpx) & ~w & valid & np.isfinite(z) & (np.abs(z - lvr) >= kw.get("rim_dz_m", 0.1))
                n_ring = np.bincount(lr[ring], minlength=n + 1)[1:]
                above = ring & (z > lvr + kw.get("rim_dz_m", 0.1))
                n_up = np.bincount(lr[above], minlength=n + 1)[1:]
                ok = big & (n_ring >= kw.get("ring_min", 20)) & (n_up >= kw.get("ring_frac", 0.6) * n_ring)
                hit = ok[lab - 1] & w
                c[hit] = CLASSES[kw.get("as", "water")]
                info["flat_lakes_fraction"] = float(hit[valid].mean()) if valid.any() else 0.0
                info["flat_lakes_n"] = float(ok.sum())
        elif name == "raster":
            # class from a categorical raster that travels with the observation, e.g. the KBay reclass's
            # fill_mask.tif (2 = harmonic fill across holes = a modelled surface, not an observation):
            # {"raster": {"path": ..., "map": {"2": "modelled"}}}
            if grid is None or win is None:
                continue
            from .stack import read_geo
            import os as _os
            if not _os.path.exists(str(kw["path"])):      # a pipeline product not built yet ($out/fill_surface.tif)
                continue
            rr, rvv = read_geo(str(kw["path"]), grid, win, "nearest")
            if kw.get("codes"):
                # the raster already holds topobathy class codes (observe_points.py class.tif): copy them,
                # void included - a 0 there means the survey observed nothing usable at that cell
                sel = valid & rvv
                c[sel] = np.rint(rr[sel]).astype(np.uint8)
                info["raster_void_fraction"] = float((sel & (c == 0))[valid].mean()) if valid.any() else 0.0
                continue
            for val, cname in kw["map"].items():
                sel = valid & rvv & (np.rint(rr) == float(val))
                if kw.get("only"):                          # recolour one class only: {"only": "ground"}
                    sel &= c == CLASSES[kw["only"]]
                else:
                    if cname != "void":
                        # a VOID has no height: a layer's placeholder value there (joint.py fills voids from the nearest
                        # surface so the raster is dense) is no one's level. Recoloured to water by the 2021 lake mask,
                        # a void in Grewingk's outlet bay read as a 0.8 ha "lake" at 27.4 m, 21 m under the lake (run 14).
                        sel &= c != CLASSES["void"]
                    if cname == "water" and not kw.get("over_bed"):
                        # a water mask says where the SURFACE is water; a measured BED under it stays bed (2026-10-23,
                        # joint_v3 run 10: the 2021 lake mask recoloured the sonar's bed in Grewingk's outlet bay as water
                        # at 20-45 m, the water stage read dozens of "lakes" at those levels inside the lake, and the fill
                        # split sent them to the river model -- a 7.8 m step across the bay). "over_bed": true to override.
                        sel &= c != CLASSES["bed"]
                if cname == "water" and kw.get("ceiling_m") is not None and sel.any():
                    # A WATER STAMP IS A CEILING (2026-10-24, run 17): a mask that says "water here" (the lake's 2021
                    # extent, the ground the glacier left by 2023) is a later observation than a height an older survey
                    # pooled into this layer -- ice at 62 m in 2019 where the lake stands at 49 m in 2023.  A height
                    # standing more than ceiling_m above the layer's own water returns within ceiling_near_m is not
                    # the surface any more: the cell is VOIDED (no height, no class), a hole for the water stage and
                    # the fill, which read the water around it and the bed under it.  Without this the stale height
                    # was "water at 62 m", its own level, a 13 m pillar in the water surface.
                    wref = valid & (c == CLASSES["water"]) & np.isfinite(z) & ~sel
                    if wref.any():
                        dist, (ii, jj) = ndimage.distance_transform_edt(~wref, sampling=res_m, return_indices=True)
                        near = z[ii, jj]; del ii, jj
                        stale = sel & np.isfinite(z) & (dist <= float(kw.get("ceiling_near_m", 200.0))) & (z > near + float(kw["ceiling_m"]))
                        del dist, near
                        if stale.any():
                            v_ = info.get("_void"); info["_void"] = stale if v_ is None else (v_ | stale)
                            info[f"raster_{cname}_ceiling_void_fraction"] = float(stale[valid].mean()) if valid.any() else 0.0
                            sel &= ~stale
                c[sel] = CLASSES[cname]
                info[f"raster_{cname}_fraction"] = float(sel[valid].mean()) if valid.any() else 0.0
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
            as_ = CLASSES[kw.get("as", "ice")]
            sel = m & valid & ((c == CLASSES[only]) if only else True)
            if not only and as_ != CLASSES["void"]:
                sel &= c != CLASSES["void"]        # as for the raster step: a void's placeholder height is no one's level
            if not only and as_ == CLASSES["water"] and not kw.get("over_bed"):
                sel &= c != CLASSES["bed"]         # as for the raster step: a measured bed under mapped water stays bed
            c[sel] = as_
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
            path = kw["path"]
            if str(path).startswith("$out/") and kw.get("_recipe") is not None:
                path = kw["_recipe"].resolve(path)
            sr, sv = read_geo(str(path), kw["_grid"], kw["_win"], "bilinear")
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
    run_prior = np.zeros(shape, bool)          # the running value came from a conditioned prior (condition.py)
    class_now = np.zeros(shape, np.uint8); date_now = np.full(shape, -np.inf, np.float32)
    winner = np.full(shape, -1, np.int16); masks = []; sts = []; gates = []
    fallback = np.zeros(shape, bool)
    # the running CEILING: the lowest water surface any credible survey observed, and its date (2026-10-16)
    run_ceil = np.full(shape, np.inf, np.float32); run_ceil_date = np.full(shape, np.nan, np.float32)
    bed_codes = [CLASSES["bed"], CLASSES["subglacial"]]
    for i, o in enumerate(obs):
        dated = o["valid"] & (o["cls"] > 0) & (o["date"] <= as_of + 1e-6)
        cred = dated & (o["sigma"] < o.get("class_sigma_max", sigma_max))
        st = sig_total(o); sts.append(st)
        # class-now: the newer of (running newest, this observation) among credible ones --
        # never a modelled surface (the hole interpolation): derived, not an observation of
        # what is there, so it can stand in for the bed without claiming to be the class
        newer = cred & (o["cls"] != CLASSES["modelled"]) & (o["date"] >= date_now)
        class_now = np.where(newer, o["cls"], class_now).astype(np.uint8)
        date_now = np.where(newer, o["date"], date_now).astype(np.float32)
        gate_i = compat(class_now, o["cls"]); gate_i[~dated] = 0
        gate_run = compat(class_now, run_cls); gate_run[~np.isfinite(run_z)] = 0
        # a conditioned PRIOR (a regional grid corrected toward the observations around it,
        # condition.py) stands in like a modelled surface: half gate, so any real observation of
        # the class beats it whatever its sigma or date, and priors rank among themselves by error
        is_prior = bool((o.get("spec") or {}).get("_prior"))
        if is_prior: gate_i = np.minimum(gate_i, 0.5)
        gate_run = np.where(run_prior, np.minimum(gate_run, 0.5), gate_run)
        gates.append(gate_i)
        take = dated & (gate_i > 0) & (
            (gate_i > gate_run) |                                       # 1/2: better compatibility (or old surface obsolete)
            ((gate_i == gate_run) & (st < run_sig - margin_m)))         # 3: better expected error
        # A BED IS BELOW EVERY WATER SURFACE EVER SEEN OVER IT (2026-10-16, Bluff Point: NCEI's "bed" at -0.48 m
        # stood under Kenai 2008's -0.85 m sea sheet -- the ceiling below exempted beds and heeded only newer water).
        # A bed observation above the lowest water surface seen there, by more than CEIL_TOL_M plus what the bed
        # could have aggraded between the two dates (rates["bed"]), is not taken; the symmetric eviction is below.
        if np.isfinite(run_ceil).any() and np.isin(o["cls"], bed_codes).any():
            tol_c = CEIL_TOL_M + rates.get("bed", 0.0) * np.abs(o["date"] - np.nan_to_num(run_ceil_date, nan=o["date"]))
            take &= ~(np.isin(o["cls"], bed_codes) & np.isfinite(run_ceil) & (o["z"] > run_ceil + tol_c))
        if o.get("name") in lowest:                                     # 4: opt-in lowest, region scale
            tol = float(lowest[o["name"]].get("tol_m", 0.3)); win_m = float(lowest[o["name"]].get("smooth_m", 50))
            with np.errstate(invalid="ignore"):                       # inf - inf where nothing is shown yet
                close = dated & (gate_i == gate_run) & (gate_i > 0) & (np.abs(st - run_sig) <= tol)
            dz = np.where(np.isfinite(run_z) & o["valid"], o["z"] - run_z, 0.0).astype(np.float32)
            k = max(int(win_m), 3)
            dz_s = ndimage.uniform_filter(dz, k) / np.maximum(ndimage.uniform_filter((np.isfinite(run_z) & o["valid"]).astype(np.float32), k), 1e-3)
            take |= close & (dz_s < -tol)
        # (no last resort -- Hig, 2026-10-10: an observation the gate does not admit, a water
        # surface where no bed is known, leaves a hole rather than standing in)
        # A WATER OBSERVATION IS A CEILING (stage B, 2026-10-15): where this observation is the newest
        # class and says water, a running value that is not a bed and stands above its surface (less
        # CEIL_TOL_M) is evicted -- an older survey's "ground" over a lake is that survey's water surface
        # (Bluff Point: Kenai 2008 held the lake at 22.2 over Homer 2019's water at 22.03, so the lake was
        # never a hole and never filled).  A bed below the ceiling stands: the rock exposed in an earlier
        # survey's rapid.  Earlier layers' masks are cleared there, so the fold shows the hole.
        evict = newer & (o["cls"] == CLASSES["water"]) & np.isfinite(run_z) & (run_z > o["z"] - CEIL_TOL_M) \
            & ~np.isin(run_cls, [CLASSES["bed"], CLASSES["subglacial"]])
        wat = cred & (o["cls"] == CLASSES["water"]) & np.isfinite(o["z"])
        if wat.any():                                                   # ...a running bed above this water surface, any date
            tol_b = CEIL_TOL_M + rates.get("bed", 0.0) * np.abs(as_of - o["date"])
            evict |= wat & np.isin(run_cls, bed_codes) & np.isfinite(run_z) & (run_z > o["z"] + tol_b)
            lower = wat & (o["z"] < run_ceil)
            run_ceil = np.where(lower, o["z"], run_ceil); run_ceil_date = np.where(lower, o["date"], run_ceil_date)
        if evict.any():
            run_z[evict] = np.nan; run_sig[evict] = np.inf; run_cls[evict] = 0; run_gate[evict] = 0
            run_prior[evict] = False; winner[evict] = -1
            for m_ in masks:
                m_[evict] = 0.0
            take &= ~evict
        masks.append(take.astype(np.float32))
        run_z = np.where(take, o["z"], run_z); run_sig = np.where(take, st, run_sig)
        run_cls = np.where(take, o["cls"], run_cls).astype(np.uint8); run_gate = np.where(take, gate_i, run_gate)
        run_prior = np.where(take, is_prior, run_prior)
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
    not_model = np.stack([o["cls"] != CLASSES["modelled"] for o in obs])      # a modelled surface never sets the class axis
    dstack = np.where(cred & not_model, dates[:, None, None], -np.inf)
    inew = np.argmax(dstack, axis=0)
    any_cred = (cred & not_model).any(axis=0)
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
    # No last resort (Hig, 2026-10-10: "water is not a real surface"): where no observation is
    # compatible with the target -- a lake with no bed known -- the composite is a hole, and
    # class_now says why.  (Until today the newest observation of any class stood in, flagged.)
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
