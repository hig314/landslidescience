"""Download NASA IceBridge L2 bed elevation / ice thickness CSVs for Alaska.

Two instruments, complementary in time (2013-2016 then 2016-2021), both
scoped natively to Alaska + NW Canada and both carrying the identical field
layout (see the NSIDC technical references for IRUAFHF2 / IRARES2):

  trace, lon_deg_e, lat_deg_n, height_m, surface_sample, surface_twtt_s,
  surface_height_m, bed_sample, bed_twtt_s, bed_height_m, ice_thickness_m

bed_height_m / ice_thickness_m are left EMPTY (not a sentinel value) on rows
where no glacier bed could be interpreted.

  IRUAFHF2  IceBridge UAF L2 HF Bed Elevation and Ice Thickness  2013-03-22 .. 2016-08-16
  IRARES2   IceBridge ARES L2 Bed Elevation and Ice Thickness    2016-05-28 .. 2021-05-13

Downloads every granule to data/glaciers/icebridge_raw/<short_name>/. That
directory is raw-data staging, not a build product — exclude it from the
prod data/ rsync the same way experiments/ and fit_tiles/ are excluded.
Run tools/build_icebridge_points.py afterward to turn this into the
GeoJSON layers the map actually serves.

Requires an Earthdata Login account (https://urs.earthdata.nasa.gov/) —
earthaccess.login() will prompt once and cache credentials in ~/.netrc.
Run from a venv with: earthaccess

Usage: python tools/fetch_icebridge_data.py
"""
from pathlib import Path

import earthaccess

OUT_DIR = Path(__file__).resolve().parent.parent / 'data' / 'glaciers' / 'icebridge_raw'

# Alaska + NW Canada — the datasets' own native spatial coverage, so this is
# a no-op filter today; kept explicit in case a future version widens it.
BBOX = (-157, 56, -129, 63)

SHORT_NAMES = ['IRUAFHF2', 'IRARES2']


def main():
    earthaccess.login()
    for short_name in SHORT_NAMES:
        results = earthaccess.search_data(short_name=short_name, bounding_box=BBOX)
        print(f'{short_name}: {len(results)} granules')
        if not results:
            continue
        out_dir = OUT_DIR / short_name
        out_dir.mkdir(parents=True, exist_ok=True)
        earthaccess.download(results, str(out_dir))


if __name__ == '__main__':
    main()
