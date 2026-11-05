#!/usr/bin/env python3
"""Ingest: NOS hydrographic surveys' digital soundings (NCEI GEODAS) and the CRM cell centres, as sounding surveys.

    python3 tools/soundings/ingest_nos_geodas.py \
        --raw /Volumes/Nunatak/lidar_build/bathy_src/nos_soundings/raw \
        --surveys /Volumes/Nunatak/lidar_build/bathy_src/nos_soundings/surveys_layer1.json \
        --crm /Volumes/Nunatak/lidar_build/obs/soundings/crm_500m_pts_20261031.csv \
        --offsets /Volumes/Nunatak/lidar_build/obs/soundings/nos_offsets.json \
        --out data/soundings
    for d in data/soundings/nos_h* data/soundings/crm_southak_500m; do
      docker exec landslidescience-web-1 python manage.py load_soundings $d; done

Why (Hig, 2026-11-01, reviewing the Taylor Bay bathymetry): "expose the data you're using to build the bathymetry
as sounding points -- like we do with lake soundings -- I'd like to understand where the inference is coming from".
The composite's sea bed between the modern surveys is inferred from these points (tools/lidar/topobathy/fill.py), so
they belong in the Soundings layer with the lake soundings, each with the sigma the fill gave it.

One package per NOS survey (slug nos_<id>, e.g. nos_h05101) plus one for the CRM centres, all `dense = true`: the
API serves a dense survey only inside a bbox and the viewer fetches them per view (326 k NOS points + 22 k CRM).

  * The originals are GEODAS xyz.gz files (survey_id, lat, long, depth, quality_code, active; depth in metres,
    positive down, negative = a drying height) and the H93 descriptive report. Only `active = 1` rows are
    soundings of record; `active = 0` are superseded by NOS and are NOT loaded (counted in `ingest`).
  * Depths are MLLW. Every point hangs from one level, `mllw` = NAVD88 -1.38 m at Seldovia (NOAA 9455500), sigma
    0.1 m, so z = -1.38 - depth. Positions are NAD83 of record, taken as WGS84 (sub-metre here).
  * Per-point sigma is the fill's own (tools/lidar/topobathy/nos_soundings_csv.py, imported): a lead line 2.0 m,
    an echo sounder in tenths 0.8 m, plus the horizontal uncertainty (100 / 30 m) times the local bed gradient
    from a plane through the neighbours within 300 m; a point with fewer than 5 neighbours is a lone line and
    takes the 10 m cap. So a popup's "+-10 m" says the fill barely trusted that point.
  * The CRM package is NOT a sounding survey: NCEI's Southern Alaska Coastal Relief Model's 500 m cell centres
    below low tide, away from 3DEP land, sigma 10 m -- the compilation as the shape between soundings, never an
    observation (method "compilation"). Loaded so the layer shows everything the inference drew on.
  * No per-point dates: a survey carries its year (collected_from/to = Jan 1 / Dec 31 of the year).
  * POSITIONS SHIFTED (--offsets, 2026-11-01): NCEI converted the 1915-31 surveys from their "Early Alaska Datums"
    to NAD83 some 400 m E / 320 m N short -- casts on land, bays sounded shallow between misplaced lines (Hig's
    Taylor Bay review).  tools/lidar/topobathy/nos_offsets.py measures one translation per survey against the
    modern lidar's land and the 1977-84 depths; the same JSON moves the composite's points and these packages, so
    the viewer shows the points where the fill used them.  The shift, its method and the untouched original
    position (NCEI's NAD83) are recorded in the survey's title, notes and `ingest`; the raw files are not edited.
"""
import argparse, csv, datetime as dt, glob, gzip, json, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lidar" / "topobathy"))
import nos_soundings_csv as nos          # report(), sigma_for(), gradient_sigma(): the fill's own rules

MLLW_NAVD88 = -1.38
LEVEL = dict(key="mllw", water_body="Kachemak Bay (MLLW)", level_m=MLLW_NAVD88, datum="NAVD88 (GEOID12B)", sigma_m=0.1,
             source="MLLW at Seldovia, NOAA 9455500: NAVD88 - 1.38 m (the station's tidal bench mark datum sheet)")


def survey_meta(sid, raw, attrs):
    h = os.path.join(raw, f"{sid}_h93.htm")
    meta = nos.report(h) if os.path.exists(h) else dict(year=None, scale="", units="", method="", datum="?")
    at = attrs.get(sid, {})
    if at.get("SURVEY_YEAR"):
        meta["year"] = int(at["SURVEY_YEAR"])
    meta["locality"] = ", ".join(s for s in (at.get("SUBLOCALITY"), at.get("LOCALITY")) if s).title()
    meta["url"] = at.get("DOWNLOAD_URL") or f"https://www.ngdc.noaa.gov/nos/{sid}.html"
    return meta


def method_of(meta):
    m = meta["method"].lower()
    return "lead line" if "lead" in m else ("echo sounder" if "echo" in m else (meta["method"] or "sounder"))


def write_package(out, slug, survey, points, ingest):
    d = Path(out) / slug
    d.mkdir(parents=True, exist_ok=True)
    survey = dict(survey, levels=[dict(LEVEL, **survey.pop("_level_pos", {}))], ingest=ingest)
    (d / "survey.json").write_text(json.dumps(survey, indent=1))
    with open(d / "points.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["lon", "lat", "observed_at", "depth_m", "depth_orig", "depth_unit", "level_key", "sigma_m", "flag",
                    "flag_reason", "name", "notes"])
        for lon, lat, dep, sg in points:
            w.writerow(["%.7f" % lon, "%.7f" % lat, "", "%.2f" % dep, "%.2f" % dep, "m", "mllw", "%.2f" % sg, "", "", "", ""])
    return d


def ingest_nos(a, out):
    import numpy as np
    from rasterio.warp import transform
    offs = {}
    if a.offsets:
        offs = {k: v for k, v in json.load(open(a.offsets))["surveys"].items() if v.get("dx") is not None}
    attrs = {}
    if a.surveys:
        for f_ in json.load(open(a.surveys)).get("features", []):
            attrs[f_["attributes"]["SURVEY_ID"]] = f_["attributes"]
    done = []
    for f in sorted(glob.glob(os.path.join(a.raw, "*.xyz.gz"))):
        sid = os.path.basename(f).split(".")[0]
        meta = survey_meta(sid, a.raw, attrs)
        if not meta["year"]:
            print(f"{sid}: no survey year -- skipped", file=sys.stderr); continue
        if meta["datum"] != "MLLW":
            print(f"{sid}: vertical datum not MLLW in the report -- skipped", file=sys.stderr); continue
        lat, lon, dep = [], [], []
        n_inactive = 0; n_bad = 0
        with gzip.open(f, "rt") as fh:
            for r in csv.DictReader(fh):
                try:
                    if int(r["active"]) != 1:
                        n_inactive += 1; continue
                    lat.append(float(r["lat"])); lon.append(float(r["long"])); dep.append(float(r["depth"]))
                except (ValueError, KeyError):
                    n_bad += 1
        if not lat:
            print(f"{sid}: no active soundings -- skipped", file=sys.stderr); continue
        x, y = transform("EPSG:4326", f"EPSG:{a.epsg}", lon, lat)
        slug = f"nos_{sid.lower()}"
        off = offs.get(slug); shift_txt = ""
        if off:
            x = [v + off["dx"] for v in x]; y = [v + off["dy"] for v in y]
            lon, lat = transform(f"EPSG:{a.epsg}", "EPSG:4326", x, y)
            shift_txt = (f" Positions shifted {off['dx']:+d} m E, {off['dy']:+d} m N from NCEI's NAD83 of record ({off['method']}): "
                         f"NCEI's conversion of the survey's Early Alaska Datum fell short by that much, measured against the "
                         f"modern lidar's land and the 1977-84 soundings (tools/lidar/topobathy/nos_offsets.py, 2026-11-01).")
        sz, sh = nos.sigma_for(meta)
        zs = -np.asarray(dep) + MLLW_NAVD88
        sig = nos.gradient_sigma(x, y, zs, sz, sh, cap=a.sigma_max)
        yr = meta["year"]; meth = method_of(meta)
        who = "US Coast and Geodetic Survey" if yr < 1970 else "NOAA National Ocean Service"
        survey = dict(
            slug=slug,
            title=f"NOS {sid} ({yr}), {meta['locality'] or 'Kachemak Bay'}: {meth}" + (f" [shifted {off['dx']:+d} E {off['dy']:+d} N]" if off else ""),
            collector=who, collected_from=f"{yr}-01-01", collected_to=f"{yr}-12-31",
            method=meth, instrument=f"{meta['method'] or meth}; original sounding units {meta['units'] or '?'}; scale 1:{meta['scale'] or '?'}",
            depth_unit="m", sigma_m=sz, horizontal_m=sh,
            licence="US Government work, public domain (NOAA NCEI)",
            citation=f"NOAA NCEI, NOS hydrographic survey {sid} ({yr}), digital soundings (GEODAS), {meta['url']}",
            public=True, dense=True, raw_path=str(Path(a.raw) / f"{sid}.xyz.gz"),
            notes=(f"Hydrographic survey soundings of record (active = 1), depths MLLW in metres as GEODAS delivers them "
                   f"(originally {meta['units'] or '?'}). Each point's sigma is the composite fill's: {sz} m for a {meth}, plus "
                   f"{sh:g} m of position times the local bed gradient (a plane through the neighbours within 300 m); a point with "
                   f"fewer than 5 neighbours is a lone line and takes the {a.sigma_max:g} m cap. These points are what the Kachemak "
                   f"Bay topobathy infers its sea bed from between the modern surveys." + shift_txt),
            _level_pos=dict(lon=round(float(np.mean(lon)), 5), lat=round(float(np.mean(lat)), 5)),
        )
        ingest = dict(script="tools/soundings/ingest_nos_geodas.py", run=dt.datetime.now().isoformat(timespec="seconds"),
                      files=[os.path.basename(f), f"{sid}_h93.htm"], report=meta, soundings=len(lat),
                      not_loaded=dict(superseded_active_0=n_inactive, unparsable=n_bad),
                      sigma=dict(depth_m=sz, horizontal_m=sh, cap_m=a.sigma_max, p50=round(float(np.median(sig)), 2),
                                 p90=round(float(np.percentile(sig, 90)), 2), rule="nos_soundings_csv.gradient_sigma"),
                      position_shift=(dict(dx_m=off["dx"], dy_m=off["dy"], epsg=a.epsg, method=off["method"], evidence=dict(land=off.get("land"), depth=off.get("depth")),
                                           why="NCEI's Early Alaska Datum -> NAD83 conversion short; measured by nos_offsets.py 2026-11-01",
                                           original="NCEI NAD83 of record, in the raw xyz.gz") if off else None))
        write_package(out, slug, survey, zip(lon, lat, dep, sig), ingest)
        done.append((slug, yr, meth, len(lat), n_inactive, float(np.median(sig))))
        print(f"{slug}: {yr} {meth:12s} {len(lat):7d} loaded, {n_inactive:6d} superseded; sigma p50 {np.median(sig):.1f}")
    return done


def ingest_crm(a, out):
    from rasterio.warp import transform
    xs, ys, deps = [], [], []
    for r in csv.DictReader(open(a.crm)):
        xs.append(float(r["x"])); ys.append(float(r["y"])); deps.append(float(r["depth_m"]))
    lon, lat = transform(f"EPSG:{a.epsg}", "EPSG:4326", xs, ys)
    survey = dict(
        slug="crm_southak_500m",
        title="NOAA CRM Southern Alaska, 500 m cell centres (a compilation, not soundings)",
        collector="NOAA NCEI", collected_from="2010-01-01", collected_to="2010-12-31",
        method="compilation", instrument="Coastal Relief Model grid cell centres below low tide, away from 3DEP land",
        depth_unit="m", sigma_m=10.0, horizontal_m=250.0,
        licence="US Government work, public domain (NOAA NCEI)",
        citation="NOAA NCEI, Southern Alaska Coastal Relief Model (CRM), gridded bathymetry compiled from NOS soundings and other sources",
        public=True, dense=True, raw_path=a.crm,
        notes=("Not a survey: the compilation grid's 500 m cell centres where it lies below low tide (MLLW) and 3DEP shows no land, "
               "at sigma 10 m. The topobathy fill takes them as the shape of the bed between soundings where nothing else "
               "reaches, never as an observation. Shown so the Soundings layer carries everything the inference drew on."),
        _level_pos=dict(lon=round(sum(lon) / len(lon), 5), lat=round(sum(lat) / len(lat), 5)),
    )
    ingest = dict(script="tools/soundings/ingest_nos_geodas.py", run=dt.datetime.now().isoformat(timespec="seconds"),
                  files=[os.path.basename(a.crm)], points=len(xs),
                  rule="crm_6334_500m_v2.tif cells with z < -1.38 m and 3DEP <= 0.5 m, sigma 10 (the 2026-10-31 points file)")
    write_package(out, "crm_southak_500m", survey, zip(lon, lat, deps, [10.0] * len(xs)), ingest)
    print(f"crm_southak_500m: {len(xs)} cell centres")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--raw", required=True, help="GEODAS xyz.gz + _h93.htm files")
    ap.add_argument("--surveys", default=None, help="nos_hydro_dynamic layer 1 attributes (survey year, locality)")
    ap.add_argument("--crm", default=None, help="the CRM 500 m centres points CSV (x, y, depth_m in --epsg)")
    ap.add_argument("--out", required=True, help="data/soundings/ (one package dir per survey)")
    ap.add_argument("--epsg", type=int, default=6334)
    ap.add_argument("--sigma-max", type=float, default=10.0)
    ap.add_argument("--offsets", default=None, help="nos_offsets.py JSON: per-survey horizontal shift applied to the positions")
    a = ap.parse_args()
    done = ingest_nos(a, a.out)
    if a.crm:
        ingest_crm(a, a.out)
    print(f"{len(done)} NOS packages, {sum(d[3] for d in done)} soundings")


if __name__ == "__main__":
    main()
