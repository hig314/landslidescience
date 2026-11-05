#!/usr/bin/env python3
"""A reviewer's decisions on single soundings (SHORE_REVIEW_WORKFLOW.md, Hig 2026-11-03/04): a cast is moved, not rejected.

  cast_edits.py export OUT.gpkg [--review cast_review.geojson]   the layer to edit in QGIS: every NOS cast at its current position, with its original position, and what the last run did with it
  cast_edits.py merge EDITED.gpkg [--basis TEXT]                 what changed in that layer, into water/cast_edits.geojson (cumulative: a point dragged back cancels its move)
  cast_edits.py apply                                            the base CSVs + the edits -> *_edited.csv, which the recipes read
  cast_edits.py sync-dev                                         the moves into the dev database, so the dev viewer draws them (apply does this too)

Drag a point to move it; the depth stays. A `decision` of use keeps what the spike filter dropped, questionable is a note. Deleting a point does nothing: the merge reports it.
The base CSVs are never touched. Every consumer reads the edited CSV, so a moved cast takes exactly the rules of any other (its position alone differs); only the despike passes over it."""
import argparse, csv, json, os, subprocess, tempfile, datetime
from pathlib import Path
import numpy as np

OBS = Path("/Volumes/Nunatak/lidar_build/obs/soundings")
BASE = {OBS / "nos_1915_1931_20261103.csv": OBS / "nos_1915_1931_edited.csv", OBS / "nos_1977_1984_20261103.csv": OBS / "nos_1977_1984_edited.csv"}
EDITS = Path(__file__).resolve().parent.parent / "water" / "cast_edits.geojson"
OGR = "/opt/homebrew/bin/ogr2ogr"
DEV_DB = "tethys-timescale-grafana-tethys_db-1"
MOVED_M = 0.5


def _edits():
    if not EDITS.exists():
        return {}
    return {f["properties"]["key"]: f for f in json.load(open(EDITS))["features"]}


def _key(r):
    return f'{r["survey"]}:{r["id"]}'


def export(a):
    E = _edits(); R = {}
    if a.review:
        for f in json.load(open(a.review))["features"]:
            q = f["properties"]; x, y = f["geometry"]["coordinates"][:2]; k = (round(x, 2), round(y, 2), q["survey"])
            if k not in R or q.get("contradicted"):            # a cast in two boxes' overlap: the worse reading
                R[k] = q
    F = []
    for src in BASE:
        with open(src) as fh:
            for r in csv.DictReader(fh):
                if r.get("bound") or r.get("z") in (None, ""):
                    continue
                x0, y0 = float(r["x"]), float(r["y"]); e = E.get(_key(r)); q = R.get((round(x0, 2), round(y0, 2), r["survey"]), {})
                xy = e["geometry"]["coordinates"][:2] if e else [x0, y0]
                F.append(dict(type="Feature", geometry=dict(type="Point", coordinates=xy),
                              properties=dict(key=_key(r), survey=r["survey"], z=float(r["z"]), sigma=float(r["sigma"]), x0=x0, y0=y0, decision=e["properties"]["decision"] if e else "", basis=e["properties"].get("basis", "") if e else "",
                                              status=q.get("status"), surface=q.get("surface"), residual=q.get("residual"), contradicted=q.get("contradicted"), near_land=q.get("near_land"))))
    with tempfile.TemporaryDirectory() as td:
        g = f"{td}/c.geojson"; json.dump(dict(type="FeatureCollection", crs=dict(type="name", properties=dict(name="EPSG:6334")), features=F), open(g, "w"))
        if os.path.exists(a.out):
            os.remove(a.out)
        subprocess.run([OGR, "-q", "-f", "GPKG", "-nln", "cast_review", "-a_srs", "EPSG:6334", a.out, g], check=True)
    print(f"{len(F)} casts -> {a.out}; {len(E)} edits carried")


def merge(a):
    with tempfile.TemporaryDirectory() as td:
        g = f"{td}/e.geojson"; subprocess.run([OGR, "-q", "-f", "GeoJSON", g, a.edited, "cast_review"], check=True); F = json.load(open(g))["features"]
    E = _edits(); seen = set(); today = datetime.date.today().isoformat(); n = dict(move=0, use=0, questionable=0, cancelled=0, bad_decision=[], depth_changed=[])
    for f in F:
        q = f["properties"]; k = q["key"]; seen.add(k); x, y = f["geometry"]["coordinates"][:2]; dx, dy = x - q["x0"], y - q["y0"]; moved = float(np.hypot(dx, dy)) > MOVED_M
        d = (q.get("decision") or "").strip().lower() or ("move" if moved else "")
        if d not in ("", "move", "use", "questionable"):
            n["bad_decision"].append(k); continue
        if moved and d != "move":
            d = "move"
        if not d or (d == "move" and not moved):
            if k in E:
                del E[k]; n["cancelled"] += 1
            continue
        old = E.get(k)
        E[k] = dict(type="Feature", geometry=dict(type="Point", coordinates=[round(x, 2), round(y, 2)]),
                    properties=dict(key=k, survey=q["survey"], z=q["z"], decision=d, x0=q["x0"], y0=q["y0"], dx=round(dx, 2), dy=round(dy, 2), basis=q.get("basis") or a.basis or "",
                                    date=old["properties"]["date"] if old and old["geometry"]["coordinates"] == [round(x, 2), round(y, 2)] else today))
        n[d] += 1
    EDITS.write_text(json.dumps(dict(type="FeatureCollection", crs=dict(type="name", properties=dict(name="EPSG:6334")), features=list(E.values())), indent=0))
    print({k: (v if not isinstance(v, list) else v[:10]) for k, v in n.items()}, f"{len(F)} points read, {len(E)} edits on file")
    tot = sum(1 for src in BASE for r in csv.DictReader(open(src)) if not r.get("bound") and r.get("z") not in (None, ""))
    if len(F) != tot:
        print(f"note: the layer holds {len(F)} of {tot} casts: a deleted point is no decision, and its cast stays as it was")
    by = {}
    for f in E.values():
        if f["properties"]["decision"] == "move":
            by.setdefault(f["properties"]["survey"], []).append((f["properties"]["dx"], f["properties"]["dy"]))
    for s, v in sorted(by.items()):
        v = np.array(v); print(f"  {s}: {len(v)} moved, median ({np.median(v[:, 0]):+.0f}, {np.median(v[:, 1]):+.0f}) m, spread ({v[:, 0].std():.0f}, {v[:, 1].std():.0f}) m")


def apply(a):
    E = _edits(); used = set()
    for src, dst in BASE.items():
        with open(src) as fh:
            rd = csv.DictReader(fh); fld = rd.fieldnames + ["x0", "y0", "moved", "held"]; rows = []
            for r in rd:
                e = E.get(_key(r)); r.update(x0="", y0="", moved="", held="")
                if e:
                    used.add(_key(r)); p = e["properties"]
                    if p["decision"] == "move":
                        r["x0"], r["y0"] = r["x"], r["y"]; r["x"], r["y"] = f'{e["geometry"]["coordinates"][0]:.2f}', f'{e["geometry"]["coordinates"][1]:.2f}'; r["moved"] = "1"; r["held"] = "1"
                    elif p["decision"] == "use":
                        r["held"] = "1"
                rows.append(r)
        with open(str(dst) + ".part", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fld, lineterminator="\n"); w.writeheader(); w.writerows(rows)
        os.replace(str(dst) + ".part", dst); print(f"{dst.name}: {len(rows)} rows, {sum(r['moved'] == '1' for r in rows)} moved, {sum(r['held'] == '1' for r in rows)} held")
    miss = set(E) - used
    if miss:
        print("edits whose cast is in no base file:", sorted(miss)[:10])
    try:
        sync_dev()
    except (OSError, subprocess.CalledProcessError) as e:
        print("dev database not updated (run cast_edits.py sync-dev when it is up):", e)


def _psql(sql):
    return subprocess.run(["docker", "exec", "-i", "-e", "PGPASSWORD=tethys_pass", DEV_DB, "psql", "-h", "127.0.0.1", "-U", "tethys", "-d", "landslides", "-At", "-F", "|", "-v", "ON_ERROR_STOP=1", "--single-transaction"], input=sql, text=True, capture_output=True, check=True).stdout


def sync_dev(a=None):
    """The dev database's soundings (what /lidar/?catalog=dev draws) follow the moves: geometry only, the depth stays.  The dev database alone, never production.  Each moved row's note holds its key and its
    position before the move, so a cancelled move goes back; load_soundings replaces a survey whole, so run this again after one."""
    P = "cast_edits:"; M = {k: f for k, f in _edits().items() if f["properties"]["decision"] == "move"}; n = dict(moved=0, restored=0, unchanged=0, not_found=[], ambiguous=[])
    now = {}
    for ln in _psql(f"SELECT id, notes FROM soundings WHERE notes LIKE '{P}%';").splitlines():
        i, note = ln.split("|", 1); now[note.split(" ", 1)[0][len(P):]] = (int(i), note)
    sql = []
    for k, (i, note) in now.items():
        if k not in M:
            h, _, rest = note.partition(" | "); lon, lat = h.split(" was ")[1].split()
            sql.append(f"UPDATE soundings SET geom = ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326), notes = '{rest.replace(chr(39), chr(39) * 2)}', updated_at = now() WHERE id = {i};"); n["restored"] += 1
    for k, f in M.items():
        x, y = f["geometry"]["coordinates"][:2]; new = f"ST_Transform(ST_SetSRID(ST_MakePoint({x}, {y}), 6334), 4326)"
        if k in now:
            sql.append(f"UPDATE soundings SET geom = {new}, updated_at = now() WHERE id = {now[k][0]} AND ST_Distance(ST_Transform(geom, 6334), ST_SetSRID(ST_MakePoint({x}, {y}), 6334)) > 0.01;"); n["unchanged"] += 1
            continue
        p = f["properties"]; pt = f"ST_SetSRID(ST_MakePoint({p['x0']}, {p['y0']}), 6334)"
        R = _psql(f"SELECT s.id, ST_X(s.geom), ST_Y(s.geom), s.notes FROM soundings s JOIN sounding_surveys u ON u.id = s.survey_id WHERE u.slug = '{p['survey']}' AND ST_DWithin(ST_Transform(s.geom, 6334), {pt}, 1.0);").splitlines()
        if len(R) != 1:
            n["not_found" if not R else "ambiguous"].append(k); continue
        i, lon, lat, old = R[0].split("|", 3); note = f"{P}{k} was {lon} {lat}" + (f" | {old}" if old else "")
        sql.append(f"UPDATE soundings SET geom = {new}, notes = '{note.replace(chr(39), chr(39) * 2)}', updated_at = now() WHERE id = {i};"); n["moved"] += 1
    if sql:
        _psql("\n".join(sql))
    print("dev database:", {k: (v if not isinstance(v, list) else v[:10]) for k, v in n.items()})


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("export"); c.add_argument("out"); c.add_argument("--review")
    m = sp.add_parser("merge"); m.add_argument("edited"); m.add_argument("--basis")
    sp.add_parser("apply"); sp.add_parser("sync-dev")
    a = ap.parse_args(); dict(export=export, merge=merge, apply=apply, **{"sync-dev": sync_dev})[a.cmd](a)
