#!/bin/sh
# Upload surveys to R2 unattended, and keep going until the bytes are actually
# there.
#
#   tools/lidar/r2_publish_overnight.sh pow_2017 dixon_2023 ...
#
# WHY THIS EXISTS RATHER THAN A PLAIN LOOP OF r2_put.sh
# -----------------------------------------------------
# Two overnight runs died silently today. The machine idle-sleeps after one
# minute (`pmset -g` reports `sleep 1`), so unless something holds an assertion
# the transfer stops the moment the laptop is left alone -- and a killed rclone
# writes nothing to say so. The queue log simply stopped mid-survey and the
# files were still missing hours later.
#
# So: `caffeinate` holds off idle and disk sleep for the duration, every file
# is VERIFIED over HTTP after its upload rather than assumed from an exit code,
# and anything missing is retried. Re-running is cheap by design -- rclone
# compares size and modification time and skips what already matches, which is
# what makes a supervisor like this affordable at all.
#
# Progress goes to the log AND to a status file, so an interrupted session can
# see where things stand without reading a transcript.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LOG=${LOG:-/Volumes/Nunatak/lidar_build/logs/r2_overnight.log}
STATUS=${STATUS:-/Volumes/Nunatak/lidar_build/logs/r2_overnight.status}
BASE=${R2_PUBLIC_BASE:-https://lidar.landslidescience.org}
TRIES=${TRIES:-6}
COG_DIR=${LIDAR_COG_OUT:-/Volumes/Nunatak/lidar_build/cog}
PM_DIR=${LIDAR_PM_OUT:-$ROOT/data/lidar/pmtiles}

say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# HEAD, with a User-Agent: Cloudflare answers 403 to some default agents, and a
# verifier that reports absence for everything would retry for ever.
#
# Presence is NOT the test. A survey rebuilt at a deeper zoom has the same key
# on the bucket, so the stale file answers 200 and a presence check calls the
# job done without uploading anything -- which is exactly what happened the
# first time portage_2020 was deepened from z18 to z19. Compare the byte count
# as well, which is what actually distinguishes the old file from the new one.
on_bucket() {
  key=$1; local_file=$2
  code=$(curl -s -o /dev/null -w '%{http_code}' -I --max-time 20 \
         -A 'landslidescience-publish/1' "$BASE/$key")
  [ "$code" = "200" ] || return 1
  [ -n "${local_file:-}" ] && [ -f "$local_file" ] || return 0   # nothing to compare
  remote=$(curl -s -I --max-time 20 -A 'landslidescience-publish/1' "$BASE/$key" \
           | awk 'tolower($1) == "content-length:" { gsub(/\r/, "", $2); print $2 }')
  want=$(wc -c < "$local_file" | tr -d ' ')
  [ -n "$remote" ] || return 0                 # no length header: presence is all we have
  [ "$remote" = "$want" ]
}

# What a survey is expected to have on the bucket, based on what exists locally.
# Each line is "<bucket key> <local file>", so the verifier can compare sizes
# rather than merely asking whether something is there.
expected() {
  id=$1
  [ -f "$COG_DIR/$id.tif" ] && echo "cog/$id.tif $COG_DIR/$id.tif"
  [ -f "$PM_DIR/$id.pmtiles" ] && echo "pmtiles/$id.pmtiles $PM_DIR/$id.pmtiles"
  [ -f "$PM_DIR/${id}_slope.pmtiles" ] && echo "pmtiles/${id}_slope.pmtiles $PM_DIR/${id}_slope.pmtiles"
  [ -f "$PM_DIR/${id}_ortho.pmtiles" ] && echo "pmtiles/${id}_ortho.pmtiles $PM_DIR/${id}_ortho.pmtiles"
}

missing_for() {
  expected "$1" | while read -r k f; do
    on_bucket "$k" "$f" || echo "$k"
  done
}

run() {
  say "=== starting: $*"
  : > "$STATUS"
  for id in "$@"; do
    n=0
    while [ "$n" -lt "$TRIES" ]; do
      miss=$(missing_for "$id")
      if [ -z "$miss" ]; then
        say "$id: all files verified on the bucket"
        echo "$id ok" >> "$STATUS"
        break
      fi
      n=$((n + 1))
      say "$id: attempt $n/$TRIES; still missing: $(echo $miss | tr '\n' ' ')"
      sh "$ROOT/tools/lidar/r2_put.sh" "$id" >> "$LOG" 2>&1
      sleep 5
    done
    if [ -n "$(missing_for "$id")" ]; then
      say "$id: GAVE UP after $TRIES attempts; missing: $(missing_for "$id" | tr '\n' ' ')"
      echo "$id FAILED" >> "$STATUS"
    fi
  done
  say "=== finished"
  echo "finished $(date '+%F %T')" >> "$STATUS"
}

# caffeinate: -i no idle sleep, -m no disk sleep, -s no system sleep on AC.
# Without it the machine sleeps after a minute and the transfer dies mute.
if [ "${CAFFEINATED:-}" = "1" ]; then
  run "$@"
else
  say "holding sleep off with caffeinate for the duration"
  CAFFEINATED=1 exec caffeinate -i -m -s "$0" "$@"
fi
