#!/opt/anaconda3/bin/python3
"""Bake one permafrost raster into a coloured PMTiles overlay.

    bake_overlay.py <id> <in.tif> <ramp.txt> <maxzoom> "<title>" ["<description>"]

gdaldem color-relief (the ramp's RGBA, nodata transparent) -> `gdal raster
tile` to XYZ PNG at z3..maxzoom (it reprojects to Web Mercator itself;
bilinear for continuous fields) -> MBTiles -> PMTiles in
permafrost_build/pmtiles/<id>.pmtiles, for R2 (<bucket>/overlays/).

PMTiles rather than a tile tree on the droplet: the five overlays are a few
GB together and the droplet has 7 GB of disk. The map reads them exactly as
it reads the lidar pyramids (pmtiles:// raster source).

Reuses dir_to_mbtiles from tools/lidar/build_lidar.py so the archive layout
is the one every PMTiles reader here already understands.

BAKE_FMT=webp (env; default png) writes lossy WebP at BAKE_QUALITY (default
85). A continuous ramp does not compress as PNG: Pastick at z12 was 103,000
tiles and 10 GB; the same bake in WebP is a fraction of that, and the
colour error is far below what a probability ramp can show.
"""
import os
import shutil, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lidar'))
import build_lidar as BL                                    # noqa: E402

B = Path('/Volumes/Nunatak/permafrost_build')
GDAL = Path('/opt/homebrew/bin')
PMTILES = '/opt/homebrew/bin/pmtiles'


def run(cmd):
    print('  $', ' '.join(str(c) for c in cmd[:6]), '…' if len(cmd) > 6 else '', flush=True)
    subprocess.run([str(c) for c in cmd], check=True,
                   env={'PATH': str(GDAL) + ':/usr/bin:/bin', 'PROJ_NETWORK': 'ON'})


def main():
    if len(sys.argv) < 6:
        sys.exit(__doc__)
    oid, src, ramp, maxz, title = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
    desc = sys.argv[6] if len(sys.argv) > 6 else title
    work = B / 'bake' / oid
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    rgba = work / 'rgba.tif'
    print(f'== {oid}: colour-relief')
    run([GDAL / 'gdaldem', 'color-relief', '-alpha', '-q', '-co', 'COMPRESS=ZSTD', '-co', 'TILED=YES',
         '-co', 'BIGTIFF=YES', src, ramp, rgba])
    tiles = work / 'tiles'
    print(f'== {oid}: tiles z3-z{maxz}')
    fmt = os.environ.get('BAKE_FMT', 'png').lower()
    fmt_args = ['-f', 'WEBP', '--co', f"QUALITY={os.environ.get('BAKE_QUALITY', '85')}"] if fmt == 'webp' else []
    run([GDAL / 'gdal', 'raster', 'tile', '--min-zoom', 3, '--max-zoom', maxz, '-r', 'bilinear',
         '--convention', 'xyz', '--skip-blank', '--webviewer', 'none', '-j', 'ALL_CPUS',
         '--no-intersection-ok', *fmt_args, rgba, tiles])
    ds = {'id': oid, 'title': title, 'min_zoom': 3, 'max_zoom': maxz}
    mb = work / f'{oid}.mbtiles'
    n = BL.dir_to_mbtiles(tiles, mb, ds, fmt, desc)
    out = B / 'pmtiles' / f'{oid}.pmtiles'
    out.parent.mkdir(exist_ok=True)
    run([PMTILES, 'convert', mb, out])
    shutil.rmtree(tiles, ignore_errors=True); mb.unlink(missing_ok=True); rgba.unlink(missing_ok=True)
    print(f'== {oid}: {n} tiles -> {out} ({out.stat().st_size / 2**20:.0f} MB)')


if __name__ == '__main__':
    main()
