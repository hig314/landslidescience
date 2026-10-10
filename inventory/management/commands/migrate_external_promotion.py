"""Provenance for a record promoted out of a mirrored third-party inventory.

    python manage.py migrate_external_promotion
    python manage.py migrate_external_promotion --dry-run

Two nullable columns on `landslides`:

    external_source   registry id of the inventory it came from
    external_id       that publisher's own identifier for the record

WHY THE PUBLISHER'S ID AND NOT A FOREIGN KEY
--------------------------------------------
A mirror table is REPLACED wholesale on every refetch -- publishers retire and
renumber entries between releases, so our row ids in `ext_*` are not stable
across a refresh and a real foreign key would break the next time someone runs
the fetch. The publisher's own identifier is the stable thing, and the pair
(source, their id) is what still means something after v15 lands.

WHY AN INDEX AND NOT A UNIQUE CONSTRAINT
----------------------------------------
Two of our records may legitimately derive from one of theirs. A mapper who
looks at one of their points and sees two separable failures should be able to
record two landslides, both pointing back at the point that prompted them.
Uniqueness here would forbid the more careful reading, which is backwards.

Null is the ordinary case: almost everything in this inventory was mapped here,
not promoted from anyone.

Idempotent: ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS throughout.
"""
from django.core.management.base import BaseCommand

SCHEMA_SQL = """
ALTER TABLE landslides ADD COLUMN IF NOT EXISTS external_source text;
ALTER TABLE landslides ADD COLUMN IF NOT EXISTS external_id     text;

CREATE INDEX IF NOT EXISTS landslides_external_idx
    ON landslides (external_source, external_id);

COMMENT ON COLUMN landslides.external_source IS
    'Registry id of the third-party inventory this record was promoted from '
    '(see inventory/external.py SOURCES). Null when mapped here, which is the '
    'ordinary case.';
COMMENT ON COLUMN landslides.external_id IS
    'The publisher''s own identifier for the record this was promoted from. '
    'Their id rather than our mirror row id, because the mirror table is '
    'replaced on every refetch and our ids there do not survive it.';
"""


class Command(BaseCommand):
    help = 'Add external_source / external_id to landslides (idempotent)'

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
                 WHERE table_name = 'landslides'
                   AND column_name IN ('external_source', 'external_id')
            """)
            n = cur.fetchone()[0]
            cur.execute("""
                SELECT count(*) FROM landslides WHERE external_source IS NOT NULL
            """)
            promoted = cur.fetchone()[0]
        finally:
            _put_conn(conn)
        self.stdout.write(self.style.SUCCESS(
            f'landslides: {n}/2 provenance columns present, '
            f'{promoted} record(s) currently marked as promoted'))
