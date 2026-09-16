"""Sentinel-2 by date and place, without downloading a scene.

WHY THIS IS CHEAP
-----------------
The Sentinel-2 L2A archive is published as cloud-optimised GeoTIFFs on public
S3 with no credentials, and indexed by a STAC API. So finding the right scene
is one HTTP query, and reading a 12 km window out of it is a handful of range
requests: measured at 8.9 MB and 48 s against roughly 1 GB for a whole granule.
That is what makes an in-app "show me this date here" button possible at all.

WHAT IS STORED
--------------
The linkage, and nothing else that matters: scene id, capture time, centre,
window size, cloud cover, and the asset URLs. `rebake()` turns that record
back into identical tiles at any time, so the baked pyramid is a cache rather
than an original. No imagery is held on our side that could not be recreated
from the record, which is also why these may be served publicly: Copernicus
data is free to redistribute, and we are not republishing a scene, only a
rendering of a window of one.

FALSE COLOUR BY DEFAULT
-----------------------
Near-infrared over red and green. A fresh landslide scar is bare rock and soil
with no chlorophyll, so it reads bright and neutral against vegetation that
near-infrared makes vivid. On natural colour the same scar is a grey smudge
among grey rock. That difference is the whole reason for the render mode.
"""
import json
import subprocess
import urllib.request
from datetime import date, datetime, timedelta

STAC = "https://earth-search.aws.element84.com/v1/search"
COLLECTION = "sentinel-2-l2a"
# R, G, B, NIR. bake_trace reads red/green/blue from the colour interpretation
# and takes the last band as near-infrared, so one file serves both renders.
BANDS = ["red", "green", "blue", "nir"]
DEFAULT_HALF_M = 6000.0
UA = "landslidescience/1 (+https://landslidescience.org)"


def search(lon, lat, when, days=7, limit=40):
    """Scenes covering a point within `days` either side of `when`.

    -> [{scene, datetime, cloud_cover, epsg, assets{band: href}}], soonest
    first by absolute distance from the requested date. Cloud cover is the
    scene's, not the window's, so it is a hint and not a verdict: a 60% scene
    can be perfectly clear over one valley.
    """
    if isinstance(when, str):
        when = datetime.strptime(when[:10], "%Y-%m-%d").date()
    if isinstance(when, datetime):
        when = when.date()
    lo, hi = when - timedelta(days=days), when + timedelta(days=days)
    body = json.dumps({
        "collections": [COLLECTION],
        "intersects": {"type": "Point", "coordinates": [float(lon), float(lat)]},
        "datetime": f"{lo}T00:00:00Z/{hi}T23:59:59Z",
        "limit": limit,
    }).encode()
    req = urllib.request.Request(STAC, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        doc = json.load(r)
    out = []
    for f in doc.get("features", []):
        p = f.get("properties", {})
        a = f.get("assets", {})
        if not all(b in a for b in BANDS):
            continue
        out.append({
            "scene": f["id"],
            "datetime": p.get("datetime", ""),
            "date": (p.get("datetime") or "")[:10],
            "cloud_cover": p.get("eo:cloud_cover"),
            "epsg": p.get("proj:epsg"),
            "assets": {b: a[b]["href"] for b in BANDS},
        })
    out.sort(key=lambda s: (abs((datetime.strptime(s["date"], "%Y-%m-%d").date() - when).days),
                            s["cloud_cover"] if s["cloud_cover"] is not None else 100))
    return out


def fetch_window(scene, lon, lat, half_m, out_tif):
    """Read the window out of the remote COGs into a 4-band GeoTIFF.

    Only the blocks the window touches are transferred. The bands are stacked
    R, G, B, NIR with the colour interpretation set, because that is what
    bake_trace keys off.
    """
    import numpy as np
    import rasterio
    from rasterio.enums import ColorInterp
    from rasterio.warp import transform
    from rasterio.windows import from_bounds

    order = [("red", ColorInterp.red), ("green", ColorInterp.green),
             ("blue", ColorInterp.blue), ("nir", ColorInterp.undefined)]
    first = scene["assets"]["red"]
    with rasterio.open(first) as s:
        xs, ys = transform("EPSG:4326", s.crs, [float(lon)], [float(lat)])
        x, y = xs[0], ys[0]
        win = from_bounds(x - half_m, y - half_m, x + half_m, y + half_m,
                          s.transform).round_offsets().round_lengths()
        prof = s.profile.copy()
        tr = s.window_transform(win)
    stack = []
    for band, _ in order:
        with rasterio.open(scene["assets"][band]) as s:
            stack.append(s.read(1, window=win))
    arr = np.stack(stack)
    if not arr.any():
        raise ValueError("window is entirely empty — off the granule edge?")
    prof.update(driver="GTiff", count=len(order), height=arr.shape[1], width=arr.shape[2],
                transform=tr, dtype=arr.dtype, compress="deflate", tiled=True,
                blockxsize=512, blockysize=512, nodata=0)
    with rasterio.open(out_tif, "w", **prof) as d:
        d.write(arr)
        d.colorinterp = [c for _, c in order]
    return out_tif


def bake(tif, raster_id, render):
    """Bake the window into the server's own PNG tile pyramid.

    Deliberately NOT tools/imagery/bake_trace.py. That tool packs WebP into a
    PMTiles archive with the `pmtiles` binary, which exists on a workstation
    and not in the app image -- and should not have to. The server already
    bakes uploads into PNG pyramids with the same stretch, gamma and false
    colour, so a Sentinel window goes through the identical path and comes out
    looking like everything else. A 12 km window is about 170 tiles.
    """
    import shutil

    from . import raster_tiles
    out_dir = raster_tiles.mode_dir(raster_id, render)
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ".started").touch()
    meta = raster_tiles._bake(str(tif), out_dir, render=render)
    (out_dir / ".complete").touch()
    return meta
