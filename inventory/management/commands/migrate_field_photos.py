"""One-shot schema migration: field photos N:M model.

Editor-uploaded field photos, linkable to one or (rarely) several landslides.
Bytes live under data/media/field_photos/<id>/ (original + web + thumb —
see inventory/photos.py); these tables hold the metadata and the join.

New tables:
  photos
    id            serial PK
    sha256        text UNIQUE   (dedupe: re-uploading an identical file links
                                 the existing photo instead of duplicating it)
    orig_filename text          (as uploaded, provenance only)
    ext           text          (original's extension, e.g. 'jpg', 'heic')
    caption       text
    photographer  text          (public credit line)
    license       text          (e.g. 'CC BY 4.0'; NULL = site default)
    taken_at      timestamptz   (EXIF DateTimeOriginal; editable. EXIF carries
                                 no timezone — stored as-if-UTC, display dates)
    geom          geometry(Point, 4326)  (EXIF GPS camera position; nullable)
    azimuth_deg   real          (EXIF GPSImgDirection — view direction)
    width, height integer       (of the original, after EXIF rotation)
    size_bytes    bigint
    uploaded_by   text
    uploaded_at   timestamptz

  landslide_photos
    landslide_id  integer FK landslides(id)  ON DELETE CASCADE
    photo_id      integer FK photos(id)      ON DELETE CASCADE
    sort_order    smallint  -- two-tier ordering: set = "featured" (curated
                            -- position, editor-draggable); NULL = uncurated,
                            -- auto-ordered by taken_at after the featured.
    PRIMARY KEY (landslide_id, photo_id)

Idempotent: CREATE TABLE IF NOT EXISTS. Safe to re-run. Run once per
environment (dev and prod databases are separate).
"""
from django.core.management.base import BaseCommand


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS photos (
    id            serial PRIMARY KEY,
    sha256        text UNIQUE NOT NULL,
    orig_filename text,
    ext           text NOT NULL,
    caption       text,
    photographer  text,
    license       text,
    taken_at      timestamptz,
    geom          geometry(Point, 4326),
    azimuth_deg   real,
    width         integer,
    height        integer,
    size_bytes    bigint,
    uploaded_by   text,
    uploaded_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS landslide_photos (
    landslide_id  integer  NOT NULL REFERENCES landslides(id) ON DELETE CASCADE,
    photo_id      integer  NOT NULL REFERENCES photos(id)     ON DELETE CASCADE,
    sort_order    smallint,
    PRIMARY KEY (landslide_id, photo_id)
);

CREATE INDEX IF NOT EXISTS landslide_photos_photo_idx
    ON landslide_photos (photo_id);
"""


class Command(BaseCommand):
    help = 'Add photos + landslide_photos (field photos, N:M to landslides).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Open a transaction, run DDL, then ROLLBACK.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn

        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(SCHEMA_SQL)
            self.stdout.write("schema: photos + landslide_photos ensured.")
            cur.execute("SELECT COUNT(*) FROM photos")
            n_photos = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM landslide_photos")
            n_links = cur.fetchone()[0]
            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING("--dry-run: rolled back."))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS("Committed."))
            self.stdout.write(f"\nphotos: {n_photos}, links: {n_links}")
        finally:
            _put_conn(conn)
