"""OPERA displacement-velocity tile proxy.

ASF's displacement portal (displacement.asf.alaska.edu) serves its OPERA
DISP-S1 velocity mosaics as a public CloudFront tile pyramid of VALUE tiles:
8-bit gray+alpha PNGs, gray 1–255 mapping linearly onto ±0.03 m/yr (their
extent.json `scale_range`), alpha = coverage, EPSG:3857, maxZoom 12. The
portal colors them client-side.

CloudFront's CORS is locked to ASF's own origin, so we proxy the raw tiles
through this view to make them same-origin: MapLibre's `operacolor`
protocol (map.js) can then canvas-decode and apply ASF's exact color ramp,
and later phases can histogram/sample the same tiles. The proxy also
isolates the upstream URL behind one constant and keeps cached areas
serving if ASF restructures.

Disk cache: data/opera_tiles/<track>/<z>/<x>/<y>.png (volume-mounted,
gitignored). Upstream 404s (no coverage — most ocean tiles) are cached as
empty `.404` marker files so we don't re-ask. ASF refreshes the mosaic
periodically; `python manage.py purge_opera_tiles` clears the cache so the
next views re-fetch (bump OPERA_TILE_V in map.js at the same time so
browser caches roll too).

Attribution: OPERA DISP-S1 © NASA/JPL; velocity mosaic service by ASF.
"""
import logging
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpResponseNotFound
from django.views.decorators.http import require_safe

log = logging.getLogger(__name__)

UPSTREAM = 'https://d3g9emy65n853h.cloudfront.net/main/{track}/vel/{z}/{x}/{y}.png'
TILES_DIR = Path(settings.BASE_DIR) / 'data' / 'opera_tiles'
TRACKS = ('asc', 'desc')
MAX_ZOOM = 12
_UA = {'User-Agent': 'landslidescience-opera-proxy/1'}

# 30 days: tiles are stable between ASF mosaic refreshes; the map.js tile URL
# carries a ?v= token that is bumped when the cache is purged, so a refresh
# rolls browser caches without waiting this out.
CACHE_HEADER = 'public, max-age=2592000'


@require_safe
def opera_tile(request, track, z, x, y):
    z, x, y = int(z), int(x), int(y)
    n = 1 << z
    if track not in TRACKS or z > MAX_ZOOM or not (0 <= x < n and 0 <= y < n):
        return HttpResponseNotFound()

    dest = TILES_DIR / track / str(z) / str(x) / f'{y}.png'
    marker = dest.with_suffix('.404')
    if dest.exists():
        resp = FileResponse(open(dest, 'rb'), content_type='image/png')
        resp['Cache-Control'] = CACHE_HEADER
        return resp
    if marker.exists():
        resp = HttpResponseNotFound()
        resp['Cache-Control'] = CACHE_HEADER
        return resp

    url = UPSTREAM.format(track=track, z=z, x=x, y=y)
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
            resp = HttpResponseNotFound()
            resp['Cache-Control'] = CACHE_HEADER
            return resp
        log.warning('opera upstream %s -> HTTP %s', url, e.code)
        return HttpResponseNotFound()   # transient upstream trouble: NOT cached
    except Exception as exc:
        log.warning('opera upstream %s failed: %s', url, exc)
        return HttpResponseNotFound()

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.png.part')
    tmp.write_bytes(body)
    tmp.rename(dest)
    resp = FileResponse(open(dest, 'rb'), content_type='image/png')
    resp['Cache-Control'] = CACHE_HEADER
    return resp
