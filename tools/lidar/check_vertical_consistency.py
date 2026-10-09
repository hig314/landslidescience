#!/opt/anaconda3/bin/python3
"""Do overlapping surveys agree on height? Pairwise vertical offsets.

For every pair of catalogued surveys whose bounds overlap (or the pairs
named on the command line), sample random points in the overlap, read both
archive COGs on Nunatak, and report the median difference B - A with its
spread (MAD), on GENTLE ground only: where the slope is steep a horizontal
misregistration of a metre reads as a vertical offset of a metre, so the
datum question is asked where that cannot happen. Slope is taken from a
3x3 window of the first survey.

A constant offset of ~8-12 m in Alaska is ellipsoidal vs NAVD88 heights;
~1-2 m is a geoid-model difference (GEOID12B vs xGEOID17B, see the
Seldovia note in RESUME.md) or a feet/metres slip of the LAST metre; tens
of centimetres is a geoid-model difference or genuine change. A spread
much larger than the offset means the ground differs between the two
dates (glaciers, slides, construction), not the datum.

    /opt/anaconda3/bin/python3 tools/lidar/check_vertical_consistency.py [--n 300] [--max-slope 5] [A B ...]

Read-only. Pairs named as `A B` on the command line are compared whether or
not their bounds overlap in the catalogue.
"""
import argparse, itertools, json, math, random, sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform

ROOT = Path(__file__).resolve().parents[2]
COG_DIR = Path('/Volumes/Nunatak/lidar_build/cog')
CATALOG = ROOT / 'data' / 'lidar' / 'catalog.geojson'


def overlap(a, b):
    return [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]


def read_px(src, lon, lat, win=1):
    """Value (and a (2w+1)^2 window) at lon/lat; None off the raster or at nodata."""
    xs, ys = transform('EPSG:4326', src.crs, [lon], [lat])
    try:
        r, c = src.index(xs[0], ys[0])
    except Exception:
        return None
    if r - win < 0 or c - win < 0 or r + win >= src.height or c + win >= src.width:
        return None
    w = src.read(1, window=((r - win, r + win + 1), (c - win, c + win + 1))).astype(float)
    if src.nodata is not None:
        w[w == src.nodata] = np.nan
    w[np.abs(w) > 1e5] = np.nan
    if np.isnan(w).any():
        return None
    return w


def slope_deg(w, res):
    dzdx = (w[1, 2] - w[1, 0]) / (2 * res)
    dzdy = (w[2, 1] - w[0, 1]) / (2 * res)
    return math.degrees(math.atan(math.hypot(dzdx, dzdy)))


def compare(fa, fb, n, max_slope):
    pa, pb = fa['properties'], fb['properties']
    ca, cb = COG_DIR / f"{pa['id']}.tif", COG_DIR / f"{pb['id']}.tif"
    if not ca.exists() or not cb.exists():
        return None
    box = overlap(pa['bounds'], pb['bounds'])
    if box[0] >= box[2] or box[1] >= box[3]:
        return None
    diffs = []
    with rasterio.open(ca) as A, rasterio.open(cb) as B:
        res_a = abs(A.transform.a)
        tries = 0
        while len(diffs) < n and tries < n * 40:
            tries += 1
            lon = random.uniform(box[0], box[2]); lat = random.uniform(box[1], box[3])
            wa = read_px(A, lon, lat)
            if wa is None:
                continue
            if slope_deg(wa, res_a) > max_slope:
                continue
            wb = read_px(B, lon, lat, win=0)
            if wb is None:
                continue
            diffs.append(float(wb[0, 0]) - float(wa[1, 1]))
    if len(diffs) < 10:
        return {'n': len(diffs)}
    d = np.array(diffs)
    med = float(np.median(d))
    mad = float(np.median(np.abs(d - med)))
    return {'n': len(d), 'median': med, 'mad': mad,
            'p10': float(np.percentile(d, 10)), 'p90': float(np.percentile(d, 90))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pairs', nargs='*', help='A B [C D ...]: explicit pairs')
    ap.add_argument('--n', type=int, default=300)
    ap.add_argument('--max-slope', type=float, default=5.0)
    args = ap.parse_args()
    random.seed(7)
    cat = json.loads(CATALOG.read_text())
    feats = {f['properties']['id']: f for f in cat['features'] if f['properties'].get('bounds')}
    if args.pairs:
        if len(args.pairs) % 2:
            sys.exit('pairs come in twos')
        pairs = [(feats[a], feats[b]) for a, b in zip(args.pairs[::2], args.pairs[1::2])]
    else:
        pairs = []
        for fa, fb in itertools.combinations(feats.values(), 2):
            box = overlap(fa['properties']['bounds'], fb['properties']['bounds'])
            if box[0] < box[2] and box[1] < box[3]:
                pairs.append((fa, fb))
    print(f"{'A':24s} {'B':24s} {'n':>4s} {'median B-A':>11s} {'MAD':>7s} {'p10':>8s} {'p90':>8s}")
    for fa, fb in pairs:
        r = compare(fa, fb, args.n, args.max_slope)
        a, b = fa['properties']['id'], fb['properties']['id']
        if r is None:
            continue
        if r['n'] < 10:
            print(f"{a:24s} {b:24s} {r['n']:4d}   (too few gentle, shared points)")
            continue
        flag = '  <-- offset' if abs(r['median']) > 0.5 else ''
        print(f"{a:24s} {b:24s} {r['n']:4d} {r['median']:+11.2f} {r['mad']:7.2f} {r['p10']:+8.2f} {r['p90']:+8.2f}{flag}")


if __name__ == '__main__':
    main()
