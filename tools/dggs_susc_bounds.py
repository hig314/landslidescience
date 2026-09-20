#!/usr/bin/env python3
"""Web Mercator y bounds that actually contain a raster, printed as shell vars.

    eval "$(python3 tools/dggs_susc_bounds.py some.vrt)"   # sets YMIN / YMAX

A raster's reported WGS84 extent is derived from its four corners. Under a
conic projection like Alaska Albers the corners are NOT the extreme latitudes
-- the top edge bows north of both its endpoints. Trusting the corners cut the
DGGS susceptibility tiles off at 67.65 N when the data reached 71.53 N, losing
the Brooks Range and the North Slope with no error anywhere.

So sample all four edges and take the true min/max.
"""
import math
import sys

import rasterio
from rasterio.warp import transform as rt

R = 6378137.0


def mercator_y(lat):
    lat = max(min(lat, 85.0), -85.0)
    return R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def main(path, samples=128):
    r = rasterio.open(path)
    b = r.bounds
    pts = []
    for i in range(samples + 1):
        fx = b.left + i * (b.right - b.left) / samples
        fy = b.bottom + i * (b.top - b.bottom) / samples
        pts += [(fx, b.top), (fx, b.bottom), (b.left, fy), (b.right, fy)]
    _, lats = rt(r.crs, 'EPSG:4326', [p[0] for p in pts], [p[1] for p in pts])
    lo, hi = min(lats), max(lats)
    print('YMIN=%d' % math.floor(mercator_y(lo - 0.05)))
    print('YMAX=%d' % math.ceil(mercator_y(hi + 0.05)))
    print('# true latitude span %.2f .. %.2f N' % (lo, hi))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
