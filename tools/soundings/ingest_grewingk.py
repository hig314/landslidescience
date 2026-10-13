#!/usr/bin/env python3
"""Ingest: Grewingk Lake depths -- Ed Berg's sounder track (2017 or earlier) and
Hig's lead-line soundings of 2019 and 2021. Two surveys, two packages.

    python3 tools/soundings/ingest_grewingk.py --out data/soundings
    docker exec landslidescience-web-1 python manage.py load_soundings data/soundings/berg_grewingk_2017
    docker exec landslidescience-web-1 python manage.py load_soundings data/soundings/higman_grewingk_2019_2021

Needs /opt/homebrew/bin/ogr2ogr (reads the shapefiles, reprojects to WGS84).
Originals are copied untouched to /Volumes/Nunatak/bathy_src/<slug>/raw/.
The judgement calls, each recorded in the package's `ingest` block:

  * BERG. Ed_Hig_Depths_UTM5.shp: 55,363 points 0.5 m apart along the boat
    track, depth in feet and metres, plus an Elev_m from an ASSUMED 47.0 m level
    (not used). One bad row (#VALUE!) is dropped. The files are dated
    September 2017, so the survey is 2017 or earlier (Hig remembered ~2018).
    THINNED to one point per 5 m cell -- the real measurement whose depth is the
    cell's median, never an average -- because the soundings layer loads every
    point into the browser, and 55 k points is ~20 MB. Every original row
    stays in the archived raw files.
  * BERG LEVEL: 50.5 m, sigma 1.0. Hig's shoreline of September 2017
    (50.5m_Shore_points.csv, same folder) reads 50.4 m median on the 2021 lidar,
    so it is in the same datum and ~2 m above the October 2021 water (48.55 m).
    Hig (2026-10-13): the water may have been ~2 m higher when Ed surveyed.
  * BERG vs the 2023 multibeam (grewingk_sonar_2023, 527 overlapping points):
    on the flat floor Ed reads ~3.3 m deeper than the multibeam below 48.55 m
    (NMAD 0.17); the difference grows with depth. NOT corrected: Hig's call is
    two measures, multibeam preferred. Plausible cause: a low-frequency
    single beam reading through dilated silt the multibeam resolves sharply.
    sigma 1.0 per point (sounder, off-nadir pickup on the steep sides).
  * HIGMAN: Hig_depths/20190712_depths.shp holds TWO field days -- 10 lead-line (plumb)
    soundings on 12 July 2019 and 10 on 30 August 2021, depth in m (and ft),
    times as the GPS wrote them (zone not recorded; taken as UTC). sigma = 2 %
    of depth (Hig), floor 0.2 m. Two 2021 rows have no depth and a name ">119",
    ">120": the line ran out -- loaded as LOWER BOUNDS (depth_bound 'min').
    Levels: the October 2021 lidar water surface, 48.55 m, for both days --
    sigma 0.3 for August 2021, 1.0 for July 2019 (stage unknown).
"""
import argparse, csv, datetime as dt, io, json, math, shutil, subprocess
from pathlib import Path

OGR = '/opt/homebrew/bin/ogr2ogr'
SRC = Path('/Volumes/Nunatak/Landslides/Grewingk')
BERG = SRC / 'Ed_Hig_Depths_UTM5.shp'
HIG = SRC / 'Hig_depths' / '20190712_depths.shp'
SHORE = SRC / '50.5m_Shore_points.csv'
CELL_M = 5.0
LEVEL_2021 = 48.55   # grewingk_2021 lidar, flat water surface over 76% of the sonar footprint
FT = 0.3048
COLS = ['lon', 'lat', 'observed_at', 'depth_m', 'depth_orig', 'depth_unit', 'level_key', 'sigma_m',
        'flag', 'flag_reason', 'name', 'notes', 'depth_bound']


def read(shp, wgs84=False):
    cmd = [OGR, '-f', 'CSV', '/vsistdout/', str(shp), '-lco', 'GEOMETRY=AS_XY']
    if wgs84:
        cmd += ['-t_srs', 'EPSG:4326']
    return list(csv.DictReader(io.StringIO(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def archive(root, slug, files):
    keep = Path(root) / slug / 'raw'
    if Path(root).parent.exists():
        keep.mkdir(parents=True, exist_ok=True)
        for f in files:
            for side in f.parent.glob(f.stem + '.*'):
                if not (keep / side.name).exists():
                    shutil.copy2(side, keep / side.name)
    return keep


def level(key, level_m, sigma, observed_on, source, rows):
    return dict(key=key, water_body='Grewingk Lake', level_m=level_m, datum='NAVD88 (GEOID12B)', sigma_m=sigma,
                observed_on=observed_on, source=source,
                lon=round(sum(float(r['lon']) for r in rows) / len(rows), 5),
                lat=round(sum(float(r['lat']) for r in rows) / len(rows), 5))


def write(out, survey, rows):
    out.mkdir(parents=True, exist_ok=True)
    with open(out / 'points.csv', 'w', newline='') as fh:
        w = csv.DictWriter(fh, COLS); w.writeheader(); w.writerows(rows)
    (out / 'survey.json').write_text(json.dumps(survey, indent=1))
    print(f"{survey['slug']}: {len(rows)} soundings -> {out}")


def berg(outdir, arch):
    slug = 'berg_grewingk_2017'
    utm = read(BERG)                       # X/Y in UTM 5N, Lat/Lon columns alongside
    good, bad = [], []
    for i, r in enumerate(utm):
        x, y, lon, lat, dm, dft = (num(r.get(k)) for k in ('X', 'Y', 'Lon', 'Lat', 'Depth_m', 'Depth_ft'))
        if None in (x, y, lon, lat, dm, dft):
            bad.append(dict(row=i, values={k: r.get(k) for k in ('Depth_ft', 'Depth_m', 'Elev_m')}))
            continue
        good.append((x, y, lon, lat, dm, dft))
    cells = {}
    for p in good:
        cells.setdefault((math.floor(p[0] / CELL_M), math.floor(p[1] / CELL_M)), []).append(p)
    rows = []
    for (cx, cy), pts in sorted(cells.items()):
        ds = sorted(p[4] for p in pts)
        med = ds[len(ds) // 2]
        p = min(pts, key=lambda q: abs(q[4] - med))
        rows.append(dict(lon='%.7f' % p[2], lat='%.7f' % p[3], observed_at='', depth_m='%.3f' % p[4],
                         depth_orig='%g' % p[5], depth_unit='ft', level_key='grewingk', sigma_m='', flag='',
                         flag_reason='', name='', depth_bound='', notes='%d points in its 5 m cell, depth %.1f-%.1f m'
                         % (len(pts), ds[0], ds[-1]) if len(pts) > 1 else ''))
    keep = archive(arch, slug, [BERG, SHORE, SRC / 'Ed_Hig_Depths.csv'])
    lv = level('grewingk', 50.5, 1.0, '2017-09-18',
               "Hig's Grewingk shoreline of September 2017 (50.5m_Shore_points.csv), which reads 50.4 m median "
               "on the 2021 lidar; ~2 m above the 2021 water. Ed's survey day is not known.", rows)
    survey = dict(
        slug=slug, title='Grewingk Lake depths (Ed Berg, 2017 or earlier)',
        collector='Ed Berg', collected_from=None, collected_to=None,
        method='sounder', instrument='boat-mounted single-beam sounder with GPS track (model not recorded)',
        depth_unit='ft', sigma_m=1.0, horizontal_m=5.0, licence='', public=False,
        citation='Berg, E., Grewingk Lake depth soundings, 2017 or earlier (unpublished field data, compiled by B. Higman).',
        raw_path=str(keep),
        notes='Dense sounder track over Grewingk Lake, thinned for display to one measured point per 5 m cell. '
              'Reads deeper than the 2023 multibeam (grewingk_sonar_2023), increasingly with depth (~3.3 m on '
              'the flat floor against the 2021 water level; ~1.3 m against this 50.5 m level): kept as a separate '
              'measure, not corrected; the multibeam is preferred where both exist. Survey date: the files are '
              'from September 2017.',
        levels=[lv],
        ingest=dict(script='tools/soundings/ingest_grewingk.py', run=dt.datetime.now().isoformat(timespec='seconds'),
                    files=[BERG.name], points_in=len(utm), points_valid=len(good), dropped=bad,
                    thinned=dict(cell_m=CELL_M, rule='one real point per cell, the one nearest the cell median depth',
                                 cells=len(rows)),
                    not_used='Elev_m (an assumed 47.0 m level minus depth)', units='depth_m as delivered (= feet x 0.3048)'))
    write(outdir / slug, survey, rows)


def higman(outdir, arch):
    slug = 'higman_grewingk_2019_2021'
    rows, skipped = [], []
    for r in read(HIG, wgs84=True):
        t = (r.get('time') or '').strip()
        when = ''
        for fmt in ('%Y/%m/%d %H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S.%f'):
            try:
                when = dt.datetime.strptime(t, fmt).strftime('%Y-%m-%dT%H:%M:%SZ'); break
            except ValueError:
                pass
        d, bound, name = num(r.get('Depth (m)')), '', (r.get('name') or '').strip()
        if d is None and name.startswith('>') and num(name[1:]) is not None:
            d, bound = num(name[1:]), 'min'
        if d is None or not when:
            skipped.append(dict(r)); continue
        rows.append(dict(lon='%.7f' % float(r['X']), lat='%.7f' % float(r['Y']), observed_at=when,
                         depth_m='%.2f' % d, depth_orig='%g' % d, depth_unit='m',
                         level_key='grewingk_' + when[:4], sigma_m='%.2f' % max(0.2, 0.02 * d), flag='',
                         flag_reason='', name=name, depth_bound=bound,
                         notes=('line ran out (%s as written)' % name) if bound else
                               (('%s ft as written' % r['Depth (ft)']) if r.get('Depth (ft)') else '')))
    keep = archive(arch, slug, [HIG])
    src21 = 'grewingk_2021 lidar water surface (flat at 48.55 m, 12 October 2021)'
    lvs = [level('grewingk_2019', LEVEL_2021, 1.0, '2021-10-12', src21 + '; the 12 July 2019 stage is unknown',
                 [r for r in rows if r['level_key'] == 'grewingk_2019']),
           level('grewingk_2021', LEVEL_2021, 0.3, '2021-10-12', src21 + ', six weeks after the 30 August survey',
                 [r for r in rows if r['level_key'] == 'grewingk_2021'])]
    survey = dict(
        slug=slug, title='Grewingk Lake lead-line depths, east end (Higman, 2019 and 2021)',
        collector='Bretwood Higman', collected_from='2019-07-12', collected_to='2021-08-30',
        method='lead_line', instrument='plumb line; handheld GPS', depth_unit='m', sigma_m=0.5, horizontal_m=5.0,
        licence='', public=False,
        citation='Higman, B., 2019, Grewingk Lake lead-line soundings, 12 July 2019 (unpublished field data).',
        raw_path=str(keep),
        notes='Plumb-line depths in the east end of Grewingk Lake on two days, 12 July 2019 and 30 August 2021; '
              'error about 2% of depth (Hig). Two 2021 drops ran out of line (>119, >120 m): lower bounds. '
              'GPS times taken as UTC (zone not recorded).',
        levels=lvs,
        ingest=dict(script='tools/soundings/ingest_grewingk.py', run=dt.datetime.now().isoformat(timespec='seconds'),
                    files=[HIG.name], soundings=len(rows), lower_bounds=sum(1 for r in rows if r['depth_bound']),
                    skipped=skipped, sigma='max(0.2 m, 2% of depth)'))
    write(outdir / slug, survey, rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True, help='parent directory; one package per survey is written inside')
    ap.add_argument('--archive', default='/Volumes/Nunatak/bathy_src')
    a = ap.parse_args()
    berg(Path(a.out), a.archive)
    higman(Path(a.out), a.archive)


if __name__ == '__main__':
    main()
