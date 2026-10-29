#!/usr/bin/env python3
"""Ingest: Hig's lead-line depths in three ice-marginal lake groups -- Grand Plateau (2021, 2024),
Petrof and Gore (2022), Malaspina (2016). Three surveys, three packages, all public (Hig, 2026-10-13:
"none of this needs to be gated").

    PATH=/opt/homebrew/bin:$PATH /opt/anaconda3/bin/python3 tools/soundings/ingest_higman_glacial_lakes.py --out data/soundings
    docker exec landslidescience-web-1 python manage.py load_soundings data/soundings/<slug>

Judgement calls, each recorded in the package's `ingest` block:

  * GRAND PLATEAU (260112_GP_depths.zip, 77 points). `raw` is the line reading; `depth (m)` is Hig's
    post-correction: in 2021 the line was read in wraps and depth = 0.927 x raw (fit: 0.9274, residual
    < 0.5 m from rounding); in 2024 raw is metres. Loaded: `depth (m)`. ">130" (2021, four drops) is
    130 WRAPS -> a lower bound at 130 x 0.9274 = 120.6 m (Hig); ">275" (2024, two) is metres -> 275 m.
    Two "Island" points at depth 0 are shoreline. Times from the file.
  * PETROF / GORE (Petrof_Gore_Depths.shp, 60 points, Name = depth in metres, half metres written "4. 5").
    The file has no times: each point takes the time of the explore.gpx InReach waypoint at its
    position (within 3 m); 31 May and 3 June 2022. Three GPX depths absent from the file ("38", "40",
    "13") are left out -- the file is the curated set. One point has no name: not loaded.
  * MALASPINA (Malaspina Depths (m).kml, 36 points, 2016; Name = metres, description = feet). "0"
    points are shoreline. No times. Method assumed lead line like the others.
  * sigma = 2 % of depth, floor 0.2 m (Hig's figure for his lead line). LEVELS are UNKNOWN (NULL):
    the points show on the map but carry no elevation until a water level is set -- one level per
    lake cluster (points within 1.5 km chained) and year, so one edit fixes a lake.
"""
import argparse, csv, datetime as dt, io, json, math, re, shutil, subprocess, zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

OGR = '/opt/homebrew/bin/ogr2ogr'
GP_ZIP = Path('/Volumes/Nunatak/Other/Glaciology/Grand_Plateau/Shapefiles/260112_GP_depths.zip')
PG_SHP = Path('/Volumes/Powder/Alaska GIS/Bathy/Petrof_Gore_Depths.shp')
MAL_KML = Path('/Volumes/Nunatak/Other/Glaciology/Malaspina/Malaspina Depths (m).kml')
GPX = Path('/Volumes/Nunatak/bathy_src/_incoming_explore_gpx/explore.gpx')
WRAP_2021 = 0.9274
COLS = ['lon', 'lat', 'observed_at', 'depth_m', 'depth_orig', 'depth_unit', 'level_key', 'sigma_m',
        'flag', 'flag_reason', 'name', 'notes', 'depth_bound']
COLLECTOR = 'Bretwood Higman'
KNOWN_LEVELS = {   # level_key: (level_m NAVD88, sigma_m, source)
    'pg_2022_a': (8.42, 1.0, "3DEP 1/3 arc-second hydroflattened lake surface (flat-patch median), Petrof Lake; 2022 level unknown"),
    'pg_2022_b': (1.59, 1.0, "3DEP 1/3 arc-second hydroflattened lake surface (flat-patch median), Gore Lake; 2022 level unknown"),
}


def ogr_rows(src):
    out = subprocess.run([OGR, '-f', 'CSV', '/vsistdout/', str(src), '-t_srs', 'EPSG:4326', '-lco', 'GEOMETRY=AS_XY'],
                         capture_output=True, text=True, check=True).stdout
    return list(csv.DictReader(io.StringIO(out)))


def num(v):
    try:
        return float(re.sub(r'\s+', '', str(v)).replace(',', '.'))
    except (TypeError, ValueError):
        return None


def sig(d):
    return '%.2f' % max(0.2, 0.02 * d)


def dist(a, b):
    return math.hypot((a[0] - b[0]) * 111200, (a[1] - b[1]) * 111200 * math.cos(math.radians(a[0])))


def clusters(pts, link_m=1500.0):
    """Single-linkage groups of (lat, lon) -> list of group index per point."""
    g = list(range(len(pts)))
    def find(i):
        while g[i] != i:
            g[i] = g[g[i]]; i = g[i]
        return i
    for i in range(len(pts)):
        for j in range(i):
            if dist(pts[i], pts[j]) < link_m:
                g[find(i)] = find(j)
    roots = {}
    return [roots.setdefault(find(i), len(roots)) for i in range(len(pts))]


def assign_levels(rows, prefix, water_body, source):
    """One NULL level per (year, lake cluster); sets each row's level_key, returns the levels."""
    pts = [(float(r['lat']), float(r['lon'])) for r in rows]
    cl = clusters(pts)
    groups = {}
    for r, c in zip(rows, cl):
        key = '%s_%s_%s' % (prefix, (r['observed_at'] or r.get('_year', ''))[:4] or 'undated', chr(97 + c))
        r['level_key'] = key
        groups.setdefault(key, []).append(r)
    for r in rows:
        r.pop('_year', None)
    return [dict(key=k, water_body='%s (lake %s)' % (water_body, k[-1]), level_m=None, datum='NAVD88', sigma_m=1.0,
                 observed_on=None, source=source,
                 lon=round(sum(float(r['lon']) for r in v) / len(v), 5), lat=round(sum(float(r['lat']) for r in v) / len(v), 5))
            for k, v in sorted(groups.items())]


def archive(root, slug, files):
    keep = Path(root) / slug / 'raw'
    if Path(root).exists():
        keep.mkdir(parents=True, exist_ok=True)
        for f in files:
            for side in f.parent.glob(f.stem + '.*'):
                if not (keep / side.name).exists():
                    shutil.copy2(side, keep / side.name)
    return keep


def write(out, survey, rows):
    out.mkdir(parents=True, exist_ok=True)
    with open(out / 'points.csv', 'w', newline='') as fh:
        w = csv.DictWriter(fh, COLS); w.writeheader(); w.writerows(rows)
    (out / 'survey.json').write_text(json.dumps(survey, indent=1))
    print(f"{survey['slug']}: {len(rows)} soundings, {len(survey['levels'])} lake levels (unknown) -> {out}")


def row(lat, lon, when, d, orig, unit, bound='', name='', notes=''):
    return dict(lon='%.6f' % lon, lat='%.6f' % lat, observed_at=when, depth_m='%.2f' % d, depth_orig='%g' % orig,
                depth_unit=unit, level_key='', sigma_m=sig(d), flag='', flag_reason='', name=name, notes=notes,
                depth_bound=bound)


def grand_plateau(outdir, arch, tmp):
    slug = 'higman_grand_plateau_2021_2024'
    d = Path(tmp) / 'gp'; d.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(GP_ZIP) as z:
        for n in z.namelist():
            if n.startswith('depths.') and '/' not in n:
                (d / n).write_bytes(z.read(n))
    rows, skipped = [], []
    for r in ogr_rows(d / 'depths.shp'):
        lat, lon, nm, t = float(r['Y']), float(r['X']), (r.get('name') or '').strip(), (r.get('time') or '').strip()
        when = (t[:19] + 'Z') if t else ''
        dm, raw = num(r.get('depth (m)')), num(r.get('raw'))
        m = re.fullmatch(r'>(\d+(?:\.\d+)?)', nm)
        if m and t.startswith('2021'):
            w = float(m.group(1))
            rows.append(row(lat, lon, when, w * WRAP_2021, w, 'wraps', 'min', nm,
                            'line ran out at %g wraps (x %.4f m = %.1f m)' % (w, WRAP_2021, w * WRAP_2021)))
        elif m:
            rows.append(row(lat, lon, when, float(m.group(1)), float(m.group(1)), 'm', 'min', nm, 'line ran out'))
        elif nm.lower() == 'island':
            x = dict(row(lat, lon, when, 0.0, 0, 'm', '', nm, 'shoreline (island), depth 0')); x['_year'] = '2021'
            rows.append(x)
        elif dm is not None:
            unit = 'wraps' if t.startswith('2021') else 'm'
            rows.append(row(lat, lon, when, dm, raw if raw is not None else dm, unit, '', nm,
                            ('%g wraps, corrected by Hig' % raw) if unit == 'wraps' else ''))
        else:
            skipped.append(dict(r))
    lvs = assign_levels(rows, 'gp', 'Grand Plateau Glacier ice-marginal lakes', 'unknown: no water surface set yet')
    keep = archive(arch, slug, [GP_ZIP])
    survey = dict(slug=slug, title='Grand Plateau lake lead-line depths (Higman, 2021 and 2024)', collector=COLLECTOR,
                  collected_from='2021-06-17', collected_to='2024-07-21', method='lead_line',
                  instrument='lead line (2021 read in wraps of 0.927 m; 2024 in metres); handheld GPS / InReach',
                  depth_unit='m', sigma_m=1.0, horizontal_m=5.0, licence='', public=True,
                  citation='Higman, B., Grand Plateau lake lead-line soundings, 2021 and 2024 (unpublished field data).',
                  raw_path=str(keep),
                  notes='Lead-line depths in the ice-marginal lakes of Grand Plateau Glacier. 2021 depths are line '
                        'readings in wraps x 0.927 (Hig\'s post-correction, the file\'s depth (m)); 2024 in metres. '
                        '">130" (2021) = 130 wraps = a lower bound at 120.6 m; ">275" (2024) = 275 m, lower bound. Two '
                        'island shore points at depth 0. Lake levels unknown.',
                  levels=lvs,
                  ingest=dict(script='tools/soundings/ingest_higman_glacial_lakes.py',
                              run=dt.datetime.now().isoformat(timespec='seconds'), files=[GP_ZIP.name], soundings=len(rows),
                              lower_bounds=sum(1 for r in rows if r['depth_bound']), skipped=skipped,
                              wrap_2021_m=WRAP_2021, sigma='max(0.2 m, 2% of depth)'))
    write(outdir / slug, survey, rows)


def gpx_times():
    r = ET.parse(GPX).getroot(); ns = '{' + r.tag.split('}')[0][1:] + '}'
    return [((float(w.get('lat')), float(w.get('lon'))), (w.findtext(ns + 'time') or '').strip())
            for w in r.iter(ns + 'wpt')]


def petrof_gore(outdir, arch):
    slug = 'higman_petrof_gore_2022'
    gt = gpx_times() if GPX.exists() else []
    rows, skipped, untimed = [], [], 0
    for r in ogr_rows(PG_SHP):
        lat, lon, nm = float(r['Y']), float(r['X']), (r.get('Name') or '').strip()
        d = num(nm)
        if d is None:
            skipped.append(dict(r)); continue
        when = next((t for p, t in gt if dist(p, (lat, lon)) < 3.0), '')
        when = (when[:19] + 'Z') if when else ''
        untimed += not when
        x = row(lat, lon, when, d, d, 'm', '', nm); x['_year'] = '2022'
        rows.append(x)
    lvs = assign_levels(rows, 'pg', 'Petrof / Gore lakes', 'unknown: no water surface set yet')
    # Levels set 2026-10-17 from 3DEP's hydroflattened lake surfaces (the median of the exactly-flat patch
    # around each cluster, NAVD88): no KBay 2023 lidar reaches either lake.  The 2022 lake levels are not
    # known; sigma 1 m.  A measured level (a gauge, a lidar flight) replaces these.
    for lv in lvs:
        k = KNOWN_LEVELS.get(lv['key'])
        if k:
            lv.update(level_m=k[0], sigma_m=k[1], source=k[2], observed_on=None)
    keep = archive(arch, slug, [PG_SHP])
    survey = dict(slug=slug, title='Petrof and Gore lake lead-line depths (Higman, 2022)', collector=COLLECTOR,
                  collected_from='2022-05-31', collected_to='2022-06-03', method='lead_line',
                  instrument='lead line; InReach', depth_unit='m', sigma_m=1.0, horizontal_m=5.0, licence='',
                  public=True,
                  citation='Higman, B., 2022, Petrof and Gore lake lead-line soundings (unpublished field data).',
                  raw_path=str(keep),
                  notes='Lead-line depths in metres. Times taken from Hig\'s InReach waypoints at the same positions '
                        '(the file has none). Three InReach depths not in the file were left out: the file is the '
                        'curated set. Lake levels unknown.',
                  levels=lvs,
                  ingest=dict(script='tools/soundings/ingest_higman_glacial_lakes.py',
                              run=dt.datetime.now().isoformat(timespec='seconds'), files=[PG_SHP.name, GPX.name],
                              soundings=len(rows), untimed=untimed, skipped=skipped,
                              left_out_from_gpx=['38 at 59.39637,-150.81480', '40 at 59.38513,-150.82067',
                                                 '13 at 59.37867,-150.81870'],
                              sigma='max(0.2 m, 2% of depth)', half_metres='"4. 5" read as 4.5'))
    write(outdir / slug, survey, rows)


def malaspina(outdir, arch):
    slug = 'higman_malaspina_2016'
    rows, skipped = [], []
    for r in ogr_rows(MAL_KML):
        lat, lon, nm = float(r['Y']), float(r['X']), (r.get('Name') or '').strip()
        d = num(nm)
        if d is None:
            skipped.append(dict(r)); continue
        x = row(lat, lon, '', d, d, 'm', '', nm, 'shoreline, depth 0' if d == 0 else (r.get('description') or ''))
        x['_year'] = '2016'
        rows.append(x)
    lvs = assign_levels(rows, 'mal', 'Malaspina Glacier ice-marginal lakes', 'unknown: no water surface set yet')
    keep = archive(arch, slug, [MAL_KML])
    survey = dict(slug=slug, title='Malaspina lake depths (Higman, 2016)', collector=COLLECTOR,
                  collected_from='2016-01-01', collected_to='2016-12-31', method='lead_line',
                  instrument='method not recorded in the file (assumed lead line); depths in m, feet alongside',
                  depth_unit='m', sigma_m=1.0, horizontal_m=5.0, licence='', public=True,
                  citation='Higman, B., 2016, Malaspina lake depth soundings (unpublished field data).',
                  raw_path=str(keep),
                  notes='Depths in metres (the KML description gives feet). Points at 0 m are shoreline. Survey day '
                        'not recorded (2016). Lake levels unknown.',
                  levels=lvs,
                  ingest=dict(script='tools/soundings/ingest_higman_glacial_lakes.py',
                              run=dt.datetime.now().isoformat(timespec='seconds'), files=[MAL_KML.name],
                              soundings=len(rows), skipped=skipped, sigma='max(0.2 m, 2% of depth)'))
    write(outdir / slug, survey, rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True)
    ap.add_argument('--archive', default='/Volumes/Nunatak/bathy_src')
    ap.add_argument('--tmp', default='/Volumes/Nunatak/bathy_src/_tmp_ingest')
    a = ap.parse_args()
    grand_plateau(Path(a.out), a.archive, a.tmp)
    petrof_gore(Path(a.out), a.archive)
    malaspina(Path(a.out), a.archive)


if __name__ == '__main__':
    main()
