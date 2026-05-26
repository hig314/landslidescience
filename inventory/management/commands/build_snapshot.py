"""Build a static-site snapshot of the inventory for a subset.

Produces a self-contained directory at `data/snapshots/<slug>/` that can be
served as-is by the snapshot_serve view. The snapshot is immutable
(content-wise) and citable — it freezes membership AND every field's
value at build time, since all API responses are pre-rendered to JSON
files alongside the HTML.

Architecture: the snapshot's index.html injects `window.LS_CONFIG = {
apiBase: './' }` immediately before map.js, so the same JS that drives
the live site instead reads from local files. Snapshot URLs all resolve
relative to the bundle's base path. The frozen JS / CSS / templates are
copied verbatim at build time, so the snapshot survives any future
schema or UI changes in the live app.

The MP4s in `data/planet_stories/<slug>.mp4` are *not* duplicated into
the snapshot. Snapshots reference them through the load-bearing stable
URL `/inventory/planet/<slug>.mp4`; that file lives in a single
persistent location and is shared across all snapshots.

Usage:
    python manage.py build_snapshot alaska-2025
    python manage.py build_snapshot alaska-2025 --name "Alaska 2025 (paper draft)"
    python manage.py build_snapshot alaska-2025 --slug alaska-2025-rev1 --force
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


# Worldwide bbox so api_polygons returns all polygons (the live endpoint
# requires bbox to keep the unfiltered response off the public surface).
WORLD_BBOX = '-180,-90,180,90'


class Command(BaseCommand):
    help = 'Build a static-site snapshot of the inventory for a named subset.'

    def add_arguments(self, parser):
        parser.add_argument('subset_slug', type=str,
                            help='Slug of the subset to snapshot (e.g. alaska-2025).')
        parser.add_argument('--name', type=str, default=None,
                            help='Human-readable name. Defaults to the subset name.')
        parser.add_argument('--slug', type=str, default=None,
                            help='Snapshot slug. Defaults to the subset slug.')
        parser.add_argument('--description', type=str, default='',
                            help='Free-text description, stored in the snapshot manifest + DB row.')
        parser.add_argument('--citation', type=str, default='',
                            help='Citation info (DOI, paper title, etc.) stored in manifest + DB row.')
        parser.add_argument('--created-by', type=str, default='',
                            help='Name to record as the snapshot creator.')
        parser.add_argument('--force', action='store_true',
                            help='Overwrite an existing snapshot at the same slug.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn

        # ---- 1. resolve subset + members ----
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, slug, name, default_owner, is_publication, citation_info, description
                FROM subsets WHERE slug = %s
            """, (opts['subset_slug'],))
            row = cur.fetchone()
            if not row:
                raise CommandError(f"No subset with slug {opts['subset_slug']!r}.")
            subset = dict(zip(
                ['id', 'slug', 'name', 'default_owner', 'is_publication', 'citation_info', 'description'],
                row,
            ))
            cur.execute("""
                SELECT landslide_id
                FROM landslide_subsets
                WHERE subset_id = %s
                ORDER BY landslide_id
            """, (subset['id'],))
            member_ids = [r[0] for r in cur.fetchall()]
            cur.execute("""
                SELECT COUNT(*) FROM landslide_polygons p
                WHERE p.landslide_id = ANY(%s::int[])
            """, (member_ids,))
            n_polygons = cur.fetchone()[0]
            conn.rollback()
        finally:
            _put_conn(conn)

        if not member_ids:
            raise CommandError(f"Subset {subset['slug']!r} has no members; nothing to snapshot.")

        snap_slug = opts['slug'] or subset['slug']
        snap_name = opts['name'] or subset['name']
        if not re.match(r'^[a-z0-9][a-z0-9-]*$', snap_slug):
            raise CommandError(f"Snapshot slug {snap_slug!r} must be lowercase alphanumeric + hyphens, starting with alphanumeric.")

        archive_root = Path(settings.BASE_DIR) / 'data' / 'snapshots'
        archive_dir = archive_root / snap_slug

        self.stdout.write(f"subset:    {subset['slug']} ({subset['name']})")
        self.stdout.write(f"members:   {len(member_ids)} landslides, {n_polygons} polygons")
        self.stdout.write(f"snap slug: {snap_slug}")
        self.stdout.write(f"snap name: {snap_name}")
        self.stdout.write(f"out dir:   {archive_dir}")

        if archive_dir.exists():
            if not opts['force']:
                raise CommandError(f"{archive_dir} already exists. Re-run with --force to overwrite.")
            self.stdout.write(self.style.WARNING(f"--force: removing existing {archive_dir}"))
            shutil.rmtree(archive_dir)

        archive_dir.mkdir(parents=True)

        # ---- 2. test client with preview bypass ----
        from django.test import Client
        from inventory.middleware import SESSION_KEY as PREVIEW_KEY
        client = Client()
        s = client.session
        s[PREVIEW_KEY] = True
        s.save()

        # Use the first ALLOWED_HOSTS entry so the request passes Django's
        # host header check. 'testserver' (the test client default) isn't in
        # production ALLOWED_HOSTS; '127.0.0.1' isn't either.
        http_host = settings.ALLOWED_HOSTS[0] if settings.ALLOWED_HOSTS else '127.0.0.1'

        def fetch(path):
            r = client.get(path, HTTP_HOST=http_host)
            if r.status_code != 200:
                raise CommandError(f"{path} → HTTP {r.status_code}: {r.content[:200]!r}")
            return r

        # ---- 3. pre-render API responses ----
        # Live api_features supports ?type and ?class filters; for the
        # snapshot we feed a precomputed ID list via a small inline filter
        # at the JSON-level. Simpler: fetch the full unfiltered response
        # and filter by id in Python (member set fits easily in memory).
        member_id_set = set(member_ids)
        self.stdout.write("rendering api/features ...")
        feats = json.loads(fetch('/inventory/api/features/').content)
        feats['features'] = [f for f in feats.get('features') or []
                             if f.get('id') in member_id_set]
        self._write_json(archive_dir / 'api' / 'features' / 'index.json', feats)

        self.stdout.write("rendering api/polygons (worldwide bbox) ...")
        polys = json.loads(fetch(f'/inventory/api/polygons/?bbox={WORLD_BBOX}').content)
        polys['features'] = [p for p in polys.get('features') or []
                             if p.get('properties', {}).get('landslide_id') in member_id_set]
        self._write_json(archive_dir / 'api' / 'polygons' / 'index.json', polys)

        self.stdout.write("rendering api/survey_circles ...")
        circles = json.loads(fetch('/inventory/api/survey_circles/').content)
        self._write_json(archive_dir / 'api' / 'survey_circles' / 'index.json', circles)

        self.stdout.write("rendering api/settings ...")
        sett = json.loads(fetch('/inventory/api/settings/').content)
        self._write_json(archive_dir / 'api' / 'settings' / 'index.json', sett)

        self.stdout.write("rendering api/timed_events ...")
        te = json.loads(fetch('/inventory/api/timed_events/').content)
        te['events'] = [e for e in te.get('events') or [] if e.get('id') in member_id_set]
        self._write_json(archive_dir / 'api' / 'timed_events' / 'index.json', te)

        self.stdout.write("rendering api/timeline_events ...")
        tl = json.loads(fetch('/inventory/api/timeline_events/').content)
        tl['events'] = [e for e in tl.get('events') or [] if e.get('id') in member_id_set]
        self._write_json(archive_dir / 'api' / 'timeline_events' / 'index.json', tl)

        self.stdout.write(f"rendering api/landslide/<id> for {len(member_ids)} members ...")
        for i, lid in enumerate(member_ids, 1):
            d = json.loads(fetch(f'/inventory/api/landslide/{lid}/').content)
            self._write_json(archive_dir / 'api' / 'landslide' / str(lid) / 'index.json', d)
            if i % 200 == 0 or i == len(member_ids):
                self.stdout.write(f"  [{i}/{len(member_ids)}] landslide details written")

        # ---- 4. HTML pages (rewrite static paths + inject LS_CONFIG) ----
        # base is the relative path from the page back to the snapshot root.
        # Used to rewrite absolute /inventory/... and /static/... references
        # into snapshot-local paths from each page's location.
        ls_config = {'apiBase': './'}
        config_script = ('<script>window.LS_CONFIG = '
                         + json.dumps(ls_config, separators=(',', ':'))
                         + ';</script>\n')

        build_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        # Snapshot banner — inline-styled so it doesn't depend on the
        # frozen main.css picking up snapshot-specific selectors. The
        # "Live site" link is intentionally absolute so it works even if
        # this bundle is mirrored to a different domain.
        banner = (
            '<div style="background:#fff3cd; border-bottom:1px solid #ffeeba; '
            'padding:5px 14px; font-size:12px; color:#664d03; '
            'font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;">'
            '<strong>Archived view</strong> &middot; ' + snap_name +
            ' &middot; built ' + build_date +
            ' &middot; <a href="https://landslidescience.org/inventory/" '
            'style="color:#664d03; text-decoration:underline; font-weight:600;">'
            'Live site &rarr;</a></div>'
        )

        self.stdout.write("rendering index.html ...")
        home_html = fetch('/inventory/').content.decode('utf-8')
        home_html = self._rewrite_html(home_html, base='./',
                                       config_script=config_script,
                                       banner=banner)
        (archive_dir / 'index.html').write_text(home_html, encoding='utf-8')

        self.stdout.write("rendering methods.html ...")
        methods_html = fetch('/inventory/methods/').content.decode('utf-8')
        methods_html = self._rewrite_html(methods_html, base='./', banner=banner)
        (archive_dir / 'methods.html').write_text(methods_html, encoding='utf-8')

        # Rule pages — list + per-rule detail. Public view-only; the apply
        # button is editor-gated so it doesn't appear in the snapshot build.
        self.stdout.write("rendering rules/index.html + per-rule detail ...")
        rules_html = fetch('/inventory/rules/').content.decode('utf-8')
        rules_html = self._rewrite_html(rules_html, base='../', banner=banner)
        rules_dir = archive_dir / 'rules'
        rules_dir.mkdir()
        (rules_dir / 'index.html').write_text(rules_html, encoding='utf-8')

        from inventory import derived
        for rule_name in derived.RULES.keys():
            detail_html = fetch(f'/inventory/rules/{rule_name}/').content.decode('utf-8')
            detail_html = self._rewrite_html(detail_html, base='../../', banner=banner)
            d = rules_dir / rule_name
            d.mkdir()
            (d / 'index.html').write_text(detail_html, encoding='utf-8')

        # ---- 5. copy static assets ----
        self.stdout.write("copying static assets ...")
        src_static = Path(settings.BASE_DIR) / 'inventory' / 'static' / 'inventory'
        dst_static = archive_dir / 'static' / 'inventory'
        shutil.copytree(src_static, dst_static)
        # pages/static/pages too (for shared CSS used by base.html — favicon etc.)
        pages_static_src = Path(settings.BASE_DIR) / 'pages' / 'static' / 'pages'
        if pages_static_src.is_dir():
            shutil.copytree(pages_static_src, archive_dir / 'static' / 'pages')

        # ---- 6. manifest.json ----
        manifest = {
            'slug':          snap_slug,
            'name':          snap_name,
            'description':   opts['description'],
            'created_at':    datetime.now(timezone.utc).isoformat(),
            'created_by':    opts['created_by'],
            'subset': {
                'id':            subset['id'],
                'slug':          subset['slug'],
                'name':          subset['name'],
                'is_publication': subset['is_publication'],
            },
            'n_landslides':  len(member_ids),
            'n_polygons':    n_polygons,
            'landslide_ids': member_ids,
            'citation_info': opts['citation'] or (subset['citation_info'] or ''),
            'git_commit':    self._git_commit(),
            'rules':         self._rules_summary(),
            'url_conventions': {
                'planet_mp4':         '/inventory/planet/<slug>.mp4',
                'api_base':           './ (relative to this directory)',
                'api_layout':         'api/<endpoint>/index.json — directory-style trailing-slash URLs',
                'snapshot_served_at': f'/inventory/archive/{snap_slug}/',
            },
            'build_command': ' '.join(sys.argv),
        }
        (archive_dir / 'manifest.json').write_text(
            json.dumps(manifest, indent=2, default=str), encoding='utf-8'
        )

        # ---- 7. insert snapshots row ----
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO snapshots
                    (slug, name, description, created_by, subset_id,
                     n_landslides, n_polygons, archive_dir, citation_info)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (slug) DO UPDATE
                    SET name=EXCLUDED.name,
                        description=EXCLUDED.description,
                        created_by=EXCLUDED.created_by,
                        subset_id=EXCLUDED.subset_id,
                        n_landslides=EXCLUDED.n_landslides,
                        n_polygons=EXCLUDED.n_polygons,
                        archive_dir=EXCLUDED.archive_dir,
                        citation_info=EXCLUDED.citation_info,
                        created_at=now()
            """, (
                snap_slug, snap_name, opts['description'], opts['created_by'],
                subset['id'], len(member_ids), n_polygons,
                f'snapshots/{snap_slug}', manifest['citation_info'],
            ))
            conn.commit()
        finally:
            _put_conn(conn)

        # ---- 8. summary ----
        total_bytes = sum(f.stat().st_size for f in archive_dir.rglob('*') if f.is_file())
        n_files = sum(1 for _ in archive_dir.rglob('*') if _.is_file())
        self.stdout.write(self.style.SUCCESS("\nSnapshot built."))
        self.stdout.write(f"  slug:      {snap_slug}")
        self.stdout.write(f"  dir:       {archive_dir}")
        self.stdout.write(f"  files:     {n_files}")
        self.stdout.write(f"  size:      {total_bytes/1e6:.1f} MB")
        self.stdout.write(f"  serve at:  /inventory/archive/{snap_slug}/")

    # ------------------------------------------------------------------
    # helpers

    @staticmethod
    def _write_json(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, separators=(',', ':'), default=str),
                        encoding='utf-8')

    @staticmethod
    def _rewrite_html(html, base='./', config_script=None, banner=None):
        """Rewrite live-app URLs to snapshot-local ones.

        `base` is the relative path from the current page back to the
        snapshot's root directory. e.g. './' for top-level pages,
        '../' for files at depth 1, '../../' for depth 2.

        config_script, if provided, is inserted immediately before the
        map.js <script> tag. Only the map-bearing index.html needs it.

        banner, if provided, is HTML inserted immediately after the
        opening <body> tag. Used to flag the page as an archived snapshot.
        """
        # 1. Static asset references → snapshot-local from the page's depth.
        html = re.sub(r'(src|href)="/static/', r'\1="' + base + 'static/', html)

        # 2. Internal navigation links → snapshot-local equivalents.
        #    The snapshot is meant to be self-contained: a reader who clicks
        #    "Methods" should stay in the snapshot, not bounce out to live.
        #    Order: more-specific patterns before less-specific.
        html = re.sub(
            r'href="/inventory/rules/([a-zA-Z0-9_]+)/"',
            lambda m: f'href="{base}rules/{m.group(1)}/"',
            html,
        )
        nav_rewrites = [
            ('href="/inventory/methods/"', f'href="{base}methods.html"'),
            ('href="/inventory/rules/"',   f'href="{base}rules/"'),
            ('href="/inventory/"',         f'href="{base}"'),
        ]
        for old, new in nav_rewrites:
            html = html.replace(old, new)

        # 3. Features that don't exist inside a snapshot → strip the anchor,
        #    keep the link text. Manage/admin/export are all editor-only or
        #    live-only. Login routes too.
        feature_unavailable_patterns = [
            r'href="/inventory/manage/[^"]*"',  # manage list, edit, etc.
            r'href="/inventory/export/"',       # zip export
            r'href="/admin/[^"]*"',             # /admin/login/, /admin/, etc.
        ]
        for pat in feature_unavailable_patterns:
            html = re.sub(pat, 'href="#"', html)

        # 4. Inject LS_CONFIG immediately before the map.js <script> tag —
        #    the seam map.js reads at IIFE entry. Only the page that loads
        #    map.js needs this (the snapshot's index.html).
        if config_script:
            html = re.sub(
                r'(<script[^>]*src="[^"]*map\.js[^"]*"[^>]*>\s*</script>)',
                config_script + r'\1',
                html,
                count=1,
            )

        # 5. Snapshot banner — visual marker that this is an archived view.
        #    Inserted immediately after <body class="…">. The map sizing
        #    code reads getBoundingClientRect().top, so the banner just
        #    shifts the map down by its own height without further code.
        if banner:
            html = re.sub(
                r'(<body[^>]*>)',
                r'\1\n' + banner,
                html,
                count=1,
            )
        return html

    @staticmethod
    def _git_commit():
        try:
            sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                          cwd=settings.BASE_DIR,
                                          stderr=subprocess.DEVNULL).decode().strip()
            return sha
        except Exception:
            return None

    @staticmethod
    def _rules_summary():
        # Capture each rule's name + summary as they exist at build time, so
        # readers of the snapshot can tell what derived columns were computed
        # and how, without consulting the live derived.py (which may have
        # changed by the time they look).
        try:
            from inventory import derived
            return [{
                'name':         r.__name__,
                'target_table': getattr(r, 'target_table', None),
                'target_column': getattr(r, 'target_column', None),
                'inputs':       list(getattr(r, 'inputs', []) or []),
                'summary':      (getattr(r, 'summary', '') or '').strip(),
            } for r in getattr(derived, 'RULES', [])]
        except Exception:
            return []
