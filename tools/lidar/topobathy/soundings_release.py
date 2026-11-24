#!/usr/bin/env python3
"""soundings_release.py -- the merged, corrected soundings as a NUMBERED REVISION of one derived dataset, with its ledger.

    soundings_release.py build REV --label TEXT --why TEXT [--parent r1] [--from DIR] [--edits F] [--shifts F] [--products ID ...]

Hig, 2026-11-23: "we might productively view the merged sounding surveys as a point cloud ... name the merged improved
sounding collection as a new derived dataset with a clear ledger of changes that led to it ... host this in a way that is
analogous to a point cloud ... a data manifest that explains the history ... consistent with what we provide for our
modified lidar point clouds, and consistent with conventions for point cloud manifests" -- and, of STAC and PDAL, STAC.

WHAT A REVISION IS.  The dataset is `kbay_soundings`: every sparse depth measurement the Kachemak Bay composite reads --
the NOS hydrographic surveys' digital soundings (1915-31 lead line, 1977-84 echo sounder), the lake and nearshore
soundings (tools/soundings export) and the hand-placed estimates (flagged kind = estimate).  The 500 m compilation
points are a prior, not measurements, and are left out.  r0 is the sources as their owners serve them (not copied).
r1, r2 ... are ours and are never overwritten, as tools/lidar/points.json lays down for the lidar clouds; a revision is
the whole corrected table (it is small) AND the ledger that made it, so it is both the registry's 'full' and 'recipe'.

WHAT IS WRITTEN, under $LIDAR_BUILD/points/kbay_soundings/<rev>/ (the layout points_publish.py uploads):
  soundings.csv.gz      one row per cast: where it is now, where the source put it, what was decided about it
  soundings.gpkg        the same, for a GIS
  soundings.copc.laz    the same as a point cloud (X Y Z, class 40 bathymetric point / 45 for an estimate, PointSourceId =
                        the survey's number in manifest.json, UserData = the status code, extra dimension Sigma); casts
                        the reviewer rejected, and depth bounds (not depths), are not in it
  changes.json          THE LEDGER: every change from the sources to this revision, each with its basis and date
  manifest.json         lineage, CRS, totals, surveys, files (the lidar revisions' manifest, same keys where they apply)
  stac/collection.json, stac/<rev>.json     the same facts as a STAC Collection and Item (pointcloud, version, processing,
                        projection, scientific and file extensions); the ledger is linked as an asset -- no standard holds it

STATUS of a cast: as_placed | sheet (its whole survey translated) | moved (dragged by the reviewer) | reread (depth read
again from the survey's sheet) | unbound (a depth bound lifted) | rejected (the reviewer's call; kept in the table, out of
the cloud).  x_src, y_src are the source's own position (for NOS: NCEI's NAD83, in EPSG:6334); depth_src its own depth.
"""
import argparse, csv, gzip, hashlib, json, os, subprocess, time, datetime
from pathlib import Path
import numpy as np

OBS = Path("/Volumes/Nunatak/lidar_build/obs/soundings")
BUILD = Path(os.environ.get("LIDAR_BUILD", "/Volumes/Nunatak/lidar_build")) / "points"
PUBLIC = os.environ.get("LIDAR_POINTS_PUBLIC_BASE", "https://lidar.landslidescience.org/points")
HERE = Path(__file__).resolve().parent
SID = "kbay_soundings"; TITLE = "Kachemak Bay soundings, merged and corrected"
FILES = dict(nos_old="nos_1915_1931_edited.csv", nos_new="nos_1977_1984_edited.csv", lakes="soundings_edited.csv", hand="hand_placed_20261117.csv")
NOS_BASE = dict(nos_old="nos_1915_1931_20261103.csv", nos_new="nos_1977_1984_20261103.csv")
STATUS = dict(as_placed=0, sheet=1, moved=2, reread=3, unbound=4, rejected=5)
OGR = "/opt/homebrew/bin/ogr2ogr"; PDAL = "/opt/homebrew/bin/pdal"
STAC = "1.1.0"
EXT = ["https://stac-extensions.github.io/pointcloud/v2.0.0/schema.json", "https://stac-extensions.github.io/version/v1.2.0/schema.json",
       "https://stac-extensions.github.io/processing/v1.3.0/schema.json", "https://stac-extensions.github.io/projection/v2.1.0/schema.json",
       "https://stac-extensions.github.io/scientific/v1.0.0/schema.json", "https://stac-extensions.github.io/file/v2.1.0/schema.json"]
CITATION = ("landslidescience.org (2026): Kachemak Bay soundings, merged and corrected. Derived from NOAA National Ocean Service hydrographic surveys "
            "(digital soundings distributed by NOAA NCEI; public domain) and lake soundings by Riordan (2018), Berg (2017) and Higman (2019-2026).")
SURVEYS = HERE.parent / "water" / "sounding_surveys.json"     # each survey's title, collector, dates, method, licence and citation (`surveys` writes it from the dev database)
LICENCE = "CC-BY-4.0"                                           # Hig 2026-11-23: "to the extent we don't need to carry through something from source data, CC-attribution is a fine default for our work"
OURS = "CC-BY-4.0 (landslidescience.org default; collector's data shared as public)"
UPSTREAM = [dict(label="NOAA NCEI: NOS hydrographic survey data (GEODAS digital soundings), by survey id", url="https://www.ncei.noaa.gov/maps/bathymetry/"),
            dict(label="landslidescience.org lake soundings (shown in the lidar viewer)", url="https://landslidescience.org/lidar/")]


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def wgs84(xs, ys):
    from rasterio.warp import transform
    lo, la = transform("EPSG:6334", "EPSG:4326", list(xs), list(ys)); return np.array(lo), np.array(la)


def surveys_cmd(a=None):
    """The surveys' own records (citation, licence, method...) from the dev database's sounding_surveys into water/sounding_surveys.json, so a build needs no database."""
    import cast_edits as C
    cols = "slug, title, collector, collected_from, collected_to, method, instrument, licence, citation"
    out = {}
    for ln in C._psql(f"SELECT row_to_json(t) FROM (SELECT {cols} FROM sounding_surveys ORDER BY slug) t;").splitlines():
        d = json.loads(ln); out[d.pop("slug")] = {k: v for k, v in d.items() if v not in (None, "")}
    SURVEYS.write_text(json.dumps(dict(note="From the dev database's sounding_surveys (soundings_release.py surveys). Titles lose any '[shifted ...]' suffix: the ledger says what moved.", made=time.strftime("%Y-%m-%d"), surveys=out), indent=1) + "\n")
    print(f"{len(out)} surveys -> {SURVEYS}")


def build(a):
    src = Path(a.src); out = BUILD / SID / a.rev; (out / "stac").mkdir(parents=True, exist_ok=True)
    if (out / "manifest.json").exists() and not a.force:
        raise SystemExit(f"{out} is already built: a revision is never overwritten (--force only before it is published)")
    edits = {f["properties"]["key"]: f["properties"] | dict(to=f["geometry"]["coordinates"][:2]) for f in json.load(open(a.edits))["features"]} if a.edits and Path(a.edits).exists() else {}
    shifts = {r["survey"]: r for r in json.load(open(a.shifts))["shifts"]} if a.shifts and Path(a.shifts).exists() else {}
    offs = json.load(open(OBS / "nos_offsets.json")); off = {k: v for k, v in offs["surveys"].items() if v.get("dx") is not None}
    rows = []
    for grp, fn in FILES.items():
        for r in csv.DictReader(open(src / fn)):
            k = f'{r["survey"]}:{r["id"]}'; e = edits.get(k); o = off.get(r["survey"]) if grp in NOS_BASE else None
            xb, yb = float(r.get("x0") or r["x"]), float(r.get("y0") or r["y"])               # the base file's position
            st = "moved" if r.get("moved") == "1" else "reread" if r.get("depth0") else "sheet" if (r.get("moved") == "sheet" or o) else "unbound" if r.get("bound0") else "as_placed"
            rows.append(dict(key=k, survey=r["survey"], id=r["id"], kind=r["kind"], group=grp, x=float(r["x"]), y=float(r["y"]), z=float(r["z"]) if r["z"] not in ("", None) else None,
                             depth_m=float(r["depth_m"]) if r["depth_m"] else None, sigma=float(r["sigma"]) if r["sigma"] else None, bound=r.get("bound") or "", level_key=r.get("level_key") or "",
                             status=st, x_src=xb - (o["dx"] if o else 0.0), y_src=yb - (o["dy"] if o else 0.0), depth_src=float(r["depth0"]) if r.get("depth0") else (float(r["depth_m"]) if r["depth_m"] else None),
                             bound_src=r.get("bound0") or r.get("bound") or ""))
    have = {r["key"] for r in rows}
    for grp, fn in NOS_BASE.items():                                                      # the rejected casts: out of what the recipes read, kept on record here
        rej = {k for k, e in edits.items() if e["decision"] == "reject" and k not in have}
        if rej:
            for r in csv.DictReader(open(OBS / fn)):
                k = f'{r["survey"]}:{r["id"]}'
                if k in rej:
                    o = off.get(r["survey"]); x, y = float(r["x"]), float(r["y"])
                    rows.append(dict(key=k, survey=r["survey"], id=r["id"], kind=r["kind"], group=grp, x=x, y=y, z=float(r["z"]), depth_m=float(r["depth_m"]), sigma=float(r["sigma"]), bound="", level_key="",
                                     status="rejected", x_src=x - (o["dx"] if o else 0.0), y_src=y - (o["dy"] if o else 0.0), depth_src=float(r["depth_m"]), bound_src=""))
    rows.sort(key=lambda r: (r["survey"], int(r["id"]) if str(r["id"]).isdigit() else 0))
    lon, lat = wgs84([r["x"] for r in rows], [r["y"] for r in rows])
    for r, lo, la in zip(rows, lon, lat): r["lon"], r["lat"] = round(float(lo), 6), round(float(la), 6)
    surveys = sorted({r["survey"] for r in rows}); snum = {s: i + 1 for i, s in enumerate(surveys)}
    cols = ["key", "survey", "id", "kind", "x", "y", "lon", "lat", "z", "depth_m", "sigma", "bound", "level_key", "status", "x_src", "y_src", "depth_src", "bound_src"]
    fmt = lambda v, n: "" if v is None or v == "" else (f"{v:.{n}f}" if isinstance(v, float) else str(v))
    nd = dict(x=2, y=2, x_src=2, y_src=2, z=3, depth_m=2, depth_src=2, sigma=2, lon=6, lat=6)
    tmp = out / "soundings.csv"
    with open(tmp, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n"); w.writerow(cols)
        for r in rows: w.writerow([fmt(r[c], nd.get(c, 0)) for c in cols])
    # GeoPackage and the point cloud, from the CSV
    gp = out / "soundings.gpkg"; gp.unlink(missing_ok=True)
    subprocess.run([OGR, "-q", "-f", "GPKG", str(gp), str(tmp), "-nln", "soundings", "-a_srs", "EPSG:6334", "-oo", "X_POSSIBLE_NAMES=x", "-oo", "Y_POSSIBLE_NAMES=y", "-oo", "KEEP_GEOM_COLUMNS=YES", "-oo", "AUTODETECT_TYPE=YES"], check=True)
    pc = [r for r in rows if r["status"] != "rejected" and not r["bound"] and r["z"] is not None]
    ptxt = out / "_pc.txt"
    with open(ptxt, "w") as fh:
        fh.write("X,Y,Z,Classification,PointSourceId,UserData,Sigma\n")
        for r in pc: fh.write(f'{r["x"]:.2f},{r["y"]:.2f},{r["z"]:.3f},{45 if r["kind"] == "estimate" else 40},{snum[r["survey"]]},{STATUS[r["status"]]},{(r["sigma"] or 0):.2f}\n')
    laz = out / "soundings.copc.laz"; laz.unlink(missing_ok=True)
    pipe = [dict(type="readers.text", filename=str(ptxt), override_srs="EPSG:6334"), dict(type="writers.copc", filename=str(laz), extra_dims="Sigma=float", a_srs="EPSG:6334", scale_x=0.01, scale_y=0.01, scale_z=0.001, offset_x="auto", offset_y="auto", offset_z="auto")]
    pr = subprocess.run([PDAL, "pipeline", "--stdin"], input=json.dumps(pipe), text=True, capture_output=True); ptxt.unlink()
    if pr.returncode: print("point-cloud copy NOT written:", pr.stderr.strip()[:300]); laz.unlink(missing_ok=True)
    with open(tmp, "rb") as fi, gzip.GzipFile(out / "soundings.csv.gz", "wb", mtime=0) as fo: fo.write(fi.read())
    tmp.unlink()
    # THE LEDGER
    ch = []
    for s, v in sorted(off.items()):
        n = sum(1 for r in rows if r["survey"] == s)
        if n: ch.append(dict(type="sheet_translation", stage="source conversion", survey=s, casts=n, dx_m=v["dx"], dy_m=v["dy"], date=offs["made"][:10], decided_by="rule (nos_offsets.py), reviewed by Hig 2026-11-01",
                             basis=f'NCEI\'s conversion of the survey\'s "Early Alaska Datum" positions to NAD83 is off by hundreds of metres, consistently within a survey. Translation by method "{v["method"]}" (depth = fit to the 1977-84 casts; land = fewest casts on lidar land; inferred = the median of the surveys run in the same season).',
                             evidence={q: v[q] for q in ("depth", "land") if v.get(q)}))
    for s, v in sorted(shifts.items()):
        ch.append(dict(type="sheet_translation", stage="review", survey=s, casts=sum(1 for r in rows if r["survey"] == s), dx_m=v["dx"], dy_m=v["dy"], date=v["date"], decided_by="Hig", basis=v["basis"], evidence=v.get("evidence", "")))
    for k, e in sorted(edits.items()):
        d = e["decision"]; c = dict(type=dict(move="move", reread="depth_reread", reject="reject", unbound="bound_lifted", use="kept")[d], stage="review", key=k, survey=e["survey"], date=e.get("date"), decided_by="Hig", basis=e.get("basis", ""))
        if d == "move": c.update(from_xy=[e["x0"], e["y0"]], to_xy=[round(e["to"][0], 2), round(e["to"][1], 2)])
        if d == "reread": c.update(depth_was_m=e["depth0_m"], depth_is_m=e["depth_m"], source=e.get("source"))
        if d == "reject": c.update(depth_m=e.get("depth0_m"))
        ch.append(c)
    import collections
    summ = collections.Counter(c["type"] + (" (" + c["stage"] + ")" if c["type"] == "sheet_translation" else "") for c in ch)
    (out / "changes.json").write_text(json.dumps(dict(dataset=SID, revision=a.rev, parent=a.parent, crs="EPSG:6334", note="Every change from the sources (r0) to this revision. A cast is moved, not rejected, unless the reviewer says so by name; depths change only where read again from the survey's own sheet.", summary=dict(summ), changes=ch), indent=1) + "\n")
    # MANIFEST (the lidar revisions' keys where they apply)
    stc = collections.Counter(r["status"] for r in rows); bb = [round(float(lon.min()), 5), round(float(lat.min()), 5), round(float(lon.max()), 5), round(float(lat.max()), 5)]
    zs = [r["z"] for r in rows if r["z"] is not None]; base = f"{PUBLIC}/{SID}/{a.rev}/"
    files = []
    for nm, role, mt, ds in (("soundings.csv.gz", "data", "text/csv", "The table: one row per cast (gzip)"), ("soundings.gpkg", "data", "application/geopackage+sqlite3", "The table as a GeoPackage"),
                             ("soundings.copc.laz", "data", "application/vnd.laszip+copc", "The casts as a point cloud (COPC); rejected casts and depth bounds left out"),
                             ("changes.json", "metadata", "application/json", "The ledger of changes from the sources to this revision")):
        p = out / nm
        if p.exists(): files.append(dict(name=nm, role=role, type=mt, description=ds, bytes=p.stat().st_size, sha256=sha256(p), url=base + nm))
    sv = []; meta = json.load(open(SURVEYS))["surveys"] if SURVEYS.exists() else {}
    for s in surveys:
        q = [r for r in rows if r["survey"] == s]; m = dict(meta.get(s, {}))
        if m.get("title"): m["title"] = m["title"].split(" [shifted")[0]
        if s.startswith("nos_h") and not m.get("citation"):                # a NOS survey the dev database does not hold (the 1977-84 sheets): NCEI's own record, by the same pattern
            n = int(s[5:]); lo = (n - 1) // 2000 * 2000 + 1; H = s[4:].upper()
            m.update(collector="NOAA National Ocean Service", licence="US Government work, public domain (NOAA NCEI)", citation=f"NOAA NCEI, NOS hydrographic survey {H}, digital soundings (GEODAS), https://www.ngdc.noaa.gov/nos/H{lo:05d}-H{lo + 1999:05d}/{H}.html")
        if s == "hand_placed": m["citation"] = "landslidescience.org editors, 2026, hand-placed depth estimates (inferred, not measured)."
        if not m.get("licence") or m["licence"].startswith("Public (Hig"): m["licence"] = OURS
        sv.append(m | dict(number=snum[s], survey=s, casts=len(q), kind=q[0]["kind"], group=dict(nos_old="NOS hydrographic survey, 1915-31 lead line", nos_new="NOS hydrographic survey, 1977-84 echo sounder", lakes="lake and nearshore soundings", hand="hand-placed estimates")[q[0]["group"]]))
    man = dict(survey=SID, title=TITLE, revision=a.rev, label=a.label, kind="full", parent=a.parent, citation=CITATION,
               format="CSV (gzip), GeoPackage, COPC LAZ (LAS 1.4)", crs="EPSG:6334", crs_name="NAD83(2011) / UTM zone 5N, metres", vertical_datum="NAVD88 (GEOID12B), metres; NOS depths below MLLW taken to NAVD88 at -1.38 m",
               classes={"40": "bathymetric point (a sounding)", "45": "estimate (hand-placed)"}, status_codes={str(v): k for k, v in STATUS.items()},
               columns=dict(key="survey:id, stable across revisions", x="east now, m", y="north now, m", lon="WGS 84 of x, y", lat="", z="bed elevation, m NAVD88", depth_m="depth below the survey's own datum or lake level, m", sigma="one standard error we assign, m",
                            bound="not a depth but a bound on it (min = at least this deep)", level_key="the lake level a lake sounding hangs from", status="see status_codes", x_src="east as the source placed it", y_src="north as the source placed it",
                            depth_src="depth as the source's digital file gives it", bound_src="the bound before review"),
               made=time.strftime("%Y-%m-%d"), tool="tools/lidar/topobathy/soundings_release.py (ledger: cast_edits.py, nos_offsets.py)", why=a.why, changes=dict(summ), changes_file=base + "changes.json", products=a.products or [],
               licence=LICENCE, licence_note="Our corrections and the merged collection are CC BY 4.0. Each survey keeps its own terms (surveys[].licence): the NOS hydrographic surveys are US Government work in the public domain; cite the survey as well as this collection.",
               points=len(rows), points_in_cloud=len(pc), by_status=dict(stc), surveys=sv, bytes=sum(f["bytes"] for f in files), bounds_wgs84=bb, z_range_m=[round(min(zs), 2), round(max(zs), 2)], base_url=base, files=files, upstream=UPSTREAM)
    (out / "manifest.json").write_text(json.dumps(man, indent=1) + "\n")
    # STAC
    geom = dict(type="Polygon", coordinates=[[[bb[0], bb[1]], [bb[2], bb[1]], [bb[2], bb[3]], [bb[0], bb[3]], [bb[0], bb[1]]]])
    lineage = f"{a.label}. {a.why} Changes from the sources: " + "; ".join(f"{n} x {t}" for t, n in sorted(summ.items())) + ". Each change, with its basis and date, is in the `changes` asset."
    assets = {f["name"].split(".")[0] + ("_" + f["name"].split(".")[1] if f["name"].startswith("soundings") else ""): {"href": f["url"], "type": f["type"], "title": f["description"], "roles": [f["role"]], "file:size": f["bytes"], "file:checksum": "1220" + f["sha256"]} for f in files}
    assets["manifest"] = dict(href=base + "manifest.json", type="application/json", title="Revision manifest (the lidar revisions' layout)", roles=["metadata"])
    item = dict(type="Feature", stac_version=STAC, stac_extensions=EXT, id=f"{SID}_{a.rev}", collection=SID, geometry=geom, bbox=bb,
                properties={"title": f"{TITLE}, revision {a.rev}", "description": a.label, "datetime": None, "start_datetime": "1915-01-01T00:00:00Z", "end_datetime": "2026-11-17T00:00:00Z",
                            "created": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "version": a.rev, "deprecated": False,
                            "pc:count": len(pc), "pc:type": "other", "pc:schemas": [dict(name="X", size=8, type="floating"), dict(name="Y", size=8, type="floating"), dict(name="Z", size=8, type="floating"), dict(name="Classification", size=1, type="unsigned"),
                                                                                    dict(name="PointSourceId", size=2, type="unsigned"), dict(name="UserData", size=1, type="unsigned"), dict(name="Sigma", size=4, type="floating")],
                            "pc:statistics": [dict(name="Z", position=2, minimum=round(min(r["z"] for r in pc), 3), maximum=round(max(r["z"] for r in pc), 3), average=round(float(np.mean([r["z"] for r in pc])), 3), count=len(pc))],
                            "proj:code": "EPSG:6334", "processing:lineage": lineage, "processing:software": {"landslidescience/tools/lidar/topobathy/soundings_release.py": time.strftime("%Y-%m-%d")}, "processing:datetime": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "sci:citation": CITATION, "sci:publications": [dict(citation=v["citation"]) for v in sv if v.get("citation")], "kbay:table_rows": len(rows), "kbay:by_status": dict(stc)},
                links=[dict(rel="self", href=base + f"stac/{a.rev}.json", type="application/geo+json"), dict(rel="collection", href=f"{PUBLIC}/{SID}/collection.json", type="application/json"), dict(rel="parent", href=f"{PUBLIC}/{SID}/collection.json", type="application/json")]
                + ([dict(rel="predecessor-version", href=f"{PUBLIC}/{SID}/{a.parent}/stac/{a.parent}.json", type="application/geo+json", title=f"revision {a.parent}")] if a.parent and a.parent != "r0" else [])
                + [dict(rel="derived_from", href=u["url"], title=u["label"]) for u in UPSTREAM], assets=assets)
    (out / "stac" / f"{a.rev}.json").write_text(json.dumps(item, indent=1) + "\n")
    collection()
    print(f"{SID} {a.rev}: {len(rows)} casts ({len(pc)} in the cloud), {dict(stc)}; {len(ch)} ledger rows {dict(summ)}; -> {out}")
    for f in files: print(f"  {f['name']}: {f['bytes'] / 1e6:.1f} MB")


def collection():
    """The dataset's STAC Collection, from the revisions built so far (rewritten at each build)."""
    revs = sorted((json.load(open(p)) for p in (BUILD / SID).glob("r*/manifest.json")), key=lambda m: int(m["revision"][1:]))
    if not revs: return
    bb = [min(m["bounds_wgs84"][0] for m in revs), min(m["bounds_wgs84"][1] for m in revs), max(m["bounds_wgs84"][2] for m in revs), max(m["bounds_wgs84"][3] for m in revs)]
    col = {"type": "Collection", "stac_version": STAC, "stac_extensions": [EXT[1], EXT[4]], "id": SID, "title": TITLE,
           "description": "Every sparse depth measurement the Kachemak Bay topobathy composite reads, as one corrected point set: NOAA National Ocean Service hydrographic surveys (1915-31 lead line, 1977-84 echo sounder), lake soundings, and a few hand-placed estimates (flagged). "
                          "Each revision is the whole table plus the ledger of every change from the sources: whole-sheet translations of surveys whose positions were converted from early Alaska datums, single casts moved by the reviewer, a depth read again from the survey's sheet, a cast rejected. "
                          "A cast is moved, not rejected, unless the reviewer says so by name; a revision is never overwritten.",
           "license": LICENCE, "keywords": ["bathymetry", "soundings", "hydrographic survey", "lead line", "Kachemak Bay", "Alaska", "point cloud"],
           "providers": [dict(name="NOAA National Ocean Service / NCEI", roles=["producer", "licensor"], url="https://www.ncei.noaa.gov/"), dict(name="Lake soundings: N. Riordan (2018), E. Berg (2017), B. Higman (2017-2026)", roles=["producer"]), dict(name="landslidescience.org", roles=["processor", "licensor", "host"], url="https://landslidescience.org/")],
           "extent": dict(spatial=dict(bbox=[bb]), temporal=dict(interval=[["1915-01-01T00:00:00Z", "2026-11-17T00:00:00Z"]])),
           "summaries": {"version": [m["revision"] for m in revs], "proj:code": ["EPSG:6334"], "pc:type": ["other"]},
           "sci:citation": CITATION, "sci:publications": [dict(citation=v["citation"]) for v in revs[-1]["surveys"] if v.get("citation")], "version": revs[-1]["revision"],
           "links": [dict(rel="self", href=f"{PUBLIC}/{SID}/collection.json", type="application/json")]
                    + [dict(rel="item", href=f"{PUBLIC}/{SID}/{m['revision']}/stac/{m['revision']}.json", type="application/geo+json", title=f"{m['revision']}: {m['label']}") for m in revs]
                    + [dict(rel="latest-version", href=f"{PUBLIC}/{SID}/{revs[-1]['revision']}/stac/{revs[-1]['revision']}.json", type="application/geo+json")]
                    + [dict(rel="derived_from", href=u["url"], title=u["label"]) for u in UPSTREAM]}
    (BUILD / SID / "collection.json").write_text(json.dumps(col, indent=1) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build"); b.add_argument("rev"); b.add_argument("--label", required=True); b.add_argument("--why", required=True); b.add_argument("--parent", default="r0")
    b.add_argument("--from", dest="src", default=str(OBS)); b.add_argument("--edits", default=str(HERE.parent / "water" / "cast_edits.geojson")); b.add_argument("--shifts", default=str(HERE.parent / "water" / "sheet_shifts.json"))
    b.add_argument("--products", nargs="*"); b.add_argument("--force", action="store_true")
    sp.add_parser("collection"); sp.add_parser("surveys")
    a = ap.parse_args(); dict(build=build, collection=lambda a: collection(), surveys=surveys_cmd)[a.cmd](a)
