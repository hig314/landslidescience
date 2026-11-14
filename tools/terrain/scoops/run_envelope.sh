#!/bin/bash
# The envelope across windows and surface sizes, r0/dx about 6.5 in every class, cheapest
# class first so whole classes finish early. Resumes: a finished npz is skipped.
# Windows were picked by mountain range and at random, with no landslide data.
PY=/opt/anaconda3/bin/python3
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT=/Volumes/Nunatak/terrain_build/scoops_state/envelope/windows
WINDOWS="chugach:-148.81,61.38 coast:-135.6072,59.2779 stelias:-141.5778,60.3569 kodiak:-153.2389,57.5068 alaskarange:-150.5,63.1 brooks:-151,68"
#        v0   dx  patches (0 = the whole 150 km window)
CLASSES="1e10:450:0 1e9:200:0 1e7:45:12 1e6:20:12 1e8:100:0"
mkdir -p "$OUT"
for c in $CLASSES; do
  IFS=: read -r v0 dx np <<< "$c"
  for w in $WINDOWS; do
    name=${w%%:*}; ll=${w#*:}
    logv=$($PY -c "import math;print(round(math.log10($v0)))")
    [ -f "$OUT/${name}_v${logv}_dx${dx}.npz" ] && continue
    $PY "$HERE/envelope.py" --name "$name" --lonlat="$ll" --size 150000 --v0 "$v0" --dx "$dx" --patches "$np" --workers 6 --out "$OUT" 2>&1 | grep -v -E "NotGeoreferenced|DatasetReader" | cut -c1-400
  done
done
echo ALLDONE
