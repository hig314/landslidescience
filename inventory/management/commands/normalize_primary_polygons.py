"""Sweep: re-assert the polygon is_primary convention across the inventory.

Convention (manifest `polygon_conventions.is_primary`, enforced by
`derived.normalize_primary`): slow → body primary; catastrophic → exactly one
SOURCE primary, deposits never primary; catastrophic with no source → no
primary (centroid falls back to deposit by role order).

Historic entry paths could violate this (draw-created records carried no
primary at all, so review-form radios sometimes crowned a deposit). All
automatic entry paths now normalize on write; this command fixes the backlog.

Idempotent. Non-deprecated records only (deprecated rows keep their history).
Changed records get the rule cascade re-run (centroid may move to the source).
After a live run, restart the web container so the in-memory feature caches
drop the stale centroids.

  python manage.py normalize_primary_polygons --dry-run
  python manage.py normalize_primary_polygons
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Re-assert the polygon is_primary convention on all active records.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change, then ROLLBACK.')

    def handle(self, *args, **opts):
        from inventory import derived
        from inventory.views import _get_conn, _put_conn

        conn = _get_conn()
        changed = []
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT l.id, l.unique_name FROM landslides l
                JOIN landslide_polygons lp ON lp.landslide_id = l.id
                WHERE l.deprecated_at IS NULL ORDER BY l.id""")
            rows = cur.fetchall()
            for ls_id, name in rows:
                if derived.normalize_primary(cur, ls_id):
                    derived.apply_rules_for_landslide(cur, ls_id)
                    changed.append((ls_id, name))
                    self.stdout.write(f'  #{ls_id} {name}: primary renormalized')
            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING('--dry-run: rolled back.'))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS('Committed.'))
            self.stdout.write(f'\n{len(changed)} of {len(rows)} records changed.')
            if changed and not opts['dry_run']:
                self.stdout.write('Restart the web container to clear cached features.')
        finally:
            _put_conn(conn)
