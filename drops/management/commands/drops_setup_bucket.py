"""Set the drops bucket's CORS so browsers can PUT to it from the site.

    python manage.py drops_setup_bucket
    python manage.py drops_setup_bucket --origin https://landslidescience.org --origin http://127.0.0.1:8001

Idempotent (PutBucketCors replaces the whole rule set). Cloudflare only lets
a token that holds bucket-admin rights change CORS; with an object-only
token this prints the JSON to paste into the dashboard instead (R2 →
bucket → Settings → CORS policy).
"""
import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from drops import r2


class Command(BaseCommand):
    help = 'Apply the CORS rules the drop uploader needs to the R2 bucket.'

    def add_arguments(self, parser):
        parser.add_argument('--origin', action='append', default=None,
                            help='Allowed origin (repeatable). Default: the site, plus '
                                 'http://127.0.0.1:8001 when DEBUG.')

    def handle(self, *args, **opts):
        if not r2.configured():
            raise CommandError('DROPS_R2_* is not set in the environment.')
        origins = opts['origin'] or ['https://landslidescience.org']
        if not opts['origin'] and settings.DEBUG:
            origins.append('http://127.0.0.1:8001')
        rules = r2.cors_rules(origins)
        self.stdout.write(f'bucket: {r2.bucket()}\norigins: {", ".join(origins)}')
        try:
            r2.put_cors(origins)
        except Exception as e:                      # noqa: BLE001 — report, then show the manual route
            self.stderr.write(f'PutBucketCors failed: {e}\n\n'
                              'Paste this into the bucket\'s CORS policy in the Cloudflare '
                              'dashboard instead:\n')
            self.stdout.write(json.dumps(rules['CORSRules'], indent=2))
            raise CommandError('CORS not applied')
        self.stdout.write(self.style.SUCCESS('CORS applied.'))
