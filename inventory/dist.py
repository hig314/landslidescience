"""OPERA DIST (land-surface disturbance) tile proxy — NASA GIBS upstream.

NASA's Global Imagery Browse Services publishes two OPERA disturbance layers
as ready-coloured Web-Mercator WMTS pyramids, no Earthdata login required:

    OPERA_L3_DIST-ALERT-HLS_Color_Index   daily,  2023-01-01 -> yesterday-ish
    OPERA_L3_DIST-ANN-HLS_Color_Index     annual, 2023 / 2024 / 2025

**Only DIST-ANN is on the map.** The daily DIST-ALERT layer was built,
evaluated against real events, and retired on 2026-09-10 — half of Alaska is
unobserved on any given date, and the one unambiguous landslide we tested
against showed up as a single step rather than the progressive change that
was the reason to want daily at all (see DIST_ALERT_ACTIVE in map.js for the
full note). Everything here still serves both layers: the route, the date
domain, the coverage probe. Nothing needs re-plumbing if it comes back.

Both are the **VEG-DIST-STATUS** code space (see tools/dist_color_status.txt).
GIBS publishes no GEN-* (generic / non-vegetated) layer — checked across both
the epsg3857 and epsg4326 endpoints on 2026-09-10 — so anything above treeline
(bare rock, talus, fresh debris on gravel) needs the LP DAAC COGs instead, not
this route. That is a separate phase, not a knob here.

Why proxy at all, when GIBS sends `Access-Control-Allow-Origin: *`?

  1. GIBS sends `Cache-Control: no-store, no-cache, must-revalidate` on every
     tile. Straight from the browser that means a refetch on every pan, and a
     date-stepped animation would refetch every frame it had already seen.
     Here the bytes land on disk once and go out immutable.
  2. It keeps the upstream URL behind one constant, and keeps cached areas
     serving if GIBS restructures.
  3. Same-origin lets the `distcolor` protocol in map.js canvas-decode the
     tiles (recolouring class 0 to transparent) without tainting the canvas.

Tile axis order is the trap worth remembering: GIBS is {z}/{TileRow}/{TileCol}
= z/y/x. Every other tile route in this app is z/x/y, so this route is z/x/y
too and the swap happens HERE, in `_upstream`, once.

Disk cache: data/dist_tiles/<layer>/<date>/<z>/<x>/<y>.png (volume-mounted,
gitignored). Upstream 404s — no coverage, or a date inside one of the gaps in
the GIBS time domain — are cached as empty `.404` markers so we don't re-ask.
Tiles for a date older than FRESH_DAYS are treated as final; the newest few
days are still being filled in upstream, so those re-fetch after FRESH_TTL.

`python manage.py purge_dist_tiles` clears the cache (bump DIST_TILE_V in
map.js at the same time so browser caches roll too).

Attribution: OPERA DIST-ALERT-HLS / DIST-ANN-HLS (c) NASA/JPL, distributed by
LP DAAC; imagery service by NASA GIBS.
"""
import datetime
import io
import json
import logging
import math
import re
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpResponseNotFound, JsonResponse
from django.views.decorators.http import require_safe

log = logging.getLogger(__name__)

# Our short key -> GIBS layer identifier. The keys are public URL surface.
LAYERS = {
    'alert': 'OPERA_L3_DIST-ALERT-HLS_Color_Index',
    'ann':   'OPERA_L3_DIST-ANN-HLS_Color_Index',
}
_GIBS = 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best'
_TILE_URL = (_GIBS + '/{layer}/default/{date}/GoogleMapsCompatible_Level12'
             '/{z}/{y}/{x}.png')
_DOMAINS_URL = (_GIBS + '/wmts.cgi?SERVICE=WMTS&VERSION=1.0.0'
                '&REQUEST=DescribeDomains&LAYER={layer}'
                '&TILEMATRIXSET=GoogleMapsCompatible_Level12')

TILES_DIR = Path(settings.BASE_DIR) / 'data' / 'dist_tiles'
MAX_ZOOM = 12            # GoogleMapsCompatible_Level12; ~18 m/px at 61 N
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_UA = {'User-Agent': 'landslidescience-dist-proxy/1'}

# A tile for a date this recent may still be filling in upstream: keep it, but
# re-ask once it is this old. Anything older than FRESH_DAYS is final.
FRESH_DAYS = 3
FRESH_TTL = 6 * 3600
CACHE_HEADER = 'public, max-age=2592000'          # 30 days, final tiles
FRESH_CACHE_HEADER = 'public, max-age=3600'       # 1 hour, still-settling dates
DOMAINS_TTL = 6 * 3600


def _valid_date(date):
    if not _DATE_RE.match(date):
        return None
    try:
        return datetime.date.fromisoformat(date)
    except ValueError:
        return None


def _is_fresh(day):
    return (datetime.date.today() - day).days <= FRESH_DAYS


def _upstream(layer, date, z, x, y):
    """GIBS tile URL. Note the y/x swap: GIBS is z/TileRow/TileCol."""
    return _TILE_URL.format(layer=LAYERS[layer], date=date, z=z, y=y, x=x)


def _send(path, header):
    resp = FileResponse(open(path, 'rb'), content_type='image/png')
    resp['Cache-Control'] = header
    return resp


def _miss(header):
    resp = HttpResponseNotFound()
    resp['Cache-Control'] = header
    return resp


def _tile_path(layer, date, z, x, y):
    return TILES_DIR / layer / date / str(z) / str(x) / f'{y}.png'


def _stale(path, fresh):
    """Only a still-settling date ever expires; a finished date is final."""
    if not fresh:
        return False
    try:
        return datetime.datetime.now().timestamp() - path.stat().st_mtime > FRESH_TTL
    except OSError:
        return True


def _tile_bytes(layer, date, z, x, y, fresh):
    """Cached tile bytes, or None for "no coverage here".

    Shared by the tile view and the coverage probe, so a probe warms exactly
    the same cache the map will read a moment later. Raises on a transient
    upstream failure so callers can tell "no data" from "could not ask".
    """
    dest = _tile_path(layer, date, z, x, y)
    marker = dest.with_suffix('.404')
    if dest.exists() and not _stale(dest, fresh):
        return dest.read_bytes()
    if marker.exists() and not _stale(marker, fresh):
        return None

    url = _upstream(layer, date, z, x, y)
    req = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
            return None
        raise
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.png.part')
    tmp.write_bytes(body)
    tmp.rename(dest)
    if marker.exists():
        marker.unlink()              # coverage appeared where there was none
    return body


@require_safe
def dist_tile(request, layer, date, z, x, y):
    z, x, y = int(z), int(x), int(y)
    n = 1 << z
    day = _valid_date(date)
    if layer not in LAYERS or day is None or z > MAX_ZOOM \
            or not (0 <= x < n and 0 <= y < n):
        return HttpResponseNotFound()

    fresh = _is_fresh(day)
    header = FRESH_CACHE_HEADER if fresh else CACHE_HEADER
    try:
        body = _tile_bytes(layer, date, z, x, y, fresh)
    except Exception as exc:
        log.warning('dist upstream %s/%s z%s failed: %s', layer, date, z, exc)
        return HttpResponseNotFound()   # transient: NOT cached
    if body is None:
        return _miss(header)
    return _send(_tile_path(layer, date, z, x, y), header)


def _fetch_domain(layer):
    """The layer's ISO8601 time domain, e.g.
    '2023-01-01/2024-03-28/P1D,2024-05-11/2025-06-02/P1D'.

    DescribeDomains is ~500 bytes; the full WMTSCapabilities.xml that carries
    the same information is 5.8 MB, so this is the endpoint to ask.
    """
    url = _DOMAINS_URL.format(layer=LAYERS[layer])
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        xml = r.read().decode('utf-8', 'replace')
    m = re.search(r'<Domain>([^<]*)</Domain>', xml)
    return m.group(1).strip() if m else ''


@require_safe
def dist_dates(request):
    """Available dates per layer, as ISO8601 range strings the client expands.

    Ranges (~500 bytes) rather than an expanded list (~1350 dates, 17 kB):
    the client has to walk them anyway to step the date control.

    `default` is the newest available date — what the map shows as "latest".
    Cached on disk for DOMAINS_TTL; a fetch failure falls back to the stale
    cache rather than breaking the control.
    """
    cache = TILES_DIR / '_domains.json'
    now = datetime.datetime.now().timestamp()
    cached = None
    try:
        cached = json.loads(cache.read_text())
        if now - cached.get('fetched_at', 0) < DOMAINS_TTL:
            return _dates_response(cached['layers'])
    except (OSError, ValueError, KeyError):
        pass

    layers = {}
    for key in LAYERS:
        try:
            domain = _fetch_domain(key)
        except Exception as exc:
            log.warning('dist domain %s failed: %s', key, exc)
            domain = ''
        if not domain and cached:
            domain = cached.get('layers', {}).get(key, {}).get('domain', '')
        layers[key] = {'domain': domain, 'default': _newest(domain)}

    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({'fetched_at': now, 'layers': layers}))
    except OSError as exc:
        log.warning('dist domain cache write failed: %s', exc)
    return _dates_response(layers)


def _layer_domain(layer):
    """This layer's ISO8601 time domain, from the same disk cache dist_dates
    serves — so a coverage probe never re-asks GIBS for something the dates
    endpoint fetched a moment ago."""
    cache = TILES_DIR / '_domains.json'
    now = datetime.datetime.now().timestamp()
    try:
        cached = json.loads(cache.read_text())
        if now - cached.get('fetched_at', 0) < DOMAINS_TTL:
            return cached['layers'][layer]['domain']
    except (OSError, ValueError, KeyError):
        pass
    try:
        return _fetch_domain(layer)
    except Exception as exc:
        log.warning('dist domain %s failed: %s', layer, exc)
        return ''


def _expand_domain(domain):
    """'a/b/P1D,c/d/P1Y' -> ascending list of dates. Mirrors _distExpand in
    map.js; the client walks the same list to step the date control."""
    out = []
    for chunk in (domain or '').split(','):
        parts = chunk.strip().split('/')
        if not parts[0]:
            continue
        if len(parts) < 2:
            out.append(parts[0])
            continue
        try:
            cur = datetime.date.fromisoformat(parts[0])
            end = datetime.date.fromisoformat(parts[1])
        except ValueError:
            continue
        yearly = len(parts) > 2 and parts[2] == 'P1Y'
        guard = 0
        while cur <= end and guard < 20000:
            out.append(cur.isoformat())
            cur = (cur.replace(year=cur.year + 1) if yearly
                   else cur + datetime.timedelta(days=1))
            guard += 1
    return out


def _newest(domain):
    """Last date in the last range of an ISO8601 domain string."""
    if not domain:
        return ''
    last = domain.split(',')[-1].strip()
    parts = last.split('/')
    # 'start/end/period' -> end; a bare date stands for itself.
    cand = parts[1] if len(parts) >= 2 else parts[0]
    return cand if _DATE_RE.match(cand) else ''


def _dates_response(layers):
    resp = JsonResponse({'layers': layers})
    resp['Cache-Control'] = 'public, max-age=3600'
    return resp


# ---------------------------------------------------------------------------
# Coverage probe — "the newest date with data in THIS view".
#
# The DIST-ALERT layer for date D holds only the granules acquired on D, and
# on any given day roughly half of Alaska goes unobserved (measured over a
# Talkeetna-sized box on 2026-09-08: 50.5% data, 20.1% no-data inside present
# tiles, 29.4% no tile at all). So "the newest published date" — the obvious
# default — routinely opens the map on a view with nothing in it, which is
# what a viewer reads as "no disturbance here". Hence this: resolve the newest
# date that actually has pixels where the user is looking, and pin to that.
#
# Cheap because it probes at a LOW zoom, where one tile stands for the whole
# view, and because it fetches through the same disk cache the map reads. It
# still crops to the viewport in tile-pixel space rather than accepting the
# whole tile — at z6 a tile is ~300 km across at these latitudes, and taking
# it whole would happily report coverage from the next drainage over.
# ---------------------------------------------------------------------------
PROBE_MAX_TILES = 6         # per date; keeps a cold walk bounded
PROBE_MAX_ZOOM = 9
# Down to 0 (one tile for the whole world), so the tile budget below is ALWAYS
# satisfiable. With a floor of 3 a zoomed-out view could not meet it, and the
# fallback then ignored the budget entirely: an all-Alaska view came to 64
# tiles, which at 20 dates is 1280 upstream requests for one button press.
PROBE_MIN_ZOOM = 0
PROBE_MAX_DATES = 20        # how far back to walk before giving up


def _lonlat_to_tilef(lon, lat, z):
    """Fractional tile coordinates (x, y) in the Web-Mercator pyramid."""
    lat = max(-85.05112878, min(85.05112878, lat))
    n = 1 << z
    x = (lon + 180.0) / 360.0 * n
    sin = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + sin) / (1 - sin)) / (4 * math.pi)) * n
    return x, y


def _probe_zoom(west, south, east, north):
    """Largest zoom whose tile cover of the bbox stays within the budget."""
    for z in range(PROBE_MAX_ZOOM, PROBE_MIN_ZOOM - 1, -1):
        x0, y0 = _lonlat_to_tilef(west, north, z)
        x1, y1 = _lonlat_to_tilef(east, south, z)
        nx = int(x1) - int(x0) + 1
        ny = int(y1) - int(y0) + 1
        if nx * ny <= PROBE_MAX_TILES:
            return z, int(x0), int(y0), nx, ny
    # Unreachable: z0 is a single tile, so the loop always returns. Kept as a
    # belt-and-braces floor rather than a path that can silently go wide.
    return 0, 0, 0, 1, 1


def _has_data_in_view(layer, date, bbox, fresh):
    """True if any pixel INSIDE the bbox is something other than no-data.

    "Data" means coverage, not disturbance: class 0 (no disturbance) counts,
    because it is a real observation of nothing happening. In the GIBS palette
    only the no-data code is transparent, so alpha carries exactly that.
    """
    from PIL import Image             # local: the site runs fine without it

    west, south, east, north = bbox
    z, tx0, ty0, nx, ny = _probe_zoom(west, south, east, north)
    fx0, fy0 = _lonlat_to_tilef(west, north, z)
    fx1, fy1 = _lonlat_to_tilef(east, south, z)

    budget = PROBE_MAX_TILES
    for ty in range(ty0, ty0 + ny):
        for tx in range(tx0, tx0 + nx):
            if not (0 <= tx < (1 << z) and 0 <= ty < (1 << z)):
                continue
            if budget <= 0:          # hard stop, whatever the zoom maths said
                return False
            budget -= 1
            body = _tile_bytes(layer, date, z, tx, ty, fresh)
            if body is None:
                continue
            # Crop to the part of THIS tile the viewport actually covers.
            px0 = max(0, min(256, int(round((fx0 - tx) * 256))))
            px1 = max(0, min(256, int(math.ceil((fx1 - tx) * 256))))
            py0 = max(0, min(256, int(round((fy0 - ty) * 256))))
            py1 = max(0, min(256, int(math.ceil((fy1 - ty) * 256))))
            if px1 <= px0 or py1 <= py0:
                continue
            im = Image.open(io.BytesIO(body)).convert('RGBA').crop((px0, py0, px1, py1))
            if im.getextrema()[3][1] > 0:      # any non-transparent alpha
                return True
    return False


@require_safe
def dist_coverage(request):
    """Newest date with data inside `bbox`, walking back from the newest.

    GET params: layer=alert|ann, bbox=west,south,east,north (EPSG:4326).
    Returns {date, newest, days_back, checked, exhausted}. `date` is null only
    if nothing in the probed window had data; the client falls back to the
    newest published date rather than showing an empty layer with no
    explanation.
    """
    layer = request.GET.get('layer', 'alert')
    if layer not in LAYERS:
        return JsonResponse({'error': 'unknown layer'}, status=400)
    try:
        west, south, east, north = (float(v) for v in request.GET['bbox'].split(','))
    except (KeyError, ValueError):
        return JsonResponse({'error': 'bbox=west,south,east,north required'}, status=400)
    if not (-180 <= west <= 180 and -180 <= east <= 180
            and -90 <= south <= 90 and -90 <= north <= 90 and south < north):
        return JsonResponse({'error': 'bbox out of range'}, status=400)
    if east < west:              # antimeridian: probe the whole width rather
        west, east = -180.0, 180.0   # than guessing which half is meant
    bbox = (west, south, east, north)

    domain = _layer_domain(layer)
    dates = _expand_domain(domain)
    if not dates:
        return JsonResponse({'date': None, 'newest': None, 'days_back': None,
                             'checked': 0, 'exhausted': True})
    newest = dates[-1]
    checked = 0
    for date in reversed(dates[-PROBE_MAX_DATES:]):
        checked += 1
        try:
            hit = _has_data_in_view(layer, date, bbox, _is_fresh(_valid_date(date)))
        except Exception as exc:
            log.warning('dist coverage probe %s %s failed: %s', layer, date, exc)
            break
        if hit:
            back = (datetime.date.fromisoformat(newest)
                    - datetime.date.fromisoformat(date)).days
            resp = JsonResponse({'date': date, 'newest': newest, 'days_back': back,
                                 'checked': checked, 'exhausted': False})
            # View-dependent, so it must not land in a shared cache.
            resp['Cache-Control'] = 'private, max-age=300'
            return resp
    resp = JsonResponse({'date': None, 'newest': newest, 'days_back': None,
                         'checked': checked, 'exhausted': True})
    resp['Cache-Control'] = 'private, max-age=60'
    return resp
