#!/bin/bash
# NSIDC G10040 (Roberts-Pierel and others 2022) -> one vector PMTiles archive
# per epoch, for the statewide glacier-cover time layer on /glaciers/.
#
#   tools/glaciers/build_cover_tiles.sh [year ...]      # default: every epoch
#
# Out: data/overlays/glac_cover_<year>.pmtiles (layer `cover`) and
#      data/overlays/glac_debris_<year>.pmtiles (layer `debris`),
# served by the existing /overlays/<id>.pmtiles route: the local file in dev,
# R2 in production (push with tools/permafrost/r2_sync_overlays.sh).
#
# One archive per year and not one archive with a year attribute: eighteen
# statewide polygon sets in one tile overflow the tile size budget at the
# zooms where the whole state is in view, and the driver then drops features.
#
# The publisher's RGI id (RGI 6.0), name and that year's area ride along, so a
# click can say whose ice it is.
set -eu
SRC=${GLACIERS_SRC:-/Volumes/Nunatak/glaciers_src}/g10040
OUT=$(cd "$(dirname "$0")/../.." && pwd)/data/overlays
OGR=/opt/homebrew/bin/ogr2ogr      # not the QGIS GDAL that is first on PATH
MAXZ=${MAXZ:-11}
mkdir -p "$OUT"
YEARS=${*:-$(ls "$SRC"/overall_area/*.shp | sed 's/.*AK_\([0-9]*\)_.*/\1/' | sort -u)}
for y in $YEARS; do
  for pair in overall_area:overall_glacier_covered_area:cover supraglacial_debris_area:supraglacial_debris_area:debris; do
    dir=${pair%%:*}; rest=${pair#*:}; stem=${rest%%:*}; lyr=${rest#*:}
    in="$SRC/$dir/AK_${y}_${stem}.shp"; out="$OUT/glac_${lyr}_${y}.pmtiles"
    [ -s "$in" ] || { echo "missing $in" >&2; exit 1; }
    rm -f "$out"
    $OGR -f PMTiles "$out" "$in" -t_srs EPSG:3857 -nln "$lyr" \
         -select RGIId,Name,GN_area -makevalid \
         -dsco MINZOOM=3 -dsco MAXZOOM="$MAXZ" -dsco NAME="glac_${lyr}_${y}"
    echo "$y $lyr $(du -h "$out" | cut -f1)"
  done
done
