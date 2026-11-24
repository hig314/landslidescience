#!/bin/zsh
# publish_v2.sh : the boxes of VERSION 2 solved so far (out_s4/solve.json newer than V2_CODE_T) to the DEV viewer as kbay_joint_s4, incrementally
# (publish_inc.py --tag s4).  Holes where a box is not yet on the version 2 code.  v1's layer kbay_joint_s3 is left as it is.
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES PATH=/opt/homebrew/bin:$PATH
PY=/opt/anaconda3/bin/python3; TL=/Users/Hig/Claude_projects/landslidescience-topobathy/tools/lidar
W=/Volumes/Nunatak/lidar_build/composite; MOS=/Volumes/Powder/lidar_build/kbay_joint_s4; LOG=$W/republish_v2.log
mkdir -p $MOS
$PY - <<PYEOF || { echo "[$(date '+%F %T')] NO VERSION 2 BOXES" >> $LOG; exit 1; }
import json, pathlib, os, sys
W = pathlib.Path("$W"); t = os.path.getmtime("$W/V2_CODE_T.stamp"); keep = []
for b in json.load(open("$TL/composites/kbay_joint/boxes.json")):
    j = W / b["id"] / "out_s4" / "solve.json"
    if j.exists() and j.stat().st_mtime > t and (W / b["id"] / "out_s4" / (b["id"] + ".tif")).exists(): keep.append(b)
json.dump(keep, open("$MOS/boxes_latest.json", "w")); open("$MOS/boxes_latest.txt", "w").write(" ".join(b["id"][3:] for b in keep))
sys.exit(0 if keep else 1)
PYEOF
n=$(wc -w < $MOS/boxes_latest.txt | tr -d ' ')
(cd $TL && $PY composites/kbay_joint/publish_inc.py $MOS/boxes_latest.json $W $MOS kbay_joint_s4 --tag s4 --title "kbay_joint_s4 — version 2 in progress, holes elsewhere" --year 2023 --max-zoom 16 --notes "VERSION 2 IN PROGRESS ($n of 48 boxes; holes elsewhere). Changes from version 1 (kbay_joint_s3): NOS sheet H05194 shifted 370 m E, 420 m N (outer reef); the lake-shore rule's raised cells written (Petrof's delta); no ramp to the Grewingk ice face; Homer Harbor on the harbour rule. Boxes: $(cat $MOS/boxes_latest.txt).") || { echo "[$(date '+%F %T')] INCREMENTAL PUBLISH FAILED" >> $LOG; exit 1; }
echo "[$(date '+%F %T')] published: $n boxes; $(tail -1 $MOS/publish.log | cut -c1-100)" >> $LOG
