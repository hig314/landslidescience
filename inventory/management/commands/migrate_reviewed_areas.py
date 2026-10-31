"""One-shot schema migration: reviewed_areas, the analysis panel's denominator.

The ground that has been searched for landslides. The analysis panel's
rate (landslides per 1000 km² of terrain) counts only terrain inside it,
and only landslides whose centroid falls inside it, so ground nobody has
reviewed neither dilutes the rate nor contributes landslides to it
(inventory/analysis.py rasterises the union onto the 1 km lattice).

  - reviewed_areas   id, name, notes, geom MultiPolygon/4326, added_by, added_at

The area GROWS BY ADDED ROWS, never by editing one (Hig, 2026-10-31): the
first row is Alaska, copied from the `alaska` region subset (TIGER/Line
2024, to the 3-nmi territorial-sea limit; only land cells are counted, so
in effect Alaska's land); review of, say, part of Yukon is a second row.
The union is the reviewed area; overlapping rows are harmless.

Idempotent: CREATE ... IF NOT EXISTS, and the Alaska row is seeded only
when the table is empty. Safe to re-run.
"""
from django.core.management.base import BaseCommand


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS reviewed_areas (
    id        serial PRIMARY KEY,
    name      text NOT NULL,
    notes     text,
    geom      geometry(MultiPolygon, 4326) NOT NULL,
    added_by  text,
    added_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS reviewed_areas_geom_idx ON reviewed_areas USING gist (geom);
"""

SEED_SQL = """
INSERT INTO reviewed_areas (name, notes, geom, added_by)
SELECT 'Alaska',
       'The initial reviewed area: the alaska region (TIGER/Line 2024 state boundary, '
       'to the 3-nmi territorial-sea limit). Only land cells are counted, so this is '
       'Alaska''s land area.',
       region_geom, 'migrate_reviewed_areas'
  FROM subsets WHERE slug = 'alaska' AND region_geom IS NOT NULL
"""


class Command(BaseCommand):
    help = 'Create reviewed_areas (the analysis rate denominator) and seed it with Alaska.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Open a transaction, run DDL, then ROLLBACK.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn

        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(SCHEMA_SQL)
            self.stdout.write("schema: reviewed_areas ensured.")
            cur.execute("SELECT COUNT(*) FROM reviewed_areas")
            if cur.fetchone()[0] == 0:
                cur.execute(SEED_SQL)
                if cur.rowcount:
                    self.stdout.write("seeded: Alaska (from the alaska region subset).")
                else:
                    self.stdout.write(self.style.WARNING(
                        "no alaska region subset with a geometry -- table left empty "
                        "(run load_region_geometry for alaska, then re-run this)."))
            cur.execute("SELECT id, name, added_at::date, "
                        "round((ST_Area(geom::geography) / 1e6)::numeric) FROM reviewed_areas ORDER BY id")
            rows = cur.fetchall()
            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING("--dry-run: rolled back."))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS("Committed."))
            for r in rows:
                self.stdout.write(f"  {r[0]:3d}  {r[1]}  {r[2]}  {r[3]:,} km² (incl. water)")
        finally:
            _put_conn(conn)
