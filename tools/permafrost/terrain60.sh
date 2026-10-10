#!/bin/sh
# The 60 m terrain grids for the permafrost downscale, EPSG:3338, over the
# study extent (170-128 W, 54-72 N; the same -te as the 1 km fit grids):
#
#   elev60.tif       elevation, m (Mapterhorn z11 mosaic warped with average)
#   slope60.tif      slope, degrees
#   aspect60.tif     aspect, degrees from north (gdaldem -zero_for_flat)
#   northness60.tif  cos(aspect) * sin(slope): +1 a steep north face,
#                    -1 a steep south face, 0 flat -- the aspect term
#   elev_1km.tif, northness_1km.tif   the same, averaged onto the 1 km
#                    fit grid (what the block regressions see)
#
# Needs the mosaic from mapterhorn_mosaic.py. Homebrew GDAL, not the QGIS one.
set -eu
export PATH=/opt/homebrew/bin:$PATH
unset PROJ_LIB PROJ_DATA
B=/Volumes/Nunatak/permafrost_build
M=$B/mapterhorn_z11.tif
TE="-1041000 443000 1665000 2622000"
CO="-co COMPRESS=ZSTD -co PREDICTOR=3 -co TILED=YES -co BIGTIFF=YES"
say() { echo "[$(date '+%F %T')] $*"; }

say "elev60: warp the mosaic to EPSG:3338 at 60 m (average)"
gdalwarp -q -overwrite -t_srs EPSG:3338 -te $TE -tr 60 60 -tap -r average -ot Float32 \
  -srcnodata -9999 -dstnodata -9999 -multi -wo NUM_THREADS=ALL_CPUS $CO $M $B/elev60.tif
say "slope60 / aspect60"
gdaldem slope  -q -compute_edges -co COMPRESS=ZSTD -co PREDICTOR=3 -co TILED=YES -co BIGTIFF=YES $B/elev60.tif $B/slope60.tif
gdaldem aspect -q -compute_edges -zero_for_flat -co COMPRESS=ZSTD -co PREDICTOR=3 -co TILED=YES -co BIGTIFF=YES $B/elev60.tif $B/aspect60.tif
say "northness60"
/opt/anaconda3/bin/python3 -I "$(dirname "$0")/rastercalc.py" northness $B/slope60.tif $B/aspect60.tif $B/northness60.tif
say "1 km cell means for the fit"
for v in elev northness; do
  gdalwarp -q -overwrite -te $TE -tr 1000 1000 -tap -r average -ot Float32 -srcnodata -9999 -dstnodata -9999 \
    -co COMPRESS=ZSTD -co PREDICTOR=3 $B/${v}60.tif $B/src/${v}_1km.tif
done
say "done"; ls -l $B/*60.tif $B/src/elev_1km.tif $B/src/northness_1km.tif | awk '{print $5, $9}'
