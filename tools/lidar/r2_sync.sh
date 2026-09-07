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
set -eu
SRC="${LIDAR_COG_OUT:-/Volumes/Nunatak/lidar_build/cog}"
set -a; . "$HOME/.r2.env"; set +a
export RCLONE_CONFIG_R2_TYPE=s3 \
       RCLONE_CONFIG_R2_PROVIDER=Cloudflare \
       RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
       RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
       RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT" \
       RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
exec rclone copy "$SRC" "r2:$R2_BUCKET/cog/" \
     --include '*.tif' \
     --s3-chunk-size 64M --s3-upload-concurrency 4 --transfers 2 \
     --progress --stats 60s --stats-one-line \
     "$@"
