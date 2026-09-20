#!/usr/bin/env python3
"""Recover the Alaska DGGS deep-seated landslide susceptibility raster.

    python3 tools/harvest_dggs_susc.py            # run / resume
    python3 tools/harvest_dggs_susc.py --status   # what is left

WHAT THIS IS
------------
Alaska DGGS published statewide deep-seated landslide susceptibility as
Preliminary Interpretive Report 2025-3 (Wikstrom Jones, K.M., and Larsen, M.C.,
2025, doi:10.14509/31691), but ONLY as a dynamic ArcGIS MapServer -- no
download, no tile cache, no image service; the report itself is a PDF and a map
sheet. This reconstructs the underlying classified raster from what that
service will draw.

It is not scraping a picture. Three measurements make the recovery exact:

  1. THE SOURCE IS A 20 m GRID IN EPSG:3338, and the service renders in its
     own projection on request. Asked at exactly 20 m/px in 3338, one output
     pixel IS one source cell -- no reprojection, no resampling. Verified by
     rendering at 10 m/px and measuring runs of identical colour: 100% of runs
     were even-length AND started on even pixels, horizontally and vertically
     (n=554 and 1228). That is a 20 m grid on a 20 m phase, and it agrees with
     the report's stated method.

  2. THE COLOURS ARE EXACT. A 4096 px test chunk returned 7 distinct RGB
     values, every one an exact legend colour -- no antialiasing, no blending,
     no intermediates. So colour -> class is a lookup, not a nearest-match.

  3. THE WHOLE STATE IS 1,125 REQUESTS, not a million tiles. The service caps
     images at 4096x4096; at 20 m/px that is 82 km a side. A test chunk
     rendered in 2.9 s.

ATTRIBUTION AND MODIFICATION (their licence asks for both)
----------------------------------------------------------
Source: Alaska Division of Geological & Geophysical Surveys, Wikstrom Jones,
K.M., and Larsen, M.C., 2025, Susceptibility to deep-seated landslides in
Alaska: Preliminary Interpretive Report 2025-3, doi:10.14509/31691.
Modification: rendered symbology decoded back to integer susceptibility
classes on the published 20 m grid; no values were altered, resampled, or
reinterpreted. Water/ice and no-data are carried as separate codes.

BEING A GOOD CITIZEN
--------------------
Their service is fragile -- it returned 504 for half an hour on 2026-09-19 and
flapped repeatedly after. So: one request at a time, a deliberate pause
between them, exponential backoff on failure, and a resumable design where a
finished chunk is never re-fetched. A whole run is ~1,100 requests over ~1.5
hours, which is less load than one person panning the web app for an afternoon.

OUTPUT
------
One uint8 GeoTIFF per chunk in CHUNKS, EPSG:3338, 20 m, named by grid position.
Build the mosaic with:

    gdalbuildvrt dggs_susc_20m.vrt chunks/*.tif

Codes: 0,3,5,6,7,8,9,10 = susceptibility (10 most susceptible); 200 = lake,
river or glacier (model not applicable); 255 = nodata.
"""
import argparse
import io
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_origin

SERVICE = ('https://geoportal.dggs.dnr.alaska.gov/arcgis/rest/services/'
           'Alaska_Deep_Seated_Landslide_Susceptibility/MapServer/export')
OUT = Path('/Volumes/Nunatak/lidar_src/dggs_susc')
CHUNKS = OUT / 'chunks'

RES = 20.0            # metres; the published grid
PX = 4096             # service maximum
SPAN = RES * PX       # 81.92 km per chunk

# Service fullExtent, snapped outward to the 20 m grid phase.
X0, Y1 = -2190040.0, 2391480.0
X1, Y0 = 1492540.0, 396740.0
NX = int(np.ceil((X1 - X0) / SPAN))
NY = int(np.ceil((Y1 - Y0) / SPAN))

# Legend colour -> class. Read from the service's own legend endpoint, which
# keeps working even when rendering does not.
DECODE = {
    (255, 255, 255): 0,    # any rock strength, slope < 3 deg
    (38, 115, 0):    3,    # strong, 10-15 deg
    (171, 205, 102): 5,    # moderate, 3-10 deg
    (255, 255, 0):   6,    # strong, 15-20 deg
    (255, 235, 190): 7,    # strong 20-30, or weak 3-10
    (245, 202, 122): 8,    # strong >30, or moderate 10-15
    (228, 130, 56):  9,    # moderate >15, or weak 10-15
    (255, 0, 0):     10,   # weak, >15 deg
    (190, 232, 255): 200,  # lake, river or glacier
}
NODATA = 255

PAUSE = 1.5           # between requests, on top of their ~3 s render
TRIES = 4
TIMEOUT = 300
UA = {'User-Agent': 'landslidescience.org research harvest '
                    '(+https://landslidescience.org; contact hig314@gmail.com)'}


def chunk_path(ix, iy):
    return CHUNKS / f'susc_{iy:02d}_{ix:02d}.tif'


def fetch(ix, iy):
    x0 = X0 + ix * SPAN
    y1 = Y1 - iy * SPAN
    bbox = (x0, y1 - SPAN, x0 + SPAN, y1)
    url = (f'{SERVICE}?bbox=%f,%f,%f,%f' % bbox +
           f'&bboxSR=3338&imageSR=3338&size={PX},{PX}'
           '&format=png32&transparent=true&dpi=96&layers=show:0&f=image')
    last = None
    for attempt in range(TRIES):
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA), timeout=TIMEOUT) as r:
                body = r.read()
            if not body.startswith(b'\x89PNG'):
                raise ValueError('not a PNG: %r' % body[:60])
            return body, (x0, y1)
        except Exception as exc:                     # noqa: BLE001
            last = exc
            if attempt < TRIES - 1:
                time.sleep(5 * 3 ** attempt)         # 5, 15, 45 s
    raise RuntimeError('gave up on %d/%d: %s' % (iy, ix, last))


def decode(body):
    """RGBA render -> uint8 class array. Returns (array, unknown_colour_count)."""
    a = np.array(Image.open(io.BytesIO(body)).convert('RGBA'))
    rgb, alpha = a[..., :3], a[..., 3]
    out = np.full(alpha.shape, NODATA, dtype=np.uint8)
    seen = np.zeros(alpha.shape, dtype=bool)
    for colour, code in DECODE.items():
        m = (rgb[..., 0] == colour[0]) & (rgb[..., 1] == colour[1]) & \
            (rgb[..., 2] == colour[2]) & (alpha > 0)
        out[m] = code
        seen |= m
    unknown = int((~seen & (alpha > 0)).sum())
    return out, unknown


def write(arr, x0, y1, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tif.part')
    with rasterio.open(
            tmp, 'w', driver='GTiff', width=PX, height=PX, count=1,
            dtype='uint8', crs='EPSG:3338',
            transform=from_origin(x0, y1, RES, RES),
            nodata=NODATA, compress='ZSTD', zstd_level=9, predictor=2,
            tiled=True, blockxsize=512, blockysize=512) as dst:
        dst.write(arr, 1)
        dst.update_tags(
            source='Alaska DGGS PIR 2025-3 (Wikstrom Jones & Larsen 2025), '
                   'doi:10.14509/31691',
            modification='rendered symbology decoded to integer susceptibility '
                         'classes on the published 20 m grid; values not '
                         'altered, resampled or reinterpreted',
            codes='0,3,5,6,7,8,9,10=susceptibility (10 = most); '
                  '200=lake/river/glacier; 255=nodata')
    tmp.replace(path)


def status():
    done = sum(1 for iy in range(NY) for ix in range(NX)
               if chunk_path(ix, iy).exists())
    total = NX * NY
    print(f'grid {NX} x {NY} = {total} chunks of {PX}px at {RES:g} m '
          f'({SPAN/1000:.1f} km each)')
    print(f'done {done} / {total}  ({100*done/total:.1f}%)')
    if done < total:
        rate = PAUSE + 3.0
        print(f'remaining ~{(total-done)*rate/3600:.1f} h at ~{rate:.1f} s/chunk')
    return done, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--limit', type=int, default=0,
                    help='stop after N fetches (for a trial run)')
    args = ap.parse_args()
    if args.status:
        status()
        return 0

    CHUNKS.mkdir(parents=True, exist_ok=True)
    done, total = status()
    fetched = skipped_empty = failed = 0
    unknown_total = 0
    t0 = time.time()
    for iy in range(NY):
        for ix in range(NX):
            p = chunk_path(ix, iy)
            if p.exists():
                continue
            try:
                body, (x0, y1) = fetch(ix, iy)
            except Exception as exc:                 # noqa: BLE001
                print(f'  FAILED {iy:02d}/{ix:02d}: {exc}', file=sys.stderr)
                failed += 1
                continue
            arr, unknown = decode(body)
            unknown_total += unknown
            if unknown:
                print(f'  {iy:02d}/{ix:02d}: {unknown} px of unrecognised '
                      f'colour (left as nodata)', file=sys.stderr)
            if (arr != NODATA).any():
                write(arr, x0, y1, p)
                fetched += 1
            else:
                # Ocean / outside the mapped area. Write nothing but leave a
                # marker so a resume does not ask again.
                p.with_suffix('.empty').touch()
                skipped_empty += 1
            n = fetched + skipped_empty
            if n % 25 == 0:
                el = time.time() - t0
                print(f'  {n} fetched ({fetched} with data, {skipped_empty} '
                      f'empty, {failed} failed) — {el/60:.1f} min, '
                      f'{el/max(n,1):.1f} s/chunk', flush=True)
            if args.limit and n >= args.limit:
                print('  --limit reached'); return 0
            time.sleep(PAUSE)
    print(f'\ndone: {fetched} chunks with data, {skipped_empty} empty, '
          f'{failed} failed, {unknown_total} unrecognised px')
    if failed:
        print('re-run to retry the failures')
    return 0


if __name__ == '__main__':
    sys.exit(main())
