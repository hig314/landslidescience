#!/bin/sh
# Fine-lattice references with winners recorded, from a frozen copy of the code so that
# edits to scoops_dev/ cannot recompile the core under a queued run. $1 = class, $2 = block km, rest = name=lon,lat
D="$(cd "$(dirname "$0")" && pwd)"; ROOT="$D/../../.."
P=/Volumes/Nunatak/terrain_build/scoops_state/multi_pilot
K=$1; KM=$2; shift 2
for s in "$@"; do
  name=${s%%=*}; at=${s#*=}
  caffeinate -i -s /opt/anaconda3/bin/python3 "$D/multisize.py" --phi 20 --out $P --classes $K --workers 1 --method fine \
     --at=$at --block-km $KM --tag _finerec_$name > "$ROOT/logs/ms_ref${K}_$name.log" 2>&1
done
