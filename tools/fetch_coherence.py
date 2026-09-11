#!/usr/bin/env python3
"""Fetch Sentinel-1 Global Coherence tiles for Alaska from AWS Open Data.

  python3 tools/fetch_coherence.py --season summer --var COH12 [--pol vv]

Source: the Sentinel-1 Global Coherence Dataset (Kellndorfer et al. 2022),
free and unauthenticated on AWS Open Data:
  s3://sentinel-1-global-coherence-earthbigdata/data/tiles/<TILE>/<TILE>_<season>_<pol>_<VAR>.tif

Facts worth knowing before touching these, all verified against live objects
on 2026-09-10:
  - The tile name is the UPPER-LEFT corner. N61W150 spans lat 60..61,
    lon -150..-149. Getting this backwards silently fetches the wrong row.
  - COH** is stored as PERCENT, uint8 0-100, nodata 0 — not 0-255 and not a
    float. tau/rho/AMP are uint16 with their own scalings.
  - They are NOT true COGs: no overviews, and blocked in 6-row strips. Range
    reads over /vsicurl are therefore slow; download and bake locally, which
    is what this script plus build_coherence_tiles.sh do.
  - Alaska (lat 54-72, lon -170..-130) is 666 tiles. COH12 is ~0.66 MB each,
    so ~450 MB a season; tau and AMP are ~3 MB each (~1.9 GB) — check before
    reaching for those.

Seasons are winter / spring / summer / fall. Variables: COH12, COH24, COH36,
COH48 (n-day coherence), AMP, rho, tau, rmse, inc, lsmap.

Resumable: an existing file of the right size is skipped, so re-running after
an interruption costs only the missing tiles.
"""
import argparse
import concurrent.futures as cf
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BUCKET = ('https://sentinel-1-global-coherence-earthbigdata'
          '.s3.us-west-2.amazonaws.com')
ROOT = pathlib.Path(__file__).resolve().parents[1]
# Same Alaska window as the susceptibility / ITS_LIVE / Hugonnet builds.
LAT_RANGE = (54, 72)
LON_RANGE = (-170, -130)


def list_tile_folders():
    out, token = [], None
    while True:
        u = (BUCKET + '/?list-type=2&delimiter=/&max-keys=1000&prefix='
             + urllib.parse.quote('data/tiles/'))
        if token:
            u += '&continuation-token=' + urllib.parse.quote(token)
        xml = urllib.request.urlopen(u, timeout=60).read().decode()
        out += [p.split('<')[0] for p in xml.split('<Prefix>')[1:]
                if p.split('<')[0] != 'data/tiles/']
        m = re.search(r'<NextContinuationToken>([^<]*)', xml)
        if m and '<IsTruncated>true' in xml:
            token = m.group(1)
        else:
            return out


def alaska_tiles():
    tiles = []
    for pref in list_tile_folders():
        name = pref.rstrip('/').split('/')[-1]
        m = re.match(r'^([NS])(\d+)([EW])(\d+)$', name)
        if not m:
            continue
        lat = int(m.group(2)) * (1 if m.group(1) == 'N' else -1)
        lon = int(m.group(4)) * (1 if m.group(3) == 'E' else -1)
        if LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]:
            tiles.append(name)
    return sorted(tiles)


def fetch(tile, season, pol, var, dest_dir):
    name = f'{tile}_{season}_{pol}_{var}.tif'
    url = f'{BUCKET}/data/tiles/{tile}/{name}'
    dest = dest_dir / name
    try:
        head = urllib.request.urlopen(
            urllib.request.Request(url, method='HEAD'), timeout=60)
        size = int(head.headers['Content-Length'])
    except urllib.error.HTTPError as e:
        return ('absent', tile, 0) if e.code == 404 else ('error', tile, 0)
    except Exception:
        return ('error', tile, 0)
    if dest.exists() and dest.stat().st_size == size:
        return ('skip', tile, size)
    tmp = dest.with_suffix('.part')
    try:
        with urllib.request.urlopen(url, timeout=300) as r, open(tmp, 'wb') as fh:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        return ('error', tile, 0)
    return ('got', tile, size)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--season', default='summer',
                    choices=('winter', 'spring', 'summer', 'fall'))
    ap.add_argument('--var', default='COH12')
    ap.add_argument('--pol', default='vv', choices=('vv', 'vh'))
    ap.add_argument('--jobs', type=int, default=12)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    dest_dir = ROOT / 'data' / 'coherence_src' / f'{args.season}_{args.pol}_{args.var}'
    tiles = alaska_tiles()
    print(f'{len(tiles)} Alaska tiles -> {dest_dir}')
    if args.dry_run:
        print('  --dry-run:', ', '.join(tiles[:8]), '...')
        return 0
    dest_dir.mkdir(parents=True, exist_ok=True)

    counts, total = {'got': 0, 'skip': 0, 'absent': 0, 'error': 0}, 0
    with cf.ThreadPoolExecutor(args.jobs) as pool:
        futs = [pool.submit(fetch, t, args.season, args.pol, args.var, dest_dir)
                for t in tiles]
        for i, f in enumerate(cf.as_completed(futs), 1):
            kind, tile, size = f.result()
            counts[kind] += 1
            total += size
            if i % 50 == 0 or i == len(tiles):
                print('  %4d/%d  got %d skip %d absent %d error %d  (%.0f MB)'
                      % (i, len(tiles), counts['got'], counts['skip'],
                         counts['absent'], counts['error'], total / 1e6), flush=True)
    print('done:', counts)
    return 1 if counts['error'] else 0


if __name__ == '__main__':
    sys.exit(main())
