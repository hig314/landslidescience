"""BC orthophoto tile proxy (the province's 1 m mosaic, 1995-2004).

GeoBC serves its imagery through one ERDAS APOLLO WMS,
https://openmaps.gov.bc.ca/imagex/ecw_wms.dll, which renders EPSG:3857 on
request. Two things stop a browser map using it directly, and this proxy
fixes both:

- **CORS is broken upstream**: every response carries the literal header
  `access-control-allow-origin: (null)`, whatever the Origin, so MapLibre
  (which needs CORS to put a tile in a WebGL texture) cannot load it.
- **Nodata is opaque black**, even with `transparent=true`. The mosaic is
  greyscale and its nodata pixels are 0-3 (JPEG-ish noise around 0), so
  every pixel whose brightest channel is <= `NODATA_MAX` becomes
  transparent. Deep shadow can reach that level too and will show as small
  holes; on the scenes checked (glaciers north of Stewart) it did not.

Layer `bc_bc_bc_xc1m_bcalb_1995_2004` is the provincial 1 m colour-balanced
orthophoto mosaic, flown 1995-2004 (BC's first digital orthophotos). Its
own scenes are greyscale. A black-and-white twin (`..._xb1m_..._1995_2003`)
and a 2 m "xc2m" layer exist on the same service; the latter is painted
relief, not imagery.

Disk cache: data/bcortho_tiles/<z>/<x>/<y>.png (volume-mounted,
gitignored). Tiles that come back entirely nodata are cached as `.404`
markers; tiles outside the province's box are refused without asking.
The mosaic is frozen, so the cache never goes stale.

Attribution / licence: Contains information licensed under the Open
Government Licence - British Columbia.
"""
import io
import logging
import math
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpResponseNotFound
from django.views.decorators.http import require_safe

log = logging.getLogger(__name__)

UPSTREAM = ('https://openmaps.gov.bc.ca/imagex/ecw_wms.dll?service=WMS&version=1.1.1'
            '&request=GetMap&layers=bc_bc_bc_xc1m_bcalb_1995_2004&styles='
            '&srs=EPSG:3857&format=image/png&width=256&height=256&bbox={bbox}')
TILES_DIR = Path(settings.BASE_DIR) / 'data' / 'bcortho_tiles'
MAX_ZOOM = 17          # 1 m pixels ~ z17 at these latitudes; MapLibre overzooms past it
NODATA_MAX = 4         # brightest channel <= this → nodata
# The layer's own WGS84 extent from GetCapabilities, padded a little.
BOX = (-134.6, 48.1, -110.4, 60.3)   # lon0, lat0, lon1, lat1
_UA = {'User-Agent': 'landslidescience-bcortho-proxy/1'}
CACHE_HEADER = 'public, max-age=2592000'
_R = 20037508.342789244


def _tile_bbox(z, x, y):
    """EPSG:3857 bbox of an XYZ tile."""
    s = 2 * _R / (1 << z)
    return (-_R + x * s, _R - (y + 1) * s, -_R + (x + 1) * s, _R - y * s)


def _tile_lonlat(z, x, y):
    n = 1 << z
    lon0, lon1 = x / n * 360 - 180, (x + 1) / n * 360 - 180
    lat = lambda t: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * t / n))))
    return lon0, lat(y + 1), lon1, lat(y)


def _not_found():
    resp = HttpResponseNotFound()
    resp['Cache-Control'] = CACHE_HEADER
    return resp


def _mask_nodata(body):
    """PNG bytes → grey+alpha PNG bytes with nodata transparent, or None if
    the whole tile is nodata."""
    from PIL import Image
    import numpy as np
    im = Image.open(io.BytesIO(body)).convert('RGB')
    a = np.asarray(im)
    valid = a.max(axis=2) > NODATA_MAX
    if not valid.any():
        return None
    alpha = valid.astype('uint8') * 255
    spread = a.max(axis=2).astype(int) - a.min(axis=2).astype(int)
    if spread.max() <= 3:           # the mosaic's scenes are greyscale: keep it small
        grey = a.mean(axis=2).round().astype('uint8')
        out = Image.fromarray(np.dstack([grey, alpha]), 'LA')
    else:
        out = Image.fromarray(np.dstack([a, alpha]), 'RGBA')
    buf = io.BytesIO()
    out.save(buf, 'PNG', optimize=True)
    return buf.getvalue()


@require_safe
def bcortho_tile(request, z, x, y):
    z, x, y = int(z), int(x), int(y)
    n = 1 << z
    if z > MAX_ZOOM or not (0 <= x < n and 0 <= y < n):
        return HttpResponseNotFound()
    lon0, lat0, lon1, lat1 = _tile_lonlat(z, x, y)
    if lon1 < BOX[0] or lon0 > BOX[2] or lat1 < BOX[1] or lat0 > BOX[3]:
        return _not_found()

    dest = TILES_DIR / str(z) / str(x) / f'{y}.png'
    marker = dest.with_suffix('.404')
    if dest.exists():
        resp = FileResponse(open(dest, 'rb'), content_type='image/png')
        resp['Cache-Control'] = CACHE_HEADER
        return resp
    if marker.exists():
        return _not_found()

    url = UPSTREAM.format(bbox=','.join('%.3f' % v for v in _tile_bbox(z, x, y)))
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            ctype = r.headers.get('Content-Type', '')
            body = r.read()
        if not ctype.startswith('image/'):
            # A WMS error comes back 200 with an XML body: transient, not cached.
            log.warning('bcortho upstream %s -> %s: %s', url, ctype, body[:200])
            return HttpResponseNotFound()
        png = _mask_nodata(body)
    except Exception as exc:
        log.warning('bcortho upstream %s failed: %s', url, exc)
        return HttpResponseNotFound()   # transient upstream trouble: NOT cached

    if png is None:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        return _not_found()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.png.part')
    tmp.write_bytes(png)
    tmp.rename(dest)
    resp = FileResponse(open(dest, 'rb'), content_type='image/png')
    resp['Cache-Control'] = CACHE_HEADER
    return resp
