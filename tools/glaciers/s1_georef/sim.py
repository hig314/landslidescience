"""3DEP -> what Sentinel-1 should see, ascending and descending.

Grid: EPSG:3857 (Earth Engine's usual export projection), north up, chosen so
a pixel is `res` metres on the ground at the box's centre latitude.
Brightness model (GEE S1_GRD is orthorectified but NOT radiometrically
flattened, so slopes facing the radar are bright and slopes away are dark):
  I = max(cos(local incidence), 0), 0 in cast shadow.
Shadow: along the look direction, a point is in shadow when the ray at the
radar's elevation angle from nearer terrain passes above it.
Geometry: S1 is right-looking. Heading ~347 deg ascending, ~193 deg
descending at 55-62 N, so the look azimuth is ~77 (asc) / ~283 (desc) deg.
Incidence 30-46 deg across the swath; one value is used (default 38).

usage: python sim.py W S E N OUT.npz [--res 20] [--inc 38]
"""
import argparse, math
import numpy as np
import cv2
import rasterio
from rasterio.warp import reproject, Resampling, transform_bounds
from rasterio.transform import from_origin

DEM = '/Volumes/Nunatak/lidar_build/ctx_3dep/all.vrt'
# Our ctx_3dep bake only has tiles near the lidar surveys (Baird's box was 75%
# empty). Mapterhorn serves the same 3DEP 1/3" statewide (memory note
# reference_mapterhorn): terrarium WebP, 512 px tiles. z11 = ~19 m on the
# ground at 60 N, enough for a 20 m grid. Tiles are cached on Nunatak.
MT_URL = 'https://tiles.mapterhorn.com/{z}/{x}/{y}.webp'
MT_CACHE = '/Volumes/Nunatak/glacier_s1_georef/mapterhorn'
LOOK = {'asc': 77.0, 'desc': 283.0}


def merc(lon, lat):
    R = 6378137.0
    return R * math.radians(lon), R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def mapterhorn(x0, y0, x1, y1, z=11):
    """Mosaic of terrarium tiles covering mercator box -> (array, transform)."""
    import io, os, urllib.request
    from PIL import Image
    R = 6378137.0; span = 2 * math.pi * R; ts = span / 2 ** z
    tx0, tx1 = int((x0 + span / 2) // ts), int((x1 + span / 2) // ts)
    ty0, ty1 = int((span / 2 - y1) // ts), int((span / 2 - y0) // ts)
    N = 512
    mos = np.full(((ty1 - ty0 + 1) * N, (tx1 - tx0 + 1) * N), np.nan, np.float32)
    os.makedirs(MT_CACHE, exist_ok=True)
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            f = os.path.join(MT_CACHE, '%d_%d_%d.webp' % (z, tx, ty))
            if not os.path.exists(f):
                req = urllib.request.Request(MT_URL.format(z=z, x=tx, y=ty), headers={'User-Agent': 'terrain-research'})
                try:
                    data = urllib.request.urlopen(req, timeout=60).read()
                except Exception:
                    continue
                open(f, 'wb').write(data)
            im = np.asarray(Image.open(f).convert('RGB')).astype(np.float32)
            el = im[..., 0] * 256 + im[..., 1] + im[..., 2] / 256 - 32768
            mos[(ty - ty0) * N:(ty - ty0 + 1) * N, (tx - tx0) * N:(tx - tx0 + 1) * N] = el
    tr = from_origin(tx0 * ts - span / 2, span / 2 - ty0 * ts, ts / N, ts / N)
    return mos, tr


def load_dem(w, s, e, n, res, source='3dep'):
    lat0 = (s + n) / 2
    k = 1 / math.cos(math.radians(lat0))           # mercator metres per ground metre
    x0, y1 = merc(w, n); x1, y0 = merc(e, s)
    px = res * k
    W = int((x1 - x0) / px); H = int((y1 - y0) / px)
    if W * H > 40e6:
        raise SystemExit('grid %dx%d too big' % (W, H))
    tr = from_origin(x0, y1, px, px)
    out = np.full((H, W), np.nan, np.float32)
    if source == 'mapterhorn':
        mos, mtr = mapterhorn(x0, y0, x1, y1)
        reproject(mos, out, src_transform=mtr, src_crs='EPSG:3857', src_nodata=np.nan, dst_transform=tr,
                  dst_crs='EPSG:3857', resampling=Resampling.bilinear, dst_nodata=np.nan)
        out[out < -50] = np.nan
        return out, tr, k
    with rasterio.open(DEM) as src:
        reproject(rasterio.band(src, 1), out, dst_transform=tr, dst_crs='EPSG:3857',
                  resampling=Resampling.average, dst_nodata=np.nan)
    return out, tr, k


def shadow(z, res, look_az, inc):
    """z on a north-up grid; rotate so the radar is to the LEFT (look = +x)."""
    H, W = z.shape
    ang = 90.0 - look_az                         # rotate look direction onto +x
    c = (W / 2, H / 2)
    M = cv2.getRotationMatrix2D(c, -ang, 1.0)
    cos, sin = abs(M[0, 0]), abs(M[0, 1])
    nW, nH = int(H * sin + W * cos) + 2, int(H * cos + W * sin) + 2
    M[0, 2] += nW / 2 - c[0]; M[1, 2] += nH / 2 - c[1]
    zr = cv2.warpAffine(np.nan_to_num(z, nan=-1e4), M, (nW, nH), flags=cv2.INTER_LINEAR, borderValue=-1e4)
    t = math.tan(math.radians(90 - inc)) * res    # drop of the ray per pixel
    x = np.arange(nW, dtype=np.float32) * t
    hor = np.maximum.accumulate(zr + x[None, :], axis=1) - x[None, :]
    sh = (zr < hor - 1.0).astype(np.uint8)
    Mi = cv2.invertAffineTransform(M)
    return cv2.warpAffine(sh, Mi, (W, H), flags=cv2.INTER_NEAREST) > 0


def simulate(z, res, inc=38.0):
    zz = np.nan_to_num(z, nan=0.0)
    gy, gx = np.gradient(zz, res)                # rows go south: gy is -dz/dN
    dzdx, dzdn = gx, -gy
    nrm = np.sqrt(dzdx ** 2 + dzdn ** 2 + 1)
    out = {}
    for k, az in LOOK.items():
        a, i = math.radians(az), math.radians(inc)
        se, sn, su = -math.sin(i) * math.sin(a), -math.sin(i) * math.cos(a), math.cos(i)
        cosl = (-dzdx * se - dzdn * sn + su) / nrm
        I = np.clip(cosl, 0, 1)
        sh = shadow(z, res, az, inc)
        I[sh] = 0
        out[k] = I.astype(np.float32); out[k + '_shadow'] = sh
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    for c in 'wsen':
        ap.add_argument(c, type=float)
    ap.add_argument('out'); ap.add_argument('--res', type=float, default=20); ap.add_argument('--inc', type=float, default=38)
    ap.add_argument('--dem', default='mapterhorn', choices=['mapterhorn', '3dep'])
    a = ap.parse_args()
    z, tr, k = load_dem(a.w, a.s, a.e, a.n, a.res, a.dem)
    sim = simulate(z, a.res, a.inc)
    np.savez_compressed(a.out, dem=z, transform=np.array(tr)[:6], res=a.res, k=k, **sim)
    for key in ('asc', 'desc'):
        cv2.imwrite(a.out.replace('.npz', '_%s.png' % key), (sim[key] * 255).astype(np.uint8))
    print('grid', z.shape, 'nan', int(np.isnan(z).sum()), 'shadow asc %.1f%% desc %.1f%%' % (
        100 * sim['asc_shadow'].mean(), 100 * sim['desc_shadow'].mean()))
