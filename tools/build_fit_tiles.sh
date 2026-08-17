#!/usr/bin/env bash
#
# build_fit_tiles.sh — tile the robust per-cell fit (tools/fit_pair_rasters.py)
# so it can be compared, pixel for pixel, against the standard ITS_LIVE
# rasters inside the map.
#
# Deliberately reuses the SAME colour ramps as the ITS_LIVE overlays
# (itslive_color_v / _vamp / _dvdt), so any visual difference between our
# layer and theirs is a DATA difference and never a palette difference.
#
# Input:  data/glaciers/experiments/columbia_pairs_fit.npz  (v0, amp, trend)
# Output: data/glaciers/fit_tiles/{v0,amp,trend}/{z}/{x}/{y}.png
#         served at /tiles/glacierfit/<var>/
#
# The fitted box is small (~21 x 18 km at 120 m), so this tiles to z13 —
# higher than the regional pyramids, because the whole point is to look
# closely at individual cells.
#
# Usage:  tools/build_fit_tiles.sh [v0|amp|trend|all] [max_zoom]
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
NPZ="$ROOT/data/glaciers/experiments/columbia_pairs_fit.npz"
OUT_DIR="$ROOT/data/glaciers/fit_tiles"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

WHICH="${1:-all}"
MAXZOOM="${2:-13}"
MINZOOM=6

[ -f "$NPZ" ] || { echo "missing $NPZ — run tools/fit_pair_rasters.py first"; exit 1; }

# npz -> per-variable GeoTIFF in EPSG:3413 (the fit's native grid).
echo "=== unpacking $NPZ"
"$PYTHON" - "$NPZ" "$TMP_DIR" <<'PY'
import json, sys
import numpy as np
from osgeo import gdal, osr
gdal.UseExceptions()
npz, tmp = sys.argv[1], sys.argv[2]
d = np.load(npz, allow_pickle=True)
g = json.loads(str(d['grid']))
srs = osr.SpatialReference(); srs.ImportFromEPSG(3413)
drv = gdal.GetDriverByName('GTiff')
for k in ('v0', 'amp', 'trend'):
    a = d[k].astype('float32')
    ds = drv.Create(f'{tmp}/{k}.tif', g['nx'], g['ny'], 1, gdal.GDT_Float32,
                    options=['COMPRESS=DEFLATE', 'TILED=YES'])
    ds.SetGeoTransform((g['x0'], g['dx'], 0, g['y0_north'], 0, -g['dx']))
    ds.SetProjection(srs.ExportToWkt())
    b = ds.GetRasterBand(1); b.SetNoDataValue(-9999.0)
    a = np.where(np.isfinite(a), a, -9999.0)
    b.WriteArray(a); ds.FlushCache()
    ok = int((a != -9999.0).sum())
    print(f'   {k}: {ok} valid cells')
PY

build_one () {
  local key="$1" ramp="$2"
  local src="$TMP_DIR/${key}.tif"
  echo "=== $key (ramp: $ramp)"
  "$WARP" -q -overwrite -t_srs EPSG:3857 -r near \
    -srcnodata -9999 -dstnodata -9999 \
    -co TILED=YES -co COMPRESS=LZW \
    "$src" "$TMP_DIR/${key}_3857.tif"
  "$DEM" color-relief "$TMP_DIR/${key}_3857.tif" "$ROOT_SH/$ramp" \
    "$TMP_DIR/${key}_color.tif" -alpha -co COMPRESS=LZW -q
  for _ in 1 2 3; do rm -rf "$OUT_DIR/$key" 2>/dev/null && break; sleep 1; done
  mkdir -p "$OUT_DIR/$key"
  "$PYTHON" "$TILES_PY" --xyz -p mercator -r near --no-kml \
    -z "$MINZOOM-$MAXZOOM" --processes=1 -w none \
    "$TMP_DIR/${key}_color.tif" "$OUT_DIR/$key" >/dev/null
  "$PYTHON" - "$OUT_DIR/$key" <<'PY'
import sys, os
from osgeo import gdal
gdal.UseExceptions()
root = sys.argv[1]; removed = kept = 0
for dp, _, fs in os.walk(root):
    for fn in fs:
        if not fn.endswith('.png'): continue
        p = os.path.join(dp, fn)
        ds = gdal.Open(p)
        a = ds.GetRasterBand(ds.RasterCount).GetMaximum()
        if a is None: a = ds.GetRasterBand(ds.RasterCount).ComputeRasterMinMax(False)[1]
        ds = None
        if not a: os.remove(p); removed += 1
        else: kept += 1
print(f'   pruned {removed}, kept {kept}')
PY
  find "$OUT_DIR/$key" -type d -empty -delete
  echo "   -> $(find "$OUT_DIR/$key" -name '*.png' | wc -l | tr -d ' ') tiles, $(du -sh "$OUT_DIR/$key" | cut -f1)"
}

case "$WHICH" in
  v0)    build_one v0    itslive_color_v.txt ;;
  amp)   build_one amp   itslive_color_vamp.txt ;;
  trend) build_one trend itslive_color_dvdt.txt ;;
  all)   build_one v0    itslive_color_v.txt
         build_one amp   itslive_color_vamp.txt
         build_one trend itslive_color_dvdt.txt ;;
  *) echo "usage: $0 [v0|amp|trend|all] [max_zoom]"; exit 1 ;;
esac
echo "All done."
