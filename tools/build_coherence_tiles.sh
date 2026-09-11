#!/usr/bin/env bash
#
# build_coherence_tiles.sh — XYZ tile pyramid for Sentinel-1 seasonal
# interferometric coherence over Alaska.
#
# Source: the Sentinel-1 Global Coherence Dataset (Kellndorfer et al. 2022),
# free and unauthenticated on AWS Open Data. Fetch first:
#   tools/fetch_coherence.py --season summer --var COH12
# which lands 1x1 degree EPSG:4326 uint8 tiles (PERCENT coherence, nodata 0,
# ~93 m) in data/coherence_src/<season>_<pol>_<VAR>/.
#
# Why a local bake rather than serving the sources: they are not true COGs —
# no overviews, blocked in 6-row strips — so range reads are slow, and there
# are 666 of them per season. Same treatment as susc / ITS_LIVE / Hugonnet /
# IceBoost, and the same deploy story (rsync data/coherence_tiles/).
#
# Output: data/coherence_tiles/<season>/{z}/{x}/{y}.png, served at
# /tiles/coherence/<season>/.
#
# Attribution: Kellndorfer, J. et al. (2022), Global seasonal Sentinel-1
# interferometric coherence and backscatter data set, Scientific Data 9, 73,
# doi:10.1038/s41597-022-01189-6. CC BY 4.0.
#
# Usage:  tools/build_coherence_tiles.sh [season] [max_zoom]
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
SEASON="${1:-summer}"
MAXZOOM="${2:-10}"      # 93 m native ~ web-mercator z10 at AK latitudes
MINZOOM=3
POL="${POL:-vv}"
VAR="${VAR:-COH12}"
# 1, not more: gdal2tiles' multiprocessing path dies under QGIS's bundled
# Python with "module '__main__' has no attribute '__spec__'". The other
# build scripts here default to 1 for the same reason.
PROCESSES="${PROCESSES:-1}"

SRC_DIR="$ROOT/data/coherence_src/${SEASON}_${POL}_${VAR}"
OUT_DIR="$ROOT/data/coherence_tiles/$SEASON"
CF="$ROOT_SH/coherence_color_coh.txt"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
KEEP_TMP="${KEEP_TMP:-}"
[ -n "$KEEP_TMP" ] && { mkdir -p "$KEEP_TMP"; TMP_DIR="$KEEP_TMP"; trap - EXIT; }

[ -d "$SRC_DIR" ] || { echo "no sources at $SRC_DIR — run tools/fetch_coherence.py first"; exit 1; }
N=$(ls "$SRC_DIR"/*.tif 2>/dev/null | wc -l | tr -d ' ')
[ "$N" -gt 0 ] || { echo "no .tif in $SRC_DIR"; exit 1; }
echo "=== coherence $SEASON $POL $VAR: $N source tiles ==="

# Same AK clip window as the other statewide builds.
CLIP_TE="${CLIP_TE:--179.99 50 -125 72}"

if [ -f "$TMP_DIR/${SEASON}_${POL}_${VAR}_3857.tif" ]; then
  echo "  [1-2/4] warp — reusing $TMP_DIR/${SEASON}_${POL}_${VAR}_3857.tif (KEEP_TMP set)"
else
echo "  [1/4] build VRT over the 1-degree tiles"
"$GDAL_BIN/gdalbuildvrt" -q -srcnodata 0 -vrtnodata 0 \
  "$TMP_DIR/${SEASON}_${POL}_${VAR}.vrt" "$SRC_DIR"/*.tif

echo "  [2/4] warp 4326 -> 3857 (bilinear), clip to AK $CLIP_TE"
# -tr in metres: 93 m native at ~61N. Bilinear is right here: coherence is a
# continuous quantity, not a class code, so smoothing between cells is honest.
"$WARP" -q -overwrite \
  -t_srs EPSG:3857 -r bilinear -tr 93 93 \
  -te $CLIP_TE -te_srs EPSG:4326 \
  -srcnodata 0 -dstnodata 0 \
  -wm 1024 -multi \
  -co TILED=YES -co COMPRESS=LZW -co BIGTIFF=YES \
  "$TMP_DIR/${SEASON}_${POL}_${VAR}.vrt" "$TMP_DIR/${SEASON}_${POL}_${VAR}_3857.tif"
fi

echo "  [3/4] color-relief via $(basename "$CF") (RGBA; nodata + <20% -> transparent)"
"$DEM" color-relief "$TMP_DIR/${SEASON}_${POL}_${VAR}_3857.tif" "$CF" \
  "$TMP_DIR/${SEASON}_${POL}_${VAR}_color.tif" -alpha -co COMPRESS=LZW -q

echo "  [4/4] tile z$MINZOOM-$MAXZOOM (XYZ)"
for _ in 1 2 3; do rm -rf "$OUT_DIR" 2>/dev/null && break; sleep 1; done
mkdir -p "$OUT_DIR"
"$PYTHON" "$TILES_PY" --xyz -p mercator -r near --no-kml \
  -z "$MINZOOM-$MAXZOOM" --processes="$PROCESSES" -w none \
  "$TMP_DIR/${SEASON}_${POL}_${VAR}_color.tif" "$OUT_DIR"

echo "  [prune] removing fully-transparent tiles"
"$PYTHON" - "$OUT_DIR" <<'PY'
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
find "$OUT_DIR" -type d -empty -delete
echo "  done: $(find "$OUT_DIR" -name '*.png' | wc -l | tr -d ' ') tiles -> $OUT_DIR ($(du -sh "$OUT_DIR" | cut -f1))"
