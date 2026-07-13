"""Load/replace the `countries` reference table from Marine Regions'
"EEZ + land union" layer (MarineRegions:eez_land, CC-BY VLIZ/Flanders
Marine Institute — cite marineregions.org).

Each sovereign's land plus its waters out to 200 nmi, so the compute_country
rule resolves coastal/fjord centroids by pure containment. One row per
source feature: name = sovereign1 (country-level; the layer splits e.g.
"United States / Alaska" into territory features, and we want nationality),
iso3 = iso_sov1. Overlapping/joint-regime claims stay as-is; the rule picks
deterministically (ORDER BY name).

Source file: WFS SHAPE-ZIP export, downloaded via
  https://geo.vliz.be/geoserver/MarineRegions/wfs?service=WFS&version=1.0.0
    &request=GetFeature&typeName=MarineRegions:eez_land&outputFormat=SHAPE-ZIP
(NOTE: WFS **1.0.0** — the 2.0 export comes back lat/lon axis-swapped.)
Expected at data/regions_seed/eez_land.shp unless --path is given.

Replaces the whole table in one transaction; re-run any time the source is
refreshed. Follow with a country rule-apply (rules admin, or review-saves
as records are touched) to propagate changes.
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

DEFAULT_PATH = Path(settings.BASE_DIR) / 'data' / 'regions_seed' / 'eez_land.shp'


class Command(BaseCommand):
    help = 'Load/replace the countries reference table from the EEZ+land union shapefile.'

    def add_arguments(self, parser):
        parser.add_argument('--path', default=str(DEFAULT_PATH),
                            help=f'Path to eez_land.shp (default: {DEFAULT_PATH})')
        parser.add_argument('--dry-run', action='store_true',
                            help='Load + report inside a transaction, then ROLLBACK.')

    def handle(self, *args, **opts):
        from pyogrio.raw import read as pyogrio_read

        from inventory.views import _get_conn, _put_conn

        path = Path(opts['path'])
        if not path.exists():
            raise CommandError(f'{path} not found — download the WFS 1.0.0 SHAPE-ZIP '
                               'export (see module docstring) and unzip it there.')

        meta, _fids, geometry, field_data = pyogrio_read(str(path), read_geometry=True,
                                                         force_2d=True)
        fields = list(meta.get('fields'))
        try:
            i_sov = fields.index('sovereign1')
            i_iso = fields.index('iso_sov1')
        except ValueError:
            raise CommandError(f'Expected sovereign1/iso_sov1 fields; got {fields}')
        crs_label = (meta.get('crs') or '').lower()
        if crs_label and 'epsg:4326' not in crs_label:
            raise CommandError(f'Source CRS is {meta.get("crs")!r}, expected EPSG:4326.')

        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute('TRUNCATE countries RESTART IDENTITY')
            n = 0
            for wkb, sov, iso in zip(geometry, field_data[i_sov], field_data[i_iso]):
                if wkb is None or len(wkb) == 0 or not sov:
                    continue
                # MakeValid + extract polygons + force Multi: shapefile rings
                # arrive as Polygon and the odd invalid ring must not poison
                # the containment tests.
                cur.execute("""
                    INSERT INTO countries (name, iso3, geom)
                    VALUES (%s, %s, ST_Multi(ST_CollectionExtract(
                                ST_MakeValid(ST_GeomFromWKB(%s, 4326)), 3)))
                """, (str(sov), str(iso) if iso else None, bytes(wkb)))
                n += 1
            cur.execute('ANALYZE countries')
            cur.execute('SELECT COUNT(DISTINCT name), COUNT(*) FROM countries')
            n_names, n_rows = cur.fetchone()
            self.stdout.write(f'loaded {n_rows} polygons, {n_names} distinct sovereigns.')
            # Smoke test: a known point must resolve.
            cur.execute("""SELECT name FROM countries
                           WHERE ST_Covers(geom, ST_SetSRID(ST_MakePoint(-136.17, 59.22), 4326))
                           ORDER BY name LIMIT 1""")
            got = cur.fetchone()
            self.stdout.write(f'smoke test (Takhin centroid): {got[0] if got else "MISS"}')
            if not got or got[0] != 'United States':
                raise CommandError('Smoke test failed — refusing to commit '
                                   '(axis order or CRS problem in the source?).')
            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING('--dry-run: rolled back.'))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS('Committed.'))
        finally:
            _put_conn(conn)
