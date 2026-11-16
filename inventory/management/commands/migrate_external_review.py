"""Editors' review of records in the mirrored third-party inventories.

    python manage.py migrate_external_review
    python manage.py migrate_external_review --dry-run

Three tables, all keyed by THE PUBLISHER'S id (see migrate_external_promotion
for why: the mirror is replaced on every refetch, our row ids there do not
survive it, and the review has to):

    ext_review_categories   the shared list of review categories, made by
                            editors with the popup's `+` as they need them
                            (Hig, 2026-11-16: "a few different review notes
                            that I'll develop in an ad hoc way"). One list for
                            every source, so one judgement has one name.
    ext_reviews             the current review of one record: its category and
                            an optional note. No row = unreviewed.
    ext_review_log          every change, append-only, so a re-categorised
                            record keeps the history of what it was called.

WHY NOT A COLUMN ON THE MIRROR TABLE
------------------------------------
A refetch REPLACES the mirror table (external.py), so a review column there
would be erased by the next release. Kept apart, the review survives a refetch
and reattaches by (source, layer, publisher id); a record the publisher retired
keeps its review row, orphaned but harmless.

WHY "PROMOTED" IS NOT A CATEGORY
--------------------------------
Promotion is read live from `landslides.external_source/external_id`, never
stored here, so the review cannot claim a promotion that was deleted or miss
one made by the draw flow.

Editor-only: none of this is served to the public (Hig, 2026-11-16).

Idempotent: CREATE ... IF NOT EXISTS throughout.
"""
from django.core.management.base import BaseCommand

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ext_review_categories (
    id          serial PRIMARY KEY,
    label       text NOT NULL,
    created_by  text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ext_review_categories_label_uq
    ON ext_review_categories (lower(btrim(label)));

CREATE TABLE IF NOT EXISTS ext_reviews (
    source       text NOT NULL,
    layer        text NOT NULL,
    ext_id       text NOT NULL,
    category_id  integer NOT NULL REFERENCES ext_review_categories (id),
    note         text,
    reviewed_by  text,
    reviewed_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, layer, ext_id)
);

CREATE TABLE IF NOT EXISTS ext_review_log (
    id           serial PRIMARY KEY,
    source       text NOT NULL,
    layer        text NOT NULL,
    ext_id       text NOT NULL,
    category_id  integer,
    note         text,
    reviewed_by  text,
    reviewed_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ext_review_log_rec_idx
    ON ext_review_log (source, layer, ext_id);

COMMENT ON TABLE ext_reviews IS
    'Editors'' current review of one mirrored record, keyed by the publisher''s '
    'id. No row = unreviewed. Promotion is read from landslides.external_*, not '
    'stored here. Editor-only.';
COMMENT ON TABLE ext_review_log IS
    'Every change to ext_reviews, append-only. category_id NULL = set back to '
    'unreviewed.';
"""


class Command(BaseCommand):
    help = 'Create the external-inventory review tables (idempotent)'

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
            cur.execute('SELECT count(*) FROM ext_review_categories')
            ncat = cur.fetchone()[0]
            cur.execute('SELECT count(*) FROM ext_reviews')
            nrev = cur.fetchone()[0]
        finally:
            _put_conn(conn)
        self.stdout.write(self.style.SUCCESS(
            f'ext_review_*: present; {ncat} categor(y/ies), {nrev} reviewed record(s)'))
