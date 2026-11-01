#!/bin/bash
# Fetch Mapterhorn terrarium tiles at zoom Z over the coverage polygon into
# $OUT/<z>/<x>/<y>.webp (default z12, Alaska + 3 km). Resumable: the list
# names only tiles not yet on disk, so re-run to resume or to pick up a
# grown coverage. One curl with --parallel reuses HTTP/2 connections (the
# per-tile curl of the first permafrost fetch managed 0.5 tiles/s).
#
#   nohup tools/terrain/fetch_mapterhorn.sh 12 > /Volumes/Nunatak/terrain_src/fetch_z12.log 2>&1 &
set -u
Z=${1:-12}
OUT=${OUT:-/Volumes/Nunatak/terrain_src/mapterhorn}
HERE=$(cd "$(dirname "$0")" && pwd)
say() { echo "[$(date '+%F %T')] $*"; }
mkdir -p "$OUT"; cd "$OUT"
for pass in 1 2; do
  /opt/anaconda3/bin/python3 -I "$HERE/mapterhorn_tiles.py" --z "$Z" --out "$OUT" > "curl$Z.cfg"
  N=$(grep -c '^url' "curl$Z.cfg")
  say "pass $pass: fetching $N tiles, 12 in parallel"
  [ "$N" -eq 0 ] && break
  curl -s -f --retry 3 --retry-delay 5 --create-dirs --parallel --parallel-max 12 -K "curl$Z.cfg"
  say "curl exit $?; have $(find "$Z" -name '*.webp' | wc -l | tr -d ' ') tiles, $(du -sh "$Z" | cut -f1)"
  find "$Z" -name '*.webp' -size 0 -delete
done
say "done (tiles still missing after two passes are most likely sea: Mapterhorn has no tile there)"
