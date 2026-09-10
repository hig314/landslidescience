#!/usr/bin/env python3
"""Read OPERA DIST layers straight from the LP DAAC COGs, for one point.

WHY THIS EXISTS. NASA GIBS publishes only VEG-DIST-STATUS, so the map overlays
are blind above treeline — bare rock, talus, fresh debris on gravel. The GEN-*
(generic) layers, which are what could see those, exist only in the source
COGs. This is the tool for looking at them, and the question it was written to
answer is whether talus overturning is visible at all: undisturbed talus
weathers and grows lichen, fresh talus shows mineral surfaces, and that is a
spectral change even where there is no vegetation to lose.

Reads windows over /vsicurl (a few hundred kB per layer, no full download).
Needs ~/.netrc with a urs.earthdata.nasa.gov entry.

  python3 tools/imagery/dist_probe.py 61.19698,-146.96439 [more points...]
      [--radius-m 1500] [--since 2026-06-01] [--granules 1] [--png OUTDIR]

Prints, per point: the covering MGRS tile, recent granules, and for each
layer the value AT the point plus the distribution over the window — VEG-*
and GEN-* side by side, so "the vegetation product sees nothing but the
generic one does" is legible at a glance. --png writes a panel per layer.
"""
import argparse
import datetime
import json
import os
import sys
import urllib.parse
import urllib.request

os.environ.setdefault('GDAL_HTTP_COOKIEFILE', '/tmp/opera_dist_cookies')
os.environ.setdefault('GDAL_HTTP_COOKIEJAR', '/tmp/opera_dist_cookies')
os.environ.setdefault('GDAL_HTTP_NETRC', 'YES')
os.environ.setdefault('GDAL_DISABLE_READDIR_ON_OPEN', 'EMPTY_DIR')
os.environ.setdefault('CPL_VSIL_CURL_ALLOWED_EXTENSIONS', '.tif')

import numpy as np
import rasterio
from rasterio.warp import transform as warp_transform
from rasterio.windows import Window

CMR = 'https://cmr.earthdata.nasa.gov/search/granules.umm_json'
ALERT = 'C2746980408-LPCLOUD'
ANN = 'C2519119034-LPCLOUD'
EPOCH = datetime.date(2020, 12, 31)      # DIST-DATE / LAST-DATE day 0

# The pairs worth reading side by side. VEG-* is what GIBS shows; GEN-* is the
# generic twin that does not require vegetation to lose.
LAYERS = ['VEG-DIST-STATUS', 'GEN-DIST-STATUS',
          'VEG-ANOM-MAX', 'GEN-ANOM-MAX',
          'VEG-DIST-CONF', 'GEN-DIST-CONF',
          'VEG-DIST-DATE', 'GEN-DIST-DATE',
          'VEG-DIST-DUR', 'GEN-DIST-DUR',
          'VEG-DIST-COUNT', 'GEN-DIST-COUNT',
          'VEG-IND', 'VEG-HIST', 'DATA-MASK']
STATUS_NAMES = {0: 'no disturbance', 1: 'first detect <50%', 2: 'provisional <50%',
                3: 'confirmed <50%', 4: 'first detect >=50%', 5: 'provisional >=50%',
                6: 'confirmed >=50%', 7: 'confirmed <50% fin', 8: 'confirmed >=50% fin',
                255: 'no data'}


def cmr_granules(collection, lon, lat, since, limit):
    q = urllib.parse.urlencode({
        'collection_concept_id': collection, 'point': f'{lon},{lat}',
        'temporal': f'{since}T00:00:00Z,', 'page_size': limit, 'sort_key': '-start_date'})
    with urllib.request.urlopen(f'{CMR}?{q}', timeout=60) as r:
        return json.load(r)['items']


def layer_urls(granule):
    out = {}
    for rel in granule['umm'].get('RelatedUrls', []):
        if rel.get('Type') != 'GET DATA' or not rel['URL'].endswith('.tif'):
            continue
        out[rel['URL'].rsplit('_', 1)[-1][:-4]] = rel['URL']
    return out


def read_window(url, lon, lat, radius_m):
    """(centre value, window array, nodata). Window is radius_m about the point."""
    with rasterio.open('/vsicurl/' + url) as ds:
        xs, ys = warp_transform('EPSG:4326', ds.crs, [lon], [lat])
        row, col = ds.index(xs[0], ys[0])
        px = max(1, int(round(radius_m / abs(ds.transform.a))))
        r0, c0 = max(0, row - px), max(0, col - px)
        r1, c1 = min(ds.height, row + px + 1), min(ds.width, col + px + 1)
        if r1 <= r0 or c1 <= c0:
            return None, None, ds.nodata
        arr = ds.read(1, window=Window(c0, r0, c1 - c0, r1 - r0))
        centre = None
        if 0 <= row < ds.height and 0 <= col < ds.width:
            centre = arr[row - r0, col - c0]
        return centre, arr, ds.nodata


def day_to_date(v):
    """DIST-DATE day number -> ISO date. 0 means "never disturbed", NOT day
    zero of the epoch — printing it as 2020-12-31 makes undisturbed ground
    look like it changed the day the record opened."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return '-'
    return (EPOCH + datetime.timedelta(days=v)).isoformat() if v > 0 else '-'


def describe(layer, centre, arr, nodata):
    valid = arr[arr != nodata] if nodata is not None else arr.ravel()
    if valid.size == 0:
        return 'window entirely no-data'
    if layer.endswith('DIST-STATUS'):
        vals, counts = np.unique(valid, return_counts=True)
        top = sorted(zip(counts, vals), reverse=True)[:4]
        mix = ', '.join('%s %.1f%%' % (STATUS_NAMES.get(int(v), int(v)), 100 * c / valid.size)
                        for c, v in top)
        alerted = 100 * np.count_nonzero((valid > 0) & (valid < 255)) / valid.size
        return 'centre=%s | %.1f%% of window alerted | %s' % (
            STATUS_NAMES.get(int(centre), centre) if centre is not None else '-', alerted, mix)
    if layer.endswith('DATE'):
        pos = valid[valid > 0]
        if pos.size == 0:
            return 'centre=%s | no dated pixels in window' % day_to_date(centre)
        return 'centre=%s | window dated %s .. %s (median %s, n=%d)' % (
            day_to_date(centre), day_to_date(pos.min()), day_to_date(pos.max()),
            day_to_date(np.median(pos)), pos.size)
    nz = valid[valid > 0]
    # VEG-ANOM-MAX is uint8 percent-cover-lost (0-100); every GEN-* metric and
    # both *-DIST-CONF are int16 "unitless" 0-32000. Different scales entirely
    # — comparing their magnitudes is meaningless, so the units are printed.
    unit = 'percent' if layer == 'VEG-ANOM-MAX' else (
        'unitless 0-32000' if layer.startswith('GEN-ANOM') or layer.endswith('CONF') else '')
    return ('centre=%s | window mean %.1f max %d%s | %.1f%% nonzero%s' % (
        centre if centre is not None else '-', valid.mean(), valid.max(),
        (' [%s]' % unit) if unit else '', 100 * nz.size / valid.size,
        (' (nonzero mean %.1f)' % nz.mean()) if nz.size else ''))


def probe(lon, lat, args):
    print('=' * 78)
    print('POINT %.5f, %.5f     window +/- %d m' % (lat, lon, args.radius_m))
    print('=' * 78)
    gs = cmr_granules(ALERT, lon, lat, args.since, max(args.granules, 6))
    if not gs:
        print('  no DIST-ALERT granules since %s' % args.since)
        return
    tile = gs[0]['umm']['GranuleUR'].split('_')[3]
    print('  MGRS tile %s | %d granules since %s | most recent: %s'
          % (tile, len(gs), args.since,
             ', '.join(g['umm']['GranuleUR'].split('_')[4][:8] for g in gs[:6])))
    # DATA-MASK is the current acquisition's usable pixels (0 = no data or
    # masked, 1 = land, 2 = water). DIST-STATUS is carried-forward state and
    # stays populated through cloud, so the newest granule can report a full
    # status field over a window that was 95% cloud — and then VEG-IND, which
    # IS current, comes back all no-data. Rank by clear land instead.
    scored = []
    for g in gs:
        urls = layer_urls(g)
        if 'DATA-MASK' not in urls:
            continue
        try:
            _, arr, _ = read_window(urls['DATA-MASK'], lon, lat, args.radius_m)
        except Exception:
            continue
        if arr is None:
            continue
        land = float(np.count_nonzero(arr == 1)) / arr.size
        water = float(np.count_nonzero(arr == 2)) / arr.size
        scored.append((land, water, g))
    scored.sort(key=lambda t: -t[0])
    print('  clear-land fraction in window, by acquisition:')
    for land, water, g in scored:
        print('     %s  land %5.1f%%  water %5.1f%%  masked/cloud %5.1f%%'
              % (g['umm']['GranuleUR'].split('_')[4][:8], 100 * land, 100 * water,
                 100 * (1 - land - water)))
    if not scored:
        print('  no DATA-MASK available; falling back to newest')
        scored = [(0.0, 0.0, g) for g in gs]
    if scored[0][0] < 0.10:
        print('  !! best look is only %.1f%% clear land — treat everything below '
              'as unreliable' % (100 * scored[0][0]))

    for land, water, g in scored[:args.granules]:
        ur = g['umm']['GranuleUR']
        acq = ur.split('_')[4]
        print('\n  --- DIST-ALERT %s  (acquired %s-%s-%s, %s; %.0f%% clear land) ---'
              % (tile, acq[:4], acq[4:6], acq[6:8], ur.split('_')[-3], 100 * land))
        urls = layer_urls(g)
        for layer in LAYERS:
            if layer not in urls:
                continue
            try:
                centre, arr, nodata = read_window(urls[layer], lon, lat, args.radius_m)
            except Exception as exc:
                print('    %-16s READ FAILED: %s' % (layer, exc))
                continue
            if arr is None:
                print('    %-16s point outside the granule' % layer)
                continue
            print('    %-16s %s' % (layer, describe(layer, centre, arr, nodata)))
            if args.png:
                write_png(args.png, lat, lon, tile, acq[:8], layer, arr, nodata)
    # The annual product does not reset mid-year and is the better cumulative
    # read; the alert layer above is a snapshot of one acquisition.
    ann = cmr_granules(ANN, lon, lat, '2023-01-01', 5)
    if ann:
        print('\n  --- DIST-ANN (annual summary, no mid-year reset) ---')
        for g in ann[:args.ann]:
            ur_a = g['umm']['GranuleUR'].split('_')
            year = ur_a[4]          # ..._<TILE>_<YEAR>_...; [3] is the tile
            tile_a = ur_a[3]
            urls = layer_urls(g)
            bits = []
            for layer in ('VEG-DIST-STATUS', 'GEN-DIST-STATUS', 'VEG-ANOM-MAX', 'GEN-ANOM-MAX'):
                if layer not in urls:
                    continue
                try:
                    centre, arr, nodata = read_window(urls[layer], lon, lat, args.radius_m)
                except Exception:
                    continue
                if arr is None:
                    continue
                valid = arr[arr != nodata] if nodata is not None else arr.ravel()
                if valid.size == 0:
                    continue
                if layer.endswith('STATUS'):
                    pct = 100 * np.count_nonzero((valid > 0) & (valid < 255)) / valid.size
                    bits.append('%s %.1f%% alerted' % (layer[:3], pct))
                else:
                    bits.append('%s anom max %d mean %.1f' % (layer[:3], valid.max(), valid.mean()))
            print('    %s %s   %s' % (year, tile_a, ' | '.join(bits) if bits else 'no data'))


def write_png(outdir, lat, lon, tile, acq, layer, arr, nodata):
    from PIL import Image
    os.makedirs(outdir, exist_ok=True)
    a = arr.astype("float32")
    mask = (arr == nodata) if nodata is not None else np.zeros(arr.shape, bool)
    valid = a[~mask]
    if valid.size == 0:
        return
    lo, hi = float(valid.min()), float(valid.max())
    norm = np.zeros(a.shape, 'uint8') if hi <= lo else \
        np.clip((a - lo) / (hi - lo) * 255, 0, 255).astype('uint8')
    rgb = np.dstack([norm, norm, norm])
    rgb[mask] = (40, 0, 60)
    name = '%.5f_%.5f_%s_%s_%s.png' % (lat, lon, tile, acq, layer)
    Image.fromarray(rgb).resize((arr.shape[1] * 2, arr.shape[0] * 2), Image.NEAREST) \
        .save(os.path.join(outdir, name))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('points', nargs='+', metavar='LAT,LON')
    ap.add_argument('--radius-m', type=int, default=1500)
    ap.add_argument('--since', default='2026-06-01')
    ap.add_argument('--granules', type=int, default=1, help='DIST-ALERT granules per point')
    ap.add_argument('--ann', type=int, default=3, help='DIST-ANN years per point')
    ap.add_argument('--png', metavar='OUTDIR')
    args = ap.parse_args()
    for p in args.points:
        lat, lon = (float(v) for v in p.split(','))
        try:
            probe(lon, lat, args)
        except Exception as exc:
            print('  PROBE FAILED for %s: %s' % (p, exc))
        print()


if __name__ == '__main__':
    main()
