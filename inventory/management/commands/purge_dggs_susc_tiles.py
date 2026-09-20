"""Clear the cached Alaska DGGS susceptibility tiles.

Use when DGGS republishes the raster, or when a stretch of tiles was cached
during one of their partial outages. Bump DGGS_SUSC_V in map.js at the same
time, or browsers keep serving the old bytes for the 30 days the tile route
promised them.
"""
import shutil

from django.core.management.base import BaseCommand

from inventory.susc_dggs import TILES_DIR


class Command(BaseCommand):
    help = 'Delete the cached DGGS susceptibility tiles'

    def add_arguments(self, parser):
        parser.add_argument('--yes', action='store_true',
                            help='skip the confirmation prompt')

    def handle(self, *args, **opts):
        if not TILES_DIR.exists():
            self.stdout.write('nothing cached at %s' % TILES_DIR)
            return
        n = sum(1 for _ in TILES_DIR.rglob('*.png'))
        m = sum(1 for _ in TILES_DIR.rglob('*.404'))
        if not opts['yes']:
            self.stdout.write('%d tiles + %d empty-area markers in %s' % (n, m, TILES_DIR))
            if input('delete them? [y/N] ').strip().lower() not in ('y', 'yes'):
                self.stdout.write('left alone')
                return
        shutil.rmtree(TILES_DIR)
        self.stdout.write(self.style.SUCCESS(
            'removed %d tiles + %d markers; bump DGGS_SUSC_V in map.js' % (n, m)))
