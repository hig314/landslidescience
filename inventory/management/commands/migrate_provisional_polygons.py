"""One-shot schema migration: provisional_polygons.

Backing store for the main-map "draw a new landslide" tool. Editors draw
polygons on the inventory map and assign each a name + role; finished
components are staged here (server-side, so a reload or crash doesn't lose
work) until the editor commits them. On commit, components are grouped by
`unique_name` into one pending landslide each (via the import synthesize
path) and these rows are cleared.

Columns:
  - editor_id    integer   Django auth_user.id of the drawing editor. A plain
                           integer (FK in spirit only) — auth_user lives in the
                           SQLite DB, so there is no cross-DB foreign key, the
                           same pattern as LandslideEditMeta.landslide_id.
  - unique_name  text      proposed landslide name (groups components)
  - role         text      source | body | deposit
  - geom         geometry(MultiPolygon, 4326)
  - created_at   timestamptz

Per-editor scoped: every query filters editor_id. Idempotent — CREATE TABLE /
INDEX IF NOT EXISTS. Safe to re-run.
"""
from django.core.management.base import BaseCommand


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS provisional_polygons (
    id           bigserial PRIMARY KEY,
    editor_id    integer NOT NULL,
    unique_name  text    NOT NULL,
    role         text    NOT NULL CHECK (role IN ('source', 'body', 'deposit')),
    geom         geometry(MultiPolygon, 4326) NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS provisional_polygons_editor_idx
    ON provisional_polygons (editor_id);
"""


class Command(BaseCommand):
    help = 'Add the provisional_polygons table (main-map draw-new staging).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Open a transaction, run DDL, then ROLLBACK.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn

        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(SCHEMA_SQL)
            self.stdout.write("schema: provisional_polygons + index ensured.")

            cur.execute("SELECT COUNT(*) FROM provisional_polygons")
            n_rows = cur.fetchone()[0]

            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING("--dry-run: rolled back."))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS("Committed."))

            self.stdout.write(f"\nprovisional rows: {n_rows}  (expect 0 on first run)")
        finally:
            _put_conn(conn)
