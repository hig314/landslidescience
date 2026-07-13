"""One-shot schema migration: spatial region subsets + country attribute.

Regions: a subset may be defined by a polygon instead of hand-curated
membership — `kind='region'` + `region_geom`. Membership stays materialized
in landslide_subsets (every read path unchanged); inventory/regions.py is
the only writer for region-kind subsets.

Country: `landslides.country` is a derived column (compute_country rule)
resolved against the `countries` reference table — Marine Regions
"EEZ + land union" polygons loaded by `load_countries`, so coastal/fjord
centroids always resolve. The table is CREATED here (empty) so the rule's
SQL is valid before the data load ever runs.

  - subsets.kind               text NOT NULL DEFAULT 'tag'   ('tag'|'region')
  - subsets.region_geom        geometry(MultiPolygon, 4326)  + GiST index
  - subsets.region_updated_at  timestamptz
  - landslides.country         text
  - countries                  reference table (name, iso3, geom + GiST)

Idempotent: ADD COLUMN / CREATE ... IF NOT EXISTS. Safe to re-run.
"""
from django.core.management.base import BaseCommand


SCHEMA_SQL = """
ALTER TABLE subsets ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'tag';
ALTER TABLE subsets ADD COLUMN IF NOT EXISTS region_geom geometry(MultiPolygon, 4326);
ALTER TABLE subsets ADD COLUMN IF NOT EXISTS region_updated_at timestamptz;
CREATE INDEX IF NOT EXISTS subsets_region_geom_idx
    ON subsets USING gist (region_geom);

ALTER TABLE landslides ADD COLUMN IF NOT EXISTS country text;

CREATE TABLE IF NOT EXISTS countries (
    id     serial PRIMARY KEY,
    name   text NOT NULL,
    iso3   text,
    geom   geometry(MultiPolygon, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS countries_geom_idx ON countries USING gist (geom);
"""


class Command(BaseCommand):
    help = 'Add region-subset columns, landslides.country, and the countries table.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Open a transaction, run DDL, then ROLLBACK.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn

        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(SCHEMA_SQL)
            self.stdout.write('schema: subsets.kind/region_geom/region_updated_at, '
                              'landslides.country, countries table ensured.')
            cur.execute("SELECT kind, COUNT(*) FROM subsets GROUP BY kind ORDER BY kind")
            for kind, n in cur.fetchall():
                self.stdout.write(f'  subsets kind={kind}: {n}')
            cur.execute("SELECT COUNT(*) FROM countries")
            self.stdout.write(f'  countries rows: {cur.fetchone()[0]}')
            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING('--dry-run: rolled back.'))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS('Committed.'))
        finally:
            _put_conn(conn)
