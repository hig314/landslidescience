"""One-shot schema migration: subsets + ownership.

Adds the schema underpinning the publication workflow:
  - subsets table (slug/name/metadata for each named grouping)
  - landslide_subsets M2M table (one row per (landslide, subset) membership)
  - landslides.owner column (free text)

Then migrates the existing single-valued `inventory_subset` text column
into rows of the new tables, seeds an `alaska-2025` publication subset
containing every landslide, and sets every landslide's owner to
'Bretwood Higman'.

Idempotent: safe to re-run. CREATE TABLE IF NOT EXISTS, ALTER TABLE ADD
COLUMN IF NOT EXISTS, INSERT ... ON CONFLICT DO NOTHING, and UPDATE only
fills NULLs so prior owner edits are preserved.

Usage:
    python manage.py migrate_subsets             # apply
    python manage.py migrate_subsets --dry-run   # report what it would do
"""
from django.core.management.base import BaseCommand


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS subsets (
    id             serial PRIMARY KEY,
    slug           text UNIQUE NOT NULL,
    name           text NOT NULL,
    description    text,
    default_owner  text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    is_publication boolean NOT NULL DEFAULT FALSE,
    citation_info  text
);

CREATE TABLE IF NOT EXISTS landslide_subsets (
    landslide_id integer NOT NULL REFERENCES landslides(id) ON DELETE CASCADE,
    subset_id    integer NOT NULL REFERENCES subsets(id)    ON DELETE CASCADE,
    PRIMARY KEY (landslide_id, subset_id)
);

CREATE INDEX IF NOT EXISTS landslide_subsets_subset_idx
    ON landslide_subsets(subset_id);

ALTER TABLE landslides ADD COLUMN IF NOT EXISTS owner text;
"""

# Slug, name, default_owner — one row per existing inventory_subset value.
# These are treated as provenance subsets (not publication subsets).
LEGACY_SUBSETS = [
    ('alaska',   'Alaska',   'Bretwood Higman'),
    ('schaefer', 'Schaefer', 'Bretwood Higman'),
    ('kim',      'Kim',      'Bretwood Higman'),
]

# The initial publication subset — every landslide is a member, marking
# the current state as the basis for the 2025 publication snapshot.
PUBLICATION_SUBSET = ('alaska-2025', 'Alaska 2025', 'Bretwood Higman')

DEFAULT_OWNER = 'Bretwood Higman'


class Command(BaseCommand):
    help = "Create subsets/landslide_subsets/owner schema and migrate existing inventory_subset values."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Open a transaction, do the work, then ROLLBACK. Shows counts without persisting.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn

        dry = opts['dry_run']
        conn = _get_conn()
        try:
            cur = conn.cursor()

            # ---- 1. schema ----
            cur.execute(SCHEMA_SQL)
            self.stdout.write("schema: subsets, landslide_subsets, owner column ensured.")

            # ---- 2. seed subsets (legacy + publication) ----
            for slug, name, owner in LEGACY_SUBSETS:
                cur.execute(
                    """INSERT INTO subsets (slug, name, default_owner)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (slug) DO NOTHING""",
                    (slug, name, owner),
                )
            cur.execute(
                """INSERT INTO subsets (slug, name, default_owner, is_publication)
                   VALUES (%s, %s, %s, TRUE)
                   ON CONFLICT (slug) DO NOTHING""",
                PUBLICATION_SUBSET,
            )

            # ---- 3. populate memberships from existing inventory_subset ----
            # Match on subsets.name so the Alaska/Schaefer/Kim text values map
            # to the corresponding new rows. Records with NULL/empty
            # inventory_subset are skipped here (they still get alaska-2025
            # in step 4).
            cur.execute("""
                INSERT INTO landslide_subsets (landslide_id, subset_id)
                SELECT l.id, s.id
                FROM landslides l
                JOIN subsets s ON s.name = l.inventory_subset
                WHERE l.inventory_subset IS NOT NULL
                  AND l.inventory_subset <> ''
                ON CONFLICT DO NOTHING
            """)
            legacy_added = cur.rowcount

            # ---- 4. every landslide → alaska-2025 ----
            cur.execute("""
                INSERT INTO landslide_subsets (landslide_id, subset_id)
                SELECT l.id, s.id
                FROM landslides l
                CROSS JOIN subsets s
                WHERE s.slug = 'alaska-2025'
                ON CONFLICT DO NOTHING
            """)
            pub_added = cur.rowcount

            # ---- 5. owner: fill NULLs only ----
            cur.execute(
                "UPDATE landslides SET owner = %s WHERE owner IS NULL",
                (DEFAULT_OWNER,),
            )
            owner_filled = cur.rowcount

            # ---- 6. verification ----
            cur.execute("SELECT COUNT(*) FROM landslides")
            n_landslides = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM subsets")
            n_subsets = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM landslide_subsets")
            n_memberships = cur.fetchone()[0]
            cur.execute("""
                SELECT COUNT(*) FROM landslides
                WHERE id NOT IN (SELECT landslide_id FROM landslide_subsets)
            """)
            n_orphan_landslides = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM landslides WHERE owner IS NULL OR owner = ''")
            n_unowned = cur.fetchone()[0]
            cur.execute("""
                SELECT s.slug, COUNT(ls.landslide_id)
                FROM subsets s
                LEFT JOIN landslide_subsets ls ON ls.subset_id = s.id
                GROUP BY s.slug
                ORDER BY s.slug
            """)
            subset_counts = cur.fetchall()

            # ---- 7. commit or roll back ----
            if dry:
                conn.rollback()
                self.stdout.write(self.style.WARNING("\n--dry-run: rolled back.\n"))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS("\nCommitted.\n"))

            self.stdout.write(f"insert landslide_subsets from legacy inventory_subset: {legacy_added} rows")
            self.stdout.write(f"insert landslide_subsets for alaska-2025:              {pub_added} rows")
            self.stdout.write(f"update landslides set owner where NULL:                {owner_filled} rows")
            self.stdout.write("")
            self.stdout.write(f"landslides total:                       {n_landslides}")
            self.stdout.write(f"subsets total:                          {n_subsets}")
            self.stdout.write(f"landslide_subsets memberships total:    {n_memberships}")
            self.stdout.write(f"landslides with no subset membership:   {n_orphan_landslides}  (expect 0)")
            self.stdout.write(f"landslides with owner NULL/empty:       {n_unowned}  (expect 0)")
            self.stdout.write("\nper-subset membership counts:")
            for slug, count in subset_counts:
                self.stdout.write(f"  {slug:<14} {count}")
        finally:
            _put_conn(conn)
