"""Make a drop's rows agree with what is actually in the bucket.

A DropFile row is normally written when the browser reports a finished
upload (`api_record`) or when the server completes a multipart upload. The
first of those is a second request after the bytes are safe, and it can be
lost: the collaborator's connection hiccups, the tab closes, the HEAD to R2
fails once. The object is then in the bucket with no row, the gallery
undercounts, and `tools/drops/r2_pull.sh` quietly mirrors more files than
the site admits to. The Portage game-cam drop (2026-10-05) arrived with 37
such orphans out of 5,183.

`reconcile(drop)` is the repair: list the drop's prefix (one request per
1,000 objects), create rows for objects without one, refresh rows whose
size or ETag differs from the object, and report rows whose object is gone.
It runs at the end of every upload session (`api_reconcile`, called by
upload.js), from the editor's `/drops/` page, and as `manage.py
drops_reconcile`. The listing's own size/ETag/LastModified are trusted, so
there is no per-object HEAD. Rows are only ever deleted with `prune=True`;
nothing here touches bytes.
"""
import logging

from django.utils import timezone

from . import r2, thumbs
from .models import DropFile, PathError

log = logging.getLogger(__name__)


def reconcile(drop, prune=False, dry_run=False):
    """Returns {'objects', 'added', 'updated', 'missing', 'pruned', 'ignored'};
    'missing' is the list of row paths with no object behind them."""
    rows = {f.path: f for f in drop.files.all()}
    seen = set()
    added = updated = ignored = 0
    objects = 0
    for o in r2.list_prefix(drop.prefix):
        if o['key'].startswith(drop.thumb_prefix):
            continue
        objects += 1
        try:
            rel = drop.rel_path_of(o['key'])
        except PathError:
            ignored += 1                       # not a key this app would have written
            continue
        seen.add(rel)
        row = rows.get(rel)
        if row is None:
            added += 1
            if not dry_run:
                DropFile.objects.create(
                    drop=drop, path=rel, key=o['key'], size=o['size'], etag=o['etag'],
                    uploaded_at=o['modified'] or timezone.now(), thumb_state='pending')
        elif row.size != o['size'] or (o['etag'] and row.etag and row.etag != o['etag']):
            updated += 1
            if not dry_run:
                # the bytes changed under the row, so the thumbnail is stale too
                DropFile.objects.filter(pk=row.pk).update(
                    size=o['size'], etag=o['etag'], uploaded_at=o['modified'] or timezone.now(),
                    thumb_state='pending', thumb_key='', thumb_error='')
    missing = sorted(set(rows) - seen)
    pruned = 0
    if prune and missing and not dry_run:
        pruned = drop.files.filter(path__in=missing).delete()[0]
    if (added or updated) and not dry_run:
        thumbs.kick()
    result = {'objects': objects, 'added': added, 'updated': updated,
              'missing': missing, 'pruned': pruned, 'ignored': ignored}
    if added or updated or missing:
        log.info('reconcile %s: %s', drop.slug, {k: (len(v) if isinstance(v, list) else v)
                                                 for k, v in result.items()})
    return result
