#!/bin/zsh
# The topobathy chain for one recipe (2026-10-15, with the water-bodies stage):
#   build --diag  (per-layer class rasters)  ->  water  (observations, bodies, edits)
#   -> build  (bed cutoffs at each body's level)  ->  shore  (the shoreline stage: labels, levels, bank slope)
#   -> fill  (the body's kind and depth, the stage's shore)  ->  build
# Usage: topobathy_chain.sh composites/<id>/recipe.json [extra build args]
# Logs to the recipe's work dir (chain.log + stage logs).  Builds on this Mac need the fork-safety env.
set -e
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export PATH=/opt/homebrew/bin:$PATH
PY=/opt/anaconda3/bin/python3
HERE=${0:a:h}
RECIPE=$1; shift
ID=$($PY -c "import json,sys; print(json.load(open(sys.argv[1]))['id'])" "$RECIPE")
WORK=${LIDAR_BUILD:-/Volumes/Nunatak/lidar_build}/composite/$ID
mkdir -p "$WORK"
LOG=$WORK/chain.log
stamp() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"; }
cd "$HERE"
# a previous run's stage products must not feed this run's first build (2026-10-17: build 1 read the LAST run's
# intertidal.tif / water bodies through the recipes' "$out/" steps, so the diag classes -- and the water stage
# built on them -- carried the old zone)
rm -f "$WORK"/out/intertidal.tif "$WORK"/out/intertidal_anchors.tif "$WORK"/out/water_bodies.tif "$WORK"/out/water_bodies.geojson \
      "$WORK"/out/water_obs.geojson "$WORK"/out/water.json "$WORK"/out/fill.tif "$WORK"/out/fill_surface.tif "$WORK"/out/fill_dryvoid.tif \
      "$WORK"/out/fill_mask.tif "$WORK"/out/fill_prior.tif "$WORK"/out/fill_sigma.tif \
      "$WORK"/out/shore_label.tif "$WORK"/out/water_blind.tif "$WORK"/out/shore_level.tif "$WORK"/out/shore_slope.tif "$WORK"/out/shore_band.tif "$WORK"/out/shoreline.geojson "$WORK"/out/shore.json
# 3DEP's hydroflattened lakes as water (topobathy/flat_water.py), where the recipe's context layer reads it
# (rebuilt when flat_water.py is newer than it: r4c3 kept a 2026-10-18 mask without the sea code and its 0 m plateau)
if grep -q "water_3dep.tif" "$RECIPE" && { [[ ! -f "$WORK/obs/water_3dep.tif" ]] || [[ topobathy/flat_water.py -nt "$WORK/obs/water_3dep.tif" ]]; }; then
  mkdir -p "$WORK/obs"
  stamp "flat water (3DEP)"; $PY topobathy/flat_water.py "$RECIPE" --dem /Volumes/Nunatak/lidar_build/ctx_3dep/all.vrt --out "$WORK/obs/water_3dep.tif" > "$WORK/flat_water.log" 2>&1
fi
stamp "build 1 (diag)";  $PY composite.py build "$RECIPE" --diag "$@" > "$WORK/build1.log" 2>&1
stamp "water";           $PY composite.py water "$RECIPE"            > "$WORK/water.log"  2>&1
stamp "build 2";         $PY composite.py build "$RECIPE" "$@"        > "$WORK/build2.log" 2>&1
stamp "shore";           $PY composite.py shore "$RECIPE"            > "$WORK/shore.log"  2>&1
stamp "fill";            $PY composite.py fill  "$RECIPE"             > "$WORK/fill1.log"  2>&1
stamp "build 3";         $PY composite.py build "$RECIPE" "$@"        > "$WORK/build3.log" 2>&1
stamp "done"
