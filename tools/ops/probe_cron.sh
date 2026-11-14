#!/bin/sh
# Every 30 minutes on production: ask each outside service the map reads
# whether it is answering (tools/ops/probe_services.py), and keep the result
# where /traffic/services/ reads it.
#
# Install: cp tools/ops/probe_cron.sh /usr/local/bin/landslidescience-probe.sh
# root crontab:
#   11,41 * * * * /usr/local/bin/landslidescience-probe.sh >> /var/log/landslidescience-probe.log 2>&1
#
# Only state changes and failures are logged, so the log stays small.
out=$(docker exec landslidescience-web python tools/ops/probe_services.py --out data/service_status.json 2>&1)
if [ $? -ne 0 ]; then
    echo "=== $(date -Is) ==="
    echo "$out" | grep -v '^ok '
fi
