"""Re-bake trace-raster tile pyramids from their stored originals.

Recovery tool for the two non-terminal failure shapes:
  - a container restart mid-bake (row stuck in 'processing' — "stalled"),
  - a bake that ended in status 'error'.
Also usable after a raster_tiles.py improvement to regenerate everything.

Idempotent; runs the bake synchronously (no thread) so output is visible.

  python manage.py rebuild_trace_rasters --stalled     # stuck + errored rows
  python manage.py rebuild_trace_rasters --id 3        # one specific row
  python manage.py rebuild_trace_rasters --all         # everything
"""
import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = 'Re-bake trace-raster tile pyramids from their stored originals.'

    def add_arguments(self, parser):
        parser.add_argument('--id', type=int, help='Rebuild one raster by id.')
        parser.add_argument('--stalled', action='store_true',
                            help='Rebuild rows stuck in processing (>30 min) or errored.')
        parser.add_argument('--all', action='store_true', help='Rebuild every raster.')

    def handle(self, *args, **opts):
        from inventory import raster_tiles
        from inventory.models import TraceRaster
        from inventory.trace_views import STALL_MINUTES

        if opts['id']:
            qs = TraceRaster.objects.filter(pk=opts['id'])
            if not qs.exists():
                raise CommandError(f'No trace raster with id {opts["id"]}.')
        elif opts['all']:
            qs = TraceRaster.objects.all()
        elif opts['stalled']:
            cutoff = timezone.now() - datetime.timedelta(minutes=STALL_MINUTES)
            qs = TraceRaster.objects.filter(
                status=TraceRaster.STATUS_ERROR
            ) | TraceRaster.objects.filter(
                status=TraceRaster.STATUS_PROCESSING, created_at__lt=cutoff)
        else:
            raise CommandError('Pass one of --id N / --stalled / --all.')

        for r in qs:
            if not (r.original and r.original.storage.exists(r.original.name)):
                self.stdout.write(self.style.WARNING(
                    f'#{r.pk} {r.title}: original missing — skipped (delete + re-upload).'))
                continue
            self.stdout.write(f'#{r.pk} {r.title}: baking …')
            TraceRaster.objects.filter(pk=r.pk).update(
                status=TraceRaster.STATUS_PROCESSING, error_message='')
            raster_tiles.process(r.pk)   # synchronous; never raises
            r.refresh_from_db()
            style = self.style.SUCCESS if r.status == 'ready' else self.style.ERROR
            self.stdout.write(style(f'#{r.pk}: {r.status}'
                                    + (f' — {r.error_message}' if r.error_message else '')))
