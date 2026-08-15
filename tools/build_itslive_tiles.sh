#!/usr/bin/env bash
#
# build_itslive_tiles.sh — generate self-hosted XYZ tile pyramids for the
# NASA MEaSUREs ITS_LIVE v2 glacier-velocity composite (Alaska, RGI01A).
#
# Source COGs (120 m, EPSG:3413) live in data/itslive_src/ (NOT in git —
# data/ is gitignored). Download once from the public CC0 S3 bucket:
#   https://its-live-data.s3.amazonaws.com/velocity_mosaic/v2/static/cog/
#     ITS_LIVE_velocity_120m_RGI01A_0000_v02_v.tif       (speed, Float32 m/yr)
#     ITS_LIVE_velocity_120m_RGI01A_0000_v02_v_amp.tif   (seasonal amplitude,
#                                                         UInt16 m/yr)
#     ITS_LIVE_velocity_120m_RGI01A_0000_v02_dv_dt.tif   (trend, Float32 m/yr²)
#
# Output: data/itslive_tiles/{v,vamp,dvdt}/{z}/{x}/{y}.png — pre-colored RGBA
# tiles (color baked by `gdaldem color-relief` from tools/itslive_color_*.txt;
# MapLibre can't recolor single-band rasters client-side — same constraint as
# the susceptibility tiles). To recolor: edit the ramp, re-run this script.
#
# Ramps: speed and amplitude use log-spaced breakpoints (values span five
# decades); trend is diverging with a transparent dead-band near zero so the
# layer highlights genuine acceleration/deceleration only.
#
# Run locally (the web container has no GDAL); same GDAL_BIN convention as
# build_susc_tiles.sh. Deploy = rsync data/itslive_tiles/ to the droplet
# (like susc tiles); the /tiles/itslive/ route serves them.
#
# Attribution: velocity data generated using auto-RIFT (Gardner et al., 2018)
# and provided by the NASA MEaSUREs ITS_LIVE project. CC0.
#
# Usage:  tools/build_itslive_tiles.sh [v|vamp|dvdt|all] [max_zoom]
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
SRC_DIR="$ROOT/data/itslive_src"
OUT_DIR="$ROOT/data/itslive_tiles"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

WHICH="${1:-all}"
MAXZOOM="${2:-10}"   # native res 120 m ≈ web-mercator z10 at AK latitudes
MINZOOM=3
PROCESSES="${PROCESSES:-1}"   # >1 crashes under this GDAL's py3.9 spawn pool
KEEP_TMP="${KEEP_TMP:-}"
[ -n "$KEEP_TMP" ] && { mkdir -p "$KEEP_TMP"; TMP_DIR="$KEEP_TMP"; trap - EXIT; }

# Same AK clip window as the susc build: keeps mainland + SE AK + the
# eastern/central Aleutians, avoids the antimeridian globe-width canvas.
CLIP_TE="${CLIP_TE:--179.99 50 -125 72}"

SRC_PREFIX="ITS_LIVE_velocity_120m_RGI01A_0000_v02"

build_one () {
  local key="$1" var="$2" nodata="$3"
  local src="$SRC_DIR/${SRC_PREFIX}_${var}.tif"
  echo "=== $key: $src ==="
  [ -f "$src" ] || { echo "  MISSING source: $src — download the COG first. Skipping."; return; }

  if [ -f "$TMP_DIR/${key}_3857.tif" ]; then
    echo "  [1/3] warp — reusing $TMP_DIR/${key}_3857.tif"
  else
    echo "  [1/3] warp 3413 -> 3857 (bilinear, NoData preserved), clip to AK $CLIP_TE"
    "$WARP" -q -overwrite \
      -t_srs EPSG:3857 -r bilinear \
      -te $CLIP_TE -te_srs EPSG:4326 \
      -srcnodata "$nodata" -dstnodata "$nodata" \
      -wm 1024 -multi \
      -co TILED=YES -co COMPRESS=LZW \
      "$src" "$TMP_DIR/${key}_3857.tif"
  fi

  local cf="$ROOT_SH/itslive_color_${key}.txt"
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

  echo "  [prune] removing fully-transparent (ocean / bare-ground) tiles"
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

  local n
  n=$(find "$OUT_DIR/$key" -name '*.png' | wc -l | tr -d ' ')
  echo "  done: $n tiles -> $OUT_DIR/$key  ($(du -sh "$OUT_DIR/$key" | cut -f1))"
}

# key  source-var  nodata
case "$WHICH" in
  v)    build_one v    v     -32767 ;;
  vamp) build_one vamp v_amp 32767 ;;
  dvdt) build_one dvdt dv_dt -32767 ;;
  all)  build_one v    v     -32767
        build_one vamp v_amp 32767
        build_one dvdt dv_dt -32767 ;;
  *)    echo "usage: $0 [v|vamp|dvdt|all] [max_zoom]"; exit 1 ;;
esac
echo "All done."
