"""Load glacier identity (RGI 7.0) and mirrored extent sources.

    python manage.py fetch_glacier_source --list
    python manage.py fetch_glacier_source rgi7          # the identity table
    python manage.py fetch_glacier_source centerlines   # RGI 7.0 flow lines (reference)
    python manage.py fetch_glacier_source glims
    python manage.py fetch_glacier_source --all         # every extent source
    python manage.py fetch_glacier_source glims --dry-run
    python manage.py fetch_glacier_source --relink      # re-match rows to glaciers

Run `rgi7` first: extent rows are matched to glaciers as they load. After a
later `rgi7` refetch, `--relink` re-matches without refetching anything.

A fetch replaces the source inside one transaction and is count-checked, so
a fetch that dies or comes back short leaves the previous copy in place.

The file-based sources (NSIDC-0634 and the two USGS releases) are read from
data/glaciers/sources/*.geojsonl, which tools/glaciers/download_sources.sh
and prep_sources.sh build on the workstation.
"""
from django.core.management.base import BaseCommand, CommandError

from glaciers import sources


class Command(BaseCommand):
    help = 'Load RGI 7.0 glaciers and mirrored glacier-extent sources'

    def add_arguments(self, p):
        p.add_argument('source', nargs='?', help="'rgi7' or a source id")
        p.add_argument('--all', action='store_true', help='every extent source')
        p.add_argument('--list', action='store_true')
        p.add_argument('--relink', action='store_true',
                       help='re-match rows to glaciers; fetch nothing')
        p.add_argument('--dry-run', action='store_true', help='fetch and report, write nothing')

    def handle(self, *a, **o):
        if o['list']:
            self.stdout.write('rgi7\n    %s\n    doi:%s' % (sources.RGI7['title'], sources.RGI7['doi']))
            for sid, s in sources.SOURCES.items():
                self.stdout.write('%s\n    %s\n    doi:%s' % (sid, s['title'], s['doi']))
            return
        # sources with no fetch (S1 auto faces) are loaded by their own command
        ids = [k for k, s in sources.SOURCES.items() if s['fetch']] if (o['all'] or (o['relink'] and not o['source'])) \
            else ([o['source']] if o['source'] else [])
        if ids and ids[0] in sources.SOURCES and not sources.SOURCES[ids[0]]['fetch']:
            raise CommandError('%s is not fetched; it is loaded by manage.py s1_videos faces' % ids[0])
        if not ids:
            raise CommandError('give a source id, rgi7, --all, or --list')
        for sid in ids:
            if sid not in ('rgi7', 'centerlines') and sid not in sources.SOURCES:
                raise CommandError('unknown source %r' % sid)
        from inventory.views import _get_conn, _put_conn
        log = self.stdout.write
        for sid in ids:
            conn = _get_conn()
            try:
                with conn.cursor() as cur:
                    if o['relink']:
                        if sid in ('rgi7', 'centerlines'):
                            continue
                        sources.link(cur, sid)
                        cur.execute('SELECT count(*), count(glacier_id) FROM glacier_obs WHERE source=%s', (sid,))
                        n, nl = cur.fetchone()
                        cur.execute('UPDATE glacier_sources SET n_linked=%s WHERE source=%s', (nl, sid))
                        log('%s: %d of %d rows matched to a glacier' % (sid, nl, n))
                    elif sid == 'rgi7':
                        n = sources.load_rgi(cur, log, dry=o['dry_run'])
                        log('rgi7: %d glaciers' % n)
                    elif sid == 'centerlines':
                        n = sources.load_centerlines(cur, log, dry=o['dry_run'])
                        log('centerlines: %d lines' % n)
                    else:
                        rep = sources.load_source(cur, sid, log, dry=o['dry_run'])
                        log(str(rep))
                if o['dry_run']:
                    conn.rollback()
                else:
                    conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                _put_conn(conn)
        if not o['dry_run']:
            from glaciers import views
            views.bump_stamp()
