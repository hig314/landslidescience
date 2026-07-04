"""Runtime Planet-story hooks: sync the N:M tables when planet_story_link
changes, and archive a newly-linked timelapse MP4 automatically.

Historically the N:M sync lived only in the manage_edit full-form save — but
scalar fields now autosave through manage_edit_field, so a link set in the
review form updated the legacy text column and nothing else: no
planet_stories row, no archive, no embed in the landslide view. And archiving
always waited for a manual archive_planet_stories run.

These hooks close that loop from every path that writes planet_story_link
(autosave, full-form save, file import):

  sync_story_link()      — mirror the change into planet_stories +
                           landslide_planet_stories (caller's transaction).
  ensure_archived_async()— background thread: HEAD-probe GCS to classify
                           timelapse vs comparison, download the MP4 when it's
                           a timelapse not yet on disk, stamp the row. A
                           download can exceed gunicorn's 30 s window, hence
                           the thread. Never raises; idempotent with the batch
                           migrate_planet_stories / archive_planet_stories
                           commands (same disk layout + stamping semantics,
                           COALESCE never clobbers an existing story_type).

This module keeps its module level free of app imports (views imports it;
it reaches back for the connection pool lazily inside functions).
"""
import logging
import re
import shutil
import threading
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings

log = logging.getLogger(__name__)

STORY_PREFIX = 'https://www.planet.com/stories/'
GCS_URL = 'https://storage.googleapis.com/planet-t2/{slug}/movie.mp4'
ARCHIVE_DIR = Path(settings.BASE_DIR) / 'data' / 'planet_stories'
_UA = {'User-Agent': 'landslidescience-archive/1'}

# Slug charset must match the serving route (inventory/urls.py planet_mp4) —
# and, since the slug becomes a filename under ARCHIVE_DIR, this is also the
# traversal guard for the download path.
_SLUG_RE = re.compile(r'^[A-Za-z0-9_-]+$')


def slug_from_url(url):
    """Slug from a Planet Stories URL, or None if it isn't one (or carries a
    slug we couldn't safely use as a filename)."""
    u = (url or '').strip()
    if not u.startswith(STORY_PREFIX):
        return None
    rest = u[len(STORY_PREFIX):].split('?', 1)[0].split('#', 1)[0].rstrip('/')
    return rest if rest and _SLUG_RE.match(rest) else None


def sync_story_link(cur, landslide_id, old_url, new_url):
    """Mirror a planet_story_link change into the N:M tables (the same
    semantics the manage_edit inline block used to have). Caller owns the
    transaction. Returns the newly-linked slug (for ensure_archived_async
    after commit) or None when nothing new was linked."""
    old_slug, new_slug = slug_from_url(old_url), slug_from_url(new_url)
    if old_slug == new_slug:
        return None
    if old_slug:
        cur.execute(
            "DELETE FROM landslide_planet_stories WHERE landslide_id = %s AND slug = %s",
            (landslide_id, old_slug))
    if new_slug:
        cur.execute(
            "INSERT INTO planet_stories (slug) VALUES (%s) ON CONFLICT (slug) DO NOTHING",
            (new_slug,))
        cur.execute(
            "INSERT INTO landslide_planet_stories (landslide_id, slug) "
            "VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (landslide_id, new_slug))
    return new_slug


def ensure_story_rows(cur, landslide_id, url):
    """Additive variant for the import path: make sure the N:M rows exist for
    a record's current link (no delete of other memberships). Returns the
    slug when rows were ensured."""
    slug = slug_from_url(url)
    if not slug:
        return None
    cur.execute(
        "INSERT INTO planet_stories (slug) VALUES (%s) ON CONFLICT (slug) DO NOTHING",
        (slug,))
    cur.execute(
        "INSERT INTO landslide_planet_stories (landslide_id, slug) "
        "VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (landslide_id, slug))
    return slug


def ensure_archived_async(slug):
    """Probe + archive `slug` in a daemon thread. Safe to call with None."""
    if not slug:
        return
    threading.Thread(target=_ensure_archived, args=(slug,),
                     daemon=True, name=f'planet-archive-{slug}').start()


def _ensure_archived(slug):
    """Worker: classify the slug, download the MP4 when warranted, stamp the
    planet_stories row. Never raises — a network hiccup leaves story_type
    NULL / mp4 unarchived, and the batch commands pick it up later."""
    from django.db import connections
    try:
        _probe_and_archive(slug)
    except Exception:
        log.exception('planet archive hook failed for %s', slug)
    finally:
        connections.close_all()


def _probe_and_archive(slug):
    from .views import _get_conn, _put_conn   # lazy — avoids an import cycle

    url = GCS_URL.format(slug=slug)
    dest = ARCHIVE_DIR / f'{slug}.mp4'

    # Classify: MP4 exists on GCS → timelapse; 404 → comparison (Planet's SPA
    # wiper widget, nothing to archive). Other errors → leave NULL for the
    # batch probe to retry.
    story_type = None
    if dest.exists():
        story_type = 'timelapse'
    else:
        try:
            req = urllib.request.Request(url, method='HEAD', headers=_UA)
            with urllib.request.urlopen(req, timeout=20) as resp:
                if resp.status == 200:
                    story_type = 'timelapse'
        except urllib.error.HTTPError as e:
            if e.code == 404:
                story_type = 'comparison'
        except Exception:
            pass

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE planet_stories SET story_type = COALESCE(story_type, %s), "
            "last_probed_at = now() WHERE slug = %s", (story_type, slug))
        conn.commit()
    finally:
        _put_conn(conn)

    if story_type != 'timelapse':
        return

    if not dest.exists():
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix('.mp4.part')
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, 'wb') as f:
                shutil.copyfileobj(resp, f, 64 * 1024)
            tmp.rename(dest)
        except Exception:
            if tmp.exists():
                tmp.unlink()
            raise
        log.info('planet story %s archived (%d bytes)', slug, dest.stat().st_size)

    size = dest.stat().st_size
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE planet_stories SET story_type = COALESCE(story_type, 'timelapse'), "
            "mp4_archived_at = COALESCE(mp4_archived_at, now()), "
            "mp4_size_bytes = %s WHERE slug = %s", (size, slug))
        conn.commit()
    finally:
        _put_conn(conn)
