"""Purge the OPERA velocity tile cache (data/opera_tiles/).

Run when ASF refreshes their velocity mosaic so the proxy re-fetches fresh
tiles on demand. Bump OPERA_TILE_V in map.js in the same change so browser
caches roll over too (the proxy serves 30-day cache headers).

  python manage.py purge_opera_tiles [--track asc|desc] [--dry-run]
"""
import shutil

from django.core.management.base import BaseCommand, CommandError

from inventory.opera import TILES_DIR, TRACKS


class Command(BaseCommand):
    help = 'Clear the cached OPERA velocity tiles so the proxy re-fetches.'

    def add_arguments(self, parser):
        parser.add_argument('--track', choices=TRACKS, help='Only this track.')
        parser.add_argument('--dry-run', action='store_true', help='Report only.')

    def handle(self, *args, **opts):
        tracks = [opts['track']] if opts['track'] else list(TRACKS)
        for track in tracks:
            d = TILES_DIR / track
            if not d.exists():
                self.stdout.write(f'{track}: no cache.')
                continue
            files = [p for p in d.rglob('*') if p.is_file()]
            size = sum(p.stat().st_size for p in files)
            self.stdout.write(f'{track}: {len(files)} files, {size/1e6:.1f} MB')
            if not opts['dry_run']:
                shutil.rmtree(d)
                self.stdout.write(self.style.SUCCESS(f'{track}: purged.'))
        if opts['dry_run']:
            self.stdout.write(self.style.WARNING('--dry-run: nothing deleted.'))
        else:
            self.stdout.write('Remember to bump OPERA_TILE_V in map.js if the '
                              'mosaic content changed.')
