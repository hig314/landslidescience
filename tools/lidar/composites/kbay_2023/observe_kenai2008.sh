#!/bin/zsh
# Kenai 2008 point-cloud evidence (observe_points.py stats) for every box the survey reaches (north of ~59.59 N:
# rows r3 and r4 of the v5 layout), each box's crop plus 200 m, into obs/kenai_2008/<box>/; then one VRT.
# Usage: observe_kenai2008.sh   (skips boxes whose cellstats.tif exists)
set -e
export PATH=/opt/homebrew/bin:$PATH OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
PY=/opt/anaconda3/bin/python3; TL=${0:a:h:h:h}; OUT=/Volumes/Nunatak/lidar_build/obs/kenai_2008; mkdir -p $OUT
cd $TL
for id in $($PY -c "import json; print(' '.join(b['id'] for b in json.load(open('composites/kbay_2023/boxes.json')) if b['crop'][3] > 6606000))"); do
  [[ -f $OUT/$id/cellstats.tif ]] && { echo "$id done"; continue; }
  B=$($PY -c "import json; b=[b for b in json.load(open('composites/kbay_2023/boxes.json')) if b['id']=='$id'][0]['crop']; print(f'{b[0]-200},{b[1]-200},{b[2]+200},{b[3]+200}')")
  echo "[$(date +%H:%M:%S)] $id $B"
  $PY observe_points.py stats --survey kenai2008 --bounds $B --out $OUT/$id --res 2 --jobs 4 > $OUT/$id.log 2>&1 || { echo "$id FAILED"; tail -3 $OUT/$id.log; }
  tail -1 $OUT/$id.log
done
ls $OUT/*/cellstats.tif > $OUT/list.txt && gdalbuildvrt -q -input_file_list $OUT/list.txt $OUT/cellstats.vrt && echo "[$(date +%H:%M:%S)] vrt: $(wc -l < $OUT/list.txt) boxes"
