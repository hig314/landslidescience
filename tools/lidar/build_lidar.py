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

def build_archive(ds, env):
    src = Path(ds["src"])
    if not src.exists():
        sys.exit(f"source missing: {src}")
    OUT_COG.mkdir(parents=True, exist_ok=True)
    dst = OUT_COG / f"{ds['id']}.tif"
    if dst.exists():
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
        run([GDAL_BIN / "gdal_translate", "-of", "COG", *args,
             "-co", "COMPRESS=ZSTD", "-co", "LEVEL=9",
             "-co", "OVERVIEW_RESAMPLING=AVERAGE",
             "-co", "BIGTIFF=YES", "-co", "NUM_THREADS=ALL_CPUS",
             src, dst], env=env)
        return dst

    print(f"  archive: -> EPSG:{target}")
    tmp = BUILD / ds["id"] / "archive_tmp.tif"
    tmp.parent.mkdir(parents=True, exist_ok=True)

    # -r bilinear: the archive is a reprojection at (near) native scale, not a
    # downsample. Nodata is normalised to -9999 so every dataset behaves the
    # same downstream regardless of what the vendor used.
    run([GDAL_BIN / "gdalwarp", "-overwrite",
         "-t_srs", f"EPSG:{target}",
         "-tr", ds["native_res_m"], ds["native_res_m"],
         "-r", "bilinear",
         "-dstnodata", "-9999",
         "-ot", "Float32",
         "-multi", "-wo", "NUM_THREADS=ALL_CPUS",
         "-co", "TILED=YES", "-co", "COMPRESS=ZSTD", "-co", "ZSTD_LEVEL=9",
         "-co", "BIGTIFF=YES",
         src, tmp], env=env)

    # Vertical unit conversion, if the vendor shipped feet. Done here, once, so
    # that BOTH products (the download COG and the terrain-RGB tiles) are in
    # metres and can never drift apart. Applied after the warp because gdalwarp
    # reprojects but does not rescale pixel values.
    scale = ds.get("vertical_scale")
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

    print("  archive: rewriting as COG with overviews")
    run([GDAL_BIN / "gdal_translate", "-of", "COG",
         "-co", "COMPRESS=ZSTD", "-co", "LEVEL=9",
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


def build_web(ds, archive, env, webp=False):
    work = BUILD / ds["id"]
    work.mkdir(parents=True, exist_ok=True)
    tiles = work / "tiles"
    minz, maxz = ds["min_zoom"], ds["max_zoom"]

    # 3857 bounds of the archive, once.
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
    bounds = (min(c[0] for c in corners), min(c[1] for c in corners),
              max(c[0] for c in corners), max(c[1] for c in corners))

    for z in range(maxz, minz - 1, -1):
        res = res_at_zoom(z)
        elev = work / f"elev_z{z}.tif"
        rgb = work / f"rgb_z{z}.tif"
        te = snap_extent(bounds, res)

        # average when genuinely downsampling; bilinear at max zoom where the
        # scale change is near unity and averaging would just blur the lidar.
        resamp = "bilinear" if z == maxz else "average"
        print(f"  z{z}: warp -> {res:.4f} m/px ({resamp})")
        run([GDAL_BIN / "gdalwarp", "-overwrite",
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
# stage 3: XYZ directory -> MBTiles -> PMTiles
# --------------------------------------------------------------------------

def dir_to_mbtiles(tiles, mb_path, ds, fmt):
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
                 ("description", f"Mapbox terrain-RGB DEM, {ds['title']}")]:
        con.execute("INSERT INTO metadata VALUES (?,?)", (k, str(v)))
    con.commit()
    con.close()
    return n


def build_pmtiles(ds, tiles, webp=False):
    OUT_PM.mkdir(parents=True, exist_ok=True)
    fmt = "webp" if webp else "png"
    mb = BUILD / ds["id"] / f"{ds['id']}.mbtiles"
    print("  packing MBTiles")
    n = dir_to_mbtiles(tiles, mb, ds, fmt)
    out = OUT_PM / f"{ds['id']}.pmtiles"
    print(f"  converting {n} tiles -> {out}")
    run([PMTILES, "convert", mb, out])
    mb.unlink(missing_ok=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", nargs="?")
    ap.add_argument("--stage", default="all",
                    choices=["archive", "web", "pmtiles", "all"])
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


if __name__ == "__main__":
    main()
