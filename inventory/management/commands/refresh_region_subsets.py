"""Recompute membership for every (unfrozen) region subset — the global
safety net behind the per-record cascade hook. Idempotent; prints the full
gained/lost diff per subset.

  python manage.py refresh_region_subsets --dry-run
  python manage.py refresh_region_subsets [--slug kim]
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Recompute membership for all (or one) region subsets.'

    def add_arguments(self, parser):
        parser.add_argument('--slug', help='Only this region subset.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report diffs, then ROLLBACK.')

    def handle(self, *args, **opts):
        from inventory import regions
        from inventory.views import _get_conn, _put_conn, _invalidate

        conn = _get_conn()
        changed = 0
        try:
            cur = conn.cursor()
            sql = ("SELECT id, slug FROM subsets WHERE kind = 'region' "
                   "AND region_geom IS NOT NULL")
            params = ()
            if opts['slug']:
                sql += " AND slug = %s"
                params = (opts['slug'],)
            cur.execute(sql + " ORDER BY slug", params)
            targets = cur.fetchall()
            if opts['slug'] and not targets:
                raise CommandError(f'No region subset {opts["slug"]!r} with a polygon.')

            frozen = regions.frozen_subset_ids(cur)
            for sid, slug in targets:
                if sid in frozen:
                    self.stdout.write(f'{slug}: frozen by a snapshot — skipped.')
                    continue
                diff = regions.recompute_subset(cur, sid)
                self.stdout.write(f"{slug}: +{len(diff['added'])} / -{len(diff['removed'])}")
                for i, name in diff['added']:
                    self.stdout.write(f'  + #{i} {name}')
                for i, name in diff['removed']:
                    self.stdout.write(f'  - #{i} {name}')
                changed += len(diff['added']) + len(diff['removed'])

            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING('--dry-run: rolled back.'))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS('Committed.'))
        finally:
            _put_conn(conn)
        self.stdout.write(f'\n{changed} membership changes across {len(targets)} region subset(s).')
        if changed and not opts['dry_run']:
            _invalidate('features', 'home_counts', 'timed_events',
                        'timeline_events', 'slug_map', 'slug_for_id')
            self.stdout.write('Restart the web container so cached subset counts refresh.')