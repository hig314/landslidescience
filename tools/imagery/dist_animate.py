#!/usr/bin/env python3
"""Animate OPERA DIST over an area, from the same GIBS tiles the map uses.

  python3 tools/imagery/dist_animate.py 61.19698,-146.96439 --km 8 \
      --from 2026-05-01 --to 2026-09-08 --out data/dist_animations/valdez.gif

WHY ACCUMULATION IS THE DEFAULT. The daily layer holds only the granules
acquired that day, and about half of Alaska goes unobserved on any given date.
Play those frames raw and the animation strobes: half of them blink to empty,
and the eye reads the flicker as change when it is only the satellite's
schedule. `--raw` shows exactly what each date published; the default carries
the last valid observation forward per pixel, so a frame means "everything
known up to this date" and the only thing that moves is actual disturbance.

Needs no Earthdata login — GIBS is open. Colours match the map overlay
(tools/dist_color_status.txt); nodata is drawn dark so "not observed" never
looks like "nothing happened".

WHAT THE DARK AREA MEANS. Two different things, and the animation separates
them: dark that fills in over the first few frames was cloud, dark that never
fills is PERMANENT SNOW AND ICE, which the product masks outright. Measured by
stacking a season of source granules and counting pixels never observed in any
of them: Columbia Glacier 32.4%, Portage 21.4%, Kachemak 7.8%, and a
non-glaciated Talkeetna site 0.0%. It is not water — of pixels DATA-MASK calls
water, only 2% go unobserved. Worth knowing before reading a glacier
foreland: a third of that scene is not "quiet", it is not surveyed.
"""
import argparse
import concurrent.futures as cf
import datetime
import io
import math
import pathlib
import re
import urllib.request

import numpy as np
from PIL import Image, ImageDraw

DEFAULT_OUT_DIR = pathlib.Path(__file__).resolve().parents[2] / 'data' / 'dist_animations'

GIBS = 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best'
LAYERS = {'alert': 'OPERA_L3_DIST-ALERT-HLS_Color_Index',
          'ann': 'OPERA_L3_DIST-ANN-HLS_Color_Index'}
TILE = ('{base}/{layer}/default/{date}/GoogleMapsCompatible_Level12'
        '/{z}/{y}/{x}.png')                       # note: z/y/x, not z/x/y
DOMAINS = ('{base}/wmts.cgi?SERVICE=WMTS&VERSION=1.0.0&REQUEST=DescribeDomains'
           '&LAYER={layer}&TILEMATRIXSET=GoogleMapsCompatible_Level12')

# GIBS palette -> ours. Same table as DIST_CLASSES in map.js; class 0 and
# anything unrecognised fall through to the "observed, no disturbance" tone.
PALETTE = {
    (18, 18, 18):    None,                  # no disturbance
    (0, 85, 85):     (237, 212, 215),       # first detection  <50%
    (137, 127, 78):  (229, 183, 169),       # provisional      <50%
    (222, 224, 67):  (197, 141, 99),        # confirmed        <50%
    (0, 136, 136):   (240, 51, 73),         # first detection >=50%
    (228, 135, 39):  (207, 66, 23),         # provisional     >=50%
    (224, 27, 7):    (110, 60, 23),         # confirmed       >=50%
    (119, 119, 119): (212, 205, 196),       # confirmed  <50% finished
    (221, 221, 221): (164, 141, 112),       # confirmed >=50% finished
}
NODATA_RGB = (24, 26, 30)          # never observed
CLEAR_RGB = (238, 236, 232)        # observed, nothing happening


def tilef(lon, lat, z):
    lat = max(-85.05112878, min(85.05112878, lat))
    n = 1 << z
    s = math.sin(math.radians(lat))
    return (lon + 180.0) / 360.0 * n, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n


def domain_dates(layer):
    url = DOMAINS.format(base=GIBS, layer=LAYERS[layer])
    xml = urllib.request.urlopen(url, timeout=30).read().decode()
    m = re.search(r'<Domain>([^<]*)</Domain>', xml)
    out = []
    for chunk in (m.group(1) if m else '').split(','):
        p = chunk.strip().split('/')
        if not p[0]:
            continue
        if len(p) < 2:
            out.append(p[0]); continue
        cur, end = datetime.date.fromisoformat(p[0]), datetime.date.fromisoformat(p[1])
        yearly = len(p) > 2 and p[2] == 'P1Y'
        while cur <= end:
            out.append(cur.isoformat())
            cur = cur.replace(year=cur.year + 1) if yearly else cur + datetime.timedelta(days=1)
    return out


def fetch(layer, date, z, x, y):
    url = TILE.format(base=GIBS, layer=LAYERS[layer], date=date, z=z, y=y, x=x)
    try:
        return np.array(Image.open(io.BytesIO(
            urllib.request.urlopen(url, timeout=30).read())).convert('RGBA'))
    except Exception:
        return None                      # 404 = no coverage, or a domain gap


def mosaic(layer, date, z, x0, y0, nx, ny, pool):
    """One date as an (H,W,4) RGBA array; alpha 0 where nothing was observed."""
    jobs = {(dx, dy): pool.submit(fetch, layer, date, z, x0 + dx, y0 + dy)
            for dy in range(ny) for dx in range(nx)}
    out = np.zeros((ny * 256, nx * 256, 4), np.uint8)
    for (dx, dy), fut in jobs.items():
        a = fut.result()
        if a is not None:
            out[dy * 256:(dy + 1) * 256, dx * 256:(dx + 1) * 256] = a
    return out


def recolour(rgba):
    """GIBS RGBA -> our palette. Returns (rgb, observed_mask)."""
    h, w = rgba.shape[:2]
    rgb = np.zeros((h, w, 3), np.uint8)
    observed = rgba[..., 3] > 0
    rgb[~observed] = NODATA_RGB
    rgb[observed] = CLEAR_RGB
    for gibs, ours in PALETTE.items():
        if ours is None:
            continue
        hit = observed & np.all(rgba[..., :3] == np.array(gibs, np.uint8), axis=-1)
        rgb[hit] = ours
    return rgb, observed


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('centre', metavar='LAT,LON')
    ap.add_argument('--km', type=float, default=8.0, help='half-width of the view')
    ap.add_argument('--zoom', type=int, default=11)
    ap.add_argument('--layer', choices=sorted(LAYERS), default='alert')
    ap.add_argument('--from', dest='start', default='2026-05-01')
    ap.add_argument('--to', dest='end', default=None)
    ap.add_argument('--every', type=int, default=1, help='use every Nth available date')
    ap.add_argument('--raw', action='store_true',
                    help='show each date alone (strobes); default accumulates')
    ap.add_argument('--ms', type=int, default=140, help='frame duration')
    # data/ is gitignored and volume-mounted, and is where every other
    # generated artefact in this repo lives. NOT /tmp: macOS cleans it, and
    # an animation worth looking at twice should not evaporate.
    ap.add_argument('--out', default=str(DEFAULT_OUT_DIR / 'dist.gif'))
    args = ap.parse_args()

    lat, lon = (float(v) for v in args.centre.split(','))
    dlat = args.km / 111.32
    dlon = args.km / (111.32 * math.cos(math.radians(lat)))
    z = args.zoom
    fx0, fy0 = tilef(lon - dlon, lat + dlat, z)
    fx1, fy1 = tilef(lon + dlon, lat - dlat, z)
    x0, y0 = int(fx0), int(fy0)
    nx, ny = int(fx1) - x0 + 1, int(fy1) - y0 + 1

    dates = [d for d in domain_dates(args.layer) if d >= args.start
             and (args.end is None or d <= args.end)][::args.every]
    if not dates:
        print('no dates in range'); return
    print('%s  z%d  %dx%d tiles  %d dates  %s .. %s'
          % (args.layer, z, nx, ny, len(dates), dates[0], dates[-1]))

    # crop box, so the GIF is the requested area rather than whole tiles
    cx0, cy0 = int(round((fx0 - x0) * 256)), int(round((fy0 - y0) * 256))
    cx1, cy1 = int(round((fx1 - x0) * 256)), int(round((fy1 - y0) * 256))

    frames = []
    state = None            # accumulated RGB
    seen = None             # accumulated observed mask
    n_obs = []
    with cf.ThreadPoolExecutor(16) as pool:
        for i, date in enumerate(dates):
            rgba = mosaic(args.layer, date, z, x0, y0, nx, ny, pool)
            rgb, observed = recolour(rgba)
            n_obs.append(float(observed.mean()))
            if args.raw:
                shown = rgb
            else:
                if state is None:
                    state = np.full_like(rgb, NODATA_RGB)
                    seen = np.zeros(observed.shape, bool)
                state[observed] = rgb[observed]     # carry the rest forward
                seen |= observed
                shown = state
            im = Image.fromarray(shown[cy0:cy1, cx0:cx1]).convert('RGB')
            d = ImageDraw.Draw(im)
            label = '%s   %s%s' % (date, args.layer.upper(),
                                   '' if args.raw else '  (cumulative)')
            d.rectangle([0, 0, im.width, 16], fill=(0, 0, 0))
            d.text((4, 4), label, fill=(235, 235, 235))
            d.text((4, im.height - 12),
                   'observed this date: %3.0f%%' % (100 * n_obs[-1]), fill=(150, 150, 150))
            frames.append(im)
            print('  %s  observed %5.1f%%' % (date, 100 * n_obs[-1]), flush=True)

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=args.ms, loop=0, optimize=True)
    print('\nwrote %s  (%d frames, %.1f MB)' % (out, len(frames), out.stat().st_size / 1e6))
    print('mean coverage per date: %.1f%%  |  dates with no coverage at all: %d'
          % (100 * float(np.mean(n_obs)), sum(1 for v in n_obs if v == 0)))


if __name__ == '__main__':
    main()
