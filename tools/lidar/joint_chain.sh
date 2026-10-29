#!/bin/zsh
# THE JOINT CHAIN (2026-10-22): the point-first build end to end for one recipe with a "joint" block.
#   units (every lidar survey's returns per 1 km unit, joint_pilot.py)  ->  mosaic  ->  joint.py fuse + layer
#   ->  topobathy_chain.sh (flat water, build 1 diag, water bodies, build 2, fill, build 3)  ->  dev viewer
# Usage: joint_chain.sh composites/joint_v2/recipe.json [publish-id]      logs: $JOINT_OUT/*.log + the chain's
# JOINT_WIN must be km-aligned so the pilot's finished units are reused (copied in, never recomputed).
set -e
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES PATH=/opt/homebrew/bin:$PATH
PY=/opt/anaconda3/bin/python3
HERE=${0:a:h}; cd $HERE
RECIPE=$1; PUB=${2:-}
export JOINT_WIN=${JOINT_WIN:-597000,6602000,621000,6622000}
export JOINT_OUT=${JOINT_OUT:-$($PY -c "import json,sys; print(json.load(open(sys.argv[1]))['joint']['out'])" "$RECIPE")}
for v in /Volumes/Nunatak /Volumes/Powder; do [[ -d $v/lidar_build ]] || { echo "$v not mounted"; exit 1; }; done
mkdir -p $JOINT_OUT/units
stamp() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a $JOINT_OUT/chain.log; }
if [[ -d /Volumes/Powder/lidar_build/joint_pilot/units && ! -f $JOINT_OUT/units/.copied ]]; then
  stamp "reuse the pilot's units"; rsync -a --ignore-existing /Volumes/Powder/lidar_build/joint_pilot/units/ $JOINT_OUT/units/ && touch $JOINT_OUT/units/.copied
fi
stamp "units ($JOINT_WIN)";  $PY topobathy/joint_pilot.py units     --workers 4 > $JOINT_OUT/units.log 2>&1
stamp "units 2019";          $PY topobathy/joint_pilot.py units2019 --workers 4 > $JOINT_OUT/units2019.log 2>&1
stamp "mosaic";              $PY topobathy/joint_pilot.py mosaic                > $JOINT_OUT/mosaic.log 2>&1
stamp "joint fuse + layer";  $PY topobathy/joint.py all "$RECIPE"               > $JOINT_OUT/joint.log 2>&1
stamp "composite chain";     zsh topobathy_chain.sh "$RECIPE" --jobs 4
if [[ -n $PUB ]]; then
  ID=$($PY -c "import json,sys; print(json.load(open(sys.argv[1]))['id'])" "$RECIPE")
  stamp "publish $PUB";      $PY dev_viewer_add.py $PUB /Volumes/Nunatak/lidar_build/composite/$ID/out/$ID.tif --title "Joint stage v2, Grewingk box (run 7)" --year 2023 --max-zoom 16 \
    --work /Volumes/Powder/lidar_build/dev_viewer --notes "Point-first build v2 (2026-10-22): every lidar return, the multibeam, BAGs, OCM, NCEI, CGOA, the SfM bare model and the Dixon ice fused by topobathy/joint.py (support, sigma from gridding loss, epoch rule, cross-check, over-water) into one layer; 3DEP only where the surveys saw nothing; water bodies and the fill as run 6, plus: a flat pond within the tide's reach inside the intertidal zone is tidal, 3DEP's hydroflattened rims are water, and 3DEP ground below water seen nearby is void." > $JOINT_OUT/publish.log 2>&1
fi
stamp done
