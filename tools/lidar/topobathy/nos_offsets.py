#!/usr/bin/env python3
"""Horizontal offsets of the old NOS hydrographic surveys, one translation per survey, from the evidence.

    nos_offsets.py OLD_CSV NEW_CSV --boxes '/Volumes/Nunatak/lidar_build/composite/kj_r*c*/out_run2/kj_r*c*.tif' \
        --out /Volumes/Nunatak/lidar_build/obs/soundings/nos_offsets.json

WHY (Hig, 2026-11-01, Taylor Bay): the 1930-31 lead-line surveys' positions fall on land and sit systematically
south-west of the bay they sounded.  NCEI's GEODAS files record the horizontal datum as NAD83 and the ORIGINAL as
"Early Alaska Datums" (the survey's own local astronomic datum); the conversion NCEI applied is off by hundreds of
metres here, consistently within a survey.  Our reading of the files (NAD83 -> EPSG:6334) is right.

TWO INDEPENDENT MEASUREMENTS of one translation (dx east, dy north, metres) per survey, over a +-800 m grid:
  land   the shift that puts the fewest casts on ground above MHHW (the modern lidar's land: a lead line cannot
         have been cast there), summed over every box product the survey touches; among ties the smallest shift.
         Only a survey with casts near a shore has leverage (as-is count > 0); an offshore survey says nothing.
  depth  where a survey overlaps a 1977-84 echo-sounder survey (positions good: the 1980 control sits at zero),
         the shift that minimises the robust scatter (MAD) of old depth minus modern depth over pairs within
         `match_m`; a constant vertical difference (the 1964 subsidence) is allowed.  Needs >= 50 pairs.
The two agree to ~50 m where both exist (H05187: land +400/+340, depth +420/+340; H05188: +240/+260 vs
+380/+360 -- the land test has little leverage there, 81 casts).  The CHOICE: depth when it has >= 100 pairs inside
the search range (it uses the bed itself), else land when it has leverage (>= 10 casts on land as-is, at least
halved by the shift), else the season group's median vector (surveys numbered together were run together on one
local datum; recorded as "inferred"), else none.  Measured 2026-11-01: the 1930-31 surveys +380..420 E / +300..360 N
(H05082, H05101, H05186, H05187, H05188, H05192; six more inferred at the median +400/+320), the 1915 survey H03802
+240/+260 by depth, the 1927 surveys no evidence (left in place).  Everything is written to the JSON with its evidence so the record
says what was done; nos_soundings_csv.py --offsets applies it, and the packages carry it in their notes.
"""
import argparse, csv, glob, json, os, collections
import numpy as np


def load(path):
    out = collections.defaultdict(list)
    for r in csv.DictReader(open(path)):
        out[r["survey"]].append((float(r["x"]), float(r["y"]), float(r["depth_m"])))
    return {k: np.array(v) for k, v in out.items()}


def land_test(P_by, boxes, R, step, mhhw):
    import rasterio
    counts = {sv: collections.Counter() for sv in P_by}; zero = collections.Counter(); n_used = collections.Counter()
    for box in boxes:
        with rasterio.open(box) as ds:
            z = ds.read(1).astype(np.float32); nd = ds.nodata; T = ds.transform; H, W = z.shape
        land = np.isfinite(z) & (z != nd) & (z > mhhw); del z
        x0, y0, x1, y1 = T.c, T.f - H * abs(T.e), T.c + W * T.a, T.f
        for sv, P in P_by.items():
            m = (P[:, 0] >= x0 + R) & (P[:, 0] <= x1 - R) & (P[:, 1] >= y0 + R) & (P[:, 1] <= y1 - R)
            if m.sum() < 20:
                continue
            Q = P[m]; n_used[sv] += int(m.sum())
            for dx in range(-R, R + 1, step):
                for dy in range(-R, R + 1, step):
                    c = ((Q[:, 0] + dx - T.c) / T.a).astype(int); r = ((T.f - (Q[:, 1] + dy)) / abs(T.e)).astype(int)
                    nl = int(land[r, c].sum())
                    counts[sv][(dx, dy)] += nl
                    if dx == 0 and dy == 0:
                        zero[sv] += nl
    out = {}
    for sv, cnt in counts.items():
        if not cnt:
            continue
        best = min(cnt.items(), key=lambda kv: (kv[1], abs(kv[0][0]) + abs(kv[0][1])))
        ties = sum(1 for v in cnt.values() if v == best[1])
        out[sv] = dict(n=n_used[sv], as_is_on_land=zero[sv], dx=best[0][0], dy=best[0][1], on_land=best[1], ties=ties)
    return out


def depth_test(P_by, new, R, step, match_m):
    from scipy.spatial import cKDTree
    tree = cKDTree(new[:, :2]); out = {}
    for sv, P in P_by.items():
        d0, _ = tree.query(P[:, :2], distance_upper_bound=1000.0)
        Q = P[np.isfinite(d0)]
        if len(Q) < 100:
            continue
        rows = []
        for dx in range(-R, R + 1, step):
            for dy in range(-R, R + 1, step):
                d, i = tree.query(Q[:, :2] + [dx, dy], distance_upper_bound=match_m)
                ok = np.isfinite(d)
                if ok.sum() < 50:
                    continue
                diff = Q[ok, 2] - new[i[ok], 2]
                rows.append((float(np.median(np.abs(diff - np.median(diff)))), int(ok.sum()), float(np.median(diff)), dx, dy))
        if not rows:
            continue
        rows.sort(); b = rows[0]
        z0 = [t for t in rows if t[3] == 0 and t[4] == 0]
        out[sv] = dict(n=len(Q), dx=b[3], dy=b[4], mad_m=round(b[0], 2), pairs=b[1], vertical_m=round(b[2], 2),
                       as_is_mad_m=round(z0[0][0], 2) if z0 else None, as_is_pairs=z0[0][1] if z0 else 0)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("old"); ap.add_argument("new")
    ap.add_argument("--boxes", required=True, help="glob of box products (the modern land)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--years", default=None, help="JSON {survey: year} (the CSV has none); default: nos_<id> groups by id range")
    ap.add_argument("--range-m", type=int, default=800); ap.add_argument("--step-m", type=int, default=20)
    ap.add_argument("--mhhw", type=float, default=4.24); ap.add_argument("--match-m", type=float, default=25.0)
    a = ap.parse_args()
    old = load(a.old); new = np.vstack(list(load(a.new).values()))
    boxes = sorted(glob.glob(a.boxes))
    land = land_test(old, boxes, a.range_m, a.step_m, a.mhhw)
    depth = depth_test(old, new, a.range_m, a.step_m, a.match_m)
    years = json.load(open(a.years)) if a.years else {}
    def group(sv):   # the season: surveys numbered together were run together on one local datum
        if sv in years: return str(years[sv])
        n = int(sv.split("_h")[-1])
        return "1930-31" if 5080 <= n <= 5200 else "1927" if 4700 <= n <= 4800 else "1915" if n < 4000 else "?"
    res = {}
    for sv in sorted(old):
        g = group(sv); L = land.get(sv); D = depth.get(sv)
        # evidence worth using: a depth match with >= 100 pairs inside the search range; a land test with >= 10 casts
        # on land as-is that the shift at least halves (an offshore survey has no leverage: every shift ties)
        d_ok = D and D["pairs"] >= 100 and max(abs(D["dx"]), abs(D["dy"])) < a.range_m - a.step_m
        l_ok = L and L["as_is_on_land"] >= 10 and L["on_land"] <= L["as_is_on_land"] // 2
        if d_ok:
            res[sv] = dict(dx=D["dx"], dy=D["dy"], method="depth", group=g)
        elif l_ok:
            res[sv] = dict(dx=L["dx"], dy=L["dy"], method="land", group=g)
        else:
            res[sv] = dict(dx=None, dy=None, method="none", group=g)
        res[sv]["land"] = L; res[sv]["depth"] = D
    # the group vector for surveys with no evidence of their own
    for g in {r["group"] for r in res.values()}:
        meas = [(r["dx"], r["dy"]) for r in res.values() if r["group"] == g and r["method"] in ("depth", "land")]
        if not meas:
            continue
        mx, my = float(np.median([m[0] for m in meas])), float(np.median([m[1] for m in meas]))
        for sv, r in res.items():
            if r["group"] == g and r["method"] == "none":
                r.update(dx=round(mx), dy=round(my), method="inferred (group median of %d surveys)" % len(meas))
    doc = dict(note=__doc__.strip().split("\n")[0], made=__import__("datetime").datetime.now().isoformat(timespec="seconds"),
               params=dict(range_m=a.range_m, step_m=a.step_m, mhhw=a.mhhw, match_m=a.match_m, boxes=len(boxes)),
               surveys=res)
    json.dump(doc, open(a.out, "w"), indent=1)
    for sv, r in res.items():
        L, D = r["land"], r["depth"]
        print(f"{sv} {r['group']:8s} dx {str(r['dx']):>5s} dy {str(r['dy']):>5s}  {r['method']:34s} "
              f"land: {('n=%d as-is %d -> %d at %+d/%+d (ties %d)' % (L['n'], L['as_is_on_land'], L['on_land'], L['dx'], L['dy'], L['ties'])) if L else '-':48s} "
              f"depth: {('n=%d MAD %.2f->%.2f at %+d/%+d, pairs %d, vert %+.2f' % (D['n'], D['as_is_mad_m'] or -1, D['mad_m'], D['dx'], D['dy'], D['pairs'], D['vertical_m'])) if D else '-'}")


if __name__ == "__main__":
    main()
