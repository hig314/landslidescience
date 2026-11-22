#!/bin/sh
# Statewide multi-size required cohesion at phi 20. Resumable: rerun this to continue.
# Stop cleanly:  touch /Volumes/Nunatak/terrain_build/scoops_state/multi_phi20/STOP   (blocks in flight finish)
# Stop now:      pkill -f "multisize.py --phi 20 --out /Volumes/Nunatak/terrain_build/scoops_state/multi_phi20"
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
O=/Volumes/Nunatak/terrain_build/scoops_state/multi_phi20
rm -f "$O/STOP"
exec caffeinate -i -s /opt/anaconda3/bin/python3 "$ROOT/tools/terrain/scoops/multisize.py" --phi 20 --out "$O" \
     --classes "${CLASSES:-10,9,8,7,6}" --workers "${WORKERS:-3}" >> "$ROOT/logs/multisize_phi20.log" 2>&1
