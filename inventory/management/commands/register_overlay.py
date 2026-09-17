"""Register an already-baked PMTiles pyramid as an imagery overlay.

    manage.py register_overlay corax_muddy_2024_ortho.pmtiles \
        --title "Muddy Creek 2024 orthomosaic (Corax)" --date 2024-08-01 \
        --source-note "Corax Drone Services" [--render rgb] [--public]

WHY NOT THE UPLOAD FORM
-----------------------
The editor upload accepts a pre-baked .pmtiles, which is the right path for a
Planet scene of a couple of hundred megabytes. A drone orthomosaic is another
matter: these are 0.3-0.7 GB each, past the 250 MB cap, and pushing them
through a browser to the droplet would be a slow round trip for bytes that are
already sitting on disk beside the destination. This registers a file that is
already in place, or moves one into place, and creates the row that makes it
visible.

The row is NOT public by default. An overlay that is not public is served only
to signed-in editors, which is what gating a proprietary survey means here.
"""
import datetime
import shutil
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from inventory import raster_tiles
from inventory.models import TraceRaster


class Command(BaseCommand):
    help = "Register a pre-baked PMTiles pyramid as an imagery overlay."

    def add_arguments(self, p):
        p.add_argument("pmtiles", help="path to the .pmtiles file")
        p.add_argument("--title", required=True)
        p.add_argument("--render", default="rgb", choices=["nrg", "rgb", "gray"])
        p.add_argument("--date", help="capture date, YYYY-MM-DD")
        p.add_argument("--source-note", default="")
        p.add_argument("--public", action="store_true",
                       help="serve to everyone (default: signed-in editors only)")
        p.add_argument("--move", action="store_true",
                       help="move the file into place instead of copying")
        p.add_argument("--replace", type=int, metavar="ID",
                       help="re-point an existing row at this file")

    def handle(self, *a, **o):
        src = Path(o["pmtiles"])
        if not src.is_file():
            raise CommandError(f"no such file: {src}")
        image_date = None
        if o.get("date"):
            try:
                image_date = datetime.date.fromisoformat(o["date"])
            except ValueError:
                raise CommandError("--date must be YYYY-MM-DD")

        if o.get("replace"):
            row = TraceRaster.objects.filter(pk=o["replace"]).first()
            if row is None:
                raise CommandError(f"no raster #{o['replace']}")
        else:
            row = TraceRaster.objects.create(
                title=o["title"][:200], image_date=image_date,
                source_note=(o["source_note"] or "")[:300], render=o["render"],
                public=bool(o["public"]), status=TraceRaster.STATUS_PROCESSING)

        dest = raster_tiles.pmtiles_path(row.pk, o["render"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.stdout.write(f"  {'moving' if o['move'] else 'copying'} "
                          f"{src.stat().st_size / 1e9:.2f} GB -> {dest}")
        try:
            if o["move"]:
                shutil.move(str(src), dest)
            else:
                shutil.copyfile(src, dest)
            # The header carries bounds and zoom range; a file that does not
            # parse is rejected here rather than becoming a row that renders
            # nothing and looks like a broken layer.
            meta = raster_tiles.read_pmtiles_header(dest)
        except ValueError as exc:
            if not o.get("replace"):
                shutil.rmtree(raster_tiles.tiles_dir(row.pk), ignore_errors=True)
                row.delete()
            raise CommandError(f"not a usable PMTiles archive: {exc}")

        TraceRaster.objects.filter(pk=row.pk).update(
            status=TraceRaster.STATUS_READY, error_message="",
            title=o["title"][:200], render=o["render"],
            public=bool(o["public"]), **meta)
        row.refresh_from_db()
        self.stdout.write(self.style.SUCCESS(
            f"ready: id={row.pk} {row.title} ({row.render}), "
            f"{'PUBLIC' if row.public else 'editors only'}, "
            f"z{row.min_zoom}-{row.max_zoom}, {row.tile_count} tiles"))
