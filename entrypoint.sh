#!/bin/sh
set -e
mkdir -p /app/data
python manage.py migrate --noinput
exec "$@"
