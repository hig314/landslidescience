#!/usr/bin/env python3
"""Grewingk Glacier's ice as of 19 September 2023 (Mark Laker's SfM, dixon_2023), on the grewingk recipe grid.

    /opt/anaconda3/bin/python3 tools/lidar/composites/grewingk/make_ice.py

Hig (2026-10-13): "use the ice as depicted in Mark Laker's 2023 dataset". The SfM surface has no
classes, and the ortho alone cannot trace the ice (the south half of the tongue is in a mountain's
shadow), so the 2023 ice is the 2020 glacier outline (NSIDC G10040, AK_2020_overall_glacier_covered_area)
MINUS the ground the lake took between 2020 and 2023 (PLUS ice the outline missed: where the 2021
lidar stands > DOWN_M above the 2023 SfM, connected to the ice -- 2026-10-14): cells inside the outline whose 2023 surface
stands within `WATER_TOL_M` of the 2023 lake level AND connect to the open lake.  The lake seed is
the water class of Grewingk 2021's point classification; the 2023 level is the median of the SfM
surface over that seed (SfM over water is noisy, so a median over a large area).  Small specks are
removed both ways (bergs < `BERG_M2` stay water, ice islands < `ICE_MIN_M2` dropped).

Writes, on the recipe grid (uint8, 1 = in): $OUT/ice_2023.tif, water_2023.tif (the 2023 lake), outline_2020.tif,
lake_2021.tif (the 2021 lake from the lidar DTM: flat at 48.55 m and connected to
the point-class lake), retreat_2023.tif (lake gained from the 2020 outline: the only
place the SfM's water is used), dixon_ice_2023.tif (the SfM surface on the grid, ONLY over the ice and
the retreat zone: the composite's dixon2023 layer), and ice_2023.json (level, areas, where the face is) -- class sources for the dixon2023 / grewingk2021 layers.
"""
import json
from pathlib import Path
import numpy as np
import rasterio
from rasterio import features
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from scipy import ndimage

HERE = Path(__file__).resolve().parent
R = json.loads((HERE / 'recipe.json').read_text())
OUT = Path('/Volumes/Nunatak/lidar_build/composite/grewingk/obs'); OUT.mkdir(parents=True, exist_ok=True)
OUTLINE = '/Volumes/Nunatak/permafrost_src/glaciers/AK_2020_overall_glacier_covered_area.shp'
DSM = '/Volumes/Nunatak/lidar_build/cog/dixon_2023.tif'
SEED = '/Volumes/Nunatak/lidar_build/obs/grewingk_2021_box/class.tif'        # topobathy codes, water = 2 (all tiles, 2026-10-14)
WATER_TOL_M = 8.0
BERG_M2 = 2000.0
ICE_MIN_M2 = 5000.0
G21 = '/Volumes/Nunatak/lidar_build/cog/grewingk_2021.tif'
LEVEL_2021 = 48.55
LAKE_TOL_M = 0.35
DOWN_M = 5.0          # lidar 2021 above SfM 2023 by more than this: the surface is ice (see main)
DOWN_NEAR_M = 400.0
HAND_LAKE = [(59.60023, -151.06399, 100.0, 'Hig 2026-10-14: 2021 ice standing as ground, lake by 2023')]
RETREAT_DOWN_M = 1.0  # in the 2023 water band: down by more than this since 2021 = ice that became lake
AGREE_MIN = 0.4       # local correlation of 2021-lidar and 2023-SfM DETAIL below this: the SfM does not see that surface
DETAIL_PX = 4         # detail = surface minus its gaussian (sigma 8 m); correlation over 9 px (18 m), smoothed 11 px
FLAT_RANGE_M = 0.02   # the vendor's hydroflattened water: 3x3 range below this


def on_grid(path, resampling):
    g = R['grid']; res = g['res_m']; x0, y0, x1, y1 = g['bounds']
    W, H = int(round((x1 - x0) / res)), int(round((y1 - y0) / res))
    T = rasterio.transform.from_origin(x0, y1, res, res)
    with rasterio.open(path) as src, WarpedVRT(src, crs=f"EPSG:{g['epsg']}", transform=T, width=W, height=H,
                                              resampling=resampling) as v:
        a = v.read(1, masked=True)
    return a.filled(np.nan).astype(np.float32) if a.dtype.kind == 'f' else a, T, (H, W)


def main():
    dsm, T, shape = on_grid(DSM, Resampling.average)
    seed, _, _ = on_grid(SEED, Resampling.nearest)
    seed = np.asarray(seed.filled(255) if hasattr(seed, 'filled') else seed) == 2
    import subprocess
    gj = json.loads(subprocess.run(['/opt/homebrew/bin/ogr2ogr', '-f', 'GeoJSON', '-t_srs', f"EPSG:{R['grid']['epsg']}",
                                    '-spat', *map(str, R['grid']['bounds']), '-spat_srs', f"EPSG:{R['grid']['epsg']}",
                                    '/vsistdout/', OUTLINE], capture_output=True, text=True, check=True).stdout)
    out20 = features.rasterize([f['geometry'] for f in gj['features']], out_shape=shape, transform=T, fill=0,
                               default_value=1, dtype='uint8').astype(bool)
    lake = seed & np.isfinite(dsm)
    level = float(np.nanmedian(dsm[lake]))
    low = np.isfinite(dsm) & (dsm < level + WATER_TOL_M)
    # the lake as of 2023: low cells connected to the 2021 lake (8-connectivity)
    lab, n = ndimage.label(low | seed, structure=np.ones((3, 3), bool))
    keep = np.unique(lab[seed & (lab > 0)])
    water23 = np.isin(lab, keep) & low
    ice = out20 & ~water23
    res = R['grid']['res_m']
    # bergs: small non-low blobs inside the 2023 lake are floating ice, not glacier -> not ice
    lab2, n2 = ndimage.label(ice)
    if n2:
        sz = ndimage.sum(ice, lab2, np.arange(1, n2 + 1)) * res * res
        ice &= ~np.isin(lab2, np.flatnonzero(sz < max(BERG_M2, ICE_MIN_M2)) + 1)
    ice = ndimage.binary_opening(ice, iterations=1)
    # ICE THE 2020 OUTLINE MISSED (Hig, 2026-10-14: the glacier's south edge read as rock, so the 2021 lidar
    # stood there and the face had no vertical drop): where the 2021 lidar stands > DOWN_M above the 2023 SfM
    # (median over 10 m) the surface came down by more than ground does in two years -- ice.  Measured on
    # this grid: ground within 800 m of the outline reads lidar - SfM -0.9 m median (p95 4.6), the outlined
    # ice +11.0 (p5 2.4).  Only cells connected to the ice, within DOWN_NEAR_M of it, not lake.
    g21_, _, _ = on_grid(G21, Resampling.average)
    dd = ndimage.median_filter(np.nan_to_num(g21_ - dsm, nan=-99.0), size=5)
    # Ice the outline missed that had become LAKE by 2023 (its 2023 surface at the water: water23) is
    # retreat, not ice -- the lake's south-east corner, 5-21 m down from 2021 (first cut excluded it, and
    # the 2021 ice stood there as rock).  The 2021 lake itself reads lidar - SfM < 0, never a candidate.
    cand = (dd > DOWN_M) & ~ice & ~seed
    labc, _ = ndimage.label(cand | ice)
    grown = np.isin(labc, np.unique(labc[ice])) & cand & (ndimage.distance_transform_edt(~ice) * R['grid']['res_m'] < DOWN_NEAR_M)
    grown = ndimage.binary_opening(grown, iterations=2)
    ice |= grown & ~water23
    ice_added_m2 = float((grown & ~water23).sum()) * R['grid']['res_m'] ** 2
    retreat_added_m2 = float((grown & water23 & ~out20).sum()) * R['grid']['res_m'] ** 2
    # SfM over water is poor (Hig, 2026-10-13: "better to use the lidar for that, and for lake shore"):
    # the SfM's water counts only where the lake took ground from the 2020 glacier outline -- the one
    # place the 2021 lidar cannot know it is lake now.  Elsewhere the lidar's own water class rules.
    retreat = (out20 | grown) & water23
    # ...and 2021 ice that is lake by 2023 but came down LESS than DOWN_M (Hig, 2026-10-14: 59.60023,-151.06399,
    # 2021 ice standing as ground in the lake): over water the SfM reads ~3.4 m high, so ice that became lake
    # shows only 1-5 m of lowering.  In the 2023 water band, not the 2021 lake, down > RETREAT_DOWN_M (rock on the
    # shore reads -0.9 m median) and connected to the retreat or the ice: retreat too.
    dd_r = ndimage.median_filter(np.nan_to_num(g21_ - dsm, nan=-99.0), size=5)
    g21_lake = np.isfinite(g21_) & (np.abs(g21_ - LEVEL_2021) < LAKE_TOL_M)
    c2 = water23 & ~g21_lake & ~retreat & ~ice & (dd_r > RETREAT_DOWN_M)
    lab2_, _ = ndimage.label(c2 | retreat | ice)
    more = ndimage.binary_opening(np.isin(lab2_, np.unique(lab2_[retreat | ice])) & c2, iterations=1)
    retreat |= more
    # ...and 2021 surface in the 2023 water band that the SfM does not REPRODUCE (Hig, 2026-10-14: the lowest part of
    # the patch, barely above the water, still stood as rock in v4 -- the SfM over water reads a few metres high, close
    # enough to low ice to pass a level test).  Agreement in LEVEL is not agreement: the SfM confirms a surface only
    # if it carries the same detail.  Measured on the SE corner: shore rock correlates 0.9 (p10 0.6), the 2021 ice
    # now under water 0.05 (p90 0.5), the open lake 0.  Only where the 2021 lidar has detail to judge (rms > 0.2 m),
    # connected to the retreat or the ice.
    def detail(a):
        a = np.where(np.isfinite(a), a, np.nanmedian(a)).astype(np.float64)
        return a - ndimage.gaussian_filter(a, DETAIL_PX)
    da, db = detail(g21_), detail(dsm)
    k = 2 * DETAIL_PX + 1
    saa, sbb = ndimage.uniform_filter(da * da, k), ndimage.uniform_filter(db * db, k)
    corr = ndimage.uniform_filter(ndimage.uniform_filter(da * db, k) / np.sqrt(saa * sbb + 1e-6), 11)
    c3 = water23 & ~g21_lake & ~retreat & ~ice & (corr < AGREE_MIN) & (np.sqrt(saa) > 0.2) & np.isfinite(dsm) & np.isfinite(g21_)
    lab3_, _ = ndimage.label(c3 | retreat | ice)
    more3 = ndimage.binary_opening(np.isin(lab3_, np.unique(lab3_[retreat | ice])) & c3, iterations=1)
    retreat |= more3; more |= more3
    retreat_unseen_m2 = float(more3.sum()) * R['grid']['res_m'] ** 2
    # the cells inside it that came down less than RETREAT_DOWN_M (noise in a 2021 ice surface) stayed ground and
    # pinned the bed 5-10 m above the water (v4): close the retreat and fill its holes, within the 2023 water band
    closed = ndimage.binary_fill_holes(ndimage.binary_closing(retreat, structure=np.ones((3, 3), bool), iterations=3))
    more2 = closed & water23 & ~g21_lake & ~ice & ~retreat
    retreat |= more2; more |= more2
    # HAND SEEDS (Hig marks a place that is lake by 2023): every 2023-water-band cell connected to the seed within
    # its radius is retreat -- where the data alone cannot tell 2021 ice from shore rock (both read alike in 2023)
    T_ = rasterio.transform.from_origin(R['grid']['bounds'][0], R['grid']['bounds'][3], R['grid']['res_m'], R['grid']['res_m'])
    from rasterio.warp import transform as _tr
    for lat, lon, rad, why in HAND_LAKE:
        (sx,), (sy,) = _tr('EPSG:4326', f"EPSG:{R['grid']['epsg']}", [lon], [lat])
        sc, sr = ~T_ * (sx, sy); sr, sc = int(sr), int(sc)
        yy, xx = np.ogrid[:shape[0], :shape[1]]
        near = (yy - sr) ** 2 + (xx - sc) ** 2 <= (rad / R['grid']['res_m']) ** 2
        cand_ = near & water23 & ~g21_lake & ~ice
        lab_h, _ = ndimage.label(cand_ | retreat)
        hand = (lab_h == lab_h[sr, sc]) & cand_ if lab_h[sr, sc] else np.zeros(shape, bool)
        retreat |= hand; more |= hand
    # cells the closed retreat encloses that the SfM puts ABOVE the water band are ice still standing in 2023
    # (a stranded block): ice, which the fill drops as evidence inside a lake -- not ground
    enc = ndimage.binary_fill_holes(retreat) & ~retreat & ~water23 & ~g21_lake & ~ice
    ice |= enc
    retreat_lowdrop_m2 = float(more.sum()) * R['grid']['res_m'] ** 2
    for name, arr in (('ice_2023', ice), ('water_2023', water23), ('outline_2020', out20), ('retreat_2023', retreat)):
        with rasterio.open(OUT / f'{name}.tif', 'w', driver='GTiff', height=shape[0], width=shape[1], count=1, dtype='uint8',
                           crs=f"EPSG:{R['grid']['epsg']}", transform=T, nodata=255, compress='deflate') as ds:
            ds.write(arr.astype(np.uint8), 1)
    # where the face is: the westmost ice column on each row that touches 2023 lake water
    face = ice & ndimage.binary_dilation(water23, structure=np.ones((3, 3), bool))
    fr, fc = np.nonzero(face)
    info = dict(level_2023_m=round(level, 2), level_from='median of the SfM surface over the Grewingk 2021 lake class',
                water_tol_m=WATER_TOL_M, outline_2020_km2=round(out20.sum() * res * res / 1e6, 3),
                ice_2023_km2=round(ice.sum() * res * res / 1e6, 3),
                lake_gained_from_2020_outline_km2=round((out20 & water23).sum() * res * res / 1e6, 3),
                ice_added_by_lowering_km2=round(ice_added_m2 / 1e6, 3), retreat_added_by_lowering_km2=round(retreat_added_m2 / 1e6, 3), retreat_added_lowdrop_km2=round(retreat_lowdrop_m2 / 1e6, 3), retreat_added_unseen_km2=round(retreat_unseen_m2 / 1e6, 3), agree_min=AGREE_MIN, down_m=DOWN_M,
                face_cells=int(face.sum()),
                face_x_range_m=[round(float(T.c + fc.min() * res)), round(float(T.c + fc.max() * res))] if fc.size else None,
                face_y_range_m=[round(float(T.f - fr.max() * res)), round(float(T.f - fr.min() * res))] if fr.size else None)
    (OUT / 'ice_2023.json').write_text(json.dumps(info, indent=1))
    # The 2021 lake FROM THE LIDAR (Hig: lidar for water and lake shore): the DGGS DTM holds the lake as a
    # flat surface at 48.55 m; where its point classes are void that surface read ground, and the lake
    # had a "shore" at water level all along the ice face, so the fill never met the ice (2026-10-13).
    # Flat cells within LAKE_TOL_M of the level, the components that touch the point-class lake.
    # HYDROFLATTENED, not merely near the level (Hig, 2026-10-14: outwash gravel at 48.2-48.9 m beside the outlet
    # joined the lake): the vendor's lake surface is constant to the centimetre, gravel is not.
    g21, _, _ = on_grid(G21, Resampling.average)
    g21f = np.nan_to_num(g21, nan=-1e4)
    rng = ndimage.maximum_filter(g21f, 3) - ndimage.minimum_filter(g21f, 3)
    flat = np.isfinite(g21) & (np.abs(g21 - LEVEL_2021) < LAKE_TOL_M) & (rng < FLAT_RANGE_M)
    lab3, _ = ndimage.label(flat | seed, structure=np.ones((3, 3), bool))
    lake21 = np.isin(lab3, np.unique(lab3[seed & (lab3 > 0)])) & flat
    lake21 = ndimage.binary_closing(lake21, iterations=2) & np.isfinite(g21) & (g21 < LEVEL_2021 + 1.0)
    with rasterio.open(OUT / 'lake_2021.tif', 'w', driver='GTiff', height=shape[0], width=shape[1], count=1, dtype='uint8',
                       crs=f"EPSG:{R['grid']['epsg']}", transform=T, nodata=255, compress='deflate') as ds:
        ds.write(lake21.astype(np.uint8), 1)
    info['lake_2021_km2'] = round(lake21.sum() * res * res / 1e6, 3)
    (OUT / 'ice_2023.json').write_text(json.dumps(info, indent=1))
    # The SfM as a composite layer: ONLY over the 2023 ice and the retreat zone (nodata elsewhere).  Given
    # everywhere with a 'canopy' default it was the newest class over the lake and the shores, so the lake
    # stopped being water and got no basin (2026-10-13, first fill).
    keep = ice | retreat
    sfm = np.where(keep & np.isfinite(dsm), dsm, np.nan).astype(np.float32)
    with rasterio.open(OUT / 'dixon_ice_2023.tif', 'w', driver='GTiff', height=shape[0], width=shape[1], count=1,
                       dtype='float32', crs=f"EPSG:{R['grid']['epsg']}", transform=T, nodata=np.nan, compress='deflate',
                       predictor=3, tiled=True) as ds:
        ds.write(sfm, 1)
    print(json.dumps(info))


if __name__ == '__main__':
    main()
