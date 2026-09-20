"""Schema for traced fault scarps.

    python manage.py migrate_fault_scarps            # apply
    python manage.py migrate_fault_scarps --dry-run  # report only

WHAT THIS IS FOR
----------------
While mapping landslides on the lidar surfaces, scarps turn up that are not
landslide headwalls -- lineaments that look tectonic. They belong with the
Alaska active faults and folds database rather than the landslide inventory,
but there is nowhere to put an observation like that at the moment it is made,
and an observation not written down at that moment is usually lost.

So: somewhere to draw the line and say what you thought. Deliberately thin.
Geometry, who traced it, a note, and timestamps. Anything that turns out to
matter (certainty, sense of motion, a link to a USGS fault id) can be added
later with ALTER TABLE ADD COLUMN, which is why this command is idempotent.

WHY MULTILINESTRING
-------------------
A scarp is a line, and the landslide table's convention is that the column type
is the MULTI form with single-part rows -- writes go through ST_Multi so a
client can send a plain LineString and the column stays one type. Keeping the
same convention means the reviser's unwrap-on-read logic ports directly if
these ever become editable the same way.

Idempotent: CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS / ALTER
TABLE ADD COLUMN IF NOT EXISTS throughout, so re-running is safe.
"""
from django.core.management.base import BaseCommand

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fault_scarps (
    id          serial PRIMARY KEY,
    geom        geometry(MultiLineString, 4326) NOT NULL,
    notes       text NOT NULL DEFAULT '',
    -- Who drew it. Stored as the Django username rather than a foreign key:
    -- the auth tables live in the app's SQLite database, not in PostGIS, so a
    -- real FK is not available across the two. The username is what a reader
    -- of this table needs anyway.
    traced_by   text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS fault_scarps_geom_idx
    ON fault_scarps USING GIST (geom);
CREATE INDEX IF NOT EXISTS fault_scarps_traced_by_idx
    ON fault_scarps (traced_by);

COMMENT ON TABLE fault_scarps IS
    'Hand-traced possible fault scarps noticed while mapping landslides on '
    'lidar. Observations, not a published fault map: see the Alaska active '
    'faults and folds database for that.';
"""


class Command(BaseCommand):
    help = 'Create the fault_scarps table (idempotent)'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn
        if opts['dry_run']:
            self.stdout.write(SCHEMA_SQL)
            return
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(SCHEMA_SQL)
            conn.commit()
            cur.execute("""
                SELECT count(*) FROM information_schema.columns
                 WHERE table_name = 'fault_scarps'
            """)
            ncols = cur.fetchone()[0]
            cur.execute('SELECT count(*) FROM fault_scarps')
            nrows = cur.fetchone()[0]
        finally:
            _put_conn(conn)
        self.stdout.write(self.style.SUCCESS(
            'fault_scarps ready: %d columns, %d rows' % (ncols, nrows)))
