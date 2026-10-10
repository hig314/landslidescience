#!/bin/bash
# Features for every fetched tile, as tiles arrive; ends when the fetch is done and every tile is processed.
set -u; cd /Volumes/Nunatak/lidar_build/kenai_taught/full
say() { echo "[$(date '+%F %T')] $*"; }
while true; do
  did=0
  while read id x y; do
    [ -s tiles/$id.laz ] || continue; [ -s feat/$id.csv ] && continue; [ -f feat/$id.failed ] && continue
    /opt/homebrew/bin/pdal info --summary tiles/$id.laz >/dev/null 2>&1 || continue   # still being written, or malformed
    out=$(./features_tile.sh $id $x $y); echo "[$(date '+%T')] $out"; did=1
    case "$out" in *FAILED*) touch feat/$id.failed;; esac
  done < <(/opt/anaconda3/bin/python3 -c "import json; [print(b['id'],b['x'],b['y']) for b in json.load(open('blocks.json'))]")
  grep -q "^\[.*\] done" fetch_tiles.log && [ $did = 0 ] && break
  [ $did = 0 ] && sleep 120
done
say "done: $(ls feat/*.csv | wc -l) feature files, $(du -sh feat | cut -f1)"
