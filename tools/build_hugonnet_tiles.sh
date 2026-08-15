#!/usr/bin/env bash
#
# build_hugonnet_tiles.sh — XYZ tile pyramid for the Hugonnet et al. (2021)
# glacier elevation-change (dh/dt) rasters, full period 2000-01-01→2020-01-01.
#
# Source tiles (100 m, Float32 m/yr, NoData -9999, per-tile UTM zones,
# glaciers + 10 km buffer only) live in data/hugonnet_src/dhdt/ (NOT in git).
# Download once from Theia/SEDOO (doi:10.6096/13, CC BY 4.0, no auth):
#   per-tile:  https://api.sedoo.fr/sedoo-glaciers-rest/data/v1_0/downloadtif/2000-2020/{TILE}
#   bulk tar:  .../prepare/01_02_rgi60/2000-01-01_2020-01-01 → check → download
# (278 tiles for RGI regions 01+02; the AK clip window below trims the rest.)
#
# gdalwarp mosaics all per-UTM-zone inputs straight into one EPSG:3857 raster
# (it reprojects each source from its own SRS). Diverging ramp: red = thinning,
# blue = thickening, |dh/dt| < 0.25 m/yr transparent (below typical noise;
# matches the ITS_LIVE trend layer's dead-band convention).
#
# Output: data/hugonnet_tiles/dhdt/{z}/{x}/{y}.png, served at
# /tiles/hugonnet/dhdt/. Deploy = rsync data/hugonnet_tiles/ (like susc).
#
# Attribution: Hugonnet et al. 2021, Nature 592, 726–731,
# doi:10.1038/s41586-021-03436-z; dataset doi:10.6096/13 (CC BY 4.0).
#
# Usage:  tools/build_hugonnet_tiles.sh [max_zoom]
set -euo pipefail

GDAL_BIN="${GDAL_BIN:-/Applications/QGIS-LTR.app/Contents/MacOS/bin}"
WARP="$GDAL_BIN/gdalwarp"
DEM="$GDAL_BIN/gdaldem"
TILES_PY="$GDAL_BIN/gdal2tiles.py"
PYTHON="$GDAL_BIN/python3"
ROOT_SH="$(cd "$(dirname "$0")" && pwd)"

export PROJ_LIB="${PROJ_LIB:-/Applications/QGIS-LTR.app/Contents/Resources/proj}"
export GDAL_PAM_ENABLED=NO

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$ROOT/data/hugonnet_src/dhdt"
OUT_DIR="$ROOT/data/hugonnet_tiles"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

MAXZOOM="${1:-10}"   # native res 100 m ≈ web-mercator z10 at AK latitudes
MINZOOM=3
PROCESSES="${PROCESSES:-1}"
KEEP_TMP="${KEEP_TMP:-}"
[ -n "$KEEP_TMP" ] && { mkdir -p "$KEEP_TMP"; TMP_DIR="$KEEP_TMP"; trap - EXIT; }

# Same AK clip window as the susc / ITS_LIVE builds.
CLIP_TE="${CLIP_TE:--179.99 50 -125 72}"

key=dhdt
echo "=== $key: $(ls "$SRC_DIR"/*.tif | wc -l | tr -d ' ') source tiles ==="

if [ -f "$TMP_DIR/${key}_3857.tif" ]; then
  echo "  [1/3] warp — reusing $TMP_DIR/${key}_3857.tif"
else
  echo "  [1/3] warp per-UTM tiles -> one 3857 mosaic (bilinear), clip to AK $CLIP_TE"
  # 100 m target res in 3857 at ~60°N ≈ 200 m ground-truth-ish; let gdalwarp
  # pick the res from the first source scaled to 3857 — explicit -tr keeps the
  # mosaic consistent across UTM zones instead.
  "$WARP" -q -overwrite \
    -t_srs EPSG:3857 -r bilinear -tr 100 100 \
    -te $CLIP_TE -te_srs EPSG:4326 \
    -srcnodata -9999 -dstnodata -9999 \
    -wm 1024 -multi \
    -co TILED=YES -co COMPRESS=LZW -co BIGTIFF=YES \
    "$SRC_DIR"/*.tif "$TMP_DIR/${key}_3857.tif"
fi

cf="$ROOT_SH/hugonnet_color_dhdt.txt"
echo "  [2/3] color-relief via $(basename "$cf") (RGBA; NoData -> transparent)"
"$DEM" color-relief "$TMP_DIR/${key}_3857.tif" "$cf" \
  "$TMP_DIR/${key}_color.tif" -alpha -co COMPRESS=LZW -q

echo "  [3/3] tile z$MINZOOM-$MAXZOOM (XYZ)"
for _ in 1 2 3; do rm -rf "$OUT_DIR/$key" 2>/dev/null && break; sleep 1; done
mkdir -p "$OUT_DIR/$key"
"$PYTHON" "$TILES_PY" --xyz -p mercator -r near --no-kml \
  -z "$MINZOOM-$MAXZOOM" --processes="$PROCESSES" \
  -w none \
  "$TMP_DIR/${key}_color.tif" "$OUT_DIR/$key"

echo "  [prune] removing fully-transparent tiles"
"$PYTHON" - "$OUT_DIR/$key" <<'PY'
import sys, os
from osgeo import gdal
gdal.UseExceptions()
root = sys.argv[1]
removed = kept = 0
for dirpath, _, files in os.walk(root):
    for fn in files:
        if not fn.endswith('.png'):
            continue
        p = os.path.join(dirpath, fn)
        ds = gdal.Open(p)
        a = ds.GetRasterBand(ds.RasterCount).GetMaximum()
        if a is None:
            a = ds.GetRasterBand(ds.RasterCount).ComputeRasterMinMax(False)[1]
        ds = None
        if not a:
            os.remove(p); removed += 1
        else:
            kept += 1
print(f"    pruned {removed} empty tiles, kept {kept}")
PY
find "$OUT_DIR/$key" -type d -empty -delete

n=$(find "$OUT_DIR/$key" -name '*.png' | wc -l | tr -d ' ')
echo "  done: $n tiles -> $OUT_DIR/$key  ($(du -sh "$OUT_DIR/$key" | cut -f1))"
echo "All done."
