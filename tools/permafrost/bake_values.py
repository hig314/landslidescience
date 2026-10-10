#!/opt/anaconda3/bin/python3
"""Value tiles for the map's patch sampler: the 60 m permafrost fields as
terrain-RGB PMTiles the browser can decode to numbers (the colour overlays
are lossy WebP and cannot be read back).

    bake_values.py [field ...]      # default: all six

One zoom only (z10, ~69 m/px at 63 N -- the products are 60 m), so there is
no overview resampling to get wrong (averaging terrain-RGB bytes across a
carry invents errors, see CLAUDE.md on the lidar build). Encoding is the
Mapbox terrain-RGB formula on a SCALED value, v_enc = v * scale:
    code = (v_enc + 10000) / 0.1 = R*65536 + G*256 + B,  alpha 0 = nodata
so the decoder is the profile tool's, divided by `scale` afterwards:
    elev60 scale 0.1 (1 m)   maat60 / magt60 10 (0.1 C)
    pzi60 / prob60 1000 (0.001)
The precision is deliberately coarse: it is plot precision, and the low
byte of a finer encoding is noise that PNG cannot compress (northness at
1e-5 was a 1 GB archive; at 0.01 it is a fraction of that).
Output: permafrost_build/pmtiles/pfv_<field>.pmtiles (PNG, lossless), served
at /overlays/pfv_<field>.pmtiles like the colour overlays; map.js has the
scale table (PF_VALUE_SCALE in permafrost.js).
"""
import math, shutil, subprocess, sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lidar'))
import build_lidar as BL                                    # noqa: E402

B = Path('/Volumes/Nunatak/permafrost_build')
GDAL = Path('/opt/homebrew/bin')
Z = 10
# north60 is deliberately absent: northness at 60 m is noise-like and did not
# compress (1 GB at 0.01); permafrost.js derives it from the elevation tile.
FIELDS = {'elev60': (B / 'elev60.tif', 0.1),
          'maat60': (B / 'out/gruber_maat60.tif', 10.0), 'pzi60': (B / 'out/gruber_pzi60.tif', 1000.0),
          'magt60': (B / 'out/obu_magt60.tif', 10.0), 'prob60': (B / 'out/obu_prob60.tif', 1000.0)}
LON0, LON1, LAT0, LAT1 = -170.0, -128.0, 54.0, 72.0
R = 6378137.0
WORLD = 2 * math.pi * R


def run(cmd):
    subprocess.run([str(c) for c in cmd], check=True, env={'PATH': str(GDAL) + ':/usr/bin:/bin', 'PROJ_NETWORK': 'ON'})


def tile_bounds():
    n = 2 ** Z
    def tx(lon): return int((lon + 180) / 360 * n)
    def ty(lat):
        la = math.radians(lat); return int((1 - math.log(math.tan(la) + 1 / math.cos(la)) / math.pi) / 2 * n)
    x0, x1, y0, y1 = tx(LON0), tx(LON1), ty(LAT1), ty(LAT0)
    s = WORLD / n
    return (-WORLD / 2 + x0 * s, WORLD / 2 - (y1 + 1) * s, -WORLD / 2 + (x1 + 1) * s, WORLD / 2 - y0 * s), s / 256


def bake(name):
    src, scale = FIELDS[name]
    work = B / 'bake' / f'pfv_{name}'
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    te, res = tile_bounds()
    warped = work / 'w3857.tif'
    print(f'== {name}: warp to z{Z} grid ({res:.3f} m/px)', flush=True)
    run([GDAL / 'gdalwarp', '-q', '-overwrite', '-t_srs', 'EPSG:3857', '-te', *te, '-tr', res, res,
         '-r', 'bilinear', '-ot', 'Float32', '-srcnodata', '-9999', '-dstnodata', '-9999',
         '-multi', '-wo', 'NUM_THREADS=ALL_CPUS', '-co', 'COMPRESS=ZSTD', '-co', 'TILED=YES', '-co', 'BIGTIFF=YES', src, warped])
    rgba = work / 'rgba.tif'
    print(f'== {name}: encode', flush=True)
    with rasterio.open(warped) as d:
        prof = d.profile.copy()
        prof.update(count=4, dtype='uint8', nodata=None, compress='DEFLATE', predictor=2, tiled=True, bigtiff='YES')
        with rasterio.open(rgba, 'w', **prof) as o:
            o.colorinterp = [rasterio.enums.ColorInterp.red, rasterio.enums.ColorInterp.green,
                             rasterio.enums.ColorInterp.blue, rasterio.enums.ColorInterp.alpha]
            for r0 in range(0, d.height, 1024):
                w = Window(0, r0, d.width, min(1024, d.height - r0))
                v = d.read(1, window=w).astype(np.float64)
                ok = (v != -9999) & np.isfinite(v)
                code = np.where(ok, np.round((v * scale + 10000.0) / 0.1), 0).astype(np.int64)
                code = np.clip(code, 0, 2 ** 24 - 1)
                out = np.stack([(code >> 16) & 255, (code >> 8) & 255, code & 255, np.where(ok, 255, 0)]).astype(np.uint8)
                o.write(out, window=w)
    tiles = work / 'tiles'
    print(f'== {name}: tiles z{Z}', flush=True)
    run([GDAL / 'gdal', 'raster', 'tile', '--min-zoom', Z, '--max-zoom', Z, '-r', 'near', '--convention', 'xyz',
         '--skip-blank', '--webviewer', 'none', '-j', 'ALL_CPUS', '--no-intersection-ok', rgba, tiles])
    ds = {'id': f'pfv_{name}', 'title': f'{name} values (terrain-RGB, scale {scale:g})', 'min_zoom': Z, 'max_zoom': Z}
    mb = work / 'v.mbtiles'
    n = BL.dir_to_mbtiles(tiles, mb, ds, 'png', f'{name} as terrain-RGB of value*{scale:g}; see tools/permafrost/bake_values.py')
    out = B / 'pmtiles' / f'pfv_{name}.pmtiles'
    run(['/opt/homebrew/bin/pmtiles', 'convert', mb, out])
    shutil.rmtree(work, ignore_errors=True)   # the Nunatak volume sometimes reports a dir not empty mid-delete
    print(f'== pfv_{name}: {n} tiles -> {out} ({out.stat().st_size / 2**20:.0f} MB)', flush=True)


if __name__ == '__main__':
    names = sys.argv[1:] or list(FIELDS)
    for n in names:
        bake(n)
