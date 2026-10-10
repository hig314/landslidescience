#!/bin/sh
# Push the baked overlay archives (permafrost colour overlays, glacier
# outline, value archives) to the lidar bucket's overlays/ prefix, which
# /overlays/<id>.pmtiles redirects to in production. Same rclone setup and
# credentials (~/.r2.env) as tools/lidar/r2_sync.sh; copy never deletes.
set -eu
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="${OVERLAYS_SRC:-$ROOT/data/overlays}"
set -a; . "$HOME/.r2.env"; set +a
export RCLONE_CONFIG_R2_TYPE=s3 \
       RCLONE_CONFIG_R2_PROVIDER=Cloudflare \
       RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
       RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
       RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT" \
       RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
exec rclone copy "$SRC" "r2:$R2_BUCKET/overlays/" "$@" \
     --filter "+ *.pmtiles" --filter "- *" \
     --s3-chunk-size 64M --s3-upload-concurrency 4 --transfers 2 \
     --progress --stats 60s --stats-one-line
