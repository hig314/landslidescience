"""Is it safe to recreate the web container right now?

Run on production, in the container that is about to be replaced, between
the image build and `up -d --force-recreate`:

  docker exec landslidescience-web python manage.py deploy_ready

Exit 1 = something is running inside this container that a restart would
kill. Today that is one thing: a trace-raster bake (an editor's GeoTIFF
upload or a Sentinel fetch), which runs in a thread of a gunicorn worker.
The 2026-11-14 deploy killed one nine minutes in; the row sat at
'processing' until it was re-baked by hand.

A row counts as running when it is 'processing' and showed a sign of life
(row created, bake started, or a tile written) in the last ACTIVE_MINUTES.
An older 'processing' row is reported but does not block: its thread is
already gone, and `rebuild_trace_rasters --stalled` after the deploy is the
fix. `--wait N` polls for up to N minutes instead of failing at once.
"""
import datetime
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

ACTIVE_MINUTES = 30


def _last_sign_of_life(row):
    from inventory import raster_tiles
    latest = row.created_at.timestamp()
    root = raster_tiles.tiles_dir(row.pk)
    if root.exists():
        # Newest directory or marker: a tile write touches its folder, so this
        # avoids a stat of every tile in a pyramid of thousands.
        for p in root.rglob('*'):
            if p.is_dir() or p.name.startswith('.'):
                try:
                    latest = max(latest, p.stat().st_mtime)
                except OSError:
                    pass
    return datetime.datetime.fromtimestamp(latest, tz=datetime.timezone.utc)


class Command(BaseCommand):
    help = 'Exit 1 if a restart of this container would interrupt running work.'

    def add_arguments(self, parser):
        parser.add_argument('--wait', type=int, default=0, metavar='MINUTES',
                            help='Poll until clear, for at most this many minutes.')

    def _scan(self):
        from inventory.models import TraceRaster
        running, stale = [], []
        cutoff = timezone.now() - datetime.timedelta(minutes=ACTIVE_MINUTES)
        for row in TraceRaster.objects.filter(status=TraceRaster.STATUS_PROCESSING):
            seen = _last_sign_of_life(row)
            (running if seen >= cutoff else stale).append((row, seen))
        return running, stale

    def handle(self, *args, **opts):
        deadline = time.time() + 60 * opts['wait']
        while True:
            running, stale = self._scan()
            if not running or time.time() >= deadline:
                break
            self.stdout.write('waiting: %s still baking' % ', '.join(
                '#%d %s' % (r.pk, r.title) for r, _ in running))
            time.sleep(20)

        now = timezone.now()
        for row, seen in stale:
            self.stdout.write(
                'note: trace raster #%d "%s" says processing but has been silent '
                'for %d min; its bake is gone. After the deploy: '
                'rebuild_trace_rasters --stalled'
                % (row.pk, row.title, (now - seen).total_seconds() // 60))
        if running:
            for row, seen in running:
                self.stdout.write(
                    'BUSY  trace raster #%d "%s" is baking (last activity %d s ago)'
                    % (row.pk, row.title, (now - seen).total_seconds()))
            self.stdout.write('Do not recreate the container yet. Re-run this, '
                              'or use --wait 30.')
            raise SystemExit(1)
        self.stdout.write('ok    nothing running that a restart would interrupt')
