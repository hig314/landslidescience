#!/opt/anaconda3/bin/python3
"""NOS hydrographic survey digital soundings (NCEI GEODAS xyz.gz) -> the composite's points CSV.

    nos_soundings_csv.py RAW_DIR OUT_DIR [--surveys surveys_layer1.json] [--offsets nos_offsets.json] [--epsg 6334] [--mllw-shift -1.38] [--date 20261031]

--offsets (2026-11-01): nos_offsets.py's JSON -- one horizontal translation (dx east, dy north, metres in --epsg) per
survey, applied to every position of that survey before anything else.  NCEI converted the 1930-31 surveys from their
"Early Alaska Datums" to NAD83 some 400 m E / 320 m N short, so their casts fell on land and the bays they sounded
were drawn shallow between them; the offsets are measured against the modern lidar's land and the 1977-84 depths
(see nos_offsets.py).  A survey with no entry, or dx null, is left where NCEI put it.

--surveys: the NCEI nos_hydro_dynamic layer-1 query (f=json) whose SURVEY_YEAR is the year of record; six of the
bay's reports do not carry the year in the form the parser reads.

RAW_DIR holds <ID>.xyz.gz (CSV: survey_id,lat,long,depth,quality_code,active; depth in metres, positive down,
negative = drying height) and <ID>_h93.htm (the descriptive report, read for the year, the original sounding units
and the method) as NCEI serves them under
https://data.ngdc.noaa.gov/platforms/ocean/nos/coast/<BIN>/<ID>/GEODAS/ .  Run with python -I: the files are
downloaded data.

Every report here says vertical datum Mean Lower Low Water and horizontal datum of records NAD83, so
    z_navd88 = -depth + mllw_shift          (Seldovia: MLLW = -1.38 m NAVD88)
Only active == 1 rows are kept (19/0 rows are superseded soundings).

sigma by vintage and method (a prior; joint.py and fill.py read the column), two parts added in quadrature:
  depth  lead line, fathoms (1915-1931)          2.0 m   whole-fathom quantisation 1.83 m, and the 1964 subsidence
                                                          (0.5-1.5 m here) that these pre-date -- their MLLW is the
                                                          1930 sea level, so they read shallow today
         echo sounder, fathoms and tenths (1977-84)  0.8 m  (checked: NMAD 0.43 m against the 2009 multibeam)
         anything else                           1.5 m
  position  sigma_h * local gradient: sextant / three-point fixes at 1:20,000 ~100 m (1930s), range-range
         electronics ~30 m (1977-84). The gradient is a plane through the survey's own soundings within 300 m
         (Hig, 2026-10-31: "the horizontal wouldn't be that certain in 1930s"; the 1980s surveys read NMAD 0.3 on
         flats and 1.5-2.5 on fjord walls against the multibeam). Capped at sigma_max (10 m).

Two files, one per vintage, so a source date can be given to each (joint.py's epoch rule: a newer solid
observation supersedes an older one by > pool_years):
    OUT_DIR/nos_<a>_<b>_<date>.csv     columns x,y,z,sigma,depth_m,survey,kind,id,level_key,bound
"""
import argparse, csv, glob, gzip, html, os, re, sys

import numpy as np
from rasterio.warp import transform


def report(path):
    t = html.unescape(re.sub(r"<[^>]+>", " ", open(path, errors="ignore").read()))
    t = re.sub(r"\s+", " ", t)
    yr = re.search(r"Strt Yr End Yr Scale of Survey Seq (\d{4}) (\d{4}) ([\d,]+)", t)
    units = re.search(r"Original Sounding Units Seq \d+ (.*?) \d\d Sounding Method Seq (.*?) \d\d ", t)
    datum = re.search(r"Vertical Datum tide Original", t) and "MLLW" or ("Mean Lower Low Water" in t and "MLLW" or "?")
    return dict(year=int(yr.group(1)) if yr else None, scale=yr.group(3) if yr else "",
                units=(re.sub(r"^[\d. ]+", "", units.group(1).replace("Mean Lower Low Water", "")).strip() if units else ""),
                method=(units.group(2).strip() if units else ""), datum=datum)


def sigma_for(meta):
    """(depth sigma, horizontal sigma) by method and units."""
    u, m = meta["units"].lower(), meta["method"].lower()
    if "lead" in m:
        return 2.0, 100.0
    if "echo" in m and "tenth" in u:
        return 0.8, 30.0
    return 1.5, 50.0


def gradient_sigma(x, y, z, sz, sh, r=300.0, nmin=5, cap=10.0):
    """Per-sounding sigma = sqrt(sz^2 + (sh * |grad z|)^2), the gradient from a plane through the neighbours within r."""
    from scipy.spatial import cKDTree
    x = np.asarray(x); y = np.asarray(y); z = np.asarray(z)
    out = np.full(x.size, float(sz))
    if x.size < nmin + 1:
        return np.minimum(out, cap)
    tr = cKDTree(np.c_[x, y])
    for i, nb in enumerate(tr.query_ball_point(np.c_[x, y], r)):
        nb = np.asarray(nb, int)
        if nb.size < nmin:
            out[i] = cap   # a lone sounding: nothing says the bed is flat around it
            continue
        G = np.c_[np.ones(nb.size), x[nb] - x[i], y[nb] - y[i]]
        cf, *_ = np.linalg.lstsq(G, z[nb], rcond=None)
        out[i] = np.sqrt(sz ** 2 + (sh * np.hypot(cf[1], cf[2])) ** 2)
    return np.minimum(out, cap)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw"); ap.add_argument("out")
    ap.add_argument("--epsg", type=int, default=6334); ap.add_argument("--mllw-shift", type=float, default=-1.38)
    ap.add_argument("--date", default="20261031"); ap.add_argument("--surveys", default=None)
    ap.add_argument("--sigma-max", type=float, default=10.0)
    ap.add_argument("--offsets", default=None, help="nos_offsets.py JSON: per-survey dx/dy (m) applied to the positions")
    a = ap.parse_args()
    years = {}
    if a.surveys:
        import json
        for f_ in json.load(open(a.surveys)).get("features", []):
            at = f_["attributes"]; years[at["SURVEY_ID"]] = at.get("SURVEY_YEAR")
    offs = {}
    if a.offsets:
        import json
        offs = {k: (v["dx"], v["dy"], v["method"]) for k, v in json.load(open(a.offsets))["surveys"].items() if v.get("dx") is not None}
    os.makedirs(a.out, exist_ok=True)
    groups = {}   # (y0, y1) -> rows
    summary = []
    for f in sorted(glob.glob(os.path.join(a.raw, "*.xyz.gz"))):
        sid = os.path.basename(f).split(".")[0]
        h = os.path.join(a.raw, f"{sid}_h93.htm")
        meta = report(h) if os.path.exists(h) else dict(year=None, scale="", units="", method="", datum="?")
        if years.get(sid): meta["year"] = int(years[sid])
        if not meta["year"]:
            print(f"{sid}: no survey year -- skipped", file=sys.stderr); continue
        if meta["datum"] != "MLLW":
            print(f"{sid}: vertical datum not MLLW in the report -- skipped", file=sys.stderr); continue
        lat, lon, dep = [], [], []
        with gzip.open(f, "rt") as fh:
            rd = csv.DictReader(fh)
            for r in rd:
                try:
                    if int(r["active"]) != 1: continue
                    lat.append(float(r["lat"])); lon.append(float(r["long"])); dep.append(float(r["depth"]))
                except (ValueError, KeyError):
                    continue
        if not lat:
            continue
        x, y = transform("EPSG:4326", f"EPSG:{a.epsg}", lon, lat)
        off = offs.get(f"nos_{sid.lower()}")
        if off:
            x = [v + off[0] for v in x]; y = [v + off[1] for v in y]
        sz, sh = sigma_for(meta)
        zs = -np.asarray(dep) + a.mllw_shift
        sig = gradient_sigma(x, y, zs, sz, sh, cap=a.sigma_max)
        yr = meta["year"] or 0
        key = (1915, 1931) if yr < 1940 else (1977, 1984) if yr < 2000 else (2000, 2099)
        rows = groups.setdefault(key, [])
        for xi, yi, di, zi, si in zip(x, y, dep, zs, sig):
            rows.append((round(xi, 2), round(yi, 2), round(float(zi), 2), round(float(si), 2), round(di, 2), f"nos_{sid.lower()}"))
        summary.append((sid, yr, meta["scale"], meta["units"], meta["method"][:28], float(np.median(sig)), float(np.percentile(sig, 90)), len(lat),
                        ("shifted %+d E %+d N (%s)" % (off[0], off[1], off[2])) if off else "in place"))
    for (y0, y1), rows in sorted(groups.items()):
        p = os.path.join(a.out, f"nos_{y0}_{y1}_{a.date}.csv")
        with open(p, "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["x", "y", "z", "sigma", "depth_m", "survey", "kind", "id", "level_key", "bound"])
            for i, r in enumerate(rows, 1):
                w.writerow([*r, "sounding", i, "", ""])
        print(f"{p}: {len(rows)} soundings")
    for s in summary:
        print("  %s %s 1:%s  %-18s %-28s sigma p50 %.1f p90 %.1f  n=%d  %s" % s)


if __name__ == "__main__":
    main()
