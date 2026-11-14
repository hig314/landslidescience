#!/bin/sh
# verify.sh <file.js> [more.js ...]   (run from the repo root)
# Every name a script uses without defining it must be a known global
# (globals_baseline.txt: browser built-ins and this site's other modules).
# Anything else is a name that was left behind in another file.
H=$(dirname "$0"); J=inventory/static/inventory/js; rc=0
for f in map.js "$@"; do
    out=$(node "$H/free.js" "$J/$f") || { echo "$f: does not parse"; rc=1; continue; }
    new=$(echo "$out" | cut -d' ' -f1 | sort -u | comm -23 - "$H/globals_baseline.txt" | tr '\n' ' ')
    wr=$(echo "$out" | grep WRITE | tr '\n' ' ')
    [ -n "$new$wr" ] && { echo "$f: UNRESOLVED $new WRITES-OUTSIDE $wr"; rc=1; } || echo "$f: ok"
done
exit $rc
