"""The regression gate for the joint topobathy runs (2026-10-27, after run 20f).

Why it exists: run 20e's gains at Halibut Cove's lagoon and on a tide flat were not made by the rules written for them
but by a bug (the `surf` rule dropped in obs_lidar); restoring the rule for China Poot Lake took both back to run 20c.
Ten probe scripts diffed by eye did not say that a number had moved that no change was aimed at.  The gate is one table:
every site a review has named is a ROW with a direction, and a run is compared with the BEST any earlier run reached on
that row, so a gain that disappears is a regression even when the previous run was no better.

    python topobathy/gate.py OUT_DIR --tag run20g [--base run20c,run20e,run20f] [--save] [--shore]

OUT_DIR is a full run's out/ (or a smoke copy holding the shore stage's products: rows that need the fill are skipped).
Metrics are stored as <joint_v3>/gate/<tag>.json with --save.  Exit status 1 when any row regressed.
Adding a site = one row in ROWS (a window, a function of the window's rasters, a direction, a tolerance).
"""
import argparse, json, sys
from pathlib import Path
import numpy as np, rasterio
from rasterio.windows import Window
from scipy import ndimage

GATE_DIR = Path("/Volumes/Nunatak/lidar_build/composite/joint_v3/gate")
LAND_CLS = (1, 5, 6, 7)      # class_now codes that are solid ground (ground, built, canopy, ice) -- as regress18
BED_CLS = 3
TIDE_MAX = 4.5               # shore.py tide_max_m
NOT_OBS = (0, 6)             # source_index: the 3DEP prior and the fill are no observation (the prior still stands in the
                             # water at the shore stage; the fill replaces it before a full run is measured)


class Win:
    """Rasters of one out dir read over a window (x, y centre in the grid CRS, half-size h cells; None = whole box)."""
    shore_only = False                                          # --shore: measure at the shore stage (smoke vs a full run)

    def __init__(self, d, x=None, y=None, h=None):
        self.d, self.x, self.y, self.h = Path(d), x, y, h
        self._c = {}

    def has(self, name):
        return (self.d / name).exists()

    def __getitem__(self, name):
        if name not in self._c:
            with rasterio.open(self.d / name) as s:
                if self.x is None:
                    a = s.read(1)
                else:
                    r, c = s.index(self.x, self.y)
                    a = s.read(1, window=Window(c - self.h, r - self.h, 2 * self.h + 1, 2 * self.h + 1), boundless=True,
                               fill_value=s.nodata if s.nodata is not None else 0)
                if a.dtype.kind == "f":
                    a = a.astype(np.float32)
                    if s.nodata is not None:
                        a[a == s.nodata] = np.nan
                self.res = s.res[0]
            self._c[name] = a
        return self._c[name]

    def level(self):
        """The water level: the fill's where the run has one, else the shore stage's (a smoke copy)."""
        full = self.has("fill_water_level.tif") and not Win.shore_only
        return self["fill_water_level.tif"] if full else self["shore_level.tif"]

    def km2(self, m):
        self["shore_label.tif"]
        return float(m.sum()) * self.res * self.res / 1e6


def china_poot_water(w):
    return w.km2(w["shore_label.tif"] == 2)

def china_poot_land_at_level(w):
    return w.km2((np.abs(w["joint_v3.tif"] - 50.46) < 0.3) & (w["shore_label.tif"] == 1))

def lagoon_land(w):
    return int((w["shore_label.tif"] == 1).sum())

def tideflat_ground_land(w):
    """Observed ground (not the fill's) standing above the day's tide, labelled land: the fraction."""
    lab, cls, z, lev = w["shore_label.tif"], w["class_now.tif"], w["joint_v3.tif"], w.level()
    si = w["source_index.tif"]
    g = np.isin(cls, LAND_CLS) & np.isfinite(z) & ~np.isin(si, NOT_OBS) & (z > -1.6)
    return float((lab[g] == 1).mean()) if g.any() else float("nan")

def bed_land(w):
    return int(((w["class_now.tif"] == BED_CLS) & np.isfinite(w["joint_v3.tif"]) & ~np.isin(w["source_index.tif"], NOT_OBS)
                & (w["shore_label.tif"] == 1)).sum())

def solid_over_level(w):
    lab, cls, z, lev = w["shore_label.tif"], w["class_now.tif"], w["joint_v3.tif"], w.level()
    m = (lab == 2) & np.isin(cls, LAND_CLS) & np.isfinite(z) & np.isfinite(lev) & (z > lev + 1.0)
    m &= ~np.isin(w["source_index.tif"], NOT_OBS)
    if w.has("fill_mask.tif") and not Win.shore_only:
        m &= w["fill_mask.tif"] == 0
    return int(m.sum())

def face_bad(w):
    lab, z, lev = w["shore_label.tif"], w["joint_v3.tif"], w.level()
    return int(((lab == 2) & np.isfinite(z) & np.isfinite(lev) & (z > lev + 1)).sum())

def face_bed_min(w):
    """The deepest bed in the face window: the vertical face's bed ran to about -118 m in 20e."""
    lab, z = w["shore_label.tif"], w["joint_v3.tif"]
    v = z[(lab == 2) & np.isfinite(z)]
    return float(np.percentile(v, 1)) if v.size else float("nan")

def emerald_depth(w):
    lab, z, lev = w["shore_label.tif"], w["joint_v3.tif"], w.level()
    m = (lab == 2) & np.isfinite(z) & np.isfinite(lev)
    return float(np.median(lev[m] - z[m])) if m.any() else float("nan")

def level_below_tide(w, low_tide=-2.9):
    lev = w.level()
    return w.km2(np.isin(w["shore_label.tif"], (2, 3)) & np.isfinite(lev) & (lev < low_tide - 1.5)) * 100

def band_land(w):
    """The seam between the 2021 lake and the 2023 retreat stamps at Grewingk's face: land below 56 m (20g: 290 cells)."""
    return int(((w["shore_label.tif"] == 1) & (w["joint_v3.tif"] < 56)).sum())

def face_level_spread(w):
    """One lake at the face: the spread (p99 - p1) of the level over its wet cells (20g: 48.9 / 50.9 / 51.3 m pools)."""
    lev = w.level(); v = lev[np.isin(w["shore_label.tif"], (2, 3)) & np.isfinite(lev) & (lev > 40) & (lev < 60)]
    return float(np.percentile(v, 99) - np.percentile(v, 1)) if v.size else float("nan")

def face_shallow(w):
    """Water at the face less than 1.5 m deep: the near-flat beds of those pools (20g)."""
    lab, z, lev = w["shore_label.tif"], w["joint_v3.tif"], w.level()
    m = (lab == 2) & np.isfinite(z) & np.isfinite(lev)
    return float(((lev - z)[m] < 1.5).mean()) if m.any() else float("nan")

def perched_over_deep(w, low_tide=-1.38):
    """Water held at a perched level over a bed below the lowest tide (the flat-water rule's sill test, 2026-10-28: three
    2.75 m bodies over the bay's -53 m multibeam stood 4 m over the low-tide sea in 20h2).  Kinds as the shore stage left
    them (shore_bodies.json).  Hectares."""
    import sys as _s; _s.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from topobathy.water import load_bodies
    _, tab = load_bodies(w.d)
    bid, z = w["water_bodies.tif"], w["joint_v3.tif"]
    ids = [k for k, p in tab.items() if p.get("kind") == "perched"]
    m = np.isin(bid, ids) & np.isin(w["shore_label.tif"], (2, 3)) & np.isfinite(z) & (z < low_tide - 1.0)
    return w.km2(m) * 100

def clip_shelf(w):
    """Modelled wet cells exactly 0.1 m under their water: the signature of the fill's clip (a plateau at the
    constraint; the face's flat shelves in 20h2).  Since 2026-10-28 the ceiling is in the fit and the clip a guard."""
    lab, z, lev, fm = w["shore_label.tif"], w["joint_v3.tif"], w.level(), w["fill_mask.tif"]
    d = lev - z
    # the shelves INSIDE lakes and rivers (2026-10-28, run 20i): of 20i's 43k cells 28k lay within 6 m of the shore (a bed
    # at the water there is the shore), 11k were the tidal lens (fill_mask 7: the bed at the water by design) and 12k the
    # sea.  Interior (> 10 m from dry ground, the ceiling's own rim), not tidal lens, not the sea.
    import sys as _s; _s.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from topobathy.water import load_bodies
    bid = w["water_bodies.tif"]; mx = int(bid.max()); seak = np.zeros(mx + 1, bool)
    for k, p in load_bodies(w.d)[1].items():
        if 0 < k <= mx and p.get("kind") in ("sea", "tidal"):
            seak[k] = True
    sea = seak[np.clip(bid, 0, mx)] | ((bid == 0) & (lev <= TIDE_MAX))
    inner = ndimage.distance_transform_edt(np.isin(lab, (2, 3))) * w.res > 10
    return int(((lab == 2) & (fm > 0) & (fm != 7) & inner & ~sea & np.isfinite(d) & (np.abs(d - 0.1) < 0.005)).sum())

def floating_mismatch(w):
    """Floating cells whose level differs by > 0.3 m from the water around them (ice takes the level it floats on;
    20h2: 50.87 / 51.33 m patches in the 48.91 m lake)."""
    lab, lev = w["shore_label.tif"], w.level()
    f = lab == 3
    if not f.any():
        return 0
    L, n = ndimage.label(f, structure=np.ones((3, 3), bool))
    own = ndimage.grey_dilation(L, size=3); ring = (own > 0) & (L == 0) & (lab == 2) & np.isfinite(lev)
    med = np.full(n + 1, np.nan)
    if ring.any():
        med[1:] = ndimage.labeled_comprehension(lev, np.where(ring, own, 0), np.arange(1, n + 1), np.median, float, np.nan)
    m = f & np.isfinite(lev) & np.isfinite(med[L]) & (np.abs(lev - med[L]) > 0.3)
    return int(m.sum())

SOUNDINGS = "/Volumes/Nunatak/lidar_build/obs/soundings/soundings_20261017.csv"
SOUNDING_DZ = {"berg_grewingk_2017": 0.36, "berg_grewingk_2017_hand": 0.36, "higman_grewingk_2019_2021": 0.36,
               "higman_grewingk_2026": 0.36}          # the recipe's fill.points_dz_m (Grewingk 2021's +0.36 m)

def sounding_misfit(w):
    """|model - sounding| p90 over the soundings in the window (bounds excluded): replaces face_bed_p1_m, a remembered
    model depth, 2026-10-28 (20h2 fitted the face's four deepest soundings far better than 20g, and that row said MOVED)."""
    import csv
    z = w["joint_v3.tif"]
    with rasterio.open(w.d / "joint_v3.tif") as s:
        r0, c0 = s.index(w.x, w.y)
        T = s.transform
    v = []
    for row in csv.DictReader(open(SOUNDINGS)):
        if row.get("bound"):
            continue
        x, y = float(row["x"]), float(row["y"])
        if abs(x - w.x) > w.h * w.res or abs(y - w.y) > w.h * w.res:
            continue
        r, c = rasterio.transform.rowcol(T, x, y)
        rr, cc = r - r0 + w.h, c - c0 + w.h
        if 0 <= rr < z.shape[0] and 0 <= cc < z.shape[1] and np.isfinite(z[rr, cc]):
            v.append(abs(z[rr, cc] - (float(row["z"]) + SOUNDING_DZ.get(row["survey"], 0.0))))
    return float(np.percentile(v, 90)) if v else float("nan")


def label_km2(code):
    return lambda w: w.km2(w["shore_label.tif"] == code)

def flat_steps(w):
    """Adjacent wet cells of flat bodies whose levels differ by > 0.3 m (regress18), river / sea / tidal excluded."""
    lab, lev, bid = w["shore_label.tif"], w.level(), w["water_bodies.tif"]
    # kinds as the shore stage left them (shore_bodies.json): the sill test's 33 re-kinded tidal bodies counted as flat
    # lakes from the raw file, their tide-stand seams as 8,110 "steps" (run 20i)
    import sys as _s; _s.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from topobathy.water import load_bodies
    kind = {k: p.get("kind") for k, p in load_bodies(w.d)[1].items()}
    mx = int(bid.max()); sloped = np.zeros(mx + 1, bool)
    for k, v in kind.items():
        if 0 < k <= mx and v in ("river", "sea", "tidal"):
            sloped[k] = True
    # body-less water at a level within the tide's reach is the sea at a survey's stand: seams between flights at
    # different tides are expected at this stage (65k of 20g's 65k "steps"; the viewer floors the sea to MLLW)
    sea = (bid == 0) & (lev <= TIDE_MAX)
    L = np.where(np.isin(lab, (2, 3)) & ~sloped[np.clip(bid, 0, mx)] & ~sea, lev, np.nan)
    st = np.zeros(lab.shape, bool)
    for dr, dc in ((0, 1), (1, 0)):
        a = L[:lab.shape[0] - dr, :lab.shape[1] - dc]; b = L[dr:, dc:]
        s_ = np.isfinite(a) & np.isfinite(b) & (np.abs(a - b) > 0.3)
        st[:lab.shape[0] - dr, :lab.shape[1] - dc] |= s_; st[dr:, dc:] |= s_
    return int(st.sum())


# (name, window (x, y, half cells) or None for the whole box, function, direction up|down|same, tolerance, needs fill,
#  where it came from)
ROWS = [
    ("china_poot_water_km2", (601180, 6602101, 400), china_poot_water, "up", 0.01, False,
     "China Poot Lake labelled water (20e regression, 0.25 vs 0.40)"),
    ("china_poot_land_at_level_km2", (601180, 6602101, 400), china_poot_land_at_level, "down", 0.005, False,
     "land within 0.3 m of the lake's 50.46 m level"),
    ("lagoon_land_cells", (601666, 6605177, 100), lagoon_land, "down", 50, False,
     "Halibut Cove lagoon: sheets at 2.5 m over the -60 m bed labelled land (20c/20f)"),
    ("tideflat_ground_land_frac", (598192, 6603735, 40), tideflat_ground_land, "up", 0.05, False,
     "a tide flat 0.4-0.7 m over the day's tide, two surveys' ground, labelled water (20c/20f)"),
    ("bed_labelled_land_cells", None, bed_land, "down", 500, False,
     "measured bed labelled land (some is a flat a sonar saw at high tide; shore.py enforces only bed below the level)"),
    ("solid_over_level_water_cells", None, solid_over_level, "down", 50, True,
     "observed solid cells labelled water > 1 m above the level (regress18; full runs only: at the shore stage the SfM's"
     " water-blind cells still stand over the water, build 3 voids them)"),
    ("flat_water_steps_cells", None, flat_steps, "down", 2000, False,
     "steps > 0.3 m between adjacent flat-water cells (regress18)"),
    ("face_bad_cells", (609042, 6608834, 120), face_bad, "down", 20, True,
     "the 51 m patch at Grewingk's ice face labelled water > 1 m above the level"),
    ("face_sounding_misfit_p90_m", (609219, 6608700, 300), sounding_misfit, "down", 0.5, True,
     "|model - sounding| p90 at Grewingk's face (replaced face_bed_p1_m 2026-10-28)"),
    ("emerald_median_depth_m", (608938, 6611619, 150), emerald_depth, "same", 3.0, True,
     "Emerald Lake's modelled depth by hypsometry (1.8 -> 26.5 m in 20e)"),
    ("band_land_cells", (609130, 6608529, 25), band_land, "down", 10, False,
     "Hig 2026-10-27: false land in a band through 59.60059,-151.06669 (a seam between two water stamps)"),
    ("face_level_spread_m", (609250, 6608700, 300), face_level_spread, "down", 0.2, False,
     "Hig 2026-10-27: pools at 50.9 and 51.3 m beside the lake's 48.9 at the face (the SfM's surface as their level)"),
    ("face_shallow_frac", (609285, 6608680, 120), face_shallow, "down", 0.02, True,
     "Hig 2026-10-27: nearly flat areas in front of the glacier (59.60043,-151.06336; 59.60355,-151.06409)"),
    ("level_below_tide_ha", None, level_below_tide, "down", 0.5, False,
     "wet cells whose level lies below low tide - 1.5 m: a bed read as a level (15 ha in 20f, pre-existing)"),
    ("perched_over_deep_ha", None, perched_over_deep, "down", 0.5, False,
     "perched water over a bed below the lowest tide (the sill test; 20h2's bay at 59.63493,-151.21814)"),
    ("clip_shelf_cells", None, clip_shelf, "down", 500, True,
     "modelled wet cells exactly 0.1 m under the water: the fill's clip (the face's flat shelves in 20h2)"),
    ("floating_level_mismatch_cells", None, floating_mismatch, "down", 20, False,
     "floating cells whose level differs from the water around them (20h2: 50.87 m ice in the 48.91 m lake)"),
    ("water_km2", None, label_km2(2), "same", 1.0, False, "total labelled water"),
    ("land_km2", None, label_km2(1), "same", 1.0, False, "total labelled land"),
    ("glacier_km2", None, label_km2(4), "same", 0.2, False, "total glacier"),
]


def measure(d):
    full = Path(d, "fill_water_level.tif").exists() and not Win.shore_only
    whole = Win(d)
    out = {}
    for name, win, fn, _, _, needs_fill, _ in ROWS:
        if needs_fill and not full:
            continue
        w = whole if win is None else Win(d, *win)
        try:
            out[name] = round(float(fn(w)), 4)
        except Exception as e:                                   # a missing raster says so; it is not a pass
            out[name] = None; print(f"  {name}: {type(e).__name__}: {e}", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir"); ap.add_argument("--tag", required=True)
    ap.add_argument("--base", default="", help="comma-separated stored tags to compare with (default: all stored)")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--shore", action="store_true", help="shore-stage rows only, at the shore stage's level")
    a = ap.parse_args()
    Win.shore_only = a.shore
    m = measure(a.out_dir)
    tags = [t for t in a.base.split(",") if t] or sorted(p.stem for p in GATE_DIR.glob("*.json") if p.stem != a.tag)
    base = {t: json.load(open(GATE_DIR / f"{t}.json")) for t in tags if (GATE_DIR / f"{t}.json").exists()}
    bad = 0
    print(f"{'row':32s} {a.tag:>10s}  " + "  ".join(f"{t:>10s}" for t in base) + "   verdict")
    for name, _, _, dirn, tol, _, why in ROWS:
        if name not in m:
            continue
        v = m[name]; prev = [b.get(name) for b in base.values()]
        pv = [p for p in prev if p is not None]
        verdict = ""
        if v is None:
            verdict = "MISSING"; bad += 1
        elif pv and dirn in ("up", "down"):
            best = max(pv) if dirn == "up" else min(pv)
            worse = (best - v) if dirn == "up" else (v - best)
            if worse > tol:
                verdict = f"REGRESSED (best {best:g})"; bad += 1
            elif -worse > tol:
                verdict = "improved"
        elif pv and dirn == "same":
            last = pv[-1]
            if abs(v - last) > tol:
                verdict = f"MOVED from {last:g}"; bad += 1
        fmt = lambda x: f"{x:10.4g}" if x is not None else f"{'-':>10s}"
        print(f"{name:32s} {fmt(v)}  " + "  ".join(fmt(p) for p in prev) + f"   {verdict}")
    if a.save:
        GATE_DIR.mkdir(exist_ok=True)
        json.dump(m, open(GATE_DIR / f"{a.tag}.json", "w"), indent=1)
    print(f"\n{bad} row(s) regressed, moved or missing" if bad else "\nno row regressed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
