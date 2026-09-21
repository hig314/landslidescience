#!/bin/zsh
# Two mask-tool instances on the same Portage window, side by side:
#   8765  portage_legacy  Hig's painted masks (the regression target)
#   8766  portage_axes    three-axis structure (dates, classes, sigma), no hand masks; paint here
# Paint saves to $LIDAR_BUILD/composite/<id>/masks/<layer>_paint.tif and is
# picked up by the tool on restart and by composite.py build.
cd "$(dirname "$0")/.."
PY=/opt/anaconda3/bin/python3
WIN=${WIN:-12096,14144,21440,23488}
pkill -f "mask_tool/server.py" 2>/dev/null; sleep 1
$PY mask_tool/server.py composites/portage_legacy/recipe.json --window $WIN --port 8765 > /tmp/masktool_legacy.log 2>&1 &
$PY mask_tool/server.py composites/portage_axes/recipe.json   --window $WIN --port 8766 > /tmp/masktool_auto.log 2>&1 &
sleep 15
echo "painted  http://127.0.0.1:8765/"
echo "auto     http://127.0.0.1:8766/"
