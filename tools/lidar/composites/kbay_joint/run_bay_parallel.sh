#!/bin/zsh
# The whole bay with N boxes at once (2026-11-03, the contour model's first full map): each worker takes the next box from one shared
# queue through run_box.sh (boxes with BOX_DONE_<tag> are skipped, so it resumes), then the feathered mosaic, the merged review files, and the dev viewer.
#   run_bay_parallel.sh <tag> <workers> <dev id> <dev title> [box-id ...]        default order as run_bay.sh
# A fill needs ~22 GB of RAM: 2 workers on this Mac (68 GB).  A worker stops taking boxes when the boot disk or a data volume is short (run_box.sh's own limits).
set -u
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES PATH=/opt/homebrew/bin:$PATH
PY=/opt/anaconda3/bin/python3
HERE=${0:a:h}; TL=${HERE:h:h}
WORK=/Volumes/Nunatak/lidar_build/composite; LOG=$WORK/kbay_joint_run.log
TAG=$1; WORKERS=$2; PUB=$3; TITLE=$4; shift 4
stamp() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a $LOG; }
ORDER=(kj_r4c1 kj_r4c2 kj_r6c2 kj_r6c3 kj_r6c4 kj_r5c3 kj_r5c2 kj_r5c1 kj_r4c3 kj_r4c4 kj_r5c4 kj_r3c1 kj_r3c2 kj_r3c3 kj_r3c4 kj_r6c1 kj_r7c1 kj_r7c2 kj_r7c3 kj_r7c4 kj_r2c1 kj_r2c2 kj_r2c3 kj_r2c4 kj_r1c1 kj_r1c2 kj_r1c3 kj_r1c4 kj_r0c1 kj_r0c2 kj_r0c3 kj_r0c4 kj_r3c5 kj_r4c5 kj_r5c5 kj_r6c5 kj_r7c5 kj_r2c5 kj_r1c5 kj_r0c5 kj_r0c0 kj_r1c0 kj_r2c0 kj_r3c0 kj_r4c0 kj_r5c0 kj_r6c0 kj_r7c0)
if (( $# > 0 )); then ORDER=("$@"); fi
Q=$WORK/.queue_$TAG; rm -f $Q; print -l $ORDER > $Q; LOCK=$Q.lock; rm -rf $LOCK

next_box() {        # pop one line under a mkdir lock
  until mkdir $LOCK 2>/dev/null; do sleep 1; done
  head -1 $Q; sed -i '' 1d $Q
  rmdir $LOCK
}
worker() {
  while true; do
    if [[ -n ${STOP_AT:-} ]] && (( $(date +%s) >= STOP_AT )); then stamp "stop time reached: no new box"; break; fi     # STOP_AT (epoch): a night's run ends with the mosaic of what is done
    id=$(next_box); [[ -z $id ]] && break
    if [[ -f $WORK/$id/BOX_DONE_$TAG ]]; then stamp "$id already done"; continue; fi
    stamp "$id start"
    for try in 1 2 3 4 5 6; do                    # run_box.sh's guards can refuse for a passing reason (a Docker build, low disk): wait and try again
      zsh $HERE/run_box.sh $id $TAG >> $WORK/${id}_run.out 2>&1; rc=$?
      tail -1 $WORK/$id/chain.log 2>/dev/null | grep -q refusing || break
      stamp "$id refused, try $try: $(tail -1 $WORK/$id/chain.log | cut -c1-90)"; sleep 600
    done
    if (( rc == 0 )) && [[ -d $WORK/$id/out_$TAG ]]; then
      if [[ -f $WORK/$id/BOX_DONE_$TAG ]]; then stamp "$id ok"; else stamp "$id GATED: $(tail -1 $WORK/$id/gate.log 2>/dev/null)"; fi
    else stamp "$id FAILED (see $WORK/${id}_run.out)"; fi
  done
}
for i in {1..$WORKERS}; do worker & sleep 20; done
wait
stamp "mosaic (feathered)"
MOS=$WORK/kbay_joint_$TAG; mkdir -p $MOS/crops
$PY $HERE/mosaic_blend.py "$HERE/boxes.json" "$WORK" "$MOS" "$TAG"
$PY - "$WORK" "$MOS" "$TAG" <<'PYEND'
import glob, json, sys
W, M, tag = sys.argv[1:]
for name in ("shore_contours", "cast_review", "cast_lines", "rock_review"):
    F = []
    for p in sorted(glob.glob(f"{W}/kj_*/out_{tag}/{name}.geojson")):
        box = p.split("/")[-3]; g = json.load(open(p))
        for f in g["features"]:
            f["properties"]["box"] = box; F.append(f)
    json.dump(dict(type="FeatureCollection", crs=dict(type="name", properties=dict(name="EPSG:6334")), features=F), open(f"{M}/{name}.geojson", "w")); print(name, len(F))
E = []
for p in sorted(glob.glob(f"{W}/kj_*/out_{tag}/edit_check.json")):
    E += [dict(e, box=p.split("/")[-3]) for e in json.load(open(p))]
json.dump(E, open(f"{M}/edit_check.json", "w")); print("edit checks", len(E), "not honoured", sum(not e["ok"] for e in E))
PYEND
stamp "publish $PUB"
(cd $TL && $PY dev_viewer_add.py $PUB $MOS/kbay_joint_$TAG.tif --title "$TITLE" --year 2023 --max-zoom 16 --work /Volumes/Nunatak/lidar_build/dev_viewer \
   --notes "Whole-bay build under the contour shore model (BAY_SOURCES 8.6, SHORE_REVIEW_WORKFLOW.md)." > $MOS/publish.log 2>&1)
stamp "done: $(tail -1 $MOS/publish.log | cut -c1-110)"
