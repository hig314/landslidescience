#!/bin/sh
# Concatenate the inventory map's sibling modules, in load order, for
# map_boot_check.js. They publish onto `window`, which that harness bridges
# into the global scope the way a browser would.
set -eu
HOST=${1:-https://landslidescience.org}
rm -f /tmp/map_deps.js
curl -s "$HOST/inventory/" \
  | grep -oE '/static/inventory/js/[a-z_]+\.[a-f0-9]*\.js' \
  | grep -v '/map\.' \
  | while read -r p; do curl -s "$HOST$p" >> /tmp/map_deps.js; echo ";" >> /tmp/map_deps.js; done
wc -c < /tmp/map_deps.js | xargs echo "wrote /tmp/map_deps.js bytes:"
