FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# libexpat1: runtime shared lib the rasterio wheel's bundled GDAL links
# against (python:slim ships without it).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python manage.py collectstatic --noinput

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
# --timeout 600: the default 30 s killed the worker mid-upload — a 67 MB GeoTIFF
# over a 1.5 MB/s home uplink takes ~45 s to arrive and the sync worker is
# still reading the body. Editor uploads are rare, so tying up one of two
# workers for the transfer is acceptable; the alternative (Caddy buffering the
# body with request_buffers) lives in the monitoring stack's Caddyfile.
CMD ["gunicorn", "landslidescience.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--timeout", "600", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
