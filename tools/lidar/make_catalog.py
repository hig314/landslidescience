#!/usr/bin/env python3
"""make_catalog.py -- build data/lidar/catalog.geojson.

This is the piece that answers "what lidar exists, where, from when, and how do
I actually get it" -- the question the public sources answer badly. One feature
per hosted dataset, carrying the real data footprint plus the URLs of both
products.

The footprint is a REAL footprint, not a bounding box. These surveys are flight
strips and coastal ribbons: Homer's data covers ~95 km2 inside a 600 km2
bounding box, so a bbox would overstate coverage roughly six-fold and send
people looking for data that isn't there. gdal_footprint traces the actual
valid-data boundary; we run it against a coarse overview level so it costs
seconds rather than a full pass over 80 GB.

Usage:  tools/lidar/make_catalog.py [--out PATH]
"""

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

for _var in ("PROJ_LIB", "PROJ_DATA"):
    os.environ.pop(_var, None)

import rasterio  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
GDAL_BIN = Path(os.environ.get("GDAL_BIN", "/opt/homebrew/bin"))

BUILD = Path(os.environ.get("LIDAR_BUILD", "/Volumes/Nunatak/lidar_build"))
COG_DIR = Path(os.environ.get("LIDAR_COG_OUT", BUILD / "cog"))
PM_DIR = Path(os.environ.get("LIDAR_PM_OUT", ROOT / "data" / "lidar" / "pmtiles"))

# Footprint detail. 0.0001 deg is ~11 m of latitude -- finer than anyone needs
# for "does this survey cover my slope?", and keeps the whole catalog small
# enough to ship as one eagerly-loaded GeoJSON.
SIMPLIFY_DEG = 0.0001
# Trace against the coarsest overview still >= this on its long side.
FOOTPRINT_TARGET_PX = 4000


def pick_overview(path):
    """Index of the coarsest overview whose long side is still >= target."""
    with rasterio.open(path) as src:
        factors = src.overviews(1)
        long_side = max(src.width, src.height)
    best = None
    for i, f in enumerate(factors):
        if long_side / f >= FOOTPRINT_TARGET_PX:
            best = i
    return best


def footprint(path):
    ovr = pick_overview(path)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "fp.geojson"
        cmd = [str(GDAL_BIN / "gdal_footprint"), "-q",
               "-t_srs", "EPSG:4326",
               "-max_points", "unlimited",
               "-simplify", str(SIMPLIFY_DEG),
               "-of", "GeoJSON", "-overwrite"]
        if ovr is not None:
            cmd += ["-ovr", str(ovr)]
        cmd += [str(path), str(out)]
        subprocess.run(cmd, check=True)
        fc = json.loads(out.read_text())
    feats = fc.get("features") or []
    if not feats:
        return None
    return feats[0]["geometry"]


def geom_area_km2(geom):
    """Spherical-excess-free approximation: good enough for a coverage number."""
    def ring_area(ring):
        # equirectangular projection about the ring's own mean latitude
        lat0 = sum(p[1] for p in ring) / len(ring)
        k = math.cos(math.radians(lat0))
        s = 0.0
        for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
            s += (x1 * k) * y2 - (x2 * k) * y1
        return abs(s) / 2 * (111320.0 ** 2) / 1e6

    polys = ([geom["coordinates"]] if geom["type"] == "Polygon"
             else geom["coordinates"])
    total = 0.0
    for poly in polys:
        if not poly:
            continue
        total += ring_area(poly[0])
        for hole in poly[1:]:
            total -= ring_area(hole)
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "lidar" / "catalog.geojson"))
    args = ap.parse_args()

    manifest = json.loads((HERE / "datasets.json").read_text())
    features = []

    for ds in manifest["datasets"]:
        did = ds["id"]
        cog = COG_DIR / f"{did}.tif"
        pm = PM_DIR / f"{did}.pmtiles"
        if not cog.exists():
            print(f"  skip {did}: no archive COG yet", file=sys.stderr)
            continue

        print(f"  footprint: {did}", file=sys.stderr)
        geom = footprint(cog)
        if geom is None:
            print(f"  skip {did}: empty footprint", file=sys.stderr)
            continue
        area = geom_area_km2(geom)

        with rasterio.open(cog) as src:
            width, height = src.width, src.height

        pm_bytes = pm.stat().st_size if pm.exists() else None
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "id": did,
                "title": ds["title"],
                "year": ds["year"],
                "product": ds["product"],
                "native_res_m": round(ds["native_res_m"], 4),
                "horizontal_crs": f"EPSG:{ds['target_epsg']}",
                "vertical_datum": ds.get("vertical_datum"),
                "min_zoom": ds["min_zoom"],
                "max_zoom": ds["max_zoom"],
                "grid": f"{width} x {height}",
                "coverage_km2": round(area, 1),
                "pmtiles_url": f"/lidar/pmtiles/{did}.pmtiles",
                "pmtiles_bytes": pm_bytes,
                "cog_url": f"/lidar/cog/{did}.tif",
                "cog_bytes": cog.stat().st_size,
                "notes": ds.get("notes", ""),
            },
        })

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"type": "FeatureCollection", "features": features}, indent=1))

    print(f"\nwrote {out}  ({len(features)} datasets)")
    for f in features:
        p = f["properties"]
        mb = (p["pmtiles_bytes"] or 0) / 2 ** 20
        per = mb / p["coverage_km2"] if p["coverage_km2"] else 0
        print(f"  {p['id']:14s} {p['coverage_km2']:8.1f} km2  "
              f"pmtiles {mb:7.1f} MB ({per:.2f} MB/km2)  "
              f"cog {p['cog_bytes'] / 2**30:5.2f} GB")


if __name__ == "__main__":
    main()
