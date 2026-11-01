#!/opt/anaconda3/bin/python3
"""List the Mapterhorn tiles at zoom Z that touch a coverage polygon.

Writes a curl -K config (url + output per tile) for the tiles not yet on
disk under OUT/<z>/<x>/<y>.webp. The coverage is an EPSG:3338 GeoJSON
(default: data/regions_seed/alaska_3338.geojson, the `alaska` region),
buffered by --buffer-m so the slope kernels have ground past the edge.
Tiles are tested in EPSG:3338 (each tile's outline sampled along its edges),
which sidesteps the antimeridian: the western Aleutians come out as tiles
near x = 2^z - 1 with no special case.

    mapterhorn_tiles.py --z 12 --out /Volumes/Nunatak/terrain_src/mapterhorn > curl.cfg

Coverage grows by pointing --coverage at a bigger polygon and re-running:
only the new tiles are listed.
"""
import argparse, json, math, os, sys
from pathlib import Path

import numpy as np
from rasterio.warp import transform
from shapely.geometry import shape, Polygon
from shapely.prepared import prep

ROOT = Path(__file__).resolve().parents[2]


def lonlat(x, y, n):
    lon = x / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--z', type=int, default=12)
    ap.add_argument('--out', default='/Volumes/Nunatak/terrain_src/mapterhorn')
    ap.add_argument('--coverage', default=str(ROOT / 'data/regions_seed/alaska_3338.geojson'))
    ap.add_argument('--buffer-m', type=float, default=3000)
    a = ap.parse_args()
    z, n = a.z, 2 ** a.z
    cov = shape(json.load(open(a.coverage))).buffer(a.buffer_m)
    pc = prep(cov)
    # Candidate box: 51-72 N, 172 E eastward to 129 W.
    def tx(lon): return int((lon + 180) / 360 * n)
    def ty(lat):
        la = math.radians(lat)
        return int((1 - math.log(math.tan(la) + 1 / math.cos(la)) / math.pi) / 2 * n)
    xs = list(range(tx(172), n)) + list(range(0, tx(-129) + 1))
    ys = range(ty(72), ty(51) + 1)
    k = kept = 0
    for x in xs:
        # one column at a time: corners + edge midpoints of every tile in it
        pts_lon, pts_lat = [], []
        for y in ys:
            for fx, fy in ((0, 0), (.5, 0), (1, 0), (1, .5), (1, 1), (.5, 1), (0, 1), (0, .5)):
                lo, la = lonlat(x + fx, y + fy, n)
                pts_lon.append(lo); pts_lat.append(la)
        X, Y = transform('EPSG:4326', 'EPSG:3338', pts_lon, pts_lat)
        X = np.array(X).reshape(-1, 8); Y = np.array(Y).reshape(-1, 8)
        for i, y in enumerate(ys):
            k += 1
            if not pc.intersects(Polygon(zip(X[i], Y[i]))):
                continue
            kept += 1
            rel = f'{z}/{x}/{y}.webp'
            if os.path.exists(os.path.join(a.out, rel)) or os.path.exists(os.path.join(a.out, rel + '.404')):
                continue
            print(f'url = "https://tiles.mapterhorn.com/{rel}"\noutput = "{rel}"')
    print(f'z{z}: {kept} of {k} candidate tiles touch the coverage', file=sys.stderr)


if __name__ == '__main__':
    main()
