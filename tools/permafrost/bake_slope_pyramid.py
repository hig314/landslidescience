#!/opt/anaconda3/bin/python3
"""Slope overlay computed at each zoom's own resolution (Hig, 2026-10-10).

A slope map tiled the usual way shows, at z6, the steepest 60 m cell
inside every ~2 km pixel -- a mountain range reads as uniformly steep.
Here each zoom level gets slope computed from the ELEVATION averaged to
that zoom's ground resolution, so zoomed out only ground that is steep at
that scale is coloured, and zooming in resolves the finer steep ground.
The same reason the lidar build resamples elevation per zoom rather than
letting the tiler average encoded bytes.

Per zoom z in MIN_Z..MAX_Z:
  res_z  = Web Mercator pixel size at LAT0 (63 N) for z        (m)
  elev_z = elev60_land warped to EPSG:3338 at res_z, average (sea nodata)
  slope  = gdaldem slope on elev_z (metres in, metres out)
  colour = gdaldem color-relief with the slope ramp, alpha
  tiles  = gdal raster tile --min-zoom z --max-zoom z (bilinear) into ONE
           tile directory, then one MBTiles -> PMTiles.
Zooms deeper than MAX_Z overzoom the z10 tiles, which is the 60 m grid.

    bake_slope_pyramid.py            -> permafrost_build/pmtiles/sp_slope.pmtiles
"""
import math, os, shutil, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lidar'))
import build_lidar as BL                                    # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
B = Path('/Volumes/Nunatak/permafrost_build')
GDAL = Path('/opt/homebrew/bin')
SRC = B / 'out' / 'elev60_land.tif'
RAMP = ROOT / 'tools' / 'permafrost' / 'sp_color_slope.txt'
TE = ['-1041000', '443000', '1665000', '2622000']
MIN_Z, MAX_Z, LAT0 = 3, 10, 63.0
OID = 'sp_slope'


def run(cmd):
    subprocess.run([str(c) for c in cmd], check=True, env={'PATH': str(GDAL) + ':/usr/bin:/bin', 'PROJ_NETWORK': 'ON'})


def main():
    work = B / 'bake' / OID
    shutil.rmtree(work, ignore_errors=True)
    tiles = work / 'tiles'; tiles.mkdir(parents=True)
    fmt = os.environ.get('BAKE_FMT', 'webp').lower()
    fmt_args = ['-f', 'WEBP', '--co', f"QUALITY={os.environ.get('BAKE_QUALITY', '85')}"] if fmt == 'webp' else []
    for z in range(MIN_Z, MAX_Z + 1):
        res = 156543.03392804097 * math.cos(math.radians(LAT0)) / 2 ** z
        res = 60.0 if z == MAX_Z else round(res)
        e = work / f'elev_z{z}.tif'; s = work / f'slope_z{z}.tif'; c = work / f'rgba_z{z}.tif'
        print(f'== z{z}: {res:.0f} m', flush=True)
        run([GDAL / 'gdalwarp', '-q', '-overwrite', '-te', *TE, '-tr', res, res, '-tap', '-r', 'average', '-ot', 'Float32',
             '-srcnodata', '-9999', '-dstnodata', '-9999', '-multi', '-wo', 'NUM_THREADS=ALL_CPUS',
             '-co', 'COMPRESS=ZSTD', '-co', 'TILED=YES', '-co', 'BIGTIFF=YES', SRC, e])
        run([GDAL / 'gdaldem', 'slope', '-q', '-compute_edges', '-co', 'COMPRESS=ZSTD', '-co', 'TILED=YES', '-co', 'BIGTIFF=YES', e, s])
        run([GDAL / 'gdaldem', 'color-relief', '-alpha', '-q', '-co', 'COMPRESS=ZSTD', '-co', 'TILED=YES', '-co', 'BIGTIFF=YES', s, RAMP, c])
        run([GDAL / 'gdal', 'raster', 'tile', '--min-zoom', z, '--max-zoom', z, '-r', 'bilinear', '--convention', 'xyz',
             '--skip-blank', '--webviewer', 'none', '-j', 'ALL_CPUS', '--no-intersection-ok', *fmt_args, c, tiles])
        for p in (e, s, c):
            p.unlink(missing_ok=True)
    ds = {'id': OID, 'title': 'Slope, computed at each zoom level\'s resolution (60 m at z10)', 'min_zoom': MIN_Z, 'max_zoom': MAX_Z}
    mb = work / f'{OID}.mbtiles'
    n = BL.dir_to_mbtiles(tiles, mb, ds, fmt, 'Slope in degrees from elevation averaged to each zoom level; see tools/permafrost/bake_slope_pyramid.py')
    out = B / 'pmtiles' / f'{OID}.pmtiles'
    run(['/opt/homebrew/bin/pmtiles', 'convert', mb, out])
    shutil.rmtree(work, ignore_errors=True)
    print(f'== {OID}: {n} tiles -> {out} ({out.stat().st_size / 2**20:.0f} MB)')


if __name__ == '__main__':
    main()
