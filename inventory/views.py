"""Inventory app views.

Ported from the Tethys landslides app's controllers.py. All public views (map,
methods, API endpoints, slug deep-links) are unauthenticated; only the admin
list and admin settings POST require staff.

The PostGIS database lives in the Tethys monitoring stack's `tethys_db`
container, reached over the shared `monitoring_internal` Docker network. We
use raw psycopg2 (not Django ORM) so no migrations are needed for landslide
data — Django doesn't need to know about these tables at all.
"""
import json
import os
import re
import time

import psycopg2
import psycopg2.pool
from django.conf import settings
from django.contrib.auth.decorators import user_passes_test
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render

from .middleware import SESSION_KEY as _PREVIEW_SESSION_KEY

_staff_required = user_passes_test(lambda u: u.is_staff, login_url='/admin/login/')

# ---------------------------------------------------------------------------
# Module-level response cache
# All landslide data is static between DB reloads, so we cache aggressively.
# The cache is populated on first request and lives for the worker lifetime.
# Restart the gunicorn workers to flush.
# ---------------------------------------------------------------------------
_cache = {}
# Stamp changes each time the worker starts (or data is reloaded).
# The home template embeds this into the page so JS can cache-bust API URLs.
_data_version = str(int(time.time()))


def _invalidate(*keys):
    global _data_version
    for k in keys:
        _cache.pop(k, None)
    _data_version = str(int(time.time()))


# ---------------------------------------------------------------------------
# Activity-gradient class metadata
# ---------------------------------------------------------------------------
_SLOW_ACTIVE_ORDER = [
    'Slow Obvious creep',
    'Slow Patchy obvious creep',
]
_SLOW_OTHER_ORDER = [
    'Slow Subtle creep',
    'Slow Geomorph creep',
    'Small slow landslide',
]
_CAT_PRECURSORY_ORDER = [
    'Catastrophic Obvious creep',
    'Catastrophic Patchy obvious creep',
    'Catastrophic Subtle creep',
    'Catastrophic Geomorph creep',
]
_CAT_HISTORICAL_ORDER = [
    'Catastrophic',
    'Catastrophic Modern',
    'Small catastrophic landslide',
    'Catastrophic Holocene',
]
_CLASS_COLOR = {
    'Slow Obvious creep':                '#f69fa1',
    'Slow Patchy obvious creep':         '#f69fa1',
    'Slow Subtle creep':                 '#faf075',
    'Slow Geomorph creep':               '#d3e9cf',
    'Small slow landslide':              '#d3e9cf',
    'Catastrophic':                      '#3f67b1',
    'Small catastrophic landslide':      '#96b8df',
    'Catastrophic Holocene':             '#96b8df',
    'Catastrophic Modern':               '#96b8df',
    'Catastrophic Obvious creep':        '#3f67b1',
    'Catastrophic Patchy obvious creep': '#3f67b1',
    'Catastrophic Subtle creep':         '#3f67b1',
    'Catastrophic Geomorph creep':       '#3f67b1',
}
# Halo color for catastrophic landslides with precursory creep
_HALO_COLOR = {
    'Catastrophic Obvious creep':        '#f69fa1',
    'Catastrophic Patchy obvious creep': '#f69fa1',
    'Catastrophic Subtle creep':         '#faf075',
    'Catastrophic Geomorph creep':       '#d3e9cf',
}

# ---------------------------------------------------------------------------
# Connection pool
# ---------------------------------------------------------------------------
_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=8,
            host=os.environ.get("TETHYS_DB_HOST", "tethys_db"),
            port=int(os.environ.get("TETHYS_DB_PORT", "5432")),
            dbname=os.environ.get("LANDSLIDE_DB_NAME", "landslides"),
            user=os.environ.get("TETHYS_DB_USERNAME", "tethys"),
            password=os.environ.get("TETHYS_DB_PASSWORD", "tethys_pass"),
        )
    return _pool


def _get_conn():
    return _get_pool().getconn()


def _put_conn(conn):
    _get_pool().putconn(conn)


# ---------------------------------------------------------------------------
# Slug deep-links: /inventory/<slug>/  -> 302 to home with map+id hash.
# Slug map cached for the worker lifetime; rebuilt on worker restart.
# ---------------------------------------------------------------------------
_SLUG_NON_ALNUM_RE = re.compile(r'[^A-Za-z0-9]+')

# Reserved tokens that resolve to existing routes — slugs collapsing to these
# never resolve as deep-links even if a future name happens to slugify to one.
_RESERVED_SLUGS = {'api', 'admin', 'methods', 'static', 'accounts', ''}

# Constant target zoom for slug deep-links — at AK latitudes, ~10 km wide.
_SLUG_ZOOM = 13


def _slugify(name):
    return _SLUG_NON_ALNUM_RE.sub('-', name).strip('-').lower()


def _slug_map():
    if 'slug_map' in _cache:
        return _cache['slug_map']
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, unique_name FROM landslides ORDER BY id")
        rows = cur.fetchall()
        conn.rollback()
    finally:
        _put_conn(conn)
    smap = {}
    inv = {}
    for id_, name in rows:
        s = _slugify(name)
        if not s:
            continue
        # Earliest id keeps the bare slug; collisions get -<id> appended
        if s in smap:
            s = '{}-{}'.format(s, id_)
        smap[s] = id_
        inv[id_] = s
    _cache['slug_map'] = smap
    _cache['slug_for_id'] = inv
    return smap


def _slug_for_id(landslide_id):
    if 'slug_for_id' not in _cache:
        _slug_map()  # populates both caches
    return _cache['slug_for_id'].get(landslide_id)


def slug_redirect(request, slug):
    slug = slug.lower()
    if slug in _RESERVED_SLUGS:
        return redirect('/inventory/')
    landslide_id = _slug_map().get(slug)
    if not landslide_id:
        return redirect('/inventory/')
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ST_X(ctr.pt), ST_Y(ctr.pt)
            FROM landslides ls
            LEFT JOIN LATERAL (
                SELECT ST_Centroid(lp.geom) AS pt
                FROM landslide_polygons lp
                WHERE lp.landslide_id = ls.id
                  AND ((ls.landslide_type = 'catastrophic' AND lp.role IN ('source', 'deposit'))
                       OR (ls.landslide_type = 'slow' AND lp.role = 'body'))
                ORDER BY
                    CASE lp.role WHEN 'source' THEN 0 WHEN 'body' THEN 0 ELSE 1 END,
                    lp.is_primary DESC NULLS LAST,
                    lp.id
                LIMIT 1
            ) ctr ON true
            WHERE ls.id = %s
            """,
            (landslide_id,),
        )
        row = cur.fetchone()
        conn.rollback()
    finally:
        _put_conn(conn)
    if not row or row[0] is None:
        return redirect('/inventory/#id={}'.format(landslide_id))
    lon, lat = row
    return redirect(
        '/inventory/#map={zoom}/{lat:.4f}/{lon:.4f}&id={id}'.format(
            zoom=_SLUG_ZOOM, lat=lat, lon=lon, id=landslide_id,
        )
    )


# ---------------------------------------------------------------------------
# Public views
# ---------------------------------------------------------------------------

def home(request):
    if 'home_counts' not in _cache:
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT landslide_type, landslide_class, COUNT(*) AS cnt
                FROM landslides
                WHERE landslide_class IS NOT NULL AND landslide_class != ''
                GROUP BY landslide_type, landslide_class
                ORDER BY landslide_type, cnt DESC
            """)
            _cache['home_counts'] = {(r[0], r[1]): r[2] for r in cur.fetchall()}
            conn.rollback()
        finally:
            _put_conn(conn)

    counts = _cache['home_counts']

    def make_class_list(type_key, order):
        result = []
        for cls in order:
            if (type_key, cls) in counts:
                result.append((cls, counts[(type_key, cls)],
                               _CLASS_COLOR.get(cls, '#888'),
                               _HALO_COLOR.get(cls)))
        return result

    return render(request, "inventory/home.html", {
        "slow_active":     make_class_list('slow', _SLOW_ACTIVE_ORDER),
        "slow_other":      make_class_list('slow', _SLOW_OTHER_ORDER),
        "cat_precursory":  make_class_list('catastrophic', _CAT_PRECURSORY_ORDER),
        "cat_historical":  make_class_list('catastrophic', _CAT_HISTORICAL_ORDER),
        "data_version":    _data_version,
    })


def methods(request):
    return render(request, "inventory/methods.html")


# ---------------------------------------------------------------------------
# GeoJSON API
# ---------------------------------------------------------------------------

def api_features(request):
    """
    Return all landslide centroids as GeoJSON.
    Filters: ?type=slow|catastrophic  ?subset=Kim|Schaefer|Alaska  ?class=...
    The unfiltered response (no query params) is cached in memory.
    """
    # Serve from cache for the common unfiltered case
    if not request.GET and 'features' in _cache:
        resp = HttpResponse(_cache['features'], content_type='application/json')
        resp['Cache-Control'] = 'no-cache'
        return resp

    conditions = []
    params = []

    ls_type = request.GET.get("type")
    if ls_type in ("slow", "catastrophic"):
        conditions.append("l.landslide_type = %s")
        params.append(ls_type)

    subset = request.GET.get("subset")
    if subset:
        conditions.append("l.inventory_subset = %s")
        params.append(subset)

    ls_class = request.GET.get("class")
    if ls_class:
        conditions.append("l.landslide_class = %s")
        params.append(ls_class)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sql = f"""
        WITH point_geoms AS (
            -- Slow: centroid of body polygon.
            -- Catastrophic: centroid of primary source polygon;
            --   falls back to deposit if no source exists.
            SELECT DISTINCT ON (lp.landslide_id)
                lp.landslide_id,
                ST_Centroid(lp.geom) AS centroid
            FROM landslide_polygons lp
            JOIN landslides l ON l.id = lp.landslide_id
            WHERE (l.landslide_type = 'catastrophic' AND lp.role IN ('source', 'deposit'))
               OR (l.landslide_type = 'slow'         AND lp.role = 'body')
            ORDER BY
                lp.landslide_id,
                CASE lp.role WHEN 'source' THEN 0 WHEN 'body' THEN 0 ELSE 1 END,
                lp.is_primary DESC NULLS LAST,
                lp.id
        ),
        display_areas AS (
            SELECT landslide_id,
                   MAX(CASE WHEN role IN ('deposit', 'body') THEN area END) AS display_area,
                   MAX(CASE WHEN role = 'body'    THEN area END) AS area_body,
                   MAX(CASE WHEN role = 'source'  THEN area END) AS area_source,
                   MAX(CASE WHEN role = 'deposit' THEN area END) AS area_deposit
            FROM landslide_polygons
            GROUP BY landslide_id
        ),
        centroids AS (
            SELECT pg.landslide_id, pg.centroid, da.display_area,
                   da.area_body, da.area_source, da.area_deposit
            FROM point_geoms pg
            JOIN display_areas da ON da.landslide_id = pg.landslide_id
        )
        SELECT json_build_object(
            'type', 'FeatureCollection',
            'features', COALESCE(json_agg(
                json_build_object(
                    'type', 'Feature',
                    'id', l.id,
                    'geometry', ST_AsGeoJSON(c.centroid)::json,
                    'properties', json_build_object(
                        'id', l.id,
                        'unique_name', l.unique_name,
                        'landslide_type', l.landslide_type,
                        'landslide_class', l.landslide_class,
                        'inventory_subset', l.inventory_subset,
                        'description', l.description,
                        'volume_preferred', l.volume_preferred,
                        'volume_method', l.volume_method,
                        'display_area', c.display_area,
                        'area_src', CASE WHEN l.landslide_type = 'slow'
                                         THEN c.area_body ELSE c.area_source END,
                        'area_dep', CASE WHEN l.landslide_type = 'catastrophic'
                                         THEN c.area_deposit ELSE NULL END,
                        'year_num', CASE
                            WHEN l.landslide_class LIKE '%%Holocene%%' THEN -1
                            WHEN l.landslide_class LIKE '%%Modern%%'   THEN 0
                            WHEN l.year_text ~ '^[0-9]{4}$' THEN l.year_text::int
                            WHEN l.date_min IS NOT NULL THEN EXTRACT(YEAR FROM l.date_min)::int
                            ELSE NULL
                        END,
                        'molards',                   l.molards,
                        'stream_damming',            l.stream_damming,
                        'exclusively_supraglacial',  l.exclusively_supraglacial,
                        'creeping_permafrost_mass',  l.creeping_permafrost_mass,
                        'has_seismic',              (l.seismic_datetime IS NOT NULL),
                        'has_time_bracket',         (l.date_min IS NOT NULL AND l.date_max IS NOT NULL),
                        'post_2012_activity_increase', l.post_2012_activity_increase
                    )
                )
            ), '[]'::json)
        )
        FROM landslides l
        JOIN centroids c ON c.landslide_id = l.id
        {where}
    """

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        result = cur.fetchone()[0]
        conn.rollback()
    finally:
        _put_conn(conn)

    body = json.dumps(result)
    if not request.GET:
        _cache['features'] = body
    resp = HttpResponse(body, content_type='application/json')
    resp['Cache-Control'] = 'no-cache'
    return resp


def api_polygons(request):
    """
    Return landslide polygons intersecting a bounding box.
    Required: ?bbox=minLon,minLat,maxLon,maxLat
    Optional: ?type=slow|catastrophic
    """
    bbox_str = request.GET.get("bbox", "")
    try:
        min_lon, min_lat, max_lon, max_lat = [float(v) for v in bbox_str.split(",")]
    except (ValueError, AttributeError):
        return JsonResponse({"error": "bbox required: minLon,minLat,maxLon,maxLat"}, status=400)

    conditions = ["ST_Intersects(p.geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326))"]
    params = [min_lon, min_lat, max_lon, max_lat]

    ls_type = request.GET.get("type")
    if ls_type in ("slow", "catastrophic"):
        conditions.append("l.landslide_type = %s")
        params.append(ls_type)

    where = "WHERE " + " AND ".join(conditions)

    sql = f"""
        SELECT json_build_object(
            'type', 'FeatureCollection',
            'features', COALESCE(json_agg(
                json_build_object(
                    'type', 'Feature',
                    'id', p.id,
                    'geometry', ST_AsGeoJSON(p.geom)::json,
                    'properties', json_build_object(
                        'landslide_id', p.landslide_id,
                        'unique_name', l.unique_name,
                        'landslide_type', l.landslide_type,
                        'landslide_class', l.landslide_class,
                        'role', p.role,
                        'area', p.area,
                        'thickness', p.thickness,
                        'polygon_volume', p.polygon_volume
                    )
                )
            ), '[]'::json)
        )
        FROM landslide_polygons p
        JOIN landslides l ON l.id = p.landslide_id
        {where}
    """

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        result = cur.fetchone()[0]
        conn.rollback()
    finally:
        _put_conn(conn)

    return HttpResponse(json.dumps(result), content_type="application/json")


def api_detail(request, landslide_id):
    """Return full attributes for a single landslide as JSON.

    Computes per-landslide OPERA InSAR display links from the same centroid
    used for the map marker (slow=body; catastrophic=source, deposit fallback).
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT row_to_json(l)
            FROM (
                SELECT ls.*,
                    (SELECT json_agg(json_build_object(
                        'id', p.id,
                        'role', p.role,
                        'is_primary', p.is_primary,
                        'area', p.area,
                        'thickness', p.thickness,
                        'polygon_volume', p.polygon_volume
                    ))
                    FROM landslide_polygons p WHERE p.landslide_id = ls.id
                    ) AS polygons,
                    CASE WHEN ctr.pt IS NULL THEN NULL ELSE
                        'https://displacement.asf.alaska.edu/#/?dispOverview=VEL&zoom=14.5&center='
                        || ROUND(ST_X(ctr.pt)::numeric, 4) || ',' || ROUND(ST_Y(ctr.pt)::numeric, 4)
                        || '&flightDirs=ASCENDING'
                    END AS opera_asc_link,
                    CASE WHEN ctr.pt IS NULL THEN NULL ELSE
                        'https://displacement.asf.alaska.edu/#/?dispOverview=VEL&zoom=14.5&center='
                        || ROUND(ST_X(ctr.pt)::numeric, 4) || ',' || ROUND(ST_Y(ctr.pt)::numeric, 4)
                        || '&flightDirs=DESCENDING'
                    END AS opera_desc_link
                FROM landslides ls
                LEFT JOIN LATERAL (
                    SELECT ST_Centroid(lp.geom) AS pt
                    FROM landslide_polygons lp
                    WHERE lp.landslide_id = ls.id
                      AND ((ls.landslide_type = 'catastrophic' AND lp.role IN ('source', 'deposit'))
                           OR (ls.landslide_type = 'slow' AND lp.role = 'body'))
                    ORDER BY
                        CASE lp.role WHEN 'source' THEN 0 WHEN 'body' THEN 0 ELSE 1 END,
                        lp.is_primary DESC NULLS LAST,
                        lp.id
                    LIMIT 1
                ) ctr ON true
                WHERE ls.id = %s
            ) l
            """,
            (landslide_id,),
        )
        row = cur.fetchone()
        conn.rollback()
    finally:
        _put_conn(conn)

    if row is None:
        return JsonResponse({"error": "not found"}, status=404)
    data = row[0]
    data['slug'] = _slug_for_id(data['id'])
    return HttpResponse(json.dumps(data, default=str), content_type="application/json")


def api_timed_events(request):
    """
    Return all events with seismic datetime or a valid date range,
    with compact fields needed for the histogram. Cached in memory.
    """
    if 'timed_events' in _cache:
        resp = JsonResponse({'events': _cache['timed_events']})
        resp['Cache-Control'] = 'no-cache'
        return resp

    sql = """
        SELECT
            l.id,
            l.landslide_type,
            l.landslide_class,
            l.volume_preferred,
            l.molards,
            l.stream_damming,
            l.exclusively_supraglacial,
            l.creeping_permafrost_mass,
            ST_Y(ctr.centroid) AS lat,
            ST_X(ctr.centroid) AS lon,
            CASE
                WHEN l.landslide_class LIKE '%%Holocene%%' THEN -1
                WHEN l.landslide_class LIKE '%%Modern%%'   THEN 0
                WHEN l.year_text ~ '^[0-9]{4}$'           THEN l.year_text::int
                WHEN l.date_min IS NOT NULL                THEN EXTRACT(YEAR FROM l.date_min)::int
                ELSE NULL
            END AS year_num,
            CASE WHEN l.seismic_datetime IS NOT NULL THEN 'point' ELSE 'range' END AS timing,
            CASE WHEN l.seismic_datetime IS NOT NULL
                 THEN EXTRACT(DOY FROM l.seismic_datetime)::int
                 ELSE EXTRACT(DOY FROM l.date_min)::int
            END AS doy,
            CASE WHEN l.seismic_datetime IS NOT NULL THEN NULL
                 ELSE EXTRACT(DOY FROM l.date_max)::int
            END AS doy_end,
            CASE WHEN l.seismic_datetime IS NOT NULL THEN 1
                 ELSE (l.date_max - l.date_min) + 1
            END AS span,
            CASE WHEN l.seismic_datetime IS NOT NULL
                 THEN EXTRACT(YEAR FROM l.seismic_datetime)::int
                 ELSE EXTRACT(YEAR FROM l.date_min)::int
            END AS event_year,
            CASE WHEN l.landslide_type = 'slow' THEN pa.area_body ELSE pa.area_source END AS area_src,
            CASE WHEN l.landslide_type = 'catastrophic' THEN pa.area_deposit ELSE NULL END AS area_dep
        FROM landslides l
        JOIN (
            SELECT landslide_id, ST_Centroid(ST_Union(geom)) AS centroid
            FROM landslide_polygons GROUP BY landslide_id
        ) ctr ON ctr.landslide_id = l.id
        JOIN (
            SELECT landslide_id,
                   MAX(CASE WHEN role = 'body'    THEN area END) AS area_body,
                   MAX(CASE WHEN role = 'source'  THEN area END) AS area_source,
                   MAX(CASE WHEN role = 'deposit' THEN area END) AS area_deposit
            FROM landslide_polygons GROUP BY landslide_id
        ) pa ON pa.landslide_id = l.id
        WHERE l.seismic_datetime IS NOT NULL
           OR (l.date_min IS NOT NULL AND l.date_max IS NOT NULL
               AND l.date_max >= l.date_min)
        ORDER BY l.id
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        conn.rollback()
    finally:
        _put_conn(conn)

    events = []
    for r in rows:
        events.append({
            'id': r[0], 'ls_type': r[1], 'cls': r[2],
            'vol': r[3],
            'molards':      bool(r[4]) if r[4] is not None else False,
            'stream_dam':   r[5] or '',
            'supraglacial': bool(r[6]) if r[6] is not None else False,
            'permafrost':   bool(r[7]) if r[7] is not None else False,
            'lat': float(r[8]), 'lon': float(r[9]),
            'year_num': r[10],
            'timing': r[11],
            'doy':     int(r[12]) if r[12] is not None else None,
            'doy_end': int(r[13]) if r[13] is not None else None,
            'span':    int(r[14]) if r[14] is not None else 1,
            'year':    int(r[15]) if r[15] is not None else 2000,
            'area_src': float(r[16]) if r[16] is not None else None,
            'area_dep': float(r[17]) if r[17] is not None else None,
        })
    _cache['timed_events'] = events
    resp = JsonResponse({'events': events})
    resp['Cache-Control'] = 'no-cache'
    return resp


def api_timeline_events(request):
    """
    Return all events that have resolvable temporal information (year_num not null),
    with lat/lon and filter attributes for the timeline histogram. Cached in memory.
    """
    if 'timeline_events' in _cache:
        resp = JsonResponse({'events': _cache['timeline_events']})
        resp['Cache-Control'] = 'no-cache'
        return resp

    sql = """
        SELECT
            l.id,
            l.landslide_type,
            l.landslide_class,
            l.volume_preferred,
            l.molards,
            l.stream_damming,
            l.exclusively_supraglacial,
            l.creeping_permafrost_mass,
            l.post_2012_activity_increase,
            (l.seismic_datetime IS NOT NULL) AS has_seismic,
            ST_Y(ctr.centroid) AS lat,
            ST_X(ctr.centroid) AS lon,
            CASE
                WHEN l.landslide_class LIKE '%%Holocene%%' THEN -1
                WHEN l.landslide_class LIKE '%%Modern%%'   THEN 0
                WHEN l.year_text ~ '^[0-9]{4}$'           THEN l.year_text::int
                WHEN l.date_min IS NOT NULL                THEN EXTRACT(YEAR FROM l.date_min)::int
                ELSE NULL
            END AS year_num,
            -- Precise date fields for monthly binning (2012+)
            CASE WHEN l.seismic_datetime IS NOT NULL
                 THEN to_char(l.seismic_datetime, 'YYYY-MM-DD') ELSE NULL END AS tl_pt,
            to_char(l.date_min, 'YYYY-MM-DD') AS tl_d0,
            to_char(l.date_max, 'YYYY-MM-DD') AS tl_d1,
            CASE WHEN l.landslide_type = 'slow' THEN pa.area_body ELSE pa.area_source END AS area_src,
            CASE WHEN l.landslide_type = 'catastrophic' THEN pa.area_deposit ELSE NULL END AS area_dep
        FROM landslides l
        JOIN (
            SELECT landslide_id, ST_Centroid(ST_Union(geom)) AS centroid
            FROM landslide_polygons GROUP BY landslide_id
        ) ctr ON ctr.landslide_id = l.id
        JOIN (
            SELECT landslide_id,
                   MAX(CASE WHEN role = 'body'    THEN area END) AS area_body,
                   MAX(CASE WHEN role = 'source'  THEN area END) AS area_source,
                   MAX(CASE WHEN role = 'deposit' THEN area END) AS area_deposit
            FROM landslide_polygons GROUP BY landslide_id
        ) pa ON pa.landslide_id = l.id
        ORDER BY l.id
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        conn.rollback()
    finally:
        _put_conn(conn)

    events = []
    for r in rows:
        yn = r[12]
        if yn is None:
            continue
        events.append({
            'id':          r[0],
            'ls_type':     r[1],
            'cls':         r[2],
            'vol':         r[3],
            'molards':     bool(r[4]) if r[4] is not None else False,
            'stream_dam':  r[5] or '',
            'supraglacial':bool(r[6]) if r[6] is not None else False,
            'permafrost':  bool(r[7]) if r[7] is not None else False,
            'post_2012':   bool(r[8]) if r[8] is not None else False,
            'has_seismic': bool(r[9]),
            'lat':         float(r[10]),
            'lon':         float(r[11]),
            'year_num':    int(yn),
            'tl_pt':    r[13],
            'tl_d0':    r[14],
            'tl_d1':    r[15],
            'area_src': float(r[16]) if r[16] is not None else None,
            'area_dep': float(r[17]) if r[17] is not None else None,
        })
    _cache['timeline_events'] = events
    resp = JsonResponse({'events': events})
    resp['Cache-Control'] = 'no-cache'
    return resp


def api_settings(request):
    """Return all map_settings as a flat JSON object."""
    if 'settings' not in _cache:
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM map_settings ORDER BY key")
            _cache['settings'] = {r[0]: r[1] for r in cur.fetchall()}
            conn.rollback()
        finally:
            _put_conn(conn)
    resp = JsonResponse(_cache['settings'])
    resp['Cache-Control'] = 'public, max-age=60'
    return resp


# ---------------------------------------------------------------------------
# Admin views (staff only)
# ---------------------------------------------------------------------------

@_staff_required
def admin_settings(request):
    conn = _get_conn()
    if request.method == 'POST':
        try:
            cur = conn.cursor()
            for key, val in request.POST.items():
                if key == 'csrfmiddlewaretoken':
                    continue
                cur.execute(
                    "UPDATE map_settings SET value = %s WHERE key = %s",
                    (val, key),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            _put_conn(conn)
        _invalidate('settings')
        return redirect('inventory:admin_settings')

    try:
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM map_settings ORDER BY key")
        rows = cur.fetchall()
        settings = {r[0]: r[1] for r in rows}
        conn.rollback()
    finally:
        _put_conn(conn)
    return render(request, 'inventory/admin_settings.html', {'settings': settings})


@_staff_required
def admin_list(request):
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT l.id, l.unique_name, l.landslide_type, l.landslide_class,
                   l.inventory_subset, l.size_inclusion,
                   COUNT(p.id) AS polygon_count
            FROM landslides l
            LEFT JOIN landslide_polygons p ON p.landslide_id = l.id
            GROUP BY l.id
            ORDER BY l.landslide_type, l.unique_name
            """
        )
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        records = [dict(zip(cols, r)) for r in rows]
        conn.rollback()
    finally:
        _put_conn(conn)

    return render(request, "inventory/admin_list.html", {"records": records})


# ---------------------------------------------------------------------------
# Pre-launch preview password (paired with InventoryPreviewMiddleware).
# Unset INVENTORY_PREVIEW_PASSWORD to disable the barrier entirely.
# ---------------------------------------------------------------------------

def preview_login(request):
    expected = settings.INVENTORY_PREVIEW_PASSWORD
    next_url = request.GET.get('next') or request.POST.get('next') or '/inventory/'
    # Only allow same-site redirects to /inventory/* to avoid open-redirect.
    if not next_url.startswith('/inventory/'):
        next_url = '/inventory/'

    error = None
    if request.method == 'POST':
        if not expected:
            # Barrier is disabled; let them through.
            return redirect(next_url)
        if request.POST.get('password') == expected:
            request.session[_PREVIEW_SESSION_KEY] = True
            return redirect(next_url)
        error = 'Incorrect password.'

    return render(request, 'inventory/preview.html', {
        'next': next_url,
        'error': error,
    })
