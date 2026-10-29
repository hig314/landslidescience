#!/bin/zsh
# One joint box, end to end (2026-10-29):  units (shared cache, this box's window)  ->  joint.py all (fuse + layer)
#   ->  topobathy_chain.sh  ->  water_review.py  ->  gate.py (invariant rows + this box's sites; per-box gate dir)
#   ->  archive out_<tag>/ with the logs.   Usage: run_box.sh <box-id> [tag]      (tag default: run1)
# Refuses to start with < 12 GB free on the boot disk or a Docker build running (the fill needs ~22 GB RAM).
set -e
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES PATH=/opt/homebrew/bin:$PATH
PY=/opt/anaconda3/bin/python3
HERE=${0:a:h}; TL=${HERE:h:h}; cd $TL
BOX=$1; TAG=${2:-run1}
RECIPE=$HERE/boxes/$BOX/recipe.json; [[ -f $RECIPE ]] || RECIPE=$HERE/crops/$BOX/recipe.json; [[ -f $RECIPE ]] || { echo "no recipe for $BOX (make_boxes.py / make_crop.py)"; exit 1; }
WORK=/Volumes/Nunatak/lidar_build/composite/$BOX; JOUT=$($PY -c "import json,sys; print(json.load(open(sys.argv[1]))['joint']['out'])" $RECIPE)
mkdir -p $WORK $JOUT; LOG=$WORK/chain.log
stamp() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $BOX $TAG: $1" | tee -a $LOG; }
for v in /Volumes/Nunatak /Volumes/Powder; do [[ -d $v/lidar_build ]] || { echo "$v not mounted"; exit 1; }; done
FREE=$(df -g / | awk 'NR==2{print $4}'); (( FREE >= 12 )) || { stamp "boot disk $FREE GB free -- refusing"; exit 1; }
for v in /Volumes/Nunatak /Volumes/Powder; do F=$(df -g $v | awk 'NR==2{print $4}'); (( F >= 15 )) || { stamp "$v has $F GB free -- refusing"; exit 1; }; done
pgrep -f "docker (compose .*)?build\b|docker buildx build|docker build " >/dev/null && { stamp "a Docker build is running -- refusing"; exit 1; }   # not Docker Desktop's own com.docker.build daemon
WIN=($($PY -c "import json,sys; print(*json.load(open(sys.argv[1]))['joint']['units']['win'])" $RECIPE))
stamp "units ($WIN)";       $PY topobathy/joint_pilot.py units --recipe $RECIPE --workers 4 --win $WIN > $JOUT/units.log 2>&1
stamp "joint fuse + layer"; $PY topobathy/joint.py all $RECIPE > $JOUT/joint.log 2>&1
stamp "composite chain";    zsh topobathy_chain.sh $RECIPE --jobs 4
stamp "review";             $PY topobathy/water_review.py $RECIPE > $WORK/review.log 2>&1 || stamp "review failed (not fatal)"
stamp "gate";               mkdir -p $WORK/gate
if $PY topobathy/gate.py $WORK/out --tag $TAG --gate-dir $WORK/gate --save > $WORK/gate.log 2>&1; then stamp "gate passed"; else stamp "GATE FAILED (see $WORK/gate.log)"; GATE_FAIL=1; fi
A=$WORK/out_$TAG; rm -rf $A; mkdir -p $A/logs
cp -pl $WORK/out/*.tif $WORK/out/*.geojson $WORK/out/*.json $A/ 2>/dev/null || true   # hard links: the archive costs no space (a later run rewrites out/ by new files)
cp -p $WORK/*.log $JOUT/*.log $A/logs/ 2>/dev/null || true; cp -p $RECIPE $A/recipe.json
stamp "archived $A"
[[ -z $GATE_FAIL ]] && touch $WORK/BOX_DONE_$TAG
stamp "done"
