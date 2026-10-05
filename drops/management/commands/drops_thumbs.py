"""Make thumbnails + read EXIF for drop files, synchronously.

    python manage.py drops_thumbs                 # every pending row
    python manage.py drops_thumbs --drop <slug>   # one drop
    python manage.py drops_thumbs --retry-errors  # put 'error' rows back first
    python manage.py drops_thumbs --stalled       # 'working' rows left by a restart

The upload flow kicks a background thread that does the same thing; this is
the CLI equivalent for when a container restart interrupted it, or after a
fix to the thumbnail code (like rebuild_trace_rasters).
"""
from django.core.management.base import BaseCommand, CommandError

from drops import r2, thumbs
from drops.models import Drop


class Command(BaseCommand):
    help = 'Thumbnail + EXIF pass over drop files.'

    def add_arguments(self, parser):
        parser.add_argument('--drop', help='slug of one drop')
        parser.add_argument('--retry-errors', action='store_true')
        parser.add_argument('--stalled', action='store_true',
                            help="also process rows stuck in 'working'")

    def handle(self, *args, **opts):
        if not r2.configured():
            raise CommandError('DROPS_R2_* is not set in the environment.')
        drop = None
        if opts['drop']:
            try:
                drop = Drop.objects.get(slug=opts['drop'])
            except Drop.DoesNotExist:
                raise CommandError(f'no drop {opts["drop"]!r}')
        if opts['retry_errors']:
            n = thumbs.reset(drop, states=('error',))
            self.stdout.write(f'{n} error rows reset to pending')
        states = ('pending', 'working') if opts['stalled'] else ('pending',)
        n = thumbs.run(drop=drop, states=states)
        self.stdout.write(self.style.SUCCESS(f'{n} files processed'))
