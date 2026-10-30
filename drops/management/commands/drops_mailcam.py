"""File emailed game-camera photos into a drop (see drops/mailcam.py).

    python manage.py drops_mailcam --drop <slug>            # new mail since last run
    python manage.py drops_mailcam --drop <slug> --all      # rescan the whole inbox
    python manage.py drops_mailcam --drop <slug> --dry-run  # report, write nothing

Needs CAMFEED_IMAP_HOST/PORT/USER/PASSWORD in the environment as well as
DROPS_R2_*. On production tools/drops/mailcam_cron.sh runs it every 15
minutes. Thumbnails are made before it exits, since a background thread
would die with the command.
"""
from django.core.management.base import BaseCommand, CommandError

from drops import mailcam, r2, thumbs
from drops.models import Drop


class Command(BaseCommand):
    help = 'Import game-camera photos from the camfeed@ mailbox into a drop.'

    def add_arguments(self, parser):
        parser.add_argument('--drop', required=True, help='slug of the drop to file into')
        parser.add_argument('--all', action='store_true',
                            help='ignore the watermark and rescan every message')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        if not r2.configured():
            raise CommandError('DROPS_R2_* is not set in the environment.')
        try:
            drop = Drop.objects.get(slug=opts['drop'])
        except Drop.DoesNotExist:
            raise CommandError(f'no drop {opts["drop"]!r}')
        try:
            r = mailcam.run(drop, rescan=opts['all'], dry_run=opts['dry_run'],
                            out=self.stdout.write)
        except mailcam.ConfigError as e:
            raise CommandError(str(e))
        tag = '(dry run) ' if opts['dry_run'] else ''
        n = 0 if opts['dry_run'] else thumbs.run(drop=drop)
        self.stdout.write(f'{tag}{drop.slug}: {r["seen"]} messages read, {r["added"]} photos filed, '
                          f'{r["refused"]} refused, {n} thumbnailed')
