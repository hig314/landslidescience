"""Cross-process invalidation for the module-level response cache in views.py.

views._cache is a plain dict, one per process. Production runs two gunicorn
workers, so an edit handled by one worker cleared only that worker's copy and
the other went on serving the old features (centroid, class, areas) until a
restart -- a reload of the map showed the edit or not depending on which
worker answered. A stamp file's mtime is the shared clock: _invalidate bumps
it, and CacheStampMiddleware compares it on every request, clearing the
process's cache when another process moved it. One stat per request; the file
lives under data/, which is volume-mounted and writable in dev and prod.
"""
import os
import time
from pathlib import Path

from django.conf import settings

STAMP = Path(settings.BASE_DIR) / 'data' / '.inventory_cache_stamp'


def current():
    try:
        return STAMP.stat().st_mtime_ns
    except OSError:
        return 0


def bump():
    """Move the stamp forward. Returns the new value (0 if data/ is unwritable,
    in which case each process falls back to its own invalidation only)."""
    try:
        STAMP.parent.mkdir(parents=True, exist_ok=True)
        STAMP.touch()
        now = time.time_ns()
        os.utime(STAMP, ns=(now, now))
        return STAMP.stat().st_mtime_ns
    except OSError:
        return 0
