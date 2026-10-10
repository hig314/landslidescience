"""Thumbnails + EXIF for drop files, made on the droplet one file at a time.

Light processing only: pull one object from R2 into memory, read its EXIF
date, write a ~400 px JPEG back beside it under `.thumbs/`. Anything heavier
(time-lapse assembly, SfM) runs on the Mac against a `tools/drops/r2_pull.sh`
mirror — the droplet has ~7 GB of disk and shares 4 GB of RAM.

One worker thread per process, started on demand (`kick()`), draining every
`pending` row and exiting. Production runs two gunicorn workers, so a row is
CLAIMED with an atomic conditional UPDATE before it is processed; whichever
process loses the race just moves to the next row.
"""
import io
import logging
import threading

from django.db import close_old_connections

from . import r2
from .models import DropFile

log = logging.getLogger(__name__)

THUMB_LONG_EDGE = 400
THUMB_QUALITY = 82
MAX_BYTES_IN_MEMORY = 80 * 1024 * 1024    # a RAW or a big TIFF is skipped, not loaded

_lock = threading.Lock()
_thread = None


def kick():
    """Start the drain thread if none is running."""
    global _thread
    if not r2.configured():
        return
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_drain, daemon=True, name='drop-thumbs')
        _thread.start()


def _drain():
    try:
        run(limit=None)
    except Exception:
        log.exception('drop thumbnail worker died')
    finally:
        close_old_connections()


def run(drop=None, limit=None, states=('pending',)):
    """Process rows synchronously. Returns the number processed."""
    n = 0
    while limit is None or n < limit:
        qs = DropFile.objects.filter(thumb_state__in=states).order_by('pk')
        if drop is not None:
            qs = qs.filter(drop=drop)
        row = qs.first()
        if row is None:
            return n
        # Atomic claim: only one process gets rowcount 1.
        if not DropFile.objects.filter(pk=row.pk, thumb_state=row.thumb_state) \
                               .update(thumb_state='working'):
            continue
        process_one(row)
        n += 1
    return n


def _parse_taken_at(s):
    """'YYYY-MM-DD HH:MM:SS' (from inventory.photos._exif_taken_at) → aware UTC."""
    from datetime import datetime, timezone as tz
    try:
        return datetime.strptime(s, '%Y-%m-%d %H:%M:%S').replace(tzinfo=tz.utc)
    except (TypeError, ValueError):
        return None


def process_one(row):
    """Thumbnail + EXIF for one row; always leaves it in a terminal state."""
    update = {'thumb_error': ''}
    try:
        if not row.is_image:
            update['thumb_state'] = 'skip'
            return
        if row.size > MAX_BYTES_IN_MEMORY:
            update['thumb_state'] = 'skip'
            update['thumb_error'] = 'too large to thumbnail on the server'
            return
        from PIL import Image, ImageOps
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            pass
        from inventory.photos import _exif_taken_at

        data = r2.get_bytes(row.key)
        img = Image.open(io.BytesIO(data))
        taken = _parse_taken_at(_exif_taken_at(img.getexif()))
        img = ImageOps.exif_transpose(img)
        update['width'], update['height'] = img.size
        update['taken_at'] = taken
        img = img.convert('RGB')
        img.thumbnail((THUMB_LONG_EDGE, THUMB_LONG_EDGE))
        out = io.BytesIO()
        img.save(out, 'JPEG', quality=THUMB_QUALITY, optimize=True)
        thumb_key = f'{row.drop.thumb_prefix}{row.pk}.jpg'
        r2.put_bytes(thumb_key, out.getvalue(), 'image/jpeg')
        update['thumb_key'] = thumb_key
        update['thumb_state'] = 'done'
    except Exception as e:                      # noqa: BLE001 — terminal state matters more
        log.warning('thumbnail failed for %s: %s', row.key, e)
        update['thumb_state'] = 'error'
        update['thumb_error'] = str(e)[:300]
    finally:
        DropFile.objects.filter(pk=row.pk).update(**update)


def reset(drop=None, states=('error',)):
    """Put rows back to pending (re-run after fixing something)."""
    qs = DropFile.objects.filter(thumb_state__in=states)
    if drop is not None:
        qs = qs.filter(drop=drop)
    return qs.update(thumb_state='pending', thumb_error='')
