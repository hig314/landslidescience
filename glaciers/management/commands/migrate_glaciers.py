"""Schema for glacier identity and mirrored extent sources.

    python manage.py migrate_glaciers            # apply
    python manage.py migrate_glaciers --dry-run  # print the SQL

Idempotent (CREATE ... IF NOT EXISTS throughout). Run once per environment,
right after the deploy that ships it: dev and production are separate
databases. The tables and why they are shaped as they are: glaciers/sources.py.
"""
from django.core.management.base import BaseCommand

from glaciers import network, sources


class Command(BaseCommand):
    help = 'Create the glacier identity and extent-source tables'

    def add_arguments(self, p):
        p.add_argument('--dry-run', action='store_true')

    def handle(self, *a, **o):
        if o['dry_run']:
            self.stdout.write(sources.SCHEMA_SQL)
            self.stdout.write(network.SCHEMA_SQL)
            return
        from inventory.views import _get_conn, _put_conn
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sources.SCHEMA_SQL)
                cur.execute(network.SCHEMA_SQL)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            _put_conn(conn)
        self.stdout.write(self.style.SUCCESS('glacier tables ready'))
