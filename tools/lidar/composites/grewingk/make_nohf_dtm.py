#!/usr/bin/env python3
"""Grewingk 2021 lidar WITHOUT hydroflattening, over the grewingk recipe box -- and the islands it hid.

    PATH=/opt/homebrew/bin:$PATH /opt/anaconda3/bin/python3 tools/lidar/composites/grewingk/make_nohf_dtm.py [--partial]

Hig (2026-10-14): the delivered DGGS DTM (RDF 2023-2) flattens the lake to one level, and with it a real
island at 59.60571,-151.11924 (visible in the 2023 SfM too) that constrains the bed.  First pass, no
reclassification: the DTM rebuilt from the classified points (ground 2 + water 9, mean per cell) on the
delivered DTM's own 1 m lattice; cells with no such return keep the delivered DTM (vegetation gaps, the
vendor's interpolation).  Water-classed returns standing clear of the lake (> ISLAND_DZ_M above the flat
lake surface, in components >= ISLAND_MIN_M2) are the islands hydroflattening hid: islands_2021.tif on
the recipe grid, a class step (-> ground) in the grewingk2021 layer, after lake_2021 -> water.

Points: the 2021 classified LAZ, range-fetched from the DGGS zip into
lidar_build/kbay_reclass_test/grewingk_2021/laz/ (fetch_one.sh; las_index/ for which tiles).  Writes to
composite/grewingk/obs/nohf/: tiles/<name>.tif (mean z, water fraction), dtm_1m.tif (COG), islands_2021.tif,
islands_2021.json.
"""
import csv, json, os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import rasterio
from rasterio.enums import Resampling
from scipy import ndimage

HERE = Path(__file__).resolve().parent
R = json.loads((HERE / 'recipe.json').read_text())
PTS = Path('/Volumes/Nunatak/lidar_build/kbay_reclass_test/grewingk_2021')
INDEX = PTS / 'las_index/data/lidar_las_index/rdf2023-2-grewingk-lidar-2021-las-index.shp'
VENDOR = '/Volumes/Nunatak/lidar_build/cog/grewingk_2021.tif'
OUT = Path('/Volumes/Nunatak/lidar_build/composite/grewingk/obs/nohf'); (OUT / 'tiles').mkdir(parents=True, exist_ok=True)
BOUNDS = R['grid']['bounds']
# --full (2026-10-18, Hig: "good Grewingk lidar isn't being used" north of the recipe box, at 59.64797,-151.15728):
# the whole 2021 survey (the delivered DTM's extent) into obs/nohf_full/, the per-tile grids shared with nohf/tiles
if '--full' in sys.argv:
    BOUNDS = (600000, 6597000, 617000, 6618400)
    _shared = OUT / 'tiles'
    OUT = OUT.with_name('nohf_full'); OUT.mkdir(parents=True, exist_ok=True)
    if not (OUT / 'tiles').exists():
        (OUT / 'tiles').symlink_to(_shared)
GDAL = '/opt/homebrew/bin/'
LEVEL = 48.55            # the delivered DTM's flat lake surface
ISLAND_DZ_M = 0.65       # water returns over open water read 48.7-48.9 (p99 ~49.0); an island stands above that
ISLAND_MIN_M2 = 50.0     # the fill's lake_islet_m2: smaller is a stray return (or a berg)
# Only CONFIRMED islands become ground: the first interim build (2026-10-14) found 13 patches, most of them in
# water Ed Berg's sounder read 8-74 m deep -- icebergs of October 2021, which as ground pin the bed to the
# surface.  The 2023 SfM cannot tell them apart (each reads like its surroundings).  The rest are written to
# islands_2021.json as candidates for Hig.  (lat, lon, who/why)
ISLANDS_KEEP = [(59.60571, -151.11924, 'Hig 2026-10-14: a real island, also in the 2023 SfM; Ed reads 1.4 m 13 m off')]
ISLAND_KEEP_R_M = 40.0
WORKERS = 4


def tiles():
    tmp = OUT / 'index.csv'; tmp.unlink(missing_ok=True)
    x0, y0, x1, y1 = BOUNDS
    subprocess.run([GDAL + 'ogr2ogr', '-f', 'CSV', str(tmp), str(INDEX), '-spat', *map(str, (x0, y0, x1, y1)),
                    '-lco', 'GEOMETRY=AS_WKT', '-overwrite'], check=True)
    out = []
    for r in csv.DictReader(open(tmp)):
        v = [float(t) for t in re.findall(r'[-\d.]+', r['WKT'])]
        out.append((Path(r['FileName']).stem + '.laz', min(v[0::2]), min(v[1::2]), max(v[0::2]), max(v[1::2])))
    return out


def lattice():
    with rasterio.open(VENDOR) as s:
        return s.transform.c, s.transform.f, s.crs


def grid_tile(t, X0, Y0):
    name, a, b, c, d = t
    dst = OUT / 'tiles' / (Path(name).stem + '.tif')
    if dst.exists():
        return dst
    src = PTS / 'laz' / name
    if not src.exists():
        raise FileNotFoundError(src)
    ox = X0 + np.floor(a - X0); oy = Y0 - np.floor(Y0 - d)            # top-left on the vendor lattice
    w = int(np.ceil(c - ox)); h = int(np.ceil(oy - b))
    common = dict(type='writers.gdal', resolution=1.0, origin_x=float(ox), origin_y=float(oy - h), width=w, height=h,
                  gdaldriver='GTiff', nodata=-9999, data_type='float32', radius=0.71, window_size=0)
    pipe = [{"type": "readers.las", "filename": str(src)},
            {"type": "filters.range", "limits": "Classification[2:2],Classification[9:9]"},
            {"type": "filters.ferry", "dimensions": "=>W"},
            {"type": "filters.assign", "value": ["W = 0", "W = 1 WHERE Classification == 9"]},
            dict(common, filename=str(dst) + '.z.tif', output_type='mean', dimension='Z'),
            dict(common, filename=str(dst) + '.w.tif', output_type='mean', dimension='W')]
    subprocess.run([GDAL + 'pdal', 'pipeline', '--stdin'], input=json.dumps(pipe), text=True, check=True,
                   capture_output=True)
    with rasterio.open(str(dst) + '.z.tif') as z, rasterio.open(str(dst) + '.w.tif') as wc:
        prof = z.profile; zz = z.read(1); ww = wc.read(1)
    prof.update(count=2, compress='deflate', predictor=3, tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(str(dst) + '.part', 'w', **prof) as o:
        o.write(zz, 1); o.write(np.where(ww < 0, 0, ww).astype(np.float32), 2)
    os.replace(str(dst) + '.part', dst); os.remove(str(dst) + '.z.tif'); os.remove(str(dst) + '.w.tif')
    return dst


def main():
    X0, Y0, crs = lattice()
    ts = tiles()
    missing = [t[0] for t in ts if not (PTS / 'laz' / t[0]).exists()]
    if missing and '--partial' not in sys.argv:
        raise SystemExit(f'{len(missing)} of {len(ts)} tiles not fetched yet: {missing[:5]}... (--partial: use what is here)')
    if missing:
        # --partial (an interim build while the fetch runs): the missing tiles' ground keeps the delivered DTM
        print(f'PARTIAL: {len(missing)} of {len(ts)} tiles not fetched; the delivered DTM stands there')
        ts = [t for t in ts if t[0] not in missing]
    with ThreadPoolExecutor(WORKERS) as ex:
        done = list(ex.map(lambda t: grid_tile(t, X0, Y0), ts))
    print(f'{len(done)} tiles gridded')
    vrt = OUT / 'tiles.vrt'
    subprocess.run([GDAL + 'gdalbuildvrt', '-q', '-overwrite', '-srcnodata', '-9999', str(vrt), *map(str, done)], check=True)
    # the merged DTM: the returns where there are any, the delivered DTM elsewhere -- on the recipe box, 1 m
    x0, y0, x1, y1 = BOUNDS
    W, H = int(x1 - x0), int(y1 - y0)
    T = rasterio.transform.from_origin(x0, y1, 1.0, 1.0)
    from rasterio.vrt import WarpedVRT
    def read(path, band=1, rs=Resampling.nearest):
        with rasterio.open(path) as s, WarpedVRT(s, crs=crs, transform=T, width=W, height=H, resampling=rs) as v:
            return v.read(band, masked=True).filled(np.nan).astype(np.float32)
    zp = read(vrt, 1); nw = read(vrt, 2); zv = read(VENDOR)
    dtm = np.where(np.isfinite(zp), zp, zv)
    # islands: water-classed cells clear of the lake surface, where the delivered DTM is the flat lake
    flat = np.isfinite(zv) & (np.abs(zv - LEVEL) < 0.35)
    high = flat & (np.nan_to_num(nw) > 0) & (np.nan_to_num(zp, nan=-1e9) > LEVEL + ISLAND_DZ_M)
    high = ndimage.binary_closing(high, iterations=1) & flat
    lab, n = ndimage.label(high, structure=np.ones((3, 3), bool))
    from rasterio.warp import transform as _tr
    kx, ky = _tr('EPSG:4326', crs, [k[1] for k in ISLANDS_KEEP], [k[0] for k in ISLANDS_KEEP])
    isl, rows = np.zeros_like(high), []
    for i in range(1, n + 1):
        m = lab == i
        a = float(m.sum())
        if a < ISLAND_MIN_M2:
            continue
        r, c = np.nonzero(m)
        xs, ys = x0 + c + 0.5, y1 - r - 0.5
        keep = [ISLANDS_KEEP[j][2] for j in range(len(kx)) if np.min(np.hypot(xs - kx[j], ys - ky[j])) < ISLAND_KEEP_R_M]
        if keep:
            isl |= m
        rows.append(dict(x=round(float(xs.mean()), 1), y=round(float(ys.mean()), 1), area_m2=int(a),
                         z_max=round(float(np.nanmax(zp[m])), 2), z_mean=round(float(np.nanmean(zp[m])), 2),
                         kept=bool(keep), why=keep[0] if keep else 'candidate (iceberg?), not used'))
    dtm[isl] = np.where(np.isfinite(zp[isl]), zp[isl], dtm[isl])
    prof = dict(driver='GTiff', height=H, width=W, count=1, dtype='float32', crs=crs, transform=T, nodata=np.nan,
                compress='deflate', predictor=3, tiled=True)
    tmp = OUT / 'dtm_1m_tmp.tif'
    with rasterio.open(tmp, 'w', **prof) as o:
        o.write(dtm, 1)
    subprocess.run([GDAL + 'gdal_translate', '-q', '-of', 'COG', '-co', 'COMPRESS=DEFLATE', '-co', 'PREDICTOR=3',
                    str(tmp), str(OUT / 'dtm_1m.tif')], check=True)
    tmp.unlink()
    # islands on the recipe grid (2 m): any 1 m island cell in the 2 m cell
    res = R['grid']['res_m']; k = int(round(res))
    i2 = isl[:H // k * k, :W // k * k].reshape(H // k, k, W // k, k).any(axis=(1, 3))
    with rasterio.open(OUT / 'islands_2021.tif', 'w', driver='GTiff', height=i2.shape[0], width=i2.shape[1], count=1,
                       dtype='uint8', crs=crs, transform=rasterio.transform.from_origin(x0, y1, res, res), nodata=255,
                       compress='deflate') as o:
        o.write(i2.astype(np.uint8), 1)
    info = dict(partial=bool(missing), tiles_missing=len(missing), level=LEVEL, island_dz_m=ISLAND_DZ_M, island_min_m2=ISLAND_MIN_M2, islands=rows,
                cells_from_returns_km2=round(float(np.isfinite(zp).sum()) / 1e6, 2),
                lake_cells_unflattened_km2=round(float((flat & np.isfinite(zp)).sum()) / 1e6, 3))
    (OUT / 'islands_2021.json').write_text(json.dumps(info, indent=1))
    print(json.dumps({k: v for k, v in info.items() if k != 'islands'}), f'{len(rows)} patches, {sum(r["kept"] for r in rows)} kept')
    for r in sorted(rows, key=lambda r: -r['area_m2'])[:15]:
        print(' ', r)


if __name__ == '__main__':
    main()
