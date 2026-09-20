#!/usr/bin/env python3
"""build_lidar.py -- turn a raw Alaska lidar DEM into the two hosted products.

For each dataset in datasets.json this produces:

  1. ARCHIVE  data/lidar/cog/<id>.tif       Cloud-Optimized GeoTIFF in the
     appropriate local UTM zone, NAD83(2011). This is the download/analysis
     product -- the thing that fixes "this data is a pain to get online".
     Its internal overviews are also what make step 2's low-zoom warps fast.

  2. WEB      data/lidar/pmtiles/<id>.pmtiles
     A single-file terrain-RGB tile pyramid in EPSG:3857, read by MapLibre as
     a `raster-dem` source over HTTP range requests. One file per dataset
     instead of ~10^5 loose PNGs, which matters for both rsync and R2.

  3. SLOPE    data/lidar/pmtiles/<id>_slope.pmtiles
     A companion pyramid of slope in degrees, 8-bit greyscale in 0.5 degree
     steps (value = round(2 * slope); 255 = nodata). See WHY SLOPE IS
     PRE-BAKED below.

Run with the Homebrew GDAL 3.13 (the QGIS-LTR bundle is 3.3 and its gdal2tiles
is broken); see REQUIRED TOOLING below.

  tools/lidar/build_lidar.py <id> [--stage archive|web|pmtiles|all] [--webp]
  tools/lidar/build_lidar.py --list


WHY THE WEB STAGE LOOKS EXPENSIVE (one warp per zoom level)
-----------------------------------------------------------
Terrain-RGB packs one elevation into three bytes of differing significance:

    h = -10000 + (R*65536 + G*256 + B) * 0.1

Those channels must never be resampled as if they were color. Averaging two
neighbouring B values that straddle a carry boundary (255 and 0) yields 127 --
a 12.7 m error invented out of nothing. So the naive pipeline (encode once at
native resolution, let the tiler build overviews) is silently wrong at every
zoom below max.

Instead we resample the *elevation* to each zoom's exact ground resolution and
encode that level separately. The per-level warps form a geometric series --
each is a quarter the size of the one above -- so the whole pyramid costs about
1.33x the max-zoom warp. Cheap, and correct at every level.

The alternative (encode once, resample with `nearest` only) is also correct but
aliases badly: at z13 it picks one 0.5 m lidar post per 8 m cell, so the
hillshade turns to noise.


WHY SLOPE IS PRE-BAKED (and hillshade is not)
--------------------------------------------
Terrain-RGB stores elevation in 0.1 m steps. On a low-gradient surface -- a
tidal flat, a floodplain -- one step falls on a single pixel, and at z17 a
0.1 m rise over a 0.6 m pixel is a 10 degree slope. Any slope computed in
the browser from those tiles therefore shows a staircase of false ~10 degree
lines across ground that is nearly flat (Hig, Anchorage flats, 2026-09-12).

Slope depends only on the terrain, so it is computed ONCE here, from the
float32 archive in its UTM zone where a pixel is a true metre (gdaldem
slope, Horn 3x3, no Mercator scale fudge), then resampled per zoom with
`average` -- mean slope per cell is the right statistic at coarse zooms --
and quantised to 0.5 degrees, which compresses far better than a float and
is finer than any colour ramp resolves. The browser reads that raster for
the slope ramp and computes nothing.

Hillshade cannot be pre-baked: the sun azimuth and altitude are sliders. It
stays a client-side Horn gradient on the elevation tiles, where the
staircase is a faint lighting artefact rather than a coloured band. The
elevation banding (mod 5 m) deliberately keeps reading the raw tile values.

A survey without a slope pyramid still works: the client falls back to the
in-browser gradient.


WHY A GENERIC "NAD83" SOURCE IS TREATED AS NAD83(2011)
------------------------------------------------------
Vendors' GeoTIFFs often carry the bare NAD83 datum (EPSG:4269, or datum
6269 in ESRI-style WKT) even though the survey was adjusted to NAD83(2011)
-- the USGS lidar base specification has required NAD83(2011) since 2012,
and no lidar vendor has delivered NAD83(1986) positions in this century.
Warping such a file to our NAD83(2011) target makes PROJ (Homebrew GDAL,
NADCON5 grids installed, PROJ_NETWORK=ON) apply the 1986->1992->2007->2011
grid chain: 0.5-1 m in Alaska, direction varying with place.

Found 2026-09-12 while co-registering the Kenai 2008 point cloud: the
homer_2019 archive sat 0.67 m E / 0.58 m N of its own source file, while
two independent surveys (USGS 2008 EPT, USACE 2018 NCMP) agreed with the
source to ~0.35 m and disagreed with the archive by ~0.7 m. Affected
sources: homer_2019, anchorage_2015, matanuska_2011 and its five siblings
(all rebuilt from 2026-09-12). Invisible in the viewer; wrong for any
survey-to-survey change detection.

Fix: retag_generic_nad83() writes a VRT of the source whose BASEGEOGCRS is
NAD83(2011) (EPSG:6318) and warps from that. Only the horizontal datum tag
changes; the projection, units and any compound vertical CRS are kept, so a
NAVD88-feet source (Anchorage) is still converted to metres by the warp.
A source that really is NAD83(1986) can set "keep_source_datum": true in
datasets.json to opt out.

Testing trap: in an interactive shell `gdalwarp` is the QGIS-LTR GDAL 3.3,
whose PROJ has no NADCON5 grids and does not convert compound vertical
units -- it does NOT reproduce this build. Call /opt/homebrew/bin/gdalwarp
explicitly (and /opt/anaconda3/bin/python3 for rasterio).


WHY SELDOVIA 2019 CARRIES A VERTICAL SHIFT
------------------------------------------
"NAVD88" on a delivery label does not say which geoid model turned ellipsoid
heights into orthometric ones, and in Alaska the choice is worth over a metre.

The 2019 NCMP Seldovia tiles are labelled NAVD88, but the vendor's own LAS
headers say the heights were computed with xGEOID17B, an experimental
GRAV-D-era model, not the GEOID12B that NAVD88 means everywhere else in this
collection. The result sits 1.42 m BELOW the 2023 Kachemak Bay survey, whose
report states NAVD88 (GEOID12B): median -1.416 m over 1.18 M cells of stable
land, MAD 0.117 m, and -1.43 m as the median of thirteen 1 km blocks spanning
-1.50 to -1.24. Two parallel surfaces, one constant step.

Confirmed independently of either lidar survey. NOAA's 2008/09 multibeam is
referenced to MLLW; differencing it against the unshifted Seldovia grid puts
MLLW 3.00 m below NAVD88 (IQR 0.13 m). NOAA's own tide stations say otherwise:
Coal Point (9455558) publishes MLLW 1.553 m below NAVD88, and Seldovia
(9455500) has the same tidal range, so ~1.50 m is expected. With the +1.42 m
shift applied the multibeam gives 1.55 m and the discrepancy disappears.

Not a build defect: the archive reproduces both vendor files exactly, and the
2018 NCMP Homer delivery agrees with the 2019 state survey to 0.08 m, so this
is specific to the 2019 NCMP season.

Fix: "vertical_shift_m": 1.42 in datasets.json, applied by
apply_vertical_shift() as a VRT ScaleOffset read before the archive is written.
Works with archive_mode "warp" and "translate", both of which read the source
through GDAL; not with "copy", which byte-copies the file. The same mechanism
carries the Kachemak multibeam from MLLW to NAVD88.


REQUIRED TOOLING
----------------
  brew install gdal pmtiles     # GDAL >= 3.11 for `gdal raster tile`
Set GDAL_BIN if it lives elsewhere. PROJ_LIB must NOT point at the QGIS bundle
while running this -- mixing QGIS's PROJ with another GDAL silently degrades
written CRSs to an ENGCRS with an empty datum.
"""

import argparse
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "datasets.json"

# Must happen before rasterio imports PROJ: a PROJ_LIB inherited from the
# QGIS-LTR bundle makes this GDAL write an ENGCRS with an empty datum, which
# then fails downstream with an unrelated-looking "cannot find coordinate
# operations" error.
for _var in ("PROJ_LIB", "PROJ_DATA"):
    os.environ.pop(_var, None)

import numpy as np          # noqa: E402
import rasterio             # noqa: E402

GDAL_BIN = Path(os.environ.get("GDAL_BIN", "/opt/homebrew/bin"))
PMTILES = os.environ.get("PMTILES_BIN", "/opt/homebrew/bin/pmtiles")

# Scratch and bulk output. Intermediates are large (MatSu's max-zoom level
# alone is ~90 GB uncompressed) and the archive COGs total ~35 GB, so both
# default to the roomiest volume rather than the boot disk. The archive COGs
# are destined for object storage, not for the 16 GB free on the droplet.
BUILD = Path(os.environ.get("LIDAR_BUILD", "/Volumes/Nunatak/lidar_build"))
OUT_COG = Path(os.environ.get("LIDAR_COG_OUT", BUILD / "cog"))
# PMTiles are small enough to sit in the repo's volume-mounted data/ for dev.
OUT_PM = Path(os.environ.get("LIDAR_PM_OUT", ROOT / "data" / "lidar" / "pmtiles"))

# Web-mercator tile grid. Snapping every level to this anchor is what makes
# `gdal raster tile -r nearest` pixel-exact instead of half-pixel-smeared.
MERC_ORIGIN = -20037508.342789244
MERC_SPAN = 2 * 20037508.342789244
TILE_PX = 256


def res_at_zoom(z):
    return MERC_SPAN / (TILE_PX * 2 ** z)


def run(cmd, **kw):
    printable = " ".join(str(c) for c in cmd)
    print(f"    $ {printable[:200]}{'...' if len(printable) > 200 else ''}", flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def gdal(tool, *args, **kw):
    run([GDAL_BIN / tool, *args], **kw)


def clean_env():
    """GDAL/PROJ env that will not pick up the QGIS-LTR PROJ database."""
    env = dict(os.environ)
    for var in ("PROJ_LIB", "PROJ_DATA", "GDAL_DATA", "GDAL_DRIVER_PATH"):
        env.pop(var, None)
    env["GDAL_PAM_ENABLED"] = "NO"
    # Let PROJ fetch NAD83 <-> WGS84 shift grids so the datum transform the
    # user asked for is the accurate one rather than a null shift.
    env.setdefault("PROJ_NETWORK", "ON")
    return env


def load_manifest():
    data = json.loads(MANIFEST.read_text())
    return {d["id"]: d for d in data["datasets"]}


# --------------------------------------------------------------------------
# stage 1: archive COG in the correct UTM zone
# --------------------------------------------------------------------------

# Generic NAD83 (EPSG:4269 geographic CRS / EPSG:6269 datum) -> NAD83(2011).
NAD83_2011_BASE = ('BASEGEOGCRS["NAD83(2011)",'
                   'DATUM["NAD83 (National Spatial Reference System 2011)",'
                   'ELLIPSOID["GRS 1980",6378137,298.257222101,LENGTHUNIT["metre",1]]],'
                   'PRIMEM["Greenwich",0,ANGLEUNIT["degree",0.0174532925199433]],'
                   'ID["EPSG",6318]]')


def retag_generic_nad83(ds, src, env):
    """Path to warp from: `src`, or a VRT of it re-tagged NAD83(2011).
    See WHY A GENERIC "NAD83" SOURCE IS TREATED AS NAD83(2011) above."""
    if ds.get("keep_source_datum"):
        print("  archive: keep_source_datum set; warping with the source's own datum")
        return src
    wkt = subprocess.run([GDAL_BIN / "gdalsrsinfo", "-o", "wkt2", "--single-line", str(src)],
                         capture_output=True, text=True, env=env).stdout.strip()
    i = wkt.find("BASEGEOGCRS[")
    if i < 0:
        return src
    depth = 0
    for j in range(i, len(wkt)):
        if wkt[j] == "[":
            depth += 1
        elif wkt[j] == "]":
            depth -= 1
            if depth == 0:
                break
    if not re.search(r'ID\["EPSG",(4269|6269)\]', wkt[i:j + 1]):
        return src
    out_dir = BUILD / ds["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    wkt_path = out_dir / "source_nad83_2011.wkt"
    vrt = out_dir / "source_nad83_2011.vrt"
    wkt_path.write_text(wkt[:i] + NAD83_2011_BASE + wkt[j + 1:])
    run([GDAL_BIN / "gdal_translate", "-q", "-of", "VRT", "-a_srs", str(wkt_path), src, vrt], env=env)
    print("  archive: source tagged generic NAD83 -> re-tagged NAD83(2011) via VRT "
          "(no NADCON5 datum shift); \"keep_source_datum\": true overrides")
    return vrt


def check_retag(src, dst, ds, env, limit_km=2.0):
    """A re-tag must not MOVE the data, only correct how it is described.

    src_srs exists for deliveries whose embedded CRS is wrong in a way that
    does not change where the pixels are -- Sitka resolving to 7 degrees east,
    a plain-NAD83 tag that should be NAD83(2011), a BOUNDCRS carrying a null
    transform. Those shift a footprint by metres at most.

    Getting the CODE wrong is a different thing entirely and looks identical
    in the manifest. EPSG:6332 is NAD83(2011) / UTM zone 3N, not zone 6N --
    the series starts at 6330 for zone 1N -- and building the Corax surveys
    with it put three Matanuska valley sites off the Yukon Delta, 18 degrees
    west, with every other property of the file perfectly in order. The
    footprints were the only evidence, so check them.
    """
    def centre(path):
        out = subprocess.run([str(GDAL_BIN / "gdalinfo"), "-json", str(path)],
                             capture_output=True, text=True, env=env).stdout
        ring = json.loads(out).get("wgs84Extent", {}).get("coordinates", [[]])[0]
        if not ring:
            return None
        return (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))
    a, b = centre(src), centre(dst)
    if not a or not b:
        return
    dlon, dlat = b[0] - a[0], b[1] - a[1]
    km = math.hypot(dlon * 111.32 * math.cos(math.radians(a[1])), dlat * 110.57)
    if km > limit_km:
        sys.exit(f"{ds['id']}: src_srs {ds['src_srs']!r} MOVES the data "
                 f"{km:.0f} km ({a[1]:.4f},{a[0]:.4f} -> {b[1]:.4f},{b[0]:.4f}).\n"
                 f"  A re-tag corrects how the pixels are described; it must not "
                 f"relocate them. Check the EPSG code -- NAD83(2011) UTM north "
                 f"zones run 6330 (zone 1N), 6331 (2N), ... so zone 6N is 6335.")
    print(f"  re-tag check: footprint moved {km * 1000:.1f} m (within {limit_km} km)")


def apply_vertical_shift(ds, src, env):
    """Path to read from: `src`, or a VRT of it with a constant added to every
    elevation. See WHY SELDOVIA 2019 CARRIES A VERTICAL SHIFT above."""
    dz = ds.get("vertical_shift_m")
    if not dz:
        return src
    info = json.loads(subprocess.run([GDAL_BIN / "gdalinfo", "-json", str(src)],
                                     capture_output=True, text=True, env=env).stdout)
    nd = info["bands"][0].get("noDataValue")
    out_dir = BUILD / ds["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    plain, vrt = out_dir / "source_plain.vrt", out_dir / "source_shifted.vrt"
    run([GDAL_BIN / "gdal_translate", "-q", "-of", "VRT", src, plain], env=env)
    # A ComplexSource applies ScaleOffset on read, so no pixels are rewritten
    # and the warp that follows sees the corrected surface. <NODATA> is
    # mandatory: without it the offset is added to the nodata value too, which
    # leaves voids at -9997.58 against a band still declaring -9999 (caught in
    # testing, 2026-09-14).
    body = open(plain).read()
    if "<ScaleOffset>" in body:
        sys.exit("vertical_shift_m: the source VRT already carries a ScaleOffset")
    shift = (f"      <ScaleOffset>{dz:g}</ScaleOffset>\n"
             "      <ScaleRatio>1</ScaleRatio>\n")
    # Match the TAG, not the exact string: a source carries attributes when the
    # VRT sets one, e.g. `<ComplexSource resampling="bilinear">` on
    # resurrection_2016, whose coarse bands are read bilinear so a 16 m cell is
    # not replicated into 256 identical metre cells. The old literal
    # "<ComplexSource>" test missed those and the build exited claiming the VRT
    # had no sources at all.
    if "<ComplexSource" in body:
        # gdal_translate flattens a VRT source into the ComplexSource entries
        # gdalbuildvrt wrote, each already carrying its own <NODATA>.
        n = body.count("</ComplexSource>")
        body = body.replace("</ComplexSource>", shift + "    </ComplexSource>")
    elif "<SimpleSource" in body:
        n = body.count("</SimpleSource>")
        # gdalinfo -json cannot express NaN in JSON, so a NaN-nodata source
        # arrives here as the STRING "nan" and `:g` raises. GDAL's VRT accepts
        # the literal text "nan", so pass it through rather than formatting it.
        # (Found on lituya_2023, whose source uses NaN nodata; it only bit once
        # the dataset gained a vertical_shift_m, since without one this whole
        # function returns early.)
        nodata_el = ""
        if nd is not None:
            try:
                nodata_el = f"      <NODATA>{float(nd):g}</NODATA>\n"
            except (TypeError, ValueError):
                nodata_el = f"      <NODATA>{nd}</NODATA>\n"
        # Keep whatever attributes the opening tag carries while renaming it.
        body = re.sub(r"<SimpleSource(\s[^>]*)?>",
                      lambda m: "<ComplexSource%s>" % (m.group(1) or ""), body)
        body = body.replace("</SimpleSource>",
                            nodata_el + shift + "    </ComplexSource>")
    else:
        sys.exit("vertical_shift_m: VRT has neither a SimpleSource nor a ComplexSource")
    open(vrt, "w").write(body)
    print(f"  archive: vertical_shift_m {dz:+g} m applied on read across {n} source(s); "
          f"nodata {nd} untouched")
    return vrt


def _archive_shift(path, env):
    """The vertical_shift_m an existing archive was built with, or None when it
    was built before stamping existed (so we cannot tell)."""
    try:
        info = json.loads(subprocess.run(
            [str(GDAL_BIN / "gdalinfo"), "-json", str(path)],
            capture_output=True, text=True, env=env).stdout)
    except Exception:
        return None
    v = (info.get("metadata", {}).get("", {}) or {}).get("vertical_shift_m")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build_archive(ds, env):
    src = Path(ds["src"])
    if not src.exists():
        sys.exit(f"source missing: {src}")
    OUT_COG.mkdir(parents=True, exist_ok=True)
    dst = OUT_COG / f"{ds['id']}.tif"
    if dst.exists():
        # The skip is what makes a re-run of --stage all cheap. But it used to
        # skip unconditionally, which made changing vertical_shift_m in the
        # manifest a silent no-op: the archive kept the OLD datum while the
        # tiles were regenerated from it, so the dataset looked rebuilt and was
        # not. That is how lituya_2023 came out 0.639 m off on 2026-09-19 --
        # caught only because a second survey covered the same seabed.
        #
        # So the archive now carries the shift it was built with, and a
        # disagreement stops the build rather than being papered over.
        want = float(ds.get("vertical_shift_m") or 0.0)
        have = _archive_shift(dst, env)
        if have is None:
            print(f"  archive exists but predates shift stamping; "
                  f"assuming it matches vertical_shift_m={want:g}: {dst}")
        elif abs(have - want) > 1e-6:
            sys.exit(
                f"\n  ARCHIVE DATUM MISMATCH for {ds['id']}\n"
                f"    the existing archive was built with vertical_shift_m="
                f"{have:g}\n"
                f"    the manifest now says vertical_shift_m={want:g}\n"
                f"    Rebuilding the tiles alone would leave the old datum in\n"
                f"    place. Delete the archive and re-run:\n"
                f"      rm {dst}\n")
        print(f"  archive exists, skipping: {dst}")
        return dst

    target = ds["target_epsg"]

    # archive_mode is set explicitly per dataset in datasets.json rather than
    # sniffed. Auto-detecting "is this already the target CRS?" is unreliable
    # here: KBay's CRS is a COMPOUND CRS (NAD83(2011)/UTM 5N + NAVD88 height),
    # for which to_epsg() returns None and equality against EPSG:6334 is False,
    # even though its horizontal CRS is exactly the target. Warping it anyway
    # would be an identity resample AND would silently drop the NAVD88 vertical
    # datum -- the only declared vertical reference in the whole collection.
    if ds.get("vertical_shift_m") and ds.get("archive_mode") == "copy":
        sys.exit(f"{ds['id']}: vertical_shift_m cannot be used with archive_mode "
                 "'copy', which byte-copies the source file")

    if ds.get("archive_mode") == "copy":
        print("  archive: source already correct (compound CRS preserved) - copying")
        shutil.copyfile(src, dst)
        return dst

    # "translate": already in the target zone but not a COG (plain/LZW GeoTIFF,
    # 128x128 blocks, external .ovr). gdal_translate -of COG keeps the CRS --
    # including a compound vertical datum -- and the pixel values bit-for-bit;
    # a gdalwarp onto its own grid would be an identity resample that still
    # drops the vertical CRS. Optional src_srs tags a file whose embedded CRS
    # is broken (Sitka's resolves to 7 deg E) without touching the pixels.
    if ds.get("archive_mode") == "translate":
        print("  archive: same zone, rewriting as COG (no resample)")
        args = []
        if ds.get("src_srs"):
            args += ["-a_srs", ds["src_srs"]]
        # A vertical shift is free here: gdal_translate reads through the
        # ScaleOffset VRT, so the pixels are still copied at their own
        # resolution with no resample, just offset on the way past.
        src = apply_vertical_shift(ds, src, env)
        # IGNORE_EXISTING: the COG driver otherwise adopts the vendor's
        # external .ovr verbatim. Glen Alps' was misregistered against its
        # own full-res grid, with a different shift either side of a vertical
        # seam -- a step in the terrain at every zoom warped from overviews,
        # gone at full res. Always recompute from the pixels we ship.
        run([GDAL_BIN / "gdal_translate", "-of", "COG", *args,
             # Stamp the shift into the file so a later run can tell whether
             # this archive matches the manifest (see build_archive's skip).
             "-mo", f"vertical_shift_m={float(ds.get('vertical_shift_m') or 0.0):g}",
             "-co", "COMPRESS=ZSTD", "-co", "LEVEL=9", "-co", "PREDICTOR=YES",
             "-co", "OVERVIEWS=IGNORE_EXISTING",
             "-co", "OVERVIEW_RESAMPLING=AVERAGE",
             "-co", "BIGTIFF=YES", "-co", "NUM_THREADS=ALL_CPUS",
             src, dst], env=env)
        if ds.get("src_srs"):
            check_retag(ds["src"], dst, ds, env)
        return dst

    print(f"  archive: -> EPSG:{target}")
    tmp = BUILD / ds["id"] / "archive_tmp.tif"
    tmp.parent.mkdir(parents=True, exist_ok=True)

    # -r bilinear: the archive is a reprojection at (near) native scale, not a
    # downsample. Nodata is normalised to -9999 so every dataset behaves the
    # same downstream regardless of what the vendor used.
    #
    # src_nodata overrides the file's own nodata tag for sources whose real
    # voids carry a different value (matsu_2019: a 0 m plateau over 84% of
    # its box, tagged -99999 which never occurs). Masked before the warp, so
    # bilinear never blends the fill into the edge of the real data.
    src_nodata = []
    if ds.get("src_nodata") is not None:
        src_nodata = ["-srcnodata", str(ds["src_nodata"])]
        print(f"  archive: masking source value {ds['src_nodata']} as nodata")
    warp_src = apply_vertical_shift(ds, retag_generic_nad83(ds, src, env), env)
    run([GDAL_BIN / "gdalwarp", "-overwrite", *src_nodata,
         "-t_srs", f"EPSG:{target}",
         "-tr", ds["native_res_m"], ds["native_res_m"],
         # bilinear is right when the archive is a reprojection at (near) native
         # scale. It is WRONG when native_res_m is deliberately coarser than the
         # source -- Prince of Wales is delivered at 0.5 m and archived at 1 m --
         # because bilinear samples the target grid rather than integrating over
         # the source pixels it skips, which aliases fine terrain into noise.
         # "archive_resample": "average" opts a downsampled archive into the
         # correct kernel.
         "-r", ds.get("archive_resample", "bilinear"),
         "-dstnodata", "-9999",
         "-ot", "Float32",
         "-multi", "-wo", "NUM_THREADS=ALL_CPUS",
         "-co", "TILED=YES", "-co", "COMPRESS=ZSTD", "-co", "ZSTD_LEVEL=9",
         "-co", "BIGTIFF=YES",
         warp_src, tmp], env=env)

    # Vertical unit conversion, if the vendor shipped feet. Done here, once, so
    # that BOTH products (the download COG and the terrain-RGB tiles) are in
    # metres and can never drift apart. Applied after the warp because gdalwarp
    # reprojects but does not rescale pixel values.
    scale = ds.get("vertical_scale")
    # A source that DECLARES its vertical CRS (compound CRS such as NOAA's
    # "NAD83 / Alaska zone 4 (ftUS) + NAVD88 height (ftUS)") is already
    # converted by gdalwarp: PROJ scales the heights to metres as part of the
    # transformation. Scaling again here shrank Anchorage 2015 by a second
    # factor of 0.3048 (found 2026-09-12). vertical_scale is for sources that
    # only say "feet" in their metadata, like the Mat-Su 2011 tiles.
    with rasterio.open(src) as probe:
        compound = probe.crs is not None and "VERT" in probe.crs.to_wkt()
    if scale and scale != 1 and compound:
        print("  archive: source declares a vertical CRS; gdalwarp already converted "
              f"its units, so vertical_scale {scale} is NOT applied")
        scale = None
    if scale and scale != 1:
        print(f"  archive: converting vertical units x{scale} (feet -> metres)")
        scaled = BUILD / ds["id"] / "archive_m.tif"
        # NB: not `as dst` — that name is the archive's output path in this
        # function, and shadowing it hands a closed dataset to gdal_translate.
        with rasterio.open(tmp) as src:
            prof = src.profile.copy()
            prof.update(tiled=True, blockxsize=512, blockysize=512,
                        compress="zstd", bigtiff="YES", num_threads="ALL_CPUS")
            nodata = src.nodata
            with rasterio.open(scaled, "w", **prof) as sink:
                for _, win in src.block_windows(1):
                    a = src.read(1, window=win)
                    good = np.isfinite(a)
                    if nodata is not None:
                        good &= a != nodata
                    sink.write(np.where(good, a * scale, nodata).astype(prof["dtype"]),
                               1, window=win)
        tmp.unlink()
        tmp = scaled

    # PREDICTOR=YES -> floating-point predictor (TIFF 3) for Float32: the KBay
    # vendor COG used it and was 11.3 GB where ours without it was 19.4 GB.
    print("  archive: rewriting as COG with overviews")
    run([GDAL_BIN / "gdal_translate", "-of", "COG",
         "-mo", f"vertical_shift_m={float(ds.get('vertical_shift_m') or 0.0):g}",
         "-co", "COMPRESS=ZSTD", "-co", "LEVEL=9", "-co", "PREDICTOR=YES",
         "-co", "OVERVIEW_RESAMPLING=AVERAGE",
         "-co", "BIGTIFF=YES", "-co", "NUM_THREADS=ALL_CPUS",
         tmp, dst], env=env)
    tmp.unlink()
    return dst


# --------------------------------------------------------------------------
# stage 2: per-zoom elevation warp -> terrain-RGB -> single-level tiles
# --------------------------------------------------------------------------

def snap_extent(bounds, res):
    """Snap a 3857 extent outward onto the tile grid at this resolution."""
    xmin, ymin, xmax, ymax = bounds
    sx = lambda v, f: MERC_ORIGIN + f((v - MERC_ORIGIN) / res) * res
    sy = lambda v, f: -MERC_ORIGIN + f((v + MERC_ORIGIN) / res) * res
    return (sx(xmin, math.floor), sy(ymin, math.floor),
            sx(xmax, math.ceil), sy(ymax, math.ceil))


def encode_terrain_rgb(src_path, dst_path):
    """Float32 elevation -> 4-band uint8 (Mapbox terrain-RGB + alpha).

    Windowed so an 80 GB level never has to fit in RAM. Nodata encodes to
    h = 0 m rather than the natural -10000 m: MapLibre's DEM decoder ignores
    the alpha channel, so a -10000 fill would put a 10 km cliff along every
    data boundary. Alpha is still written so `--skip-blank` can drop empty
    tiles and the map can mask edges with the footprint.
    """
    with rasterio.open(src_path) as src:
        prof = src.profile.copy()
        prof.update(driver="GTiff", dtype="uint8", count=4, nodata=None,
                    tiled=True, blockxsize=512, blockysize=512,
                    compress="deflate", zlevel=6, bigtiff="YES",
                    predictor=1, num_threads="ALL_CPUS")
        nodata = src.nodata
        with rasterio.open(dst_path, "w", **prof) as dst:
            for _, win in src.block_windows(1):
                dem = src.read(1, window=win)
                valid = np.isfinite(dem)
                if nodata is not None:
                    valid &= dem != nodata
                # Guard the encodable range: -10000 .. +6553.5 m
                h = np.where(valid, np.clip(dem, -9999.0, 6553.0), 0.0)
                v = np.rint((h + 10000.0) / 0.1).astype(np.int64)
                dst.write((v >> 16).astype(np.uint8), 1, window=win)
                dst.write(((v >> 8) & 0xFF).astype(np.uint8), 2, window=win)
                dst.write((v & 0xFF).astype(np.uint8), 3, window=win)
                dst.write((valid * 255).astype(np.uint8), 4, window=win)
            dst.colorinterp = [rasterio.enums.ColorInterp.red,
                               rasterio.enums.ColorInterp.green,
                               rasterio.enums.ColorInterp.blue,
                               rasterio.enums.ColorInterp.alpha]


def drop_stale_tiles(tiles, archive, work, patterns, mbtiles=None):
    """Discard a tile pyramid the archive has outgrown.

    `gdal raster tile --resume` keeps every tile already on disk, which is what
    makes an interrupted bake resumable -- and a silent liar the moment the
    archive changes underneath it. Rebuilding seldovia_2019 with its +1.42 m
    vertical shift produced a fresh archive, a fresh .mbtiles and a fresh
    .pmtiles whose tiles were every one of them the OLD surface: the pyramid
    differed from the archive it claimed to come from by exactly the correction
    (found in the browser, 2026-09-14; the archive itself was correct, so
    nothing upstream of the tiles could reveal it). An archive newer than the
    tile directory means every tile in it is suspect, so start over.
    """
    if not tiles.exists() or not archive.exists():
        return
    if archive.stat().st_mtime <= tiles.stat().st_mtime:
        return
    print(f"  archive is newer than {tiles.name}/ -- discarding stale tiles")
    shutil.rmtree(tiles)
    for pat in patterns:
        for leftover in work.glob(pat):
            leftover.unlink()
    if mbtiles and mbtiles.exists():
        mbtiles.unlink()


def build_web(ds, archive, env, webp=False):
    work = BUILD / ds["id"]
    work.mkdir(parents=True, exist_ok=True)
    tiles = work / "tiles"
    minz, maxz = ds["min_zoom"], ds["max_zoom"]

    drop_stale_tiles(tiles, archive, work, ("elev_z*.tif", "rgb_z*.tif"),
                     mbtiles=work / f"{ds['id']}.mbtiles")

    # 3857 bounds of the archive, once.
    bounds = merc_bounds(archive, env)

    for z in range(maxz, minz - 1, -1):
        res = res_at_zoom(z)
        elev = work / f"elev_z{z}.tif"
        rgb = work / f"rgb_z{z}.tif"
        te = snap_extent(bounds, res)

        # average when genuinely downsampling; bilinear at max zoom where the
        # scale change is near unity and averaging would just blur the lidar.
        resamp = "bilinear" if z == maxz else "average"
        print(f"  z{z}: warp -> {res:.4f} m/px ({resamp})")
        # -s_srs <2-D horizontal> and -novshift: the archive may carry a
        # COMPOUND CRS (UTM + NAVD88 height). Given that and a 2-D EPSG:3857
        # target, gdalwarp applies the NAVD88 -> ellipsoid geoid shift (+8.4 m
        # at Anchorage) when it reads the source at full resolution, but NOT
        # when it reads an overview. With -multi some chunks took each path,
        # so every build had a random ~800 m band of rows 8.4 m high: the
        # "two parallel lines with the mod-5 bands jumping between them" seen
        # in Glen Alps, 2026-09-06. Heights must pass through untouched, as
        # NAVD88 orthometric, like every other survey in the collection.
        run([GDAL_BIN / "gdalwarp", "-overwrite",
             "-s_srs", f"EPSG:{ds['target_epsg']}", "-novshift",
             "-t_srs", "EPSG:3857",
             "-te", *[f"{v:.10f}" for v in te],
             "-tr", f"{res:.12f}", f"{res:.12f}",
             "-r", resamp,
             "-dstnodata", "-9999",
             "-ot", "Float32",
             "-multi", "-wo", "NUM_THREADS=ALL_CPUS",
             "-co", "TILED=YES", "-co", "COMPRESS=ZSTD", "-co", "BIGTIFF=YES",
             archive, elev], env=env)

        print(f"  z{z}: encode terrain-RGB")
        encode_terrain_rgb(elev, rgb)

        # Input is already on the tile grid at exactly this resolution, so
        # nearest is an exact copy -- no resampling of RGB happens here.
        print(f"  z{z}: tile")
        cmd = [GDAL_BIN / "gdal", "raster", "tile",
               "--min-zoom", z, "--max-zoom", z,
               "-r", "nearest", "--convention", "xyz",
               "--skip-blank", "--resume", "--webviewer", "none",
               "-j", "ALL_CPUS", "--no-intersection-ok"]
        if webp:
            cmd += ["--output-format", "WEBP", "--co", "LOSSLESS=TRUE"]
        cmd += [rgb, tiles]
        run(cmd, env=env)

        elev.unlink(missing_ok=True)
        rgb.unlink(missing_ok=True)

    return tiles


# --------------------------------------------------------------------------
# stage 2b: slope from the archive -> per-zoom average -> 8-bit tiles
# --------------------------------------------------------------------------

SLOPE_STEP_DEG = 0.5      # quantisation: value = round(slope / SLOPE_STEP_DEG)
SLOPE_NODATA = 255


def build_slope_native(ds, archive, env):
    """Slope in degrees from the archive, in the archive's own UTM metres."""
    work = BUILD / ds["id"]
    work.mkdir(parents=True, exist_ok=True)
    out = work / "slope_native.tif"
    if out.exists():
        print(f"  slope: native raster exists, reusing {out}")
        return out
    print("  slope: gdaldem slope on the archive (degrees, true metres)")
    run([GDAL_BIN / "gdaldem", "slope", archive, out,
         "-compute_edges", "-s", "1",
         "-co", "TILED=YES", "-co", "COMPRESS=ZSTD", "-co", "PREDICTOR=3",
         "-co", "BIGTIFF=YES", "-co", "NUM_THREADS=ALL_CPUS"], env=env)
    return out


def quantise_slope(src_path, dst_path):
    """Float32 degrees -> uint8 in SLOPE_STEP_DEG steps, SLOPE_NODATA where empty."""
    with rasterio.open(src_path) as src:
        prof = src.profile.copy()
        prof.update(driver="GTiff", dtype="uint8", count=1, nodata=SLOPE_NODATA,
                    tiled=True, blockxsize=512, blockysize=512,
                    compress="deflate", zlevel=6, bigtiff="YES",
                    predictor=1, num_threads="ALL_CPUS")
        nodata = src.nodata
        with rasterio.open(dst_path, "w", **prof) as dst:
            for _, win in src.block_windows(1):
                a = src.read(1, window=win)
                valid = np.isfinite(a) & (a >= 0)
                if nodata is not None:
                    valid &= a != nodata
                q = np.rint(np.clip(a, 0, 90) / SLOPE_STEP_DEG).astype(np.uint8)
                dst.write(np.where(valid, q, SLOPE_NODATA).astype(np.uint8), 1, window=win)


def build_slope_web(ds, archive, env, bounds):
    """Per-zoom slope tiles, mirroring build_web's warps on the slope raster."""
    work = BUILD / ds["id"]
    tiles = work / "tiles_slope"
    # Same trap as the elevation pyramid. A constant vertical shift leaves
    # slope untouched, but a resampling or extent change does not.
    drop_stale_tiles(tiles, archive, work, ("slope_z*.tif", "slopeq_z*.tif"),
                     mbtiles=work / f"{ds['id']}_slope.mbtiles")
    native = build_slope_native(ds, archive, env)
    minz, maxz = ds["min_zoom"], ds["max_zoom"]
    for z in range(maxz, minz - 1, -1):
        res = res_at_zoom(z)
        warped = work / f"slope_z{z}.tif"
        q = work / f"slopeq_z{z}.tif"
        te = snap_extent(bounds, res)
        resamp = "bilinear" if z == maxz else "average"
        print(f"  slope z{z}: warp -> {res:.4f} m/px ({resamp})")
        run([GDAL_BIN / "gdalwarp", "-overwrite",
             "-s_srs", f"EPSG:{ds['target_epsg']}",
             "-t_srs", "EPSG:3857",
             "-te", *[f"{v:.10f}" for v in te],
             "-tr", f"{res:.12f}", f"{res:.12f}",
             "-r", resamp,
             "-dstnodata", "-9999",
             "-ot", "Float32",
             "-multi", "-wo", "NUM_THREADS=ALL_CPUS",
             "-co", "TILED=YES", "-co", "COMPRESS=ZSTD", "-co", "BIGTIFF=YES",
             native, warped], env=env)
        print(f"  slope z{z}: quantise + tile")
        quantise_slope(warped, q)
        run([GDAL_BIN / "gdal", "raster", "tile",
             "--min-zoom", z, "--max-zoom", z,
             "-r", "nearest", "--convention", "xyz",
             "--skip-blank", "--resume", "--webviewer", "none",
             "-j", "ALL_CPUS", "--no-intersection-ok",
             q, tiles], env=env)
        warped.unlink(missing_ok=True)
        q.unlink(missing_ok=True)
    return tiles


def merc_bounds(archive, env):
    """3857 bounds of the archive, from its WGS84 extent."""
    info = json.loads(subprocess.run(
        [str(GDAL_BIN / "gdalinfo"), "-json", str(archive)],
        check=True, capture_output=True, text=True, env=env).stdout)
    ext = info["wgs84Extent"]["coordinates"][0]
    lons = [p[0] for p in ext]
    lats = [p[1] for p in ext]

    def to_merc(lon, lat):
        x = MERC_SPAN / 2 * lon / 180.0
        y = MERC_SPAN / 2 * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) / math.pi
        return x, y

    corners = [to_merc(lo, la) for lo in (min(lons), max(lons))
               for la in (min(lats), max(lats))]
    return (min(c[0] for c in corners), min(c[1] for c in corners),
            max(c[0] for c in corners), max(c[1] for c in corners))


# --------------------------------------------------------------------------
# stage 3: XYZ directory -> MBTiles -> PMTiles
# --------------------------------------------------------------------------

def dir_to_mbtiles(tiles, mb_path, ds, fmt, description=None):
    """MBTiles stores TMS y (origin bottom-left); our tiles are XYZ (top-left)."""
    if mb_path.exists():
        mb_path.unlink()
    con = sqlite3.connect(mb_path)
    con.executescript("""
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE metadata (name TEXT, value TEXT);
        CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER,
                            tile_row INTEGER, tile_data BLOB);
    """)
    n = 0
    for zdir in sorted(tiles.iterdir()):
        if not zdir.is_dir() or not zdir.name.isdigit():
            continue
        z = int(zdir.name)
        for xdir in zdir.iterdir():
            if not xdir.is_dir() or not xdir.name.isdigit():
                continue
            x = int(xdir.name)
            rows = []
            for tile in xdir.iterdir():
                stem = tile.stem
                if not stem.isdigit() or tile.suffix.lstrip(".") != fmt:
                    continue
                y = 2 ** z - 1 - int(stem)          # XYZ -> TMS
                rows.append((z, x, y, tile.read_bytes()))
            if rows:
                con.executemany("INSERT INTO tiles VALUES (?,?,?,?)", rows)
                n += len(rows)
        con.commit()
        print(f"    z{z}: {n} tiles cumulative", flush=True)
    con.execute("CREATE UNIQUE INDEX tile_index ON tiles "
                "(zoom_level, tile_column, tile_row)")
    for k, v in [("name", ds["title"]), ("format", fmt), ("type", "baselayer"),
                 ("minzoom", ds["min_zoom"]), ("maxzoom", ds["max_zoom"]),
                 ("description", description or f"Mapbox terrain-RGB DEM, {ds['title']}")]:
        con.execute("INSERT INTO metadata VALUES (?,?)", (k, str(v)))
    con.commit()
    con.close()
    return n


def build_pmtiles(ds, tiles, webp=False, suffix="", description=None):
    OUT_PM.mkdir(parents=True, exist_ok=True)
    fmt = "webp" if webp else "png"
    mb = BUILD / ds["id"] / f"{ds['id']}{suffix}.mbtiles"
    print("  packing MBTiles")
    n = dir_to_mbtiles(tiles, mb, ds, fmt, description)
    out = OUT_PM / f"{ds['id']}{suffix}.pmtiles"
    print(f"  converting {n} tiles -> {out}")
    run([PMTILES, "convert", mb, out])
    mb.unlink(missing_ok=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", nargs="?")
    ap.add_argument("--stage", default="all",
                    choices=["archive", "web", "pmtiles", "slope", "all"],
                    help="slope = the slope pyramid only (needs the archive)")
    ap.add_argument("--webp", action="store_true",
                    help="lossless WEBP tiles (~25%% smaller than PNG)")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    manifest = load_manifest()
    if args.list or not args.dataset:
        for k, d in manifest.items():
            print(f"{k:14s} z{d['min_zoom']}-{d['max_zoom']}  "
                  f"EPSG:{d['target_epsg']}  {d['src']}")
        return
    if args.dataset not in manifest:
        sys.exit(f"unknown dataset {args.dataset!r}; try --list")

    ds = manifest[args.dataset]
    env = clean_env()
    BUILD.mkdir(parents=True, exist_ok=True)
    print(f"== {ds['id']}: {ds['title']}")

    archive = OUT_COG / f"{ds['id']}.tif"
    if args.stage in ("archive", "all"):
        archive = build_archive(ds, env)
    tiles = BUILD / ds["id"] / "tiles"
    if args.stage in ("web", "all"):
        tiles = build_web(ds, archive, env, webp=args.webp)
    if args.stage in ("pmtiles", "all"):
        out = build_pmtiles(ds, tiles, webp=args.webp)
        print(f"== done: {out} ({out.stat().st_size / 2**30:.2f} GB)")
    if args.stage in ("slope", "all"):
        if not archive.exists():
            sys.exit(f"slope stage needs the archive: {archive}")
        stiles = build_slope_web(ds, archive, env, merc_bounds(archive, env))
        sout = build_pmtiles(ds, stiles, suffix="_slope",
                             description=f"Slope, degrees x{1 / SLOPE_STEP_DEG:g} as uint8 "
                                         f"(255 = nodata), {ds['title']}")
        print(f"== done: {sout} ({sout.stat().st_size / 2**20:.0f} MB)")
        (BUILD / ds["id"] / "slope_native.tif").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
