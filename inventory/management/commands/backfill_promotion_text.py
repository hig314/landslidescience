"""Rewrite the provenance sentence on records promoted from an outside
inventory, to the current rule (their id, and their comment / citation where
they recorded one); see PROMOTION RULES in inventory/external.py.

Dry-run by default: prints, per record, what would change. --apply writes it
all in one transaction. Run once per environment (dev and prod are separate
databases); re-running is harmless, a record already in the current wording is
reported unchanged.

Two kinds of record are repaired on the way:

  * stamped with an id the mirror does not hold. Three DGGS promotions carry
    the OBJECTID (1893) instead of their points_id; an id that matches a
    mirrored OBJECTID is re-stamped with that record's own id.

  * carrying the "Noted in ..." sentence but no stamp (drawn as an ordinary
    record, the sentence typed by hand). Hig, 2026-11-24: where the text says
    it was promoted, the nearest point of that source is the match. Matched
    within --max-distance of our centroid, otherwise reported and left alone.
    --pair ID:EXT_ID overrides the nearest-point guess for one record.

noted_by is never changed here: on existing records it is whatever the editor
settled on.
"""
from django.core.management.base import BaseCommand, CommandError

from inventory import external as X
from inventory.views import _get_conn, _put_conn


class Command(BaseCommand):
    help = 'Rewrite promoted records\' provenance text to the current rule (dry-run unless --apply)'

    def add_arguments(self, p):
        p.add_argument('--apply', action='store_true', help='write the changes')
        p.add_argument('--only', type=int, nargs='*', help='landslide ids to limit to')
        p.add_argument('--max-distance', type=float, default=3000,
                       help='metres; unstamped records further than this from the '
                            'nearest source point are left alone (default 3000)')
        p.add_argument('--pair', action='append', default=[],
                       help='ID:EXT_ID, the source record for an unstamped landslide')

    def handle(self, *a, **o):
        pairs = {}
        for s in o['pair']:
            try:
                k, v = s.split(':', 1)
                pairs[int(k)] = v
            except ValueError:
                raise CommandError(f'--pair wants ID:EXT_ID, got {s!r}')
        conn = _get_conn()
        changed = 0
        try:
            cur = conn.cursor()
            cur.execute("""SELECT id, unique_name, external_source, external_id, ongoing_work,
                                  centroid_lon, centroid_lat
                             FROM landslides WHERE deprecated_at IS NULL ORDER BY id""")
            rows = cur.fetchall()
            for lid, name, sid, eid, ow, lon, lat in rows:
                if o['only'] and lid not in o['only']:
                    continue
                note = ''
                if sid:
                    if sid not in X.SOURCES:
                        self.stdout.write(f'#{lid} {name}: unknown source {sid!r}, skipped')
                        continue
                    layer = X.SOURCES[sid]['primary_layer']
                    if X.mirrored_row(cur, sid, layer, eid) is None:
                        fixed = self._by_objectid(cur, sid, layer, eid)
                        if not fixed:
                            self.stdout.write(self.style.WARNING(
                                f'#{lid} {name}: {sid} has no record {eid!r}; skipped'))
                            continue
                        note = f'id {eid} is their OBJECTID; re-stamped as {fixed}'
                        eid = fixed
                else:
                    sid = next((s for s in X.SOURCES
                                if any(p.search(ow or '') for p in X._text_patterns(s))), None)
                    if not sid:
                        continue
                    layer = X.SOURCES[sid]['primary_layer']
                    if lid in pairs:
                        eid, note = pairs[lid], 'unstamped; paired by hand'
                    else:
                        if lon is None:
                            self.stdout.write(self.style.WARNING(
                                f'#{lid} {name}: says {sid} but has no centroid; skipped'))
                            continue
                        eid, dist = self._nearest(cur, sid, layer, lon, lat)
                        if eid is None or dist > o['max_distance']:
                            self.stdout.write(self.style.WARNING(
                                f'#{lid} {name}: says {sid}, nearest point {eid} is {dist:.0f} m '
                                f'away (> {o["max_distance"]:.0f}); skipped, use --pair'))
                            continue
                        note = f'unstamped; nearest {sid} point {eid} at {dist:.0f} m'
                cur.execute('SAVEPOINT r')
                try:
                    res = X.apply_promotion(cur, lid, sid, layer, eid, fresh=False)
                except X.PromotionError as e:
                    cur.execute('ROLLBACK TO SAVEPOINT r')
                    self.stdout.write(self.style.WARNING(f'#{lid} {name}: {e}; skipped'))
                    continue
                prev = res['previous']
                # Backfill never changes noted_by (apply_promotion may fill a blank one).
                if (res['noted_by'] or None) != (prev['noted_by'] or None):
                    cur.execute('UPDATE landslides SET noted_by = %s WHERE id = %s',
                                (prev['noted_by'], lid))
                same = (prev['ongoing_work'] or '') == res['ongoing_work'] and \
                       prev['external_source'] == sid and prev['external_id'] == eid
                if same:
                    cur.execute('ROLLBACK TO SAVEPOINT r')
                    self.stdout.write(f'#{lid} {name}: unchanged')
                    continue
                changed += 1
                self.stdout.write(self.style.SUCCESS(f'#{lid} {name}') +
                                  (f'  [{note}]' if note else ''))
                if (prev['external_source'], prev['external_id']) != (sid, eid):
                    self.stdout.write(f'    stamp: {prev["external_source"]}/{prev["external_id"]}'
                                      f' -> {sid}/{eid}')
                self.stdout.write(f'    was: {prev["ongoing_work"] or ""}')
                self.stdout.write(f'    now: {res["ongoing_work"]}')
            if o['apply']:
                conn.commit()
            else:
                conn.rollback()
        finally:
            _put_conn(conn)
        if o['apply'] and changed:
            from inventory.views import _invalidate
            _invalidate()
        self.stdout.write(f'{changed} record(s) {"changed" if o["apply"] else "would change"}'
                          + ('' if o['apply'] else ' (dry run; --apply to write)'))

    def _by_objectid(self, cur, sid, layer, eid):
        if not str(eid).isdigit():
            return None
        t = X.table_name(sid, layer)
        cur.execute("""SELECT 1 FROM information_schema.columns
                        WHERE table_name = %s AND column_name = 'objectid'""", (t,))
        if not cur.fetchone():
            return None
        cur.execute(f'SELECT ext_id FROM {t} WHERE objectid = %s', (int(eid),))
        r = cur.fetchall()
        return r[0][0] if len(r) == 1 else None

    def _nearest(self, cur, sid, layer, lon, lat):
        t = X.table_name(sid, layer)
        cur.execute(f"""
            SELECT ext_id, ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)
              FROM {t} WHERE geom IS NOT NULL
             ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326) LIMIT 1""",
                    (lon, lat, lon, lat))
        r = cur.fetchone()
        return (r[0], r[1]) if r else (None, float('inf'))
