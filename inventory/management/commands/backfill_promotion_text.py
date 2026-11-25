"""Rewrite the provenance sentence on records promoted from an outside
inventory, to the current rule (their id, and their comment / citation where
they recorded one); see PROMOTION RULES in inventory/external.py.

Dry-run by default: prints, per record, what would change. --apply writes it
all in one transaction. Run once per environment (dev and prod are separate
databases); re-running is harmless, a record already in the current wording is
reported unchanged.

Reads landslide_external_links (run migrate_external_links first). Two kinds
of record are repaired on the way:

  * linked with an id the mirror does not hold. Three DGGS promotions carried
    the OBJECTID (1893) instead of their points_id; an id that matches a
    mirrored OBJECTID is re-linked with that record's own id.

  * carrying a source's "Noted in ..." sentence but no link to that source
    (drawn as an ordinary record, the sentence typed by hand). Hig, 2026-11-24: where the text says
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
            if not X.links_ready(cur):
                raise CommandError('run manage.py migrate_external_links first')
            cur.execute("""SELECT id, unique_name, ongoing_work, centroid_lon, centroid_lat
                             FROM landslides WHERE deprecated_at IS NULL ORDER BY id""")
            records = cur.fetchall()
            cur.execute(f'SELECT landslide_id, source, ext_id FROM {X.LINKS} ORDER BY linked_at')
            linked = {}
            for lid, sid, eid in cur.fetchall():
                linked.setdefault(lid, []).append((sid, eid))
            for lid, name, ow, lon, lat in records:
                if o['only'] and lid not in o['only']:
                    continue
                todo = []          # (sid, layer, eid, note, old link to replace or None)
                for sid, eid in linked.get(lid, []):
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
                        todo.append((sid, layer, fixed,
                                     f'id {eid} is their OBJECTID; re-linked as {fixed}', eid))
                    else:
                        todo.append((sid, layer, eid, '', None))
                # A source named in the text but not linked: the record was drawn
                # as an ordinary one and the sentence typed by hand.
                have = {sid for sid, _e in linked.get(lid, [])}
                for sid in X.SOURCES:
                    if sid in have or not any(p.search(ow or '') for p in X._text_patterns(sid)):
                        continue
                    layer = X.SOURCES[sid]['primary_layer']
                    if lid in pairs:
                        todo.append((sid, layer, pairs[lid], 'unlinked; paired by hand', None))
                        continue
                    if lon is None:
                        self.stdout.write(self.style.WARNING(
                            f'#{lid} {name}: says {sid} but has no centroid; skipped'))
                        continue
                    eid, dist = self._nearest(cur, sid, layer, lon, lat)
                    if eid is None or dist > o['max_distance']:
                        self.stdout.write(self.style.WARNING(
                            f'#{lid} {name}: says {sid}, nearest record {eid} is {dist:.0f} m '
                            f'away (> {o["max_distance"]:.0f}); skipped, use --pair'))
                        continue
                    todo.append((sid, layer, eid, f'unlinked; nearest {sid} record {eid} at {dist:.0f} m', None))
                if not todo:
                    continue
                cur.execute('SAVEPOINT r')
                cur.execute('SELECT ongoing_work, noted_by FROM landslides WHERE id = %s', (lid,))
                ow0, nb0 = cur.fetchone()
                notes, ok = [], True
                for sid, layer, eid, note, old in todo:
                    if old is not None:
                        cur.execute(f'DELETE FROM {X.LINKS} WHERE landslide_id = %s AND source = %s '
                                    f'AND ext_id = %s', (lid, sid, old))
                    try:
                        X.apply_promotion(cur, lid, sid, layer, eid, fresh=False, who='backfill')
                    except X.PromotionError as e:
                        self.stdout.write(self.style.WARNING(f'#{lid} {name}: {e}; skipped'))
                        ok = False
                        break
                    if note:
                        notes.append(note)
                if not ok:
                    cur.execute('ROLLBACK TO SAVEPOINT r')
                    continue
                # Backfill never changes noted_by (apply_promotion may fill a blank one).
                cur.execute('UPDATE landslides SET noted_by = %s WHERE id = %s', (nb0, lid))
                cur.execute('SELECT ongoing_work FROM landslides WHERE id = %s', (lid,))
                ow1 = cur.fetchone()[0]
                if (ow0 or '') == (ow1 or '') and not notes:
                    cur.execute('ROLLBACK TO SAVEPOINT r')
                    self.stdout.write(f'#{lid} {name}: unchanged')
                    continue
                changed += 1
                self.stdout.write(self.style.SUCCESS(f'#{lid} {name}') +
                                  (f'  [{"; ".join(notes)}]' if notes else ''))
                self.stdout.write(f'    was: {ow0 or ""}')
                self.stdout.write(f'    now: {ow1}')
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
