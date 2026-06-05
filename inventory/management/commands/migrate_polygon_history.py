"""One-shot schema migration: landslide_polygons_history.

Safety net for in-app polygon geometry editing. Before the web editor
overwrites or deletes a polygon's geometry, the pre-edit row is snapshotted
into this table so any edit can be reviewed or reverted — the project's
"never lose data" stance applied to geometry.

Columns:
  - polygon_id    int          the live landslide_polygons.id (orphaned on delete)
  - landslide_id  int          owning landslide (survives polygon-row deletion)
  - role          text         pre-edit role
  - area          float8       pre-edit area (EPSG:3338 m²)
  - geom          geometry(MULTIPOLYGON,4326)   pre-edit geometry
  - operation     text         'update' | 'delete'
  - edited_by_id  int          auth_user.id of the editor (nullable)
  - edited_at     timestamptz  default now()

Append-only; never updated. Idempotent — CREATE TABLE / INDEX IF NOT EXISTS.
Safe to re-run.
"""
from django.core.management.base import BaseCommand


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS landslide_polygons_history (
    id            bigserial PRIMARY KEY,
    polygon_id    integer,
    landslide_id  integer,
    role          text,
    area          double precision,
    geom          geometry(MultiPolygon, 4326),
    operation     text,
    edited_by_id  integer,
    edited_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS landslide_polygons_history_landslide_idx
    ON landslide_polygons_history (landslide_id, edited_at DESC);
CREATE INDEX IF NOT EXISTS landslide_polygons_history_polygon_idx
    ON landslide_polygons_history (polygon_id);
"""


class Command(BaseCommand):
    help = 'Add the landslide_polygons_history table (geometry-edit safety net).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Open a transaction, run DDL, then ROLLBACK.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn

        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(SCHEMA_SQL)
            self.stdout.write("schema: landslide_polygons_history + indexes ensured.")

            cur.execute("SELECT COUNT(*) FROM landslide_polygons_history")
            n_rows = cur.fetchone()[0]

            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING("--dry-run: rolled back."))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS("Committed."))

            self.stdout.write(f"\nhistory rows: {n_rows}  (expect 0 on first run)")
        finally:
            _put_conn(conn)
