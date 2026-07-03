"""One-shot schema migration: landslides.default_map_view.

Per-landslide curated map view — a URL-hash view-state string
(`map=<zoom>/<lat>/<lon>&base=<id>[&swipe=<id>&sx=<pct>]`, no leading '#')
set by editors from the map's detail panel ("Set default view"). Consumed by
slug deep-links (slug_redirect) and snapshot slug stubs, so a shared landslide
URL opens at the curated framing — including an active wiper comparison —
instead of the generic centroid + zoom-13 view.

  - default_map_view   text

Idempotent: ADD COLUMN IF NOT EXISTS. Safe to re-run.
"""
from django.core.management.base import BaseCommand


SCHEMA_SQL = """
ALTER TABLE landslides ADD COLUMN IF NOT EXISTS default_map_view text;
"""


class Command(BaseCommand):
    help = 'Add landslides.default_map_view (curated per-landslide map view).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Open a transaction, run DDL, then ROLLBACK.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn

        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(SCHEMA_SQL)
            self.stdout.write("schema: landslides.default_map_view ensured.")
            cur.execute("SELECT COUNT(*) FROM landslides "
                        "WHERE default_map_view IS NOT NULL")
            n = cur.fetchone()[0]
            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING("--dry-run: rolled back."))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS("Committed."))
            self.stdout.write(f"\nrows with a default view: {n}")
        finally:
            _put_conn(conn)
