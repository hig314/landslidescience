#!/opt/anaconda3/bin/python3
"""ArcticDEM mosaic v4.1 (2 m) -> one NAVD88 GeoTIFF on the composite grid, real data only.

    arcticdem_mosaic.py TILE_DIR OUT_DIR X0 Y0 X1 Y1 [--epsg 6334] [--res 2] [--itrf-shift -0.45]

Steps (each skipped when its output exists, so a crashed run resumes):
  1. dem.vrt / mask.vrt over TILE_DIR/*_dem.tif and *_datamask.tif (EPSG:3413, WGS84 ellipsoid heights).
  2. gdalwarp both onto the target grid: the DEM bilinear (Float32, nodata -9999), the datamask nearest (Byte).
  3. GEOID12B Alaska (PROJ's us_noaa_g2012ba0.tif) bilinear onto the same grid at 50 m.
  4. Windowed combine: out = dem + itrf_shift - N where datamask == 1, else nodata.
       - datamask 1 = a cell with real stereo data; 0 = PGC's fill (the mosaic interpolates through water and every
         gap, so an unmasked ArcticDEM puts a smooth false surface over lakes and the sea -- Hig, 2026-10-31).
       - itrf_shift: ArcticDEM heights are ITRF/WGS84 ellipsoidal; GEOID12B is defined on NAD83(2011). cs2cs
         EPSG:7912 -> EPSG:6319 at Homer gives h_NAD83 = h_ITRF - 0.451 m (varies by a few cm over the bay).
       - N: GEOID12B height, 9.1-11.5 m across Kachemak Bay, so a constant would be 2 m wrong at one end.
     Residual datum error (ITRF epoch, GEOID12B vs the lidar's geoid) is left to joint.py's derived_adjust
     calibration against the lidar it overlaps.
  5. Overviews (average).

Output: OUT_DIR/arcticdem_v41_navd88_<res>m.tif (tiled LZW, predictor 3, BigTIFF).
"""
import argparse, glob, os, subprocess, sys, time

GDAL = "/opt/homebrew/bin"
GEOID = "/opt/homebrew/share/proj/us_noaa_g2012ba0.tif"


def stamp(msg):
    print(f"[{time.strftime('%F %T')}] {msg}", flush=True)


def run(cmd):
    stamp(" ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tiles"); ap.add_argument("out")
    ap.add_argument("x0", type=float); ap.add_argument("y0", type=float); ap.add_argument("x1", type=float); ap.add_argument("y1", type=float)
    ap.add_argument("--epsg", type=int, default=6334); ap.add_argument("--res", type=float, default=2.0)
    ap.add_argument("--itrf-shift", type=float, default=-0.451, help="h_NAD83(2011) - h_ITRF2014 (m)")
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    te = [a.x0, a.y0, a.x1, a.y1]
    dem_vrt, mask_vrt = os.path.join(a.out, "dem_3413.vrt"), os.path.join(a.out, "mask_3413.vrt")
    dem_ell = os.path.join(a.out, f"dem_ell_{a.epsg}_{a.res:g}m.tif")
    mask = os.path.join(a.out, f"datamask_{a.epsg}_{a.res:g}m.tif")
    geoid = os.path.join(a.out, f"geoid12b_{a.epsg}_50m.tif")
    final = os.path.join(a.out, f"arcticdem_v41_navd88_{a.res:g}m.tif")

    dems = sorted(glob.glob(os.path.join(a.tiles, "*_dem.tif")))
    masks = sorted(glob.glob(os.path.join(a.tiles, "*_datamask.tif")))
    if not dems or len(dems) != len(masks):
        sys.exit(f"{len(dems)} dem tiles vs {len(masks)} datamask tiles in {a.tiles}")
    stamp(f"{len(dems)} tiles; target EPSG:{a.epsg} {a.res:g} m, extent {te}")

    if not os.path.exists(dem_vrt):
        run([f"{GDAL}/gdalbuildvrt", "-q", dem_vrt] + dems)
    if not os.path.exists(mask_vrt):
        run([f"{GDAL}/gdalbuildvrt", "-q", "-srcnodata", "None", mask_vrt] + masks)   # 0 is a value (fill), not nodata
    common = ["-q", "-t_srs", f"EPSG:{a.epsg}", "-te", *te, "-tr", a.res, a.res, "-multi", "-wo", f"NUM_THREADS={a.threads}", "-wm", "1024",
              "-co", "TILED=YES", "-co", "BIGTIFF=YES", "-co", "BLOCKXSIZE=512", "-co", "BLOCKYSIZE=512"]
    if not os.path.exists(dem_ell):
        run([f"{GDAL}/gdalwarp", *common, "-r", "bilinear", "-ot", "Float32", "-srcnodata", "-9999", "-dstnodata", "-9999",
             "-co", "COMPRESS=LZW", "-co", "PREDICTOR=3", dem_vrt, dem_ell + ".part.tif"])
        os.replace(dem_ell + ".part.tif", dem_ell)
    if not os.path.exists(mask):
        run([f"{GDAL}/gdalwarp", *common, "-r", "near", "-ot", "Byte", "-dstnodata", "255", "-co", "COMPRESS=DEFLATE", mask_vrt, mask + ".part.tif"])
        os.replace(mask + ".part.tif", mask)
    if not os.path.exists(geoid):
        run([f"{GDAL}/gdalwarp", "-q", "-overwrite", "-t_srs", f"EPSG:{a.epsg}", "-te", *te, "-tr", 50, 50, "-r", "bilinear", "-ot", "Float32",
             "-co", "COMPRESS=LZW", GEOID, geoid])

    if not os.path.exists(final):
        import numpy as np, rasterio
        from rasterio.enums import Resampling
        from rasterio.windows import Window
        stamp("combine: dem + itrf_shift - geoid where datamask == 1")
        with rasterio.open(dem_ell) as D, rasterio.open(mask) as M, rasterio.open(geoid) as G:
            prof = D.profile.copy()
            prof.update(driver="GTiff", dtype="float32", nodata=-9999.0, tiled=True, blockxsize=512, blockysize=512,
                        compress="lzw", predictor=3, bigtiff="YES", count=1)
            gf = 50.0 / a.res   # geoid cells per output cell
            n_real = n_all = 0
            with rasterio.open(final + ".part.tif", "w", **prof) as O:
                step = 4096
                for r0 in range(0, D.height, step):
                    h = min(step, D.height - r0)
                    for c0 in range(0, D.width, step):
                        w = min(step, D.width - c0)
                        win = Window(c0, r0, w, h)
                        z = D.read(1, window=win)
                        m = M.read(1, window=win)
                        gw = Window(c0 / gf, r0 / gf, w / gf, h / gf)
                        N = G.read(1, window=gw, out_shape=(h, w), resampling=Resampling.bilinear, boundless=True, fill_value=np.nan)
                        keep = (m == 1) & (z > -9000) & np.isfinite(N)
                        out = np.full((h, w), -9999.0, np.float32)
                        out[keep] = (z[keep] + a.itrf_shift - N[keep]).astype(np.float32)
                        O.write(out, 1, window=win)
                        n_real += int(keep.sum()); n_all += h * w
                    stamp(f"  rows {r0 + h}/{D.height}  real-data so far {n_real / max(n_all, 1):.1%}")
        os.replace(final + ".part.tif", final)
        stamp(f"real data on {n_real * a.res * a.res / 1e6:.1f} km2 of {n_all * a.res * a.res / 1e6:.0f} km2")
    run([f"{GDAL}/gdaladdo", "-q", "-r", "average", final, "4", "16", "64", "256"])
    stamp(f"done: {final}")


if __name__ == "__main__":
    main()
