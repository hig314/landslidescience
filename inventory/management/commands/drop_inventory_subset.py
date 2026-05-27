"""One-shot: drop the legacy landslides.inventory_subset column.

Subset memberships now live in the landslide_subsets ↔ subsets join, populated
by migrate_subsets. All live read paths (api_features, api_detail, manage_list,
home counts) consult the join. The legacy text column is no longer read by
any application code.

This command performs the actual DROP. Idempotent: if the column is already
gone, it reports that and exits cleanly. --dry-run rolls back at the end.

Sequence to run on dev or prod:
    python manage.py drop_inventory_subset --dry-run   # confirm
    python manage.py drop_inventory_subset             # commit

The drop is irreversible at the schema level. The original values are
preserved indirectly via landslide_subsets memberships (each row's slug
maps to the old name) and in any GeoJSON exports already on disk.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Drop the legacy landslides.inventory_subset column.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Open a transaction, run DDL, then ROLLBACK.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn

        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_schema='public'
                  AND table_name='landslides'
                  AND column_name='inventory_subset'
            """)
            exists = cur.fetchone() is not None
            if not exists:
                self.stdout.write(self.style.SUCCESS(
                    "Column landslides.inventory_subset is already gone — nothing to do."))
                conn.rollback()
                return

            # Sanity check: verify subset memberships are populated before
            # dropping the legacy column. If somehow landslide_subsets is
            # empty, refuse and ask for migrate_subsets to be run first.
            cur.execute("SELECT COUNT(*) FROM landslide_subsets")
            n_memberships = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM landslides")
            n_landslides = cur.fetchone()[0]
            self.stdout.write(f"landslides:          {n_landslides}")
            self.stdout.write(f"join memberships:    {n_memberships}")
            if n_memberships < n_landslides:
                self.stdout.write(self.style.ERROR(
                    "Refusing to drop: fewer memberships than landslides. "
                    "Run migrate_subsets first."))
                conn.rollback()
                return

            # landslide_overview is a Tethys-era view that pulls a subset of
            # landslides columns plus aggregated polygon geometry. We drop +
            # recreate it without inventory_subset (and with the new owner
            # column) so the view continues to serve any external clients
            # (e.g. QGIS connections) but no longer holds back the schema.
            cur.execute("DROP VIEW IF EXISTS landslide_overview")
            cur.execute("ALTER TABLE landslides DROP COLUMN inventory_subset")
            cur.execute("""
                CREATE VIEW landslide_overview AS
                SELECT l.id,
                       l.unique_name,
                       l.landslide_type,
                       l.landslide_class,
                       l.owner,
                       l.size_inclusion,
                       l.description,
                       l.notes,
                       l.noted_by,
                       l.ongoing_work,
                       l.creep_behavior,
                       l.stream_damming,
                       l.planet_labs_creep,
                       l.planet_labs_patchy_creep,
                       l.insar_schaefer,
                       l.insar_kim,
                       l.insar_opera,
                       l.insar_other,
                       l.insar_creep,
                       l.other_subtle_creep,
                       l.geomorph_creep,
                       l.volume_preferred,
                       l.volume_site_specific,
                       l.volume_method,
                       l.planet_story_link,
                       l.esri_wayback_link,
                       l.google_images_link,
                       l.sentinel2_link,
                       l.sentinel1_link,
                       l.post_2012_activity_increase,
                       l.creeping_permafrost_mass,
                       l.catastrophic_failure_years,
                       l.date_min,
                       l.date_max,
                       l.year_text,
                       l.precursory_headscarp,
                       l.exclusively_supraglacial,
                       l.molards,
                       l.seismic_datetime,
                       l.seismic_note,
                       l.seismic_credit,
                       l.created_at,
                       l.updated_at,
                       ST_Centroid(ST_Union(p.geom)) AS centroid,
                       ST_Union(p.geom) AS full_extent,
                       MAX(CASE WHEN p.role IN ('deposit', 'body') THEN p.area ELSE NULL END) AS display_area
                FROM landslides l
                JOIN landslide_polygons p ON p.landslide_id = l.id
                GROUP BY l.id
            """)
            self.stdout.write("dropped landslides.inventory_subset; recreated landslide_overview view (now exposes owner instead).")

            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING("--dry-run: rolled back."))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS("Committed."))
        finally:
            _put_conn(conn)
