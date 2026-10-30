#!/bin/sh
# Every 15 minutes on production: file emailed game-camera photos into a
# drop (manage.py drops_mailcam; design in drops/mailcam.py).
#
# Installed as /usr/local/bin/landslidescience-mailcam.sh; root crontab:
#   */15 * * * * MAILCAM_DROP=<slug> /usr/local/bin/landslidescience-mailcam.sh >> /var/log/mailcam.log 2>&1
#
# The IMAP app password stays in /opt/monitoring/.env (mode 600), where the
# owner put it. Only the CAMFEED_* lines are read, and they reach the
# container as inherited variables (`docker exec -e NAME`), so the password
# never appears on a command line or in the web container's own env.
[ -n "$MAILCAM_DROP" ] || { echo "MAILCAM_DROP not set" >&2; exit 1; }
exec 9>/run/landslidescience-mailcam.lock
flock -n 9 || exit 0                    # previous run still going

while IFS='=' read -r k v; do
    case "$k" in CAMFEED_IMAP_*) export "$k=$v" ;; esac
done < /opt/monitoring/.env

echo "=== $(date -Is) mailcam run ==="
docker exec -e CAMFEED_IMAP_HOST -e CAMFEED_IMAP_PORT -e CAMFEED_IMAP_USER \
    -e CAMFEED_IMAP_PASSWORD landslidescience-web \
    python manage.py drops_mailcam --drop "$MAILCAM_DROP"
