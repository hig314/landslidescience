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

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from rasterio import features  # noqa: E402
from rasterio.enums import Resampling  # noqa: E402
from rasterio.transform import Affine  # noqa: E402
from rasterio.warp import transform_geom  # noqa: E402
from shapely.geometry import MultiPolygon, mapping, shape  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
GDAL_BIN = Path(os.environ.get("GDAL_BIN", "/opt/homebrew/bin"))

BUILD = Path(os.environ.get("LIDAR_BUILD", "/Volumes/Nunatak/lidar_build"))
COG_DIR = Path(os.environ.get("LIDAR_COG_OUT", BUILD / "cog"))
PM_DIR = Path(os.environ.get("LIDAR_PM_OUT", ROOT / "data" / "lidar" / "pmtiles"))
# Where the archive COGs are served from. They live in Cloudflare R2 behind the
# bucket's custom domain (CDN-cached, CORS for Range/ETag), not on the droplet:
# 38 GB does not fit there and a 12 GB download must never pass through the two
# gunicorn workers. Pushed by tools/lidar/r2_sync.sh into <bucket>/cog/.
COG_PUBLIC_BASE = os.environ.get("LIDAR_COG_PUBLIC_BASE",
                                 "https://lidar.landslidescience.org/cog")
# The web pyramids moved to R2 too (2026-09-11): 7.3 GB that no longer needs
# droplet disk, and every range read now comes off Cloudflare instead of two
# gunicorn workers. The bucket's CORS allowlist must include every origin that
# reads them (landslidescience.org and the dev origin do).
PMTILES_PUBLIC_BASE = os.environ.get(
    "LIDAR_PMTILES_PUBLIC_BASE", "https://lidar.landslidescience.org/pmtiles")

# Footprint detail. 0.0001 deg is ~11 m of latitude -- finer than anyone needs
# for "does this survey cover my slope?", and keeps the whole catalog small
# enough to ship as one eagerly-loaded GeoJSON.
SIMPLIFY_DEG = 0.0004
# 40 m: footprints are a where-is-there-data cue at z8-12, not a boundary
# product (the archive carries the exact edge). At 11 m the 25-survey catalog
# was 5.4 MB / 94k vertices (Glacier Bay alone 58k) and took a visitor on a
# slow link 25 s to load before the raster panel could work (2026-09-13);
# at 40 m it is 0.3 MB / 13k vertices with the same areas to 0.1 km2.
SIMPLIFY_M = 40.0
MIN_PART_KM2 = 0.02   # islands smaller than this add vertices, not information
# Trace against the coarsest overview still >= this on its long side.
FOOTPRINT_TARGET_PX = 4000


def footprint(path):
    """Trace where the archive actually has data, as a 4326 MultiPolygon.

    Reads the coarsest overview still >= FOOTPRINT_TARGET_PX on its long
    side, masks nodata, polygonises the mask and simplifies it. The nodata
    comparison is done at float32 on purpose: vendor GeoTIFFs kept in
    translate mode carry a decimal tag such as "-3.402823e+38" whose double
    value is not the float32 pixel written from it, so a double comparison
    can miss the sentinel. (Checked 2026-09-11: gdal_footprint, which this
    replaces, was in fact handling it -- the few box-shaped footprints, Glen
    Alps, Eagle River and Ketchikan 2024, are rectangular deliveries filled
    edge to edge. Areas agree to 0.1 km2 on every dataset.) A guard on
    anything below -1e30 covers a file whose tag was dropped altogether.
    """
    with rasterio.open(path) as src:
        long_side = max(src.width, src.height)
        factor = 1
        for f in src.overviews(1):
            if long_side / f >= FOOTPRINT_TARGET_PX:
                factor = f
        out_h = max(1, round(src.height / factor))
        out_w = max(1, round(src.width / factor))
        # Nearest keeps the overview's own values; the COG overviews were built
        # with AVERAGE, which already ignores nodata, so no sentinel bleeds in.
        a = src.read(1, out_shape=(out_h, out_w), resampling=Resampling.nearest)
        valid = np.isfinite(a)
        if src.nodata is not None:
            valid &= a != np.float32(src.nodata)
        valid &= a > -1e30
        if not valid.any():
            return None
        transform = src.transform * Affine.scale(src.width / out_w, src.height / out_h)
        polys = [shape(g) for g, v in features.shapes(valid.astype("uint8"), mask=valid,
                                                        transform=transform, connectivity=8)
                 if v == 1]
        geom = unary_union(polys)
        # Simplify in the source's own units; every archive is projected UTM,
        # where SIMPLIFY_M matches the old 0.0001 deg of latitude.
        tol = SIMPLIFY_M if src.crs.is_projected else SIMPLIFY_DEG
        geom = geom.simplify(tol, preserve_topology=True)
        # Drop specks smaller than a few overview cells: stray returns and
        # single-pixel islands add vertices, not information.
        cell = abs(transform.a * transform.e)
        parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        parts = [g for g in parts if g.area >= max(4 * cell, MIN_PART_KM2 * 1e6 if src.crs.is_projected else 0)]
        if not parts:
            return None
        gj = transform_geom(src.crs, "EPSG:4326", mapping(MultiPolygon(parts)), precision=5)
        if gj["type"] == "Polygon":
            gj = {"type": "MultiPolygon", "coordinates": [gj["coordinates"]]}
        return gj


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

    include_gated = "--include-gated" in sys.argv
    for ds in manifest["datasets"]:
        if ds.get("gated") and not include_gated:
            print(f"  skip {ds['id']}: gated (pass --include-gated for the admin catalog)", file=sys.stderr)
            continue
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
        slope_pm = PM_DIR / f"{did}_slope.pmtiles"
        slope_bytes = slope_pm.stat().st_size if slope_pm.exists() else None
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "id": did,
                "title": ds["title"],
                "region": ds.get("region", "Other"),
                "year": ds["year"],
                "product": ds["product"],
                "native_res_m": round(ds["native_res_m"], 4),
                "horizontal_crs": f"EPSG:{ds['target_epsg']}",
                "vertical_datum": ds.get("vertical_datum"),
                "min_zoom": ds["min_zoom"],
                "max_zoom": ds["max_zoom"],
                "grid": f"{width} x {height}",
                "coverage_km2": round(area, 1),
                "pmtiles_url": f"{PMTILES_PUBLIC_BASE}/{did}.pmtiles",
                "pmtiles_bytes": pm_bytes,
                # Companion slope pyramid (build_lidar.py --stage slope): 8-bit
                # degrees in slope_step steps, 255 = nodata. Absent until built;
                # the client then falls back to its in-browser gradient.
                "slope_url": f"{PMTILES_PUBLIC_BASE}/{did}_slope.pmtiles" if slope_bytes else None,
                "slope_bytes": slope_bytes,
                "slope_step": 0.5 if slope_bytes else None,
                "cog_url": f"{COG_PUBLIC_BASE}/{did}.tif",
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
