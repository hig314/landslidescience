"""One-shot schema migration: landslides.flagged + flag_reason.

Editor-only review flag. `flagged` marks a landslide that needs an editor's
attention (e.g. a name that doesn't follow the disambiguation standard, or a
likely duplicate); `flag_reason` is a short human note (set by the
flag_name_issues scan or by hand). Both are editor-facing metadata — not shown
on public surfaces; the map's "Flagged" filter is editor-gated.

  - flagged       boolean  DEFAULT false   (NULL treated as false)
  - flag_reason   text

Idempotent: ADD COLUMN / CREATE INDEX IF NOT EXISTS. Safe to re-run.
"""
from django.core.management.base import BaseCommand


SCHEMA_SQL = """
ALTER TABLE landslides ADD COLUMN IF NOT EXISTS flagged boolean DEFAULT false;
ALTER TABLE landslides ADD COLUMN IF NOT EXISTS flag_reason text;
CREATE INDEX IF NOT EXISTS landslides_flagged_idx ON landslides (flagged) WHERE flagged;
"""


class Command(BaseCommand):
    help = 'Add landslides.flagged + flag_reason (editor review flag).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Open a transaction, run DDL, then ROLLBACK.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn

        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(SCHEMA_SQL)
            self.stdout.write("schema: landslides.flagged + flag_reason + index ensured.")
            cur.execute("SELECT COUNT(*) FROM landslides WHERE flagged")
            n = cur.fetchone()[0]
            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING("--dry-run: rolled back."))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS("Committed."))
            self.stdout.write(f"\nflagged rows: {n}")
        finally:
            _put_conn(conn)
