"""The sea's shore built from TIDE-LEVEL CONTOURS (2026-11-03, Hig: "generating smooth contours in the intertidal that are
consistent with the data ... shorelines convex immediately below low tide ... they agree with soundings ... the contours
become tide levels, so I can edit them"; and the reference "a datum lower than MLLW": the tide falls below MLLW and wave action
reworks sediment to a bit below low tide).

A lake is well posed because it has a shoreline at a known level.  The land rim is no such line for the sea: it is off by the whole
intertidal width.  So, in a floor-path sea domain (no observed water surface):
  1 THE BASE CONTOUR at shore_base_m (MLLW - 2 m; in the lidar + multibeam the steep tail of slopes relaxes to the deep background by
    about -4 m, and the lowest tide stand is about MLLW - 1.5 m): the level set of a smooth field of the data clipped to base +- clip_m --
    observed cells, the DEM's ground, casts, edited tide lines, and the rim's slope extended as a weak prior, which alone says
    where the contour lies off a cliff with no data.  Clipping means a 2 ft cast and a 15 fathom cast each say only "above"
    and "below": no compromise depth.  A smooth field has a smooth level set: no pixel steps of the DEM reach the contour.
  2 THE INTERTIDAL, landward: a harmonic surface between its contours -- the land (its own height; off a cliff, the cliff's foot at
    tide_max_m), the DEM's ground, edited tide lines and the base contour.  The maximum principle makes it monotone between contour
    lines, so no terrace, sawtooth or trough can form.  Casts here adjust it softly.
  3 THE BASIN, seaward: the lake recipe with the base contour as its shore -- a smooth prior from the casts (a kernel never narrower than
    the casts' spacing: no discs), a rollover below the contour (slope continuous with the intertidal's, steepening to the prior:
    convex), and the thin-plate spline through the casts at their calibrated sigmas.
Edited lines live in `tide_lines` (GeoJSON in the grid's CRS, LineStrings with property level_m): they are data in steps 1 and 2, so a line
traced from imagery at a known tide moves the surface on the next run.  The model's own contours are written as shore_contours.geojson."""
from __future__ import annotations
import csv, json, os
import numpy as np
from scipy import ndimage

LEVELS = (("base", None), ("MLLW", "low_tide_m"), ("0 m NAVD88", 0.0), ("MHW", 2.9), ("MHHW", 3.4))     # for reading the surface; only the mapped lines and a reviewer's lines constrain it


def _cast_lines(xy, z, sg, P):
    """Troughs and ridges the casts themselves trace, on their Delaunay triangulation (2026-11-03, Hig, Taylor: -18, -25, -32 m in a line with -3 to -7 m on both sides; the plane test called the middle one a
    spike and the spline drew blobs).  A line is three casts: a, i, b natural neighbours of i, nearly straight, i within half the feature's prominence of the a-b chord, and the neighbours on BOTH
    sides of it standing shallower (a trough) or deeper (a ridge) by at least shore_line_prom_m and 2 sigma.  A survey line running along a slope has one side up and one down and is no line.
    Not through a narrow gap between contradictory readings: every flank is held to shore_line_flank_deg from the line, the nearest flank on each side to shore_line_flank_ratio x the step along it, and a step is at most shore_line_max_m.
    Each cast keeps its best line of each kind.  Returns [(a, b, kind)] edges."""
    from scipy.spatial import Delaunay
    if len(xy) < 5:
        return []
    Lmax = float(P.get("shore_line_max_m", 250.0)); turn = np.cos(np.radians(float(P.get("shore_line_turn_deg", 35.0))))
    T0 = float(P.get("shore_line_prom_m", 5.0)); fl = np.tan(np.radians(float(P.get("shore_line_flank_deg", 40.0)))); al = np.tan(np.radians(float(P.get("shore_line_along_deg", 30.0)))); fr = float(P.get("shore_line_flank_ratio", 1.5))
    try:
        D = Delaunay(xy)
    except Exception:
        return []
    ip, nb = D.vertex_neighbor_vertices
    edges = {}
    for i in range(len(xy)):
        n = nb[ip[i]:ip[i + 1]]; v = xy[n] - xy[i]; d = np.hypot(v[:, 0], v[:, 1])
        k = (d > 1.0) & (d <= Lmax); n, v, d = n[k], v[k], d[k]
        if n.size < 4:
            continue
        best = {}
        for p_ in range(n.size):
            for q_ in range(p_ + 1, n.size):
                if -(v[p_] @ v[q_]) / (d[p_] * d[q_]) < turn:              # a -> i -> b goes on nearly straight
                    continue
                a, b = int(n[p_]), int(n[q_]); w = d[p_] / (d[p_] + d[q_]); zc = z[a] + w * (z[b] - z[a])
                if abs(z[a] - z[i]) > al * d[p_] or abs(z[b] - z[i]) > al * d[q_]:
                    continue
                u = (v[q_] - v[p_]) / (d[p_] + d[q_]); side = v[:, 0] * u[1] - v[:, 1] * u[0]
                rest = np.ones(n.size, bool); rest[[p_, q_]] = False
                L = rest & (side > 0); R = rest & (side < 0)
                if not (L.any() and R.any()):
                    continue
                fL, fR = np.median(z[n[L]]), np.median(z[n[R]]); e = abs(z[i] - zc)
                for kind, prom in (("trough", min(fL, fR) - max(z[i], zc)), ("ridge", min(z[i], zc) - max(fL, fR))):
                    if prom < max(T0, 2.0 * float(np.mean(sg[[a, i, b]]))) or e > 0.5 * prom:
                        continue
                    if (np.abs(z[n[rest]] - z[i]) > fl * np.maximum(np.abs(side[rest]), 1.0)).any():
                        continue
                    if max(np.abs(side[L]).min(), np.abs(side[R]).min()) > fr * max(d[p_], d[q_]):       # casts close along a track with the next track far off show no direction: a track reading deep is no trough
                        continue
                    if kind not in best or prom - e > best[kind][0]:
                        best[kind] = (prom - e, a, b)
        for kind, (_, a, b) in best.items():
            edges[(min(a, i), max(a, i), kind)] = 1; edges[(min(b, i), max(b, i), kind)] = 1
    return [(a, b, k) for a, b, k in edges]


def _casts(P, T, shape, C, log):
    """Point depths in the cells C, despiked per survey: rows (r, c, x, y, z, sigma); and the lines they trace (_cast_lines): sites along them, same columns, and the line features for review."""
    from .fill import _points_files
    from scipy.spatial import cKDTree
    out, sv, held = [], [], []
    for path in _points_files(P):
        with open(path) as fh:
            for row in csv.DictReader(fh):
                if row.get("bound") or row.get("z") in (None, ""):
                    continue
                x, y = float(row["x"]), float(row["y"]); cc, rr = ~T * (x, y); rr, cc = int(rr), int(cc)
                if 0 <= rr < shape[0] and 0 <= cc < shape[1] and C[rr, cc]:
                    out.append((rr, cc, x, y, float(row["z"]), float(row.get("sigma") or P["points_sigma_m"]))); sv.append(row.get("survey", "")); held.append(row.get("held") == "1")
    if not out:
        return np.zeros((0, 6)), np.zeros((0, 6)), []
    S = np.array(out, np.float64); sv = np.array(sv); held = np.array(held, bool); keep = np.ones(len(S), bool)      # held: a reviewer's move or use (cast_edits.py)
    # the lines first, over every survey: a cast on one is no spike
    Zb = float(P.get("shore_base_m", float(P["low_tide_m"]) - 2.0)); sub = np.flatnonzero(S[:, 4] < Zb - 0.1); on_line = np.zeros(len(S), bool)
    sites, feats = [], []
    step = float(P.get("shore_line_site_m", 30.0))
    for a, b, kind in _cast_lines(S[sub, 2:4], S[sub, 4], S[sub, 5], P):
        a, b = sub[a], sub[b]; L = float(np.hypot(*(S[b, 2:4] - S[a, 2:4]))); w = np.linspace(0.0, 1.0, max(int(L / step), 1) + 1)[1:-1]
        xs, ys = S[a, 2] + w * (S[b, 2] - S[a, 2]), S[a, 3] + w * (S[b, 3] - S[a, 3]); cc, rr = ~T * (xs, ys); rr, cc = rr.astype(int), cc.astype(int)
        if not C[rr, cc].all():                                     # across land or an observed surface: no line
            continue
        on_line[[a, b]] = True
        sites.append(np.c_[rr, cc, xs, ys, S[a, 4] + w * (S[b, 4] - S[a, 4]), np.full(w.size, max(S[a, 5], S[b, 5]))])
        feats.append(dict(type="Feature", geometry=dict(type="LineString", coordinates=[[S[a, 2], S[a, 3]], [S[b, 2], S[b, 3]]]),
                          properties=dict(kind=kind, z_a=S[a, 4], z_b=S[b, 4], survey_a=str(sv[a]), survey_b=str(sv[b]), length_m=round(L, 1))))
    B = np.vstack(sites) if sites else np.zeros((0, 6))
    n_need = int(P.get("points_despike_n", 5)); R = float(P.get("points_despike_deep_r_m", 150.0)); D0 = float(P.get("points_despike_deep_m", 10.0))
    for name in np.unique(sv):
        ii = np.flatnonzero(sv == name)
        if ii.size <= n_need:
            continue
        for a, nb in zip(ii, cKDTree(S[ii, 2:4]).query_ball_point(S[ii, 2:4], R)):
            nb = ii[np.asarray(nb, int)]; dxy = S[nb, 2:4] - S[a, 2:4]; nb = nb[np.hypot(dxy[:, 0], dxy[:, 1]) > 5.0]
            if nb.size < n_need:
                continue
            G = np.c_[np.ones(nb.size), S[nb, 2:4] - S[a, 2:4]]; cf, *_ = np.linalg.lstsq(G, S[nb, 4], rcond=None); rs = S[nb, 4] - G @ cf
            if abs(S[a, 4] - cf[0]) > max(D0, 3.0 * 1.4826 * np.median(np.abs(rs - np.median(rs)))) and not on_line[a] and not held[a]:
                keep[a] = False
    if (~keep).any():
        log(f"  shoreface: {int((~keep).sum())} of {len(S)} casts stand > {D0:g} m (and 3 scatters) off their own survey's neighbours: dropped")
    if feats:
        log(f"  shoreface: the casts trace {sum(f['properties']['kind'] == 'trough' for f in feats)} trough and {sum(f['properties']['kind'] == 'ridge' for f in feats)} ridge segments ({len(B)} sites on them); "
            f"{int(on_line.sum())} casts on them")
    return S[keep], B, feats


def _tide_lines(P, T, shape, res, log):
    """Edited lines rasterised: (level grid, NaN off the lines; per line in the grid, its cells for the edit check)."""
    L = np.full(shape, np.nan, np.float32); path = P.get("tide_lines"); items = []
    if not path:
        return L, items
    try:
        fc = json.load(open(path))
    except FileNotFoundError:
        return L, items
    n = 0
    for fi, f in enumerate(fc.get("features", [])):
        lv = (f.get("properties") or {}).get("level_m"); g = f.get("geometry") or {}
        if lv is None or g.get("type") not in ("LineString", "MultiLineString"):
            continue
        for line in ([g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]):
            for (x0, y0, *_), (x1, y1, *_) in zip(line[:-1], line[1:]):
                k = max(int(np.hypot(x1 - x0, y1 - y0) / res), 1); t = np.linspace(0, 1, k + 1)
                c, r = ~T * (x0 + t * (x1 - x0), y0 + t * (y1 - y0)); r = r.astype(int); c = c.astype(int)
                ok = (r >= 0) & (r < shape[0]) & (c >= 0) & (c < shape[1])
                if ok.any():
                    L[r[ok], c[ok]] = float(lv); n += 1
                    items.append((fi, float(lv), r[ok], c[ok]))
    log(f"  shoreface: edited tide lines from {path}: {n} segments in the grid")
    return L, items


def _cast_review(P, T, shape, S, C0, C, hi, z, fill, Z0, crs, U, lines, res, Zf):
    """Every cast in the grid with what the model did with it, for review (2026-11-03, Hig: 3DEP wins over a sounding, but a cast it contradicts puts its neighbours in question --
    a cast is wrong in its position rather than its depth, and so is the survey around it).  status: used | not_fit (past the despike, but on the wrong side of the base contour for either fit; line_m: how far the nearest drawn line, which won) | spike (the despike dropped it) | land (under 3DEP land: ignored) |
    observed (under an observed surface); contradicted: the surface stands more than max(3 m, 2 sigma) off it; near_land: same survey within shore_review_near_m of a land contradiction."""
    from .fill import _points_files
    from scipy.spatial import cKDTree
    used = {(round(a, 2), round(b, 2)) for a, b in S[:, 2:4]} if len(S) else set()
    dl = ndimage.distance_transform_edt(~np.isfinite(lines), sampling=res) if np.isfinite(lines).any() else np.full(shape, np.nan)
    rows = []
    for path in _points_files(P):
        with open(path) as fh:
            for row in csv.DictReader(fh):
                if row.get("bound") or row.get("z") in (None, ""):
                    continue
                x, y = float(row["x"]), float(row["y"]); cc, rr = ~T * (x, y); rr, cc = int(rr), int(cc)
                if not (0 <= rr < shape[0] and 0 <= cc < shape[1]):
                    continue
                zc = float(row["z"]); sg = float(row.get("sigma") or P["points_sigma_m"])
                if hi[rr, cc]:
                    st, sf = "land", z[rr, cc]
                elif C[rr, cc]:
                    st, sf = ("used" if (round(x, 2), round(y, 2)) in used else "spike"), fill[rr, cc]
                    if st == "used" and not ((U[rr, cc] and zc >= Z0 - 0.1) or (not U[rr, cc] and zc < Zf[rr, cc] - 0.1)):
                        st = "not_fit"
                elif C0[rr, cc] or not np.isfinite(z[rr, cc]):
                    continue
                else:
                    st, sf = "observed", z[rr, cc]
                sf = float(sf); r = sf - zc
                if not np.isfinite(sf):
                    continue
                rows.append(dict(x=round(x, 2), y=round(y, 2), z=zc, sigma=sg, survey=row.get("survey", ""), key=f'{row.get("survey", "")}:{row.get("id", "")}', moved=row.get("moved") == "1", file=os.path.basename(path), status=st, surface=round(sf, 2), residual=round(r, 2),
                                 contradicted=bool(abs(r) > max(3.0, 2.0 * sg)), near_land=False, line_m=round(float(dl[rr, cc]), 1) if np.isfinite(dl[rr, cc]) else None))
    bad = [q for q in rows if q["contradicted"] and q["surface"] > Z0 and q["status"] in ("land", "observed") and q["z"] < Z0]
    for name in {q["survey"] for q in bad}:
        same = [q for q in rows if q["survey"] == name]
        tree = cKDTree([(q["x"], q["y"]) for q in same]); seen = set()
        for i_ in tree.query_ball_point([(q["x"], q["y"]) for q in bad if q["survey"] == name], float(P.get("shore_review_near_m", 150.0))):
            seen.update(i_)
        for i_ in seen:
            same[i_]["near_land"] = True
    for q in bad:
        q["near_land"] = False
    return dict(type="FeatureCollection", crs=dict(type="name", properties=dict(name=str(crs) if crs is not None else "EPSG:6334")),
                features=[dict(type="Feature", geometry=dict(type="Point", coordinates=[q.pop("x"), q.pop("y")]), properties=q) for q in rows])


def _smooth_field(mass, val, sig):
    from .fill import _gsmooth
    num = _gsmooth(mass * val, sig); den = _gsmooth(mass, sig)
    return num / np.maximum(den, 1e-9)


def _basin_prior(rr, cc, zz, wt, shape, c):
    """Smooth surface through the casts and the observed bed on a c-cell lattice: normalised Gaussians, coarse to fine, each finer one
    taken only where it holds enough mass, so its bandwidth is never narrower than the data's spacing and no cast owns a disc."""
    from .fill import _gsmooth
    H, W = shape[0] // c + 1, shape[1] // c + 1
    num = np.zeros((H, W)); den = np.zeros((H, W)); np.add.at(num, (rr // c, cc // c), wt * zz); np.add.at(den, (rr // c, cc // c), wt)
    out = None
    for s in (64, 32, 16, 8, 4):                                   # sigma in lattice cells
        d = _gsmooth(den, s); v = _gsmooth(num, s) / np.maximum(d, 1e-12)
        a = np.clip(d * 2 * np.pi * s * s / 4.0 - 0.5, 0, 1)       # the mass within the kernel, in casts: below ~1.5 the coarser scale stands
        out = v if out is None else a * v + (1 - a) * out
    return out


def _ground_sea(P, T, shape):
    from .fill import _read_on_grid
    if not P.get("ground_water") or not P.get("tide_sea"):
        return np.zeros(shape, bool)
    return np.nan_to_num(_read_on_grid(P["ground_water"], T, P["_crs"], shape, (0, shape[0]), (0, shape[1]), nearest=True), nan=0.0) > 1.5


def contour_sea(z, hole, dom, lab, ids, rim, land_rim, srim, res, P, log=print, dry_from=None):
    """Returns dict(cells, fill, mask, sigma, contours) over the floor-path sea domains `ids`, or None."""
    from .fill import harmonic, _spline_residual, _dem_band, _dem_body_ground, _gsmooth
    from .fill import _read_on_grid
    T = P["_transform"]; shape = z.shape
    C = dom & hole & np.isin(lab, list(ids))
    if not C.any():
        return None
    top_ = float(P.get("tide_max_m", 4.5))
    if dry_from is not None and P.get("ground_prior") and P.get("_crs") is not None:
        # A DRY VOID BESIDE THE SEA IS THE SEA'S WHERE ITS GROUND PRIOR IS IN THE TIDE'S REACH (2026-11-03, Hig: the flat at 59.32416,-151.03250 ending in a straight
        # line: a void with a high rim, a domain of its own, bridged as a plate at 2 m against the sea at -4 along 3DEP's water edge).  It joins the sea's domain; its
        # high ground then stands on the prior like the sea's own.
        pz_ = _read_on_grid(P["ground_prior"], T, P["_crs"], shape, (0, shape[0]), (0, shape[1]))
        t_ = ndimage.binary_dilation(C, structure=np.ones((3, 3), bool)) & dom & hole & ~C & (lab > dry_from) & np.isfinite(pz_) & ((pz_ < top_ + 1.0) | _ground_sea(P, T, shape))
        if t_.any():
            tid = np.unique(lab[t_])
            if P.get("_bodies") is not None:
                tid = np.setdiff1d(tid, np.unique(lab[dom & (P["_bodies"][0] > 0)]))
            if tid.size:
                _, (ni, nj) = ndimage.distance_transform_edt(~C, return_indices=True)
                ad = dom & hole & np.isin(lab, tid); lab = lab.copy()
                first = {}
                rt, ct = np.nonzero(t_)
                for r0_, c0_ in zip(rt, ct):
                    first.setdefault(int(lab[r0_, c0_]), int(lab[ni[r0_, c0_], nj[r0_, c0_]]))
                for i_ in tid:
                    lab[dom & (lab == i_)] = first[int(i_)]
                C = C | ad
                log(f"  shoreface: {tid.size} dry voids beside the sea with ground in the tide's reach join it: {ad.sum() * res * res / 1e4:.1f} ha")
                del ni, nj
        del pz_, t_
    if os.environ.get("SHORE_DUMP"):
        import pickle
        pickle.dump(dict(z=z, hole=hole, dom=dom, lab=lab, ids=ids, rim=rim, land_rim=land_rim, srim=srim, res=res, P=P, dry_from=dry_from), open(os.environ["SHORE_DUMP"], "wb"), protocol=4)
    Z0 = float(P.get("shore_base_m", float(P["low_tide_m"]) - 2.0)); clip = float(P.get("shore_clip_m", 3.0)); top = float(P.get("tide_max_m", 4.5))
    sg = max(float(P.get("shore_level_smooth_m", 40.0)) / res, 1.0); c = 4
    C0 = C.copy(); hi = np.zeros(shape, bool); hi_z = None; el = None
    if P.get("ground_prior") and P.get("_crs") is not None:
        # THE GROUND PRIOR ABOVE THE TIDE'S REACH IS LAND, wherever it stands in the sea's void (the large-void rule, fill_mask 8, made general: 3DEP at 19 and
        # 8 m below ArcticDEM's blocky no-data edge was modelled as sea, a stair-stepped wall).  It meets the observations beside it by a fading misfit, so
        # the blocks of that edge do not show.
        el, pz, _ = _dem_band(C, P, shape, res, islands=True)
        hi = C & np.isfinite(pz) & (pz >= top + 1.0) & ~_ground_sea(P, T, shape)      # mapped high-tide water: 3DEP's smear of a cliff there is no land (tide_vectors.py)
        if P.get("_bodies") is not None:
            hi &= (P["_bodies"][0] <= 0) | _dem_body_ground(pz, P, res, lo=top + 1.0)      # an island inside a tidal body is land too (3DEP wins over a sounding, Hig 2026-11-03)
        if hi.any():
            ob0 = np.isfinite(z); edge0 = ob0 & ndimage.binary_dilation(hi, structure=np.ones((3, 3), bool))
            sb_ = max(30.0 / res, 1.0); m0 = _gsmooth(edge0.astype(np.float32), sb_)
            mis = _gsmooth(np.where(edge0, z - np.nan_to_num(pz), 0.0).astype(np.float32), sb_) / np.maximum(m0, 1e-6)
            hi_z = (pz + mis * (m0 / (m0 + 0.002))).astype(np.float32)
            z = z.copy(); z[hi] = hi_z[hi]; C = C & ~hi
            nr = hi & ndimage.binary_dilation(C, structure=np.ones((3, 3), bool))
            land_rim = land_rim | nr
            from .fill import slope_deg
            s2 = slope_deg(z, res); srim = srim.copy(); srim[nr] = np.clip(np.nan_to_num(_smooth_field(np.isfinite(s2).astype(np.float32), np.nan_to_num(s2), max(10.0 / res, 1.0))[nr], nan=1.0), float(P["slope_min_deg"]), float(P["slope_max_deg"]))
            log(f"  shoreface: the ground prior at or above {top + 1.0:g} m stands as land on {hi.sum() * res * res / 1e4:.1f} ha of the sea's void")
    S, Bl, line_feats = _casts(P, T, shape, C, log); lines, line_items = _tide_lines(P, T, shape, res, log)
    Ll, Hw_, Lw_, Hl_ = _mapped_shores(P, T, shape)
    Lnear = Hnear = None
    if Ll is not None:
        _, (li_, lj_) = ndimage.distance_transform_edt(~np.isfinite(Ll), return_indices=True); Lnear = Ll[li_, lj_]; del li_, lj_
    if Hl_ is not None and np.isfinite(Hl_).any():
        _, (li_, lj_) = ndimage.distance_transform_edt(~np.isfinite(Hl_), return_indices=True); Hnear = Hl_[li_, lj_]; del li_, lj_
    S, Bl, line_feats, rock_rows = _rock_soundings(P, T, res, C, S, Bl, line_feats, Ll, Lnear, log)
    # the rim's slope extended: the weak prior, and off a cliff the foot
    d, (ri, ci) = ndimage.distance_transform_edt(~land_rim, return_indices=True, sampling=res)
    ext = z[ri, ci] - np.tan(np.radians(srim[ri, ci])) * d
    dem = None
    if el is not None:
        el = el & C
        if el.any():
            dem = np.where(el, _smooth_field(el.astype(np.float32), np.nan_to_num(pz), max(10.0 / res, 1.0)), np.nan).astype(np.float32)
            # ...and it meets the observations beside it by a fading misfit, as the high ground does (2026-11-03, Hig: the rectilinear low area at 59.31980,-151.04149: an
            # ArcticDEM block 0.8 m under 3DEP on the flat, its outline a step, inside both sources' uncertainty)
            ob0 = np.isfinite(z); e0_ = ob0 & ndimage.binary_dilation(el, structure=np.ones((3, 3), bool))
            if e0_.any():
                sb_ = max(30.0 / res, 1.0); m0 = _gsmooth(e0_.astype(np.float32), sb_)
                dem = dem + (_gsmooth(np.where(e0_, z - np.nan_to_num(pz), 0.0).astype(np.float32), sb_) / np.maximum(m0, 1e-6) * (m0 / (m0 + 0.002))).astype(np.float32)
    # an edited line outranks the DEM where they meet (SHORE_REVIEW_WORKFLOW.md 2), or the two fight over the cells beside it
    if dem is not None and np.isfinite(lines).any():
        dem = np.where(ndimage.distance_transform_edt(~np.isfinite(lines), sampling=res) <= float(P.get("shore_line_clear_m", 20.0)), np.nan, dem).astype(np.float32)
    # ...and the mapped high-tide water is the sea's: the DEM has no say in it (2026-11-04, Hig; the land side is high_line.py's)
    if dem is not None and P.get("tide_sea"):
        dem = np.where(_ground_sea(P, T, shape), np.nan, dem).astype(np.float32)
    # 1 THE BASE CONTOUR
    # The field starts from the rim's slope extended -- a steep wall puts the contour close, a gentle shore far -- and only DATA move it: the land
    # is the rim and has no vote (its Gaussian tail outvoted everything for 190 m and stranded the fjord walls' deep casts inside the intertidal).
    ob = np.isfinite(z)
    e0 = ndimage.gaussian_filter(np.clip(ext, Z0 - clip, Z0 + clip).astype(np.float32), max(10.0 / res, 1.0))
    mass = np.zeros(shape, np.float32); dv = np.zeros(shape, np.float32)
    k = ob & (z <= Z0) & ~np.isnan(z); mass[k] = 1.0; dv[k] = np.clip(z[k], Z0 - clip, Z0 + clip) - e0[k]     # a measured bed below the base
    if dem is not None:
        k = np.isfinite(dem); mass[k] = 1.0; dv[k] = np.clip(dem[k], Z0 - clip, Z0 + clip) - e0[k]
    # only a line at or under the base says where the base is: one higher up only holds its own room off the sea (below), or a long shore of them lifts the field for 100 m and
    # strands the casts in the intertidal (2026-11-04: Hig's tide polygons on Taylor, cast p90 1.9 m -> 18.6 m)
    k = np.isfinite(lines) & (lines <= Z0 + float(P.get("shore_line_base_m", 0.5))); mass[k] = 400.0; dv[k] = np.clip(lines[k], Z0 - clip, Z0 + clip) - e0[k]
    if len(S):
        r_, c_ = S[:, 0].astype(int), S[:, 1].astype(int)
        np.maximum.at(mass, (r_, c_), np.minimum(1.0, (2.0 / S[:, 5]) ** 2) * float(P.get("shore_cast_mass", 300.0)))
        dv[r_, c_] = np.clip(S[:, 4], Z0 - clip, Z0 + clip) - e0[r_, c_]
    gm_ = _gsmooth(mass, sg)
    F = e0 + _gsmooth(mass * dv, sg) / (gm_ + float(P.get("shore_data_m0", 0.02)))
    Sea = C & (F <= Z0); U = C & ~Sea
    # NO WALL BETWEEN THE TIDE AND THE BASE (2026-11-03, Hig: a coastal flat abruptly dropping to the bathymetry): the base contour keeps the distance a shore face of
    # shore_face_deg (or the land's own slope, off a steeper rim) needs from anything standing higher
    sf_ = np.tan(np.radians(float(P.get("shore_face_deg", 12.0)))); nc0_ = 0
    dfix = float(P.get("shore_dem_fix_min_m", 1.0))
    for h_ in (0.0, 1.0, 2.0, 3.0, top):
        gr_ = land_rim & (z >= h_)
        if gr_.any():
            d_, (i_, j_) = ndimage.distance_transform_edt(~gr_, return_indices=True, sampling=res)
            nr_ = d_ < (h_ - Z0) / np.maximum(sf_, np.tan(np.radians(srim[i_, j_]))); del i_, j_
            Sea &= ~nr_
        if dem is not None and h_ >= dfix:
            gd_ = np.isfinite(dem) & (dem >= h_)
            if gd_.any():
                Sea &= ~(ndimage.distance_transform_edt(~gd_, sampling=res) < (h_ - Z0) / sf_)
    if len(S):                                                     # ...but a good sounding says what it says 
        kg_ = (S[:, 5] <= float(P.get("shore_face_cast_sigma_m", 4.0))) & (S[:, 4] < Z0 - 1.0)
        if kg_.any():
            mg_ = np.zeros(shape, bool); mg_[r_[kg_], c_[kg_]] = True
            # ...whatever the modelled field says there (2026-11-04, Hig, 59.30601,-151.04754: a -28 m cast under a modelled contour was in neither fit); a hand-drawn line holds its room below, and wins
            Sea |= C & (ndimage.distance_transform_edt(~mg_, sampling=res) <= 20.0)
        # ...and a cast at or above the base is above the contour, by definition (2026-11-03, Hig's review, Taylor delta edge: a line of casts 2 m deep stood over a basin
        # at -20 m; they were in neither fit).  Each holds a disc off the sea, a line of them closes into one shoal, and what it cuts off with no deeper data behind it is the flat's
        ks_ = S[:, 4] >= Z0 - 0.1
        if ks_.any():
            ms_ = np.zeros(shape, bool); ms_[S[ks_, 0].astype(int), S[ks_, 1].astype(int)] = True
            rh_, rl_ = float(P.get("shore_cast_hold_m", 20.0)), float(P.get("shore_cast_link_m", 40.0))
            ds_ = ndimage.distance_transform_edt(~ms_, sampling=res)
            sh_ = (ds_ <= rh_) | (ndimage.distance_transform_edt(ds_ <= rl_, sampling=res) > rl_)
            kd_ = S[:, 4] < Z0 - 1.0
            md_ = np.zeros(shape, bool); md_[S[kd_, 0].astype(int), S[kd_, 1].astype(int)] = True
            if md_.any():
                sh_ &= ndimage.distance_transform_edt(~md_, sampling=res) > 15.0
            add_ = Sea & sh_
            if add_.any():
                Sea &= ~add_
                lb, nl = ndimage.label(Sea, structure=np.ones((3, 3), bool))
                if nl:
                    deep_ = np.zeros(nl + 1, bool); deep_[np.unique(lb[md_])] = True
                    deep_[np.unique(lb[ndimage.binary_dilation(ob & (z <= Z0), structure=np.ones((3, 3), bool))])] = True; deep_[0] = True
                    touch_ = np.zeros(nl + 1, bool); touch_[np.unique(lb[ndimage.binary_dilation(add_, structure=np.ones((3, 3), bool))])] = True; touch_[0] = False
                    big_ = int(np.argmax(np.bincount(lb.ravel(), minlength=nl + 1)[1:])) + 1; touch_[big_] = False
                    cut_ = touch_ & ~deep_
                    Sea &= ~cut_[lb]
                    log(f"  shoreface: {int(ks_.sum())} casts at or above the base hold {add_.sum() * res * res / 1e4:.2f} ha off the sea, and cut off {cut_[lb].sum() * res * res / 1e4:.2f} ha of it")
    # AN EDITED LINE AT OR ABOVE THE BASE IS ABOVE THE CONTOUR, and needs a shore face below it (the no-wall rule, counting it as higher ground); what it cuts off from the sea with no
    # deeper data behind it is the flat's.  Above casts in the order of things (SHORE_REVIEW_WORKFLOW.md 2)
    up_ = np.isfinite(lines) & (lines >= Z0 - 0.1)
    if up_.any():
        add_ = Sea & up_
        for h_ in np.unique(lines[up_]):
            add_ |= Sea & (ndimage.distance_transform_edt(~(np.isfinite(lines) & (lines >= h_)), sampling=res) < max(float(h_) - Z0, 0.0) / sf_)
        if add_.any():
            Sea &= ~add_
            lb, nl = ndimage.label(Sea, structure=np.ones((3, 3), bool))
            if nl:
                deep_ = np.zeros(nl + 1, bool); deep_[np.unique(lb[ndimage.binary_dilation(ob & (z <= Z0), structure=np.ones((3, 3), bool))])] = True
                if len(S):
                    kd_ = S[:, 4] < Z0 - 1.0; deep_[np.unique(lb[S[kd_, 0].astype(int), S[kd_, 1].astype(int)])] = True
                deep_[0] = True
                touch_ = np.zeros(nl + 1, bool); touch_[np.unique(lb[ndimage.binary_dilation(add_, structure=np.ones((3, 3), bool))])] = True; touch_[0] = False
                touch_[int(np.argmax(np.bincount(lb.ravel(), minlength=nl + 1)[1:])) + 1] = False
                cut_ = touch_ & ~deep_; Sea &= ~cut_[lb]
                log(f"  shoreface: edited lines hold {add_.sum() * res * res / 1e4:.2f} ha off the sea, and cut off {cut_[lb].sum() * res * res / 1e4:.2f} ha of it")
    # THE MAPPED SHORES ARE THE MODEL'S SHORES (2026-11-04, Hig, 59.30534,-151.04886: "it should simply be going from the low tide contour down to the deep soundings"; the base contour's reach from higher ground was a bench 30 m wide): in the low water
    # it is the sea right up to the low line; between the two lines it is the intertidal, whatever the fields above said; the base contour is left to the coast nobody mapped
    if Ll is not None:
        m_band = C & Hw_ & ~Lw_ & (ndimage.distance_transform_edt(~np.isfinite(Ll), sampling=res) <= float(P.get("shore_mapped_reach_m", 1000.0)))
        Sea = (Sea | (C & Lw_)) & ~m_band & ~(np.isfinite(lines) & (lines >= Z0 - 0.1))
        if len(S):
            ks_ = Sea[r_, c_] & (S[:, 4] >= Lnear[r_, c_] - 0.1)       # a cast above the low level in the low water is a shoal it holds off the sea
            if ks_.any():
                ms_ = np.zeros(shape, bool); ms_[r_[ks_], c_[ks_]] = True
                Sea &= ndimage.distance_transform_edt(~ms_, sampling=res) > float(P.get("shore_cast_hold_m", 20.0))
            nb_ = int((m_band[r_, c_] & (S[:, 4] < Lnear[r_, c_] - 1.0)).sum())
            if nb_:
                log(f"  shoreface: {nb_} casts more than 1 m below the low shore's level stand between the shores: not fit, for review (moved, not rejected)")
        log(f"  shoreface: mapped shores: {(C & Lw_).sum() * res * res / 1e4:.1f} ha of the sea's void in the low water, {m_band.sum() * res * res / 1e4:.1f} ha between the lines")
    else:
        m_band = np.zeros(shape, bool)
    U = C & ~Sea
    if len(S):
        nc0_ = int((U[r_, c_] & (S[:, 4] < Z0 - 1.0)).sum())
        if nc0_:
            log(f"  shoreface: {nc0_} casts more than 1 m below the base stand inside the shore face's reach of higher ground")
    # pockets: a small sea cut off from the sea is the flat's; a shoal with no data in it is the sea's
    lb, nl = ndimage.label(Sea, structure=np.ones((3, 3), bool))
    if nl:
        sz = np.bincount(lb.ravel(), minlength=nl + 1) * res * res; small = np.flatnonzero(sz < float(P.get("shore_pocket_m2", 5000.0)))
        Sea &= ~np.isin(lb, small[small > 0]); U = C & ~Sea
    lb, nl = ndimage.label(U, structure=np.ones((3, 3), bool))
    if nl:
        ad = ndimage.binary_dilation(ob, structure=np.ones((3, 3), bool))
        data = (ad & U) | (U & np.isfinite(lines)) | (np.isfinite(dem) & U if dem is not None else np.zeros(shape, bool))
        if len(S):
            data[r_, c_] |= (S[:, 4] >= Z0 - 0.1) & U[r_, c_]
        has = np.zeros(nl + 1, bool); has[np.unique(lb[data])] = True; has[0] = True
        Sea |= U & ~has[lb]; U = C & ~Sea
    edge = Sea & ndimage.binary_dilation(U, structure=np.ones((3, 3), bool))
    # the contour the basin starts from: the low line's level where it runs along the low line, the base elsewhere; carried out from the nearest of it and smoothed (Zf), exact on the contour (Ze)
    Ze = np.full(shape, Z0, np.float32); Zf = Ze
    if edge.any():
        if Ll is not None:
            dl_ = ndimage.distance_transform_edt(~np.isfinite(Ll), sampling=res); kl_ = edge & (dl_ <= float(P.get("shore_mapped_gap_m", 30.0))); Ze[kl_] = Lnear[kl_]; del dl_
        _, (er_, ec_) = ndimage.distance_transform_edt(~edge, return_indices=True)
        Zf = ndimage.gaussian_filter(Ze[er_, ec_], max(16.0 / res, 1.0)).astype(np.float32); Zf[edge] = Ze[edge]; del er_, ec_
    # DEEP CASTS ALONG THE SHORE ARE ONE LINE (shore_chain, a trial, 2026-11-03, Hig, Taylor: -47, -47, -48 m casts 22-36 m off the contour, each tucked into its own pocket between
    # them, where the correction fades): neighbours near the contour, too deep for the shore face at their distance, of like depth and about parallel to the shore, get sites between them
    if len(S) and edge.any() and (P.get("shore_chain") or os.environ.get("SHORE_CHAIN")):
        from scipy.spatial import Delaunay
        dd_ = ndimage.distance_transform_edt(~edge, sampling=res); sub = np.flatnonzero((S[:, 4] < Z0 - 1.0) & Sea[S[:, 0].astype(int), S[:, 1].astype(int)])
        chain, n_ch = [], 0
        if sub.size >= 5:
            X_ = S[sub, 2:4]; Z_ = S[sub, 4]; dc_ = dd_[S[sub, 0].astype(int), S[sub, 1].astype(int)]; near_ = float(P.get("shore_chain_near_m", 80.0)); ip_, nb_ = Delaunay(X_).vertex_neighbor_vertices
            step_ = float(P.get("shore_line_site_m", 30.0))
            for i in range(sub.size):
                for j in nb_[ip_[i]:ip_[i + 1]]:
                    if j <= i:
                        continue
                    L_ = float(np.hypot(*(X_[i] - X_[j])))
                    if not (20.0 <= L_ <= float(P.get("shore_chain_max_m", 150.0)) and dc_[i] <= near_ and dc_[j] <= near_ and abs(Z_[i] - Z_[j]) <= 0.2 * L_ and abs(dc_[i] - dc_[j]) <= 0.5 * L_):
                        continue
                    if min(Z_[i], Z_[j]) >= Z0 - 10.0 or (Z0 - Z_[i]) / max(dc_[i], 1.0) <= sf_ or (Z0 - Z_[j]) / max(dc_[j], 1.0) <= sf_:
                        continue
                    w = np.linspace(0.0, 1.0, max(int(L_ / step_), 2) + 1)[1:-1]; xs, ys = X_[i, 0] + w * (X_[j, 0] - X_[i, 0]), X_[i, 1] + w * (X_[j, 1] - X_[i, 1])
                    cc, rr = ~T * (xs, ys); rr, cc = rr.astype(int), cc.astype(int)
                    if not Sea[rr, cc].all():
                        continue
                    chain.append(np.c_[rr, cc, xs, ys, Z_[i] + w * (Z_[j] - Z_[i]), np.full(w.size, max(S[sub[i], 5], S[sub[j], 5]))]); n_ch += 1
                    line_feats.append(dict(type="Feature", geometry=dict(type="LineString", coordinates=[X_[i].tolist(), X_[j].tolist()]), properties=dict(kind="shore_chain", z_a=float(Z_[i]), z_b=float(Z_[j]), length_m=round(L_, 1))))
        if chain:
            Bl = np.vstack([Bl] + chain)
            log(f"  shoreface: {n_ch} shore chains between deep casts near the contour, {sum(len(c_) for c_ in chain)} sites")
    log(f"  shoreface: base contour at {Z0:g} m: intertidal {U.sum() * res * res / 1e6:.3f} km2, sea below it {Sea.sum() * res * res / 1e6:.3f} km2, {edge.sum() * res / 1e3:.1f} km of contour; "
        f"{len(S)} casts, {int((S[:, 4] > Z0).sum()) if len(S) else 0} above the base")
    # every line the casts trace, as cells: the weight and the blur follow the line, not its sites, or each site is a bead (the chain trial, 2026-11-03)
    line_m = np.zeros(shape, bool)
    for f_ in line_feats:
        (x0_, y0_), (x1_, y1_) = f_["geometry"]["coordinates"]; t_ = np.linspace(0.0, 1.0, max(int(np.hypot(x1_ - x0_, y1_ - y0_) / res), 1) + 1)
        c_l, r_l = ~T * (x0_ + t_ * (x1_ - x0_), y0_ + t_ * (y1_ - y0_)); line_m[r_l.astype(int), c_l.astype(int)] = True
    # a line below the base is a bathymetric contour: sites in the basin's fit, tight
    dn_ = np.isfinite(lines) & (lines < Z0 - 0.1) & Sea
    if dn_.any():
        rd_, cd_ = np.nonzero(dn_); rd_, cd_ = rd_[::max(int(15.0 / res), 1)], cd_[::max(int(15.0 / res), 1)]
        Bl = np.vstack([Bl, np.c_[rd_, cd_, T.c + (cd_ + 0.5) * res, T.f - (rd_ + 0.5) * res, lines[rd_, cd_], np.full(rd_.size, float(P.get("shore_line_sigma_m", 0.5)))]]); line_m |= dn_
    fill = np.full(shape, np.nan, np.float32)
    # 2 THE INTERTIDAL
    fv = np.full(shape, np.nan, np.float64); fv[ob & ndimage.binary_dilation(C, structure=np.ones((3, 3), bool))] = z[ob & ndimage.binary_dilation(C, structure=np.ones((3, 3), bool))]
    foot = U & (ext > top) & ~m_band; fv[foot] = ext[foot]        # off a cliff: the rim's slope down to the top of the tide (between mapped lines there is no such thing: it stood 8-12 m high under the water's edge, 59.29380,-151.04345)
    if dem is not None:
        k = U & np.isfinite(dem) & (dem >= dfix) & ~m_band; fv[k] = dem[k]        # the DEM's last cells tape to its flattened water at 0: its shoreline's interpolation, not a height (they still hold the contour off, above)
    k = (U | np.isfinite(fv)) & np.isfinite(lines); fv[k] = lines[k]       # a line over an observed rim cell (the DEM clip's false shore) holds the band there too
    if Hnear is not None:             # what stands beside the band (a cliff where the line itself lies on an observed cell, or the DEM's false shore in the water) holds it at the line's level at most: a higher rim raised the band over the line, a shelf once capped
        k = np.isfinite(fv) & ndimage.binary_dilation(m_band, structure=np.ones((3, 3), bool)) & ~np.isfinite(lines) & ~edge; fv[k] = np.minimum(fv[k], Hnear[k])
    fv[edge] = Ze[edge]
    fixed = np.isfinite(fv)
    if U.any():
        sol = harmonic(fv, fixed, U & ~fixed, coarse=P["coarse"], iters=P["cg_iters"])
        fill[U] = np.where(fixed[U], fv[U], sol[U]).astype(np.float32)
        fill[U] = np.where(np.isfinite(fill[U]), fill[U], Z0)
    # 3 THE BASIN
    if Sea.any():
        bo = ob & (z <= Zf)
        rb, cb = np.nonzero(bo[::4, ::4]); rb *= 4; cb *= 4
        rr = rb; cc = cb; zz = z[rb, cb].astype(np.float64); wt = np.full(rb.size, 0.25)
        if len(S):
            k = S[:, 4] < Zf[r_, c_]
            rr = np.r_[rr, S[k, 0].astype(int)]; cc = np.r_[cc, S[k, 1].astype(int)]; zz = np.r_[zz, S[k, 4]]; wt = np.r_[wt, np.minimum(1.0, (2.0 / S[k, 5]) ** 2)]
        de = ndimage.distance_transform_edt(~edge, sampling=res) if edge.any() else np.full(shape, 1e4)
        # THE ROLLOVER: the slope starts at the intertidal's beside the contour and steepens over shore_rollover_m to the shore's own (the rim's, at least
        # shore_rollover_deg) -- convex by construction, in closed form -- then settles softly onto the casts' surface.  Both slope fields are smooth
        # across the coast (a nearest-shore lookup scored radial creases), and the descent never passes the casts' level (it ran the prior 100 m deep).
        s0 = np.tan(np.radians(float(P.get("shore_slope_min_deg", 0.5)))); s_max = np.tan(np.radians(35.0))
        sgs = max(float(P.get("shore_slope_smooth_m", 80.0)) / res, 1.0)
        # each slope is carried out from its NEAREST shore and then smoothed: a kernel with a fallback beyond its reach jumped 0.01 -> 0.52 at that reach, an arc across the bay
        if U.any() and edge.any():
            gy, gx = np.gradient(np.where(U | edge, np.nan_to_num(fill, nan=Z0), Zf), res); g = np.clip(np.hypot(gx, gy), 0, s_max); ue = (U & ~fixed) | edge
            s_loc = np.clip(_smooth_field(ue.astype(np.float32), np.where(ue, g, 0).astype(np.float32), max(30.0 / res, 1.0)), s0, s_max)
            _, (er, ec) = ndimage.distance_transform_edt(~edge, return_indices=True)
            s_in = np.clip(ndimage.gaussian_filter(s_loc[er, ec].astype(np.float32), sgs), s0, s_max)
        else:
            s_in = np.full(shape, s0, np.float32)
        s_rim = ndimage.gaussian_filter(np.tan(np.radians(srim[ri, ci])).astype(np.float32), sgs)
        s5 = np.tan(np.radians(float(P.get("shore_rollover_deg", 5.0))))
        if zz.size:
            b = _basin_prior(rr, cc, zz, wt, shape, 4)
            B = np.repeat(np.repeat(b, 4, axis=0), 4, axis=1)[:shape[0], :shape[1]]
            B = ndimage.gaussian_filter(np.minimum(B, Zf), 4)
        # THE LAND'S SLOPE DOES NOT CARRY UNDER WATER ON ITS OWN SAY-SO (2026-11-03, Hig: 59.23944,-151.36836: a 30 degree hillside put the rollover at 30 degrees, so it
        # reached the casts' surface 10 m off the contour and stood as a wall 150 m from the nearest cast).  The slope below the contour is what the casts ask for:
        # their depth over their distance at this stretch of shore, at least shore_rollover_deg, at most the land's.
        s_t = np.full(shape, s5, np.float32)
        if zz.size and edge.any():
            dc_ = np.zeros(shape, bool); dc_[rr, cc] = True
            dc_ = ndimage.distance_transform_edt(~dc_, sampling=res)
            _, (er2, ec2) = ndimage.distance_transform_edt(~edge, return_indices=True)
            Le = ndimage.gaussian_filter(dc_[er2, ec2].astype(np.float32), sgs); DDe = ndimage.gaussian_filter(np.maximum(Zf - B, 0.5)[er2, ec2].astype(np.float32), sgs)
            s_t = np.maximum(s5, np.minimum(float(P.get("shore_cast_reach", 2.0)) * DDe / np.maximum(Le, 10.0), s_rim)); del dc_, er2, ec2, Le, DDe
        s_t = np.clip(np.maximum(s_t, s_in), s0, s_max)
        x0 = float(P.get("shore_rollover_m", 60.0)); t = de / x0
        Q = np.where(t < 1, t ** 3 - t ** 4 / 2, t - 0.5)                  # the integral of a smoothstep
        near = Zf - (s_in * de + (s_t - s_in) * x0 * Q)
        if zz.size:
            # the descent saturates at the casts' level: an exponential settle, with no kink where the rollover meets the casts' surface
            # (a soft maximum left thin coast-parallel arcs along that line)
            DD = np.maximum(Zf - B, 0.5); q = Zf - near
            pr = Zf - DD * (1.0 - np.exp(-q / DD))
        else:
            pr = near
        pr = np.minimum(Zf, _smooth_field(Sea.astype(np.float32), np.where(Sea, pr, 0).astype(np.float32), max(float(P.get("shore_prior_smooth_m", 16.0)) / res, 1.0)))
        base = np.full(shape, np.nan, np.float32); base[Sea] = pr[Sea].astype(np.float32)
        # the rim's own misfit fades into the sea (a measured bed beside it is met, not stepped)
        rimS = ob & ndimage.binary_dilation(Sea, structure=np.ones((3, 3), bool))
        rv = np.full(shape, np.nan, np.float64)
        if rimS.any():
            _, (pi, pj) = ndimage.distance_transform_edt(~Sea, return_indices=True)
            rv[rimS] = (z - pr[pi, pj])[rimS]
        rv[edge] = (Ze - pr)[edge]          # the prior's smoothing leaves it under the contour there; holding the contour as a boundary of 0 left a ridge along it
        rf = np.isfinite(rv) & (~Sea | edge)
        unk = Sea & ~edge
        if rf.any() and unk.any():
            sol = harmonic(rv, rf, unk, coarse=P["coarse"], iters=P["cg_iters"])
            base[unk] = (base[unk] + np.nan_to_num(sol[unk])).astype(np.float32)
        fill[Sea] = base[Sea]
        if os.environ.get("SHORE_DEBUG"):
            np.savez_compressed(os.environ["SHORE_DEBUG"], B=(B if zz.size else pr).astype(np.float32), pr=pr.astype(np.float32), base=base, near=near.astype(np.float32), de=de.astype(np.float32), s_in=s_in.astype(np.float32), s_t=s_t.astype(np.float32))
    # the casts, per domain and per side: the band and the basin never share a smoothing (tide1: one lambda re-tuned the whole sea)
    if len(S):
        Pz = dict(P); Pz["points_sigma_m"] = float(P["points_sigma_m"]); Pz["points_chi"] = float(P.get("shore_cast_chi", 0.2))
        # the spline's correction is smoothing at its boundary sites, so it does not vanish there: faded over shore_cast_fade_m below the contour, or the contour stands as a step of that size
        fd_ = np.clip((ndimage.distance_transform_edt(~edge, sampling=res) if edge.any() else np.full(shape, 1e4)) / float(P.get("shore_cast_fade_m", 40.0)), 0, 1); fd_ = fd_ * fd_ * (3 - 2 * fd_)
        # ...but a good cast near the contour is met all the same (2026-11-03, Hig's review, Taylor: -47 m casts 26 m off the contour under a surface at -21): the weight reaches 1 at the
        # cast and still 0 on the contour, and the steepness that takes is what the cast asks for
        kg_ = (S[:, 5] <= float(P.get("shore_face_cast_sigma_m", 4.0))) & (S[:, 4] < Zf[r_, c_] - 0.1) & ~U[r_, c_]
        if kg_.any() and edge.any():
            mg_ = np.zeros(shape, bool); mg_[r_[kg_], c_[kg_]] = True; mg_ |= line_m & Sea
            de_ = ndimage.distance_transform_edt(~edge, sampling=res); dc_ = ndimage.distance_transform_edt(~mg_, sampling=res)
            fd_ = np.maximum(fd_, de_ / np.maximum(de_ + dc_, 1e-6)); del de_, dc_
        full = np.where(np.isfinite(fill), fill, np.nan).astype(np.float32)
        sl = lab[r_, c_]; onU = U[r_, c_]
        for cellsS, keep, _, name in ((Sea & ~edge, ~onU & (S[:, 4] < Zf[r_, c_] - 0.1), None, "basin"), (U & ~fixed, onU & (S[:, 4] >= Z0 - 0.1), None, "intertidal")):
            for i in np.unique(sl[keep]):
                Sd = S[keep & (sl == i)]
                if len(Sd) < 3:
                    continue
                if name == "basin" and len(Bl):                 # the lines the casts trace: a spline through their sites follows the trough, where the casts alone give a string of pits
                    Sd = np.vstack([Sd, Bl[Sea[Bl[:, 0].astype(int), Bl[:, 1].astype(int)] & (lab[Bl[:, 0].astype(int), Bl[:, 1].astype(int)] == i) & ~edge[Bl[:, 0].astype(int), Bl[:, 1].astype(int)]]])
                r = _spline_residual(Sd, full, cellsS & (lab == i), dom, ob | (U if name == "basin" else fixed) | edge, res, Pz, T=T, log=log, name=f"{int(i)} {name}")
                if r is not None:
                    m = np.isfinite(r); fill[m] = (full[m] + (r[m] * fd_[m] if name == "basin" else r[m])).astype(np.float32)
    # the ceiling on the basin: what the spline overshot is refitted under sites just below the contour (a cut alone leaves a shelf), then held
    if Sea.any():
        if len(S):
            # ...only where the correction can act: inside the fade a site does nothing but compete with the casts (c4: holding the contour lifted the ring next to it into "over",
            # 50 -> 1126 sites at Rocky and 497 -> 2341 at Windy, whose refit then averaged its casts into 40 m blocks, cast NMAD 0.26 -> 0.69); the cut below holds that ring
            over = Sea & ~edge & (fill > Zf - 0.1) & (fd_ >= 0.5)
            if over.any():
                k_ = max(int(round(float(P["points_rim_step_m"]) / res)), 1) * 2
                or_, oc_ = np.nonzero(over); _, first = np.unique((or_ // k_) * 1_000_003 + oc_ // k_, return_index=True); or_, oc_ = or_[first], oc_[first]
                add = np.column_stack([or_, oc_, T.c + (oc_ + 0.5) * res, T.f - (or_ + 0.5) * res, Zf[or_, oc_] - 0.5, np.full(or_.size, float(P["points_rim_sigma_m"]))])
                log(f"  shoreface: basin above the contour on {over.sum() * res * res / 1e4:.2f} ha (up to {float((fill - Zf)[over].max()):.2f} m): {or_.size} ceiling sites, refit")
                keep = ~U[r_, c_] & (S[:, 4] < Zf[r_, c_] - 0.1); sl_ = lab[r_, c_]; dl_ = lab[or_, oc_]
                for i in np.unique(dl_):
                    Sd = np.vstack([S[keep & (sl_ == i)], add[dl_ == i], Bl[Sea[Bl[:, 0].astype(int), Bl[:, 1].astype(int)] & (lab[Bl[:, 0].astype(int), Bl[:, 1].astype(int)] == i) & ~edge[Bl[:, 0].astype(int), Bl[:, 1].astype(int)]]])
                    r = _spline_residual(Sd, full, Sea & ~edge & (lab == i), dom, ob | U | edge, res, Pz, T=T, log=log, name=f"{int(i)} basin +ceiling")
                    if r is not None:
                        m = np.isfinite(r); fill[m] = (full[m] + r[m] * fd_[m]).astype(np.float32)
        ex = np.where(Sea, np.maximum(fill - (Zf - 0.1), 0), 0).astype(np.float32)
        if (ex > 0).any():
            log(f"  shoreface: still above after the refit on {(ex > 0).sum() * res * res / 1e4:.2f} ha, up to {float(ex.max()):.2f} m: held")
            fill[Sea] = (fill[Sea] - ex[Sea]).astype(np.float32)
    if U.any():
        lo = U & ~fixed & (fill < Z0)
        fill[lo] = Z0
    fill[edge] = Ze[edge]
    # NO DETAIL WHERE THERE IS NO DATA (2026-11-03, Hig: "there's no reason to infer steep cliffs while interpolating between real data"; topobathy/step_check.py measures it): the
    # modelled surface is blurred with a kernel growing with the distance to the nearest datum, so what is measured stands, and every kink the construction left in a gap
    # (the contour, the rollover, the ends of the correction) goes.  The land is clipped at the top of the tide for it: a 40 m hillside does not bleed into the shore.
    gs_ = float(P.get("shore_gap_sigma_max_m", 40.0)); gk_ = float(P.get("shore_gap_k", 0.5))
    if gs_ > 0:
        cs_ = U | Sea
        da_ = ob | (hi if hi_z is not None else np.zeros(shape, bool)) | np.isfinite(lines)
        if dem is not None:
            da_ |= np.isfinite(dem) & (dem >= dfix) & ~m_band
        if len(S):
            da_[r_, c_] = True
        da_ |= line_m & Sea         # or the blur takes the line back out between its casts
        dg_ = ndimage.distance_transform_edt(~da_, sampling=res) if da_.any() else np.full(shape, 1e4)
        src = np.where(cs_, fill, np.minimum(z, top)); hv = np.isfinite(src); src = np.nan_to_num(src, nan=0.0).astype(np.float32)
        tg = np.clip(gk_ * dg_, 0, gs_); out_ = fill.copy().astype(np.float64); prev = fill.astype(np.float64); s_prev = 0.0
        sig_ = 4.0
        while s_prev < gs_:
            sig_ = min(sig_, gs_) if sig_ < gs_ * 1.5 else gs_
            cur = _gsmooth(src * hv, sig_ / res) / np.maximum(_gsmooth(hv.astype(np.float32), sig_ / res), 1e-6)
            kk = cs_ & (tg > s_prev) & (tg <= sig_)
            w_ = (tg[kk] - s_prev) / (sig_ - s_prev)
            out_[kk] = (1 - w_) * prev[kk] + w_ * cur[kk]
            prev = cur; s_prev = sig_; sig_ *= 2
        moved = np.abs(out_ - fill)[cs_]
        log(f"  shoreface: gap smoothing (sigma = {gk_:g} x distance to data, up to {gs_:g} m): moved p50 {np.nanmedian(moved):.2f} m, p99 {np.nanpercentile(moved, 99):.2f} m")
        fill[cs_] = out_[cs_].astype(np.float32)
    # the misfit at the casts: the review's first number
    if len(S):
        res_ = fill[r_, c_] - S[:, 4]; ok = np.isfinite(res_)
        log(f"  shoreface: surface - cast at {int(ok.sum())} casts: median {np.median(res_[ok]):+.2f} m, NMAD {1.4826 * np.median(np.abs(res_[ok] - np.median(res_[ok]))):.2f}, "
            f"p90 |r| {np.percentile(np.abs(res_[ok]), 90):.2f}; / sigma p90 {np.percentile(np.abs(res_[ok] / S[ok, 5]), 90):.2f}")
    mask = np.zeros(shape, np.uint8); mask[U] = 9; mask[Sea] = 2; mask[hi] = 8
    if hi_z is not None:
        fill[hi] = hi_z[hi]
    rock_review = _rock_review(rock_rows, P, C0, z, fill, log)
    if len(S):
        ds = ndimage.distance_transform_edt(~_cells_of(S, shape), sampling=res); mask[Sea & (ds <= 150.0)] = 3
        sig = np.where(ds <= 500, 0.5 + ds / 100.0, 5.5)
    else:
        sig = np.full(shape, 5.5)
    # THE EDIT CHECK: does the surface now do what each line says?  A line that could not be honoured is reported, never dropped in silence
    chk = {}
    for fi, lv, rl, cl in line_items:
        e = chk.setdefault(fi, dict(feature=fi, level_m=lv, cells=0, not_modelled=0, off=[])); e["cells"] += rl.size
        inm = C[rl, cl]; e["not_modelled"] += int((~inm).sum()); e["off"].extend((fill[rl[inm], cl[inm]] - lv).tolist())
    tol = float(P.get("shore_line_tol_m", 0.25)); edit_check = []
    for e in chk.values():
        o = np.abs(np.array(e.pop("off"), float)); o = o[np.isfinite(o)]
        e.update(off_median_m=round(float(np.median(o)), 2) if o.size else None, off_p90_m=round(float(np.percentile(o, 90)), 2) if o.size else None, ok=bool(o.size and np.percentile(o, 90) <= (tol if e["level_m"] >= Z0 - 0.1 else 2.0 * float(P.get("shore_line_sigma_m", 0.5))) and e["not_modelled"] <= 0.1 * e["cells"]))
        edit_check.append(e)
        log(f"  shoreface: edit check, line {e['feature']} at {e['level_m']:g} m: {e['cells']} cells, {e['not_modelled']} on land or an observed surface (not moved), off by median {e['off_median_m']}, p90 {e['off_p90_m']} m: {'ok' if e['ok'] else 'NOT HONOURED'}")
    return dict(edit_check=edit_check, cells=C0, fill=fill, mask=mask, sigma=np.where(C0, sig, np.nan).astype(np.float32),
                contours=_contours(z, fill, C0, Z0, res, T, P, P.get("_crs")), cast_review=_cast_review(P, T, shape, S, C0, C, hi, z, fill, Z0, P.get("_crs"), U, lines, res, Zf),
                cast_lines=dict(type="FeatureCollection", crs=dict(type="name", properties=dict(name=str(P.get("_crs")))), features=line_feats), rock_review=rock_review)


def _mapped_shores(P, T, shape):
    """From the tide polygons beside tide_sea (tide_vectors.py): the low line's level on its cells, high water, low water, the high line's level on its cells (the high line wins a shared cell in high_line.py); all None without a low line here."""
    import shapely
    from pathlib import Path
    from rasterio import features
    from rasterio.transform import array_bounds
    from shapely.geometry import box, mapping, shape as shape_
    if not P.get("tide_sea") or not P.get("tide_lines"):
        return None, None, None, None
    h_, l_ = Path(P["tide_sea"]).with_name("tide_high_water.geojson"), Path(P["tide_sea"]).with_name("tide_low_water.geojson")
    if not h_.exists() or not l_.exists():
        return None, None, None, None
    b_ = box(*array_bounds(shape[0], shape[1], T))
    F_ = [f for f in json.load(open(P["tide_lines"]))["features"] if shape_(f["geometry"]).intersects(b_)]
    L_ = [(f["geometry"], float(f["properties"]["level_m"])) for f in F_ if (f.get("properties") or {}).get("source") == "vector_low"]
    H_ = [(f["geometry"], float(f["properties"]["level_m"])) for f in F_ if (f.get("properties") or {}).get("source") == "vector_high"]
    if not L_:
        return None, None, None, None
    Ll = features.rasterize(L_, out_shape=shape, transform=T, fill=np.nan, dtype="float32")
    Hl = features.rasterize(H_, out_shape=shape, transform=T, fill=np.nan, dtype="float32") if H_ else None
    def poly(p):
        g = shapely.clip_by_rect(shape_(json.load(open(p))["features"][0]["geometry"]), *b_.bounds)
        return features.rasterize([(mapping(g), 1)], out_shape=shape, transform=T, fill=0, dtype="uint8") > 0 if not g.is_empty else np.zeros(shape, bool)
    return Ll, poly(h_), poly(l_), Hl


def _rock_soundings(P, T, res, C, S, Bl, line_feats, Ll, Lnear, log):
    """A rock is a sounding (2026-11-04, Hig: patched in after the surface it stands as an isolated sea-mount; as a sounding at 59.29450,-151.04686 "it would indicate there's a shoal extending out from land").  Each active rock in the void is a row of S at its
    floor (no higher than just under the low shore's level: the basin's ceiling), and, where the low shore is mapped, the shoal: sites from the rock to the low line or to a rock nearer it, in a line the fit follows as it does the casts' lines, unless a
    cast within rock_shoal_clear_m of that line is much deeper than the line (then the rock stands alone).  Returns S, Bl, line_feats and the rows for review; the despike never sees a rock."""
    from scipy.spatial import cKDTree
    path = P.get("rocks")
    if not path or not os.path.exists(path):
        return S, Bl, line_feats, []
    sg = float(P.get("rock_sigma_m", 1.0)); ss = float(P.get("rock_shoal_sigma_m", 2.0)); mx = float(P.get("rock_shoal_max_m", 300.0)); clr = float(P.get("rock_shoal_clear_m", 30.0)); step = float(P.get("shore_line_site_m", 30.0))
    rows = []; shape = C.shape
    with open(path) as fh:
        for f in json.load(fh)["features"]:
            q = f["properties"]
            if q.get("active"):
                x, y = f["geometry"]["coordinates"][:2]; cc, rr = ~T * (x, y); rr, cc = int(rr), int(cc)
                if 0 <= rr < shape[0] and 0 <= cc < shape[1]:
                    fl = float(q["floor_m"]); rows.append(dict(key=q["key"], type=q["type"], floor=fl, x=x, y=y, r=rr, c=cc, z=fl if Lnear is None else min(fl, float(Lnear[rr, cc]) - 0.2), inC=bool(C[rr, cc]), shoal=None))
    I = [q for q in rows if q["inC"]]
    if I:
        S = np.vstack([S, [[q["r"], q["c"], q["x"], q["y"], q["z"], sg] for q in I]])
    if I and Ll is not None and (lk_ := np.isfinite(Ll)).any():
        d_, (li, lj) = ndimage.distance_transform_edt(~lk_, sampling=res, return_indices=True); sites = []; done = []
        casts = S[:-len(I)]; tree = cKDTree(casts[:, 2:4]) if len(casts) else None
        for q in sorted(I, key=lambda q: d_[q["r"], q["c"]]):
            tx, ty = T.c + (lj[q["r"], q["c"]] + 0.5) * res, T.f - (li[q["r"], q["c"]] + 0.5) * res; tz = float(Ll[li[q["r"], q["c"]], lj[q["r"], q["c"]]]); L = float(d_[q["r"], q["c"]]); to = "low line"
            for o in done:
                d2 = float(np.hypot(o["x"] - q["x"], o["y"] - q["y"]))
                if d2 < L:
                    tx, ty, tz, L, to = o["x"], o["y"], o["z"], d2, "rock"
            done.append(q)
            if not 0 < L <= mx:
                q["shoal"] = "too far"; continue
            w = np.linspace(0.0, 1.0, max(int(L / step), 1) + 1)[1:-1]; xs, ys = q["x"] + w * (tx - q["x"]), q["y"] + w * (ty - q["y"]); zs = q["z"] + w * (tz - q["z"]); cc, rr = ~T * (xs, ys); rr, cc = rr.astype(int), cc.astype(int)
            hit = False
            if tree is not None:
                ww = np.linspace(0.0, 1.0, max(int(L / 10.0), 1) + 1); xw, yw = q["x"] + ww * (tx - q["x"]), q["y"] + ww * (ty - q["y"]); zw = q["z"] + ww * (tz - q["z"])
                for x_, y_, z_ in zip(xw, yw, zw):
                    nb = tree.query_ball_point((x_, y_), clr)
                    if nb and (casts[nb, 4] < z_ - float(P.get("rock_shoal_deeper_m", 5.0))).any():
                        hit = True; break
            if hit or (w.size and not C[rr, cc].all()):
                q["shoal"] = "cast deeper beside it" if hit else "across land"; continue
            q["shoal"] = to
            if w.size:
                sites.append(np.c_[rr, cc, xs, ys, zs, np.full(w.size, ss)])
            line_feats.append(dict(type="Feature", geometry=dict(type="LineString", coordinates=[[q["x"], q["y"]], [float(tx), float(ty)]]), properties=dict(kind="rock_shoal", z_a=q["z"], z_b=tz, length_m=round(L, 1))))
        if sites:
            Bl = np.vstack([Bl] + sites)
    log(f"  shoreface: {len(I)} rocks as soundings of {len(rows)} here; shoals to the shore: {sum(q['shoal'] in ('low line', 'rock') for q in I)}, alone: {sum(q['shoal'] not in ('low line', 'rock') for q in I)}")
    return S, Bl, line_feats, rows


def _rock_review(rows, P, C0, z, fill, log):
    """What the model did with every active rock.  status: sounding (lift_m, what the surface still falls short of its floor) | below_observed | not_modelled (an observed surface is never overruled)."""
    if not rows and not (P.get("rocks") and os.path.exists(P["rocks"])):
        return None
    F = []; n = {}
    for q in rows:
        here = float(fill[q["r"], q["c"]] if C0[q["r"], q["c"]] else z[q["r"], q["c"]]); st = "sounding" if q["inC"] else ("below_observed" if np.isfinite(here) else "not_modelled"); n[st] = n.get(st, 0) + 1
        F.append(dict(type="Feature", geometry=dict(type="Point", coordinates=[q["x"], q["y"]]), properties=dict(key=q["key"], rock_type=q["type"], floor_m=q["floor"], status=st, surface=None if not np.isfinite(here) else round(here, 2), lift_m=round(max(q["z"] - here, 0.0), 2) if np.isfinite(here) else 0.0, shoal=q["shoal"])))
    log(f"  shoreface: rocks: {n}")
    return dict(type="FeatureCollection", crs=dict(type="name", properties=dict(name=str(P.get("_crs")))), features=F)


def _cells_of(S, shape):
    m = np.zeros(shape, bool); m[S[:, 0].astype(int), S[:, 1].astype(int)] = True
    return m


def _contours(z, fill, C, Z0, res, T, P, crs):
    """The product's tide-level contours where it is modelled: the file to edit."""
    import contourpy
    k = 4; a = np.where(C, fill, z).astype(np.float64); a = np.where(np.isfinite(a), a, 1e3)
    a = ndimage.uniform_filter(a, k)[::k, ::k]; near = ndimage.maximum_filter(C.astype(np.uint8), 2 * k + 1)[::k, ::k] > 0
    gen = contourpy.contour_generator(z=np.where(near, a, np.nan))
    feats = []
    for label, lv in LEVELS:
        level = Z0 if lv is None else (float(P[lv]) if isinstance(lv, str) else float(lv))
        for ln in gen.lines(level):
            if len(ln) < 8:
                continue
            xy = np.column_stack([T.c + (ln[:, 0] * k + k / 2) * res, T.f - (ln[:, 1] * k + k / 2) * res])
            feats.append(dict(type="Feature", properties=dict(level_m=round(level, 2), label=label, source="model"), geometry=dict(type="LineString", coordinates=np.round(xy, 1).tolist())))
    return dict(type="FeatureCollection", crs=dict(type="name", properties=dict(name=str(crs))), features=feats)
