"""Round-trip stability test for the GeoJSON export/import.

Verifies that:
  1. compute_diff against the just-exported snapshot is empty.
  2. apply_import on the snapshot is a no-op (0 UPDATEs).
  3. A second export after the no-op apply is byte-identical to the first.

This is the foundational test for Phase D — proves that download then
upload-without-changes leaves the DB exactly as it was.

Usage:
    python manage.py roundtrip_test
    python manage.py roundtrip_test --verbose
"""
import io
import zipfile

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Verify export → import round-trip is a no-op against current DB.'

    def add_arguments(self, parser):
        parser.add_argument('--verbose', action='store_true',
                            help='Show details of any non-empty diff')

    def handle(self, *args, **options):
        from inventory.io_geojson import (
            build_export_bundle, parse_upload, compute_diff, apply_import,
        )

        verbose = options['verbose']

        self.stdout.write('Step 1: snapshot inventory state...')
        zip1, _ = build_export_bundle()
        ls1, po1, _ = parse_upload(zip1)
        self.stdout.write(
            f"  exported {len(ls1['features'])} landslides, "
            f"{len(po1['features'])} polygons "
            f"({len(zip1):,} bytes zipped)"
        )

        self.stdout.write('Step 2: diff snapshot against current DB...')
        diff = compute_diff(ls1, po1)
        ls_updates = len(diff['landslides']['updates'])
        po_updates = len(diff['landslide_polygons']['updates'])

        if ls_updates or po_updates:
            self.stdout.write(self.style.ERROR(
                f'  ❌ FAIL: diff is non-empty even though no edits were made.\n'
                f'    {ls_updates} landslide change(s); {po_updates} polygon change(s).'
            ))
            if verbose:
                for u in diff['landslides']['updates'][:5]:
                    self.stdout.write(f'    landslide id={u["id"]}: '
                                      f'{list(u["changes"].keys())}')
                for u in diff['landslide_polygons']['updates'][:5]:
                    self.stdout.write(f'    polygon id={u["id"]}: '
                                      f'{list(u["changes"].keys())}')
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS('  ✅ diff empty'))

        self.stdout.write('Step 3: apply (should be no-op)...')
        sysuser = get_user_model().objects.filter(is_superuser=True).first()
        summary = apply_import(ls1, po1, sysuser)
        if summary['landslides_updated'] or summary['polygons_updated']:
            self.stdout.write(self.style.ERROR(
                f'  ❌ FAIL: apply was not a no-op. '
                f'{summary["landslides_updated"]} landslide UPDATEs, '
                f'{summary["polygons_updated"]} polygon UPDATEs.'
            ))
            raise SystemExit(2)
        self.stdout.write(self.style.SUCCESS('  ✅ apply was a no-op'))

        self.stdout.write('Step 4: re-export and compare to first snapshot...')
        zip2, _ = build_export_bundle()
        files1 = _geojson_files(zip1)
        files2 = _geojson_files(zip2)
        diffs = []
        for name in sorted(set(files1) | set(files2)):
            if files1.get(name) != files2.get(name):
                diffs.append(name)
        if diffs:
            self.stdout.write(self.style.ERROR(
                f'  ❌ FAIL: re-exported snapshot differs from first in: {diffs}'
            ))
            raise SystemExit(3)
        self.stdout.write(self.style.SUCCESS(
            '  ✅ re-export byte-identical to first snapshot'
        ))

        self.stdout.write(self.style.SUCCESS('\nRound-trip stable.'))


def _geojson_files(zip_bytes):
    """Return {name: bytes} for .geojson files in the zip."""
    out = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zf:
        for name in zf.namelist():
            if name.endswith('.geojson'):
                out[name] = zf.read(name)
    return out
