"""One-shot schema migration: landslides.deprecated_at + superseded_by.

Part of the supersede/merge induction flow. When an improved landslide is
uploaded that replaces an existing one, the original is **deprecated** rather
than deleted — retained in the database for provenance but hidden from public
and active surfaces:

  - `deprecated_at timestamptz`  NULL = active; non-NULL = superseded/retired.
  - `superseded_by integer`      id of the record that replaced it (nullable).

No backfill: every existing row stays active (deprecated_at NULL). Idempotent —
ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS. Safe to re-run.
"""
from django.core.management.base import BaseCommand


SCHEMA_SQL = """
ALTER TABLE landslides ADD COLUMN IF NOT EXISTS deprecated_at timestamptz;
ALTER TABLE landslides ADD COLUMN IF NOT EXISTS superseded_by integer;
CREATE INDEX IF NOT EXISTS landslides_deprecated_idx
    ON landslides (deprecated_at)
    WHERE deprecated_at IS NOT NULL;
"""


class Command(BaseCommand):
    help = 'Add landslides.deprecated_at + superseded_by columns (supersede/merge flow).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Open a transaction, run DDL, then ROLLBACK.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn

        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(SCHEMA_SQL)
            self.stdout.write("schema: landslides.deprecated_at + superseded_by + index ensured.")

            cur.execute("SELECT COUNT(*) FROM landslides WHERE deprecated_at IS NOT NULL")
            n_deprecated = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM landslides")
            n_total = cur.fetchone()[0]

            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING("--dry-run: rolled back."))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS("Committed."))

            self.stdout.write(f"\nlandslides total: {n_total}")
            self.stdout.write(f"deprecated:       {n_deprecated}  (expect 0 right after this run)")
        finally:
            _put_conn(conn)
