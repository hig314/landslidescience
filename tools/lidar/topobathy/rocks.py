#!/usr/bin/env python3
"""Rocks as information about the bed (Hig 2026-11-04): a rock seen at low tide stands at or above MLLW; every other rock is for review until a reviewer says otherwise.

  rocks.py build [--hig SHP] [--enc ENC.geojson]     Hig's rocks + NOAA's chart rocks (ENC UWTROC) + the edits -> water/rocks.geojson, which the recipes read (shoreface._rock_floors)
  rocks.py export OUT.gpkg --surface DEM [DEM ...]   the layer to edit in QGIS: every rock, what the surface does there (later DEMs win)
  rocks.py merge EDITED.gpkg [--basis TEXT]          what changed in that layer -> water/rock_edits.geojson (cumulative), then build
  build also reads water/rocks_hig_review.gpkg (--review): Hig's review against one image (hig_review_source -> water/review_sources.json, that image's water level L) with hig_review_type
  high | mid | low | sub (how far the rock stands out of that water).  A typed rock is active at floor L+1.5 | L+0.7 | L+0.1 | L-1.0; a point moved there moves the rock; a row with no key is a new rock.

active: the rock is a floor in the fill.  Seen rocks start active at MLLW, unless flagged uncertain; Sub, Chart, Data, Deep and the ENC's start inactive.  A `decision` of use makes
any rock active at its floor_m, reject makes it inactive; drag a point to move it.  Observed surfaces are never overruled: a floor applies to modelled cells only.
ENC: VALSOU is a depth under chart datum (MLLW); WATLEV 3 always under water, 4 covers and uncovers, 5 awash.  A rock charted at several scales is kept at the largest."""
import argparse, json, os, subprocess, tempfile, datetime
from pathlib import Path
import numpy as np

WATER = Path(__file__).resolve().parent.parent / "water"
ROCKS = WATER / "rocks.geojson"; EDITS = WATER / "rock_edits.geojson"
HIG = "/Users/Hig/Documents/Outreach/KBSP_Map/New Shapefiles/Rocks/Hig_rocks.shp"
ENC = "/Volumes/Nunatak/lidar_build/obs/rocks/enc_rocks.geojson"
BOXES = Path(__file__).resolve().parent.parent / "composites" / "kbay_joint" / "boxes.json"
OGR = "/opt/homebrew/bin/ogr2ogr"
REVIEW = WATER / "rocks_hig_review.gpkg"; SOURCES = WATER / "review_sources.json"
CLASS_UP = dict(high=1.5, mid=0.7, low=0.1, sub=-1.0); CLASS_ALIAS = dict(loq="low")
MLLW = -1.38; SIX_FT = MLLW - 6 * 0.3048; MOVED_M = 0.5; SAME_ROCK_M = 100.0


def _edits():
    return {f["properties"]["key"]: f for f in json.load(open(EDITS))["features"]} if EDITS.exists() else {}


def _to_grid(lon, lat, src="EPSG:4326"):
    from rasterio.warp import transform
    return transform(src, "EPSG:6334", list(lon), list(lat))


def _enc_rows(path, bounds):
    """One row per charted rock: largest scale first, a smaller scale's rock within SAME_ROCK_M of one already kept is that rock."""
    from scipy.spatial import cKDTree
    F = json.load(open(path))["features"]
    x, y = _to_grid([f["geometry"]["coordinates"][0] for f in F], [f["geometry"]["coordinates"][1] for f in F])
    usage = lambda f: -int(f["properties"]["cell"][2])          # the cell name's digit is its usage band: 5 harbour .. 1 overview
    keep = []
    for i in sorted(range(len(F)), key=lambda i: usage(F[i])):
        if not (bounds[0] <= x[i] <= bounds[2] and bounds[1] <= y[i] <= bounds[3]):
            continue
        keep.append(i)
    out = []; xy = []
    for i in keep:
        if xy and cKDTree(xy).query((x[i], y[i]))[0] <= SAME_ROCK_M:
            continue
        xy.append((x[i], y[i])); q = F[i]["properties"]; v = q.get("VALSOU")
        out.append(dict(x=x[i], y=y[i], key=f'enc:{q["LNAM"]}', source="enc", type="ENC", cell=q["cell"], valsou=v, watlev=q.get("WATLEV"), sordat=q.get("SORDAT"), quasou=",".join(q.get("QUASOU") or []), z_chart=None if v is None else round(MLLW - v, 2)))
    return out


def build(a):
    import fiona
    from scipy.spatial import cKDTree
    B = json.load(open(BOXES)); bounds = (min(b["crop"][0] for b in B), min(b["crop"][1] for b in B), max(b["crop"][2] for b in B), max(b["crop"][3] for b in B))
    with fiona.open(a.hig) as s:
        crs = str(s.crs); H = list(s)
    hx, hy = _to_grid([f["geometry"]["coordinates"][0] for f in H], [f["geometry"]["coordinates"][1] for f in H], crs)
    enc = _enc_rows(a.enc, bounds); tr = cKDTree([(q["x"], q["y"]) for q in enc]); rows = []
    for i, f in enumerate(H):
        q = f["properties"]; ty = q["Type"]; d, j = tr.query((hx[i], hy[i])); m = enc[j] if d <= SAME_ROCK_M else None
        rows.append(dict(x=hx[i], y=hy[i], key=f"hig:{i}", source="hig", type=ty, flag=q["Flag"] or "", note=q["Note"] or "", cell=m and m["cell"], valsou=m and m["valsou"], watlev=m and m["watlev"], z_chart=m and m["z_chart"], enc_m=round(float(d), 1) if m else None,
                         active=ty == "Seen" and not q["Flag"]))
    rows += [dict(q, flag="", note="", enc_m=None, active=False) for q in enc]
    E = _edits(); F = []; n = dict(moved=0, use=0, reject=0)
    for q in rows:
        x0, y0 = round(q.pop("x"), 2), round(q.pop("y"), 2); x, y = x0, y0; q.update(x0=x0, y0=y0, decision="", basis="")
        # the floor: what the rock is known to reach.  A rock that shows at low tide (seen, or charted as covering or awash) reaches MLLW; a charted depth is its own; otherwise the hazard depth, 6 ft under MLLW
        q["floor_m"] = MLLW if q["type"] == "Seen" or q.get("watlev") in (4, 5) else q["z_chart"] if q.get("z_chart") is not None else SIX_FT
        e = E.get(q["key"])
        if e:
            p = e["properties"]; x, y = e["geometry"]["coordinates"][:2]; q.update(decision=p["decision"], basis=p["basis"])
            if p.get("floor_m") is not None:
                q["floor_m"] = p["floor_m"]
            n["moved"] += (x, y) != (x0, y0); n["use"] += p["decision"] == "use"; n["reject"] += p["decision"] == "reject"
            if p["decision"]:
                q["active"] = p["decision"] == "use"
        F.append(dict(type="Feature", geometry=dict(type="Point", coordinates=[x, y]), properties=q))
    n["review"] = _review(F, a.review)
    ROCKS.write_text(json.dumps(dict(type="FeatureCollection", crs=dict(type="name", properties=dict(name="EPSG:6334")), features=F), indent=0))
    c = {}
    for f in F:
        c.setdefault(f["properties"]["type"], [0, 0]); c[f["properties"]["type"]][0] += 1; c[f["properties"]["type"]][1] += f["properties"]["active"]
    print(f"{len(F)} rocks -> {ROCKS}; by type [all, active]: {c}; {sum(q['enc_m'] is not None for q in rows)} of Hig's within {SAME_ROCK_M:g} m of a charted rock; edits: {n}")


def export(a):
    import rasterio
    F = json.load(open(ROCKS))["features"]; xy = [f["geometry"]["coordinates"][:2] for f in F]
    sf = np.full(len(F), np.nan); top = np.full(len(F), np.nan)
    for dem in a.surface:
        with rasterio.open(dem) as d:
            for i, (x, y) in enumerate(xy):
                c, r = ~d.transform * (x, y); c, r = int(c), int(r)
                if not (0 <= r < d.height and 0 <= c < d.width):
                    continue
                n = int(round(a.near_m / d.res[0])); v = d.read(1, window=((max(r - n, 0), r + n + 1), (max(c - n, 0), c + n + 1))).astype(float); v[v == d.nodata] = np.nan
                if np.isfinite(v).any():
                    sf[i] = v[min(r, n), min(c, n)]; top[i] = np.nanmax(v)
    for i, f in enumerate(F):
        q = f["properties"]
        q["surface"] = None if not np.isfinite(sf[i]) else round(float(sf[i]), 2); q["top_near"] = None if not np.isfinite(top[i]) else round(float(top[i]), 2)
        q["short_m"] = None if not np.isfinite(top[i]) else round(float(max(q["floor_m"] - top[i], 0.0)), 2)     # how far the surface within near_m falls short of the rock's floor
    with tempfile.TemporaryDirectory() as td:
        g = f"{td}/r.geojson"; json.dump(dict(type="FeatureCollection", crs=dict(type="name", properties=dict(name="EPSG:6334")), features=F), open(g, "w"))
        if os.path.exists(a.out):
            os.remove(a.out)
        subprocess.run([OGR, "-q", "-f", "GPKG", "-nln", "rock_review", "-a_srs", "EPSG:6334", a.out, g], check=True)
    print(f"{len(F)} rocks -> {a.out}; {int(np.isfinite(sf).sum())} on a surface, {sum((f['properties']['short_m'] or 0) > 0 for f in F)} with the surface short of the floor within {a.near_m:g} m")


def _review(F, path):
    """Hig's review layer over the built rows (in place): moves, active, and the class's floor against its source's water level; new rows for the rocks it adds."""
    import fiona
    if not path or not Path(path).exists():
        return None
    S = json.load(open(SOURCES)); by = {f["properties"]["key"]: f for f in F}; n = dict(moved=0, typed=0, new=0, activated=0, bad=[])
    with fiona.open(path) as s:
        R = list(s)
    for i, r in enumerate(R):
        p = dict(r["properties"]); k = p.get("key"); x, y = r["geometry"]["coordinates"][:2]
        ty = (p.get("hig_review_type") or "").strip().lower(); ty = CLASS_ALIAS.get(ty, ty); src = p.get("hig_review_source")
        if ty and ty not in CLASS_UP:
            n["bad"].append((k, ty)); ty = ""
        fl = None
        if ty:
            if src is None or str(int(src)) not in S:
                n["bad"].append((k, f"source {src}")); ty = ""
            else:
                fl = round(S[str(int(src))]["level_m"] + CLASS_UP[ty], 2)
        if k is None:
            if not ty:
                continue
            k = f"hig_review:{int(src)}:{i}"; f = dict(type="Feature", geometry=dict(type="Point", coordinates=[round(x, 2), round(y, 2)]), properties=dict(key=k, source="hig_review", type="Review", flag="", note=p.get("note") or "", x0=round(x, 2), y0=round(y, 2), decision="", basis="", active=False, floor_m=fl))
            F.append(f); by[k] = f; n["new"] += 1
        f = by.get(k)
        if f is None:
            n["bad"].append((k, "unknown key")); continue
        q = f["properties"]
        if float(np.hypot(x - f["geometry"]["coordinates"][0], y - f["geometry"]["coordinates"][1])) > MOVED_M:
            f["geometry"]["coordinates"] = [round(x, 2), round(y, 2)]; n["moved"] += 1
        if p.get("active") and not q["active"]:
            q["active"] = True; n["activated"] += 1
        if ty:
            q.update(active=True, floor_m=fl, review_type=ty, review_source=int(src), basis=f"hig review source {int(src)}: {ty}"); n["typed"] += 1
    return {k_: (v if not isinstance(v, list) else v[:10]) for k_, v in n.items()}


def merge(a):
    with tempfile.TemporaryDirectory() as td:
        g = f"{td}/e.geojson"; subprocess.run([OGR, "-q", "-f", "GeoJSON", g, a.edited, "rock_review"], check=True); F = json.load(open(g))["features"]
    base = {f["properties"]["key"]: f["properties"] for f in json.load(open(ROCKS))["features"]}
    E = _edits(); today = datetime.date.today().isoformat(); n = dict(kept=0, cancelled=0, bad=[])
    for f in F:
        q = f["properties"]; k = q["key"]; x, y = f["geometry"]["coordinates"][:2]; d = (q.get("decision") or "").strip().lower()
        if d not in ("", "use", "reject"):
            n["bad"].append(k); continue
        moved = float(np.hypot(x - q["x0"], y - q["y0"])) > MOVED_M; fl = q.get("floor_m"); fl_changed = fl is not None and k in base and abs(fl - base[k]["floor_m"]) > 0.005
        if not (moved or d or fl_changed):
            n["cancelled"] += E.pop(k, None) is not None; continue
        E[k] = dict(type="Feature", geometry=dict(type="Point", coordinates=[round(x, 2), round(y, 2)] if moved else [q["x0"], q["y0"]]),
                    properties=dict(key=k, decision=d, floor_m=fl if fl_changed else (E[k]["properties"].get("floor_m") if k in E else None), basis=q.get("basis") or a.basis or "", date=today))
        n["kept"] += 1
    EDITS.write_text(json.dumps(dict(type="FeatureCollection", crs=dict(type="name", properties=dict(name="EPSG:6334")), features=list(E.values())), indent=0))
    print({k: (v if not isinstance(v, list) else v[:10]) for k, v in n.items()}, f"{len(F)} points read, {len(E)} edits on file")
    build(a)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "merge"):
        c = sp.add_parser(name); c.add_argument("--hig", default=HIG); c.add_argument("--enc", default=ENC); c.add_argument("--review", default=str(REVIEW))
        if name == "merge":
            c.add_argument("edited"); c.add_argument("--basis")
    c = sp.add_parser("export"); c.add_argument("out"); c.add_argument("--surface", nargs="+", required=True); c.add_argument("--near-m", type=float, default=50.0)
    a = ap.parse_args(); dict(build=build, export=export, merge=merge)[a.cmd](a)
