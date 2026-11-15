#!/usr/bin/env python3
"""Kenai 2008: the sea a flight line saw, classed ground -> water, line by line (2026-11-14, Hig: "do a dedicated reclassification of
these points, without reprocessing the whole point cloud"; $W/review_1114/REVIEW.md sections 8 and 10).

Why a flight line: an NIR lidar sees nothing under water, so at the moment a line was flown NO ground return of that line can lie at or
below the sea it flew over.  The Anchor Point shelf (59.73148,-151.84045) is 4,394 "ground" points of line 2030 at +1.64 m (sd 0.48,
intensity 4, all single returns) beside line 2512's beach at +3.7..4.1 m: the sea at a mid tide.

    kenai_line_water.py scan  [tile ...]     per tile and line: the sea level it saw (if it saw the sea) -> <work>/scan/<tile>.json
    kenai_line_water.py apply [tile ...]     write <out>/<tile>.laz with those ground points as class 9; every other byte as it was

Only the 2 km tiles that meet Hig's mapped high-tide water (tools/lidar/water/tide_high_water.geojson).  The r1 tiles are never written."""
from __future__ import annotations
import json, os, re, subprocess, sys, time
from pathlib import Path
import numpy as np

SRC = Path("/Volumes/Powder/lidar_build/kenai_2008/classified"); OUT = Path("/Volumes/Powder/lidar_build/kenai_2008/classified_r2")
WORK = Path("/Volumes/Nunatak/lidar_build/kenai_r2_work"); PDAL = "/opt/homebrew/bin/pdal"
HW = Path("/Users/Hig/Claude_projects/landslidescience-topobathy/tools/lidar/water/tide_high_water.geojson")
P = dict(res=2.0, sea_in_m=20.0, min_pts=400, bin_m=0.05, peak_frac=0.05, peak_min=300, level_slope=0.0008, level_cells=40, cell_m=10.0, k_sigma=2.0, cap_m=0.6, sigma_min=0.05, crest_m=1.0, crest_frac=0.5, crest_thin=6)
REC = np.dtype([("X", "<i4"), ("Y", "<i4"), ("Z", "<i4"), ("I", "<u2"), ("flags", "u1"), ("cls", "u1"), ("ang", "i1"), ("user", "u1"), ("psid", "<u2"), ("t", "<f8")])   # LAS 1.2, point format 1


def read_las(path):
    """(header bytes up to the points, the record array (a copy), scales, offsets) of an uncompressed LAS 1.2 format 1 file."""
    b = Path(path).read_bytes()
    off = int(np.frombuffer(b, "<u4", 1, 96)[0]); fmt = b[104]; rl = int(np.frombuffer(b, "<u2", 1, 105)[0]); n = int(np.frombuffer(b, "<u4", 1, 107)[0])
    assert fmt == 1 and rl == REC.itemsize == 28, (fmt, rl)
    sc = np.frombuffer(b, "<f8", 3, 131); of = np.frombuffer(b, "<f8", 3, 155)
    return b[:off], np.frombuffer(b, REC, n, off).copy(), sc, of, b[off + n * rl:]


def decompress(tile):
    WORK.mkdir(parents=True, exist_ok=True); las = WORK / f"{tile}.las"
    subprocess.run([PDAL, "translate", str(SRC / f"{tile}.laz"), str(las), "--writers.las.forward=all", "--writers.las.compression=false"], check=True, capture_output=True)
    return las


def sea_mask(tile, hw):
    """The mapped high-tide water on the tile at res, with how far inside it each cell lies (m)."""
    from rasterio import features, transform
    from scipy import ndimage
    from shapely.geometry import box
    m = re.search(r"t_(\d+)_(\d+)", tile); x0, y0 = int(m.group(1)), int(m.group(2)); pad = 60.0; r = P["res"]
    n = int((2000 + 2 * pad) / r); T = transform.from_origin(x0 - pad, y0 + 2000 + pad, r, r)
    g = hw.intersection(box(x0 - pad, y0 - pad, x0 + 2000 + pad, y0 + 2000 + pad))
    sea = features.rasterize([(g, 1)], out_shape=(n, n), transform=T).astype(bool) if not g.is_empty else np.zeros((n, n), bool)
    return sea, ndimage.distance_transform_edt(sea) * r, (x0 - pad, y0 + 2000 + pad, r, n)


def line_levels(x, y, z, cls, inten, nret, psid, t, sea, din, geo):
    """Per flight line in the tile: did it see the sea, and at what level.  Returns {psid: {...}}."""
    gx, gy, r, n = geo; c = np.clip(((x - gx) / r).astype(int), 0, n - 1); rr = np.clip(((gy - y) / r).astype(int), 0, n - 1)
    insea = sea[rr, c]; deep_in = din[rr, c] >= P["sea_in_m"]; solid = np.isin(cls, (1, 2)); out = {}
    for k in np.unique(psid):
        ln = psid == k; s = ln & solid & deep_in & (z < 10.0); ns = int(s.sum())
        land = ln & (cls == 2) & ~insea & (z > 5.0)
        d = dict(n_line=int(ln.sum()), n_sea=ns, t=[float(t[ln].min()), float(t[ln].max())], land_int=float(np.median(inten[land])) if land.sum() > 200 else None, level=None, why="")
        out[int(k)] = d
        if ns < P["min_pts"]: d["why"] = "too few points over the mapped sea"; continue
        zs = z[s]; lo = float(np.percentile(zs, 0.5)) - 0.5; h, e = np.histogram(zs, bins=np.arange(lo, 10.0 + P["bin_m"], P["bin_m"]))
        from scipy import ndimage
        hs = ndimage.gaussian_filter1d(h.astype(float), 3.0); need = max(P["peak_min"], P["peak_frac"] * ns); L = None
        for i in range(1, len(hs) - 1):                                                    # the LOWEST peak that holds a real share of the points
            if hs[i] >= hs[i - 1] and hs[i] > hs[i + 1] and h[max(0, i - 10):i + 11].sum() >= need:
                L = float(e[i] + P["bin_m"] / 2); break
        if L is None: d["why"] = "no low peak"; continue
        below = zs[(zs < L) & (zs > L - 2.0)]; sg = max(float(1.4826 * np.median(np.abs(below - L))) if below.size > 50 else 0.3, P["sigma_min"])   # the peak's lower half: no ground in it
        sh = s & (np.abs(z - L) <= max(3 * sg, 0.3))
        cm = P["cell_m"]; ci = ((x[sh] - gx) // cm).astype(np.int64) * 100000 + ((gy - y[sh]) // cm).astype(np.int64); u, inv, cnt = np.unique(ci, return_inverse=True, return_counts=True)
        ok = cnt >= 4; zc = np.bincount(inv, z[sh]) / cnt; xc = np.bincount(inv, x[sh]) / cnt; yc = np.bincount(inv, y[sh]) / cnt
        d.update(level=round(L, 2), sigma=round(sg, 3), sheet_pts=int(sh.sum()), sheet_cells=int(ok.sum()), sheet_ground_share=round(float((cls[sh] == 2).mean()), 3),
                 sheet_int=float(np.median(inten[sh])), sheet_single=round(float((nret[sh] == 1).mean()), 3))
        if ok.sum() < P["level_cells"]: d["why"] = "sheet too small to test for level"; d["level"] = None; continue
        A = np.c_[np.ones(ok.sum()), xc[ok] - xc[ok].mean(), yc[ok] - yc[ok].mean()]; co, *_ = np.linalg.lstsq(A, zc[ok], rcond=None); slope = float(np.hypot(co[1], co[2]))
        ext = float(max(np.ptp(xc[ok]), np.ptp(yc[ok]))); d.update(slope=round(slope, 5), extent_m=round(ext), cell_sd=round(float(np.std(zc[ok] - A @ co)), 3))
        if slope > P["level_slope"] and slope * ext > 0.3: d["why"] = "the low surface slopes: ground (a flat), not the sea"; d["level_rejected"] = d["level"]; d["level"] = None; continue
        thr = L + min(P["k_sigma"] * sg, P["cap_m"]); d["threshold"] = round(thr, 2)
        d["ground_to_water"] = int((ln & (cls == 2) & insea & (z <= thr)).sum()); d["ground_in_sea"] = int((ln & (cls == 2) & insea).sum())
    return out


def load_hw():
    from shapely.geometry import shape
    return shape(json.load(open(HW))["features"][0]["geometry"])


def tiles_default():
    return [f"t_{x}_{y}" for x, y, _ in json.load(open("/Volumes/Nunatak/lidar_build/composite/review_1114/kenai_sea_tiles.json"))]


def scan(tiles):
    hw = load_hw(); (WORK / "scan").mkdir(parents=True, exist_ok=True)
    for tile in tiles:
        o = WORK / "scan" / f"{tile}.json"
        if o.exists(): continue
        t0 = time.time(); las = decompress(tile); hdr, a, sc, of, tail = read_las(las); las.unlink()
        x = a["X"] * sc[0] + of[0]; y = a["Y"] * sc[1] + of[1]; z = a["Z"] * sc[2] + of[2]; cls = a["cls"] & 31; nret = (a["flags"] >> 3) & 7
        sea, din, geo = sea_mask(tile, hw); res = line_levels(x, y, z, cls, a["I"], nret, a["psid"], a["t"], sea, din, geo)
        json.dump(dict(tile=tile, n=int(a.size), classes={int(k): int(v) for k, v in zip(*np.unique(cls, return_counts=True))}, lines=res, seconds=round(time.time() - t0, 1)), open(o, "w"), indent=1)
        nl = sum(1 for v in res.values() if v["level"] is not None); print(f"{tile}: {a.size} pts, {len(res)} lines, {nl} saw the sea, {sum(v.get('ground_to_water', 0) for v in res.values())} ground -> water, {time.time() - t0:.0f} s", flush=True)


def levels():
    """Which lines saw the OPEN SEA, and at what level -> <work>/levels.json.

    A line is flown through a tile in seconds, so the sea it saw has ONE level; and the sea is the LOWEST level surface a line can see
    inside the mapped water (ground stands above the water, and an NIR lidar sees nothing under it).  So a line's sea level is the lowest
    level sheet it saw in any tile -- supported by a second tile within tol_m, or by one large, very level sheet.  A higher level sheet of
    the same line is not the sea (a perched slough, a wet flat, a marsh platform: the upper Fox River flats read 3.1-4.9 m on lines whose
    sea stood at 1.3-1.5 m) and is left as it is.  Last, the levels of lines flown within an hour of each other must follow one tide: a
    line standing more than tol above what its neighbours allow (their level + max_rate x the time between) did not see the open sea."""
    import glob, collections
    Q = dict(tol_m=0.2, big_cells=300, big_slope=0.0004, max_rate_m_h=2.0, session_gap_h=3.0, tide_tol_m=0.3)
    rows = []
    for f in sorted(glob.glob(str(WORK / "scan" / "*.json"))):
        d = json.load(open(f))
        for k, v in d["lines"].items():
            if v["level"] is not None: rows.append(dict(v, tile=d["tile"], line=int(k)))
    by = collections.defaultdict(list)
    for r in rows: by[r["line"]].append(r)
    L = {}
    for k, v in by.items():
        v = sorted(v, key=lambda r: r["level"]); m = None
        for i, r in enumerate(v):                                               # the lowest SUPPORTED level
            if any(abs(o["level"] - r["level"]) <= Q["tol_m"] for o in v[i + 1:]) or (r["sheet_cells"] >= Q["big_cells"] and r["slope"] <= Q["big_slope"]):
                m = r; break
        if m is not None: L[k] = dict(level=m["level"], t=float(np.mean([r["t"][0] for r in v]) / 3600.0), tiles=len(v), basis=m["tile"])
    for k, d in L.items():                                                      # the tide: one curve per hour of flying, per sensor series (thousands of the line number share a week)
        nb = [o["level"] + Q["max_rate_m_h"] * abs(o["t"] - d["t"]) for j, o in L.items() if j != k and j // 1000 % 10 in ((k // 1000) % 10,) and abs(o["t"] - d["t"]) <= 1.0]
        nb += [o["level"] + Q["max_rate_m_h"] * abs(o["t"] - d["t"]) for j, o in L.items() if j != k and (j // 100) in (33,) and (k // 100) in (30, 33) and abs(o["t"] - d["t"]) <= 1.0]
        nb += [o["level"] + Q["max_rate_m_h"] * abs(o["t"] - d["t"]) for j, o in L.items() if j != k and (k // 100) in (33,) and (j // 100) in (30, 33) and abs(o["t"] - d["t"]) <= 1.0]
        d["envelope"] = round(min(nb), 2) if nb else None; d["sea"] = bool(d["envelope"] is None or d["level"] <= d["envelope"] + Q["tide_tol_m"])
    out = {}
    for r in rows:
        d = L.get(r["line"])
        if d is None or not d["sea"]: continue
        lev = r["level"] if abs(r["level"] - d["level"]) <= Q["tol_m"] else d["level"]; sg = r["sigma"] if lev == r["level"] else 0.12
        out.setdefault(r["tile"], {})[str(r["line"])] = dict(level=round(lev, 2), threshold=round(lev + min(P["k_sigma"] * sg, P["cap_m"]), 2), own=bool(lev == r["level"]))
    json.dump(dict(params=Q, lines={str(k): v for k, v in sorted(L.items(), key=lambda kv: kv[1]["t"])}, tiles=out), open(WORK / "levels.json", "w"), indent=1)
    for k, d in sorted(L.items(), key=lambda kv: kv[1]["t"]):
        print(f"  {d['t']:8.3f} h  line {k:5d}  sea {d['level']:6.2f}  ({d['tiles']} tiles)  envelope {d['envelope']}  {'' if d['sea'] else 'NOT the open sea: left alone'}")
    print(f"{sum(1 for d in L.values() if d['sea'])} lines saw the open sea; {len(out)} tiles, {sum(len(v) for v in out.values())} tile-lines to reclassify")


def apply(tiles):
    hw = load_hw(); lv = json.load(open(WORK / "levels.json"))["tiles"]; OUT.mkdir(parents=True, exist_ok=True); (WORK / "apply").mkdir(exist_ok=True)
    for tile in tiles:
        rep = WORK / "apply" / f"{tile}.json"
        if rep.exists() or tile not in lv: continue
        assert not (OUT / f"{tile}.laz").exists(), f"{tile}: already in {OUT} (a hard link to r1?) -- remove it first, never write through it"
        t0 = time.time(); las = decompress(tile); hdr, a, sc, of, tail = read_las(las); las.unlink()
        x = a["X"] * sc[0] + of[0]; y = a["Y"] * sc[1] + of[1]; z = a["Z"] * sc[2] + of[2]; cls = a["cls"] & 31
        sea, din, geo = sea_mask(tile, hw); gx, gy, r, n = geo; insea = sea[np.clip(((gy - y) / r).astype(int), 0, n - 1), np.clip(((x - gx) / r).astype(int), 0, n - 1)]
        ch = np.zeros(a.size, bool); per = {}
        from scipy import ndimage
        cs = 4.0; nc = int(n * r / cs) + 1; ci = np.clip(((gy - y) / cs).astype(int), 0, nc - 1) * nc + np.clip(((x - gx) / cs).astype(int), 0, nc - 1)
        for k, d in lv[tile].items():
            ln = (a["psid"] == int(k)) & insea; m = ln & (cls == 2) & (z <= d["threshold"])
            # wave crests: a ground point of the line up to crest_m above its sea, in a 12 m neighbourhood where >= crest_frac of the line's
            # returns lie at the sea, is on the water too.  At the waterline the neighbourhood is mixed, so the beach keeps its points.
            al = ln & np.isin(cls, (1, 2)) & (z <= d["level"] + 3.0)
            na = ndimage.uniform_filter(np.bincount(ci[al], minlength=nc * nc).reshape(nc, nc).astype(float), 3)
            nb = ndimage.uniform_filter(np.bincount(ci[al & (z <= d["threshold"])], minlength=nc * nc).reshape(nc, nc).astype(float), 3)
            frac = np.where(na > 0, nb / np.maximum(na, 1e-9), 0.0).ravel()
            # where the line's returns are too thin to judge at 12 m (under crest_thin points), the 44 m neighbourhood decides; a rock awash
            # is a dense cluster and is judged at 12 m, where it is not mostly sea
            A0 = np.bincount(ci[al], minlength=nc * nc).reshape(nc, nc).astype(float); B0 = np.bincount(ci[al & (z <= d["threshold"])], minlength=nc * nc).reshape(nc, nc).astype(float)
            na2 = ndimage.uniform_filter(A0, 11); frac2 = np.where(na2 > 0, ndimage.uniform_filter(B0, 11) / np.maximum(na2, 1e-9), 0.0).ravel(); thin = (na * 9.0 < P["crest_thin"]).ravel()
            mc = ln & (cls == 2) & (z > d["threshold"]) & (z <= d["level"] + P["crest_m"]) & ((frac[ci] >= P["crest_frac"]) | (thin[ci] & (frac2[ci] >= P["crest_frac"])))
            ch |= m | mc; per[k] = dict(d, points=int(m.sum()), crests=int(mc.sum()), ground_left_within_1m=int((ln & (cls == 2) & ~m & ~mc & (z <= d["level"] + 1.0)).sum()))
        b = a.copy(); b["cls"][ch] = (b["cls"][ch] & 0xE0) | 9                    # class 9 = water; the flag bits stay
        tmp = WORK / f"{tile}_r2.las"; tmp.write_bytes(hdr + b.tobytes() + tail)
        out_tmp = OUT / f".tmp_{tile}.laz"
        subprocess.run([PDAL, "translate", str(tmp), str(out_tmp), "--writers.las.forward=all", "--writers.las.compression=laszip", "-w", "writers.las"], check=True, capture_output=True); tmp.unlink()
        chk = WORK / f"{tile}_chk.las"                                           # read the new tile back: every field but the class must be as r1 has it
        subprocess.run([PDAL, "translate", str(out_tmp), str(chk), "--writers.las.forward=all", "--writers.las.compression=false"], check=True, capture_output=True)
        _, c, sc2, of2, _ = read_las(chk); chk.unlink()
        same = c.size == a.size and np.array_equal(sc, sc2) and np.array_equal(of, of2) and all(np.array_equal(c[f], a[f]) for f in ("X", "Y", "Z", "I", "flags", "ang", "user", "psid", "t"))
        diff = int(((c["cls"] != a["cls"])).sum()); good = same and diff == int(ch.sum()) and np.array_equal(c["cls"], b["cls"])
        if not good:
            out_tmp.unlink(); raise SystemExit(f"{tile}: the rewritten tile does not match r1 outside the class field (same {same}, changed {diff} vs {int(ch.sum())}) -- nothing kept")
        os.replace(out_tmp, OUT / f"{tile}.laz")
        json.dump(dict(tile=tile, points=int(a.size), ground_before=int((cls == 2).sum()), to_water=int(ch.sum()), lines=per, verified=True, seconds=round(time.time() - t0, 1)), open(rep, "w"), indent=1)
        print(f"{tile}: {int(ch.sum())} ground -> water on {len(per)} lines; verified; {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1]; tl = sys.argv[2:] or tiles_default()
    if cmd == "scan": scan(tl)
    elif cmd == "levels": levels()
    elif cmd == "apply": apply(tl)
    else: raise SystemExit("scan | levels | apply")
