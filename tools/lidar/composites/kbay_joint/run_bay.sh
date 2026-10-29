#!/bin/zsh
# The whole bay (2026-10-29): every box through run_box.sh (boxes with BOX_DONE_<tag> are skipped, so it resumes), then
# the mosaic of the boxes' build-3 products cropped to their nominal extents -> VRT -> COG -> the dev viewer.
#   run_bay.sh [tag] [box-id ...]        default tag run1, default order: the outer coast first, then the rest
#   KJ_PUB=kbay_joint_v1 run_bay.sh ...  also publishes the mosaic to the dev viewer under that id
set -u
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES PATH=/opt/homebrew/bin:$PATH
PY=/opt/anaconda3/bin/python3
HERE=${0:a:h}; TL=${HERE:h:h}
WORK=/Volumes/Nunatak/lidar_build/composite; LOG=$WORK/kbay_joint_run.log
TAG=${1:-run1}; shift || true
stamp() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a $LOG; }
ORDER=(kj_r4c1 kj_r4c2 kj_r6c2 kj_r6c3 kj_r6c4 kj_r5c3 kj_r5c2 kj_r5c1 kj_r4c3 kj_r4c4 kj_r5c4 kj_r3c1 kj_r3c2 kj_r3c3 kj_r3c4 kj_r6c1 kj_r7c1 kj_r7c2 kj_r7c3 kj_r7c4 kj_r2c1 kj_r2c2 kj_r2c3 kj_r2c4 kj_r1c1 kj_r1c2 kj_r1c3 kj_r1c4 kj_r0c1 kj_r0c2 kj_r0c3 kj_r0c4 kj_r3c5 kj_r4c5 kj_r5c5 kj_r6c5 kj_r7c5 kj_r2c5 kj_r1c5 kj_r0c5 kj_r0c0 kj_r1c0 kj_r2c0 kj_r3c0 kj_r4c0 kj_r5c0 kj_r6c0 kj_r7c0)   # 48 boxes (2026-10-29 extent): Seldovia/Bluff first, the Grewingk box (r6c3/c4), the bay, then the outer coast and the open water
if (( $# > 0 )); then ORDER=("$@"); fi
for id in $ORDER; do
  if [[ -f $WORK/$id/BOX_DONE_$TAG ]]; then stamp "$id already done"; continue; fi
  stamp "$id start"
  if zsh $HERE/run_box.sh $id $TAG >> $WORK/${id}_run.out 2>&1; then stamp "$id ok"; else stamp "$id FAILED or gated (see $WORK/${id}_run.out)"; fi
done
# ---- mosaic of every box that finished (gated or not: the gate decides publishing, not the mosaic)
stamp "mosaic"
MOS=$WORK/kbay_joint_$TAG; mkdir -p $MOS/crops
$PY - "$HERE/boxes.json" "$WORK" "$MOS" "$TAG" <<'PYEOF'
import json, sys, subprocess, pathlib
boxes, work, mos, tag = json.load(open(sys.argv[1])), pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]), sys.argv[4]
crops = []
for b in boxes:
    src = work / b['id'] / f'out_{tag}' / f"{b['id']}.tif"
    if not src.exists(): print(b['id'], 'no product'); continue
    x0, y0, x1, y1 = b['crop']; dst = mos / 'crops' / f"{b['id']}.tif"
    subprocess.run(['gdal_translate', '-q', '-projwin', str(x0), str(y1), str(x1), str(y0), '-co', 'COMPRESS=ZSTD', '-co', 'TILED=YES', str(src), str(dst)], check=True)
    crops.append(str(dst))
subprocess.run(['gdalbuildvrt', '-q', str(mos / 'mosaic.vrt')] + crops, check=True)
subprocess.run(['gdal_translate', '-q', '-of', 'COG', '-co', 'COMPRESS=ZSTD', '-co', 'BIGTIFF=IF_SAFER', str(mos / 'mosaic.vrt'), str(mos / f'kbay_joint_{tag}.tif')], check=True)
print(len(crops), 'boxes in the mosaic')
PYEOF
if [[ -n ${KJ_PUB:-} ]]; then
  stamp "publish $KJ_PUB"
  (cd $TL && $PY dev_viewer_add.py $KJ_PUB $MOS/kbay_joint_$TAG.tif --title "Kachemak Bay point-first topobathy ($TAG)" --year 2023 --max-zoom 16 \
     --work /Volumes/Nunatak/lidar_build/dev_viewer --notes "Whole-bay point-first build, BAY_SOURCES.md (2026-10-29): every lidar from its returns, beds from the multibeam, BAGs and compilations, Chugachmiut as a cleaned DTM, 3DEP where nothing saw the ground." > $MOS/publish.log 2>&1)
fi
stamp "done"
