#!/opt/anaconda3/bin/python3
"""Do the published web pyramids agree with their archives?

For every survey in the catalogue that has both a local PMTiles pyramid and
an archive COG on Nunatak, read N random points inside the survey's bounds
from BOTH -- the terrain-RGB tile at the survey's max zoom, decoded here,
and the COG through rasterio -- and report the median offset and the median
ratio of (tile / archive). A pyramid built from its archive agrees to the
0.1 m terrain-RGB step plus resampling; a stale pyramid does not.

Why this exists (2026-10-08): the profile tool showed anchorage_2015 at 0.31x
the Portage surveys. Its archive is right; its PYRAMID is the first build's
(heights x0.3048 too small), kept by `gdal raster tile --resume` when the
archive was rebuilt on 2026-09-12 -- the stale-tile trap that
build_lidar.drop_stale_tiles was written for on 2026-09-14, two days later.
Nothing upstream of the tiles can reveal that, so this reads the tiles.

    /opt/anaconda3/bin/python3 tools/lidar/check_web_heights.py [--n 40] [id ...]

Read-only. Exit 1 if any survey's median |offset| > 1 m or ratio off by > 2 %.
"""
import argparse, io, json, math, random, sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from pmtiles.reader import Reader, MmapSource

ROOT = Path(__file__).resolve().parents[2]
PM_DIR = ROOT / 'data' / 'lidar' / 'pmtiles'
COG_DIR = Path('/Volumes/Nunatak/lidar_build/cog')
CATALOG = ROOT / 'data' / 'lidar' / 'catalog.geojson'


def tile_xy(lon, lat, z):
    n = 2 ** z
    la = math.radians(lat)
    return (lon + 180) / 360 * n, (1 - math.log(math.tan(la) + 1 / math.cos(la)) / math.pi) / 2 * n


def decode(png):
    im = Image.open(io.BytesIO(png)).convert('RGBA')
    a = np.asarray(im).astype(np.float64)
    h = -10000 + (a[..., 0] * 65536 + a[..., 1] * 256 + a[..., 2]) * 0.1
    h[a[..., 3] < 255] = np.nan
    return h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ids', nargs='*')
    ap.add_argument('--n', type=int, default=40)
    args = ap.parse_args()
    cat = json.loads(CATALOG.read_text())
    random.seed(1)
    bad = 0
    for f in cat['features']:
        p = f['properties']
        if args.ids and p['id'] not in args.ids:
            continue
        pm = PM_DIR / f"{p['id']}.pmtiles"
        cog = COG_DIR / f"{p['id']}.tif"
        if not pm.exists() or not cog.exists():
            print(f"{p['id']:28s} skip ({'no pmtiles' if not pm.exists() else 'no cog'})")
            continue
        z = p['max_zoom']
        b = p['bounds']
        tiles = {}
        diffs, ratios = [], []
        with open(pm, 'rb') as fh, rasterio.open(cog) as src:
            rd = Reader(MmapSource(fh))
            tries = 0
            while len(diffs) < args.n and tries < args.n * 20:
                tries += 1
                lon = random.uniform(b[0], b[2]); lat = random.uniform(b[1], b[3])
                tx, ty = tile_xy(lon, lat, z)
                key = (int(tx), int(ty))
                if key not in tiles:
                    data = rd.get(z, key[0], key[1])
                    tiles[key] = decode(data) if data else None
                t = tiles[key]
                if t is None:
                    continue
                px = int((tx - key[0]) * t.shape[1]); py = int((ty - key[1]) * t.shape[0])
                v = t[py, px]
                if np.isnan(v):
                    continue
                # archive value at the same lon/lat (nearest cell)
                from rasterio.warp import transform
                xs, ys = transform('EPSG:4326', src.crs, [lon], [lat])
                try:
                    r, c = src.index(xs[0], ys[0])
                    w = src.read(1, window=((r, r + 1), (c, c + 1)))
                except Exception:
                    continue
                a = float(w[0, 0]) if w.size else float('nan')
                if src.nodata is not None and a == src.nodata or np.isnan(a) or abs(a) > 1e5:
                    continue
                diffs.append(v - a)
                if abs(a) > 5:
                    ratios.append(v / a)
        if not diffs:
            print(f"{p['id']:28s} no comparable points")
            continue
        md = float(np.median(diffs)); mr = float(np.median(ratios)) if ratios else float('nan')
        flag = abs(md) > 1.0 or (ratios and abs(mr - 1) > 0.02)
        bad += bool(flag)
        print(f"{p['id']:28s} n={len(diffs):3d}  median tile-archive {md:+8.2f} m   median ratio {mr:6.3f}"
              + ("   <-- DISAGREES" if flag else ""))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
