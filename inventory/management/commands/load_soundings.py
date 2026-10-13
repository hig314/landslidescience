"""Load one sounding survey from a package directory.

    python manage.py load_soundings data/soundings/<slug>/            # new survey
    python manage.py load_soundings data/soundings/<slug>/ --replace  # reload it
    python manage.py load_soundings data/soundings/<slug>/ --dry-run

A PACKAGE is what a per-source ingest script (tools/soundings/ingest_*.py, run
on the Mac against the untouched originals) writes:

  survey.json     the survey record (columns of sounding_surveys), its
                  `levels` (water bodies: key, water_body, level_m, datum,
                  sigma_m, observed_on, source, lon, lat) and `ingest` (what
                  the script did: unit changes, rows dropped and why, counts).
  points.csv      lon, lat, observed_at, depth_m, depth_orig, depth_unit,
                  level_key, sigma_m, flag, flag_reason, name, notes
                  (empty sigma_m = the survey's default)
  contours.geojson  optional: LineString/MultiLineString features with
                  properties depth_m, depth_orig, depth_unit, level_key,
                  sigma_m, flag, flag_reason, notes

The ingest script owns judgement (which rows are depths, which are typos);
this command only loads, in one transaction, and refuses a slug that exists
unless --replace. A replace drops the survey's rows and loads the package
again: fine while packages are the record, NOT once points are edited in the
browser -- it then refuses if any row was edited after the last load.
"""
import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

SURVEY_COLS = ['slug', 'title', 'collector', 'collected_from', 'collected_to', 'method', 'instrument',
               'depth_unit', 'sigma_m', 'horizontal_m', 'licence', 'citation', 'public', 'raw_path', 'notes']
# What the table's NOT NULL columns take when survey.json leaves them out.
DEFAULTS = dict(collector='', method='sounder', instrument='', depth_unit='m', sigma_m=1.0, licence='',
                citation='', public=False, raw_path='', notes='')


def _f(v):
    return None if v in (None, '') else float(v)


class Command(BaseCommand):
    help = 'Load a sounding survey package (survey.json + points.csv [+ contours.geojson])'

    def add_arguments(self, parser):
        parser.add_argument('package')
        parser.add_argument('--replace', action='store_true')
        parser.add_argument('--force', action='store_true', help='with --replace: even over browser edits')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--user', default='', help='recorded as created_by')

    def handle(self, *args, **o):
        from inventory.views import _get_conn, _put_conn
        pkg = Path(o['package'])
        sv = json.loads((pkg / 'survey.json').read_text())
        missing = [k for k in ('slug', 'title') if not sv.get(k)]
        if missing:
            raise CommandError('survey.json lacks ' + ', '.join(missing))
        pts = list(csv.DictReader(open(pkg / 'points.csv'))) if (pkg / 'points.csv').exists() else []
        cons = json.loads((pkg / 'contours.geojson').read_text())['features'] if (pkg / 'contours.geojson').exists() else []
        levels = sv.get('levels') or []
        keys = {l['key'] for l in levels}
        bad = sorted({p['level_key'] for p in pts if p.get('level_key') and p['level_key'] not in keys})
        if bad:
            raise CommandError('points name levels the survey does not define: ' + ', '.join(bad))
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute('SELECT id, updated_at FROM sounding_surveys WHERE slug = %s', [sv['slug']])
            row = cur.fetchone()
            if row:
                if not o['replace']:
                    raise CommandError('survey %s exists (id %d); --replace to reload it' % (sv['slug'], row[0]))
                cur.execute('SELECT count(*) FROM soundings WHERE survey_id = %s AND updated_at > %s',
                            [row[0], row[1]])
                edited = cur.fetchone()[0]
                if edited and not o['force']:
                    raise CommandError('%d points were edited after the last load; --force discards that' % edited)
                cur.execute('DELETE FROM sounding_surveys WHERE id = %s', [row[0]])
            vals = [sv.get(c) if sv.get(c) is not None else DEFAULTS.get(c) for c in SURVEY_COLS]
            cur.execute('INSERT INTO sounding_surveys (' + ', '.join(SURVEY_COLS) + ', ingest, created_by) VALUES (' +
                        ', '.join(['%s'] * len(SURVEY_COLS)) + ', %s, %s) RETURNING id',
                        vals + [json.dumps(sv.get('ingest')) if sv.get('ingest') is not None else None, o['user']])
            sid = cur.fetchone()[0]
            lid = {}
            for l in levels:
                geom = ('ST_SetSRID(ST_MakePoint(%s, %s), 4326)' if l.get('lon') is not None else 'NULL')
                gp = [l['lon'], l['lat']] if l.get('lon') is not None else []
                cur.execute('INSERT INTO sounding_levels (survey_id, key, water_body, level_m, datum, sigma_m, '
                            'observed_on, source, geom) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, ' + geom + ') RETURNING id',
                            [sid, l['key'], l.get('water_body', ''), l.get('level_m'), l.get('datum', 'NAVD88'),
                             l.get('sigma_m', 0.5), l.get('observed_on'), l.get('source', '')] + gp)
                lid[l['key']] = cur.fetchone()[0]
            for p in pts:
                cur.execute("""
                    INSERT INTO soundings (survey_id, level_id, geom, observed_at, depth_m, depth_orig, depth_unit,
                                           sigma_m, flag, flag_reason, name, notes, created_by)
                    VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, [sid, lid.get(p.get('level_key')), float(p['lon']), float(p['lat']), p.get('observed_at') or None,
                      float(p['depth_m']), _f(p.get('depth_orig')), p.get('depth_unit') or 'm', _f(p.get('sigma_m')),
                      p.get('flag', ''), p.get('flag_reason', ''), p.get('name', ''), p.get('notes', ''), o['user']])
            for f in cons:
                q = f['properties']
                cur.execute("""
                    INSERT INTO sounding_contours (survey_id, level_id, geom, depth_m, depth_orig, depth_unit, sigma_m,
                                                   flag, flag_reason, notes, created_by)
                    VALUES (%s, %s, ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), 2)),
                            %s, %s, %s, %s, %s, %s, %s, %s)
                """, [sid, lid.get(q.get('level_key')), json.dumps(f['geometry']), float(q['depth_m']),
                      _f(q.get('depth_orig')), q.get('depth_unit') or 'm', _f(q.get('sigma_m')),
                      q.get('flag', ''), q.get('flag_reason', ''), q.get('notes', ''), o['user']])
            cur.execute("SELECT count(*), count(*) FILTER (WHERE flag = 'rejected'), count(z) FROM soundings_z WHERE survey_id = %s", [sid])
            n, nrej, nz = cur.fetchone()
            msg = ('%s: survey id %d, %d levels, %d soundings (%d rejected, %d with an elevation), %d contours'
                   % (sv['slug'], sid, len(levels), n, nrej, nz, len(cons)))
            if o['dry_run']:
                conn.rollback()
                self.stdout.write('DRY RUN (rolled back) ' + msg)
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS(msg))
        except Exception:
            conn.rollback()
            raise
        finally:
            _put_conn(conn)
