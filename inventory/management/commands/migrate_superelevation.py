"""One-shot schema migration: landslides.super_elevated_deposits.

Catastrophic records — the deposit runs up the far/outer side of the valley,
recording flow that was fast enough to bank around the bend. Read as a
velocity indicator, and it sits with the other failure/deposit-character
flags (molards, precursory_headscarp, exclusively_supraglacial) in the
edit form's 'event' group, so it shows for catastrophic records only.

Plain nullable boolean like its siblings: the form's BooleanField(
required=False) writes False on first save, so NULL means "never saved
since the column was added" rather than "observed absent".

Idempotent: ADD COLUMN IF NOT EXISTS. Safe to re-run. Run per-environment
(dev and prod hit separate tethys_db instances).
"""
from django.core.management.base import BaseCommand


SCHEMA_SQL = """
ALTER TABLE landslides ADD COLUMN IF NOT EXISTS super_elevated_deposits boolean;
"""


class Command(BaseCommand):
    help = 'Add the landslides.super_elevated_deposits boolean column.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Open a transaction, run DDL, then ROLLBACK.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn

        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(SCHEMA_SQL)
            self.stdout.write('schema: landslides.super_elevated_deposits ensured.')
            cur.execute("SELECT COUNT(*) FILTER (WHERE super_elevated_deposits), "
                        "COUNT(*) FROM landslides")
            n, total = cur.fetchone()
            self.stdout.write(f'  super_elevated_deposits set: {n} of {total}')
            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING('--dry-run: rolled back.'))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS('Committed.'))
        finally:
            _put_conn(conn)
