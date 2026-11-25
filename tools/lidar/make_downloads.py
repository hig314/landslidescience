#!/opt/anaconda3/bin/python3
"""Smaller download copies of an archive COG (manifest key "downloads").

    /opt/anaconda3/bin/python3 tools/lidar/make_downloads.py <id> [--force]

For each {"res_m": R, "resampling": "average"} of the survey's "downloads",
writes $LIDAR_BUILD/cog/<id>_<R>m.tif from the archive cog/<id>.tif: the same
CRS and nodata, cells of R metres on the archive's own lattice (R must be a
whole multiple of the archive's cell), each the mean of the valid
full-resolution cells it covers. `-ovr NONE` matters: without it gdalwarp
reads the archive's overviews, whose resampling is whatever the archive was
built with, and the copy is then not the mean of anything in particular.

r2_put.sh / r2_publish_overnight.sh upload cog/<id>_<N>m.tif with the survey;
make_catalog.py lists a copy once it is there; /lidar/ shows it beside
"Download DTM". First used for kbay_topobathy_v1 (2 m -> 8 m), 2026-11-24.
"""
import os, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lidar_manifest  # noqa: E402
import rasterio  # noqa: E402

COG = Path(os.environ.get("LIDAR_COG_OUT",
           Path(os.environ.get("LIDAR_BUILD", "/Volumes/Nunatak/lidar_build")) / "cog"))
GDALWARP = Path(os.environ.get("GDAL_BIN", "/opt/homebrew/bin")) / "gdalwarp"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.exit(__doc__)
    did = args[0]
    ds = {d["id"]: d for d in lidar_manifest.load()["datasets"]}.get(did)
    if ds is None:
        sys.exit(f"{did}: not in the manifest")
    src = COG / f"{did}.tif"
    if not src.exists():
        sys.exit(f"{did}: no archive at {src}")
    with rasterio.open(src) as r:
        b, cell, nodata = r.bounds, r.res[0], r.nodata
    env = {k: v for k, v in os.environ.items() if k not in ("PROJ_LIB", "PROJ_DATA")}
    for dl in ds.get("downloads") or []:
        if not dl.get("res_m"):
            continue                                  # a named companion (the water surface): built elsewhere, not a copy of the archive
        res = dl["res_m"]
        if dl.get("resampling", "average") != "average":
            sys.exit(f"{did}: only 'average' is built here")
        if abs(res / cell - round(res / cell)) > 1e-9 or res <= cell:
            sys.exit(f"{did}: {res} m is not a whole multiple of the archive's {cell} m")
        if any(abs((v / res) - round(v / res)) > 1e-9 for v in (b.right - b.left, b.top - b.bottom)):
            sys.exit(f"{did}: the archive's extent is not a whole number of {res} m cells")
        dst = COG / f"{did}_{res:g}m.tif"
        if dst.exists() and "--force" not in sys.argv:
            print(f"{dst.name}: exists (--force to rebuild)")
            continue
        tmp = dst.with_name(dst.stem + ".part.tif")
        subprocess.run([str(GDALWARP), "-q", "-overwrite", "-ovr", "NONE", "-tr", str(res), str(res),
                        "-te", str(b.left), str(b.bottom), str(b.right), str(b.top), "-r", "average",
                        "-srcnodata", str(nodata), "-dstnodata", str(nodata), "-wm", "1024", "-multi",
                        "-of", "COG", "-co", "COMPRESS=DEFLATE", "-co", "LEVEL=9", "-co", "PREDICTOR=3", "-co", "BLOCKSIZE=512",
                        "-co", "OVERVIEW_RESAMPLING=AVERAGE", "-co", "NUM_THREADS=4", str(src), str(tmp)],
                       check=True, env=env)
        tmp.rename(dst)
        print(f"{dst.name}: {dst.stat().st_size / 2**20:.0f} MB")


if __name__ == "__main__":
    main()
