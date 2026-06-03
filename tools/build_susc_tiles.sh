#!/usr/bin/env bash
#
# build_susc_tiles.sh — generate self-hosted, recolorable XYZ tile pyramids for
# the USGS Belair et al. (2024) landslide-susceptibility rasters (Alaska).
#
# Source rasters (90 m, EPSG:3338, single-band Int32, value 0-81 = count of
# 10 m susceptible sub-cells per 90 m cell, NoData = 2147483647) live under
# data/usgs_susceptibility/{lw_susc,n10_susc}/. They are NOT in git (data/ is
# gitignored); download them once and unzip the AK tifs.
#
# Output: data/susc_tiles/{lw,n10}/{z}/{x}/{y}.png  — pre-colored RGBA tiles.
# Color is baked at tile-gen time by `gdaldem color-relief` from the editable
# ramp file tools/susc_color.txt; NoData (ocean / no data) -> transparent.
#
# Why pre-colored (not value-tiles recolored in the browser): MapLibre GL JS
# (5.5) does NOT implement Mapbox's `raster-color`/`raster-value` paint
# properties, so client-side recoloring of a single-band raster is not possible
# without a custom WebGL layer. To RECOLOR: edit tools/susc_color.txt and re-run
# this script — the only cost is re-tiling (a few minutes).
#
# Run locally (the web container has no GDAL). Point GDAL_BIN at a GDAL that has
# gdal2tiles.py with --xyz (GDAL >= 3.1). Defaults to the QGIS-LTR bundle.
#
# Usage:  tools/build_susc_tiles.sh [lw|n10|all] [max_zoom]
set -euo pipefail

GDAL_BIN="${GDAL_BIN:-/Applications/QGIS-LTR.app/Contents/MacOS/bin}"
WARP="$GDAL_BIN/gdalwarp"
DEM="$GDAL_BIN/gdaldem"
TILES_PY="$GDAL_BIN/gdal2tiles.py"
PYTHON="$GDAL_BIN/python3"
ROOT_SH="$(cd "$(dirname "$0")" && pwd)"
COLOR_FILE="${COLOR_FILE:-$ROOT_SH/susc_color.txt}"

# PROJ data — without this the QGIS-bundled GDAL can't find proj.db and
# mis-georeferences the XYZ tiles.
export PROJ_LIB="${PROJ_LIB:-/Applications/QGIS-LTR.app/Contents/Resources/proj}"
# Don't scatter PAM .aux.xml sidecars next to every tile during the prune scan.
export GDAL_PAM_ENABLED=NO

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$ROOT/data/usgs_susceptibility"
OUT_DIR="$ROOT/data/susc_tiles"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

WHICH="${1:-all}"
MAXZOOM="${2:-10}"   # native res ~90 m ≈ web-mercator z10 at AK latitudes
MINZOOM=3
# gdal2tiles --processes>1 crashes under this GDAL's py3.9 spawn pool
# (module '__main__' has no attribute '__spec__'); single-process is reliable.
PROCESSES="${PROCESSES:-1}"
# Reuse the warped/byte intermediates if present (set KEEP_TMP=dir). Speeds up
# re-tiling at a different zoom without re-warping the full raster.
KEEP_TMP="${KEEP_TMP:-}"
[ -n "$KEEP_TMP" ] && { mkdir -p "$KEEP_TMP"; TMP_DIR="$KEEP_TMP"; trap - EXIT; }

# Clip the warp to an Alaska window (lon/lat). The source covers AK incl. the
# Aleutians, which straddle the antimeridian; reprojecting to EPSG:3857 without
# a clip yields a globe-WIDTH canvas (one seam at +/-180) and thousands of empty
# tiles. Clipping to the western-hemisphere AK window keeps mainland + SE AK +
# the eastern/central Aleutians (the entire landslide-inventory area) and drops
# only the handful of far-western Aleutian islands east of the dateline.
CLIP_TE="${CLIP_TE:--179.99 50 -125 72}"

build_one () {
  local key="$1" src="$2"
  echo "=== $key: $src ==="
  [ -f "$src" ] || { echo "  MISSING source: $src — unzip the AK tif first. Skipping."; return; }

  if [ -f "$TMP_DIR/${key}_3857.tif" ]; then
    echo "  [1/3] warp — reusing $TMP_DIR/${key}_3857.tif"
  else
    echo "  [1/3] warp 3338 -> 3857 (NoData preserved), clip to AK $CLIP_TE"
    "$WARP" -q -overwrite \
      -t_srs EPSG:3857 -r near \
      -te $CLIP_TE -te_srs EPSG:4326 \
      -srcnodata 2147483647 -dstnodata 2147483647 \
      -wm 1024 -multi \
      -co TILED=YES -co COMPRESS=LZW \
      "$src" "$TMP_DIR/${key}_3857.tif"
  fi

  # Per-model color file (tools/susc_color_<key>.txt) if present, else the
  # shared COLOR_FILE. Lets n10 and lw use different class breaks.
  local cf="$ROOT_SH/susc_color_${key}.txt"
  [ -f "$cf" ] || cf="$COLOR_FILE"
  echo "  [2/3] color-relief via $(basename "$cf") (RGBA; NoData -> transparent)"
  "$DEM" color-relief "$TMP_DIR/${key}_3857.tif" "$cf" \
    "$TMP_DIR/${key}_color.tif" -alpha -co COMPRESS=LZW -q

  echo "  [3/3] tile z$MINZOOM-$MAXZOOM (XYZ)"
  # rm -rf occasionally trips "Directory not empty" on macOS when Spotlight is
  # mid-index of a freshly-written pyramid; retry a couple of times.
  for _ in 1 2 3; do rm -rf "$OUT_DIR/$key" 2>/dev/null && break; sleep 1; done
  mkdir -p "$OUT_DIR/$key"
  "$PYTHON" "$TILES_PY" --xyz -p mercator -r near --no-kml \
    -z "$MINZOOM-$MAXZOOM" --processes="$PROCESSES" \
    -w none \
    "$TMP_DIR/${key}_color.tif" "$OUT_DIR/$key"

  echo "  [prune] removing fully-transparent (ocean / no-data) tiles"
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
        # alpha is the last band of the RGBA PNG; if it's all zero the tile is empty
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
  # drop now-empty zoom/column dirs left behind by pruning
  find "$OUT_DIR/$key" -type d -empty -delete

  local n
  n=$(find "$OUT_DIR/$key" -name '*.png' | wc -l | tr -d ' ')
  echo "  done: $n tiles -> $OUT_DIR/$key  ($(du -sh "$OUT_DIR/$key" | cut -f1))"
}

case "$WHICH" in
  lw)  build_one lw  "$SRC_DIR/lw_susc/lw_ak.tif" ;;
  n10) build_one n10 "$SRC_DIR/n10_susc/n10_ak.tif" ;;
  all) build_one lw  "$SRC_DIR/lw_susc/lw_ak.tif"
       build_one n10 "$SRC_DIR/n10_susc/n10_ak.tif" ;;
  *)   echo "usage: $0 [lw|n10|all] [max_zoom]"; exit 1 ;;
esac
echo "All done."
