#!/usr/bin/env bash
#
# build_iceboost_tiles.sh — XYZ tile pyramids for the IceBoost v2.0 glacier
# ice-thickness products (Maffezzoli et al. 2025, doi:10.5194/gmd-18-2545-2025;
# data doi:10.5281/zenodo.21220985, CC-BY 4.0).
#
# Inputs are the per-complex derived rasters from prep_iceboost_derived.py
# (data/iceboost/derived/{thickness,bed,overdeep}/, single-band float32,
# nodata -9999, each in its complex's UTM zone). Stage A must run first.
#
# Pipeline per product (hugonnet template, two adaptations):
#   1. Per-UTM-zone VRTs via gdalbuildvrt -input_file_list — ~32k inputs
#      would choke a single gdalwarp argv/open pass; VRTs group same-CRS
#      files cheaply (zone membership read from the raw bounds_index.json).
#   2. gdalwarp the handful of zone VRTs -> one EPSG:3857 mosaic, 100 m,
#      clipped to AK + western-Canada window (RGI-01 + transboundary RGI-02).
#   3. gdaldem color-relief (ramp files tools/iceboost_color_*.txt).
#      BED ONLY: multidirectional hillshade is computed from the bed mosaic
#      and multiplied into the color (python blend step) — the troughs and
#      overdeepenings only read with relief shading.
#   4. gdal2tiles --xyz z3-10 + transparent-tile prune.
#
# Output: data/iceboost_tiles/{thickness,bed,overdeep}/{z}/{x}/{y}.png,
# served at /tiles/iceboost/<product>/. Deploy = rsync data/iceboost_tiles/.
# Bump ICEBOOST_TILE_V in map.js whenever these rebuild with different pixels.
#
# Usage: tools/build_iceboost_tiles.sh [thickness|bed|overdeep|all] [max_zoom]
set -euo pipefail

GDAL_BIN="${GDAL_BIN:-/Applications/QGIS-LTR.app/Contents/MacOS/bin}"
WARP="$GDAL_BIN/gdalwarp"
DEM="$GDAL_BIN/gdaldem"
BUILDVRT="$GDAL_BIN/gdalbuildvrt"
TILES_PY="$GDAL_BIN/gdal2tiles.py"
PYTHON="$GDAL_BIN/python3"
SYS_PYTHON="${SYS_PYTHON:-python3}"   # rasterio/numpy for the blend step
ROOT_SH="$(cd "$(dirname "$0")" && pwd)"

export PROJ_LIB="${PROJ_LIB:-/Applications/QGIS-LTR.app/Contents/Resources/proj}"
export GDAL_PAM_ENABLED=NO

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DRV="$ROOT/data/iceboost/derived"
IDX="$ROOT/data/iceboost/raw/bounds_index.json"
OUT_DIR="$ROOT/data/iceboost_tiles"
TMP_DIR="${KEEP_TMP:-$(mktemp -d)}"
[ -n "${KEEP_TMP:-}" ] && mkdir -p "$TMP_DIR" || trap 'rm -rf "$TMP_DIR"' EXIT

WHICH="${1:-all}"
MAXZOOM="${2:-10}"       # native 100 m ≈ z10 at AK latitudes (hugonnet parity)
MINZOOM=3
# PROCESSES=1 is load-bearing, not a conservative default: gdal2tiles'
# --processes uses multiprocessing spawn, which dies with "module __main__
# has no attribute __spec__" under the QGIS egg-script wrapper. The hugonnet
# template had it right; "improving" it to 4 killed the tiling stage.
PROCESSES="${PROCESSES:-1}"

# RGI-01 window widened east/south for the transboundary + BC/Alberta RGI-02
# complexes (Sierra/Rockies-south excluded — outside the site's scope).
CLIP_TE="${CLIP_TE:--179.99 47 -113 72}"

zone_lists () {  # $1 = product; writes $TMP_DIR/lists/<product>_<epsg>.txt
  "$SYS_PYTHON" - "$1" "$DRV" "$IDX" "$TMP_DIR/lists" <<'PY'
import json, os, sys
product, drv, idxp, outdir = sys.argv[1:5]
os.makedirs(outdir, exist_ok=True)
idx = json.load(open(idxp))
by_zone = {}
for e in idx:
    cid = os.path.splitext(os.path.basename(e['p']))[0]
    p = os.path.join(drv, product, cid + '.tif')
    if os.path.exists(p):
        by_zone.setdefault(e['epsg'], []).append(p)
for epsg, files in sorted(by_zone.items()):
    lp = os.path.join(outdir, f'{product}_{epsg}.txt')
    open(lp, 'w').write('\n'.join(sorted(files)) + '\n')
    print(f'  zone {epsg}: {len(files)} files')
PY
}

mosaic () {  # $1 = product -> $TMP_DIR/<product>_3857.tif
  local key="$1"
  if [ -f "$TMP_DIR/${key}_3857.tif" ]; then
    echo "  [warp] reusing ${key}_3857.tif"; return
  fi
  echo "  [vrt] per-UTM-zone VRTs"
  zone_lists "$key"
  local vrts=()
  for lst in "$TMP_DIR/lists/${key}_"*.txt; do
    local epsg; epsg=$(basename "$lst" .txt); epsg=${epsg##*_}
    "$BUILDVRT" -q -input_file_list "$lst" "$TMP_DIR/${key}_${epsg}.vrt"
    vrts+=("$TMP_DIR/${key}_${epsg}.vrt")
  done
  echo "  [warp] ${#vrts[@]} zone VRTs -> one 3857 mosaic (bilinear, 100 m)"
  "$WARP" -q -overwrite \
    -t_srs EPSG:3857 -r bilinear -tr 100 100 \
    -te $CLIP_TE -te_srs EPSG:4326 \
    -srcnodata -9999 -dstnodata -9999 \
    -wm 2048 -multi -wo NUM_THREADS=ALL_CPUS \
    -co TILED=YES -co COMPRESS=LZW -co BIGTIFF=YES \
    "${vrts[@]}" "$TMP_DIR/${key}_3857.tif"
}

tile_out () {  # $1 = product key, $2 = RGBA tif
  local key="$1" rgba="$2"
  echo "  [tiles] z$MINZOOM-$MAXZOOM (XYZ)"
  rm -rf "$OUT_DIR/$key"; mkdir -p "$OUT_DIR/$key"
  "$PYTHON" "$TILES_PY" --xyz -p mercator -r near --no-kml \
    -z "$MINZOOM-$MAXZOOM" --processes="$PROCESSES" -w none \
    "$rgba" "$OUT_DIR/$key"
  echo "  [prune] removing fully-transparent tiles"
  "$PYTHON" - "$OUT_DIR/$key" <<'PY'
import sys, os
from osgeo import gdal
gdal.UseExceptions()
root = sys.argv[1]
removed = kept = 0
for dirpath, _, files in os.walk(root):
    for fn in files:
        if not fn.endswith('.png'): continue
        p = os.path.join(dirpath, fn)
        ds = gdal.Open(p)
        a = ds.GetRasterBand(ds.RasterCount).GetMaximum()
        if a is None:
            a = ds.GetRasterBand(ds.RasterCount).ComputeRasterMinMax(False)[1]
        ds = None
        if not a: os.remove(p); removed += 1
        else: kept += 1
print(f"    pruned {removed} empty, kept {kept}")
PY
  find "$OUT_DIR/$key" -type d -empty -delete
  echo "  done: $(find "$OUT_DIR/$key" -name '*.png' | wc -l | tr -d ' ') tiles, $(du -sh "$OUT_DIR/$key" | cut -f1)"
}

build_flat () {  # thickness / overdeep: color-relief only
  local key="$1"
  echo "=== $key ==="
  mosaic "$key"
  "$DEM" color-relief "$TMP_DIR/${key}_3857.tif" "$ROOT_SH/iceboost_color_${key}.txt" \
    "$TMP_DIR/${key}_color.tif" -alpha -co COMPRESS=LZW -q
  tile_out "$key" "$TMP_DIR/${key}_color.tif"
}

build_bed () {
  echo "=== bed (color-relief x multidirectional hillshade) ==="
  mosaic bed
  "$DEM" color-relief "$TMP_DIR/bed_3857.tif" "$ROOT_SH/iceboost_color_bed.txt" \
    "$TMP_DIR/bed_color.tif" -alpha -co COMPRESS=LZW -q
  "$DEM" hillshade -multidirectional -z 2 "$TMP_DIR/bed_3857.tif" \
    "$TMP_DIR/bed_shade.tif" -co COMPRESS=LZW -q
  echo "  [blend] color x shade^0.7"
  "$SYS_PYTHON" - "$TMP_DIR/bed_color.tif" "$TMP_DIR/bed_shade.tif" \
      "$TMP_DIR/bed_rgba_raw.tif" <<'PY'
import sys
import numpy as np
import rasterio
cp, sp, op = sys.argv[1:4]
with rasterio.open(cp) as c, rasterio.open(sp) as s:
    rgba = c.read()                       # 4 x H x W uint8
    shade = s.read(1).astype(np.float32)  # 0-255 (hillshade nodata=0 -> dark,
                                          # but those cells are transparent in
                                          # rgba's alpha anyway)
    prof = c.profile
f = (shade / 255.0) ** 0.7                # gamma keeps midtones from muddying
out = rgba.copy()
out[:3] = np.clip(rgba[:3].astype(np.float32) * f, 0, 255).astype(np.uint8)
with rasterio.open(op, 'w', **prof) as w:
    w.write(out)
print('  blended')
PY
  # Re-stamp the CRS with the QGIS-bundled GDAL. The system rasterio (PROJ 9)
  # writes WKT2 that GDAL 3.3.2 parses as an ENGCRS engineering CRS, and
  # gdal2tiles then fails with "Cannot find coordinate operations ... to
  # EPSG:3857". A translate with -a_srs from the same GDAL that will tile it
  # writes WKT it understands.
  "$GDAL_BIN/gdal_translate" -q -a_srs EPSG:3857 -co COMPRESS=LZW \
    "$TMP_DIR/bed_rgba_raw.tif" "$TMP_DIR/bed_rgba.tif"
  tile_out bed "$TMP_DIR/bed_rgba.tif"
}

case "$WHICH" in
  thickness) build_flat thickness ;;
  overdeep)  build_flat overdeep ;;
  bed)       build_bed ;;
  all)       build_flat thickness; build_bed; build_flat overdeep ;;
  *) echo "usage: $0 [thickness|bed|overdeep|all] [max_zoom]"; exit 1 ;;
esac
echo "All done."
