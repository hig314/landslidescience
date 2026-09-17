"""Add a Sentinel-2 window as a public imagery layer, by date and place.

    manage.py add_sentinel_scene --lat 59.7942 --lon -138.6297 --date 2026-09-10
    manage.py add_sentinel_scene --lat .. --lon .. --date .. --list
    manage.py add_sentinel_scene --rebake 10

`--list` shows the candidate scenes with their cloud cover and does nothing
else; without it the closest scene to the date is taken. The row that results
is a TraceRaster with `public` set, so it is served to every visitor, and with
`source_ref` holding the linkage, so `--rebake` can rebuild the tiles from the
record alone. The window imagery itself is never kept.
"""
from django.core.management.base import BaseCommand, CommandError

from inventory import sentinel
from inventory.models import TraceRaster


class Command(BaseCommand):
    help = "Add or rebuild a public Sentinel-2 imagery layer for a date and place."

    def add_arguments(self, p):
        p.add_argument("--lat", type=float)
        p.add_argument("--lon", type=float)
        p.add_argument("--date", help="YYYY-MM-DD; the nearest scene is used")
        p.add_argument("--days", type=int, default=7, help="search window either side")
        p.add_argument("--half", type=float, default=sentinel.DEFAULT_HALF_M,
                       help="half-width of the image in metres (default 6000 = 12 km box)")
        p.add_argument("--render", default="nrg", choices=["nrg", "rgb", "gray"])
        p.add_argument("--level", default=sentinel.DEFAULT_LEVEL, choices=["l1c", "l2a"],
                       help="processing level (default l1c: no atmospheric correction "
                            "to misread in deep shade)")
        p.add_argument("--scene", help="use this exact scene id from the search")
        p.add_argument("--list", action="store_true", help="show candidates and stop")
        p.add_argument("--rebake", type=int, help="rebuild an existing row from its source_ref")
        p.add_argument("--title")

    def handle(self, *a, **o):
        if o.get("rebake"):
            return self._rebake(o["rebake"])
        for k in ("lat", "lon", "date"):
            if o.get(k) is None:
                raise CommandError("--lat, --lon and --date are required (or use --rebake)")

        collection = sentinel.COLLECTIONS[o["level"]]
        scenes = sentinel.search(o["lon"], o["lat"], o["date"], days=o["days"],
                                 collection=collection)
        if not scenes:
            raise CommandError("no Sentinel-2 scenes cover that point in that window")
        self.stdout.write(f"{len(scenes)} candidate scenes:")
        for s in scenes[:12]:
            cc = "?" if s["cloud_cover"] is None else f"{s['cloud_cover']:5.1f}%"
            self.stdout.write(f"  {s['date']}  cloud {cc}  {s['scene']}")
        if o["list"]:
            return

        pick = scenes[0]
        if o.get("scene"):
            pick = next((s for s in scenes if s["scene"] == o["scene"]), None)
            if pick is None:
                raise CommandError(f"scene {o['scene']} is not among the candidates")
        cc = pick["cloud_cover"]
        self.stdout.write(self.style.NOTICE(f"\nusing {pick['scene']} ({pick['date']}, cloud {cc}%)"))

        ref = {"kind": collection, "scene": pick["scene"],
               "datetime": pick["datetime"], "centre": [o["lon"], o["lat"]],
               "half_m": o["half"], "cloud_cover": cc, "assets": pick["assets"]}
        title = o.get("title") or f"Sentinel-2 {pick['date']}"
        row = TraceRaster.objects.create(
            title=title[:200], image_date=pick["date"], render=o["render"],
            source_note=f"Copernicus {collection} · {pick['scene']} · cloud {cc}%",
            status=TraceRaster.STATUS_PROCESSING, public=True, source_ref=ref)
        try:
            self._build(row)
        except Exception as e:
            TraceRaster.objects.filter(pk=row.pk).update(
                status=TraceRaster.STATUS_ERROR, error_message=str(e)[:500])
            raise CommandError(f"build failed: {e}")
        self.stdout.write(self.style.SUCCESS(
            f"ready: id={row.pk} {row.title} ({row.render}), public, "
            f"z{row.min_zoom}-{row.max_zoom}, {row.tile_bytes/1e6:.1f} MB"))

    def _rebake(self, pk):
        row = TraceRaster.objects.filter(pk=pk).first()
        if row is None:
            raise CommandError(f"no raster #{pk}")
        if not row.source_ref:
            raise CommandError(f"#{pk} has no source_ref — it is an upload, not a linkage")
        self._build(row)
        self.stdout.write(self.style.SUCCESS(f"rebuilt #{pk} from its linkage"))

    def _build(self, row):
        self.stdout.write("  reading the window from the remote COGs, then baking\u2026")
        sentinel.build(row)
