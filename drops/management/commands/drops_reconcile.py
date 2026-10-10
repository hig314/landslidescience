"""Make drop rows agree with the bucket (see drops/reconcile.py).

    python manage.py drops_reconcile                  # every drop
    python manage.py drops_reconcile --drop <slug>
    python manage.py drops_reconcile --dry-run        # report, write nothing
    python manage.py drops_reconcile --prune          # also delete rows whose object is gone

Objects without a row get one (and a thumbnail, via the usual worker); rows
whose object changed size are refreshed; rows whose object is gone are
listed, and removed only with --prune. Nothing here touches bytes.
"""
from django.core.management.base import BaseCommand, CommandError

from drops import r2
from drops.models import Drop
from drops.reconcile import reconcile


class Command(BaseCommand):
    help = 'Create rows for bucket objects the app never recorded; report the reverse.'

    def add_arguments(self, parser):
        parser.add_argument('--drop', help='slug of one drop (default: all)')
        parser.add_argument('--prune', action='store_true', help='delete rows with no object behind them')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        if not r2.configured():
            raise CommandError('DROPS_R2_* is not set in the environment.')
        qs = Drop.objects.all()
        if opts['drop']:
            qs = qs.filter(slug=opts['drop'])
            if not qs.exists():
                raise CommandError(f'no drop {opts["drop"]!r}')
        for d in qs:
            r = reconcile(d, prune=opts['prune'], dry_run=opts['dry_run'])
            tag = '(dry run) ' if opts['dry_run'] else ''
            self.stdout.write(
                f'{tag}{d.slug} {d.title!r}: {r["objects"]} objects in bucket, '
                f'{r["added"]} rows added, {r["updated"]} refreshed, '
                f'{len(r["missing"])} rows without an object'
                + (f', {r["pruned"]} pruned' if r['pruned'] else '')
                + (f', {r["ignored"]} keys ignored' if r['ignored'] else ''))
            for p in r['missing'][:20]:
                self.stdout.write(f'    missing object: {p}')
            if len(r['missing']) > 20:
                self.stdout.write(f'    … and {len(r["missing"]) - 20} more')
