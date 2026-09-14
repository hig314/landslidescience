#!/usr/bin/env python3
"""bake_context.py -- the terrain context under every hosted survey, pre-baked.

Output: data/lidar/pmtiles/ctx_3dep.pmtiles, Mapbox terrain-RGB, z5-z13, from the
USGS 3DEP 1/3 arc-second seamless DEM (1x1 degree COGs, prd-tnm S3) for every
cell within ~0.5 deg lon / 0.25 deg lat of a survey in the public catalog.

WHY
---
The viewer composites each survey over "context" so a survey sits on real
ground in 3D rather than a 0 m plane. Until 2026-09-13 that context was a live
3DEP ImageServer exportImage per 256 px tile: ~1.4 s each, four at a time, and
the least reliable thing a page load depended on (it threw CORS errors under
load). The same data baked once into one PMTiles archive on R2 is read like any
survey -- a range request, cacheable per tile at the Cloudflare edge by the
tile Worker.

In Alaska the 1/3 arc-second product is what the 3DEP service itself draws for
nearly all of the state (5 m IfSAR resampled to ~10 m; lidar where 3DEP has
it). z13 is ~9.5 m ground at 60 N, so it is the natural top of the pyramid; the
client over-zooms it (demshade `overzoom`). Beyond the baked cells the client
still falls back to the live service, so nothing is lost at the far horizon.

HOW
---
Low zooms (z5-z10) are warped once from a VRT of every cell: at z10 the whole
union is ~15k x 12k px. High zooms (z11-z13) are warped per connected group of
cells, so the empty ocean between Southeast and Kachemak Bay is never
materialised; groups are at least a cell apart, so no z11+ tile spans two groups
and the shared tile directory never has one group overwrite another's tile.
Everything goes through build_lidar.build_web (per-zoom elevation warp, then
terrain-RGB encode, then exact tiling -- see its docstring for why) and
build_lidar.build_pmtiles.

  tools/lidar/bake_context.py [--cells-dir DIR] [--catalog PATH]
"""
import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_lidar as bl  # noqa: E402

CELLS_DIR = Path("/Volumes/Nunatak/lidar_src/usgs_3dep_13")
LON_PAD, LAT_PAD = 0.5, 0.25
LOW = (5, 10)
HIGH = (11, 13)


def wanted_cells(catalog):
    """USGS cell names (NW-corner convention: n60w152 = 59-60 N, 152-151 W)."""
    fc = json.loads(Path(catalog).read_text())
    cells = set()
    for f in fc["features"]:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        xs = [p[0] for poly in polys for r in poly for p in r]
        ys = [p[1] for poly in polys for r in poly for p in r]
        w, e = min(xs) - LON_PAD, max(xs) + LON_PAD
        s, n = min(ys) - LAT_PAD, max(ys) + LAT_PAD
        for lat in range(math.floor(s), math.ceil(n)):
            for lon in range(math.floor(w), math.ceil(e)):
                cells.add((lat + 1, -lon))
    return cells


def groups(cells):
    """8-connected components of the cell set."""
    left, out = set(cells), []
    while left:
        stack, comp = [left.pop()], set()
        while stack:
            c = stack.pop()
            comp.add(c)
            for dla in (-1, 0, 1):
                for dlo in (-1, 0, 1):
                    nb = (c[0] + dla, c[1] + dlo)
                    if nb in left:
                        left.remove(nb)
                        stack.append(nb)
        out.append(sorted(comp))
    return out


# Three coastal cells (n57w135, n60w152, n60w153 in 2026-09) mark open water
# with -9999 instead of the declared -999999 nodata. Warped as data, those become
# 10 km pits in the terrain and a sheer wall at every shoreline. No Alaskan
# ground is below -100 m, so anything under VOID_BELOW is a void.
VOID_BELOW = -500.0


def clean_cell(path, work):
    """The cell itself, or a cached copy with its undeclared voids set to nodata."""
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling
    with rasterio.open(path) as src:
        nd = src.nodata
        probe = src.read(1, out_shape=(src.height // 8, src.width // 8), resampling=Resampling.nearest)
        if not np.any((probe < VOID_BELOW) & (probe != nd)):
            return path
        out = work / "cells" / Path(path).name
        if out.exists():
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        print(f"  {Path(path).name}: undeclared voids (< {VOID_BELOW} m) -> nodata")
        prof = src.profile.copy()
        prof.update(driver="GTiff", tiled=True, blockxsize=512, blockysize=512,
                    compress="zstd", predictor=3, bigtiff="YES")
        tmp = out.with_suffix(".tmp.tif")
        with rasterio.open(tmp, "w", **prof) as dst:
            for _, win in src.block_windows(1):
                a = src.read(1, window=win)
                a[(a < VOID_BELOW) & (a != nd)] = nd
                dst.write(a, 1, window=win)
        tmp.rename(out)
        return out


def vrt(paths, out, env):
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"{p}\n" for p in paths))
    bl.run([bl.GDAL_BIN / "gdalbuildvrt", "-overwrite", "-input_file_list", lst, out], env=env)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells-dir", default=str(CELLS_DIR))
    ap.add_argument("--catalog", default=str(bl.ROOT / "data" / "lidar" / "catalog.geojson"))
    args = ap.parse_args()
    env = bl.clean_env()
    cells_dir = Path(args.cells_dir)

    cells = wanted_cells(args.catalog)
    have = {c: cells_dir / f"n{c[0]}w{c[1]}" / f"USGS_13_n{c[0]}w{c[1]}.tif" for c in cells}
    # USGS publishes no tile for an all-ocean cell (n58w138-140, n59w140 and
    # n59w151 around our surveys, 2026-09-14), so a cell absent on disk after
    # a completed download is simply sea: skip it.
    missing = sorted(c for c, p in have.items() if not p.exists())
    if missing:
        print(f"  {len(missing)} cells have no USGS tile (open water): "
              + " ".join(f"n{la}w{lo}" for la, lo in missing))
    have = {c: p for c, p in have.items() if p.exists()}

    ds = {"id": "ctx_3dep", "title": "USGS 3DEP 1/3 arc-second context",
          "target_epsg": 4269}
    work = bl.BUILD / ds["id"]
    work.mkdir(parents=True, exist_ok=True)
    tiles = work / "tiles"
    have = {c: clean_cell(p, work) for c, p in have.items()}

    print(f"== low zooms z{LOW[0]}-z{LOW[1]} from all {len(have)} cells")
    all_vrt = vrt(sorted(str(p) for p in have.values()), work / "all.vrt", env)
    bl.build_web(dict(ds, min_zoom=LOW[0], max_zoom=LOW[1]), all_vrt, env)

    comps = groups(have.keys())
    for i, comp in enumerate(comps):
        names = [f"n{la}w{lo}" for la, lo in comp]
        print(f"== high zooms z{HIGH[0]}-z{HIGH[1]}, group {i + 1}/{len(comps)}: {' '.join(names)}")
        gv = vrt([str(have[c]) for c in comp], work / f"group{i}.vrt", env)
        bl.build_web(dict(ds, min_zoom=HIGH[0], max_zoom=HIGH[1]), gv, env)

    out = bl.build_pmtiles(dict(ds, min_zoom=LOW[0], max_zoom=HIGH[1]), tiles,
                           description="Mapbox terrain-RGB DEM, USGS 3DEP 1/3 arc-second, "
                                       "around landslidescience.org lidar surveys")
    print(f"== done: {out} ({out.stat().st_size / 2**30:.2f} GB)")


if __name__ == "__main__":
    main()
