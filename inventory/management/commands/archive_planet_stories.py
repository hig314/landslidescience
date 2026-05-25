"""Archive Planet Labs Story MP4s to a local directory.

Each landslide row may have a `planet_story_link` pointing to a Planet Stories
URL of the form `https://www.planet.com/stories/{slug}`. The underlying MP4 is
publicly hosted on Google Cloud Storage at
`https://storage.googleapis.com/planet-t2/{slug}/movie.mp4` (no auth needed).

This command walks distinct slugs, downloads each MP4 to
`/app/data/planet_stories/{slug}.mp4` (mapped to `/opt/landslidescience/data/`
on the host), and skips slugs already cached. Designed to be idempotent so it
can be re-run periodically to pick up newly added Planet Stories.

Usage:
    python manage.py archive_planet_stories          # download all missing
    python manage.py archive_planet_stories --dry-run    # report only
    python manage.py archive_planet_stories --limit 50   # cap for testing
"""
import os
import time
import urllib.request
import urllib.parse
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


ARCHIVE_DIR     = Path(settings.BASE_DIR) / 'data' / 'planet_stories'
GCS_URL_TEMPLATE = 'https://storage.googleapis.com/planet-t2/{slug}/movie.mp4'
PLANET_STORY_PREFIX = 'https://www.planet.com/stories/'


def _slug_from_url(url):
    """Extract the slug suffix from a Planet Stories URL.

    Returns None if the URL doesn't match the expected pattern (e.g. it's
    a typo or some other Planet product). Trailing slashes and query strings
    are stripped.
    """
    u = (url or '').strip()
    if not u.startswith(PLANET_STORY_PREFIX):
        return None
    rest = u[len(PLANET_STORY_PREFIX):].split('?', 1)[0].split('#', 1)[0]
    rest = rest.rstrip('/')
    return rest or None


class Command(BaseCommand):
    help = 'Archive Planet Labs Story MP4s referenced by landslide records.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would be downloaded without doing it.')
        parser.add_argument('--limit', type=int, default=None,
                            help='Cap the number of downloads (useful for testing).')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn

        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

        # Pull distinct planet_story_link values from the landslides table.
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT planet_story_link
                FROM landslides
                WHERE planet_story_link IS NOT NULL AND planet_story_link != ''
            """)
            urls = [r[0] for r in cur.fetchall()]
            conn.rollback()
        finally:
            _put_conn(conn)

        slugs = sorted({s for s in (_slug_from_url(u) for u in urls) if s})
        self.stdout.write(f"distinct planet_story_link URLs: {len(urls)}")
        self.stdout.write(f"distinct slugs (deduplicated):  {len(slugs)}")

        already = {p.stem for p in ARCHIVE_DIR.glob('*.mp4')}
        todo = [s for s in slugs if s not in already]
        skipped = [s for s in slugs if s in already]
        self.stdout.write(f"already cached:  {len(skipped)}")
        self.stdout.write(f"to download:     {len(todo)}")

        if opts['limit'] is not None:
            todo = todo[:opts['limit']]
            self.stdout.write(f"(limited to {len(todo)} for this run)")

        if opts['dry_run']:
            self.stdout.write("--dry-run: not downloading.")
            for s in todo[:20]:
                self.stdout.write(f"  would fetch  {s}")
            if len(todo) > 20:
                self.stdout.write(f"  ... ({len(todo) - 20} more)")
            return

        downloaded = 0
        bytes_total = 0
        failed = []
        t0 = time.time()
        for i, slug in enumerate(todo, 1):
            url = GCS_URL_TEMPLATE.format(slug=slug)
            dest = ARCHIVE_DIR / f'{slug}.mp4'
            tmp  = dest.with_suffix('.mp4.part')
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'landslidescience-archive/1'})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    with open(tmp, 'wb') as f:
                        while True:
                            chunk = resp.read(64 * 1024)
                            if not chunk: break
                            f.write(chunk)
                size = tmp.stat().st_size
                tmp.rename(dest)
                downloaded += 1
                bytes_total += size
                if i % 25 == 0 or i == len(todo):
                    elapsed = time.time() - t0
                    self.stdout.write(
                        f"  [{i}/{len(todo)}]  {downloaded} ok, {len(failed)} failed, "
                        f"{bytes_total/1e6:.1f} MB so far ({bytes_total/elapsed/1024:.0f} KB/s)"
                    )
            except Exception as e:
                if tmp.exists(): tmp.unlink()
                failed.append((slug, str(e)))

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. downloaded={downloaded}, failed={len(failed)}, "
            f"skipped={len(skipped)}, total bytes={bytes_total:,}"
        ))
        if failed:
            self.stdout.write("\nFailed slugs:")
            for slug, err in failed[:30]:
                self.stdout.write(f"  {slug}  →  {err}")
            if len(failed) > 30:
                self.stdout.write(f"  ... ({len(failed) - 30} more)")
