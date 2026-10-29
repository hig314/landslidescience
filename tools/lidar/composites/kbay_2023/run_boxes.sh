#!/bin/zsh
# Whole-bay run (Hig, 2026-10-16): every box through topobathy_chain.sh (build --diag -> water -> build -> fill
# -> build), then the mosaic (each box's *_filled.tif cropped to its nominal extent, VRT, COG) and the dev viewer.
# Boxes with a BOX_DONE marker are skipped, so the script can be re-run after a failure.  Logs:
# $WORK/kbay_run.log and each box's own chain.log.  Usage: run_boxes.sh [box-id ...]   (default: all, interesting first)
set -u
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export PATH=/opt/homebrew/bin:$PATH
PY=/opt/anaconda3/bin/python3
HERE=${0:a:h}; TL=${HERE:h:h}
WORK=${LIDAR_BUILD:-/Volumes/Nunatak/lidar_build}/composite
LOG=$WORK/kbay_run.log
stamp() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"; }
ORDER=(kbay_r3c1 kbay_r2c0 kbay_r3c2 kbay_r2c2 kbay_r2c3 kbay_r2c1 kbay_r1c3 kbay_r0c3 kbay_r4c1 kbay_r3c3 kbay_r4c3 kbay_r4c2 kbay_r4c0 kbay_r4c4 kbay_r3c4 kbay_r3c0 kbay_r2c4 kbay_r1c0 kbay_r1c1 kbay_r1c2 kbay_r1c4 kbay_r0c0 kbay_r0c1 kbay_r0c2 kbay_r0c4)
if (( $# > 0 )); then ORDER=("$@"); fi
for id in $ORDER; do
  if [[ -f $WORK/$id/BOX_DONE ]]; then stamp "$id already done"; continue; fi
  stamp "$id chain start"
  if $TL/topobathy_chain.sh $HERE/boxes/$id/recipe.json --jobs 4 >> $WORK/${id}_chain.out 2>&1; then
    touch $WORK/$id/BOX_DONE; stamp "$id done"
  else
    stamp "$id FAILED (see $WORK/${id}_chain.out and $WORK/$id/*.log)"
  fi
done
# ---- mosaic of every finished box, cropped to the nominal extents
stamp "mosaic"
VER=${KBAY_VER:-v2}                      # the product version: mosaic dir, COG and dev-viewer id (v1 = 2026-10-16 morning run)
MOS=$WORK/kbay_2023_$VER; mkdir -p $MOS/crops
LIST=()
$PY - "$HERE/boxes.json" "$WORK" "$MOS/crops" <<'PYEOF'
import json, subprocess, sys, pathlib
boxes, work, crops = json.load(open(sys.argv[1])), pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
for b in boxes:
    src = work / b["id"] / "out" / f"{b['id']}.tif"          # build 3's composite (the fill folded in, the fallback in the fill's dry voids), not the fill stage's _filled.tif (2026-10-16)
    if not (work / b["id"] / "BOX_DONE").exists() or not src.exists():
        print("skip", b["id"]); continue
    x0, y0, x1, y1 = b["crop"]
    dst = crops / f"{b['id']}.tif"
    subprocess.run(["/opt/homebrew/bin/gdal_translate", "-q", "-projwin", str(x0), str(y1), str(x1), str(y0), "-of", "GTiff",
                    "-co", "TILED=YES", "-co", "COMPRESS=ZSTD", "-co", "PREDICTOR=3", "-co", "BIGTIFF=YES", str(src), str(dst)], check=True)
    print("cropped", b["id"])
PYEOF
ls $MOS/crops/*.tif > $MOS/crops.txt 2>/dev/null
if [[ ! -s $MOS/crops.txt ]]; then stamp "no boxes finished; no mosaic"; exit 1; fi
gdalbuildvrt -q -input_file_list $MOS/crops.txt $MOS/kbay_2023_topobathy_$VER.vrt
gdal_translate -q -of COG -co COMPRESS=ZSTD -co PREDICTOR=3 -co BIGTIFF=YES -co NUM_THREADS=ALL_CPUS $MOS/kbay_2023_topobathy_$VER.vrt $MOS/kbay_2023_topobathy_$VER.tif
stamp "mosaic written: $MOS/kbay_2023_topobathy_$VER.tif ($(wc -l < $MOS/crops.txt) boxes)"
# ---- dev viewer
stamp "dev viewer"
$PY $TL/dev_viewer_add.py kbay_2023_topobathy_$VER $MOS/kbay_2023_topobathy_$VER.tif --title "${KBAY_TITLE:-Kachemak Bay topobathy $VER (whole-survey run, 2 m; tidal lens)}" --year 2023 --max-zoom 16 \
  --notes "${KBAY_NOTES:-Whole KBay 2023 reclass extent in 12 boxes (16 km, 1 km build margin, cropped), the patch recipes merged: conditioned OCM/NCEI priors, water bodies with kinds, the tidal lens, Hig's mapped lakes, kelp test where the Seldovia green lidar exists, soundings export in the fill. Seams: fill domains are solved per box. v2 (2026-10-16 evening): 3DEP fallback over unflown ground (NCEI land no longer counts as a survey cell; large dry voids left to 3DEP), water-surface returns of Kenai 2008 / Homer 2018 classed water (islets, flat_below), zone anchors = ground patches above any nearby tidal level, beds below every water surface seen, fold NaN fix.}" >> $WORK/kbay_dev_viewer.log 2>&1 \
  && stamp "dev viewer: kbay_2023_topobathy_$VER published" || stamp "dev viewer FAILED (see $WORK/kbay_dev_viewer.log)"
stamp "all done"
