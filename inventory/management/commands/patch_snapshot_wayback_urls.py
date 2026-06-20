"""Patch legacy ESRI Wayback #ext= links inside published snapshot bundles.

Snapshots are immutable, citable point-in-time freezes (see build_snapshot):
they pre-render every API response and bundle a downloadable GeoJSON export,
so a snapshot built before the Wayback URL fix still embeds the broken #ext=
links in its frozen files. ESRI's app ignores #ext=, so those links open to
the whole world.

This command surgically rewrites only the Wayback URLs in place, leaving all
scientific data, the manifest, build date, and git_commit untouched. It uses
the same conversion as migrate_wayback_urls (inventory.views) so the snapshot
agrees with the live DB.

Files touched per snapshot at data/snapshots/<slug>/:
  - api/landslide/<id>/index.json   (the detail endpoint carries the link)
  - landslidescience_archive_<slug>.zip  (landslides.geojson +
    landslide_polygons_flat.geojson members)

The rewrite is a literal old-URL -> new-URL substitution on the raw file text,
so JSON/GeoJSON formatting is preserved byte-for-byte apart from the URLs. The
new URL needs no JSON escaping (commas become %2C). Idempotent: files with no
#ext= are skipped.

Usage:
    python manage.py patch_snapshot_wayback_urls --dry-run   # preview
    python manage.py patch_snapshot_wayback_urls             # apply
    python manage.py patch_snapshot_wayback_urls alaska-2025 # one slug
"""
import io
import re
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

# A Wayback URL as it appears inside a JSON string: the fixed base, running to
# the closing quote (the URL contains no " or backslash). Every match is fed to
# _convert_wayback_ext_url, which rewrites a bbox (#ext= or #active=N&ext=) and
# leaves already-converted (#mapCenter=) links untouched.
_WB_URL_RE = re.compile(
    r'https://livingatlas\.arcgis\.com/wayback/[^"\\]*')


class Command(BaseCommand):
    help = 'Convert legacy #ext= ESRI Wayback links inside snapshot bundles.'

    def add_arguments(self, parser):
        parser.add_argument('slugs', nargs='*',
                            help='Snapshot slug(s). Default: every snapshot on disk.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing.')

    def handle(self, *args, **opts):
        from inventory.views import _convert_wayback_ext_url

        def patch_text(text):
            """Convert every legacy Wayback URL in `text`; return (new_text, n)."""
            replaced = 0
            for url in set(_WB_URL_RE.findall(text)):
                new, changed = _convert_wayback_ext_url(url)
                if changed:
                    replaced += text.count(url)
                    text = text.replace(url, new)
            return text, replaced

        self.patch_text = patch_text
        self.dry = opts['dry_run']

        root = Path(settings.BASE_DIR) / 'data' / 'snapshots'
        if not root.is_dir():
            raise CommandError(f"No snapshots directory at {root}.")

        slugs = opts['slugs'] or sorted(
            p.name for p in root.iterdir() if p.is_dir())
        if not slugs:
            self.stdout.write("No snapshots found; nothing to do.")
            return

        grand = 0
        for slug in slugs:
            snap = root / slug
            if not snap.is_dir():
                raise CommandError(f"No snapshot directory {snap}.")
            self.stdout.write(self.style.MIGRATE_HEADING(f"snapshot: {slug}"))
            grand += self._patch_detail_json(snap)
            grand += self._patch_archive_zip(snap, slug)

        verb = "would convert" if self.dry else "converted"
        self.stdout.write(self.style.SUCCESS(
            f"\nDone: {verb} {grand} Wayback URL(s)"
            + (" (dry-run, nothing written)." if self.dry else ".")))

    def _patch_detail_json(self, snap):
        files = sorted((snap / 'api' / 'landslide').glob('*/index.json'))
        total = touched = 0
        for f in files:
            text = f.read_text(encoding='utf-8')
            if 'ext=' not in text:
                continue
            new_text, n = self.patch_text(text)
            if n:
                total += n
                touched += 1
                if not self.dry:
                    f.write_text(new_text, encoding='utf-8')
        self.stdout.write(
            f"  detail json: {total} URL(s) in {touched} file(s)")
        return total

    def _patch_archive_zip(self, snap, slug):
        zip_path = snap / f'landslidescience_archive_{slug}.zip'
        if not zip_path.exists():
            self.stdout.write("  archive zip: (none)")
            return 0

        total = 0
        members = []  # (ZipInfo, bytes) preserving order
        with zipfile.ZipFile(zip_path) as zin:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if b'ext=' in data:
                    new_text, n = self.patch_text(data.decode('utf-8'))
                    if n:
                        total += n
                        data = new_text.encode('utf-8')
                        self.stdout.write(
                            f"    {info.filename}: {n} URL(s)")
                members.append((info, data))

        self.stdout.write(f"  archive zip: {total} URL(s)")
        if total and not self.dry:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
                for info, data in members:
                    # Preserve the member's name, timestamp, and external attrs;
                    # recompress with deflate (the snapshot's default).
                    zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                    zi.external_attr = info.external_attr
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    zout.writestr(zi, data)
            zip_path.write_bytes(buf.getvalue())
        return total
