#!/usr/bin/env python3
"""The shore model in 3D (Hig 2026-11-04): a run's surface with its tide-level contours, the lines that shaped it, its casts and its rocks, each at its own height.  Look only: lines and casts are edited in QGIS.
    shore3d.py --set taylor=/path/to/out_cm5:kbay_joint_cm5_taylor [--set bay=/path/to/kbay_joint_cm5:kbay_joint_cm5] [--port 8768]     then http://127.0.0.1:8768/
A set is a run's review files (shore_contours, cast_review, cast_lines, rock_review .geojson) and the dev survey to draw them on.  Local, mask-tool pattern: Flask on the Mac, never the droplet.
The custom-layer vertical scale is ls_water.js's; this page lives here because /lidar/ is the hosting stream's (WORKSTREAMS.md)."""
import argparse, json, os
from pathlib import Path
import numpy as np
from flask import Flask, jsonify, send_file, send_from_directory, abort
from rasterio.warp import transform

HERE = Path(__file__).resolve().parent
TIDE = HERE.parent.parent / "water" / "tide_lines.geojson"
PM = Path(os.environ.get("LIDAR_DEV_PMTILES", str(Path.home() / "Claude_projects/landslidescience/data/lidar_dev/pmtiles")))
app = Flask(__name__); SETS = {}; CACHE = {}


def _ll(xy):
    a = np.asarray(xy, float); lo, la = transform("EPSG:6334", "EPSG:4326", a[:, 0].tolist(), a[:, 1].tolist())
    return np.round(np.c_[lo, la], 7)


def _gj(p):
    return json.load(open(p))["features"] if p.exists() else []


def _lines(F, zf, kind, bb=None):
    """Lines as [kind, label, [[lon, lat, z], ...]]; every vertex carries its own z."""
    out = []
    for f in F:
        g = f.get("geometry") or {}; q = f["properties"]
        for ln in ([g["coordinates"]] if g.get("type") == "LineString" else g.get("coordinates", [])):
            a = np.asarray(ln, float)[:, :2]
            if len(a) < 2 or (bb and not (a[:, 0].max() >= bb[0] and a[:, 0].min() <= bb[2] and a[:, 1].max() >= bb[1] and a[:, 1].min() <= bb[3])):
                continue
            k, lab, z = zf(q, len(a)); o = _ll(a)
            out.append([k or kind, lab, np.c_[o, np.round(z, 2)].tolist(), q.get("level_sigma_m")])
    return out


def _tide_of(run):
    """The lines this run was built with, from its archived recipe (a later edit of the live file is not what shaped it)."""
    r = run / "recipe.json"
    p = json.load(open(r)).get("fill", {}).get("tide_lines") if r.exists() else None
    return Path(p) if p and Path(p).exists() else TIDE


def build(name):
    if name in CACHE:
        return CACHE[name]
    run, survey = SETS[name]; run = Path(run)
    casts = []; xy = []; Fc = _gj(run / "cast_review.geojson")
    for f in Fc:
        xy.append(f["geometry"]["coordinates"][:2])
    bb = (min(p[0] for p in xy) - 500, min(p[1] for p in xy) - 500, max(p[0] for p in xy) + 500, max(p[1] for p in xy) + 500) if xy else None
    if xy:
        L = _ll(xy)
        for f, (lo, la) in zip(Fc, L):
            q = f["properties"]; casts.append(dict(p=[lo, la], z=q["z"], s=q.get("surface"), st=q["status"], c=bool(q.get("contradicted")), k=q["key"], m=bool(q.get("moved")), l=q.get("line_m")))
    Fr = _gj(run / "rock_review.geojson"); rocks = []
    if Fr:
        L = _ll([f["geometry"]["coordinates"][:2] for f in Fr])
        for f, (lo, la) in zip(Fr, L):
            q = f["properties"]; rocks.append(dict(p=[lo, la], z=q["floor_m"], s=q.get("surface"), st=q["status"], k=q["key"], t=q["rock_type"], lift=q.get("lift_m")))
    level = lambda q, n: (None, f'{q.get("label") or ""} {q["level_m"]:g} m', np.full(n, q["level_m"]))
    held = lambda q, n: (q["source"], f'{q["source"]} {q["level_m"]:g} m' + (f' ±{q["level_sigma_m"]:g}' if q.get("level_sigma_m") else "") + (f' ({q["why"]})' if q.get("why") else ""), np.full(n, q["level_m"]))
    tide = _tide_of(run)
    trace = lambda q, n: (q.get("kind"), f'{q.get("kind")} {q.get("z_a"):g}..{q.get("z_b"):g} m', np.linspace(q.get("z_a", 0), q.get("z_b", 0), n))
    D = dict(name=name, survey=survey, sets=list(SETS), casts=casts, rocks=rocks, contours=_lines(_gj(run / "shore_contours.geojson"), level, "contour"),
             tide=_lines(_gj(tide) + _gj(tide.with_name("tide_lines_tossed.geojson")), held, "vector", bb), traces=_lines(_gj(run / "cast_lines.geojson"), trace, "trough"))
    D["center"] = [float(np.median([c["p"][0] for c in casts])), float(np.median([c["p"][1] for c in casts]))] if casts else None
    if casts:           # a run still going has no files yet: look again next time
        CACHE[name] = D
    return D


@app.route("/")
def index():
    return send_from_directory(HERE, "shore3d.html")


@app.route("/api/<name>")
def data(name):
    if name not in SETS:
        abort(404)
    return jsonify(build(name))


@app.route("/pmtiles/<name>")
def pmtiles(name):
    if "/" in name or not name.endswith(".pmtiles"):
        abort(404)
    return send_file(PM / name, mimetype="application/vnd.pmtiles", conditional=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--set", action="append", required=True); ap.add_argument("--port", type=int, default=8768); A = ap.parse_args()
    for s in A.set:
        n, v = s.split("=", 1); SETS[n] = v.rsplit(":", 1)
    app.run(host="127.0.0.1", port=A.port, threaded=True)
