#!/opt/anaconda3/bin/python3
"""Bake NASA's global landslide susceptibility map into one PMTiles overlay.

    tools/build_nasa_susc.py [max_zoom]        (default 8)

Source: NASA GSFC's GeoTIFF of the Stanley & Kirschbaum (2017) map, in the
February 2023 update NASA distributes (the 2017 original is no longer
offered): 30 arc-seconds (~1 km), 72 N to 60 S, Int8 classes
    0 = sea / not assessed   1 very low   2 low   3 moderate   4 high   5 very high
Downloaded once into data/susc_src/nasa/ (33 MB; data/ is gitignored).

Steps: gdaldem color-relief with EXACT class colours (tools/nasa_susc_color.txt,
-nearest_color_entry, class 0 transparent) -> `gdal raster tile` z0..max in
Web Mercator, nearest at the deepest zoom so a class is never blended into
one the source does not hold, averaged below it (a picture of the classes,
like every pyramid here) -> MBTiles -> data/overlays/susc_nasa.pmtiles.

z8 is ~600 m a pixel at the equator and ~300 m at 60 N, so the 1 km cells
are resolved everywhere; the map overzooms from there, which shows the cells
as the blocks they are.

Served by /overlays/susc_nasa.pmtiles (local file in dev, R2 in production:
tools/permafrost/r2_sync_overlays.sh). PMTiles on R2 rather than a tile tree
under data/susc_tiles/, because the droplet's disk is nearly full.
"""
import shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'lidar'))
import build_lidar as BL                                    # noqa: E402

URL = 'https://gpm.nasa.gov/sites/default/files/downloads/global-landslide-susceptibility-map-2-27-23.tif'
SRC_DIR = ROOT / 'data' / 'susc_src' / 'nasa'
SRC = SRC_DIR / URL.rsplit('/', 1)[1]
RAMP = ROOT / 'tools' / 'nasa_susc_color.txt'
OUT = ROOT / 'data' / 'overlays' / 'susc_nasa.pmtiles'
GDAL = Path('/opt/homebrew/bin')
PMTILES = '/opt/homebrew/bin/pmtiles'
OID = 'susc_nasa'


def run(cmd):
    print('  $', ' '.join(str(c) for c in cmd[:7]), '…' if len(cmd) > 7 else '', flush=True)
    subprocess.run([str(c) for c in cmd], check=True,
                   env={'PATH': str(GDAL) + ':/usr/bin:/bin'})


def main():
    maxz = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    if not SRC.exists():
        print('== download', URL)
        run(['curl', '-fL', '--retry', '3', '-A', 'landslidescience.org build', '-o', SRC, URL])
    work = SRC_DIR / 'bake'
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    rgba = work / 'rgba.tif'
    print('== colour-relief')
    run([GDAL / 'gdaldem', 'color-relief', '-alpha', '-nearest_color_entry', '-q',
         '-co', 'COMPRESS=ZSTD', '-co', 'TILED=YES', '-co', 'BIGTIFF=YES', SRC, RAMP, rgba])
    tiles = work / 'tiles'
    print(f'== tiles z0-z{maxz}')
    run([GDAL / 'gdal', 'raster', 'tile', '--min-zoom', 0, '--max-zoom', maxz,
         '-r', 'nearest', '--overview-resampling', 'average',
         '--convention', 'xyz', '--skip-blank', '--webviewer', 'none', '-j', 'ALL_CPUS',
         rgba, tiles])
    ds = {'id': OID, 'title': 'Global landslide susceptibility (NASA)', 'min_zoom': 0, 'max_zoom': maxz}
    mb = work / f'{OID}.mbtiles'
    n = BL.dir_to_mbtiles(tiles, mb, ds, 'png',
                          'NASA GSFC global landslide susceptibility, Stanley & Kirschbaum (2017), '
                          '2023 update; classes coloured by landslidescience.org')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    run([PMTILES, 'convert', mb, OUT])
    shutil.rmtree(work, ignore_errors=True)
    print(f'== {n} tiles -> {OUT} ({OUT.stat().st_size / 2**20:.1f} MB)')


if __name__ == '__main__':
    main()
