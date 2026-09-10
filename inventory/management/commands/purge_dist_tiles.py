"""Purge the OPERA DIST tile cache (data/dist_tiles/).

The cache is keyed by date, so it does NOT normally need purging: a new date
is a new path, and only the FRESH_DAYS window re-fetches on its own. Reach for
this when GIBS reprocesses a past date, when the cache has simply grown too
big, or to drop the cached date domain after an upstream change.

  python manage.py purge_dist_tiles [--layer alert|ann] [--before YYYY-MM-DD]
                                    [--domains] [--dry-run]

--before keeps the recent dates and drops everything older, which is the usual
way to reclaim space after a long animation session. Bump DIST_TILE_V in
map.js in the same change if the CONTENT of a date changed (the proxy serves
30-day cache headers for settled dates, so browsers hold them otherwise).
"""
import datetime
import shutil

from django.core.management.base import BaseCommand, CommandError

from inventory.dist import LAYERS, TILES_DIR


def _size(paths):
    return sum(p.stat().st_size for p in paths if p.is_file())


class Command(BaseCommand):
    help = 'Clear cached OPERA DIST tiles so the proxy re-fetches.'

    def add_arguments(self, parser):
        parser.add_argument('--layer', choices=sorted(LAYERS), help='Only this layer.')
        parser.add_argument('--before', metavar='YYYY-MM-DD',
                            help='Only date directories strictly older than this.')
        parser.add_argument('--domains', action='store_true',
                            help='Also drop the cached GIBS date domain.')
        parser.add_argument('--dry-run', action='store_true', help='Report only.')

    def handle(self, *args, **opts):
        cutoff = None
        if opts['before']:
            try:
                cutoff = datetime.date.fromisoformat(opts['before'])
            except ValueError:
                raise CommandError('--before must be YYYY-MM-DD')

        layers = [opts['layer']] if opts['layer'] else sorted(LAYERS)
        total_files = total_bytes = 0
        for layer in layers:
            root = TILES_DIR / layer
            if not root.exists():
                self.stdout.write(f'{layer}: no cache.')
                continue
            for datedir in sorted(p for p in root.iterdir() if p.is_dir()):
                if cutoff is not None:
                    try:
                        if datetime.date.fromisoformat(datedir.name) >= cutoff:
                            continue
                    except ValueError:
                        # _all_<sig> holds merged annual composites, which have
                        # no single date. --before is about reclaiming space
                        # from a long animation session, so leave them.
                        continue
                files = [p for p in datedir.rglob('*') if p.is_file()]
                nbytes = _size(files)
                total_files += len(files)
                total_bytes += nbytes
                self.stdout.write(f'{layer}/{datedir.name}: '
                                  f'{len(files)} files, {nbytes/1e6:.1f} MB')
                if not opts['dry_run']:
                    shutil.rmtree(datedir)

        if opts['domains']:
            cache = TILES_DIR / '_domains.json'
            if cache.exists():
                self.stdout.write('dropping cached date domain')
                if not opts['dry_run']:
                    cache.unlink()

        self.stdout.write(f'total: {total_files} files, {total_bytes/1e6:.1f} MB')
        if opts['dry_run']:
            self.stdout.write(self.style.WARNING('--dry-run: nothing deleted.'))
        else:
            self.stdout.write(self.style.SUCCESS('purged.'))
            self.stdout.write('Bump DIST_TILE_V in map.js if a date\'s CONTENT '
                              'changed upstream.')
