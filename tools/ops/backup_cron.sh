#!/bin/sh
# Nightly on production: back up what exists only on the droplet to the
# private R2 bucket (manage.py site_backup; what and why in its docstring).
#
# Install: cp tools/ops/backup_cron.sh /usr/local/bin/landslidescience-backup.sh
# root crontab:
#   20 10 * * * /usr/local/bin/landslidescience-backup.sh >> /var/log/landslidescience-backup.log 2>&1
#   (10:20 UTC = about 01:20 in Alaska)
#
# The PostGIS landslide tables live in the monitoring stack's database, which
# the web container cannot dump (no pg_dump), so this script dumps the whole
# `landslides` database from the database container into data/backup_staging/
# and site_backup uploads and removes it. The password is read from the web
# container's environment and never appears on a command line.
set -u
exec 9>/run/landslidescience-backup.lock
flock -n 9 || exit 0
echo "=== $(date -Is) backup ==="
STAGE=/opt/landslidescience/data/backup_staging
mkdir -p "$STAGE"
f="$STAGE/landslides-$(date -u +%F).sql.gz"
if docker exec landslidescience-web sh -c 'printf "%s" "$TETHYS_DB_PASSWORD"' \
     | docker exec -i monitoring-tethys_db-1 sh -c \
       'read -r PGPASSWORD; export PGPASSWORD; pg_dump -h 127.0.0.1 -U tethys -d landslides --no-owner' \
     | gzip > "$f.part" && [ "$(stat -c %s "$f.part")" -gt 100000 ]; then
    mv "$f.part" "$f"
else
    echo "pg_dump FAILED or implausibly small; nothing staged" >&2
    rm -f "$f.part"
fi
docker exec landslidescience-web python manage.py site_backup
