"""One-shot schema migration: landslides.its_live_creep.

A per-source creep-evidence flag beside planet_labs_creep: the motion shows
in the ITS_LIVE feature-tracking velocity fields. It is an input to the
creep_behavior rule (derived.compute_creep_behavior), where it counts as
'Obvious creep'.

  - its_live_creep   boolean, nullable, no default

Deliberately the same shape as planet_labs_creep and the InSAR flags
(nullable, no default), so NULL keeps meaning "not assessed" rather than
"assessed and absent", and so the edit form, the explorer and the GeoJSON
round-trip treat it exactly as they treat its neighbours -- all three
discover columns from the database, none needs telling.

RUN THIS BEFORE (or immediately after) deploying the code that names the
column: the rule cascade SELECTs every rule input by name, so until the
column exists a review-save or a rule-apply fails on creep_behavior.

Idempotent: ADD COLUMN IF NOT EXISTS. Safe to re-run.
"""
from django.core.management.base import BaseCommand


SCHEMA_SQL = """
ALTER TABLE landslides ADD COLUMN IF NOT EXISTS its_live_creep boolean;
"""


class Command(BaseCommand):
    help = 'Add landslides.its_live_creep (ITS_LIVE creep-evidence flag).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Open a transaction, run DDL, then ROLLBACK.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn

        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(SCHEMA_SQL)
            self.stdout.write("schema: landslides.its_live_creep ensured.")
            cur.execute("SELECT COUNT(*) FROM landslides WHERE its_live_creep")
            n = cur.fetchone()[0]
            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING("--dry-run: rolled back."))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS("Committed."))
            self.stdout.write(f"\nits_live_creep set on: {n}")
        finally:
            _put_conn(conn)
