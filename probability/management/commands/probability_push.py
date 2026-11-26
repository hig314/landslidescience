"""Upload a project's data to the bucket the site reads it from.

    manage.py probability_push homer            # dry run: what would go
    manage.py probability_push homer --write

Reads data/probability/<site>/ (put there by ProbHaz's publish/to_site.py) and
writes probability/<site>/... in the PRIVATE bucket (drops.r2), which is where
an unpublished project is served from (probability/views.py). A file is
skipped when the bucket's copy was uploaded from the same size and
modification time.

There is deliberately no flag here for the public bucket: publishing a
project is a separate, deliberate step.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from probability.projects import PROJECTS


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument('site')
        parser.add_argument('--write', action='store_true')

    def handle(self, site, write, **_):
        from botocore.exceptions import ClientError
        from drops import r2
        if site not in PROJECTS:
            raise CommandError(f'no project "{site}" in probability/projects.py')
        if not r2.configured():
            raise CommandError('DROPS_R2_* is not set')
        root = settings.BASE_DIR / 'data' / 'probability' / site
        files = sorted(f for f in root.rglob('*') if f.is_file() and f.suffix in ('.pmtiles', '.json', '.geojson'))
        if not files:
            raise CommandError(f'nothing under {root}')
        sent = same = 0
        for f in files:
            key = f'probability/{site}/{f.relative_to(root).as_posix()}'
            st = f.stat()
            stamp = f'{st.st_size}-{st.st_mtime_ns}'
            try:
                have = r2.client().head_object(Bucket=r2.bucket(), Key=key).get('Metadata', {}).get('src')
            except ClientError:
                have = None
            if have == stamp:
                same += 1
                continue
            self.stdout.write(f"{'put ' if write else 'would put'} {key}  {st.st_size / 1e6:.1f} MB")
            if write:
                r2.client().upload_file(str(f), r2.bucket(), key, ExtraArgs={'Metadata': {'src': stamp}})
            sent += 1
        self.stdout.write(f"{sent} {'uploaded' if write else 'to upload'}, {same} already there, bucket {r2.bucket()}")
