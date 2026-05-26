"""One-shot schema migration: planet_stories N:M model.

Replaces the single-valued `landslides.planet_story_link` text column with a
proper many-to-many relation: a landslide can reference multiple Planet
stories, and a single Planet story can be referenced by multiple landslides.

New tables:
  planet_stories
    slug            text PK
    story_type      'timelapse' | 'comparison' | NULL
    mp4_archived_at timestamptz   (when an MP4 was last successfully archived)
    mp4_size_bytes  bigint
    last_probed_at  timestamptz   (last HEAD check against GCS)
    manually_set    boolean       (if TRUE, future archive runs skip auto-reclassify)

  landslide_planet_stories
    landslide_id  integer FK landslides(id)
    slug          text    FK planet_stories(slug)
    sort_order    smallint (editor-controllable display order)
    PRIMARY KEY (landslide_id, slug)

Migration steps:
  1. Create tables (IF NOT EXISTS).
  2. From distinct landslides.planet_story_link, INSERT into planet_stories.
  3. For each landslide with a link, INSERT into landslide_planet_stories.
  4. (Optional) HEAD-probe each slug at GCS to classify timelapse vs. comparison.
  5. For timelapse slugs whose MP4 is on disk, record archive timestamp + size.

The `landslides.planet_story_link` column is NOT dropped here; that waits
until views.py is rewired to read from the join table.

Usage:
    python manage.py migrate_planet_stories             # full run (probes GCS)
    python manage.py migrate_planet_stories --dry-run   # report only, no changes
    python manage.py migrate_planet_stories --no-probe  # tables + migrate only,
                                                          skip the GCS HEAD checks
"""
import time
import urllib.request
import urllib.error
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


ARCHIVE_DIR = Path(settings.BASE_DIR) / 'data' / 'planet_stories'
GCS_URL_TEMPLATE = 'https://storage.googleapis.com/planet-t2/{slug}/movie.mp4'
PLANET_STORY_PREFIX = 'https://www.planet.com/stories/'

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS planet_stories (
    slug            text PRIMARY KEY,
    story_type      text,
    mp4_archived_at timestamptz,
    mp4_size_bytes  bigint,
    last_probed_at  timestamptz,
    manually_set    boolean NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS landslide_planet_stories (
    landslide_id integer  NOT NULL REFERENCES landslides(id)       ON DELETE CASCADE,
    slug         text     NOT NULL REFERENCES planet_stories(slug) ON DELETE CASCADE,
    sort_order   smallint NOT NULL DEFAULT 0,
    PRIMARY KEY (landslide_id, slug)
);

CREATE INDEX IF NOT EXISTS landslide_planet_stories_slug_idx
    ON landslide_planet_stories(slug);
"""


def _slug_from_url(url):
    """Extract slug from a Planet Stories URL; None if it doesn't match."""
    u = (url or '').strip()
    if not u.startswith(PLANET_STORY_PREFIX):
        return None
    rest = u[len(PLANET_STORY_PREFIX):].split('?', 1)[0].split('#', 1)[0].rstrip('/')
    return rest or None


def _head_probe(slug, timeout=15):
    """HEAD-check the GCS MP4 URL. Returns 'timelapse' on 200, 'comparison' on 404,
    None on any other error/timeout."""
    url = GCS_URL_TEMPLATE.format(slug=slug)
    req = urllib.request.Request(url, method='HEAD',
                                  headers={'User-Agent': 'landslidescience-archive/1'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 'timelapse' if resp.status == 200 else None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return 'comparison'
        return None
    except Exception:
        return None


class Command(BaseCommand):
    help = 'Migrate planet_story_link to N:M planet_stories + landslide_planet_stories.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Roll back at the end; report what it would do.')
        parser.add_argument('--no-probe', action='store_true',
                            help='Skip GCS HEAD probes; leave story_type NULL on new rows.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn

        dry = opts['dry_run']
        no_probe = opts['no_probe']

        conn = _get_conn()
        try:
            cur = conn.cursor()

            # ---- 1. schema ----
            cur.execute(SCHEMA_SQL)
            self.stdout.write("schema: planet_stories, landslide_planet_stories ensured.")

            # ---- 2. collect (landslide_id, slug) from existing column ----
            cur.execute("""
                SELECT id, planet_story_link
                FROM landslides
                WHERE planet_story_link IS NOT NULL AND planet_story_link <> ''
            """)
            pairs = []
            bad_urls = []
            for lid, link in cur.fetchall():
                slug = _slug_from_url(link)
                if slug:
                    pairs.append((lid, slug))
                else:
                    bad_urls.append((lid, link))
            distinct_slugs = sorted({s for _, s in pairs})
            self.stdout.write(f"landslide rows with planet_story_link: {len(pairs) + len(bad_urls)}")
            self.stdout.write(f"distinct slugs extracted:               {len(distinct_slugs)}")
            if bad_urls:
                self.stdout.write(self.style.WARNING(
                    f"  ignored {len(bad_urls)} row(s) with non-Planet-Stories URLs:"))
                for lid, link in bad_urls[:5]:
                    self.stdout.write(f"    id={lid}: {link}")
                if len(bad_urls) > 5:
                    self.stdout.write(f"    ... ({len(bad_urls)-5} more)")

            # ---- 3. INSERT planet_stories ----
            cur.executemany(
                "INSERT INTO planet_stories (slug) VALUES (%s) ON CONFLICT (slug) DO NOTHING",
                [(s,) for s in distinct_slugs],
            )
            cur.execute("SELECT COUNT(*) FROM planet_stories")
            n_stories = cur.fetchone()[0]
            self.stdout.write(f"planet_stories rows total: {n_stories}")

            # ---- 4. INSERT memberships ----
            cur.executemany(
                """INSERT INTO landslide_planet_stories (landslide_id, slug)
                   VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                pairs,
            )
            cur.execute("SELECT COUNT(*) FROM landslide_planet_stories")
            n_memberships = cur.fetchone()[0]
            self.stdout.write(f"landslide_planet_stories rows total: {n_memberships}")

            # ---- 5. classify mp4_archived_at from disk for the slugs we have ----
            on_disk = {p.stem for p in ARCHIVE_DIR.glob('*.mp4')}
            stamped = 0
            for slug in distinct_slugs:
                if slug in on_disk:
                    path = ARCHIVE_DIR / f'{slug}.mp4'
                    size = path.stat().st_size
                    mtime = path.stat().st_mtime
                    cur.execute("""
                        UPDATE planet_stories
                        SET story_type      = COALESCE(story_type, 'timelapse'),
                            mp4_archived_at = to_timestamp(%s),
                            mp4_size_bytes  = %s
                        WHERE slug = %s AND NOT manually_set
                    """, (mtime, size, slug))
                    stamped += cur.rowcount
            self.stdout.write(f"stamped mp4_archived_at from disk: {stamped} rows")

            # ---- 6. probe GCS for classification (timelapse vs comparison) ----
            if no_probe:
                self.stdout.write("--no-probe: skipping GCS HEAD checks.")
            else:
                # Probe only rows that still lack a story_type AND aren't manually-set.
                cur.execute("""
                    SELECT slug FROM planet_stories
                    WHERE story_type IS NULL AND NOT manually_set
                    ORDER BY slug
                """)
                to_probe = [r[0] for r in cur.fetchall()]
                self.stdout.write(f"probing {len(to_probe)} slug(s) at GCS...")
                t0 = time.time()
                tl, cp, unknown = 0, 0, 0
                for i, slug in enumerate(to_probe, 1):
                    result = _head_probe(slug)
                    if result == 'timelapse':
                        tl += 1
                    elif result == 'comparison':
                        cp += 1
                    else:
                        unknown += 1
                    cur.execute("""
                        UPDATE planet_stories
                        SET story_type     = COALESCE(story_type, %s),
                            last_probed_at = now()
                        WHERE slug = %s AND NOT manually_set
                    """, (result, slug))
                    if i % 100 == 0 or i == len(to_probe):
                        self.stdout.write(
                            f"  [{i}/{len(to_probe)}]  "
                            f"timelapse={tl} comparison={cp} unknown={unknown}  "
                            f"({(time.time()-t0):.1f}s)"
                        )

            # ---- 7. final state summary ----
            cur.execute("""
                SELECT story_type, COUNT(*) FROM planet_stories
                GROUP BY story_type ORDER BY story_type NULLS LAST
            """)
            type_counts = cur.fetchall()
            cur.execute("SELECT COUNT(*) FROM landslides WHERE planet_story_link IS NOT NULL AND planet_story_link <> ''")
            n_landslides_with_link = cur.fetchone()[0]

            if dry:
                conn.rollback()
                self.stdout.write(self.style.WARNING("\n--dry-run: rolled back.\n"))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS("\nCommitted.\n"))

            self.stdout.write("\n--- final state ---")
            self.stdout.write(f"landslides with planet_story_link (column unchanged): {n_landslides_with_link}")
            self.stdout.write(f"planet_stories rows:        {n_stories}")
            self.stdout.write(f"landslide_planet_stories:   {n_memberships}")
            self.stdout.write("story_type breakdown:")
            for st, count in type_counts:
                self.stdout.write(f"  {str(st or '(unclassified)'):<16} {count}")
        finally:
            _put_conn(conn)
