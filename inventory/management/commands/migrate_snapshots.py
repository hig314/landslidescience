"""One-shot schema migration: snapshots table.

A snapshot is an immutable, citable, browsable copy of the inventory's
state for a given subset at a given moment. The on-disk bundle lives under
data/snapshots/<slug>/ as a static site; this table holds metadata so
listings, citation, and download links can be built from SQL without
walking the filesystem.

Idempotent: CREATE TABLE IF NOT EXISTS. Safe to re-run.
"""
from django.core.management.base import BaseCommand


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS snapshots (
    id            serial PRIMARY KEY,
    slug          text UNIQUE NOT NULL,
    name          text NOT NULL,
    description   text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    created_by    text,
    subset_id     integer REFERENCES subsets(id),
    n_landslides  integer,
    n_polygons    integer,
    archive_dir   text NOT NULL,
    citation_info text
);
CREATE INDEX IF NOT EXISTS snapshots_subset_idx ON snapshots(subset_id);
"""


class Command(BaseCommand):
    help = 'Create the snapshots table (idempotent).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Open a transaction, run DDL, then ROLLBACK.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn

        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(SCHEMA_SQL)
            cur.execute("SELECT COUNT(*) FROM snapshots")
            n = cur.fetchone()[0]
            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING("--dry-run: rolled back."))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS("Committed."))
            self.stdout.write(f"snapshots rows: {n}")
        finally:
            _put_conn(conn)
