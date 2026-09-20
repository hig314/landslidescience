"""Alaska DGGS deep-seated landslide susceptibility — tile proxy.

Alaska DGGS published a statewide deep-seated landslide susceptibility raster
as Preliminary Interpretive Report 2025-3 (Wikstrom Jones, K.M., and Larsen,
M.C., 2025, doi:10.14509/31691). It is the state's own view of where this
failure style is possible, which makes it the natural companion to the USGS
Belair et al. (2024) models already on the map -- a different method, a
different agency, same question.

The classes cross rock strength with slope, and the codes are not a ramp:

    0   any rock strength, slope < 3 deg      (drawn white upstream)
    3   strong,   10-15 deg
    5   moderate,  3-10 deg
    6   strong,   15-20 deg
    7   strong 20-30 deg, or weak 3-10 deg
    8   strong >30 deg,   or moderate 10-15 deg
    9   moderate >15 deg, or weak 10-15 deg
    10  weak, >15 deg
    --  lake, river or glacier               (drawn pale blue upstream)

WHY PROXY RATHER THAN POINT THE MAP AT THEM
-------------------------------------------
DGGS publishes this ONLY as a dynamic ArcGIS MapServer -- no tile cache
(`singleFusedMapCache: false`), no WMS extension, no ImageServer, and the
report itself ships only a PDF and a map sheet. Their whole portal was
searched on 2026-09-19; the MapServer is the only machine-readable copy.

That service is not reliable. On 2026-09-19 every operation that touches the
raster -- `export` and `identify`, full extent and a single 256 px tile, with
and without a browser User-Agent and a geoportal Referer -- returned 504 from
their nginx for over half an hour, while the service description and legend
answered in under a second and two sibling services on the same host rendered
fine. It came back on its own. A disk cache means an area a user has already
looked at keeps working through the next outage, which a direct browser source
cannot do. CORS is not the reason (a working sibling echoes our Origin back);
resilience and per-request render cost are.

ZOOM CEILING
------------
The layer carries `maxScale: 36000` and stops drawing above it. Measured
against live tiles rather than computed: z14 returns real data, z15 comes back
fully transparent. ArcGIS derives scale from PROJECTED units, so a Web Mercator
tile's scale is the same at every latitude -- z14 is 1:36,111, just inside
their limit, and z15 is 1:18,055, outside it. Hence MAX_ZOOM = 14 here and
`maxzoom: 14` on the client source, which makes MapLibre overzoom rather than
ask for blanks.

CLASS 0 IS MADE TRANSPARENT
---------------------------
Upstream paints class 0 -- "slope under 3 degrees", i.e. the places this hazard
cannot occur -- as opaque white, which is correct for a standalone map sheet
and useless as an overlay: it would white out every valley floor the inventory
sits in. It is dropped to transparent once, here, at cache time, so the tile on
disk is the tile the client draws (no canvas decode, no re-doing it per view).
The pale-blue water/glacier class is KEPT: it marks where the model does not
apply, which is information, and the overlay's opacity slider handles the rest.

Cache: data/dggs_susc_tiles/<z>/<x>/<y>.png, volume-mounted and gitignored.
A tile that comes back empty is recorded as a `.404` marker so a statewide pan
never asks twice -- most of Alaska is outside the mapped area. A 504 or other
transport failure is NOT cached; it returns an uncached 404 so the next pan
retries -- but a run of them trips a short circuit breaker, so an outage costs
one slow request rather than one per tile.

`python manage.py purge_dggs_susc_tiles` clears the cache (bump DGGS_SUSC_V in
map.js at the same time so browser caches roll too).
"""
import io
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpResponseNotFound
from django.views.decorators.http import require_safe

log = logging.getLogger(__name__)

EXPORT_URL = ('https://geoportal.dggs.dnr.alaska.gov/arcgis/rest/services/'
              'Alaska_Deep_Seated_Landslide_Susceptibility/MapServer/export')
TILES_DIR = Path(settings.BASE_DIR) / 'data' / 'dggs_susc_tiles'
MAX_ZOOM = 14                       # measured: z15 comes back fully transparent
MIN_ZOOM = 3
TILE_PX = 256
# A healthy render returns in well under a second; 20 s is generous for a slow
# one and short enough that a stuck request does not hold a browser connection
# hostage. It was 60 s until an outage during testing showed what that costs --
# see the breaker below.
TIMEOUT = 20
CACHE_HEADER = 'public, max-age=2592000'     # 30 days; the report is static

# CIRCUIT BREAKER.
#
# MapLibre asks for ~20 tiles per view and browsers allow ~6 connections per
# origin, so when DGGS is down every uncached tile waiting out its full timeout
# does not merely fail slowly -- it fills the connection pool this app's own
# API calls share, and the whole page crawls. Observed during their outage on
# 2026-09-19: every tile took the full timeout, because a transient failure is
# deliberately not cached.
#
# So after FAIL_LIMIT consecutive upstream failures, stop asking for COOLDOWN
# seconds and fail at once. One request then pays the timeout to discover they
# are back; the rest return immediately. Any success resets it.
FAIL_LIMIT = 3
COOLDOWN = 120
_breaker = {'fails': 0, 'until': 0.0}
_breaker_lock = threading.Lock()


def _breaker_open():
    with _breaker_lock:
        return _breaker['until'] > time.monotonic()


def _breaker_record(ok):
    with _breaker_lock:
        if ok:
            _breaker['fails'] = 0
            _breaker['until'] = 0.0
        else:
            _breaker['fails'] += 1
            if _breaker['fails'] >= FAIL_LIMIT:
                _breaker['until'] = time.monotonic() + COOLDOWN

# Web Mercator half-circumference, for tile -> bbox.
_R = 20037508.342789244
# Upstream paints "slope < 3 deg" pure white. Anything within this of white is
# treated as that class; the other eight colours are far away in RGB, so the
# tolerance only absorbs PNG quantisation, never a real class.
_WHITE_TOL = 6
_UA = {'User-Agent': 'landslidescience.org tile cache (+https://landslidescience.org)'}


def _bbox(z, x, y):
    span = 2 * _R / (1 << z)
    return (-_R + x * span, _R - (y + 1) * span,
            -_R + (x + 1) * span, _R - y * span)


def _upstream(z, x, y):
    return (f'{EXPORT_URL}?bbox=%f,%f,%f,%f' % _bbox(z, x, y) +
            f'&bboxSR=3857&imageSR=3857&size={TILE_PX},{TILE_PX}'
            '&format=png32&transparent=true&dpi=96&layers=show:0&f=image')


def _drop_class_zero(body):
    """White -> transparent. Returns (png_bytes, has_any_data)."""
    from PIL import Image
    import numpy as np
    im = Image.open(io.BytesIO(body)).convert('RGBA')
    a = np.array(im)
    rgb, alpha = a[..., :3].astype(np.int16), a[..., 3]
    white = (np.abs(rgb - 255).max(axis=2) <= _WHITE_TOL) & (alpha > 0)
    a[white, 3] = 0
    if not (a[..., 3] > 0).any():
        return None, False
    out = io.BytesIO()
    Image.fromarray(a, 'RGBA').save(out, 'PNG', optimize=True)
    return out.getvalue(), True


def _tile_path(z, x, y):
    return TILES_DIR / str(z) / str(x) / f'{y}.png'


def _tile_bytes(z, x, y):
    """Cached tile bytes, or None for "nothing mapped here".

    Raises on a transient upstream failure, so the caller can tell an empty
    area from an outage -- the distinction that matters with this service.
    """
    dest = _tile_path(z, x, y)
    marker = dest.with_suffix('.404')
    if dest.exists():
        return dest.read_bytes()
    if marker.exists():
        return None

    if _breaker_open():
        raise RuntimeError('upstream circuit open (DGGS failing); not asking')
    req = urllib.request.Request(_upstream(z, x, y), headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
    except Exception:
        _breaker_record(False)
        raise
    _breaker_record(True)
    # An ArcGIS error comes back as JSON or HTML with a 200 in some configs;
    # anything that is not a PNG is an outage, not an answer.
    if not raw.startswith(b'\x89PNG'):
        raise ValueError('upstream returned %r, not a PNG' % raw[:40])

    body, has_data = _drop_class_zero(raw)
    if not has_data:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.png.part.%d.%d' % (os.getpid(), threading.get_ident()))
    tmp.write_bytes(body)
    tmp.replace(dest)
    return body


@require_safe
def dggs_susc_tile(request, z, x, y):
    z, x, y = int(z), int(x), int(y)
    n = 1 << z
    if not (MIN_ZOOM <= z <= MAX_ZOOM) or not (0 <= x < n and 0 <= y < n):
        return HttpResponseNotFound()
    try:
        body = _tile_bytes(z, x, y)
    except Exception as exc:
        # Transient (their 504s). Deliberately NOT cached, so the next pan
        # over the same ground tries again.
        log.warning('dggs susc z%s/%s/%s upstream failed: %s', z, x, y, exc)
        return HttpResponseNotFound()
    if body is None:
        resp = HttpResponseNotFound()
        resp['Cache-Control'] = CACHE_HEADER
        return resp
    resp = FileResponse(open(_tile_path(z, x, y), 'rb'), content_type='image/png')
    resp['Cache-Control'] = CACHE_HEADER
    return resp
