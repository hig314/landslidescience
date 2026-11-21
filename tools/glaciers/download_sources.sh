#!/bin/bash
# Download the file-based glacier-extent sources to the workstation's source
# archive. Run on the host (not in the container). Re-running skips files that
# are already there. See GLACIERS_PLAN.md §6 for what each source is.
#
#   tools/glaciers/download_sources.sh [small|g10040|nsidc0634|all]
#
# NSIDC-0634 needs an Earthdata login in ~/.netrc (urs.earthdata.nasa.gov).
set -u
SRC=${GLACIERS_SRC:-/Volumes/Nunatak/glaciers_src}
WHAT=${1:-all}
mkdir -p "$SRC"
get() {  # url, destination
  if [ -s "$2" ]; then return 0; fi
  curl -sS -L --fail --retry 3 -m 3600 -o "$2.part" "$1" && mv "$2.part" "$2" \
    || { echo "FAILED $1" >&2; rm -f "$2.part"; return 1; }
}

if [ "$WHAT" = small ] || [ "$WHAT" = all ]; then
  # USGS Glacier Bay terminus positions (Avdievitch, Bliss & Coe 2023), CC0
  mkdir -p "$SRC/usgs_glacier_bay"
  get 'https://www.sciencebase.gov/catalog/file/get/64889018d34ef77fcafe39af?f=__disk__56%2Fae%2F30%2F56ae3071fc1a29cff4c6056ec6191680354676a9' \
      "$SRC/usgs_glacier_bay/GBNPP_Glacial_Terminus_Positions.zip"
  get 'https://www.sciencebase.gov/catalog/file/get/64889018d34ef77fcafe39af?f=__disk__f0%2F2b%2Fd4%2Ff02bd4c9c957e05168433e1437903b8040675d37' \
      "$SRC/usgs_glacier_bay/Readme.txt"
  # USGS benchmark glacier boundaries, CC0
  mkdir -p "$SRC/usgs_benchmark"
  get 'https://www.sciencebase.gov/catalog/file/get/5d7adbcde4b0c4f70d02a4d6?f=__disk__f8%2F2b%2F65%2Ff82b65c77369fe070d40df604e81fdbad0fedaa9' \
      "$SRC/usgs_benchmark/USGS_Glacier_Boundaries_shp.zip"
  get 'https://www.sciencebase.gov/catalog/file/get/5d7adbcde4b0c4f70d02a4d6?f=__disk__8f%2Fd9%2Fbb%2F8fd9bb01fc5f67e9a1984ca675f927f64ccdcb2b' \
      "$SRC/usgs_benchmark/README.pdf"
fi

if [ "$WHAT" = small ] || [ "$WHAT" = all ]; then
  # RGI 7.0 centerlines product (L), region 01. The NSIDC copy needs an
  # Earthdata login; this is the same release on the RGI lead's Bremen mirror.
  mkdir -p "$SRC/rgi7_centerlines"
  get 'https://cluster.klima.uni-bremen.de/~fmaussion/misc/rgi7_data/rgi7_final/regional_files/RGI2000-v7.0-L/RGI2000-v7.0-L-01_alaska.zip' \
      "$SRC/rgi7_centerlines/L01.zip"
fi

if [ "$WHAT" = g10040 ] || [ "$WHAT" = all ]; then
  # NSIDC G10040 (Roberts-Pierel and others 2022): even years 1986-2020.
  B=https://noaadata.apps.nsidc.org/NOAA/G10040
  for kind in overall_area:overall_glacier_covered_area supraglacial_debris_area:supraglacial_debris; do
    dir=${kind%%:*}
    mkdir -p "$SRC/g10040/$dir"
    curl -sS -L -m 120 "$B/$dir/" -o "$SRC/g10040/$dir/index.html"
    grep -o 'href="[^"]*\.\(shp\|shx\|dbf\|prj\|cpg\)"' "$SRC/g10040/$dir/index.html" \
      | sed 's/href="//; s/"$//' | sort -u | while read -r f; do
        get "$B/$dir/$f" "$SRC/g10040/$dir/$(basename "$f")"
      done
  done
fi

if [ "$WHAT" = nsidc0634 ] || [ "$WHAT" = all ]; then
  # McNabb & Hock tidewater terminus positions. Earthdata login via ~/.netrc.
  D="$SRC/nsidc0634"; mkdir -p "$D"
  B=https://daacdata.apps.nsidc.org/pub/DATASETS/nsidc0634_ak_tidewater_glacier_v1
  # (wget's Earthdata redirect handling fetched nothing; curl -n with a
  # cookie jar works.)
  eget() { [ -s "$2" ] || { curl -sS -n -L --fail -c "$D/.cj" -b "$D/.cj" -m 600 -o "$2.part" "$1" \
             && mv "$2.part" "$2" || { echo "FAILED $1" >&2; rm -f "$2.part"; }; }; }
  eget "$B/00README.txt" "$D/00README.txt"
  for sub in coast_range glacier_bay icy_bay kenai length_timeseries west_chugach yakutat; do
    mkdir -p "$D/$sub"
    curl -sS -n -L -c "$D/.cj" -b "$D/.cj" -m 120 "$B/$sub/" -o "$D/$sub/.index.html"
    grep -o 'href="[^"?/]*\.[A-Za-z0-9]*"' "$D/$sub/.index.html" | sed 's/href="//; s/"$//' \
      | sort -u | while read -r f; do eget "$B/$sub/$f" "$D/$sub/$f"; done
  done
fi
echo done "$WHAT"
