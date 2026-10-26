"""Imagery review history: what a signed-in user has looked at, on which
basemap, for how long (Hig, 2026-10-26).

The browser (ls_review.js) records VIEWS -- the map at rest with its tiles
drawn, from then until the next move, basemap or wiper change, the tab
hiding, or two minutes without input -- and posts them in batches. Each view
is the box on screen in one pane, its basemap, zoom and dwell. Anonymous
visitors are never recorded.

The measure is DENSITY, dwell / ground area on screen (stored s/km²,
shown s/100 km²), with one view counting at most DWELL_CAP_S. It is
zoom-aware without being told the zoom (five seconds at z12 spreads over 16x
the ground of five seconds at z14), and it adds: returning to ground that
was looked at before adds that view's density to it. The overlay draws the
sum per pixel, area-weighted, in factor-of-4 bands (BREAKS); the raw dwell
stays in the table so the bands -- or a different reading altogether -- can
change with experience.

The date matters because the readily available imagery is swapped out over
time: a view on ESRI World Imagery on a given day pins which Wayback release
was being looked at.

Reviewers are kept apart: every query is one user's history. A user's
history is visible to others only after they opt in (ReviewSharing); the
map asks once, which is also how a collaborator learns the recording exists.

Storage is the app's SQLite (models.ReviewView), not PostGIS: this is user
activity, like the edit audit log, not landslide data.
"""
import io
import json
import math
from datetime import datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST

from .models import ReviewSharing, ReviewView

# Displayed in s/100 km² (UNIT_SCALE x the stored s/km²) so the band edges
# are mostly whole numbers. Factor-of-4 steps from 0.5 (Hig, 2026-10-26: 2x
# more sensitive than the first cut): on a ~1500x1000 px map at 60° N a 4 s
# look reads ~4 at z13, ~60 at z15 and ~900 at z17. The client draws its key
# from these (api/review/meta/), so they live in one place.
UNITS = 's/100 km²'
UNIT_SCALE = 100.0
BREAKS = [0.5, 2, 8, 32, 128, 512]
# YlGnBu, one colour per band (len(BREAKS) + 1); the faintest band is also
# the most transparent, so a glance reads as a glance.
COLORS = [(255, 255, 204, 130), (199, 233, 180, 175), (127, 205, 187, 200),
          (65, 182, 196, 210), (29, 145, 192, 220), (34, 94, 168, 230),
          (12, 44, 132, 240)]

MAX_BATCH = 500
MAX_DWELL_S = 3600        # the client caps idle time at 120 s; this is a backstop
# One view counts at most this long toward density: past ~20 s on one frame
# it is more likely a browser left alone than closer reading (Hig,
# 2026-10-26). The raw dwell is stored, so the rule can be re-run.
DWELL_CAP_S = 20.0
MIN_ZOOM = 10             # = the client's; below it is navigation, not review
R = 6378137.0
_HALF = math.pi * R
_R_KM = 6371.0088


def _json_login(fn):
    def wrapped(request, *a, **kw):
        if not request.user.is_authenticated:
            return JsonResponse({'ok': False, 'error': 'Sign in to use review history.'}, status=403)
        return fn(request, *a, **kw)
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped


def _merc_x(lon):
    return R * math.radians(lon)


def _merc_y(lat):
    lat = max(-85.05, min(85.05, lat))
    return R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def _area_km2(w, s, e, n):
    """Ground area of a lon/lat box on the sphere."""
    return (_R_KM ** 2) * math.radians(e - w) * abs(
        math.sin(math.radians(n)) - math.sin(math.radians(s)))


def _rows_from(rec, user):
    """One posted view → ReviewView rows (two if it crosses the antimeridian)."""
    b = str(rec.get('b') or '')[:80]
    if not b or not all(c.isalnum() or c in '-_.:' for c in b):
        raise ValueError('basemap id')
    pane = str(rec.get('p') or '')
    if pane not in ('', 'l', 'r'):
        raise ValueError('pane')
    z, w, s, e, n = (float(rec[k]) for k in ('z', 'w', 's', 'e', 'n'))
    d = float(rec['d'])
    t = float(rec['t'])
    k = int(rec.get('k') or 1)     # high-res layers sharing this view
    if not (1 <= k <= 20):
        raise ValueError('share count')
    if not (MIN_ZOOM <= z <= 24) or not (0 < d <= MAX_DWELL_S):
        raise ValueError('zoom or dwell out of range')
    if not (-90 <= s < n <= 90) or not (0 < e - w <= 180):
        raise ValueError('box')
    started = datetime.fromtimestamp(t / 1000.0, tz=dt_timezone.utc)
    # Normalise the west edge into [-180, 180); split a box that crosses 180.
    shift = math.floor((w + 180) / 360) * 360
    w, e = w - shift, e - shift
    parts = [(w, e)] if e <= 180 else [(w, 180.0), (-180.0, e - 360)]
    total = _area_km2(w, s, e, n)
    if total <= 0:
        raise ValueError('area')
    density = min(d, DWELL_CAP_S) / k / total
    out = []
    for pw, pe in parts:
        out.append(ReviewView(
            user=user, basemap=b, pane=pane, zoom=z,
            west=pw, south=s, east=pe, north=n,
            x0=_merc_x(pw), y0=_merc_y(s), x1=_merc_x(pe), y1=_merc_y(n),
            area_km2=total, dwell_s=d, density=density, started_at=started))
    return out


@_json_login
@require_POST
def api_review_log(request):
    """Body: {views: [{b, p, z, w, s, e, n, t (epoch ms), d (s)}, ...]}.
    Bad records are skipped and counted, never fatal to the batch."""
    try:
        payload = json.loads(request.body.decode('utf-8'))
        views = payload.get('views') or []
    except (ValueError, UnicodeDecodeError, AttributeError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON body.'}, status=400)
    if not isinstance(views, list) or len(views) > MAX_BATCH:
        return JsonResponse({'ok': False, 'error': f'Expected up to {MAX_BATCH} views.'}, status=400)
    rows, skipped = [], 0
    for rec in views:
        try:
            rows.extend(_rows_from(rec, request.user))
        except (KeyError, TypeError, ValueError):
            skipped += 1
    ReviewView.objects.bulk_create(rows)
    return JsonResponse({'ok': True, 'saved': len(rows), 'skipped': skipped})


def _visible_user(request, username):
    """The user whose history `username` names, if the caller may see it:
    their own, or someone who has opted in. None otherwise."""
    if not username or username == request.user.get_username():
        return request.user
    U = get_user_model()
    try:
        u = U.objects.get(**{U.USERNAME_FIELD: username})
    except U.DoesNotExist:
        return None
    sh = ReviewSharing.objects.filter(user=u, shared=True).exists()
    return u if sh else None


def _filtered(request, user):
    qs = ReviewView.objects.filter(user=user)
    b = request.GET.get('b') or '*'
    # 'all imagery' and 'all lidar' are disjoint (Hig, 2026-10-26): imagery is
    # every basemap and uploaded image, lidar every survey.
    if b == 'lidar:*':
        qs = qs.filter(basemap__startswith='lidar:')
    elif b == '*':
        qs = qs.exclude(basemap__startswith='lidar:')
    else:
        qs = qs.filter(basemap=b)
    since = request.GET.get('since') or ''
    if since:
        try:
            qs = qs.filter(started_at__date__gte=datetime.strptime(since, '%Y-%m-%d').date())
        except ValueError:
            pass
    return qs


@_json_login
def api_review_meta(request):
    """The overlay row's state: my sharing answer, whose histories I may see
    (with counts), the basemaps in the chosen history, and the band key."""
    from django.db.models import Count, Max, Min
    me = request.user
    sh = ReviewSharing.objects.filter(user=me).first()
    others = (ReviewSharing.objects.filter(shared=True).exclude(user=me)
              .select_related('user'))
    reviewers = [{'username': me.get_username(), 'me': True}]
    reviewers += [{'username': o.user.get_username(), 'me': False} for o in others]
    for r in reviewers:
        u = me if r['me'] else _visible_user(request, r['username'])
        agg = ReviewView.objects.filter(user=u).aggregate(
            n=Count('id'), first=Min('started_at'), last=Max('started_at'))
        r['views'] = agg['n']
        r['first'] = agg['first'].date().isoformat() if agg['first'] else None
        r['last'] = agg['last'].date().isoformat() if agg['last'] else None
    u = _visible_user(request, request.GET.get('u')) or me
    basemaps = list(ReviewView.objects.filter(user=u).values('basemap')
                    .annotate(n=Count('id')).order_by('-n'))
    return JsonResponse({
        'ok': True,
        'sharing': None if sh is None else sh.shared,
        'reviewers': reviewers,
        'basemaps': basemaps,
        'breaks': BREAKS,
        'units': UNITS,
        'colors': ['rgba(%d,%d,%d,%.2f)' % (c[0], c[1], c[2], c[3] / 255) for c in COLORS],
    })


@_json_login
@require_POST
def api_review_share(request):
    """Body: {share: true|false}. Records the answer to the one-time notice
    (or a later change of mind from the overlay row)."""
    try:
        share = bool(json.loads(request.body.decode('utf-8')).get('share'))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON body.'}, status=400)
    ReviewSharing.objects.update_or_create(user=request.user, defaults={'shared': share})
    return JsonResponse({'ok': True, 'sharing': share})


@_json_login
def api_review_point(request):
    """The views that covered one point, newest first (the overlay's click)."""
    try:
        lon, lat = float(request.GET['lon']), float(request.GET['lat'])
    except (KeyError, ValueError):
        return JsonResponse({'ok': False, 'error': 'lon and lat required.'}, status=400)
    u = _visible_user(request, request.GET.get('u'))
    if u is None:
        return JsonResponse({'ok': False, 'error': 'That history is not shared.'}, status=403)
    lon = ((lon + 180) % 360) - 180
    qs = _filtered(request, u).filter(west__lte=lon, east__gte=lon, south__lte=lat, north__gte=lat)
    total = 0.0
    n = 0
    for (dens,) in qs.values_list('density'):
        total += dens
        n += 1
    rows = [{'date': v.started_at.date().isoformat(), 'basemap': v.basemap,
             'zoom': round(v.zoom, 1), 'dwell': round(v.dwell_s, 1),
             'density': v.density}
            for v in qs.order_by('-started_at')[:25]]
    for r in rows:
        r['density'] *= UNIT_SCALE
    return JsonResponse({'ok': True, 'user': u.get_username(), 'views': n,
                         'density': total * UNIT_SCALE, 'units': UNITS, 'recent': rows})


_EMPTY_PNG = None


def _empty_png():
    global _EMPTY_PNG
    if _EMPTY_PNG is None:
        from PIL import Image
        buf = io.BytesIO()
        Image.new('RGBA', (1, 1), (0, 0, 0, 0)).save(buf, 'PNG')
        _EMPTY_PNG = buf.getvalue()
    return _EMPTY_PNG


def _png_response(data):
    resp = HttpResponse(data, content_type='image/png')
    # Tiles change as the user browses; the client puts a version token in
    # the URL and bumps it, so a short private cache is enough.
    resp['Cache-Control'] = 'private, max-age=300'
    return resp


def review_tile(request, z, x, y):
    """The summed density of one user's views over one 256 px tile, banded.

    Each view is an axis-aligned box in Web Mercator (the inventory map is
    north-up), added to the pixels it covers weighted by the fraction of
    each pixel it covers -- so a pixel holds the area-weighted mean density
    over its ground, at every zoom, and a z15 view seen from z8 is diluted
    into its pixel rather than dropped or blown up."""
    if not request.user.is_authenticated:
        return HttpResponse(status=403)
    u = _visible_user(request, request.GET.get('u'))
    if u is None:
        return HttpResponse(status=403)
    if not (0 <= z <= 22) or not (0 <= x < 2 ** z) or not (0 <= y < 2 ** z):
        return HttpResponse(status=404)
    size = 2 * _HALF / (2 ** z)
    tx0 = -_HALF + x * size
    ty1 = _HALF - y * size
    tx1, ty0 = tx0 + size, ty1 - size
    boxes = list(_filtered(request, u).filter(
        x1__gt=tx0, x0__lt=tx1, y1__gt=ty0, y0__lt=ty1
    ).values_list('x0', 'y0', 'x1', 'y1', 'density'))
    if not boxes:
        return _png_response(_empty_png())

    import numpy as np
    from PIL import Image
    N = 256
    res = size / N
    acc = np.zeros((N, N), dtype=np.float64)
    for x0, y0, x1, y1, dens in boxes:
        px0, px1 = max(0.0, (x0 - tx0) / res), min(float(N), (x1 - tx0) / res)
        py0, py1 = max(0.0, (ty1 - y1) / res), min(float(N), (ty1 - y0) / res)
        if px1 <= px0 or py1 <= py0:
            continue
        i0, i1 = int(math.floor(px0)), int(math.ceil(px1))
        j0, j1 = int(math.floor(py0)), int(math.ceil(py1))
        ii = np.arange(i0, i1, dtype=np.float64)
        jj = np.arange(j0, j1, dtype=np.float64)
        wx = np.clip(np.minimum(px1, ii + 1) - np.maximum(px0, ii), 0, 1)
        wy = np.clip(np.minimum(py1, jj + 1) - np.maximum(py0, jj), 0, 1)
        acc[j0:j1, i0:i1] += dens * np.outer(wy, wx)

    band = np.searchsorted(np.asarray(BREAKS), acc * UNIT_SCALE, side='right')
    lut = np.asarray(COLORS, dtype=np.uint8)
    rgba = lut[band]
    rgba[acc <= 0] = 0
    buf = io.BytesIO()
    Image.fromarray(rgba, 'RGBA').save(buf, 'PNG')
    return _png_response(buf.getvalue())
