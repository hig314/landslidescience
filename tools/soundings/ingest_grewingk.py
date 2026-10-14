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
    (not used). One bad row (#VALUE!) is dropped. Ed's email of 2017-09-19
    (fwdgrewingkandemeraldlakebathymetry/ATT00001.txt) dates it: "about 20 hours
    of Lowrance pack raft profiling plus some hand sample depths", cleaned in
    ReefMaster 2.0 -- so a Lowrance sounder, September 2017 (Hig remembered ~2018).
    THINNED to one point per 5 m cell -- the real measurement whose depth is the
    cell's median, never an average -- because the soundings layer loads every
    point into the browser, and 55 k points is ~20 MB. Every original row
    stays in the archived raw files.
  * BERG SPEED OF SOUND (2026-10-14, Hig: assume the sounder used seawater): every
    depth is the delivered one x 1421.6/1500 = 0.9477 (fresh water at ~4 C against
    the 1500 m/s seawater default); depth_orig keeps the delivered feet. Before it, Ed
    read 1.30 m deeper than the 2023 multibeam on its flat floor (~23 m, NMAD 0.06) --
    a ratio of 1.063; after it -0.20 m, i.e. the two agree and the multibeam's datum
    stands. (6-10 m: +0.5 m, NMAD 1.0.)
  * BERG LEVEL: 50.01 m, sigma 0.3 (2026-10-14). First set at 50.5 m from Hig's
    shoreline of September 2017 (50.5m_Shore_points.csv, which reads 50.4 m median
    on the 2021 lidar). Hig (2026-10-14): the track reads a little HIGH where it
    overlaps dry 2021 lidar, drop it wholesale. Measured on the raw track: rows
    whose 2021 lidar is above 49.0 m (dry; the 2021 water is 48.55, 48.7-48.8 near
    the inflows) on ground under 5 deg, 152 rows, depth ~0.6 m, read +0.46 m above
    the lidar (NMAD 0.32) at level 50.5 -- and each of the three overlap areas
    agrees (west end +0.40, outlet +0.33 and +0.67); +0.49 m with the sound-speed
    correction applied. So the level is 50.5 - 0.49.
    Bed change 2017-2021 on those shoals (the outlet especially) would look the same.
  * BERG REJECTED: raw rows 21131-21135 follow a 1.34 km position jump and read
    24-43 m on a shoal the lidar shows dry at 49.9 m (Hig: "one obvious bad
    point at 59.60800, -151.13920"); loaded as flag 'rejected', kept out of thinning.
  * BERG ICE-FACE LINE: the run of the track from 59.60011,-151.07073 to
    59.60665,-151.08217 (raw rows ICE_RUN) ran along the 2017 ice face; Hig
    (2026-10-14): some readings are unrealistically shallow, probably off-nadir
    returns from the face. Every row of it is a LOWER BOUND (depth_bound 'min'),
    thinned on its own so no cell mixes it with ordinary track.
  * BERG vs the 2023 multibeam (grewingk_sonar_2023, 527 overlapping points):
    on the flat floor Ed reads ~3.3 m deeper than the multibeam below 48.55 m
    (NMAD 0.17); the difference grows with depth. First kept as two measures
    (Hig); 2026-10-14 explained and corrected as the sounder's speed of sound
    (BERG SPEED OF SOUND below).
    sigma 1.0 per point (sounder, off-nadir pickup on the steep sides).
  * 2017 LEAD LINE: the last 10 rows of Ed's file are not track but lead-line drops of 21 July 2017
    (Waypoints_21-JUL-17.gpx, names = wraps of a 3.51 ft line); split off as berg_grewingk_2017_hand,
    MAX loaded as a lower bound at the deepest numbered drop.
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
HAND_GPX = Path('/Volumes/Nunatak/bathy_src/berg_grewingk_2017_hand/raw/Waypoints_21-JUL-17.gpx')
CELL_M = 5.0
LEVEL_2021 = 48.55   # grewingk_2021 lidar, flat water surface over 76% of the sonar footprint
BERG_LEVEL = 50.01   # Hig's 2017 shoreline (50.5) less the +0.49 m the (sound-corrected) track reads over dry 2021 lidar
SOUND_C, SOUND_ASSUMED = 1421.6, 1500.0   # m/s: fresh water at ~4 C (Del Grosso); the sounder's seawater default (Hig, 2026-10-14)
SOUND_K = SOUND_C / SOUND_ASSUMED          # every delivered sounder depth x 0.9477
BAD_AT = (59.60800, -151.13920)          # Hig: an obvious bad point; rows within BAD_R_M of it deeper than BAD_MIN_M
BAD_R_M, BAD_MIN_M = 5.0, 5.0
ICE_ENDS = ((59.60011, -151.07073), (59.60665, -151.08217))   # Hig: the run between these hugged the ice face
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
    nhand = hand_tail(utm)
    track = utm[:len(utm) - nhand]
    i0, i1 = sorted(nearest_row(track, *e) for e in ICE_ENDS)
    good, bad, ice, rejected = [], [], [], []
    for i, r in enumerate(track):
        x, y, lon, lat, dm, dft = (num(r.get(k)) for k in ('X', 'Y', 'Lon', 'Lat', 'Depth_m', 'Depth_ft'))
        if None in (x, y, lon, lat, dm, dft):
            bad.append(dict(row=i, values={k: r.get(k) for k in ('Depth_ft', 'Depth_m', 'Elev_m')}))
            continue
        p = (x, y, lon, lat, dm * SOUND_K, dft)
        if dist_m(lat, lon, *BAD_AT) < BAD_R_M and dm > BAD_MIN_M:
            rejected.append((i, p))
        elif i0 <= i <= i1:
            ice.append(p)
        else:
            good.append(p)
    rows = thin(good) + thin(ice, bound='min', note='ice-face line (2017): a lower bound, off-nadir returns from the face '
                                                    'read shallow (Hig 2026-10-14); ')
    for i, p in rejected:
        rows.append(dict(lon='%.7f' % p[2], lat='%.7f' % p[3], observed_at='', depth_m='%.3f' % p[4],
                         depth_orig='%g' % p[5], depth_unit='ft', level_key='grewingk', sigma_m='', flag='rejected',
                         flag_reason='raw row %d: after a 1.34 km position jump, %.1f m on a shoal the 2021 lidar shows '
                                     'dry (Hig: obvious bad point)' % (i, p[4]), name='', depth_bound='', notes=''))
    keep = archive(arch, slug, [BERG, SHORE, SRC / 'Ed_Hig_Depths.csv'])
    lv = level('grewingk', BERG_LEVEL, 0.3, '2017-09-18',
               "Tied to the 2021 lidar (2026-10-14, Hig): where the track overlaps dry 2021 ground (lidar > 49.0 m, "
               "slope < 5 deg, 152 raw rows in three areas) it read +0.49 m above the lidar (depths sound-corrected) at "
               "the 50.5 m of Hig's September 2017 shoreline (50.5m_Shore_points.csv), so 50.5 - 0.49. Ed's survey was under way "
               "by 19 September 2017.", rows)
    survey = dict(
        slug=slug, title='Grewingk Lake depths (Ed Berg, September 2017)',
        collector='Ed Berg', collected_from=None, collected_to='2017-09-07',
        method='sounder', instrument='Lowrance sounder with GPS on a pack raft (model not recorded); cleaned in ReefMaster 2.0',
        depth_unit='ft', sigma_m=1.0, horizontal_m=5.0, licence='', public=True,
        citation='Berg, E., Grewingk Lake depth soundings, September 2017 (unpublished field data, compiled by B. Higman).',
        raw_path=str(keep),
        notes='Dense sounder track over Grewingk Lake, thinned for display to one measured point per 5 m cell. '
              'Depths corrected for the speed of sound: x 1421.6/1500 (cold fresh water against the seawater default '
              'the sounder is assumed to have used; Hig, 2026-10-14) -- after which it agrees with the 2023 multibeam '
              '(grewingk_sonar_2023) on its flat floor to 0.2 m, where it had read 1.3 m (6 %) deep. Level tied to the '
              '2021 lidar where the track crosses ground that was dry in 2021 (50.01 m). The multibeam is preferred '
              'where both exist. '
              'The run along the 2017 ice face is loaded as lower bounds (off-nadir returns from the face read '
              'shallow), and one mislocated burst near 59.6080,-151.1392 as rejected. Surveyed by September 19, '
              '2017 (Ed\'s email of that date: ~20 hours of Lowrance pack-raft profiling, cleaned in ReefMaster 2.0).',
        levels=[lv],
        ingest=dict(script='tools/soundings/ingest_grewingk.py', run=dt.datetime.now().isoformat(timespec='seconds'),
                    files=[BERG.name], points_in=len(utm) - nhand, points_valid=len(good) + len(ice), dropped=bad,
                    rejected_rows=[i for i, _ in rejected], ice_face_rows=[i0, i1], ice_face_points=len(ice),
                    level_tie='+0.49 m (n 152, sound-corrected depths) over dry 2021 lidar at 50.5 m -> 50.01 m',
                    sound_speed=dict(factor=round(SOUND_K, 5), true_m_s=SOUND_C, assumed_m_s=SOUND_ASSUMED,
                                     why='Hig 2026-10-14: assume the sounder used seawater; fresh water ~4 C'),
                    hand_rows_split_off=dict(rows=nhand, package=slug + '_hand'),
                    thinned=dict(cell_m=CELL_M, rule='one real point per cell, the one nearest the cell median depth; '
                                 'the ice-face run thinned on its own', cells=len(rows) - len(rejected)),
                    not_used='Elev_m (an assumed 47.0 m level minus depth)', units='depth_m = delivered metres (feet x 0.3048) x SOUND_K; depth_orig = delivered feet'))
    write(outdir / slug, survey, rows)
    berg_hand(outdir, keep, utm[len(utm) - nhand:], len(utm) - nhand)


def dist_m(lat, lon, lat0, lon0):
    return math.hypot((lat - lat0) * 111320.0, (lon - lon0) * 111320.0 * math.cos(math.radians(lat0)))


def nearest_row(rows, lat, lon):
    """Index of the row nearest (lat, lon): where Hig's coordinates for a run's ends fall on the track."""
    best = min(((dist_m(num(r['Lat']), num(r['Lon']), lat, lon), i) for i, r in enumerate(rows)
                if num(r.get('Lat')) is not None and num(r.get('Lon')) is not None))
    assert best[0] < 10, 'no track row within 10 m of %s,%s' % (lat, lon)
    return best[1]


def thin(pts, bound='', note=''):
    """One real point per CELL_M cell, the one nearest the cell's median depth (never an average)."""
    cells = {}
    for p in pts:
        cells.setdefault((math.floor(p[0] / CELL_M), math.floor(p[1] / CELL_M)), []).append(p)
    rows = []
    for (cx, cy), cp in sorted(cells.items()):
        ds = sorted(p[4] for p in cp)
        med = ds[len(ds) // 2]
        p = min(cp, key=lambda q: abs(q[4] - med))
        rows.append(dict(lon='%.7f' % p[2], lat='%.7f' % p[3], observed_at='', depth_m='%.3f' % p[4],
                         depth_orig='%g' % p[5], depth_unit='ft', level_key='grewingk', sigma_m='', flag='',
                         flag_reason='', name='', depth_bound=bound,
                         notes=note + ('%d points in its 5 m cell, depth %.1f-%.1f m' % (len(cp), ds[0], ds[-1])
                                       if len(cp) > 1 else '')))
    return rows


def hand_tail(utm, gap_m=15.0):
    """Rows at the END of Ed's file that are not on a track: each more than gap_m from the row
    before it.  The track steps 0.5 m (99th pct 0.7 m); the tail is 10 points 47-1,189 m apart."""
    n = 0
    for i in range(len(utm) - 1, 0, -1):
        a, b = utm[i], utm[i - 1]
        if math.hypot(float(a['X']) - float(b['X']), float(a['Y']) - float(b['Y'])) <= gap_m:
            break
        n += 1
    return n


def wpt_times(gpx):
    """{(lat5, lon5): ISO time} from a GPX's waypoints (Hig's Garmin export)."""
    import xml.etree.ElementTree as ET
    r = ET.parse(gpx).getroot(); ns = r.tag.split('}')[0] + '}' if r.tag.startswith('{') else ''
    return {(round(float(w.get('lat')), 5), round(float(w.get('lon')), 5)): (w.findtext(ns + 'time') or '').strip()
            for w in r.iter(ns + 'wpt')}


def berg_hand(outdir, keep, tail, first_row):
    """The non-track tail of Ed's file: lead-line drops of 21 July 2017. Hig (2026-10-13): the line is counted
    in WRAPS of 3.51 ft, and the waypoint names in Waypoints_21-JUL-17.gpx (Hig's GPS archive, matched to
    0.0 m) are wrap counts -- so Ed's feet column (wraps x 3.51) and its metres are the correct depths.
    The last drop, MAX, ran out of line: loaded as a LOWER BOUND at the deepest numbered drop (78 wraps,
    83.45 m), Hig's call. sigma 2 % of depth (Hig)."""
    slug = 'berg_grewingk_2017_hand'
    times = wpt_times(HAND_GPX) if HAND_GPX.exists() else {}
    rows, deepest = [], None
    for k, r in enumerate(tail):
        dm, dft = num(r.get('Depth_m')), num(r.get('Depth_ft'))
        if dm is not None and (deepest is None or dm > deepest[0]):
            deepest = (dm, dft)
    for k, r in enumerate(tail):
        dm, dft = num(r.get('Depth_m')), num(r.get('Depth_ft'))
        bound = ''
        if dm is None and (r.get('Depth_ft') or '').strip().upper() == 'MAX':
            dm, dft, bound = deepest[0], deepest[1], 'min'
        if dm is None:
            continue
        lat, lon = float(r['Lat']), float(r['Lon'])
        when = times.get((round(lat, 5), round(lon, 5)), '')
        rows.append(dict(lon='%.7f' % lon, lat='%.7f' % lat, observed_at=when, depth_m='%.3f' % dm,
                         depth_orig='%g' % dft, depth_unit='ft', level_key='grewingk',
                         sigma_m='%.2f' % max(0.2, 0.02 * dm), flag='', flag_reason='', name='MAX' if bound else '',
                         depth_bound=bound,
                         notes=('line ran out (MAX); bed deeper than the deepest numbered drop, %.2f m' % dm) if bound else
                               '%g wraps of 3.51 ft' % round(dft / 3.51)))
    lv = level('grewingk', 50.5, 1.0, '2017-09-18',
               "Hig's Grewingk shoreline of September 2017 (50.5m_Shore_points.csv), which reads 50.4 m median "
               "on the 2021 lidar; the 21 July 2017 stage itself is unknown.", rows)
    survey = dict(
        slug=slug, title='Grewingk Lake lead-line depths (Higman, 21 July 2017)',
        collector='Bretwood Higman', collected_from='2017-07-21', collected_to='2017-07-21',
        method='lead_line', instrument='lead line counted in wraps of 3.51 ft; handheld GPS',
        depth_unit='ft', sigma_m=1.0, horizontal_m=5.0, licence='', public=True,
        citation='Higman, B., 2017, Grewingk Lake lead-line soundings, 21 July 2017 (unpublished field data).',
        raw_path=str(keep),
        notes='Ten lead-line drops of 21 July 2017 in the central and eastern lake, appended after the sounder track in '
              'Ed_Hig_Depths_UTM5 and matched exactly to Waypoints_21-JUL-17.gpx in Hig\'s GPS archive, whose names '
              'are wrap counts of a line wrapped at 3.51 ft (Hig): depth = wraps x 3.51 ft, 16-83 m. The last drop, '
              'MAX, ran out of line and is a lower bound at the deepest numbered drop (83.45 m). Error about 2% of '
              'depth. Hig\'s measurements (Hig, 2026-10-13), compiled into Ed Berg\'s sounder file.',
        levels=[lv],
        ingest=dict(script='tools/soundings/ingest_grewingk.py', run=dt.datetime.now().isoformat(timespec='seconds'),
                    files=[BERG.name, HAND_GPX.name], rows=[first_row, first_row + len(tail) - 1], soundings=len(rows),
                    lower_bounds=sum(1 for r in rows if r['depth_bound']), times_from=HAND_GPX.name,
                    depth='wraps x 3.51 ft (Ed\'s Depth_ft); GPX names are the wrap counts', sigma='max(0.2 m, 2% of depth)'))
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
        licence='', public=True,
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


# Hig's 2026 plumb-line drops, given in conversation 2026-10-13 (lat, lon, depth m), taken 22 September 2026.
HIG_2026 = [(59.60402, -151.06686, 149.0), (59.60329, -151.06729, 164.5), (59.60233, -151.06720, 164.0),
            (59.60171, -151.06662, 138.0)]


def higman_2026(outdir, arch):
    """Four lead-line (plumb) depths of 2026 at the east end, near the ice face, incl. the deepest so far
    (164.5 m), 22 September 2026. sigma = 2 % of depth (Hig). Level is the 2021 lidar water (48.55 m) at
    sigma 1.0 -- the 2026 stage is unknown, as for 2019."""
    slug = 'higman_grewingk_2026'
    rows = [dict(lon='%.5f' % lon, lat='%.5f' % lat, observed_at='2026-09-22', depth_m='%.1f' % d, depth_orig='%g' % d,
                 depth_unit='m', level_key='grewingk_2026', sigma_m='%.2f' % max(0.2, 0.02 * d), flag='',
                 flag_reason='', name='', depth_bound='', notes='') for lat, lon, d in HIG_2026]
    lv = level('grewingk_2026', LEVEL_2021, 1.0, '2021-10-12',
               'grewingk_2021 lidar water surface (flat at 48.55 m, 12 October 2021); the 22 September 2026 stage is unknown', rows)
    survey = dict(
        slug=slug, title='Grewingk Lake lead-line depths, east end (Higman, 2026)',
        collector='Bretwood Higman', collected_from='2026-09-22', collected_to='2026-09-22',
        method='lead_line', instrument='plumb line; handheld GPS', depth_unit='m', sigma_m=0.5, horizontal_m=5.0,
        licence='', public=True,
        citation='Higman, B., 2026, Grewingk Lake lead-line soundings (unpublished field data).',
        raw_path='', notes='Four plumb-line depths near the ice face, 138-164.5 m: the deepest yet measured in the lake. '
              'Given by Hig in conversation 2026-10-13 (no file); error about 2% of depth (Hig). Taken 22 September 2026 (time of day not recorded).',
        levels=[lv],
        ingest=dict(script='tools/soundings/ingest_grewingk.py', run=dt.datetime.now().isoformat(timespec='seconds'),
                    source='typed by Hig 2026-10-13', soundings=len(rows), sigma='max(0.2 m, 2% of depth)'))
    write(outdir / slug, survey, rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True, help='parent directory; one package per survey is written inside')
    ap.add_argument('--archive', default='/Volumes/Nunatak/bathy_src')
    a = ap.parse_args()
    berg(Path(a.out), a.archive)
    higman(Path(a.out), a.archive)
    higman_2026(Path(a.out), a.archive)


if __name__ == '__main__':
    main()
