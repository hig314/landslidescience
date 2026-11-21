"""Bring Hig's Sentinel-1 timelapses in from a Drive folder, in two steps.

    manage.py s1_videos resolve <review dir>              (dev: writes <dir>/manifest.json)
    manage.py s1_videos import <manifest> <decisions> [--dry-run]   (prod)

The review dir is what tools/glaciers/s1_videos.py wrote (probe + dates).
RESOLVE names each video's glacier and picks one video per glacier; Hig
checks the result on a review page (one frame a video) and the page's
decisions come back as a JSON file. IMPORT then adds each approved video as
a public `s1` video, anchored on the segment that holds the present ice face,
adopting the glacier's RGI centrelines first if the network has none.

Which glacier (`_place`): a rule in tools/glaciers/s1_sites.json, else a
flow line Hig drew with the site's name, else an RGI or GLIMS glacier name
(the largest piece when several share it; flagged when they are far apart),
else a GNIS glacier's point, else a GNIS lake, stream or summit (the nearest
large glacier, flagged). The rest are left unplaced for Hig.

Which video (`_choose`), within one glacier and subject (the site name less
any trailing number or date): videos without the S1B gap (a jump of a year or
more between consecutive frames), then the latest last frame, then the fewest
missing months. Where every video of a glacier has the gap, the latest is
chosen and flagged.

The anchor is the downstream end of the glacier's main RGI centreline (the
RGI terminus, about 2000), or the downstream end of Hig's named line. The
video lands on the segment of the network holding that point
(videos.resolve).
"""
import csv
import json
import re
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from inventory.views import _get_conn, _put_conn

from ... import network, videos

SITES = Path(settings.BASE_DIR) / 'tools' / 'glaciers' / 's1_sites.json'
GNIS = Path(settings.BASE_DIR) / 'data' / 'glaciers' / 'gnis' / 'Text' / 'DomesticNames_AK.txt'
GAP_MONTHS = 12          # a jump this long between two frames is the S1B gap
LIKELY_GAP_MISSING = 40  # with the gap not read, this many missing months is taken to be it
FAR_KM = 30              # same-named glaciers further apart than this are different glaciers
NEAR_GLACIER_KM = 20     # a GNIS lake or stream finds its glacier within this
MIN_AREA_KM2 = 5         # ... among glaciers at least this big
_TAIL = re.compile(r'(_V\d+|_(?:May|Nov)_?\d{4}|_calving_May\d{4}|_\d{4}|_\d|\d)$')


def norm(s):
    return re.sub(r'[^a-z]', '', (s or '').lower().replace('glacier', ''))


def site_key(site):
    s = site
    while True:
        t = _TAIL.sub('', s)
        if t == s:
            return s
        s = t


def months(label):
    return int(label[:4]) * 12 + int(label[5:7]) - 1


def label_of(m):
    return '%04d-%02d' % (m // 12, m % 12 + 1)


def date_facts(probe, first, last, gap):
    """First and last month (the labels of the first and last frames), the
    months missing between them, and the S1B gap from <id>_gap.json (the
    months skipped between the last frame before 2022 and the next)."""
    f, l = months(first), months(last)
    span = l - f + 1
    g, at = None, None
    if gap and gap.get('before') and gap.get('after'):
        g, at = months(gap['after']) - months(gap['before']) - 1, (gap['before'], gap['after'])
    elif gap and gap.get('note'):
        g = 0                         # ends before 2022, or starts after: no gap to have
    elif span - probe['frames'] >= LIKELY_GAP_MISSING:
        # not read by seeking yet: this many missing months can only be the
        # outage (scattered skips run to a handful), so count it as the gap
        g, at = span - probe['frames'], None
        gap = dict(gap or {}, note='not read; %d months missing' % (span - probe['frames']))
    return {'first': first, 'last': last, 'span': span, 'missing': span - probe['frames'], 'gap': g, 'gap_at': at,
            'gap_note': (gap or {}).get('note') or (gap or {}).get('error')}


class Command(BaseCommand):
    help = __doc__.split('\n\n')[0]

    def add_arguments(self, p):
        p.add_argument('step', choices=['resolve', 'import'])
        p.add_argument('path')
        p.add_argument('decisions', nargs='?')
        p.add_argument('--dry-run', action='store_true')

    def handle(self, *a, **o):
        if o['step'] == 'resolve':
            self.resolve(Path(o['path']))
        else:
            if not o['decisions']:
                raise CommandError('import needs the manifest and the decisions file')
            self.do_import(Path(o['path']), Path(o['decisions']), o['dry_run'])

    # ------------------------------------------------------------------
    def resolve(self, d):
        rules = json.loads(SITES.read_text())
        listing = json.loads((d / 'listing.json').read_text())
        labels = json.loads((d / 'labels.json').read_text())
        vids = []
        for fid, name in listing['files']:
            p = d / (fid + '.json')
            first, last = labels.get(fid + '_first'), labels.get(fid + '_last')
            if not p.exists() or not first or not last:
                self.stderr.write('not probed or no dates: %s' % name)
                continue
            r = json.loads(p.read_text())
            gp = d / (fid + '_gap.json')
            r.update(date_facts(r, first, last, json.loads(gp.read_text()) if gp.exists() else None))
            r['key'] = site_key(r['site'])
            vids.append(r)
        conn = _get_conn()
        try:
            cur = conn.cursor()
            gnis = self._gnis()
            places = {}
            for k in sorted({v['key'] for v in vids}):
                places[k] = self._place(cur, k, rules, gnis)
            cur.execute("SELECT source_url, id FROM glacier_videos WHERE source_url IS NOT NULL")
            on_prod = {videos.drive_id(u): i for u, i in cur.fetchall() if videos.drive_id(u)}
            conn.rollback()
        finally:
            _put_conn(conn)
        for v in vids:
            pl = places[v['key']]
            v['place'] = pl
            # One pick per glacier AND subject: Portage, Portage_2 and Portage3
            # compete; Malaspina_lake and Oily_Lake, both placed on the
            # Malaspina complex, do not.
            where = pl.get('glacier_id') or ('line:%d' % pl['line_id'] if pl.get('line_id') else 'unplaced')
            v['group'] = where + '|' + norm(v['key'])
            v['existing'] = on_prod.get(v['id'])
        self._choose(vids)
        out = {'folder': listing['folder'], 'videos': sorted(vids, key=lambda v: (v['place'].get('name') or v['key'], v['name']))}
        (d / 'manifest.json').write_text(json.dumps(out, indent=1, default=str))
        n = sum(1 for v in vids if v['choice'] == 'chosen')
        self.stdout.write('%d videos, %d groups, %d chosen, %d unplaced, %d already on the site'
                          % (len(vids), len({v['group'] for v in vids}), n,
                             sum(1 for v in vids if not v['place'].get('glacier_id') and not v['place'].get('line_id')),
                             sum(1 for v in vids if v['existing'])))

    def _gnis(self):
        if not GNIS.exists():
            return {}
        out = {}
        with GNIS.open(encoding='utf-8-sig') as f:
            for r in csv.DictReader(f, delimiter='|'):
                out.setdefault(r['feature_name'].lower(), []).append(
                    (r['feature_class'], float(r['prim_long_dec']), float(r['prim_lat_dec'])))
        return out

    def _glacier_at(self, cur, lon, lat, need_area=0, within_km=2):
        cur.execute("""SELECT id, name, area_km2, ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)
                       FROM glaciers WHERE ST_DWithin(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s) AND area_km2 >= %s
                       ORDER BY 4, area_km2 DESC LIMIT 1""", (lon, lat, lon, lat, within_km / 50.0, need_area))
        r = cur.fetchone()
        if r and r[3] <= within_km * 1000:
            return {'glacier_id': r[0], 'name': r[1], 'area_km2': r[2], 'dist_m': round(r[3])}
        return None

    def _by_name(self, cur, name):
        """RGI glaciers called `name` (RGI name, else a GLIMS outline's name)."""
        n = norm(name)
        if not n:
            return []
        cur.execute("""SELECT id, name, area_km2, cenlon, cenlat FROM glaciers
                       WHERE name <> '' AND regexp_replace(lower(replace(name, 'Glacier', '')), '[^a-z]', '', 'g') = %s""", (n,))
        rows = cur.fetchall()
        if not rows:
            cur.execute("""SELECT g.id, g.name, g.area_km2, g.cenlon, g.cenlat FROM glaciers g
                           WHERE g.id IN (SELECT DISTINCT o.glacier_id FROM glacier_obs o
                                          WHERE o.source = 'glims' AND o.glacier_id IS NOT NULL
                                            AND regexp_replace(lower(replace(o.props->>'glac_name', 'Glacier', '')), '[^a-z]', '', 'g') = %s)""", (n,))
            rows = cur.fetchall()
        return rows

    def _place(self, cur, key, rules, gnis):
        rule = rules['sites'].get(key, {})
        note = rule.get('note', '')
        if rule.get('unplaced'):
            return {'how': 'unplaced', 'note': note or 'no rule for this name'}
        # Hig's own line by name
        lname = rule.get('line') or key.replace('_', ' ')
        cur.execute("""SELECT id, glacier_id, name FROM glacier_lines
                       WHERE kind = 'flowline' AND regexp_replace(lower(replace(name, 'Glacier', '')), '[^a-z]', '', 'g') = %s
                       ORDER BY length_m DESC LIMIT 1""", (norm(lname),))
        r = cur.fetchone()
        if r and rule.get('line'):
            return {'how': 'your line', 'line_id': r[0], 'glacier_id': r[1], 'name': r[2], 'note': note}
        if rule.get('glacier'):
            names = [rule['glacier']]
        elif rule.get('gnis'):
            names = []
        else:
            # the full name first ("East Nunatak", "Lake George"), then with
            # the qualifier words taken out ("Bering East" -> "Bering")
            al = {k.lower(): v for k, v in rules['aliases'].items()}
            quals = {w.lower() for w in rules['qualifiers']}
            words = [al.get(w.lower(), w) for w in re.split(r'[_-]', al.get(key.lower(), key)) if w]
            names = [' '.join(words)]
            core = ' '.join(w for w in words if w.lower() not in quals)
            if core and core != names[0]:
                names.append(core)
        for name in names:
            rows = self._by_name(cur, name)
            if rows:
                rows.sort(key=lambda x: -(x[2] or 0))
                best = rows[0]
                far = [x for x in rows[1:] if x[3] is not None and
                       ((x[3] - best[3]) ** 2 * 0.25 + (x[4] - best[4]) ** 2) ** 0.5 * 111 > FAR_KM]
                how = 'name'
                if far:
                    how = 'name, ambiguous'
                    note = (note + '; ' if note else '') + 'also called %s: %s' % (
                        name, ', '.join('%s (%.0f km², %.2f, %.2f)' % (x[1], x[2] or 0, x[3], x[4]) for x in far[:4]))
                return {'how': how, 'glacier_id': best[0], 'name': best[1] or name, 'area_km2': best[2], 'note': note}
        for name in names:
            for nm in (name.lower(), (name + ' glacier').lower()):
                for cls, lon, lat in gnis.get(nm, []):
                    if cls == 'Glacier':
                        g = self._glacier_at(cur, lon, lat, within_km=3)
                        if g:
                            label = nm.title() if nm.endswith('glacier') else name
                            return dict(g, name=g['name'] or label, how='gnis glacier',
                                        note=note or 'GNIS: %s%s' % (label, ' (RGI calls it %s)' % g['name'] if g['name'] and norm(g['name']) != norm(label) else ''))
        feat = rule.get('gnis')
        if feat:
            for cls, lon, lat in gnis.get(feat.lower(), []):
                g = self._glacier_at(cur, lon, lat, need_area=MIN_AREA_KM2, within_km=NEAR_GLACIER_KM)
                if g:
                    return dict(g, how='near ' + cls.lower(), note=note or 'nearest large glacier to %s' % feat)
        return {'how': 'unplaced', 'note': note or 'no glacier of that name in RGI, GLIMS or GNIS'}

    def _choose(self, vids):
        groups = {}
        for v in vids:
            groups.setdefault(v['group'], []).append(v)
        for g, vs in groups.items():
            def rank(v):
                # known clean, then not known (the seek could not read it), then gapped
                state = 2 if (v['gap'] is not None and v['gap'] >= GAP_MONTHS) else (1 if v['gap'] is None else 0)
                return (state, -months(v['last']), v['missing'])
            vs.sort(key=rank)
            best = vs[0]
            all_gapped = best['gap'] is not None and best['gap'] >= GAP_MONTHS
            for v in vs:
                gapped = v['gap'] is not None and v['gap'] >= GAP_MONTHS
                if v is best:
                    v['choice'] = 'chosen'
                    v['why'] = ('every video of this glacier has the S1B gap; the latest' if all_gapped else
                                ('gap not read' if best['gap'] is None else 'no S1B gap') +
                                ('; the latest of %d' % len(vs) if len(vs) > 1 else ''))
                else:
                    v['choice'] = 'alternative'
                    v['why'] = ('has the S1B gap (%d months, %s to %s)' % (v['gap'], v['gap_at'][0], v['gap_at'][1])
                                if gapped and v['gap_at'] else
                                'ends %s, before %s' % (v['last'], best['last']) if months(v['last']) < months(best['last']) else
                                'more months missing (%d against %d)' % (v['missing'], best['missing']))

    # ------------------------------------------------------------------
    def do_import(self, manifest, decisions, dry):
        man = {v['id']: v for v in json.loads(manifest.read_text())['videos']}
        dec = json.loads(decisions.read_text())
        todo = []
        for fid, d in dec.items():
            if d.get('status') != 'approved':
                continue
            v = man.get(fid)
            if not v:
                raise CommandError('decision for a video not in the manifest: %s' % fid)
            if v.get('existing'):
                self.stdout.write('already on the site (video %s): %s' % (v['existing'], v['name']))
                continue
            gid = d.get('glacier_id') or v['place'].get('glacier_id')
            if not gid and not v['place'].get('line_id'):
                raise CommandError('approved but unplaced: %s' % v['name'])
            todo.append((v, gid))
        need = sum(v['size'] for v, _ in todo)
        free = shutil.disk_usage(videos.VIDEO_DIR.parent).free
        self.stdout.write('%d to import, %.1f GB; %.1f GB free' % (len(todo), need / 1e9, free / 1e9))
        if need > free - 2e9:
            raise CommandError('not enough disk: keep 2 GB free after the import')
        for v, gid in todo:
            self._import_one(v, gid, dry)

    def _anchor(self, cur, v, gid):
        """The point to anchor on: the downstream end of Hig's line, else of
        the glacier's main RGI centreline. Adopts the glacier's centrelines
        when the network has no line of it."""
        lid = v['place'].get('line_id') if not gid or gid == v['place'].get('glacier_id') else None
        if lid:
            cur.execute("SELECT ST_X(p), ST_Y(p) FROM (SELECT ST_EndPoint(ST_GeometryN(geom, 1)) p FROM glacier_lines WHERE id = %s) q", (lid,))
            return cur.fetchone(), 0
        cur.execute("SELECT count(*) FROM glacier_lines WHERE kind = 'flowline' AND glacier_id = %s", (gid,))
        adopted = 0
        if not cur.fetchone()[0]:
            cur.execute('SELECT name FROM glaciers WHERE id = %s', (gid,))
            g = cur.fetchone()
            if not g:
                raise CommandError('no such glacier %s' % gid)
            adopted = network.adopt(cur, gid, g[0], 's1 import')
        cur.execute("""SELECT ST_X(p), ST_Y(p) FROM (SELECT ST_EndPoint(geom) p FROM glacier_centerlines
                       WHERE glacier_id = %s ORDER BY is_main DESC, length_m DESC LIMIT 1) q""", (gid,))
        return cur.fetchone(), adopted

    def _import_one(self, v, gid, dry):
        fetched = None
        conn = _get_conn()
        try:
            cur = conn.cursor()
            pt, adopted = self._anchor(cur, v, gid)
            if not pt:
                raise CommandError('%s: glacier %s has no RGI centreline to anchor on' % (v['name'], gid))
            seg = videos.resolve(cur, [list(pt)])[0]
            if not seg:
                raise CommandError('%s: the anchor %s is on no flow line' % (v['name'], pt))
            cap = '%s · Sentinel-1 %s, monthly' % (v['key'].replace('_', ' ').replace('-', ' and '), v['orbit'].lower())
            url = 'https://drive.google.com/file/d/%s/view' % v['id']
            self.stdout.write('%s → %s (segment of line %d%s), %s to %s%s' % (
                v['name'], v['place'].get('name') or gid, seg['line_id'], ', %d lines adopted' % adopted if adopted else '',
                v['first'], v['last'], ' [dry run]' if dry else ''))
            if dry:
                conn.rollback()
                return
            name = videos.fetch_to_file(videos.DRIVE_GET.format(id=v['id']))
            fetched = videos.VIDEO_DIR / name
            cur.execute("""INSERT INTO glacier_videos (kind, file, caption, date_min, date_max, added_by, source_url)
                           VALUES ('s1', %s, %s, %s, %s, 's1 import', %s) RETURNING id""",
                        (name, cap, v['first'] + '-01', _month_end(v['last']), url))
            vid = cur.fetchone()[0]
            cur.execute("INSERT INTO glacier_video_anchors (video_id, geom) VALUES (%s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
                        (vid, pt[0], pt[1]))
            conn.commit()
            fetched = None
        except Exception:
            conn.rollback()
            if fetched:
                fetched.unlink(missing_ok=True)       # downloaded, but the row did not go in
            raise
        finally:
            _put_conn(conn)


def _month_end(label):
    import calendar
    y, m = int(label[:4]), int(label[5:7])
    return '%04d-%02d-%02d' % (y, m, calendar.monthrange(y, m)[1])
