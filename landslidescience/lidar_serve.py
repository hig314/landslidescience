"""Range-capable static serving for the self-hosted lidar collection.

Two products, one requirement in common: HTTP range requests.

  /lidar/pmtiles/<id>.pmtiles   MapLibre reads a PMTiles archive by fetching
                                the header, then the directory, then individual
                                tile byte-spans. Without 206 responses the
                                pmtiles protocol simply does not work.

  /lidar/cog/<id>.tif           A Cloud-Optimized GeoTIFF is only "cloud
                                optimized" if the client can fetch the header
                                and then just the tiles it needs. With ranges
                                working, QGIS can open these straight off the
                                URL -- which is most of the point of hosting
                                them at all.

Django does not do this for us: as of 5.2 neither `FileResponse` nor
`django.views.static.serve` implements the Range header (verified, not
assumed). So the parsing lives here.

Deliberately minimal: single ranges only. Multi-range (`bytes=0-9,20-29`) is
legal HTTP but no PMTiles or GDAL client emits it, and honouring it would mean
building multipart/byteranges bodies for no benefit. A multi-range request
falls back to a normal 200 with the whole body, which is a permitted response.
"""

import re

from django.conf import settings
from django.http import (FileResponse, Http404, HttpResponse,
                         HttpResponseNotModified, StreamingHttpResponse)
from django.utils.http import http_date

# Dataset ids come from tools/lidar/datasets.json and are always plain slugs.
# Anchoring on this is what keeps `../` out of the path join.
SAFE_ID = re.compile(r'^[a-z0-9][a-z0-9_]{0,63}$')

_RANGE_RE = re.compile(r'^bytes=(\d*)-(\d*)$')
_CHUNK = 512 * 1024

LIDAR_DIR = settings.BASE_DIR / 'data' / 'lidar'
# The archive COGs are tens of GB and live on external storage, not in the
# repo's data/. In dev they are bind-mounted (see docker-compose.override.yml);
# in prod they are expected to live in object storage and this route is
# unused. Falls back to data/lidar/cog so a small local set also works.
COG_DIR = getattr(settings, 'LIDAR_COG_DIR', None) or (LIDAR_DIR / 'cog')


def _parse_range(header, size):
    """-> (start, end) inclusive, 'unsatisfiable', or None for 'ignore me'."""
    m = _RANGE_RE.match(header.strip())
    if not m:
        return None
    first, last = m.group(1), m.group(2)
    if first == '' and last == '':
        return None
    if first == '':
        # Suffix form: bytes=-500 means "the last 500 bytes".
        n = int(last)
        if n == 0:
            return 'unsatisfiable'
        start, end = max(0, size - n), size - 1
    else:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    if start > end or start >= size:
        return 'unsatisfiable'
    return start, end


def _chunks(fh, remaining):
    try:
        while remaining > 0:
            data = fh.read(min(_CHUNK, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data
    finally:
        fh.close()


def serve_ranged(request, path, content_type, cache_control,
                 download_name=None):
    if not path.is_file():
        raise Http404(path.name)
    stat = path.stat()
    size = stat.st_size
    # Weak-ish validator built from the two things a rebuild always changes.
    etag = f'"{stat.st_mtime_ns:x}-{size:x}"'

    if request.headers.get('If-None-Match') == etag:
        resp = HttpResponseNotModified()
        resp['ETag'] = etag
        return resp

    rng = request.headers.get('Range')
    parsed = _parse_range(rng, size) if rng else None

    if parsed == 'unsatisfiable':
        resp = HttpResponse(status=416)
        resp['Content-Range'] = f'bytes */{size}'
    elif parsed:
        start, end = parsed
        length = end - start + 1
        fh = path.open('rb')
        fh.seek(start)
        resp = StreamingHttpResponse(_chunks(fh, length), status=206,
                                     content_type=content_type)
        resp['Content-Range'] = f'bytes {start}-{end}/{size}'
        resp['Content-Length'] = str(length)
    else:
        resp = FileResponse(path.open('rb'), content_type=content_type,
                            as_attachment=bool(download_name),
                            filename=download_name)

    resp['Accept-Ranges'] = 'bytes'
    resp['ETag'] = etag
    resp['Last-Modified'] = http_date(stat.st_mtime)
    resp['Cache-Control'] = cache_control
    return resp


def _checked(directory, dataset_id, suffix):
    if not SAFE_ID.match(dataset_id):
        raise Http404('bad dataset id')
    return directory / f'{dataset_id}{suffix}'


def pmtiles(request, dataset_id):
    """The web DEM pyramid. Not immutable: a rebuild replaces the file in
    place, so this leans on the ETag rather than a year-long max-age."""
    return serve_ranged(
        request, _checked(LIDAR_DIR / 'pmtiles', dataset_id, '.pmtiles'),
        'application/vnd.pmtiles', 'public, max-age=3600')


def cog(request, dataset_id):
    return serve_ranged(
        request, _checked(COG_DIR, dataset_id, '.tif'),
        'image/tiff; application=geotiff; profile=cloud-optimized',
        'public, max-age=3600')


def catalog(request):
    """Footprints + metadata for every hosted dataset."""
    return serve_ranged(request, LIDAR_DIR / 'catalog.geojson',
                        'application/geo+json', 'public, max-age=300')


def preview(request):
    """Standalone dev page that exercises the whole hosting path."""
    from django.shortcuts import render
    return render(request, 'pages/lidar_preview.html')
