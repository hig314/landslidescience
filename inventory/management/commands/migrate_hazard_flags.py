"""One-shot schema migration: landslides.tsunamigenic + glacier_contact.

Two editor-set boolean attributes:
  - tsunamigenic    (catastrophic records — the failure generated a tsunami/
                     displacement wave; edit-form 'event' group, so it shows
                     for catastrophic only)
  - glacier_contact (slow records — the mass is in contact with a glacier;
                     edit-form creep group's slow-only set)

Plain nullable booleans like molards/exclusively_supraglacial: the form's
BooleanField(required=False) writes False on first save, so NULL simply
means "never saved since the column was added".

Idempotent: ADD COLUMN IF NOT EXISTS. Safe to re-run.
"""
from django.core.management.base import BaseCommand


SCHEMA_SQL = """
ALTER TABLE landslides ADD COLUMN IF NOT EXISTS tsunamigenic boolean;
ALTER TABLE landslides ADD COLUMN IF NOT EXISTS glacier_contact boolean;
"""


class Command(BaseCommand):
    help = 'Add landslides.tsunamigenic + glacier_contact boolean columns.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Open a transaction, run DDL, then ROLLBACK.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn

        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(SCHEMA_SQL)
            self.stdout.write('schema: landslides.tsunamigenic + glacier_contact ensured.')
            cur.execute("SELECT COUNT(*) FILTER (WHERE tsunamigenic), "
                        "COUNT(*) FILTER (WHERE glacier_contact) FROM landslides")
            t, g = cur.fetchone()
            self.stdout.write(f'  tsunamigenic set: {t} | glacier_contact set: {g}')
            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING('--dry-run: rolled back.'))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS('Committed.'))
        finally:
            _put_conn(conn)
