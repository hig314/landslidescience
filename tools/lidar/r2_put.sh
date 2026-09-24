#!/bin/sh
# Upload one dataset's files to R2 -- archive COG, web pyramid, slope pyramid --
# without the include/exclude ordering ambiguity of r2_sync.sh filters.
#   tools/lidar/r2_put.sh <dataset_id>
# Refuses gated datasets. Credentials from ~/.r2.env, as r2_sync.sh.
set -eu
id="$1"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if python3 -c "import json,sys; d={x['id']:x for x in json.load(open('$ROOT/tools/lidar/datasets.json'))['datasets']}; sys.exit(0 if d.get('$id',{}).get('gated') else 1)"; then
  echo "$id is gated; not uploading" >&2; exit 1
fi
set -a; . "$HOME/.r2.env"; set +a
export RCLONE_CONFIG_R2_TYPE=s3 RCLONE_CONFIG_R2_PROVIDER=Cloudflare \
       RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
       RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT" RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
# Transfer shape. 64M parts x 4 concurrent was the original and it does not
# survive a slow uplink: at the ~1.1 MB/s this machine gets, four concurrent
# 64 MB parts each hold a connection open for about four minutes, and a NAT or
# edge reset then kills the part. pow_2018's 16.9 GB slope pyramid spent 31
# hours failing that way on "broken pipe" write errors while its two larger
# siblings, uploaded before the link degraded, went up fine. Smaller parts
# finish in well under a minute, so a reset costs one part instead of the
# attempt. Raise both on a fast link:
#   R2_CHUNK=64M R2_CONC=4 tools/lidar/r2_put.sh <id>
CHUNK=${R2_CHUNK:-16M}
CONC=${R2_CONC:-2}
COG="${LIDAR_COG_OUT:-/Volumes/Nunatak/lidar_build/cog}"
PM="${LIDAR_PM_OUT:-$ROOT/data/lidar/pmtiles}"
# The orthomosaic is part of a survey, not a separate dataset: a DSM whose
# imagery is missing drapes nothing, and the catalogue only advertises
# ortho_url when the file exists, so publishing one without the other
# produces a survey that silently lost a feature it had on dev.
for pair in "$COG/$id.tif cog/$id.tif" "$PM/$id.pmtiles pmtiles/$id.pmtiles" "$PM/${id}_slope.pmtiles pmtiles/${id}_slope.pmtiles" "$PM/${id}_ortho.pmtiles pmtiles/${id}_ortho.pmtiles"; do
  set -- $pair
  [ -f "$1" ] || { echo "skip (not built): $1"; continue; }
  for i in 1 2 3; do
    rclone copyto "$1" "r2:$R2_BUCKET/$2" \
      --s3-chunk-size "$CHUNK" --s3-upload-concurrency "$CONC" \
      --multi-thread-streams "$CONC" --low-level-retries 20 \
      --stats 600s --stats-one-line && break
    sleep 60
  done
  echo "$(date '+%F %T') $2: $(curl -s -o /dev/null -r 0-15 -w '%{http_code}' https://lidar.landslidescience.org/$2)"
done
echo "$(date '+%F %T') done"
