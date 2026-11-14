"""Back up what exists only on the droplet to the private R2 bucket.

    python manage.py site_backup [--dry-run] [--only sqlite,media,...]

What it copies, under `backups/` in the drops bucket (private; the droplet's
existing DROPS_R2_* token, so no new credential):

  sqlite     data/db.sqlite3, as a consistent snapshot (SQLite's own backup
             API, safe while the site is writing), gzipped, one per day:
             backups/sqlite/db-YYYY-MM-DD.sqlite3.gz. Accounts, the audit
             log, review history, drops records, saved comparisons, QMS and
             trace-raster registries.
  staging    anything in data/backup_staging/ (the cron wrapper puts a
             pg_dump of the PostGIS landslide tables there), uploaded to
             backups/postgis/ and then removed locally.
  media      data/media/  — field photos (originals are irreplaceable),
             hosted files, trace-raster originals.
  traces     data/trace_tiles/**/*.pmtiles — pre-baked uploads have no
             original on the droplet. PNG pyramids are re-bakeable, skipped.
  snapshots  data/snapshots/ — the citable published record.
  planet     data/planet_stories/ — archived Planet Stories MP4s; the
             archive is the only copy we control.

Dated dumps (sqlite, postgis) are thinned: every day for 14 days, then the
first of each month. File trees are copied incrementally (a file is sent
when the bucket has no object of that key and size) and **nothing is ever
deleted from the bucket for them** — a file removed on the droplet stays in
the backup, which is the point.

Not covered, on purpose: anything rebuildable (tile caches, pyramids,
data/quantities, data/permafrost — all built on the Mac and kept on
Nunatak or R2) and the drops themselves (already in this bucket).

Restoring: rclone or any S3 client with the drops token; keys mirror the
paths under data/. `gunzip` the SQLite file into data/db.sqlite3 with the
container stopped.

The token on the droplet can delete as well as write, so this protects
against disk loss and mistakes, not against someone who holds the droplet.
Pulling backups/ to Nunatak now and then (tools/drops/r2_pull.sh style)
covers that.
"""
import datetime
import gzip
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from drops import r2

PREFIX = 'backups/'
KEEP_DAILY = 14
PARTS = ('sqlite', 'staging', 'media', 'traces', 'snapshots', 'planet')


def _listing(client, prefix):
    """{key: size} for everything under prefix."""
    out = {}
    for page in client.get_paginator('list_objects_v2').paginate(Bucket=r2.bucket(), Prefix=prefix):
        for o in page.get('Contents', []):
            out[o['Key']] = o['Size']
    return out


class Command(BaseCommand):
    help = 'Copy droplet-only data (SQLite, photos, uploads, snapshots) to the private R2 bucket.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--from-dev', action='store_true',
                            help='allow a real run with DEBUG on (dev shares the bucket with prod)')
        parser.add_argument('--only', default=','.join(PARTS),
                            help='comma-separated subset of: ' + ', '.join(PARTS))

    def handle(self, *args, **o):
        if not r2.configured():
            raise CommandError('DROPS_R2_* is not configured.')
        if settings.DEBUG and not o['dry_run'] and not o['from_dev']:
            raise CommandError('This is a dev environment and the bucket is shared with production: '
                               'a real run would overwrite prod backups with dev data. --dry-run, '
                               'or --from-dev if that is really meant.')
        only = {p.strip() for p in o['only'].split(',') if p.strip()}
        if only - set(PARTS):
            raise CommandError('unknown part(s): ' + ', '.join(sorted(only - set(PARTS))))
        self.dry = o['dry_run']
        self.c = r2.client()
        self.data = Path(settings.BASE_DIR) / 'data'
        self.sent = self.sent_bytes = 0
        today = datetime.date.today()

        if 'sqlite' in only:
            self._sqlite(today)
        if 'staging' in only:
            self._staging()
        for part, sub, pattern in (('media', 'media', None), ('traces', 'trace_tiles', '.pmtiles'),
                                   ('snapshots', 'snapshots', None), ('planet', 'planet_stories', None)):
            if part in only:
                self._tree(sub, pattern)
        for kind in ('sqlite', 'postgis'):
            if kind == 'sqlite' and 'sqlite' not in only or kind == 'postgis' and 'staging' not in only:
                continue
            self._thin(PREFIX + kind + '/', today)
        self.stdout.write('%s%d file(s), %.1f MB' % ('would send ' if self.dry else 'sent ',
                                                     self.sent, self.sent_bytes / 1e6))

    def _put(self, path, key):
        size = os.path.getsize(path)
        self.sent += 1
        self.sent_bytes += size
        if self.dry:
            self.stdout.write('  would send %s (%.1f MB)' % (key, size / 1e6))
            return
        self.c.upload_file(str(path), r2.bucket(), key)
        head = self.c.head_object(Bucket=r2.bucket(), Key=key)
        if head['ContentLength'] != size:
            raise CommandError('%s: bucket has %d bytes, sent %d' % (key, head['ContentLength'], size))

    def _sqlite(self, today):
        src = settings.DATABASES['default']['NAME']
        key = '%ssqlite/db-%s.sqlite3.gz' % (PREFIX, today.isoformat())
        with tempfile.TemporaryDirectory() as tmp:
            snap = os.path.join(tmp, 'db.sqlite3')
            a = sqlite3.connect('file:%s?mode=ro' % src, uri=True)
            b = sqlite3.connect(snap)
            with b:
                a.backup(b)
            ok = b.execute('PRAGMA integrity_check').fetchone()[0]
            a.close(); b.close()
            if ok != 'ok':
                raise CommandError('SQLite snapshot failed its integrity check: %s' % ok)
            gz = snap + '.gz'
            with open(snap, 'rb') as f, gzip.open(gz, 'wb', compresslevel=6) as g:
                shutil.copyfileobj(f, g)
            self._put(gz, key)

    def _staging(self):
        d = self.data / 'backup_staging'
        if not d.is_dir():
            return
        for p in sorted(d.iterdir()):
            if p.is_file() and not p.name.endswith('.part'):
                self._put(p, PREFIX + 'postgis/' + p.name)
                if not self.dry:
                    p.unlink()

    def _tree(self, sub, suffix):
        root = self.data / sub
        if not root.is_dir():
            return
        have = _listing(self.c, PREFIX + sub + '/')
        n = 0
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if suffix and not name.endswith(suffix):
                    continue
                p = Path(dirpath) / name
                key = PREFIX + sub + '/' + p.relative_to(root).as_posix()
                if have.get(key) != p.stat().st_size:
                    self._put(p, key)
                    n += 1
        self.stdout.write('%s: %d new or changed' % (sub, n))

    def _thin(self, prefix, today):
        """Keep every dump from the last KEEP_DAILY days and each month's first."""
        import re
        firsts = {}
        doomed = []
        for key in sorted(_listing(self.c, prefix)):
            m = re.search(r'(\d{4})-(\d{2})-(\d{2})', key)
            if not m:
                continue
            day = datetime.date(*map(int, m.groups()))
            if (today - day).days <= KEEP_DAILY:
                continue
            month = (day.year, day.month)
            if month not in firsts:
                firsts[month] = key          # sorted, so the month's earliest
            else:
                doomed.append(key)
        for key in doomed:
            self.stdout.write('  %s %s' % ('would thin' if self.dry else 'thinned', key))
            if not self.dry:
                self.c.delete_object(Bucket=r2.bucket(), Key=key)
