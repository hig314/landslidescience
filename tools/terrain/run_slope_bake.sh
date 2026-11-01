#!/bin/bash
# Waits for the z12 fetch, proves what is missing really is missing, then bakes.
# A tile absent from disk is read as open sea, so a failed download must not
# pass for one: every missing tile is re-asked, and anything but a 404 stops
# the run. Stops the bake if Nunatak falls under MIN_FREE_GB.
set -u
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SRC=/Volumes/Nunatak/terrain_src/mapterhorn
OUT=/Volumes/Nunatak/terrain_build/slope
MIN_FREE_GB=15
say() { echo "[$(date '+%F %T')] $*"; }
while pgrep -f fetch_mapterhorn.sh > /dev/null; do sleep 30; done
say "fetch finished: $(tail -1 /Volumes/Nunatak/terrain_src/fetch_z12.log)"
cd "$SRC"
/opt/anaconda3/bin/python3 -I "$ROOT/tools/terrain/mapterhorn_tiles.py" --z 12 --out "$SRC" > missing12.cfg
N=$(grep -c '^url' missing12.cfg)
say "$(find 12 -name '*.webp' | wc -l | tr -d ' ') tiles on disk, $(du -sh 12 | cut -f1); $N missing, asking again"
grep '^url' missing12.cfg | sed 's/^url = "//; s/"$//' > missing12.txt
xargs -P 12 -n 1 curl -s -o /dev/null -w '%{http_code} %{url_effective}\n' < missing12.txt > missing12.status
BAD=$(grep -vc '^404 ' missing12.status)
say "status of the missing: $(cut -d' ' -f1 missing12.status | sort | uniq -c | tr '\n' ' ')"
if [ "$BAD" -ne 0 ]; then say "STOP: $BAD missing tiles are not 404s, see $SRC/missing12.status"; exit 2; fi
cd "$ROOT"
rm -rf /Volumes/Nunatak/terrain_build/slope_test
/opt/anaconda3/bin/python3 -I -W ignore tools/terrain/bake_slope.py --workers 6 --out "$OUT" &
PID=$!
while kill -0 $PID 2>/dev/null; do
  FREE=$(df -g /Volumes/Nunatak | tail -1 | awk '{print $4}')
  if [ "$FREE" -lt "$MIN_FREE_GB" ]; then
    say "STOP: Nunatak has ${FREE} GB free"; pkill -P $PID; kill $PID; exit 3
  fi
  sleep 60
done
wait $PID; RC=$?
say "bake exit $RC; $(du -sh "$OUT" | cut -f1), Nunatak $(df -g /Volumes/Nunatak | tail -1 | awk '{print $4}') GB free"
exit $RC
