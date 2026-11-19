#!/opt/anaconda3/bin/python3
"""Fetch the Alaska part of Daymet V4 R1 annual summaries (ORNL DAAC 2130), 1 km.

Reads only the window out of each North America GeoTIFF over HTTP (GDAL
/vsicurl with the Earthdata login in ~/.netrc), so 30 years x 3 variables is
a few hundred MB, not 8 GB. Resumable: finished files are skipped.

    daymet_fetch.py [--years 1991 2020] [--dest /Volumes/Nunatak/climate_src/daymet/annual]
"""
import argparse, json, os, subprocess, sys
from pathlib import Path

import numpy as np
from rasterio.warp import transform as tr

ROOT = Path(__file__).resolve().parents[2]
GDAL = '/opt/homebrew/bin/gdal_translate'
BASE = 'https://data.ornldaac.earthdata.nasa.gov/protected/daymet/Daymet_Annual_V4R1/data/daymet_v4_{var}_na_{year}.tif'
LCC = '+proj=lcc +lat_1=25 +lat_2=60 +lat_0=42.5 +lon_0=-100 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs'
VARS = ('prcp_annttl', 'tmax_annavg', 'tmin_annavg')


def window():
    """The coverage polygon's bounds in Daymet's projection, with a 20 km margin, on the 1 km grid."""
    g = json.loads((ROOT / 'data/regions_seed/alaska_3338.geojson').read_text())
    pts = []

    def walk(c):
        if isinstance(c[0], (int, float)):
            pts.append(c[:2])
        else:
            for k in c:
                walk(k)
    for f in g.get('features', [g]):
        walk(f.get('geometry', f)['coordinates'])
    p = np.array(pts)
    x, y = tr('EPSG:3338', LCC, p[:, 0].tolist(), p[:, 1].tolist())
    r = lambda v, up: (np.ceil(v / 1000) if up else np.floor(v / 1000)) * 1000 + (250 if False else 0)
    # Daymet's grid lines sit at ...750 / ...500; snapping to whole km only shifts the request, GDAL reads whole source pixels
    return r(min(x) - 20000, 0), r(max(y) + 20000, 1), r(max(x) + 20000, 1), r(min(y) - 20000, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--years', type=int, nargs=2, default=[1991, 2020])
    ap.add_argument('--dest', default='/Volumes/Nunatak/climate_src/daymet/annual')
    a = ap.parse_args()
    dest = Path(a.dest); dest.mkdir(parents=True, exist_ok=True)
    ulx, uly, lrx, lry = window()
    print('window', ulx, uly, lrx, lry, flush=True)
    jar = str(dest / '.cookies')
    env = dict(os.environ, GDAL_HTTP_NETRC='YES', GDAL_HTTP_COOKIEFILE=jar, GDAL_HTTP_COOKIEJAR=jar,
               GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR', CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tif',
               GDAL_HTTP_MAX_RETRY='5', GDAL_HTTP_RETRY_DELAY='10')
    for var in VARS:
        for year in range(a.years[0], a.years[1] + 1):
            out = dest / f'{var}_{year}.tif'
            if out.exists():
                continue
            tmp = out.with_suffix('.part.tif')
            r = subprocess.run([GDAL, '-q', '-projwin', str(ulx), str(uly), str(lrx), str(lry), '-co', 'COMPRESS=DEFLATE', '-co', 'TILED=YES',
                                '/vsicurl/' + BASE.format(var=var, year=year), str(tmp)], env=env, capture_output=True, text=True)
            if r.returncode or not tmp.exists():
                print('FAILED', var, year, r.stderr[-300:], flush=True)
                tmp.unlink(missing_ok=True)
                continue
            tmp.rename(out)
            print('ok', var, year, out.stat().st_size // 1024, 'kB', flush=True)


if __name__ == '__main__':
    main()
