#!/bin/sh
# Once a day on production: the outside-service probe with --basemaps, which
# also asks for one tile from every outside URL in the basemap table
# (basemaps.js), so a withdrawn or renamed service is found even if nobody
# listed it in probe_services.py. Results go to the same file as the
# 30-minute probe (/traffic/services/ shows them as "Basemap: ...").
#
# Install: cp tools/ops/probe_daily_cron.sh /usr/local/bin/landslidescience-probe-daily.sh
# root crontab (minute 26 keeps it off the 30-minute run at 11 and 41):
#   26 14 * * * /usr/local/bin/landslidescience-probe-daily.sh >> /var/log/landslidescience-probe.log 2>&1
out=$(docker exec landslidescience-web python tools/ops/probe_services.py --basemaps --out data/service_status.json 2>&1)
if [ $? -ne 0 ]; then
    echo "=== $(date -Is) daily ==="
    echo "$out" | grep -v '^ok '
fi
