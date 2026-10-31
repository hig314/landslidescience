#!/bin/sh
# Push the whole permafrost build directory (sources, working grids, the 60 m
# outputs the analysis cube and ls_quantities.json are sampled from, fits,
# logs) to the lidar bucket's permafrost_build/ prefix, paths unchanged.
# It is the off-site copy of grids that otherwise exist only on Nunatak, and
# it makes them readable by URL
# (https://lidar.landslidescience.org/permafrost_build/out/elev60_land.tif):
# the big rasters are tiled ZSTD GeoTIFFs, so /vsicurl reads only the blocks
# it needs. Public on purpose (Hig, 2026-10-31).
#
#   tools/permafrost/r2_sync_build.sh             # copy (resumable, never deletes)
#   tools/permafrost/r2_sync_build.sh --dry-run
#   nohup tools/permafrost/r2_sync_build.sh > /tmp/r2_pf_build.log 2>&1 &
#
# Same rclone setup and credentials (~/.r2.env) as tools/lidar/r2_sync.sh.
set -eu
SRC="${PF_BUILD:-/Volumes/Nunatak/permafrost_build}"
set -a; . "$HOME/.r2.env"; set +a
export RCLONE_CONFIG_R2_TYPE=s3 \
       RCLONE_CONFIG_R2_PROVIDER=Cloudflare \
       RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
       RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
       RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT" \
       RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
# --filter, never --exclude: see the note in tools/lidar/r2_sync.sh.
exec rclone copy "$SRC" "r2:$R2_BUCKET/permafrost_build/" \
     --filter "- .DS_Store" --filter "- ._*" "$@" \
     --s3-chunk-size 64M --s3-upload-concurrency 4 --transfers 2 \
     --progress --stats 60s --stats-one-line
