#!/bin/sh
# Bake the Alaska DGGS deep-seated landslide susceptibility raster into
# self-hosted XYZ tiles, the same way tools/build_susc_tiles.sh does for the
# USGS pair.
#
#   tools/build_dggs_susc_tiles.sh [max_zoom]
#
# Source: /Volumes/Nunatak/lidar_src/dggs_susc/dggs_susc_20m.vrt, the statewide
# 20 m class raster recovered by tools/harvest_dggs_susc.py.
#
# Colour is baked at tile-gen time from tools/dggs_susc_color.txt, because
# MapLibre GL JS does not implement Mapbox's `raster-color`. To recolour: edit
# that file, re-run this, and bump DGGS_SUSC_V in map.js.
#
# z12 is the native ceiling: 20 m data is 19.1 m/px at z12 for 60 N, so z13
# would only invent detail. The client overzooms above it.
set -eu
G=${GDAL_BIN:-/opt/homebrew/bin}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SRC=${DGGS_SUSC_SRC:-/Volumes/Nunatak/lidar_src/dggs_susc/dggs_susc_20m.vrt}
WORK=${DGGS_SUSC_WORK:-/Volumes/Nunatak/lidar_build/dggs_susc}
OUT=$ROOT/data/susc_tiles/dggs
MAXZ=${1:-12}
COLOR=$ROOT/tools/dggs_susc_color.txt

mkdir -p "$WORK" "$OUT"
export GDAL_PAM_ENABLED=NO
unset PROJ_LIB PROJ_DATA 2>/dev/null || true

echo "== colour-relief (RGBA, classes -> YlOrRd)"
"$G/gdaldem" color-relief "$SRC" "$COLOR" "$WORK/rgba_3338.vrt" -alpha -of VRT

# THE ANTIMERIDIAN. Alaska's Aleutians run past 180 deg, so this raster's
# WGS84 extent reads -132.5 to +175.7 and a single warp to Web Mercator tries
# to span the whole globe -- the first attempt produced a 261229 x 35161 grid
# with non-square pixels. Warp the two sides separately instead. XYZ tiles are
# keyed by absolute x index, so the two passes drop into one directory with no
# conflict and no seam.
#
# Resolution is pinned to the z12 tile resolution (38.2185 Mercator m/px)
# rather than left to gdalwarp's estimate, which is what makes the pixel grid
# line up with the tile grid.
RES=38.2185
# y bounds are DERIVED by tools/dggs_susc_bounds.py, not written down here. The
# first version hardcoded them from the VRT's wgs84Extent, which comes from the
# raster's four CORNERS -- and Albers is rotated against lat/lon, so a corner is
# not the northernmost point. The corners read 63.88 and 67.64 N while the top
# EDGE reaches 71.53 N, so the bound silently cut the Brooks Range and the whole
# North Slope out of the tiles while the raster itself held them.
PY=${PY:-/opt/anaconda3/bin/python3}
eval "$(env -u PROJ_LIB -u PROJ_DATA "$PY" "$ROOT/tools/dggs_susc_bounds.py" "$SRC")"
echo "== y bounds: $YMIN .. $YMAX"

warp_and_tile() {   # label xmin xmax
  lbl=$1; xmin=$2; xmax=$3
  out="$WORK/rgba_3857_$lbl.tif"
  echo "== warp ($lbl): x $xmin .. $xmax"
  "$G/gdalwarp" -overwrite -t_srs EPSG:3857 -r near \
    -te "$xmin" "$YMIN" "$xmax" "$YMAX" -tr "$RES" "$RES" \
    -dstalpha -co TILED=YES -co COMPRESS=ZSTD -co BIGTIFF=YES \
    -co NUM_THREADS=ALL_CPUS -multi -wo NUM_THREADS=ALL_CPUS \
    "$WORK/rgba_3338.vrt" "$out"
  echo "== tile ($lbl) z3-z$MAXZ"
  "$G/gdal" raster tile --min-zoom 3 --max-zoom "$MAXZ" -r near \
    --convention xyz --skip-blank --resume --webviewer none \
    -j ALL_CPUS --no-intersection-ok "$out" "$OUT"
}

# West of the antimeridian: the mainland, Southeast, and most of the chain.
warp_and_tile main -20037508 -14360000
# East of it: the far western Aleutians (45 of 1800 chunk corners).
warp_and_tile aleut 19035000 20037508

echo "== done: $(find "$OUT" -name '*.png' | wc -l | tr -d ' ') tiles in $OUT"
