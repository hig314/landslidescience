#!/bin/sh
# Mirror one photo drop from R2 to the Nunatak volume — the backup copy, and
# where any real processing (time-lapse assembly, SfM) runs.
#
#   tools/drops/r2_pull.sh <slug>                       # -> /Volumes/Nunatak/Landslides/drops/<slug>/
#   tools/drops/r2_pull.sh <slug> /some/other/dir
#   tools/drops/r2_pull.sh --list                        # drops present in the bucket
#
# Credentials come from ~/.r2_drops.env (mode 600, never in the repo):
#   DROPS_R2_ENDPOINT, DROPS_R2_ACCESS_KEY_ID, DROPS_R2_SECRET_ACCESS_KEY, DROPS_R2_BUCKET
# — the same four values the web container has. This is a DIFFERENT token
# from ~/.r2.env: that one is scoped to the public lidar bucket only.
#
# `copy`, not `sync`: the local copy only ever grows. A file removed from the
# bucket (or a mistaken local path) can never delete anything on Nunatak.
# Server-made thumbnails (.thumbs/) are excluded; they are derived and small.
set -eu
ENV="$HOME/.r2_drops.env"
[ -r "$ENV" ] || { echo "missing $ENV (DROPS_R2_* — see the header of this script)" >&2; exit 1; }
set -a; . "$ENV"; set +a
export RCLONE_CONFIG_R2DROPS_TYPE=s3 \
       RCLONE_CONFIG_R2DROPS_PROVIDER=Cloudflare \
       RCLONE_CONFIG_R2DROPS_ACCESS_KEY_ID="$DROPS_R2_ACCESS_KEY_ID" \
       RCLONE_CONFIG_R2DROPS_SECRET_ACCESS_KEY="$DROPS_R2_SECRET_ACCESS_KEY" \
       RCLONE_CONFIG_R2DROPS_ENDPOINT="$DROPS_R2_ENDPOINT" \
       RCLONE_CONFIG_R2DROPS_NO_CHECK_BUCKET=true

if [ "${1:-}" = "--list" ]; then
  rclone lsd "r2drops:$DROPS_R2_BUCKET/drops/"
  exit 0
fi
SLUG="${1:?usage: r2_pull.sh <slug> [dest]}"
DEST="${2:-/Volumes/Nunatak/Landslides/drops/$SLUG}"
mkdir -p "$DEST"
echo "r2drops:$DROPS_R2_BUCKET/drops/$SLUG/  ->  $DEST"
rclone copy "r2drops:$DROPS_R2_BUCKET/drops/$SLUG/" "$DEST" \
  --exclude '.thumbs/**' --transfers 8 --checkers 16 --progress --stats-one-line
