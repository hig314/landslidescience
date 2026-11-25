"""Create landslide_external_links and move the old single stamps into it.

    python manage.py migrate_external_links

One row per (our landslide, their source, their id): a record of ours may
derive from several outside records, in one inventory or several (Hig,
2026-11-24: "We should be able to link multiple sources"). Until then the
provenance was two columns on `landslides` (external_source, external_id),
which held one link; those columns are copied here and then no longer read or
written by the app (kept, not dropped, so nothing is destroyed; the edit form
no longer shows them).

ON DELETE CASCADE: a permanently deleted landslide takes its links with it,
whichever path deletes it. Idempotent; run once per environment (dev and prod
are separate databases), right after the deploy that ships the code. Until it
has run, link endpoints answer 503 and the card shows nothing.
"""
from django.core.management.base import BaseCommand

from inventory.views import _get_conn, _put_conn

DDL = """
CREATE TABLE IF NOT EXISTS landslide_external_links (
    landslide_id integer NOT NULL REFERENCES landslides(id) ON DELETE CASCADE,
    source       text    NOT NULL,
    ext_id       text    NOT NULL,
    linked_at    timestamptz NOT NULL DEFAULT now(),
    linked_by    text,
    PRIMARY KEY (landslide_id, source, ext_id)
);
CREATE INDEX IF NOT EXISTS landslide_external_links_src_idx
    ON landslide_external_links (source, ext_id);
COMMENT ON TABLE landslide_external_links IS
    'Outside-inventory records a landslide of ours was promoted from or linked to '
    '(inventory/external.py). source = registry id, ext_id = THE PUBLISHER''S id.';
"""

COPY = """
INSERT INTO landslide_external_links (landslide_id, source, ext_id, linked_at)
SELECT id, external_source, external_id, coalesce(updated_at, created_at, now())
  FROM landslides
 WHERE coalesce(external_source, '') <> '' AND coalesce(external_id, '') <> ''
ON CONFLICT DO NOTHING
"""


class Command(BaseCommand):
    help = 'Create landslide_external_links and copy the old single stamps (idempotent)'

    def handle(self, *a, **o):
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(DDL)
            cur.execute(COPY)
            copied = cur.rowcount
            cur.execute('SELECT count(*), count(DISTINCT landslide_id) FROM landslide_external_links')
            n, ls = cur.fetchone()
            conn.commit()
        finally:
            _put_conn(conn)
        self.stdout.write(self.style.SUCCESS(
            f'landslide_external_links: {n} link(s) on {ls} landslide(s); '
            f'{copied} copied from external_source/external_id this run'))
