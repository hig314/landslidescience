#!/bin/bash
# Turn the file-based glacier-extent sources (download_sources.sh) into
# newline-delimited GeoJSON in EPSG:4326 under data/glaciers/sources/, which
# is what `manage.py fetch_glacier_source` loads. Run on the host: the web
# container has no GDAL. The outputs are small (a few MB each) and travel to
# production by rsync like the rest of data/.
#
# Publisher attributes pass through untouched. Two columns are added where
# the publisher keeps the fact in a file or folder name instead of a field:
# NSIDC-0634 gets `glacier` (file stem) and `area` (folder).
set -eu
SRC=${GLACIERS_SRC:-/Volumes/Nunatak/glaciers_src}
OUT=$(cd "$(dirname "$0")/../.." && pwd)/data/glaciers/sources
OGR=/opt/homebrew/bin/ogr2ogr      # not the QGIS GDAL that is first on PATH
mkdir -p "$OUT"

# USGS Glacier Bay terminus positions
( cd "$SRC/usgs_glacier_bay" && unzip -oq GBNPP_Glacial_Terminus_Positions.zip -d gb )
rm -f "$OUT/usgs_glacier_bay.geojsonl"
$OGR -f GeoJSONSeq -t_srs EPSG:4326 -lco COORDINATE_PRECISION=6 \
     "$OUT/usgs_glacier_bay.geojsonl" "$SRC/usgs_glacier_bay/gb/GBNPP_Glacial_Terminus_Positions.shp"

# USGS benchmark glacier boundaries: Alaska only (west of 128 W)
( cd "$SRC/usgs_benchmark" && unzip -oq USGS_Glacier_Boundaries_shp.zip -d bm )
rm -f "$OUT/usgs_benchmark.geojsonl"
$OGR -f GeoJSONSeq -t_srs EPSG:4326 -lco COORDINATE_PRECISION=6 -spat -180 50 -128 72 -spat_srs EPSG:4326 \
     "$OUT/usgs_benchmark.geojsonl" "$SRC/usgs_benchmark/bm/USGS_Glacier_Boundaries.shp"

# RGI 7.0 centerlines (region 01)
( cd "$SRC/rgi7_centerlines" && unzip -oq L01.zip -d L01 )
rm -f "$OUT/rgi7_centerlines.geojsonl"
$OGR -f GeoJSONSeq -lco COORDINATE_PRECISION=6 \
     "$OUT/rgi7_centerlines.geojsonl" "$SRC/rgi7_centerlines/L01/RGI2000-v7.0-L-01_alaska.shp"

# NSIDC-0634: one shapefile per glacier, in per-area folders
rm -f "$OUT/nsidc0634.geojsonl"; TMP=$(mktemp -d "$OUT/.tmp0634.XXXX")
for shp in "$SRC"/nsidc0634/*/*.shp; do
  g=$(basename "$shp" .shp); a=$(basename "$(dirname "$shp")")
  # yakutat/turner ships without a .prj; its `proj` attribute says which UTM
  # zone, as every file's does, so a missing .prj is read from there.
  SSRS=()
  if [ ! -s "${shp%.shp}.prj" ]; then
    zone=$(/opt/homebrew/bin/ogrinfo -al -geom=NO "$shp" | sed -n 's/.*proj (String) = UTM zone \([0-9]*\)N.*/\1/p' | head -1)
    [ -n "$zone" ] || { echo "no CRS for $shp" >&2; exit 1; }
    SSRS=(-s_srs "EPSG:326$(printf %02d "$zone")")
  fi
  $OGR -f GeoJSONSeq ${SSRS[@]+"${SSRS[@]}"} -t_srs EPSG:4326 -lco COORDINATE_PRECISION=6 \
       -sql "SELECT *, '$g' AS glacier, '$a' AS area FROM \"$g\"" "$TMP/$a.$g.geojsonl" "$shp"
  cat "$TMP/$a.$g.geojsonl" >> "$OUT/nsidc0634.geojsonl"
done
rm -rf "$TMP"
wc -l "$OUT"/*.geojsonl
