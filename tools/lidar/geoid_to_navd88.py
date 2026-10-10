#!/usr/bin/env python3
"""geoid_to_navd88.py -- ellipsoid heights -> NAVD88 (GEOID12B), per pixel, before build_lidar.

    geoid_to_navd88.py SRC DST [--nodata V]

build_lidar.py only knows a constant `vertical_shift_m`. That is right for a tidal datum
(Lituya, Kachemak) but wrong for ellipsoid heights wherever the geoid slopes: across the
Taan Fiord / Icy Bay 2016 surveys GEOID12B runs from +9.5 to +12.3 m, so a constant would
tilt the surface by up to 3 m. This writes a new Float32 tiled GeoTIFF on the SOURCE grid
(same CRS, same pixels, no resampling) holding  H = h - N,  N = GEOID12B undulation
(PROJ grid us_noaa_g2012ba0.tif, bilinear), which build_lidar then treats as NAVD88.

N is evaluated on a coarse lattice (every STEP pixels) and interpolated linearly to every
pixel: the grid is 1 arc-minute and smooth, so the interpolation error is far below 1 mm.

Horizontal datum: the source is used as tagged. A "WGS 84" tag is taken as the NAD83(2011)
frame, the collection's convention (PROJ's WGS84 -> NAD83(2011) is a null shift; see the
header of datasets.json). If the heights really are ITRF ellipsoid heights, NAD83(2011)
ellipsoid heights differ from them by about a metre in Alaska and this output carries that.

Run with /opt/anaconda3/bin/python (rasterio + scipy), PROJ_LIB/PROJ_DATA unset.
"""
import argparse
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform
from rasterio.windows import Window
from scipy import ndimage

GEOID = Path('/opt/homebrew/share/proj/us_noaa_g2012ba0.tif')
STEP = 64


class Geoid:
    def __init__(self, path=GEOID):
        with rasterio.open(path) as g:
            self.a = g.read(1).astype('f8'); self.T = g.transform; self.lon360 = g.bounds.left > 0

    def __call__(self, lon, lat):
        lon = np.asarray(lon, 'f8'); lat = np.asarray(lat, 'f8')
        if self.lon360: lon = np.where(lon < 0, lon + 360, lon)
        col, row = ~self.T * (lon, lat)
        return ndimage.map_coordinates(self.a, [row - 0.5, col - 0.5], order=1, mode='nearest')


def convert(src, dst, nodata=None, geoid=None):
    geoid = geoid or Geoid()
    with rasterio.open(src) as S:
        nd = S.nodata if nodata is None else nodata
        prof = S.profile.copy()
        prof.update(driver='GTiff', dtype='float32', count=1, nodata=nd, tiled=True, blockxsize=512,
                    blockysize=512, compress='deflate', predictor=3, BIGTIFF='YES')
        prof.pop('photometric', None)
        with rasterio.open(dst, 'w', **prof) as D:
            D.update_tags(VERTICAL_DATUM='NAVD88 (GEOID12B), converted from ellipsoid heights by geoid_to_navd88.py',
                          SOURCE=str(src))
            H, W = S.height, S.width
            for r0 in range(0, H, 2048):
                h = min(2048, H - r0)
                for c0 in range(0, W, 2048):
                    w = min(2048, W - c0); win = Window(c0, r0, w, h)
                    a = S.read(1, window=win).astype('f4')
                    bad = ~np.isfinite(a) | (a == nd) | (a < -1000) | (a > 1e6)
                    if bad.all():
                        D.write(np.full_like(a, nd), 1, window=win); continue
                    # coarse lattice of pixel centres -> N, then linear to every pixel
                    rr = np.r_[np.arange(0, h, STEP), h - 1]; cc = np.r_[np.arange(0, w, STEP), w - 1]
                    RR, CC = np.meshgrid(rr + r0 + 0.5, cc + c0 + 0.5, indexing='ij')
                    xs, ys = S.transform * (CC.ravel(), RR.ravel())
                    lon, lat = transform(S.crs, 'EPSG:4326', xs, ys)
                    Nc = geoid(lon, lat).reshape(RR.shape)
                    ri = np.interp(np.arange(h), rr, np.arange(len(rr)))
                    ci = np.interp(np.arange(w), cc, np.arange(len(cc)))
                    RI, CI = np.meshgrid(ri, ci, indexing='ij')
                    N = ndimage.map_coordinates(Nc, [RI, CI], order=1, mode='nearest').astype('f4')
                    out = np.where(bad, np.float32(nd), a - N)
                    D.write(out, 1, window=win)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('src'); ap.add_argument('dst'); ap.add_argument('--nodata', type=float)
    a = ap.parse_args()
    Path(a.dst).parent.mkdir(parents=True, exist_ok=True)
    convert(a.src, a.dst, a.nodata)
    print('wrote', a.dst)
