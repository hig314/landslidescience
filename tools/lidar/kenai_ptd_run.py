#!/usr/bin/env python3
"""Ground-classify the Kenai 2008 point cloud with progressive TIN densification.

  kenai_ptd_run.py [--tiles N] [--jobs N] [--dry-run] [--only TILEID]

WHAT IT DOES, PER TILE
  1. readers.ept on the LOCAL octree, bounds = tile + 30 m buffer, noise
     classes 7 and 10 dropped, reprojected 3857 -> 6334 (NAD83(2011) UTM 5N),
     written as an UNCOMPRESSED LAS scratch file.
  2. X/Y/Z read straight out of that file with numpy and piped to ptd_ground.
  3. The classification byte is rewritten in place: 2 for ground, 1 for the
     rest. Every other attribute survives untouched.
  4. The classified tile is written as LAZ and KEPT, so the gridding can be
     redone -- a different cell size, a different interpolator -- without
     reclassifying 14 billion points.
  5. The tile's ground points are gridded to a DTM with bounds PINNED to the
     tile, so neighbouring tiles share a lattice exactly.

WHY THE LAS ROUND TRIP AND NOT writers.text
  The obvious pipe is PDAL -> text -> numpy -> text -> PDAL. Measured on a
  7 M point tile that costs 19 s against 61 s of actual classification, a 31%
  tax for moving numbers as ASCII. Reading the LAS point records with a numpy
  dtype and rewriting one byte per point costs a fraction of a second. There
  is no pdal-python or laspy in this environment, so the format is parsed
  here; it is a fixed-width record and the header says where it starts.

BUFFERS
  Every ground filter is weaker at the edge of its data, because it has no
  neighbourhood to judge against. Tiles are read with a 30 m buffer and the
  DTM is written to the unbuffered bounds, so every output cell was decided
  with real ground on all sides.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
from rasterio.warp import transform as rio_transform

PDAL = "/opt/homebrew/bin/pdal"
PTD = "/Volumes/Nunatak/lidar_build/ptd/ptd_ground"
EPT = "/Volumes/Nunatak/lidar_src/kenai_2008/ept/ept.json"
OUT = "/Volumes/Nunatak/lidar_build/kenai_2008"        # DTM tiles, ~17 GB
# The classified point cloud goes on the other drive. At 4.8 bytes a point it
# is about 67 GB for the survey, and Nunatak is also holding the Prince of
# Wales downloads; Powder has 393 GB spare. Keeping the classified tiles is the
# point of the exercise -- it means the gridding can be redone at another cell
# size or with another interpolator without reclassifying 14 billion points.
CLASSIFIED = "/Volumes/Powder/lidar_build/kenai_2008/classified"
SCRATCH = os.path.join(OUT, "scratch")

TILE = 2000.0          # m, in EPSG:6334
BUFFER = 30.0          # m, edge buffer for the classifier
CELL = 1.2192          # m, 4 ft, matching the rest of the Kenai surveys
EPSG = 6334
# angle 40 / distance 3 / seed 10: chosen on the Woodard patch against the 2019
# QL1 surface. Crest loss 11.9% against the vendor's 20.5%, the tightest spread
# and the fewest gross errors of six candidates.
PTD_ARGS = ["--angle", "40", "--distance", "3", "--seed-res", "10", "--spacing", "0.25"]


def ptd_supports_no_spikes():
    """Does the built binary have the outlier-detection switch?"""
    try:
        h = subprocess.run([PTD, "--help"], capture_output=True, text=True, timeout=30)
        return "--no-spikes" in (h.stdout + h.stderr)
    except Exception:
        return False


# ---- LAS, just enough of it -------------------------------------------------
def read_las(path):
    """-> (xyz float64 Nx3, header dict). Scaled coordinates, all points."""
    with open(path, "rb") as f:
        head = f.read(375)
    if head[:4] != b"LASF":
        raise ValueError(f"{path}: not a LAS file")
    ver = (head[24], head[25])
    offset = int.from_bytes(head[96:100], "little")
    fmt = head[104] & 0x3F
    reclen = int.from_bytes(head[105:107], "little")
    legacy_n = int.from_bytes(head[107:111], "little")
    scale = np.frombuffer(head[131:155], "<f8", 3)
    orig = np.frombuffer(head[155:179], "<f8", 3)
    n = legacy_n
    if ver >= (1, 4) and n == 0:
        n = int.from_bytes(head[247:255], "little")
    raw = np.memmap(path, dtype=np.uint8, mode="r", offset=offset, shape=(n * reclen,))
    recs = raw.reshape(n, reclen)
    ints = recs[:, 0:12].copy().view("<i4").reshape(n, 3)
    xyz = ints.astype(np.float64) * scale + orig
    # Classification sits at byte 15 for point formats 0-5 and byte 16 for 6-10.
    cls_off = 15 if fmt <= 5 else 16
    return xyz, {"offset": offset, "reclen": reclen, "n": n, "fmt": fmt, "cls_off": cls_off}


def write_classification(path, hdr, ground):
    """Set the classification byte in place: 2 where ground, 1 elsewhere."""
    n, reclen, off, cls_off = hdr["n"], hdr["reclen"], hdr["offset"], hdr["cls_off"]
    raw = np.memmap(path, dtype=np.uint8, mode="r+", offset=off, shape=(n * reclen,))
    recs = raw.reshape(n, reclen)
    # Formats 0-5 pack classification with three flag bits in the top of the
    # byte; formats 6-10 keep them in a separate byte. Preserve whatever is
    # there and only touch the class bits.
    if hdr["fmt"] <= 5:
        keep = recs[:, cls_off] & 0xE0
        recs[:, cls_off] = keep | np.where(ground, 2, 1).astype(np.uint8)
    else:
        recs[:, cls_off] = np.where(ground, 2, 1).astype(np.uint8)
    raw.flush()
    del raw


# ---- one tile ---------------------------------------------------------------
def tile_bounds_3857(x0, y0, x1, y1):
    """The 3857 box that covers a 6334 tile. Transformed corners plus a margin,
    because the two grids are not parallel and a corner-only box can clip."""
    px = np.repeat(np.linspace(x0, x1, 5), 5)
    py = np.tile(np.linspace(y0, y1, 5), 5)
    xs, ys = rio_transform(f"EPSG:{EPSG}", "EPSG:3857", list(px), list(py))
    m = 50.0
    return (min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m)


def run_tile(job):
    tid, x0, y0, no_spikes = job
    x1, y1 = x0 + TILE, y0 + TILE
    las = os.path.join(SCRATCH, f"{tid}.las")
    laz = os.path.join(CLASSIFIED, f"{tid}.laz")
    dtm = os.path.join(OUT, "dtm", f"{tid}.tif")
    if os.path.exists(laz) and os.path.exists(dtm):
        return tid, 0, 0, "skip"
    t0 = time.time()
    b = tile_bounds_3857(x0 - BUFFER, y0 - BUFFER, x1 + BUFFER, y1 + BUFFER)
    fetch = {"pipeline": [
        {"type": "readers.ept", "filename": EPT,
         "bounds": f"([{b[0]},{b[2]}],[{b[1]},{b[3]}])", "threads": 2},
        {"type": "filters.range", "limits": "Classification![7:7], Classification![10:10]"},
        {"type": "filters.reprojection", "in_srs": "EPSG:3857", "out_srs": f"EPSG:{EPSG}"},
        {"type": "writers.las", "filename": las, "compression": "none",
         "minor_version": 2, "dataformat_id": 1, "a_srs": f"EPSG:{EPSG}"}]}
    r = subprocess.run([PDAL, "pipeline", "--stdin"], input=json.dumps(fetch),
                       text=True, capture_output=True)
    if r.returncode or not os.path.exists(las):
        return tid, 0, time.time() - t0, "fetch failed: " + r.stderr.strip()[-120:]
    xyz, hdr = read_las(las)
    if hdr["n"] == 0:
        os.remove(las)
        return tid, 0, time.time() - t0, "empty"

    args = [PTD] + PTD_ARGS + (["--no-spikes"] if no_spikes else [])
    p = subprocess.run(args, input=np.ascontiguousarray(xyz).tobytes(), capture_output=True)
    if p.returncode:
        os.remove(las)
        return tid, 0, time.time() - t0, "ptd failed: " + p.stderr.decode().strip()[-120:]
    flags = np.frombuffer(p.stdout, dtype=np.uint8)
    ground = flags == 1
    write_classification(las, hdr, ground)

    # Keep the classified tile, then grid its ground points on the pinned lattice.
    os.makedirs(os.path.dirname(laz), exist_ok=True)
    os.makedirs(os.path.dirname(dtm), exist_ok=True)
    out = {"pipeline": [
        las,
        {"type": "filters.crop", "bounds": f"([{x0},{x1}],[{y0},{y1}])"},
        {"type": "writers.las", "filename": laz, "compression": "laszip",
         "forward": "all", "a_srs": f"EPSG:{EPSG}"},
        {"type": "filters.range", "limits": "Classification[2:2]"},
        {"type": "writers.gdal", "filename": dtm, "resolution": CELL,
         "output_type": "idw", "radius": 1.75, "window_size": 4,
         "bounds": f"([{x0},{x1}],[{y0},{y1}])",
         "gdaldriver": "GTiff", "data_type": "float", "nodata": -9999,
         "gdalopts": "COMPRESS=ZSTD,PREDICTOR=3,TILED=YES"}]}
    r2 = subprocess.run([PDAL, "pipeline", "--stdin"], input=json.dumps(out),
                        text=True, capture_output=True)
    os.remove(las)
    if r2.returncode:
        return tid, hdr["n"], time.time() - t0, "grid failed: " + r2.stderr.strip()[-120:]
    return tid, hdr["n"], time.time() - t0, "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--tiles", type=int, default=0, help="stop after N tiles (a trial run)")
    ap.add_argument("--only", help="run one tile id")
    ap.add_argument("--bbox", help="x0,y0,x1,y1 in EPSG:6334; run only tiles meeting it")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    os.makedirs(SCRATCH, exist_ok=True)
    no_spikes = ptd_supports_no_spikes()
    ept = json.load(open(EPT))
    bb = ept["bounds"]
    cx = np.repeat(np.linspace(bb[0], bb[3], 9), 9)
    cy = np.tile(np.linspace(bb[1], bb[4], 9), 9)
    xs, ys = rio_transform("EPSG:3857", f"EPSG:{EPSG}", list(cx), list(cy))
    x0 = np.floor(min(xs) / TILE) * TILE
    y0 = np.floor(min(ys) / TILE) * TILE
    nx = int(np.ceil((max(xs) - x0) / TILE))
    ny = int(np.ceil((max(ys) - y0) / TILE))
    jobs = []
    for i in range(nx):
        for j in range(ny):
            tx, ty = x0 + i * TILE, y0 + j * TILE
            jobs.append((f"t_{int(tx)}_{int(ty)}", tx, ty, no_spikes))
    if a.bbox:
        bx0, by0, bx1, by1 = (float(v) for v in a.bbox.split(","))
        jobs = [j for j in jobs
                if not (j[1] + TILE < bx0 or j[1] > bx1 or j[2] + TILE < by0 or j[2] > by1)]
    if a.only:
        jobs = [j for j in jobs if j[0] == a.only]
    if a.tiles:
        jobs = jobs[:a.tiles]

    print(f"kenai ptd: {len(jobs):,} tiles of {TILE:.0f} m, {a.jobs} at a time, "
          f"outlier detection {'OFF' if no_spikes else 'ON'}", flush=True)
    print(f"  ept    {EPT}\n  out    {OUT}", flush=True)
    if a.dry_run:
        for j in jobs[:5]:
            print("   ", j[0], j[1], j[2])
        return

    t0 = time.time()
    done = pts = empty = failed = 0
    with ProcessPoolExecutor(max_workers=a.jobs) as ex:
        futs = {ex.submit(run_tile, j): j[0] for j in jobs}
        for f in as_completed(futs):
            tid, n, dt, status = f.result()
            done += 1
            if status == "ok":
                pts += n
            elif status == "empty":
                empty += 1
            elif status != "skip":
                failed += 1
                print(f"  [{done}/{len(jobs)}] {tid}: {status}", flush=True)
            if done % 25 == 0 or done == len(jobs):
                el = time.time() - t0
                rate = pts / el if el else 0
                left = (len(jobs) - done) * el / max(done, 1)
                print(f"  [{done}/{len(jobs)}] {pts:,} pts  {rate:,.0f} pts/s  "
                      f"elapsed {el/3600:.2f} h  eta {left/3600:.2f} h  "
                      f"empty {empty}  failed {failed}", flush=True)
    print(f"done: {done} tiles, {pts:,} points, {empty} empty, {failed} failed, "
          f"{(time.time()-t0)/3600:.2f} h", flush=True)


if __name__ == "__main__":
    main()
