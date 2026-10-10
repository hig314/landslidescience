#!/bin/bash
# Fetch every 1 km block of the Homer 2019 footprint from the USGS EPT (remote, retried),
# reproject to EPSG:6334 minus noise classes. Sequential, low priority; ~2 min per block.
set -u; cd /Volumes/Nunatak/lidar_build/kenai_taught/full; mkdir -p tiles
say() { echo "[$(date '+%F %T')] $*"; }
P=/opt/homebrew/bin/pdal
/opt/anaconda3/bin/python3 -c "
import json; [print(b['id'], b['ept_bounds']) for b in json.load(open('blocks.json'))]" | while read id bounds; do
  [ -s tiles/$id.laz ] && $P info --summary tiles/$id.laz >/dev/null 2>&1 && continue
  ok=0
  for i in 1 2 3 4 5 6; do
    rm -f tiles/$id.laz    # a failed attempt can leave a truncated file that still passes -s (2026-09-12: 11 of 13 tiles)
    nice -n 10 $P pipeline --stdin > tiles/$id.fetch.log 2>&1 <<JSON
{"pipeline":[{"type":"readers.ept","filename":"https://usgs-lidar-public.s3.us-west-2.amazonaws.com/AK_Kenai_2008/ept.json","bounds":"$bounds","threads":4},
 {"type":"filters.range","limits":"Classification![7:7], Classification![10:10]"},
 {"type":"filters.reprojection","in_srs":"EPSG:3857","out_srs":"EPSG:6334"},
 {"type":"writers.las","filename":"tiles/$id.laz","compression":"laszip","forward":"all","a_srs":"EPSG:6334"}]}
JSON
    [ -s tiles/$id.laz ] && $P info --summary tiles/$id.laz >/dev/null 2>&1 && { ok=1; break; }; sleep 30
  done
  say "$id: $([ $ok = 1 ] && echo ok $(du -h tiles/$id.laz | cut -f1) || echo FAILED after 6 attempts)"
done
say "done: $(ls tiles/*.laz 2>/dev/null | wc -l) tiles"
