#!/usr/bin/env python
"""build_dem_datum_ak.py : the height correction that takes PGC's ArcticDEM 'Height Orthometric' (EGM2008) to NAVD88
(GEOID12B) in Alaska, as a coarse grid the browser reads (inventory/static/inventory/js/dem_datum_ak.js).

    /opt/anaconda3/bin/python tools/build_dem_datum_ak.py [--step 0.25] [--check]

Why: the profile and the Alt-click probe put ArcticDEM beside NAVD88 surfaces (3DEP through Mapterhorn, the lidar).
EGM2008 heights sit about a metre BELOW NAVD88 in south-central Alaska (1.06 m at Petrof Lake, measured against our
staged NAVD88 mosaic, 2026-11-21), which reads as an error in one of the DEMs. dem_read.js adds this grid to the
ArcticDEM samples as they are decoded, so everything downstream sees NAVD88.

    H_navd88 = h_itrf + dh - N_geoid12b          (what tools/lidar/topobathy/arcticdem_mosaic.py does)
    H_pgc    = h_itrf - N_egm2008
    correction = H_navd88 - H_pgc = N_egm2008 - N_geoid12b + dh,   dh = h_NAD83(2011) - h_ITRF2014  (about -0.45 m)

Only inside Alaska: GEOID12B's grid runs into Canada, but there Mapterhorn is Copernicus (EGM2008) and NAVD88 means
nothing, so cells east of a CRUDE border line (good to ~15 km; it crosses icefields) carry no correction.
Needs PROJ's grids us_nga_egm08_25.tif and us_noaa_g2012ba0.tif and cs2cs (homebrew).
"""
import argparse, base64, subprocess, sys
import numpy as np, rasterio
from scipy.ndimage import map_coordinates
from shapely.geometry import Polygon, Point
from shapely.prepared import prep
PROJ = "/opt/homebrew/share/proj"; CS2CS = "/opt/homebrew/bin/cs2cs"
LON0, LON1, LAT0, LAT1 = -180.0, -129.0, 50.0, 72.0
# Alaska, crudely: the 141st meridian, then the panhandle border by its well-known points, then out to sea.
AK = Polygon([(-180, 49), (-180, 73), (-141, 73), (-141, 60.3), (-139.2, 60.1), (-139.05, 60.35), (-138.7, 59.9),
              (-137.6, 59.25), (-137.45, 58.91), (-136.45, 59.45), (-135.1, 59.63), (-134.95, 59.28), (-134.0, 58.85),
              (-133.4, 58.4), (-133.15, 58.0), (-132.25, 57.2), (-131.85, 56.7), (-131.1, 56.4), (-130.1, 56.1),
              (-130.02, 55.91), (-130.2, 55.3), (-130.65, 54.72), (-133.0, 54.7), (-134.0, 54.3), (-134.0, 49)])

def grid_at(path, lon, lat, wrap):
    with rasterio.open(path) as s:
        a = s.read(1).astype(np.float64); T = s.transform
        if s.nodata is not None: a[a == s.nodata] = np.nan
    lo = np.where(wrap & (lon < 0), lon + 360.0, lon)
    c = (lo - T.c) / T.a - 0.5; r = (lat - T.f) / T.e - 0.5
    v = map_coordinates(np.nan_to_num(a, nan=0.0), [r, c], order=1, mode="nearest")
    ok = (c >= 0) & (c <= a.shape[1] - 1) & (r >= 0) & (r <= a.shape[0] - 1)
    return np.where(ok, v, np.nan)

def dh_itrf(lon, lat):
    txt = "".join(f"{la:.6f} {lo:.6f} 0\n" for lo, la in zip(lon, lat))
    out = subprocess.run([CS2CS, "-f", "%.4f", "EPSG:7912", "EPSG:6319"], input=txt, capture_output=True, text=True, check=True).stdout
    return np.array([float(l.split()[2]) for l in out.strip().splitlines()])

def correction(lon, lat, dh=None):
    n08 = grid_at(f"{PROJ}/us_nga_egm08_25.tif", lon, lat, False); n12 = grid_at(f"{PROJ}/us_noaa_g2012ba0.tif", lon, lat, True)
    return n08 - n12 + (dh_itrf(lon, lat) if dh is None else dh)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--step", type=float, default=0.25); ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", default="inventory/static/inventory/js/dem_datum_ak.js"); a = ap.parse_args()
    nx = int(round((LON1 - LON0) / a.step)) + 1; ny = int(round((LAT1 - LAT0) / a.step)) + 1
    lon, lat = np.meshgrid(LON0 + np.arange(nx) * a.step, LAT1 - np.arange(ny) * a.step)
    c = correction(lon.ravel(), lat.ravel()).reshape(ny, nx)
    ak = prep(AK); inside = np.array([ak.contains(Point(x, y)) for x, y in zip(lon.ravel(), lat.ravel())]).reshape(ny, nx)
    c[~inside] = np.nan
    q = np.where(np.isfinite(c), np.round(c * 100), -32768).astype("<i2")
    print(f"grid {nx} x {ny} at {a.step} deg; {np.isfinite(c).sum()} cells carry a correction; range {np.nanmin(c):+.2f} .. {np.nanmax(c):+.2f} m, median {np.nanmedian(c):+.2f}")
    if a.check:   # the coarse grid, read bilinearly as the browser does, against the full formula at random points
        rng = np.random.default_rng(1); n = 20000; lo = rng.uniform(-170, -130, n); la = rng.uniform(54, 71, n)
        truth = correction(lo, la); g = np.where(q == -32768, np.nan, q / 100.0)
        fx = (lo - LON0) / a.step; fy = (LAT1 - la) / a.step; x0 = np.floor(fx).astype(int); y0 = np.floor(fy).astype(int); u = fx - x0; v = fy - y0
        est = (g[y0, x0] * (1 - u) + g[y0, x0 + 1] * u) * (1 - v) + (g[y0 + 1, x0] * (1 - u) + g[y0 + 1, x0 + 1] * u) * v
        e = (est - truth)[np.isfinite(est) & np.isfinite(truth)]
        print(f"check, {e.size} points: |error| p50 {np.percentile(abs(e),50):.3f}  p95 {np.percentile(abs(e),95):.3f}  p99 {np.percentile(abs(e),99):.3f}  max {abs(e).max():.3f} m")
        print("Petrof Lake (59.40, -150.80):", np.round(correction(np.array([-150.80]), np.array([59.40])), 3), " Anchorage:", np.round(correction(np.array([-149.9]), np.array([61.2])), 3))
    js = ("/* dem_datum_ak.js -- GENERATED by tools/build_dem_datum_ak.py; do not edit.\n"
          " * Metres to ADD to PGC's ArcticDEM 'Height Orthometric' (EGM2008) to get NAVD88 (GEOID12B), Alaska only.\n"
          " * int16 centimetres, little-endian, row-major from the north-west corner; -32768 = no correction here. */\n"
          f"window.LSDemDatumAK = {{ lon0: {LON0}, lat1: {LAT1}, step: {a.step}, nx: {nx}, ny: {ny},\n  b64: '{base64.b64encode(q.tobytes()).decode()}' }};\n")
    open(a.out, "w").write(js); print(f"wrote {a.out} ({len(js)//1024} KB)")
if __name__ == "__main__":
    sys.exit(main())
