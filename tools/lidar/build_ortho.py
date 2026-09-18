#!/usr/bin/env python3
"""build_ortho.py -- bake a drone orthomosaic into one WebP PMTiles pyramid.

    build_ortho.py <source.tif> --id corax_muddy_2024_ortho --title "..."
        [--max-zoom auto] [--min-zoom 11] [--quality 80] [--keep-cog]

WHY NOT build_lidar
-------------------
An orthomosaic is a picture, not a surface: three or four bytes per pixel
rather than an elevation, and nothing downstream wants terrain-RGB or a slope
derivative from it. What it shares with build_lidar is the awkward half --
turning a directory of tiles into one PMTiles file -- so that is imported
rather than copied.

WHY THE INTERMEDIATE COG
------------------------
These deliveries are stripe-blocked: one block the full width of the image
and 32 rows tall. Tiling reads small square windows, and against a stripe
layout every window pulls the whole width of the image through the
decompressor. Converting to a 512-square tiled COG first costs about a
minute and turns a pathological read pattern into a sequential one. The same
trap is recorded against the Lower KP delivery.

WHY WEBP, AND WHY ALPHA MATTERS
-------------------------------
Imagery tolerates lossy compression where terrain-RGB would not: one bad bit
in terrain-RGB is a cliff, in a photograph it is nothing. WebP also carries
an alpha channel, which these orthomosaics need -- a drone survey covers an
irregular polygon, and without alpha the area outside the flight lines is
painted black and hides the basemap underneath it.
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
for _v in ("PROJ_LIB", "PROJ_DATA"):
    os.environ.pop(_v, None)

import build_lidar as bl  # noqa: E402  (paths, run(), dir_to_mbtiles, PMTILES)

GDAL = Path(os.environ.get("GDAL_BIN", "/opt/homebrew/bin"))
WORK = Path(os.environ.get("ORTHO_WORK", "/Volumes/Powder/lidar_build/corax"))


def native_zoom(src):
    """The zoom whose ground resolution first matches the source's.

    Web Mercator resolution is quoted at the equator; the ground distance a
    pixel covers at latitude L is smaller by cos(L). At 62 degrees north that
    is a factor of two -- ignore it and every one of these surveys is built
    one zoom short of its own detail.
    """
    d = json.loads(subprocess.run([str(GDAL / "gdalinfo"), "-json", str(src)],
                                  capture_output=True, text=True).stdout)
    res = abs(d["geoTransform"][1])
    ring = d.get("wgs84Extent", {}).get("coordinates", [[]])[0]
    lat = sum(p[1] for p in ring) / len(ring) if ring else 0.0
    ground = 156543.03392 * math.cos(math.radians(lat))
    # Round UP rather than to nearest, with a 10% grace band. Rounding to
    # nearest quietly throws away detail whenever a source sits just above a
    # zoom boundary: the MNI 2023 lidar at 0.105 m rounded to z19, which is
    # 0.141 m here -- 1.35x coarser than it was flown. Over-sampling by up to
    # 2x costs tiles and loses nothing; under-sampling cannot be undone
    # without rebuilding. The grace band keeps a source within 10% of a zoom
    # from doubling the pyramid for a couple of percent of detail.
    z = math.ceil(math.log2(ground / res) - 0.1375)
    return max(0, min(24, z)), res, lat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--id", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--min-zoom", type=int, default=11)
    ap.add_argument("--max-zoom", default="auto")
    ap.add_argument("--quality", type=int, default=80)
    ap.add_argument("--keep-cog", action="store_true")
    ap.add_argument("--srs", default="EPSG:6335",
                    help="re-tag the source with this CRS (it must not move the data)")
    ap.add_argument("--scale", action="append", metavar="LO,HI",
                    help="per-band input range to stretch to 0-255, repeated once per "
                         "band. Required for a source that is not already 8-bit.")
    ap.add_argument("--gamma", type=float, default=None,
                    help="applied after the stretch; <1 lifts the midtones")
    ap.add_argument("--out", default=None, help="PMTiles output directory")
    a = ap.parse_args()

    src = Path(a.src)
    if not src.exists():
        sys.exit(f"no such file: {src}")
    WORK.mkdir(parents=True, exist_ok=True)
    out_dir = Path(a.out) if a.out else bl.OUT_PM
    out_dir.mkdir(parents=True, exist_ok=True)

    z_native, res, lat = native_zoom(src)
    z_max = z_native if a.max_zoom == "auto" else int(a.max_zoom)
    print(f"== {a.id}: {src.name}")
    print(f"  {res:.4f} m/px at {lat:.3f}N -> native zoom {z_native}; building "
          f"z{a.min_zoom}-{z_max}", flush=True)

    t0 = time.time()
    cog = WORK / f"{a.id}_cog.tif"
    if cog.is_file():
        print(f"  reusing {cog.name}", flush=True)
    else:
        print("  converting to a tiled COG (the source is stripe-blocked)", flush=True)
        scale = []
        if a.scale:
            # A 16-bit aerial ortho has to be stretched to 8 bits somewhere, and
            # the only safe place is ONCE, globally, here. Letting each tile
            # find its own range -- which is what any per-tile automatic
            # stretch does -- makes every seam in the mosaic a brightness step,
            # because a tile of dark forest and a tile of bright gravel would
            # each be stretched to full range. So the numbers come from
            # sampling the whole survey and are passed in explicitly.
            for i, rng in enumerate(a.scale, start=1):
                lo, hi = rng.split(",")
                scale += [f"-scale_{i}", lo, hi, "0", "255"]
            if a.gamma:
                for i in range(1, len(a.scale) + 1):
                    scale += [f"-exponent_{i}", str(a.gamma)]
            scale += ["-ot", "Byte"]
        bl.run([str(GDAL / "gdal_translate"), "-of", "COG", "-a_srs", a.srs, *scale,
                "-co", "COMPRESS=ZSTD", "-co", "LEVEL=9", "-co", "PREDICTOR=YES",
                "-co", "BIGTIFF=YES", "-co", "NUM_THREADS=ALL_CPUS",
                "-co", "OVERVIEWS=IGNORE_EXISTING", "-co", "OVERVIEW_RESAMPLING=AVERAGE",
                str(src), str(cog)])

    tiles = WORK / f"{a.id}_tiles"
    shutil.rmtree(tiles, ignore_errors=True)
    print(f"  tiling -> WebP q{a.quality}", flush=True)
    bl.run([str(GDAL / "gdal"), "raster", "tile",
            "--min-zoom", str(a.min_zoom), "--max-zoom", str(z_max),
            "-r", "cubic", "--overview-resampling", "average",
            # --add-alpha is not optional: the WebP driver otherwise drops the
            # source's alpha band and paints everything outside the flight
            # polygon opaque black. Measured on the first build -- edge tiles
            # came out 40-78% pure black, which on the map is a black collar
            # hiding the basemap around every survey.
            "--convention", "xyz", "--skip-blank", "--add-alpha",
            "--webviewer", "none",
            "-j", "ALL_CPUS", "--no-intersection-ok",
            "-f", "WEBP", "--co", f"QUALITY={a.quality}",
            str(cog), str(tiles)])

    ds = {"id": a.id, "title": a.title, "min_zoom": a.min_zoom, "max_zoom": z_max}
    mb = WORK / f"{a.id}.mbtiles"
    print("  packing MBTiles", flush=True)
    n = bl.dir_to_mbtiles(tiles, mb, ds, "webp",
                          description=f"Orthomosaic, {a.title}")
    pm = out_dir / f"{a.id}.pmtiles"
    print(f"  converting {n} tiles -> {pm}", flush=True)
    bl.run([bl.PMTILES, "convert", str(mb), str(pm)])
    mb.unlink(missing_ok=True)
    shutil.rmtree(tiles, ignore_errors=True)
    if not a.keep_cog:
        cog.unlink(missing_ok=True)
    print(f"== done: {pm} ({pm.stat().st_size/1e9:.2f} GB, {n} tiles, "
          f"{(time.time()-t0)/60:.1f} min)", flush=True)


if __name__ == "__main__":
    main()
