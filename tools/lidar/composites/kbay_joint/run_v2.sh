#!/bin/zsh
# run_v2.sh : VERSION 2 of the bay composite, as a LOW-PRIORITY background queue (Hig 2026-11-23: "develop and then run as a low-priority
# background task when time permits"; "starting a queue with the fixed rules ... including the fixed sounding cloud").
# The queue is the FILE $W/run_v2.queue, one job a line, read again after every job so that jobs can be APPENDED while it runs:
#     <box> chain     chain cm23 (reads the corrected soundings, kbay_soundings r2) then solve s4 on it
#     <box> solve     solve s4 only, on the box's cm22 chain (a box whose chain's inputs and rules are unchanged: only solve rules moved)
#     PUB pub         publish the boxes solved so far to the dev viewer as kbay_joint_s4 (publish_v2.sh; skipped under 25 GB of boot disk)
#     # ...           a comment;   a job is skipped when its product already exists (resumable)
# A solve counts as done only if its solve.json is NEWER than the time in $W/V2_CODE_T (the last change to the solve's rules): an earlier
# s4 solve is redone.  Move that time forward whenever a solve rule changes.
# v1's products stay: out_cm22 / out_s3 and the published file are not written here (chain archives to out_cm23, solves go to out_s4).
# Launch:  cd $W && nohup caffeinate -i -s zsh run_v2.sh > /dev/null 2>&1 &        Stop after the current job: touch $W/run_v2.STOP
W=/Volumes/Nunatak/lidar_build/composite; TL=/Users/Hig/Claude_projects/landslidescience-topobathy/tools/lidar; PY=/opt/anaconda3/bin/python3
LOG=$W/run_v2.log; RB=$TL/composites/kbay_joint/run_box.sh; DIFF=$W/run_v2_diff.txt; Q=$W/run_v2.queue; NICE=15
export PYTHONPATH=$W/guard
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2 NUMEXPR_NUM_THREADS=2      # every solve needs these (2026-11-23: without them tiles ran 5-7x slower)
stamp() { echo "[$(date '+%F %T')] $1" >> $LOG; }
pgrep -f "memguard2[.]sh" >/dev/null || { nohup zsh $W/memguard2.sh 40 4 >/dev/null 2>&1 &; stamp "run_v2: memguard2 restarted"; }
touch -t $(tr -d ' :-' < $W/V2_CODE_T | cut -c1-12) $W/V2_CODE_T.stamp       # V2_CODE_T holds "YYYY-MM-DD HH:MM"
stamp "run_v2 start (solves older than $(cat $W/V2_CODE_T) are redone)"
typeset -A seen
while true; do
  [[ -f $W/run_v2.STOP ]] && { stamp "run_v2 stopped by $W/run_v2.STOP"; rm -f $W/run_v2.STOP; exit 0; }
  job=""; while read -r b mode rest; do [[ -z $b || $b == \#* ]] && continue; [[ -n ${seen[$b:$mode]} ]] && continue; job="$b $mode"; break; done < $Q
  [[ -z $job ]] && break
  b=${job% *}; mode=${job#* }; seen[$b:$mode]=1; B=kj_$b
  if [[ $b == PUB ]]; then
    FB=$(df -g / | awk 'NR==2{print $4}'); if (( FB < 25 )); then stamp "PUB skipped: boot disk has $FB GB free"; continue; fi
    zsh $W/publish_v2.sh > $W/publish_v2.out 2>&1; stamp "published: $(tail -1 $W/republish_v2.log | cut -c1-160)"; continue
  fi
  F=$(df -g /Volumes/Nunatak | awk 'NR==2{print $4}'); if (( F < 20 )); then stamp "STOPPED before $B: /Volumes/Nunatak has $F GB free"; exit 1; fi
  while (( $(vm_stat | awk '/Pages free|Pages inactive|Pages speculative/ {s+=$NF} END {printf "%d", s*16384/2^30}') < 20 )); do stamp "$B waits: under 20 GB of memory free"; sleep 120; done
  CT=cm22
  if [[ $mode == chain ]]; then
    CT=cm23
    if [[ -f $W/$B/out_cm23/$B.tif ]] && tail -1 $W/$B/chain.log | grep -q "cm23: done"; then stamp "$B chain cm23 already done"
    else
      stamp "$B chain cm23 start"
      nice -n $NICE zsh $RB $B cm23 > $W/$B.cm23.out 2>&1; rc=$?
      stamp "$B chain rc $rc: $(tail -3 $W/$B/chain.log | tr '\n' ' ' | cut -c1-260)"
      [[ -f $W/$B/gate.log ]] && stamp "$B gate: $(tail -1 $W/$B/gate.log | cut -c1-120)"
    fi
  elif [[ $mode != solve ]]; then stamp "$B: unknown mode '$mode' -- skipped"; continue
  fi
  if grep -q "\"tag\": \"$CT\"" $W/$B/out_s4/solve.json 2>/dev/null && [[ $W/$B/out_s4/solve.json -nt $W/V2_CODE_T.stamp ]]; then stamp "$B solve s4 already done"
  elif [[ -f $W/$B/out_$CT/$B.tif ]]; then
    stamp "$B solve s4 (on $CT) start"
    (cd $TL && nice -n $NICE $PY topobathy/solve_box.py $B --tag $CT --out-tag s4 --deep-grid 16 --snd-lakes > $W/$B.solve_s4.out 2>&1); rc=$?
    stamp "$B solve rc $rc: $(grep -E 'deep pass|done:' $W/$B.solve_s4.out | tr '\n' ' ' | cut -c1-300)"
    nf=$(grep -c "product stands" $W/$B/out_s4/solve.log 2>/dev/null); (( nf > 0 )) && stamp "$B: $nf solve tile(s) FAILED and kept the product -- see $W/$B/out_s4/solve.log"
    echo "[$(date '+%F %T')]" >> $DIFF; $PY $W/box_diff.py $B s4 s3 >> $DIFF 2>&1
  else stamp "$B: no $CT product -- solve skipped"
  fi
done
stamp "run_v2 queue empty"
