#!/usr/bin/env python3
"""geoid_to_navd88.py -- ellipsoid heights -> NAVD88 (GEOID12B), per pixel, before build_lidar.

    geoid_to_navd88.py SRC DST [--nodata V] [--void-water WIN,FRAC] [--drop-islands HA]

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

--void-water WIN,FRAC (off by default) is for a photogrammetric surface whose water was
never masked: image matching fails on water and leaves noise thousands of metres deep
(Grand Plateau 2026: lake, sea and flight edges read down to -8,890 m). A cell is voided
when its NAVD88 height is below 0 m, or when at least FRAC of the valid cells in the
WIN x WIN pixel box around it are -- the second test removes the positive half of the same
noise, which no height threshold can tell from land. Land loses nothing but the few metres
of shore inside such a box. The count and area voided are printed and written to the tags.

--drop-islands HA then voids every patch of surviving cells smaller than HA hectares that
is detached from the rest (8-connected on an 8 px grid, so a gap under ~8 px does not
detach). Where the water noise happens to be positive over a few tens of metres it passes
the test above and is left as a patch floating on the void. Measured on Grand Plateau
against the hydro-flattened 2019 lidar: 65-80 % of such patches under 20 ha sat on 2019
water. A real islet under HA goes with them; a larger one (the 61 ha moraine there) stays.

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


def convert(src, dst, nodata=None, geoid=None, void_water=None):
    geoid = geoid or Geoid()
    halo = (void_water[0] // 2) if void_water else 0
    n_water = 0
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
                    # read with a halo so the --void-water box sees across block edges
                    ra, ca = max(0, r0 - halo), max(0, c0 - halo)
                    rb, cb = min(H, r0 + h + halo), min(W, c0 + w + halo)
                    hh, ww = rb - ra, cb - ca
                    a = S.read(1, window=Window(ca, ra, ww, hh)).astype('f4')
                    bad = ~np.isfinite(a) | (a == nd) | (a < -1000) | (a > 1e6)
                    inner = (slice(r0 - ra, r0 - ra + h), slice(c0 - ca, c0 - ca + w))
                    if bad[inner].all():
                        # nothing to keep; the cells below -1000 m still count as voided water
                        if void_water:
                            n_water += int((np.isfinite(a) & (a != nd))[inner].sum())
                        D.write(np.full((h, w), nd, 'f4'), 1, window=win); continue
                    # coarse lattice of pixel centres -> N, then linear to every pixel
                    rr = np.r_[np.arange(0, hh, STEP), hh - 1]; cc = np.r_[np.arange(0, ww, STEP), ww - 1]
                    RR, CC = np.meshgrid(rr + ra + 0.5, cc + ca + 0.5, indexing='ij')
                    xs, ys = S.transform * (CC.ravel(), RR.ravel())
                    lon, lat = transform(S.crs, 'EPSG:4326', xs, ys)
                    Nc = geoid(lon, lat).reshape(RR.shape)
                    ri = np.interp(np.arange(hh), rr, np.arange(len(rr)))
                    ci = np.interp(np.arange(ww), cc, np.arange(len(cc)))
                    RI, CI = np.meshgrid(ri, ci, indexing='ij')
                    N = ndimage.map_coordinates(Nc, [RI, CI], order=1, mode='nearest').astype('f4')
                    out = a - N
                    if void_water:
                        # "valid" here is every cell the file holds, the < -1000 ones included:
                        # they are the same water noise and must count toward the box
                        held = np.isfinite(a) & (a != nd)
                        neg = held & (out < 0)
                        k = void_water[0]
                        fn = ndimage.uniform_filter(neg.astype('f4'), k, mode='constant')
                        fv = ndimage.uniform_filter(held.astype('f4'), k, mode='constant')
                        water = held & (neg | (fn >= void_water[1] * np.maximum(fv, 1e-6)))
                        n_water += int(water[inner].sum())
                        bad = bad | water
                    out = np.where(bad, np.float32(nd), out)
                    D.write(out[inner], 1, window=win)
            if void_water:
                km2 = n_water * abs(S.transform.a * S.transform.e) / 1e6
                note = (f'{n_water} cells ({km2:.1f} km2) voided as water noise: NAVD88 < 0 m, or >= '
                        f'{void_water[1]:g} of the valid cells in the {void_water[0]} px box are')
                D.update_tags(VOID_WATER=note); print(note)


def drop_islands(dst, ha, B=8):
    with rasterio.open(dst, 'r+') as D:
        H, W, nd = D.height, D.width, D.nodata
        h, w = -(-H // B), -(-W // B)
        cnt = np.zeros((h, w), 'u1')                       # valid cells per B x B block (<= 64)
        for r0 in range(0, H, 1024):                       # 1024 % B == 0
            n = min(1024, H - r0)
            v = D.read(1, window=Window(0, r0, W, n)) != nd
            v = np.pad(v, ((0, (-n) % B), (0, (-W) % B)))
            cnt[r0 // B: r0 // B + v.shape[0] // B] = v.reshape(v.shape[0] // B, B, v.shape[1] // B, B).sum((1, 3))
        lab, nlab = ndimage.label(cnt > 0, structure=np.ones((3, 3)))
        cell_ha = abs(D.transform.a * D.transform.e) / 1e4
        area = ndimage.sum(cnt, lab, np.arange(1, nlab + 1)) * cell_ha
        small = np.r_[False, area < ha]
        kill = small[lab]
        n_cells = int(cnt[kill].sum())
        for _, win in D.block_windows(1):
            r0, c0 = int(win.row_off), int(win.col_off)
            k = kill[r0 // B: -(-(r0 + win.height) // B), c0 // B: -(-(c0 + win.width) // B)]
            if not k.any(): continue
            k = np.repeat(np.repeat(k, B, 0), B, 1)[(r0 % B):(r0 % B) + int(win.height), (c0 % B):(c0 % B) + int(win.width)]
            a = D.read(1, window=win); a[k] = nd; D.write(a, 1, window=win)
        note = (f'{int(small.sum())} detached patches under {ha:g} ha voided, {n_cells} cells '
                f'({n_cells * cell_ha / 100:.2f} km2); {nlab - int(small.sum())} patches kept, '
                f'the largest {area.max() / 100:.1f} km2')
        D.update_tags(DROP_ISLANDS=note); print(note)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('src'); ap.add_argument('dst'); ap.add_argument('--nodata', type=float)
    ap.add_argument('--void-water', metavar='WIN,FRAC',
                    help='void unmasked-water noise: box size in pixels (odd) and the share of '
                         'below-zero cells in it that condemns the rest, e.g. 65,0.25')
    ap.add_argument('--drop-islands', metavar='HA', type=float,
                    help='then void detached patches of surviving cells smaller than HA hectares')
    a = ap.parse_args()
    vw = None
    if a.void_water:
        k, f = a.void_water.split(','); vw = (int(k), float(f))
    Path(a.dst).parent.mkdir(parents=True, exist_ok=True)
    convert(a.src, a.dst, a.nodata, void_water=vw)
    if a.drop_islands:
        drop_islands(a.dst, a.drop_islands)
    print('wrote', a.dst)
