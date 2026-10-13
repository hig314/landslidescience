#!/usr/bin/env python3
"""Ingest: Nick Riordan's lake depths, Kachemak Bay State Park, 18-21 July 2018.

    python3 tools/soundings/ingest_riordan_2018.py \
        --raw "~/Documents/Outreach/KBSP_Map/Field data/180801 Nick lakedepths" \
        --out data/soundings/riordan_2018_kbsp_lakes
    docker exec landslidescience-web-1 python manage.py load_soundings data/soundings/riordan_2018_kbsp_lakes

Writes a load_soundings package (survey.json + points.csv) and copies the
untouched originals to /Volumes/Nunatak/bathy_src/<slug>/raw/ (never edited).
Standard library only. The judgement calls live here, each recorded in the
package's `ingest` block so the database says what was done:

  * The originals are three KMZs of GPS waypoints whose NAME is the depth in
    FEET from a handheld sounder (converted to metres; the foot value is kept).
  * Zero-padded three-digit names (005, 008, ...) are the GPS's own waypoint
    numbers, not depths; other names (PASS, ...) are route waypoints. Neither
    is a sounding; both are listed, not loaded.
  * Upper Hazel "854" sits between 76 and 96 on its line: a typo (85 or 84?).
    Loaded, flagged rejected -- the record of what was written survives.
  * Each sounding hangs from its lake's level. The 2018 stage is unknown; the
    levels are the KBay 2023 lidar water surface (reclass DTM, 10th percentile
    at the points), NAVD88 (GEOID12B), sigma 0.3 m for the five-year gap.
  * sigma 1.0 m per sounding (Hig, 2026-10-12: "don't take these as absolute
    truth"): the sounder, a 3-5 m GPS position on a steep shore, the boat's drift.
"""
import argparse, csv, datetime as dt, json, math, re, shutil, zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

SLUG = 'riordan_2018_kbsp_lakes'
KML = '{http://www.opengis.net/kml/2.2}'
FT = 0.3048
# Water bodies: label point (the mean of its soundings), level from the KBay 2023 lidar water surface.
LEVELS = [
    dict(key='upper_hazel', water_body='Upper Hazel Lake', level_m=528.57, lon=-151.18570, lat=59.43824),
    dict(key='lower_hazel', water_body='Lower Hazel Lake', level_m=90.23, lon=-151.22970, lat=59.48557),
    dict(key='lake_66m', water_body='Lake south of China Poot Lake (66 m)', level_m=66.94, lon=-151.20078, lat=59.52686),
    dict(key='china_poot', water_body='China Poot (Leisure) Lake', level_m=50.46, lon=-151.20257, lat=59.54018),
]
for l in LEVELS:
    l.update(datum='NAVD88 (GEOID12B)', sigma_m=0.3, observed_on='2023-08-01',
             source='KBay 2023 lidar water surface (reclass DTM, 10th percentile at the soundings); '
                    'the 2018 stage is unknown')
REACH_M = 1500          # a run farther than this from every label point belongs to no lake: an error
LINK_M = 150            # soundings closer than this are one run (one lake)
REJECT = {('upper_hazel', '854'): 'between 76 and 96 on its line: a typo (85 or 84?)'}


def dist_m(lon1, lat1, lon2, lat2):
    k = math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lon1 - lon2) * 111320 * k, (lat1 - lat2) * 110570)


def placemarks(kmz):
    with zipfile.ZipFile(kmz) as z:
        root = ET.fromstring(z.read([n for n in z.namelist() if n.endswith('.kml')][0]))
    for pm in root.iter(KML + 'Placemark'):
        c = pm.find('.//' + KML + 'coordinates')
        if c is None:
            continue
        lon, lat = [float(v) for v in c.text.strip().split(',')[:2]]
        when = pm.find('.//' + KML + 'when')
        yield dict(name=(pm.findtext(KML + 'name') or '').strip(), desc=(pm.findtext(KML + 'description') or '').strip(),
                   lon=lon, lat=lat, when=when.text.strip() if when is not None else '', file=kmz.name)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--raw', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--archive', default='/Volumes/Nunatak/bathy_src')
    a = ap.parse_args()
    raw = Path(a.raw).expanduser(); out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    kmzs = sorted(raw.glob('*.kmz'))
    keep = Path(a.archive) / SLUG / 'raw'
    if Path(a.archive).parent.exists():
        keep.mkdir(parents=True, exist_ok=True)
        for k in kmzs:
            if not (keep / k.name).exists():
                shutil.copy2(k, keep / k.name)
    rows, not_depth, no_lake, depths = [], [], [], []
    for k in kmzs:
        for p in placemarks(k):
            if not re.fullmatch(r'[1-9]\d{0,2}|0', p['name']):
                not_depth.append(dict(name=p['name'], desc=p['desc'], file=p['file'], when=p['when'],
                                      why='waypoint number (zero-padded)' if re.fullmatch(r'0\d{2}', p['name'])
                                      else 'route waypoint, not a depth'))
            else:
                depths.append(p)
    # Lake by RUN, not by point: soundings within LINK_M of each other are one lake (single
    # linkage), and the run goes to the label point nearest its mean. Nearest-label per point put
    # China Poot's south arm on the lake beside it, 16 m higher.
    parent = list(range(len(depths)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i in range(len(depths)):
        for j in range(i + 1, len(depths)):
            if dist_m(depths[i]['lon'], depths[i]['lat'], depths[j]['lon'], depths[j]['lat']) < LINK_M:
                parent[root(i)] = root(j)
    runs = {}
    for i, p in enumerate(depths):
        runs.setdefault(root(i), []).append(p)
    for run in runs.values():
        mlon = sum(p['lon'] for p in run) / len(run); mlat = sum(p['lat'] for p in run) / len(run)
        dm, lk = min((dist_m(mlon, mlat, l['lon'], l['lat']), l['key']) for l in LEVELS)
        for p in run:
            if dm > REACH_M:
                no_lake.append(dict(name=p['name'], lon=p['lon'], lat=p['lat'], file=p['file']))
                continue
            ft = float(p['name'])
            why = REJECT.get((lk, p['name']), '')
            rows.append(dict(lon='%.7f' % p['lon'], lat='%.7f' % p['lat'], observed_at=p['when'],
                             depth_m='%.3f' % (ft * FT), depth_orig='%g' % ft, depth_unit='ft', level_key=lk,
                             sigma_m='', flag='rejected' if why else '', flag_reason=why, name=p['name'],
                             notes=p['desc']))
    rows.sort(key=lambda r: r['observed_at'])
    cols = ['lon', 'lat', 'observed_at', 'depth_m', 'depth_orig', 'depth_unit', 'level_key', 'sigma_m',
            'flag', 'flag_reason', 'name', 'notes']
    with open(out / 'points.csv', 'w', newline='') as fh:
        w = csv.DictWriter(fh, cols); w.writeheader(); w.writerows(rows)
    per = {l['key']: sum(r['level_key'] == l['key'] for r in rows) for l in LEVELS}
    survey = dict(
        slug=SLUG, title='Lake depths, Kachemak Bay State Park (Riordan 2018)',
        collector='Nick Riordan', collected_from='2018-07-18', collected_to='2018-07-21',
        method='sounder', instrument='handheld depth sounder; GPS waypoints named with the depth in feet',
        depth_unit='ft', sigma_m=1.0, horizontal_m=5.0, licence='Public (Hig, 2026-10-12)',
        citation='Riordan, N., 2018, lake depth soundings, Upper and Lower Hazel, China Poot and a neighbouring lake, '
                 'Kachemak Bay State Park, 18-21 July 2018 (unpublished field data).',
        public=True, raw_path=str(keep),
        notes='Depth soundings from a packraft along lines across each lake. Waypoint positions 3-5 m; depths in '
              'feet from a handheld sounder. Levels for the elevations are the 2023 lidar water surface.',
        levels=LEVELS,
        ingest=dict(script='tools/soundings/ingest_riordan_2018.py', run=dt.datetime.now().isoformat(timespec='seconds'),
                    files=[k.name for k in kmzs], soundings=len(rows), per_lake=per,
                    rejected=[dict(name=r['name'], level_key=r['level_key'], why=r['flag_reason']) for r in rows if r['flag']],
                    not_loaded=dict(not_depth=not_depth, outside_every_lake=no_lake),
                    units='depth_m = feet x 0.3048'))
    (out / 'survey.json').write_text(json.dumps(survey, indent=1))
    print(f'{SLUG}: {len(rows)} soundings {per}, {sum(1 for r in rows if r["flag"])} rejected; '
          f'not loaded: {len(not_depth)} non-depth waypoints, {len(no_lake)} outside every lake -> {out}')


if __name__ == '__main__':
    main()
