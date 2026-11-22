#!/usr/bin/env python3
"""A reviewer's decisions on single soundings (SHORE_REVIEW_WORKFLOW.md, Hig 2026-11-03/04): a cast is moved, not rejected.

  cast_edits.py export OUT.gpkg [--review cast_review.geojson]   the layer to edit in QGIS: every NOS cast at its current position, with its original position, and what the last run did with it
  cast_edits.py merge EDITED.gpkg [--basis TEXT]                 what changed in that layer, into water/cast_edits.geojson (cumulative: a point dragged back cancels its move)
  cast_edits.py apply                                            the base CSVs + the edits -> *_edited.csv, which the recipes read
  cast_edits.py sync-dev                                         the moves into the dev database, so the dev viewer draws them (apply does this too)
  cast_edits.py reread KEY DEPTH_M --basis TEXT [--source URL]   a depth READ AGAIN FROM THE SURVEY'S OWN SHEET, where the digital file disagrees with it (then run apply)
  cast_edits.py reject KEY --basis TEXT [--withdraw]             a cast the REVIEWER rejects outright (then run apply)

A REJECTION IS THE REVIEWER'S CALL, NAMED AND WRITTEN DOWN (2026-11-16, Hig: "Let's reject this one", nos_h05191:61292 at 59.06360,-151.37572:
3 fathoms in the 1931 lead-line record among 33-35 fathoms, nearest neighbour 222 m, almost certainly a dropped digit; no sheet to reread it from).
The cast leaves the edited CSV every recipe reads; the base CSV keeps it, the dev database flags it 'rejected' (drawn as a grey ring), and
--withdraw puts it back.  Rules never reject: a lone cast that only looks unlikely goes on the review list, not here.

A REREAD IS NOT A REJECTION, AND NOT AN OPINION ABOUT THE SEA BED (2026-11-05, Hig's review of cm18, nos_h05187:55404 at 59.16419,-151.51308: 0.9 m among 11-13 fathoms, no rock on the chart; the 1931 smooth sheet shows
a plain "12" there, keyed as "1/2" fathom when the sheet was digitized).  The measurement is the surveyor's number on the sheet; the digital file is a copy of it, and where the copy is wrong the sheet is put back.  A reread
needs the sheet looked at and the basis written down (--basis: what the sheet shows, how the record was matched to it); a depth that only looks unlikely is no reread.  The digital value stays on record (depth0_m in the
edits file, the depth0 column of the edited CSV, depth_orig in the dev database).

Drag a point to move it; the depth stays. A `decision` of use keeps what the spike filter dropped, questionable is a note. Deleting a point does nothing: the merge reports it.
The base CSVs are never touched. Every consumer reads the edited CSV, so a moved cast takes exactly the rules of any other (its position alone differs); only the despike passes over it."""
import argparse, csv, json, os, subprocess, tempfile, datetime
from pathlib import Path
import numpy as np

OBS = Path("/Volumes/Nunatak/lidar_build/obs/soundings")
BASE = {OBS / "nos_1915_1931_20261103.csv": OBS / "nos_1915_1931_edited.csv", OBS / "nos_1977_1984_20261103.csv": OBS / "nos_1977_1984_edited.csv"}
# the lake and nearshore soundings (tools/soundings export): only an `unbound` edit applies to them -- they are not in the review layer
LAKE = {OBS / "soundings_20261121.csv": OBS / "soundings_edited.csv"}   # 20261017 + Petrof Lake at 14.00 m (Hig 2026-11-21; level_key pg_2022_a was 3DEP's flat, 8.42)
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


def _reread_shift(e, r):
    """How far a reread lowers the cast (new depth - the base file's), 0 for any other decision.  Refuses a reread whose recorded digital depth is not the base file's: the base changed under it."""
    p = e["properties"]
    if p.get("decision") != "reread":
        return 0.0
    if abs(float(r["depth_m"]) - float(p["depth0_m"])) > 0.051:
        raise SystemExit(f'{p["key"]}: the reread was made against a digital depth of {p["depth0_m"]} m, the base file now says {r["depth_m"]} m -- look again')
    return float(p["depth_m"]) - float(p["depth0_m"])


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
                zr = float(r["z"]) - (_reread_shift(e, r) if e else 0.0)
                F.append(dict(type="Feature", geometry=dict(type="Point", coordinates=xy),
                              properties=dict(key=_key(r), survey=r["survey"], z=round(zr, 3), sigma=float(r["sigma"]), x0=x0, y0=y0, decision=e["properties"]["decision"] if e else "", basis=e["properties"].get("basis", "") if e else "",
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
    E = _edits(); seen = set(); today = datetime.date.today().isoformat(); n = dict(move=0, use=0, questionable=0, reread=0, reject=0, cancelled=0, bad_decision=[], depth_changed=[])
    for f in F:
        q = f["properties"]; k = q["key"]; seen.add(k); x, y = f["geometry"]["coordinates"][:2]; dx, dy = x - q["x0"], y - q["y0"]; moved = float(np.hypot(dx, dy)) > MOVED_M
        d = (q.get("decision") or "").strip().lower() or ("move" if moved else "")
        if k in E and E[k]["properties"]["decision"] == "reread":          # made by `reread`, from the sheet: the layer can move it, nothing else
            if moved:
                E[k]["geometry"]["coordinates"] = [round(x, 2), round(y, 2)]; E[k]["properties"].update(dx=round(dx, 2), dy=round(dy, 2))
            n["reread"] += 1; continue
        if k in E and E[k]["properties"]["decision"] == "reject":          # made by `reject`: only `reject --withdraw` undoes it
            n["reject"] += 1; continue
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


def reread(a):
    """Record a depth read again from the survey's sheet.  The position is the cast's current one (its move, if it has one, stays)."""
    E = _edits(); row = None
    for src in BASE:
        with open(src) as fh:
            for r in csv.DictReader(fh):
                if _key(r) == a.key:
                    row = r; break
        if row:
            break
    if row is None:
        raise SystemExit(f"{a.key} is in no base file")
    if not (a.basis or "").strip():
        raise SystemExit("a reread needs its basis: what the sheet shows and how the record was matched to it")
    old = E.get(a.key); x0, y0 = float(row["x"]), float(row["y"]); xy = old["geometry"]["coordinates"][:2] if old else [x0, y0]
    d0 = float(row["depth_m"]); z = round(float(row["z"]) - (a.depth_m - d0), 3)
    E[a.key] = dict(type="Feature", geometry=dict(type="Point", coordinates=[round(xy[0], 2), round(xy[1], 2)]),
                    properties=dict(key=a.key, survey=row["survey"], z=z, decision="reread", x0=x0, y0=y0, dx=round(xy[0] - x0, 2), dy=round(xy[1] - y0, 2), depth_m=a.depth_m, depth0_m=d0, z0=float(row["z"]),
                                    basis=a.basis.strip(), source=a.source or "", date=datetime.date.today().isoformat()))
    EDITS.write_text(json.dumps(dict(type="FeatureCollection", crs=dict(type="name", properties=dict(name="EPSG:6334")), features=list(E.values())), indent=0))
    print(f"{a.key}: depth {d0} m -> {a.depth_m} m (z {row['z']} -> {z}); {len(E)} edits on file.  Now: cast_edits.py apply")


def unbound(a):
    """A sounding exported as a LOWER BOUND (bound = min: the lead never reached the bed, or a line run along an ice face) read as an
    ordinary sounding again, with the basis written down (2026-11-10, Hig: Grewingk's 2017 ice-face line "swings back into the lake";
    assume the face was straight, and the stretch off it is an ordinary sounding).  KEYS is a JSON list of [key, ...] or of [key, ...]
    rows, or the keys themselves.  Then run apply."""
    if not (a.basis or "").strip():
        raise SystemExit("an unbound needs its basis")
    keys = a.keys
    if len(keys) == 1 and keys[0].endswith(".json"):
        keys = [k if isinstance(k, str) else k[0] for k in json.load(open(keys[0]))]
    rows = {}
    for src in LAKE:
        with open(src) as fh:
            for r in csv.DictReader(fh):
                if _key(r) in keys:
                    rows[_key(r)] = r
    miss = [k for k in keys if k not in rows]; nb = [k for k, r in rows.items() if not r.get("bound")]
    if miss or nb:
        raise SystemExit(f"not in the lake file: {miss[:5]}; not a bound: {nb[:5]}")
    E = _edits(); today = datetime.date.today().isoformat()
    for k, r in rows.items():
        x0, y0 = float(r["x"]), float(r["y"])
        E[k] = dict(type="Feature", geometry=dict(type="Point", coordinates=[round(x0, 2), round(y0, 2)]),
                    properties=dict(key=k, survey=r["survey"], z=float(r["z"]), decision="unbound", bound0=r["bound"], x0=x0, y0=y0, dx=0.0, dy=0.0, basis=a.basis.strip(), date=today))
    EDITS.write_text(json.dumps(dict(type="FeatureCollection", crs=dict(type="name", properties=dict(name="EPSG:6334")), features=list(E.values())), indent=0))
    print(f"{len(rows)} soundings read as ordinary; {len(E)} edits on file.  Now: cast_edits.py apply")


def reject(a):
    """Record (or with --withdraw remove) a reviewer's rejection of one NOS cast."""
    E = _edits()
    if a.withdraw:
        if E.get(a.key, {}).get("properties", {}).get("decision") != "reject":
            raise SystemExit(f"{a.key} is not rejected")
        del E[a.key]
    else:
        row = next((r for src in BASE for r in csv.DictReader(open(src)) if _key(r) == a.key), None)
        if row is None:
            raise SystemExit(f"{a.key} is in no base file")
        if not (a.basis or "").strip():
            raise SystemExit("a rejection needs its basis: why, and whose call")
        if a.key in E and E[a.key]["properties"]["decision"] not in ("reject",):
            raise SystemExit(f"{a.key} already has a {E[a.key]['properties']['decision']} edit: withdraw that first")
        x0, y0 = float(row["x"]), float(row["y"])
        E[a.key] = dict(type="Feature", geometry=dict(type="Point", coordinates=[round(x0, 2), round(y0, 2)]),
                        properties=dict(key=a.key, survey=row["survey"], z=float(row["z"]), decision="reject", x0=x0, y0=y0, dx=0.0, dy=0.0, depth0_m=float(row["depth_m"]),
                                        basis=a.basis.strip(), date=datetime.date.today().isoformat()))
    EDITS.write_text(json.dumps(dict(type="FeatureCollection", crs=dict(type="name", properties=dict(name="EPSG:6334")), features=list(E.values())), indent=0))
    print(f"{a.key}: {'rejection withdrawn' if a.withdraw else 'rejected'}; {len(E)} edits on file.  Now: cast_edits.py apply")


def apply(a):
    E = _edits(); used = set()
    for src, dst in {**BASE, **LAKE}.items():
        with open(src) as fh:
            rd = csv.DictReader(fh); fld = rd.fieldnames + ["x0", "y0", "moved", "held", "depth0", "bound0"]; rows = []; nrej = 0
            for r in rd:
                e = E.get(_key(r)); r.update(x0="", y0="", moved="", held="", depth0="", bound0="")
                if e and src in LAKE and e["properties"]["decision"] != "unbound":
                    e = None                                   # a lake sounding takes only an unbound
                if e and e["properties"]["decision"] == "reject":
                    used.add(_key(r)); nrej += 1; continue          # the reviewer's rejection: out of what the recipes read
                if e:
                    used.add(_key(r)); p = e["properties"]
                    if p["decision"] == "unbound":
                        r["bound0"] = r.get("bound") or ""; r["bound"] = ""
                    elif p["decision"] == "reread":
                        dz = _reread_shift(e, r); r["depth0"] = r["depth_m"]; r["depth_m"] = f'{float(p["depth_m"]):.1f}'
                        for c in ("z", "z_raw"):
                            if r.get(c) not in (None, ""):
                                r[c] = f'{float(r[c]) - dz:.3f}' if c == "z" else f'{float(r[c]) - dz:.2f}'
                        ex, ey = e["geometry"]["coordinates"][:2]
                        if float(np.hypot(ex - float(r["x"]), ey - float(r["y"]))) > MOVED_M:       # dragged as well: it is moved like any other
                            r["x0"], r["y0"] = r["x"], r["y"]; r["x"], r["y"] = f"{ex:.2f}", f"{ey:.2f}"; r["moved"] = "1"; r["held"] = "1"
                    elif p["decision"] == "move":
                        r["x0"], r["y0"] = r["x"], r["y"]; r["x"], r["y"] = f'{e["geometry"]["coordinates"][0]:.2f}', f'{e["geometry"]["coordinates"][1]:.2f}'; r["moved"] = "1"; r["held"] = "1"
                    elif p["decision"] == "use":
                        r["held"] = "1"
                rows.append(r)
        with open(str(dst) + ".part", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fld, lineterminator="\n"); w.writeheader(); w.writerows(rows)
        os.replace(str(dst) + ".part", dst); print(f"{dst.name}: {len(rows)} rows, {sum(r['moved'] == '1' for r in rows)} moved, {sum(r['held'] == '1' for r in rows)} held, {sum(r['depth0'] != '' for r in rows)} reread, {sum(r['bound0'] != '' for r in rows)} unbound, {nrej} rejected")
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
    # the rereads: depth_m takes the sheet's value, depth_orig keeps the digital file's, and the note says why (and holds the old depth, so a withdrawn reread goes back)
    Q = "cast_reread:"; RR = {k: f for k, f in _edits().items() if f["properties"]["decision"] == "reread"}; n.update(reread=0, reread_unchanged=0, reread_withdrawn=0)
    for ln in _psql(f"SELECT id, depth_m, replace(notes, E'\\n', ' ') FROM soundings WHERE notes LIKE '%{Q}%';").splitlines():
        i, dep, note = ln.split("|", 2); k = note.split(Q, 1)[1].split(" ", 1)[0]
        if k not in RR:
            was = note.split(Q, 1)[1].split(" was ", 1)[1].split(" m", 1)[0]; head, _, tail = note.partition(Q); rest = (head.rstrip(" |") + " " + tail[tail.find("||") + 2:]).strip()
            sql.append(f"UPDATE soundings SET depth_m = {float(was)}, notes = '{rest.replace(chr(39), chr(39) * 2)}', updated_at = now() WHERE id = {int(i)};"); n["reread_withdrawn"] += 1
    for k, f in RR.items():
        p = f["properties"]; x, y = f["geometry"]["coordinates"][:2]
        R = _psql(f"SELECT s.id, s.depth_m, replace(coalesce(s.notes, ''), E'\\n', ' ') FROM soundings s JOIN sounding_surveys u ON u.id = s.survey_id WHERE u.slug = '{p['survey']}' AND (ST_DWithin(ST_Transform(s.geom, 6334), "
                  f"ST_SetSRID(ST_MakePoint({p['x0']}, {p['y0']}), 6334), 1.0) OR ST_DWithin(ST_Transform(s.geom, 6334), ST_SetSRID(ST_MakePoint({x}, {y}), 6334), 1.0));").splitlines()
        if len(R) != 1:
            n["not_found" if not R else "ambiguous"].append(k); continue
        i, dep, old = R[0].split("|", 2)
        if f"{Q}{k} " in old:
            if abs(float(dep) - float(p["depth_m"])) > 0.051:
                sql.append(f"UPDATE soundings SET depth_m = {float(p['depth_m'])}, updated_at = now() WHERE id = {int(i)};")
            n["reread_unchanged"] += 1; continue
        if abs(float(dep) - float(p["depth0_m"])) > 0.051:
            n["ambiguous"].append(k); continue                               # the database's depth is not the digital file's: someone else changed it
        note = (f"{old} | " if old else "") + f"{Q}{k} was {p['depth0_m']} m in the digital file; reread from the survey's sheet as {p['depth_m']} m ({p['date']}). {p['basis'].replace('|', '/')} ||"    # after a move's header, which sync reads from the front
        sql.append(f"UPDATE soundings SET depth_m = {float(p['depth_m'])}, notes = '{note.replace(chr(39), chr(39) * 2)}', updated_at = now() WHERE id = {int(i)};"); n["reread"] += 1
    # the rejections: flag 'rejected' with the basis as the reason (prefixed, so a withdrawn one is cleared)
    J = "cast_reject:"; RJ = {k: f for k, f in _edits().items() if f["properties"]["decision"] == "reject"}; n.update(rejected=0, reject_withdrawn=0)
    for ln in _psql(f"SELECT id, flag_reason FROM soundings WHERE flag_reason LIKE '{J}%';").splitlines():
        i, fr = ln.split("|", 1)
        if fr[len(J):].split(" ", 1)[0] not in RJ:
            sql.append(f"UPDATE soundings SET flag = NULL, flag_reason = NULL, updated_at = now() WHERE id = {int(i)};"); n["reject_withdrawn"] += 1
    for k, f in RJ.items():
        p = f["properties"]
        R = _psql(f"SELECT s.id FROM soundings s JOIN sounding_surveys u ON u.id = s.survey_id WHERE u.slug = '{p['survey']}' AND ST_DWithin(ST_Transform(s.geom, 6334), "
                  f"ST_SetSRID(ST_MakePoint({p['x0']}, {p['y0']}), 6334), 1.0);").splitlines()
        if len(R) != 1:
            n["not_found" if not R else "ambiguous"].append(k); continue
        reason = f"{J}{k} {p['basis']} ({p['date']})".replace("'", "''")
        sql.append(f"UPDATE soundings SET flag = 'rejected', flag_reason = '{reason}', updated_at = now() WHERE id = {int(R[0])};"); n["rejected"] += 1
    if sql:
        _psql("\n".join(sql))
    print("dev database:", {k: (v if not isinstance(v, list) else v[:10]) for k, v in n.items()})


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("export"); c.add_argument("out"); c.add_argument("--review")
    m = sp.add_parser("merge"); m.add_argument("edited"); m.add_argument("--basis")
    sp.add_parser("apply"); sp.add_parser("sync-dev")
    j = sp.add_parser("reject"); j.add_argument("key"); j.add_argument("--basis"); j.add_argument("--withdraw", action="store_true")
    c = sp.add_parser("reread"); c.add_argument("key"); c.add_argument("depth_m", type=float); c.add_argument("--basis", required=True); c.add_argument("--source")
    c = sp.add_parser("unbound"); c.add_argument("keys", nargs="+"); c.add_argument("--basis", required=True)
    a = ap.parse_args(); dict(export=export, merge=merge, apply=apply, reread=reread, unbound=unbound, reject=reject, **{"sync-dev": sync_dev})[a.cmd](a)
