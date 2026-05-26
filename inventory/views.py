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
from pathlib import Path

import psycopg2
import psycopg2.pool
from django.conf import settings
from django.http import FileResponse, HttpResponse, HttpResponseNotFound, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_safe

from .auth import inventory_editor_required
from .middleware import SESSION_KEY as _PREVIEW_SESSION_KEY

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
_CAT_RECENT_ORDER = [
    'Catastrophic Obvious creep',
    'Catastrophic Patchy obvious creep',
    'Catastrophic Subtle creep',
    'Catastrophic Geomorph creep',
    'Catastrophic Cryptic',
]
_CAT_OTHER_ORDER = [
    'Catastrophic Modern',
    'Catastrophic Holocene',
    'Small catastrophic landslide',
]
_CLASS_COLOR = {
    'Slow Obvious creep':                '#f69fa1',
    'Slow Patchy obvious creep':         '#f69fa1',
    'Slow Subtle creep':                 '#faf075',
    'Slow Geomorph creep':               '#d3e9cf',
    'Small slow landslide':              '#d3e9cf',
    'Catastrophic Cryptic':              '#3f67b1',
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
            "SELECT centroid_lon, centroid_lat FROM landslides WHERE id = %s",
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

    # Count records with NULL/empty landslide_class — these can never match a
    # specific-class filter, so they need their own "Incomplete classification"
    # checkbox to remain visible.
    if 'unclassified_count' not in _cache:
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT COUNT(*) FROM landslides
                WHERE landslide_class IS NULL OR landslide_class = ''
            """)
            _cache['unclassified_count'] = cur.fetchone()[0]
            conn.rollback()
        finally:
            _put_conn(conn)

    def make_class_list(type_key, order):
        # Always include every known class — count=0 entries render as a
        # disabled checkbox so future data gaps are visible rather than hidden.
        return [(cls,
                 counts.get((type_key, cls), 0),
                 _CLASS_COLOR.get(cls, '#888'),
                 _HALO_COLOR.get(cls))
                for cls in order]

    return render(request, "inventory/home.html", {
        "slow_active":        make_class_list('slow', _SLOW_ACTIVE_ORDER),
        "slow_other":         make_class_list('slow', _SLOW_OTHER_ORDER),
        "cat_recent":         make_class_list('catastrophic', _CAT_RECENT_ORDER),
        "cat_other":          make_class_list('catastrophic', _CAT_OTHER_ORDER),
        "unclassified_count": _cache['unclassified_count'],
        "data_version":       _data_version,
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
        SELECT json_build_object(
            'type', 'FeatureCollection',
            'features', COALESCE(json_agg(
                json_build_object(
                    'type', 'Feature',
                    'id', l.id,
                    'geometry', CASE WHEN l.centroid_lat IS NOT NULL AND l.centroid_lon IS NOT NULL
                                     THEN json_build_object(
                                            'type', 'Point',
                                            'coordinates', json_build_array(l.centroid_lon, l.centroid_lat))
                                     ELSE NULL END,
                    'properties', json_build_object(
                        'id', l.id,
                        'unique_name', l.unique_name,
                        'landslide_type', l.landslide_type,
                        'landslide_class', l.landslide_class,
                        'inventory_subset', l.inventory_subset,
                        'description', l.description,
                        'volume_preferred', l.volume_preferred,
                        'volume_method', l.volume_method,
                        'display_area', COALESCE(l.area_body, l.area_deposit),
                        'area_src', CASE WHEN l.landslide_type = 'slow'
                                         THEN l.area_body ELSE l.area_source END,
                        'area_dep', CASE WHEN l.landslide_type = 'catastrophic'
                                         THEN l.area_deposit ELSE NULL END,
                        'year_num', CASE
                            WHEN l.landslide_class LIKE '%%Holocene%%' THEN -1
                            WHEN l.landslide_class LIKE '%%Modern%%'   THEN 0
                            WHEN l.year_text ~ '^[0-9]{4}$' THEN l.year_text::int
                            WHEN l.date_min IS NOT NULL THEN EXTRACT(YEAR FROM l.date_min)::int
                            ELSE NULL
                        END,
                        'molards',                   l.molards,
                        'stream_damming',            l.stream_damming,
                        'precursory_headscarp',      l.precursory_headscarp,
                        'has_site_specific_volume', (l.volume_site_specific IS NOT NULL),
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


def api_survey_circles(request):
    """Full FeatureCollection of survey circles (525 multipolygons).

    Independent of landslide tables. Cached in memory; cache invalidates
    only on rebuild.
    """
    if 'survey_circles' in _cache:
        resp = HttpResponse(_cache['survey_circles'], content_type='application/json')
        resp['Cache-Control'] = 'no-cache'
        return resp
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT json_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(json_agg(
                    json_build_object(
                        'type', 'Feature',
                        'id',   sc.id,
                        'geometry', ST_AsGeoJSON(sc.geom)::json,
                        'properties', json_build_object(
                            'id',                  sc.id,
                            'reviewed',            sc.reviewed,
                            'notes',               sc.notes,
                            'recent_catastrophic', sc.recent_catastrophic,
                            'obvious_creep',       sc.obvious_creep,
                            'update_total',        sc.update_total
                        )
                    )
                ), '[]'::json)
            )
            FROM survey_circles sc
        """)
        body = json.dumps(cur.fetchone()[0])
        conn.rollback()
    finally:
        _put_conn(conn)
    _cache['survey_circles'] = body
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
                    (SELECT json_agg(json_build_object(
                        'slug',       ps.slug,
                        'story_type', ps.story_type,
                        'planet_url', 'https://www.planet.com/stories/' || ps.slug,
                        'mp4_url',    CASE WHEN ps.story_type = 'timelapse'
                                            AND ps.mp4_archived_at IS NOT NULL
                                       THEN '/inventory/planet/' || ps.slug || '.mp4'
                                       ELSE NULL END,
                        'sort_order', lps.sort_order
                    ) ORDER BY lps.sort_order, ps.slug)
                    FROM landslide_planet_stories lps
                    JOIN planet_stories ps ON ps.slug = lps.slug
                    WHERE lps.landslide_id = ls.id
                    ) AS planet_stories,
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
            l.centroid_lat AS lat,
            l.centroid_lon AS lon,
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
            CASE WHEN l.landslide_type = 'slow' THEN l.area_body ELSE l.area_source END AS area_src,
            CASE WHEN l.landslide_type = 'catastrophic' THEN l.area_deposit ELSE NULL END AS area_dep,
            l.precursory_headscarp,
            (l.volume_site_specific IS NOT NULL) AS has_site_specific_volume
        FROM landslides l
        WHERE (l.seismic_datetime IS NOT NULL
               OR (l.date_min IS NOT NULL AND l.date_max IS NOT NULL
                   AND l.date_max >= l.date_min))
          AND l.centroid_lat IS NOT NULL
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
            'headscarp':       bool(r[18]) if r[18] is not None else False,
            'has_site_volume': bool(r[19]) if r[19] is not None else False,
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
            l.centroid_lat AS lat,
            l.centroid_lon AS lon,
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
            CASE WHEN l.landslide_type = 'slow' THEN l.area_body ELSE l.area_source END AS area_src,
            CASE WHEN l.landslide_type = 'catastrophic' THEN l.area_deposit ELSE NULL END AS area_dep,
            l.precursory_headscarp,
            (l.volume_site_specific IS NOT NULL) AS has_site_specific_volume
        FROM landslides l
        WHERE l.centroid_lat IS NOT NULL
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
            'headscarp':       bool(r[18]) if r[18] is not None else False,
            'has_site_volume': bool(r[19]) if r[19] is not None else False,
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
# Editor UI — /inventory/manage/* (inventory_editors group)
# ---------------------------------------------------------------------------

# Columns that are auto-managed by Postgres / not user-editable.
_FORM_EXCLUDED_COLS = ('id', 'created_at', 'updated_at')


def _discover_editable_columns(cur):
    """Return ordered metadata for every editable column on the landslides table.

    Reads information_schema so adding a column to Postgres makes it appear in
    the editor automatically. Excludes `id` and the auto-managed timestamps.
    """
    cur.execute("""
        SELECT column_name, udt_name, is_nullable, character_maximum_length
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'landslides'
        ORDER BY ordinal_position
    """)
    return [
        {'name': r[0], 'udt': r[1], 'nullable': r[2] == 'YES', 'max_length': r[3]}
        for r in cur.fetchall()
        if r[0] not in _FORM_EXCLUDED_COLS
    ]

_LIST_PAGE_SIZE = 50


@inventory_editor_required
def manage_list(request):
    """List view of all landslide records, with search + filter + pagination."""
    q          = request.GET.get('q', '').strip()
    type_f     = request.GET.get('type', '')
    class_f    = request.GET.get('class', '')
    subset_f   = request.GET.get('subset', '')
    try:
        page = max(1, int(request.GET.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    offset = (page - 1) * _LIST_PAGE_SIZE

    where, params = ['1=1'], []
    if q:
        where.append("l.unique_name ILIKE %s")
        params.append(f'%{q}%')
    if type_f in ('slow', 'catastrophic'):
        where.append("l.landslide_type = %s")
        params.append(type_f)
    if class_f:
        where.append("l.landslide_class = %s")
        params.append(class_f)
    if subset_f:
        where.append("l.inventory_subset = %s")
        params.append(subset_f)
    where_clause = ' AND '.join(where)

    list_sql = f"""
        SELECT l.id, l.unique_name, l.landslide_type, l.landslide_class,
               l.inventory_subset, l.size_inclusion,
               COUNT(p.id) AS polygon_count
        FROM landslides l
        LEFT JOIN landslide_polygons p ON p.landslide_id = l.id
        WHERE {where_clause}
        GROUP BY l.id
        ORDER BY l.landslide_type, l.unique_name
        LIMIT %s OFFSET %s
    """
    count_sql = f"SELECT COUNT(*) FROM landslides l WHERE {where_clause}"

    # Distinct values for filter dropdowns
    facet_sql = """
        SELECT
            ARRAY(SELECT DISTINCT landslide_class FROM landslides WHERE landslide_class IS NOT NULL AND landslide_class != '' ORDER BY landslide_class),
            ARRAY(SELECT DISTINCT inventory_subset FROM landslides WHERE inventory_subset IS NOT NULL AND inventory_subset != '' ORDER BY inventory_subset)
    """

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(list_sql, params + [_LIST_PAGE_SIZE, offset])
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        records = [dict(zip(cols, r)) for r in rows]

        cur.execute(count_sql, params)
        total = cur.fetchone()[0]

        cur.execute(facet_sql)
        all_classes, all_subsets = cur.fetchone()
        conn.rollback()
    finally:
        _put_conn(conn)

    # Attach last-edited info from SQLite (one query for the visible page)
    from .models import LandslideEditMeta
    ids = [r['id'] for r in records]
    meta = {m.landslide_id: m for m in LandslideEditMeta.objects.filter(landslide_id__in=ids).select_related('last_edited_by')}
    for r in records:
        m = meta.get(r['id'])
        r['last_edited_by'] = m.last_edited_by.username if m and m.last_edited_by else None
        r['last_edited_at'] = m.last_edited_at if m else None

    total_pages = (total + _LIST_PAGE_SIZE - 1) // _LIST_PAGE_SIZE
    return render(request, "inventory/manage_list.html", {
        'records':     records,
        'total':       total,
        'page':        page,
        'total_pages': total_pages,
        'q':           q,
        'type_f':      type_f,
        'class_f':     class_f,
        'subset_f':    subset_f,
        'all_classes': all_classes or [],
        'all_subsets': all_subsets or [],
    })


@inventory_editor_required
def manage_edit(request, landslide_id):
    """Edit a single landslide record. GET = form; POST = validate + UPDATE.

    Form fields and the UPDATE column list are both derived from
    `_discover_editable_columns()` so this view auto-tracks schema changes.
    """
    from .forms import build_landslide_form_class, COMMON_CLASS_VALUES
    from .models import LandslideEditMeta

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cols_meta = _discover_editable_columns(cur)
        col_names = [c['name'] for c in cols_meta]
        cols_csv = ', '.join(col_names)
        cur.execute(f"SELECT id, {cols_csv} FROM landslides WHERE id = %s",
                    (landslide_id,))
        row = cur.fetchone()
        conn.rollback()
    finally:
        _put_conn(conn)
    if not row:
        return JsonResponse({'error': 'not found'}, status=404)

    LandslideEditForm = build_landslide_form_class(cols_meta)
    initial = {f: row[i + 1] for i, f in enumerate(col_names)}
    unique_name = initial.get('unique_name', '')

    error_msg = None
    if request.method == 'POST':
        form = LandslideEditForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            # If the editor changed planet_story_link, mirror the change into
            # the planet_stories N:M tables so api_detail (which now reads
            # from the join) reflects it immediately. When this view grows
            # multi-story management UI, this block becomes the source of
            # truth and the column write goes away.
            old_slug = _planet_slug_from_url(initial.get('planet_story_link'))
            new_slug = _planet_slug_from_url(data.get('planet_story_link'))

            set_clause = ', '.join(f"{f} = %s" for f in col_names)
            values = [data.get(f) for f in col_names]
            values.append(landslide_id)
            update_sql = f"UPDATE landslides SET {set_clause} WHERE id = %s"

            conn = _get_conn()
            try:
                cur = conn.cursor()
                cur.execute(update_sql, values)
                if old_slug != new_slug:
                    if old_slug:
                        cur.execute(
                            "DELETE FROM landslide_planet_stories "
                            "WHERE landslide_id = %s AND slug = %s",
                            (landslide_id, old_slug),
                        )
                    if new_slug:
                        cur.execute(
                            "INSERT INTO planet_stories (slug) VALUES (%s) "
                            "ON CONFLICT (slug) DO NOTHING",
                            (new_slug,),
                        )
                        cur.execute(
                            "INSERT INTO landslide_planet_stories (landslide_id, slug) "
                            "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                            (landslide_id, new_slug),
                        )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                error_msg = f'Update failed: {exc}'
            finally:
                _put_conn(conn)

            if not error_msg:
                LandslideEditMeta.objects.update_or_create(
                    landslide_id=landslide_id,
                    defaults={'last_edited_by': request.user},
                )
                _invalidate('features', 'home_counts', 'unclassified_count',
                            'timed_events', 'timeline_events', 'slug_map', 'slug_for_id')
                return redirect('inventory:manage_edit', landslide_id=landslide_id)
    else:
        form = LandslideEditForm(initial=initial)

    # Planet Stories — list of stories associated with this landslide for
    # the template's status/player block. Each item carries enough metadata
    # to render the right UI: archived timelapse → embedded video; otherwise
    # link out + (for timelapse) a Fetch MP4 button.
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT ps.slug, ps.story_type, ps.mp4_archived_at, ps.mp4_size_bytes
            FROM landslide_planet_stories lps
            JOIN planet_stories ps ON ps.slug = lps.slug
            WHERE lps.landslide_id = %s
            ORDER BY lps.sort_order, ps.slug
        """, (landslide_id,))
        story_rows = cur.fetchall()
        conn.rollback()
    finally:
        _put_conn(conn)
    planet_stories = [{
        'slug':        r[0],
        'story_type':  r[1],
        'is_archived': r[1] == 'timelapse' and r[2] is not None,
        'planet_url':  f'https://www.planet.com/stories/{r[0]}',
        'mp4_url':     f'/inventory/planet/{r[0]}.mp4'
                       if r[1] == 'timelapse' and r[2] is not None else None,
        'mp4_size_kb': (r[3] // 1024) if r[3] else None,
    } for r in story_rows]

    return render(request, 'inventory/manage_edit.html', {
        'form':            form,
        'landslide_id':    landslide_id,
        'unique_name':     unique_name,
        'slug':            _slug_for_id(landslide_id),
        'editable_fields': col_names,
        'common_classes':  COMMON_CLASS_VALUES,
        'error_msg':       error_msg,
        'planet_stories':  planet_stories,
        'planet_msg':      request.GET.get('planet_msg', ''),
    })


# Planet Stories archive helpers — mirror the management command's logic.
_PLANET_STORY_PREFIX = 'https://www.planet.com/stories/'
_GCS_MP4_URL_TEMPLATE = 'https://storage.googleapis.com/planet-t2/{slug}/movie.mp4'


def _planet_slug_from_url(url):
    from pathlib import Path
    u = (url or '').strip()
    if not u.startswith(_PLANET_STORY_PREFIX):
        return None
    rest = u[len(_PLANET_STORY_PREFIX):].split('?', 1)[0].split('#', 1)[0]
    return rest.rstrip('/') or None


def _planet_mp4_path(slug):
    from pathlib import Path
    from django.conf import settings
    return Path(settings.BASE_DIR) / 'data' / 'planet_stories' / f'{slug}.mp4'


# Slug shape allowed by the serving URL — must match the regex in urls.py.
# Planet's slugs are [A-Za-z0-9_-]+ in practice.
_PLANET_SLUG_RE = re.compile(r'^[A-Za-z0-9_-]+$')

# Snapshot slug shape — lower-only by convention (we control these); the regex
# in urls.py is stricter than this. Used to validate the slug at the view
# layer as a defense-in-depth check before filesystem access.
_SNAPSHOT_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]*$')


def snapshot_index(request):
    """Public listing of all published snapshots.

    Pulled from the snapshots table. Order is most-recent first so the
    list reflects current publication state.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT s.slug, s.name, s.description, s.created_at, s.created_by,
                   s.n_landslides, s.n_polygons, s.citation_info,
                   sub.slug, sub.name
            FROM snapshots s
            LEFT JOIN subsets sub ON sub.id = s.subset_id
            ORDER BY s.created_at DESC
        """)
        rows = cur.fetchall()
        conn.rollback()
    finally:
        _put_conn(conn)
    snapshots = [{
        'slug':          r[0],
        'name':          r[1],
        'description':   r[2],
        'created_at':    r[3],
        'created_by':    r[4],
        'n_landslides':  r[5],
        'n_polygons':    r[6],
        'citation_info': r[7],
        'subset_slug':   r[8],
        'subset_name':   r[9],
    } for r in rows]
    return render(request, 'inventory/snapshots_index.html',
                  {'snapshots': snapshots})


@require_safe
def snapshot_serve(request, slug, rest=''):
    """Serve a file inside a published snapshot bundle.

    Maps /inventory/archive/<slug>/<rest> to data/snapshots/<slug>/<rest>,
    with two conveniences:
      * empty <rest> (i.e. /archive/<slug>/) → index.html
      * <rest> ending with `/` → look for index.json or index.html inside

    Static (immutable-ish) content with a 1-day cache lifetime — long enough
    to be cheap, short enough that future surgical fixes (e.g. patching an
    EOX basemap URL in an old snapshot) propagate to readers within 24 hours.
    """
    import mimetypes
    if not _SNAPSHOT_SLUG_RE.match(slug or ''):
        return HttpResponseNotFound()
    base = (Path(settings.BASE_DIR) / 'data' / 'snapshots' / slug).resolve()
    if not base.is_dir():
        return HttpResponseNotFound()

    rest = rest or ''
    if rest in ('', '/'):
        candidate = base / 'index.html'
    elif rest.endswith('/'):
        # Try JSON first (API path), then HTML.
        json_path = base / rest / 'index.json'
        html_path = base / rest / 'index.html'
        candidate = json_path if json_path.is_file() else html_path
    else:
        candidate = base / rest

    try:
        candidate = candidate.resolve()
    except (OSError, ValueError):
        return HttpResponseNotFound()

    # Stay inside the snapshot dir — no traversal.
    try:
        candidate.relative_to(base)
    except ValueError:
        return HttpResponseNotFound()

    if not candidate.is_file():
        return HttpResponseNotFound()

    ctype, _enc = mimetypes.guess_type(str(candidate))
    if ctype is None:
        ctype = 'application/octet-stream'
    resp = FileResponse(candidate.open('rb'), content_type=ctype)
    resp['Content-Length'] = str(candidate.stat().st_size)
    resp['Cache-Control'] = 'public, max-age=86400'
    return resp


@require_safe
def serve_planet_mp4(request, slug):
    """Stable serving URL for archived Planet Story MP4s.

    Load-bearing: snapshot bundles embed this URL. The storage backend can
    change over time, but the URL pattern must not. The slug regex prevents
    path traversal; the file existence check provides the 404.
    """
    if not _PLANET_SLUG_RE.match(slug or ''):
        return HttpResponseNotFound()
    path = _planet_mp4_path(slug)
    if not path.is_file():
        return HttpResponseNotFound()
    resp = FileResponse(path.open('rb'), content_type='video/mp4')
    resp['Content-Length'] = str(path.stat().st_size)
    # Slugs are immutable identifiers, so we can cache aggressively. If we
    # ever re-encode an MP4 under the same slug, bump the slug or invalidate
    # via a query string from the page that embeds it.
    resp['Cache-Control'] = 'public, max-age=31536000, immutable'
    resp['Accept-Ranges'] = 'bytes'
    return resp


@inventory_editor_required
def manage_edit_fetch_planet(request, landslide_id):
    """Download the Planet Story MP4 referenced by this landslide.

    POST-only. Reads `planet_story_link` from the DB, derives the GCS asset
    URL, streams it to `data/planet_stories/<slug>.mp4`, and redirects back
    to the edit page with a `planet_msg=` flag for a flash-style banner.
    Skips silently (no-op) if the slug is already cached.
    """
    import urllib.request
    if request.method != 'POST':
        return redirect('inventory:manage_edit', landslide_id=landslide_id)

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT planet_story_link FROM landslides WHERE id = %s",
                    (landslide_id,))
        row = cur.fetchone()
        conn.rollback()
    finally:
        _put_conn(conn)
    if not row:
        return redirect('inventory:manage_edit', landslide_id=landslide_id)

    slug = _planet_slug_from_url(row[0])
    if not slug:
        return redirect(f"{reverse('inventory:manage_edit', kwargs={'landslide_id': landslide_id})}"
                        f"?planet_msg=no_slug")
    dest = _planet_mp4_path(slug)
    if dest.exists():
        return redirect(f"{reverse('inventory:manage_edit', kwargs={'landslide_id': landslide_id})}"
                        f"?planet_msg=already_cached")

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.mp4.part')
    msg = 'ok'
    try:
        req = urllib.request.Request(
            _GCS_MP4_URL_TEMPLATE.format(slug=slug),
            headers={'User-Agent': 'landslidescience-archive/1'},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            with open(tmp, 'wb') as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk: break
                    f.write(chunk)
        tmp.rename(dest)
    except Exception as exc:
        if tmp.exists(): tmp.unlink()
        msg = f'error:{exc.__class__.__name__}'

    return redirect(f"{reverse('inventory:manage_edit', kwargs={'landslide_id': landslide_id})}"
                    f"?planet_msg={msg}")


def export_download(request):
    """Download a zip of the inventory as GeoJSON + QGIS .qml styles.

    Public — same data the map already serves via /api/features and /api/polygons,
    bundled into a single QGIS-ready archive. While the pre-launch preview
    password is set, this is still gated by InventoryPreviewMiddleware along
    with the rest of /inventory/*.
    """
    from .io_geojson import build_export_bundle
    urls = {
        'map':     request.build_absolute_uri(reverse('inventory:home')),
        'methods': request.build_absolute_uri(reverse('inventory:methods')),
    }
    body, fname = build_export_bundle(urls=urls)
    resp = HttpResponse(body, content_type='application/zip')
    resp['Content-Disposition'] = f'attachment; filename="{fname}"'
    return resp


_IMPORT_STAGE_DIR = '/tmp/landslidescience_imports'


@inventory_editor_required
def manage_import(request):
    """GET = upload form; POST = parse, stage, render diff preview."""
    from .io_geojson import parse_upload, compute_diff, ImportError_
    import os as _os
    import uuid as _uuid

    if request.method == 'POST' and 'upload' in request.FILES:
        try:
            ls_fc, po_fc, manifest = parse_upload(request.FILES['upload'].read())
        except ImportError_ as e:
            return render(request, 'inventory/manage_import.html', {'error': str(e)})

        diff = compute_diff(ls_fc, po_fc)

        # Stash for the apply step. /tmp is acceptable; large enough for ~10MB JSON.
        _os.makedirs(_IMPORT_STAGE_DIR, exist_ok=True)
        token = _uuid.uuid4().hex
        path = _os.path.join(_IMPORT_STAGE_DIR, f'{token}.json')
        with open(path, 'w') as f:
            json.dump({'landslides': ls_fc, 'landslide_polygons': po_fc}, f)

        return render(request, 'inventory/manage_import_preview.html', {
            'diff':     diff,
            'manifest': manifest,
            'token':    token,
            'filename': request.FILES['upload'].name,
        })

    return render(request, 'inventory/manage_import.html', {})


@inventory_editor_required
def manage_import_apply(request):
    """POST: apply a previously-staged import by token."""
    from .io_geojson import apply_import
    import os as _os

    if request.method != 'POST' or not request.POST.get('token'):
        return redirect('inventory:manage_import')
    token = request.POST['token']
    if not token.replace('-', '').isalnum():
        return redirect('inventory:manage_import')
    path = _os.path.join(_IMPORT_STAGE_DIR, f'{token}.json')
    if not _os.path.exists(path):
        return render(request, 'inventory/manage_import.html', {
            'error': 'Staged import expired or not found. Please re-upload.',
        })

    with open(path) as f:
        staged = json.load(f)
    ls_fc = staged['landslides']
    po_fc = staged['landslide_polygons']

    summary = apply_import(ls_fc, po_fc, request.user)

    # Cache invalidation — landslide data changed.
    _invalidate('features', 'home_counts', 'unclassified_count',
                'timed_events', 'timeline_events', 'slug_map', 'slug_for_id')

    # Cleanup the stage file
    try:
        _os.remove(path)
    except OSError:
        pass

    return render(request, 'inventory/manage_import_done.html', {'summary': summary})


@inventory_editor_required
def manage_settings(request):
    """Map display settings (colors, point sizes). Renamed from admin_settings."""
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
        return redirect('inventory:manage_settings')

    try:
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM map_settings ORDER BY key")
        rows = cur.fetchall()
        settings_map = {r[0]: r[1] for r in rows}
        conn.rollback()
    finally:
        _put_conn(conn)
    return render(request, 'inventory/manage_settings.html', {'settings': settings_map})


# ---------------------------------------------------------------------------
# Rules — auto-classification logic exposed for review + bulk apply.
# ---------------------------------------------------------------------------

def manage_rules(request):
    """Index page: list each registered rule with a disagreement count.
    Public — view-only. Apply requires editor permissions (separate view)."""
    from . import derived
    conn = _get_conn()
    try:
        cur = conn.cursor()
        rules = []
        for name, fn in derived.RULES.items():
            d = derived.diff_against_db(cur, name)
            rules.append({
                'name':          name,
                'target_table':  getattr(fn, 'target_table', 'landslides'),
                'target_column': fn.target_column,
                'is_sql':        getattr(fn, 'is_sql', False),
                'inputs':        fn.inputs,
                'summary':       getattr(fn, 'summary', ''),
                'agreements':    d['agreements'],
                'disagreements': len(d['changes']),
            })
        conn.rollback()
    finally:
        _put_conn(conn)
    return render(request, 'inventory/manage_rules.html', {'rules': rules})


def manage_rule_detail(request, name):
    """Detail view: function source, agree/disagree counts, full change preview.
    Public — view-only. The Apply button is hidden for non-editors via the template."""
    from . import derived
    import inspect
    if name not in derived.RULES:
        return redirect('inventory:rules')
    fn = derived.RULES[name]
    conn = _get_conn()
    try:
        cur = conn.cursor()
        d = derived.diff_against_db(cur, name)
        conn.rollback()
    finally:
        _put_conn(conn)
    return render(request, 'inventory/manage_rule_detail.html', {
        'name':          name,
        'target_column': fn.target_column,
        'inputs':        fn.inputs,
        'summary':       getattr(fn, 'summary', ''),
        'docstring':     (fn.__doc__ or '').strip(),
        'source':        inspect.getsource(fn),
        'agreements':    d['agreements'],
        'changes':       d['changes'],
    })


@inventory_editor_required
def manage_rule_apply(request, name):
    """POST-only: UPDATE the target column on every row where computed != stored.

    Handles both `landslides` and `landslide_polygons` as target tables.
    Audit entries always point to the parent landslide id.
    """
    from . import derived
    if request.method != 'POST' or name not in derived.RULES:
        return redirect('inventory:rules')
    fn = derived.RULES[name]
    target_table  = getattr(fn, 'target_table', 'landslides')
    target_column = fn.target_column

    conn = _get_conn()
    try:
        cur = conn.cursor()
        d = derived.diff_against_db(cur, name)
        touched_row_ids = []
        for ch in d['changes']:
            cur.execute(
                f"UPDATE {target_table} SET {target_column} = %s WHERE id = %s",
                (ch['new'], ch['id']),
            )
            touched_row_ids.append(ch['id'])

        # Resolve to parent landslide ids for the audit log.
        if target_table == 'landslides':
            affected_landslide_ids = list(touched_row_ids)
        elif touched_row_ids:
            cur.execute(
                "SELECT DISTINCT landslide_id FROM landslide_polygons WHERE id = ANY(%s)",
                (touched_row_ids,),
            )
            affected_landslide_ids = [r[0] for r in cur.fetchall()]
        else:
            affected_landslide_ids = []

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _put_conn(conn)

    from .models import LandslideEditMeta
    for ls_id in affected_landslide_ids:
        LandslideEditMeta.objects.update_or_create(
            landslide_id=ls_id,
            defaults={'last_edited_by': request.user},
        )

    _invalidate('features', 'home_counts', 'unclassified_count',
                'timed_events', 'timeline_events', 'slug_map', 'slug_for_id')

    return redirect('inventory:rule_detail', name=name)


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
