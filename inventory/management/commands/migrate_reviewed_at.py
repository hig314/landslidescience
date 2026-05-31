"""One-shot schema migration: landslides.reviewed_at.

A nullable `reviewed_at timestamptz` column tracks the per-record
induction status. NULL = pending review (newly inserted, not yet
inducted); non-NULL = inducted (editor reviewed + rules applied).

Existing records at the time of this migration are backfilled to
`reviewed_at = NOW()` so they're considered already-inducted; only
records created via the upload flow after this point start as pending.

Idempotent: ALTER TABLE IF NOT EXISTS, UPDATE only fills NULLs. Safe
to re-run.
"""
from django.core.management.base import BaseCommand


SCHEMA_SQL = """
ALTER TABLE landslides ADD COLUMN IF NOT EXISTS reviewed_at timestamptz;
CREATE INDEX IF NOT EXISTS landslides_pending_review_idx
    ON landslides (created_at DESC)
    WHERE reviewed_at IS NULL;
"""


class Command(BaseCommand):
    help = 'Add the landslides.reviewed_at column and backfill existing rows.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Open a transaction, run DDL + backfill, then ROLLBACK.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn

        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(SCHEMA_SQL)
            self.stdout.write("schema: landslides.reviewed_at + pending-review index ensured.")

            # Backfill: every existing row gets reviewed_at = NOW() so the
            # transition doesn't suddenly mark 1400+ records as pending.
            cur.execute("UPDATE landslides SET reviewed_at = NOW() WHERE reviewed_at IS NULL")
            n_backfilled = cur.rowcount
            self.stdout.write(f"backfilled {n_backfilled} row(s) as reviewed.")

            cur.execute("SELECT COUNT(*) FROM landslides WHERE reviewed_at IS NULL")
            n_pending = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM landslides")
            n_total   = cur.fetchone()[0]

            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING("--dry-run: rolled back."))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS("Committed."))

            self.stdout.write(f"\nlandslides total:    {n_total}")
            self.stdout.write(f"reviewed (inducted): {n_total - n_pending}")
            self.stdout.write(f"pending review:      {n_pending}  (expect 0 right after this run)")
        finally:
            _put_conn(conn)
