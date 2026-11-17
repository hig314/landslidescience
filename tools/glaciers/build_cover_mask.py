#!/opt/anaconda3/bin/python3
"""NSIDC G10040 (Roberts-Pierel and others 2022) -> ONE raster PMTiles archive
per quantity holding every epoch, for the statewide ice-cover time layer on
/glaciers/.

    tools/glaciers/build_cover_mask.py            # everything
    tools/glaciers/build_cover_mask.py --from combine   # reuse the burned rasters
    tools/glaciers/build_cover_mask.py --from tile

Out: data/overlays/glac_cover_mask.pmtiles   (ice of any kind)
     data/overlays/glac_debris_mask.pmtiles  (debris-covered ice)
served by the existing /overlays/<id>.pmtiles route: the local file in dev,
R2 in production (tools/permafrost/r2_sync_overlays.sh).

WHAT A TILE HOLDS
-----------------
Not a picture. Each pixel is a bit mask of the epochs in which that 30 m cell
was mapped as ice: bit i is epoch YEARS[i] (1986, 1988 ... 2020), packed
R = bits 0-7, G = bits 8-15, B = bits 16-17, alpha 255 where any bit is set
and 0 elsewhere. The page (gl_retreat.js, the `glcover` protocol) decodes a
tile once and colours it for whatever year the bar is on, so stepping through
eighteen epochs needs one download and no refetch, and "ice in 1986 that is
gone by this year" is a comparison of two bits.

So the tiles must stay EXACT: PNG, nearest-neighbour at every zoom, alpha
only ever 0 or 255 (a canvas premultiplies alpha, and anything between would
corrupt the bits on the way back out).

WHY NOT VECTOR TILES (tried first, 2026-11-16)
----------------------------------------------
GDAL's MVT/PMTiles writer on these polygons (each icefield one polygon with
thousands of holes) filled whole tiles solid and dropped others; and eighteen
vector archives would have meant a refetch on every step of the year bar.

The publisher's per-polygon attributes (RGI 6.0 id, that year's area) are not
in the tiles. They belong in a table (GLACIERS_PLAN.md, phase 3).
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lidar'))
import build_lidar as BL                                    # noqa: E402

SRC = Path('/Volumes/Nunatak/glaciers_src/g10040')
WORK = Path('/Volumes/Nunatak/glaciers_build/cover')
OUT = Path(__file__).resolve().parents[2] / 'data' / 'overlays'
GDAL = Path('/opt/homebrew/bin')          # not the QGIS GDAL that is first on PATH
PMTILES = '/opt/homebrew/bin/pmtiles'
YEARS = list(range(1986, 2021, 2))        # bit i = YEARS[i]; gl_retreat.js holds the same list
RES = 30
MAXZ = 11                                 # 38 m per pixel at 60 N: the data's own resolution
KINDS = {'cover': ('overall_area', 'overall_glacier_covered_area'),
         'debris': ('supraglacial_debris_area', 'supraglacial_debris_area')}
ENV = {'PATH': str(GDAL) + ':/usr/bin:/bin'}


def run(cmd, **kw):
    print('  $', ' '.join(str(c) for c in cmd[:7]), '…' if len(cmd) > 7 else '', flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, env=ENV, **kw)


def shp(kind, year):
    d, stem = KINDS[kind]
    return SRC / d / f'AK_{year}_{stem}.shp'


def grid():
    """One grid for every epoch: the union of the cover extents, snapped
    outward to the resolution."""
    x0 = y0 = float('inf'); x1 = y1 = float('-inf')
    for y in YEARS:
        out = run([GDAL / 'ogrinfo', '-so', '-al', shp('cover', y)], capture_output=True, text=True).stdout
        m = re.search(r'Extent: \(([-\d.]+), ([-\d.]+)\) - \(([-\d.]+), ([-\d.]+)\)', out)
        nums = [float(v) for v in m.groups()]
        x0, y0, x1, y1 = min(x0, nums[0]), min(y0, nums[1]), max(x1, nums[2]), max(y1, nums[3])
    snap = lambda v, up: (np.ceil(v / RES) if up else np.floor(v / RES)) * RES
    return snap(x0, False), snap(y0, False), snap(x1, True), snap(y1, True)


def burn(te):
    WORK.mkdir(parents=True, exist_ok=True)
    for kind in KINDS:
        for y in YEARS:
            out = WORK / f'{kind}_{y}.tif'
            if out.exists():
                continue
            tmp = out.with_suffix('.part.tif')
            run([GDAL / 'gdal_rasterize', '-q', '-burn', 1, '-ot', 'Byte', '-init', 0,
                 '-tr', RES, RES, '-te', *te, '-co', 'COMPRESS=ZSTD', '-co', 'TILED=YES',
                 '-co', 'BLOCKXSIZE=512', '-co', 'BLOCKYSIZE=512', '-co', 'BIGTIFF=YES',
                 '-co', 'SPARSE_OK=TRUE', shp(kind, y), tmp])
            tmp.rename(out)
            print(f'burned {kind} {y}', flush=True)


def combine(kind):
    """Eighteen one-bit rasters -> one RGBA raster of bit masks. Block by
    block: the grid is over four billion cells."""
    srcs = [rasterio.open(WORK / f'{kind}_{y}.tif') for y in YEARS]
    prof = srcs[0].profile.copy()
    prof.update(count=4, dtype='uint8', compress='zstd', tiled=True, blockxsize=512, blockysize=512,
                BIGTIFF='YES', SPARSE_OK='TRUE', photometric='RGB', nodata=None)
    out = WORK / f'{kind}_mask.tif'
    step = 8192
    with rasterio.open(out, 'w', **prof) as dst:
        dst.colorinterp = [rasterio.enums.ColorInterp.red, rasterio.enums.ColorInterp.green,
                           rasterio.enums.ColorInterp.blue, rasterio.enums.ColorInterp.alpha]
        H, W = srcs[0].height, srcs[0].width
        for r in range(0, H, step):
            for c in range(0, W, step):
                w = Window(c, r, min(step, W - c), min(step, H - r))
                m = np.zeros((int(w.height), int(w.width)), dtype=np.uint32)
                for i, s in enumerate(srcs):
                    a = s.read(1, window=w)
                    if a.any():
                        m |= (a.astype(np.uint32) & 1) << i
                if not m.any():
                    continue
                rgba = np.stack([(m & 0xFF), (m >> 8) & 0xFF, (m >> 16) & 0xFF,
                                 np.where(m > 0, 255, 0)]).astype(np.uint8)
                dst.write(rgba, window=w)
            print(f'  {kind}: row {min(r + step, H)} / {H}', flush=True)
    for s in srcs:
        s.close()
    return out


def tile(kind, tif):
    oid = f'glac_{kind}_mask'
    tiles = WORK / f'{oid}_tiles'
    if tiles.exists():
        shutil.rmtree(tiles)
    # -r near at EVERY level: a bit mask cannot be averaged.
    run([GDAL / 'gdal', 'raster', 'tile', '--min-zoom', 3, '--max-zoom', MAXZ, '-r', 'nearest',
         '--overview-resampling', 'nearest', '--convention', 'xyz', '--skip-blank', '--webviewer', 'none',
         '-j', 'ALL_CPUS', '--no-intersection-ok', tif, tiles])
    ds = {'id': oid, 'title': f'Glacier {kind} epochs 1986-2020 (bit mask)', 'min_zoom': 3, 'max_zoom': MAXZ}
    mb = WORK / f'{oid}.mbtiles'
    mb.unlink(missing_ok=True)
    n = BL.dir_to_mbtiles(tiles, mb, ds, 'png',
                          'NSIDC G10040 epoch bit mask: bit i = year 1986 + 2i; R low byte')
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f'{oid}.pmtiles'
    out.unlink(missing_ok=True)
    run([PMTILES, 'convert', mb, out])
    shutil.rmtree(tiles, ignore_errors=True)
    mb.unlink(missing_ok=True)
    print(f'== {oid}: {n} tiles -> {out} ({out.stat().st_size / 2**20:.0f} MB)', flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--from', dest='start', default='burn', choices=['burn', 'combine', 'tile'])
    ap.add_argument('--kind', choices=list(KINDS), action='append')
    a = ap.parse_args()
    kinds = a.kind or list(KINDS)
    if a.start == 'burn':
        te = grid()
        print('grid', te, flush=True)
        burn(te)
    for k in kinds:
        tif = WORK / f'{k}_mask.tif'
        if a.start in ('burn', 'combine'):
            tif = combine(k)
        tile(k, tif)


if __name__ == '__main__':
    main()
