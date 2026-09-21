#!/bin/sh
# Push the lidar archive COGs to Cloudflare R2.
#
#   tools/lidar/r2_sync.sh            # sync /Volumes/Nunatak/lidar_build/cog -> r2:<bucket>/cog/
#   tools/lidar/r2_sync.sh --dry-run
#
# Credentials come from ~/.r2.env (mode 600, never in the repo):
#   R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
# The token is scoped to the one bucket, so rclone must not try to list or
# create buckets (RCLONE_CONFIG_R2_NO_CHECK_BUCKET).
#
# Public reads go through the bucket's custom domain, lidar.landslidescience.org
# (Cloudflare CDN, CORS for Range/ETag set on the bucket). make_catalog.py
# writes those URLs into catalog.geojson.
#
# rclone `copy` is resumable per file and never deletes on the remote. Run it
# detached for the multi-hour first push:
#   nohup tools/lidar/r2_sync.sh > /tmp/r2_sync.log 2>&1 &
#
# Since 2026-09-11 the web pyramids live in R2 too (<bucket>/pmtiles/):
#   tools/lidar/r2_sync.sh --pmtiles   # sync data/lidar/pmtiles -> r2:<bucket>/pmtiles/
# Archives over 512 MB rely on the zone's bypass-cache rule covering /pmtiles/.
set -eu
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [ "${1:-}" = "--pmtiles" ]; then
  shift
  SRC="${LIDAR_PM_OUT:-$ROOT/data/lidar/pmtiles}"
  DEST_PREFIX=pmtiles
  PATTERN='*.pmtiles'
else
  SRC="${LIDAR_COG_OUT:-/Volumes/Nunatak/lidar_build/cog}"
  DEST_PREFIX=cog
  PATTERN='*.tif'
fi
set -a; . "$HOME/.r2.env"; set +a
export RCLONE_CONFIG_R2_TYPE=s3 \
       RCLONE_CONFIG_R2_PROVIDER=Cloudflare \
       RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
       RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
       RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT" \
       RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
# EVERY selection rule here is a --filter, never --include/--exclude. rclone
# keeps those two in SEPARATE lists and, when any --include is present, the
# --exclude list is dropped outright. It is not an ordering problem, so putting
# the excludes first does not help. Measured against rclone 1.75 on 2026-09-21:
# `--exclude g.pmtiles --include '*.pmtiles'` copies g.pmtiles, in either
# order. --filter rules ARE one ordered list, first match wins, which is the
# behaviour this script needs. (The 2026-09-08 note that used to sit here read
# the same symptom as an ordering bug and moved the caller flags to the front;
# that was the wrong diagnosis, and the exclusions below never fired once.)
#
# Gated datasets ("gated": true in datasets.json) never go to the public
# bucket. EVERY product of a gated survey must be listed here -- the ortho was
# missing as well, and the ortho is the more revealing of the two products, so
# a gate that leaks it is a gate in name only. A new product kind needs a line
# here at the same time it gets a builder. Ours are prepended, so a caller
# cannot accidentally out-rank them; pass `--filter '- foo.tif'` rather than
# `--exclude foo.tif`, or it will be ignored exactly as described above.
for id in $(python3 -c "import json;print(' '.join(d['id'] for d in json.load(open('$ROOT/tools/lidar/datasets.json'))['datasets'] if d.get('gated')))"); do
  set -- --filter "- $id.tif" --filter "- $id.pmtiles" \
         --filter "- ${id}_slope.pmtiles" --filter "- ${id}_ortho.pmtiles" "$@"
done
exec rclone copy "$SRC" "r2:$R2_BUCKET/$DEST_PREFIX/" \
     "$@" \
     --filter "+ $PATTERN" --filter "- *" \
     --s3-chunk-size 64M --s3-upload-concurrency 4 --transfers 2 \
     --progress --stats 60s --stats-one-line
