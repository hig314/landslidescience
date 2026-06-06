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
from django.views.decorators.http import require_POST, require_safe

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


def _imagery_suggestions(lat, lon):
    """Pre-built imagery-browser URLs centered on a landslide's centroid, to
    seed the esri_wayback_link / google_images_link fields. The editor opens
    one, pans/zooms to frame the landslide, then copies the final URL back into
    the field. Formats match what's already in the inventory (ESRI Wayback #ext
    bbox; Google Maps satellite @lat,lon,<alt>m)."""
    import math
    if lat is None or lon is None:
        return {}
    half_m = 750.0  # ~1.5 km view
    dlat = half_m / 111000.0
    dlon = half_m / (111000.0 * max(0.1, math.cos(math.radians(lat))))
    ext = f"{lon - dlon:.5f},{lat - dlat:.5f},{lon + dlon:.5f},{lat + dlat:.5f}"
    opera = (f"https://displacement.asf.alaska.edu/#/?dispOverview=VEL&zoom=14.5"
             f"&center={lon:.4f},{lat:.4f}&flightDirs=")
    return {
        'esri_wayback_link':  f"https://livingatlas.arcgis.com/wayback/#ext={ext}",
        'google_images_link': f"https://www.google.com/maps/@{lat:.6f},{lon:.6f},1500m/data=!3m1!1e3",
        'opera_asc':  opera + 'ASCENDING',
        'opera_desc': opera + 'DESCENDING',
        'topoview':   f"https://ngmdb.usgs.gov/topoview/viewer/#13/{lat:.4f}/{lon:.4f}",
    }


def public_landslide_filter(alias='l'):
    """SQL predicate for the publicly/active-visible landslides: reviewed
    (inducted) and not deprecated (superseded). Applied to every public surface
    — home counts, features/polygons APIs, chart data, slug map, snapshot — so
    pending uploads and superseded originals never leak to the public map.
    Keep all public queries in sync via this one helper.
    """
    a = (alias + '.') if alias else ''
    return f"{a}reviewed_at IS NOT NULL AND {a}deprecated_at IS NULL"


def _slug_map():
    if 'slug_map' in _cache:
        return _cache['slug_map']
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT id, unique_name FROM landslides l "
                    f"WHERE {public_landslide_filter('l')} ORDER BY id")
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

def _home_counts(subset):
    """Compute (class_counts, unclassified_count) for the sidebar.

    subset: slug of a subset to filter by, or None for the full inventory.
    The unfiltered case is cached for the worker lifetime (hot path);
    filtered cases hit the DB each time (rare).
    """
    if (subset is None and 'home_counts' in _cache and 'unclassified_count' in _cache
            and 'flagged_count' in _cache):
        return _cache['home_counts'], _cache['unclassified_count'], _cache['flagged_count']

    join = ""
    where_class = [public_landslide_filter('l'), "l.landslide_class IS NOT NULL", "l.landslide_class != ''"]
    where_null  = [public_landslide_filter('l'), "(l.landslide_class IS NULL OR l.landslide_class = '')"]
    where_flag  = [public_landslide_filter('l'), "l.flagged"]
    params      = []
    if subset:
        join = ("JOIN landslide_subsets lps ON lps.landslide_id = l.id "
                "JOIN subsets s ON s.id = lps.subset_id")
        where_class.append("s.slug = %s")
        where_null.append("s.slug = %s")
        where_flag.append("s.slug = %s")
        params.append(subset)

    counts_sql = f"""
        SELECT l.landslide_type, l.landslide_class, COUNT(*) AS cnt
        FROM landslides l {join}
        WHERE {' AND '.join(where_class)}
        GROUP BY l.landslide_type, l.landslide_class
        ORDER BY l.landslide_type, cnt DESC
    """
    null_sql = f"SELECT COUNT(*) FROM landslides l {join} WHERE {' AND '.join(where_null)}"
    flag_sql = f"SELECT COUNT(*) FROM landslides l {join} WHERE {' AND '.join(where_flag)}"

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(counts_sql, params)
        counts = {(r[0], r[1]): r[2] for r in cur.fetchall()}
        cur.execute(null_sql, params)
        unclassified = cur.fetchone()[0]
        cur.execute(flag_sql, params)
        flagged = cur.fetchone()[0]
        conn.rollback()
    finally:
        _put_conn(conn)

    if subset is None:
        _cache['home_counts']        = counts
        _cache['unclassified_count'] = unclassified
        _cache['flagged_count']      = flagged
    return counts, unclassified, flagged


def home(request):
    # The sidebar's class checkboxes show member counts. When the page is
    # accessed with ?subset=<slug>, the counts reflect just that subset's
    # members so users see what's actually visible after filtering. The
    # unfiltered case is hot and cached; the filtered case is rare (mostly
    # exercised by snapshot builds) so we don't cache it.
    subset = (request.GET.get('subset') or '').strip() or None
    counts, unclassified, flagged = _home_counts(subset)

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
        "unclassified_count": unclassified,
        "flagged_count":      flagged,
        "data_version":       _data_version,
    })


def methods(request):
    return render(request, "inventory/methods.html")


def naming(request):
    return render(request, "inventory/naming.html")


# ---------------------------------------------------------------------------
# GeoJSON API
# ---------------------------------------------------------------------------

# Landslide-level properties consumed by the shared MapLibre filter (`buildFilter`
# in map.js). SINGLE SOURCE OF TRUTH: spliced into BOTH api_features (centroid
# points) and api_polygons so one filter expression hides/shows points and
# polygons identically. Add a filterable field here ONCE — never hand-mirror it
# into just one query: a property absent from the other source silently drops
# every feature there whenever that filter is active (exactly how `flagged`
# regressed for polygons). Both queries alias the landslides table as `l`; the
# `%%` escapes survive psycopg2 parameter substitution.
_FILTER_PROPS_SQL = """
                        'volume_preferred', l.volume_preferred,
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
                        'molards',                     l.molards,
                        'stream_damming',              l.stream_damming,
                        'precursory_headscarp',        l.precursory_headscarp,
                        'has_site_specific_volume',   (l.volume_site_specific IS NOT NULL),
                        'exclusively_supraglacial',    l.exclusively_supraglacial,
                        'creeping_permafrost_mass',    l.creeping_permafrost_mass,
                        'has_seismic',                (l.seismic_datetime IS NOT NULL),
                        'has_time_bracket',           (l.date_min IS NOT NULL AND l.date_max IS NOT NULL),
                        'post_2012_activity_increase', l.post_2012_activity_increase,
                        'flagged',                     COALESCE(l.flagged, false)"""

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

    conditions = [public_landslide_filter('l')]
    params = []

    ls_type = request.GET.get("type")
    if ls_type in ("slow", "catastrophic"):
        conditions.append("l.landslide_type = %s")
        params.append(ls_type)

    # ?subset=<slug> filters via the N:M join table. The legacy
    # inventory_subset text column is no longer the source of truth.
    subset = request.GET.get("subset")
    if subset:
        conditions.append("""EXISTS (
            SELECT 1 FROM landslide_subsets lps
            JOIN subsets s ON s.id = lps.subset_id
            WHERE lps.landslide_id = l.id AND s.slug = %s
        )""")
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
                        'subsets', COALESCE(
                            (SELECT json_agg(s.slug ORDER BY s.slug)
                             FROM landslide_subsets lps
                             JOIN subsets s ON s.id = lps.subset_id
                             WHERE lps.landslide_id = l.id),
                            '[]'::json),
                        'description', l.description,
                        'volume_method', l.volume_method,
                        'display_area', COALESCE(l.area_body, l.area_deposit),
                        -- display-only extras (labels / info-box); not filter inputs
                        'flag_reason',  l.flag_reason,
                        'owner',        l.owner,
                        'noted_by',     l.noted_by,
                        'year_text',    l.year_text,
                        'creep_behavior', l.creep_behavior,
                        -- shared filter properties (mirror of api_polygons) →
                        {_FILTER_PROPS_SQL}
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

    conditions = ["ST_Intersects(p.geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326))",
                  public_landslide_filter('l')]
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
                        'polygon_volume', p.polygon_volume,
                        -- shared filter properties (same fragment as api_features)
                        -- so one map filter hides/shows points and polygons
                        -- identically. n10/lw are merged client-side (by
                        -- landslide_id), as for points.
                        {_FILTER_PROPS_SQL}
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


@inventory_editor_required
@require_safe
def api_provisional(request):
    """Editor-only: pending (provisional) landslides — not yet reviewed, hidden
    from the public map. Returned so editors can see what they've added while
    mapping a series (rendered in a distinct colour). Points sit at the centroid
    (falling back to the polygon union for records the cascade hasn't run on)."""
    sql = """
        SELECT json_build_object(
            'points', json_build_object('type', 'FeatureCollection', 'features', (
                SELECT COALESCE(json_agg(json_build_object(
                    'type', 'Feature',
                    'geometry', json_build_object('type', 'Point',
                        'coordinates', json_build_array(pt_lon, pt_lat)),
                    'properties', json_build_object('id', id, 'unique_name', unique_name,
                                                    'landslide_type', landslide_type)
                )), '[]'::json)
                FROM (
                    SELECT l.id, l.unique_name, l.landslide_type,
                           COALESCE(l.centroid_lon, ST_X(ST_Centroid(ST_Collect(p.geom)))) AS pt_lon,
                           COALESCE(l.centroid_lat, ST_Y(ST_Centroid(ST_Collect(p.geom)))) AS pt_lat
                    FROM landslides l LEFT JOIN landslide_polygons p ON p.landslide_id = l.id
                    WHERE l.reviewed_at IS NULL AND l.deprecated_at IS NULL
                    GROUP BY l.id
                ) s WHERE pt_lon IS NOT NULL
            )),
            'polygons', json_build_object('type', 'FeatureCollection', 'features', (
                SELECT COALESCE(json_agg(json_build_object(
                    'type', 'Feature',
                    'geometry', ST_AsGeoJSON(p.geom)::json,
                    'properties', json_build_object('landslide_id', p.landslide_id,
                                                    'unique_name', l.unique_name, 'role', p.role)
                )), '[]'::json)
                FROM landslide_polygons p JOIN landslides l ON l.id = p.landslide_id
                WHERE l.reviewed_at IS NULL AND l.deprecated_at IS NULL
            ))
        )
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        result = cur.fetchone()[0]
        conn.rollback()
    finally:
        _put_conn(conn)
    resp = HttpResponse(json.dumps(result), content_type="application/json")
    resp['Cache-Control'] = 'no-store'   # editor-specific + changes as they map
    return resp


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
                    (SELECT COALESCE(json_agg(s.slug ORDER BY s.slug), '[]'::json)
                     FROM landslide_subsets lps
                     JOIN subsets s ON s.id = lps.subset_id
                     WHERE lps.landslide_id = ls.id
                    ) AS subsets,
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
                    END AS opera_desc_link,
                    CASE WHEN ctr.pt IS NULL THEN NULL ELSE
                        'https://ngmdb.usgs.gov/topoview/viewer/#13/'
                        || ROUND(ST_Y(ctr.pt)::numeric, 4) || '/' || ROUND(ST_X(ctr.pt)::numeric, 4)
                    END AS topoview_link
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
                  -- public-only (keep in sync with public_landslide_filter('ls')):
                  AND ls.reviewed_at IS NOT NULL AND ls.deprecated_at IS NULL
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
          -- public-only (keep in sync with public_landslide_filter('l')):
          AND l.reviewed_at IS NOT NULL AND l.deprecated_at IS NULL
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
          -- public-only (keep in sync with public_landslide_filter('l')):
          AND l.reviewed_at IS NOT NULL AND l.deprecated_at IS NULL
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
_FORM_EXCLUDED_COLS = (
    'id', 'created_at', 'updated_at',
    # State column managed by the review workflow — never set by the form
    # directly; it gets set to NOW() when a pending record completes review.
    'reviewed_at',
    # Supersede/merge state — managed by the merge flow, never the edit form.
    # (If editable, a normal save would write NULL and silently un-deprecate.)
    'deprecated_at', 'superseded_by',
    # Legacy column — superseded by the landslide_subsets M:N join. The
    # subset-membership UI on the edit form writes the new table; the
    # legacy column will be dropped after the rest of the transition lands.
    'inventory_subset',
)


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
        # subset_f is a subsets.slug (selected from the facet dropdown,
        # which lists slug→name pairs).
        where.append("""EXISTS (
            SELECT 1 FROM landslide_subsets lps
            JOIN subsets s ON s.id = lps.subset_id
            WHERE lps.landslide_id = l.id AND s.slug = %s
        )""")
        params.append(subset_f)
    # Induction status filter. Default hides deprecated (superseded) records;
    # 'all' shows everything; explicit values isolate one bucket.
    status_f = request.GET.get('status', '')
    if status_f == 'pending':
        where.append("l.reviewed_at IS NULL AND l.deprecated_at IS NULL")
    elif status_f == 'active':
        where.append("l.reviewed_at IS NOT NULL AND l.deprecated_at IS NULL")
    elif status_f == 'deprecated':
        where.append("l.deprecated_at IS NOT NULL")
    elif status_f != 'all':
        where.append("l.deprecated_at IS NULL")   # default: hide deprecated
    where_clause = ' AND '.join(where)

    list_sql = f"""
        SELECT l.id, l.unique_name, l.landslide_type, l.landslide_class,
               l.reviewed_at, l.deprecated_at, l.superseded_by,
               COALESCE(
                   (SELECT array_agg(s.slug ORDER BY s.slug)
                    FROM landslide_subsets lps
                    JOIN subsets s ON s.id = lps.subset_id
                    WHERE lps.landslide_id = l.id),
                   ARRAY[]::text[]
               ) AS subset_slugs,
               l.size_inclusion,
               COUNT(p.id) AS polygon_count
        FROM landslides l
        LEFT JOIN landslide_polygons p ON p.landslide_id = l.id
        WHERE {where_clause}
        GROUP BY l.id
        ORDER BY l.landslide_type, l.unique_name
        LIMIT %s OFFSET %s
    """
    count_sql = f"SELECT COUNT(*) FROM landslides l WHERE {where_clause}"

    # Filter-dropdown facets. Subsets come from the subsets table directly
    # (slug + name pairs); classes still come from distinct landslides values.
    facet_sql = """
        SELECT
            ARRAY(SELECT DISTINCT landslide_class FROM landslides
                  WHERE landslide_class IS NOT NULL AND landslide_class != ''
                  ORDER BY landslide_class)
    """
    subsets_sql = "SELECT slug, name FROM subsets ORDER BY name"

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(list_sql, params + [_LIST_PAGE_SIZE, offset])
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        records = [dict(zip(cols, r)) for r in rows]

        cur.execute(count_sql, params)
        total = cur.fetchone()[0]

        # Pending-review queue summary (independent of the active filters) so the
        # header can offer a one-click jump straight into the review flow.
        cur.execute("""
            SELECT COUNT(*) AS n,
                   (SELECT id FROM landslides
                    WHERE reviewed_at IS NULL AND deprecated_at IS NULL
                    ORDER BY created_at DESC LIMIT 1) AS first_id
            FROM landslides
            WHERE reviewed_at IS NULL AND deprecated_at IS NULL
        """)
        prow = cur.fetchone()
        pending_count, pending_first_id = prow[0], prow[1]

        cur.execute(facet_sql)
        all_classes = cur.fetchone()[0]
        cur.execute(subsets_sql)
        all_subsets = [{'slug': r[0], 'name': r[1]} for r in cur.fetchall()]
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
        'status_f':    status_f,
        'all_classes': all_classes or [],
        'all_subsets': all_subsets or [],
        'pending_count':    pending_count,
        'pending_first_id': pending_first_id,
    })


@inventory_editor_required
def manage_review(request, landslide_id):
    """Two-stage induction: edit pending-review records before they
    join gen pop. Delegates to manage_edit with review_mode=True so the
    save handler can also set reviewed_at, apply the rule cascade, and
    advance to the next pending record."""
    return manage_edit(request, landslide_id, review_mode=True)


# Edit-form field grouping (manage_edit.html). Drives the section layout and
# the slow/catastrophic show-hide that streamlines data entry. Fields not listed
# here fall into a trailing "Other" group so a newly-added column still surfaces.
# Rule-derived columns live in 'computed' (rendered collapsed). Per-type
# visibility is applied client-side by manage_edit.html using the `vis` class
# computed in _group_edit_fields().
_EDIT_FIELD_GROUPS = [
    {'key': 'core', 'title': '', 'fields': [
        'unique_name', 'landslide_type', 'description', 'notes',
        'noted_by', 'owner', 'ongoing_work', 'stream_damming', 'volume_site_specific']},
    {'key': 'creep', 'title': 'Creep / slow-movement detection', 'fields': [
        'creep_evaluated',
        'planet_labs_creep', 'planet_labs_patchy_creep',
        'insar_schaefer', 'insar_kim', 'insar_opera', 'insar_other',
        'other_subtle_creep', 'geomorph_creep',
        'post_2012_activity_increase', 'creeping_permafrost_mass']},
    {'key': 'event', 'title': 'Catastrophic event & timing', 'fields': [
        'catastrophic_failure_years', 'year_text', 'date_min', 'date_max',
        'precursory_headscarp', 'exclusively_supraglacial', 'molards',
        'seismic_datetime', 'seismic_note', 'seismic_credit']},
    {'key': 'imagery', 'title': 'Imagery & external links', 'fields': [
        'planet_story_link', 'esri_wayback_link', 'google_images_link',
        'sentinel2_link', 'sentinel1_link']},
    {'key': 'review', 'title': 'Review flag', 'fields': ['flagged', 'flag_reason']},
    {'key': 'computed', 'title': 'Computed (auto-filled — override only if needed)',
     'collapsed': True, 'fields': [
        'landslide_class', 'size_inclusion', 'creep_behavior', 'insar_creep',
        'volume_preferred', 'volume_method', 'volume_estimated',
        'area_body', 'area_source', 'area_deposit',
        'volume_body', 'volume_source', 'volume_deposit',
        'centroid_albers_x', 'centroid_albers_y', 'centroid_lat', 'centroid_lon']},
]
# Within the creep group, these two are slow-only (hidden for catastrophic even
# when creep is being evaluated). creep_evaluated is the gate; the remaining
# creep fields show for slow, or for catastrophic only once the gate is checked.
_CREEP_SLOW_ONLY = {'post_2012_activity_increase', 'creeping_permafrost_mass'}


def _group_edit_fields(form):
    """Bucket a LandslideEditForm's bound fields into _EDIT_FIELD_GROUPS, tagging
    each with a `vis` class the template/JS use for per-type visibility. Returns
    a list of {'meta', 'items':[{'field', 'vis'}]}; unlisted columns trail in an
    'Other' group so the form never silently drops a field."""
    seen = set()
    grouped = []
    for g in _EDIT_FIELD_GROUPS:
        items = []
        for name in g['fields']:
            if name not in form.fields:
                continue
            seen.add(name)
            if g['key'] == 'event':
                vis = 'grp-event'
            elif g['key'] == 'creep':
                if name == 'creep_evaluated':
                    vis = 'creep-gate'
                elif name in _CREEP_SLOW_ONLY:
                    vis = 'creep-slow-only'
                else:
                    vis = 'creep-detail'
            else:
                vis = ''
            items.append({'field': form[name], 'vis': vis})
        if items:
            grouped.append({'meta': g, 'items': items})
    rest = [{'field': form[n], 'vis': ''} for n in form.fields if n not in seen]
    if rest:
        grouped.append({'meta': {'key': 'other', 'title': 'Other'}, 'items': rest})
    return grouped


@inventory_editor_required
def manage_edit(request, landslide_id, review_mode=False):
    """Edit a single landslide record. GET = form; POST = validate + UPDATE.

    Form fields and the UPDATE column list are both derived from
    `_discover_editable_columns()` so this view auto-tracks schema changes.

    review_mode=True: the form excludes rule-populated columns (those get
    computed at the end of the review), shows a mini-map of the polygon,
    and on save sets reviewed_at + redirects to the next pending record.
    Records that have already been reviewed bounce back to the regular
    edit form.
    """
    from . import derived
    from .forms import build_landslide_form_class, COMMON_CLASS_VALUES
    from .models import LandslideEditMeta

    rule_targets = {fn.target_column for fn in derived.RULES.values()}

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cols_meta = _discover_editable_columns(cur)
        # In review mode, drop rule-populated columns from the editable
        # form — they get computed at the end of the review, not set by
        # the editor.
        if review_mode:
            cols_meta = [c for c in cols_meta if c['name'] not in rule_targets]
        col_names = [c['name'] for c in cols_meta]
        cols_csv = ', '.join(col_names)
        cur.execute(f"SELECT id, reviewed_at, {cols_csv} FROM landslides WHERE id = %s",
                    (landslide_id,))
        row = cur.fetchone()
        conn.rollback()
    finally:
        _put_conn(conn)
    if not row:
        return JsonResponse({'error': 'not found'}, status=404)

    already_reviewed = row[1] is not None
    if review_mode and already_reviewed:
        # Nothing to do — kick to the regular edit form.
        return redirect('inventory:manage_edit', landslide_id=landslide_id)

    LandslideEditForm = build_landslide_form_class(cols_meta)
    initial = {f: row[i + 2] for i, f in enumerate(col_names)}
    unique_name = initial.get('unique_name', '')
    # Auto-fill the editor's identity into a blank owner / noted_by (same default
    # the file-import preview applies), so a record they touch is credited to
    # them by default. They can override before saving.
    if 'owner' in col_names and not initial.get('owner') and request.user.username:
        initial['owner'] = request.user.username
    if 'noted_by' in col_names and not initial.get('noted_by'):
        _full = (request.user.get_full_name() or '').strip()
        if _full:
            initial['noted_by'] = _full

    error_msg = None
    if request.method == 'POST':
        form = LandslideEditForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            type_changed = ('landslide_type' in col_names
                            and data.get('landslide_type') != initial.get('landslide_type'))
            roles_changed = False
            # If the editor changed planet_story_link, mirror the change into
            # the planet_stories N:M tables so api_detail (which now reads
            # from the join) reflects it immediately. When this view grows
            # multi-story management UI, this block becomes the source of
            # truth and the column write goes away.
            old_slug = _planet_slug_from_url(initial.get('planet_story_link'))
            new_slug = _planet_slug_from_url(data.get('planet_story_link'))

            # Subset memberships submitted as checkboxes named "subset_<id>".
            posted_subset_ids = set()
            for key in request.POST.keys():
                if key.startswith('subset_'):
                    try:
                        posted_subset_ids.add(int(key[len('subset_'):]))
                    except ValueError:
                        pass

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

                # Sync subset memberships to the checkbox state. Frozen
                # (snapshotted) subsets are excluded from both directions
                # of the diff — their membership can't be changed once a
                # snapshot has captured them, so any attempt to add/remove
                # via a crafted POST is silently dropped.
                cur.execute(
                    "SELECT DISTINCT subset_id FROM snapshots WHERE subset_id IS NOT NULL"
                )
                frozen_ids = {r[0] for r in cur.fetchall()}
                cur.execute(
                    "SELECT subset_id FROM landslide_subsets WHERE landslide_id = %s",
                    (landslide_id,),
                )
                current_subset_ids = {r[0] for r in cur.fetchall()}
                to_add    = (posted_subset_ids - current_subset_ids) - frozen_ids
                to_remove = (current_subset_ids - posted_subset_ids) - frozen_ids
                if to_remove:
                    cur.execute(
                        "DELETE FROM landslide_subsets "
                        "WHERE landslide_id = %s AND subset_id = ANY(%s::int[])",
                        (landslide_id, list(to_remove)),
                    )
                if to_add:
                    cur.executemany(
                        "INSERT INTO landslide_subsets (landslide_id, subset_id) "
                        "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        [(landslide_id, sid) for sid in to_add],
                    )

                # Polygon role edits (source/body/deposit), submitted as
                # polygon_role_<id>. Exposed so a mis-typed landslide can be
                # fully corrected: switching landslide_type also needs the role
                # to match (slow→body; catastrophic→source/deposit) or the
                # centroid rule won't resolve. The cascade below recomputes.
                cur.execute("SELECT id, role FROM landslide_polygons WHERE landslide_id = %s",
                            (landslide_id,))
                poly_ids = []
                for pid, cur_role in cur.fetchall():
                    poly_ids.append(pid)
                    posted = request.POST.get(f'polygon_role_{pid}')
                    if posted in ('source', 'body', 'deposit') and posted != cur_role:
                        cur.execute(
                            "UPDATE landslide_polygons SET role = %s WHERE id = %s AND landslide_id = %s",
                            (posted, pid, landslide_id),
                        )
                        roles_changed = True

                # Primary polygon (the one that defines the centroid), submitted
                # as polygon_primary. Set is_primary on the chosen polygon and
                # clear it on the rest — lets the editor fix a wrong primary
                # (e.g. a deposit marked primary). The cascade recomputes the
                # centroid from the new primary.
                try:
                    posted_primary = int(request.POST.get('polygon_primary') or 0)
                except (TypeError, ValueError):
                    posted_primary = 0
                if posted_primary and posted_primary in poly_ids:
                    cur.execute(
                        "UPDATE landslide_polygons SET is_primary = (id = %s) "
                        "WHERE landslide_id = %s", (posted_primary, landslide_id))
                    roles_changed = True

                conn.commit()
            except Exception as exc:
                conn.rollback()
                error_msg = f'Update failed: {exc}'
            finally:
                _put_conn(conn)

            if not error_msg and (review_mode or roles_changed or type_changed):
                # Apply the per-record rule cascade (centroids, areas, volumes,
                # class — same as the batch rule-apply) so computed columns stay
                # consistent. Runs on induction (review) and whenever a
                # geometry-affecting field changed (landslide_type / polygon
                # role). On review it also stamps reviewed_at. One transaction;
                # failure leaves the record unreviewed/unchanged.
                from .derived import apply_rules_for_landslide
                conn = _get_conn()
                try:
                    cur = conn.cursor()
                    apply_rules_for_landslide(cur, landslide_id)
                    if review_mode:
                        cur.execute(
                            "UPDATE landslides SET reviewed_at = NOW() WHERE id = %s",
                            (landslide_id,),
                        )
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    error_msg = f'Rule cascade failed: {exc}'
                finally:
                    _put_conn(conn)

            if not error_msg:
                LandslideEditMeta.objects.update_or_create(
                    landslide_id=landslide_id,
                    defaults={'last_edited_by': request.user},
                )
                _invalidate('features', 'home_counts', 'unclassified_count',
                            'timed_events', 'timeline_events', 'slug_map', 'slug_for_id')

                if review_mode:
                    # Find the next pending record (recent-upload first).
                    next_id = _first_pending_landslide()
                    if next_id is not None:
                        return redirect('inventory:manage_review', landslide_id=next_id)
                    return redirect('inventory:manage_list')
                return redirect('inventory:manage_edit', landslide_id=landslide_id)
    else:
        form = LandslideEditForm(initial=initial)

    # Planet Stories + subset memberships for the template. Single conn,
    # two queries.
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

        cur.execute("""
            SELECT s.id, s.slug, s.name, s.is_publication,
                   EXISTS (
                       SELECT 1 FROM landslide_subsets lps
                       WHERE lps.subset_id = s.id AND lps.landslide_id = %s
                   ) AS is_member,
                   EXISTS (
                       SELECT 1 FROM snapshots sn WHERE sn.subset_id = s.id
                   ) AS is_frozen
            FROM subsets s
            ORDER BY s.is_publication DESC, s.name
        """, (landslide_id,))
        subset_rows = cur.fetchall()
        conn.rollback()
    finally:
        _put_conn(conn)
    all_subsets_for_form = [
        {'id': r[0], 'slug': r[1], 'name': r[2],
         'is_publication': r[3], 'is_member': r[4], 'is_frozen': r[5]}
        for r in subset_rows
    ]
    planet_stories = [{
        'slug':        r[0],
        'story_type':  r[1],
        'is_archived': r[1] == 'timelapse' and r[2] is not None,
        'planet_url':  f'https://www.planet.com/stories/{r[0]}',
        'mp4_url':     f'/inventory/planet/{r[0]}.mp4'
                       if r[1] == 'timelapse' and r[2] is not None else None,
        'mp4_size_kb': (r[3] // 1024) if r[3] else None,
    } for r in story_rows]

    # Review-mode extras: polygon GeoJSON for the mini-map, plus the
    # remaining-pending count so the editor knows how far along they are.
    # Polygon GeoJSON for the preview mini-map — built in BOTH modes now (the
    # edit form gets the same imagery-switchable map as review, for slow-change
    # comparison via AHAP). ::text yields JSON-encoded text we embed via |safe.
    polygons_geojson = None
    pending_remaining = None
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT json_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(json_agg(
                    json_build_object(
                        'type', 'Feature',
                        'geometry', ST_AsGeoJSON(p.geom, 15)::json,
                        'properties', json_build_object('db_id', p.id,
                                                        'role', p.role,
                                                        'is_primary', p.is_primary,
                                                        -- so the preview map colors
                                                        -- polygons by class, matching
                                                        -- the main map (ls_colors.js)
                                                        'landslide_class', l.landslide_class)
                    )
                ), '[]'::json)
            )::text
            FROM landslide_polygons p
            JOIN landslides l ON l.id = p.landslide_id
            WHERE p.landslide_id = %s
        """, (landslide_id,))
        polygons_geojson = cur.fetchone()[0]
        if review_mode:
            cur.execute("SELECT COUNT(*) FROM landslides WHERE reviewed_at IS NULL")
            pending_remaining = cur.fetchone()[0]
        conn.rollback()
    finally:
        _put_conn(conn)

    # Imagery-link suggestions seeded from the centroid (ESRI Wayback + Google).
    # Pending records haven't run the rule cascade yet (centroid_lat/lon NULL),
    # so fall back to the centroid of the polygons' union.
    imagery_suggestions = {}
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT COALESCE(l.centroid_lat, ST_Y(ST_Centroid(ST_Collect(p.geom)))),
                   COALESCE(l.centroid_lon, ST_X(ST_Centroid(ST_Collect(p.geom))))
            FROM landslides l
            LEFT JOIN landslide_polygons p ON p.landslide_id = l.id
            WHERE l.id = %s
            GROUP BY l.id, l.centroid_lat, l.centroid_lon
        """, (landslide_id,))
        crow = cur.fetchone()
        conn.rollback()
    finally:
        _put_conn(conn)
    if crow:
        imagery_suggestions = _imagery_suggestions(crow[0], crow[1])

    # Polygons (id, role) so the form can expose role editing — needed to fully
    # correct a mis-typed landslide (type switch + matching role).
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, role, is_primary FROM landslide_polygons "
            "WHERE landslide_id = %s ORDER BY id", (landslide_id,))
        polygons = [{'id': r[0], 'role': r[1], 'is_primary': r[2]} for r in cur.fetchall()]
        conn.rollback()
    finally:
        _put_conn(conn)

    template = 'inventory/manage_review.html' if review_mode else 'inventory/manage_edit.html'
    return render(request, template, {
        'form':              form,
        # Grouped bound-fields for the edit form's sectioned, type-aware layout
        # (review mode keeps its own flat template).
        'field_groups':      None if review_mode else _group_edit_fields(form),
        'landslide_id':      landslide_id,
        'unique_name':       unique_name,
        'landslide_type':    initial.get('landslide_type', ''),
        'imagery_suggestions': imagery_suggestions,
        'polygons':          polygons,
        'slug':              _slug_for_id(landslide_id),
        'editable_fields':   col_names,
        'common_classes':    COMMON_CLASS_VALUES,
        'error_msg':         error_msg,
        'planet_stories':    planet_stories,
        'all_subsets':       all_subsets_for_form,
        'planet_msg':        request.GET.get('planet_msg', ''),
        'review_mode':       review_mode,
        'polygons_geojson':  polygons_geojson,
        'pending_remaining': pending_remaining,
    })


def _first_pending_landslide():
    """Return the id of the most-recently-created pending-review record,
    or None if everything's been inducted. Recent-upload-first ordering
    means freshly uploaded batches get reviewed before any older
    pending records that may have accumulated."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id FROM landslides
            WHERE reviewed_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
        """)
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        _put_conn(conn)


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
        # Directory-style URL without a trailing slash (e.g. `rules` or
        # `rules/area_body`) — redirect to the slashed form so the HTML's
        # relative paths resolve from the correct base. Django's
        # APPEND_SLASH middleware doesn't fire here because the URL
        # pattern itself matched; the view has to do the redirect.
        if (base / rest).is_dir() and not (base / rest).is_file():
            return redirect(request.path + '/', permanent=True)
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
        f = request.FILES['upload']
        try:
            ls_fc, po_fc, manifest = parse_upload(f.read(), filename=f.name)
        except ImportError_ as e:
            return render(request, 'inventory/manage_import.html', {'error': str(e)})

        diff = compute_diff(ls_fc, po_fc)

        # Stash for the apply step. /tmp is acceptable; large enough for ~10MB JSON.
        _os.makedirs(_IMPORT_STAGE_DIR, exist_ok=True)
        token = _uuid.uuid4().hex
        path = _os.path.join(_IMPORT_STAGE_DIR, f'{token}.json')
        with open(path, 'w') as f:
            json.dump({'landslides': ls_fc, 'landslide_polygons': po_fc}, f)

        # Subsets dropdown: exclude any subset that's already been
        # snapshotted — those are frozen artifacts and shouldn't grow
        # silently after a publication. (The check is by FK from
        # snapshots.subset_id; a snapshot for the full inventory has
        # NULL subset_id so it doesn't freeze any specific subset.)
        from .forms import build_landslide_form_class
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT s.slug, s.name
                FROM subsets s
                WHERE NOT EXISTS (
                    SELECT 1 FROM snapshots sn WHERE sn.subset_id = s.id
                )
                ORDER BY s.is_publication DESC, s.name
            """)
            subsets = [{'slug': r[0], 'name': r[1]} for r in cur.fetchall()]
            cols_meta = _discover_editable_columns(cur)
            conn.rollback()
        finally:
            _put_conn(conn)

        # Common-fields form: editor fills whatever should apply uniformly
        # to every new record in this upload; blanks pass through. Exclude
        # rule-populated columns (centroid_lat, area_*, volume_*, etc. —
        # these get re-derived from polygons after import) and unique_name
        # (must be unique per record, so it makes no sense as a batch value).
        from . import derived
        rule_targets = {fn.target_column for fn in derived.RULES.values()}
        common_exclude = rule_targets | {'unique_name'}
        CommonForm = build_landslide_form_class(cols_meta, all_optional=True,
                                                 exclude=common_exclude)
        # Defaults from the editor's user record. `owner` uses the short
        # username (canonical identifier across the system); `noted_by`
        # uses the editor's full name (the display credit on map detail
        # popups). Editor can override either before applying.
        initial = {}
        if request.user.username:
            initial['owner'] = request.user.username
        full_name = (request.user.get_full_name() or '').strip()
        if full_name:
            initial['noted_by'] = full_name
        common_form = CommonForm(initial=initial or None)

        from . import io_geojson as _iog
        return render(request, 'inventory/manage_import_preview.html', {
            'diff':            diff,
            'manifest':        manifest,
            'token':           token,
            'filename':        request.FILES['upload'].name,
            'subsets':         subsets,
            'common_form':     common_form,
            'COLLISION_IOU':   _iog.COLLISION_IOU,
            'COLLISION_NEAR_M': _iog.COLLISION_NEAR_M,
        })

    return render(request, 'inventory/manage_import.html', {})


@inventory_editor_required
def manage_import_apply(request):
    """POST: apply a previously-staged import by token."""
    from .io_geojson import apply_import, ImportError_

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
    subset_slug = (request.POST.get('subset_slug') or '').strip() or None

    # Common fields the editor wants applied to all new landslides in this
    # upload. The form factory parses + coerces them by column type; we
    # only keep the entries the editor actually filled in (non-blank).
    from .forms import build_landslide_form_class
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cols_meta = _discover_editable_columns(cur)
        conn.rollback()
    finally:
        _put_conn(conn)
    from . import derived
    rule_targets = {fn.target_column for fn in derived.RULES.values()}
    common_exclude = rule_targets | {'unique_name'}
    CommonForm = build_landslide_form_class(cols_meta, all_optional=True,
                                             exclude=common_exclude)
    common_form = CommonForm(request.POST)
    common_fields = {}
    if common_form.is_valid():
        for k, v in common_form.cleaned_data.items():
            # Blank string / unticked checkbox / unset field → don't impose
            # on the import. Anything else overrides per-record values.
            if v in (None, '', False):
                continue
            common_fields[k] = v

    try:
        summary = apply_import(
            ls_fc, po_fc, request.user,
            subset_slug=subset_slug,
            common_fields=common_fields,
        )
    except ImportError_ as e:
        return render(request, 'inventory/manage_import.html', {'error': str(e)})

    # Cache invalidation — landslide data changed.
    _invalidate('features', 'home_counts', 'unclassified_count',
                'timed_events', 'timeline_events', 'slug_map', 'slug_for_id')

    # Cleanup the stage file
    try:
        _os.remove(path)
    except OSError:
        pass

    # If new landslides were inserted, send the editor straight into the
    # review queue (most-recent-upload first). Pure UPDATE flows fall back
    # to the done page since no records need induction.
    if summary.get('landslides_inserted'):
        next_id = _first_pending_landslide()
        if next_id is not None:
            return redirect('inventory:manage_review', landslide_id=next_id)

    return render(request, 'inventory/manage_import_done.html', {'summary': summary})


# Geometry written from Terra Draw (simple WGS84 Polygon) coerced to the
# landslide_polygons MULTIPOLYGON/4326 column: set SRID explicitly, make valid,
# force polygonal output (drop stray points/lines a self-touch can produce),
# then promote to MultiPolygon.
_GEOM_WRITE_EXPR = ("ST_Multi(ST_CollectionExtract("
                    "ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), 3))")
_POLY_MIN_AREA_M2 = 1.0          # reject degenerate / collapsed polygons
_POLY_ROLES = ('source', 'body', 'deposit')


def _snapshot_polygon(cur, polygon_id, landslide_id, operation, user):
    """Copy a polygon's CURRENT row into landslide_polygons_history before an
    UPDATE/DELETE overwrites or removes it. Runs inside the caller's
    transaction so the snapshot and the write commit (or roll back) together."""
    uid = getattr(user, 'id', None)
    cur.execute("""
        INSERT INTO landslide_polygons_history
            (polygon_id, landslide_id, role, area, geom, operation, edited_by_id)
        SELECT id, landslide_id, role, area, geom, %s, %s
        FROM landslide_polygons WHERE id = %s
    """, (operation, uid, polygon_id))


@inventory_editor_required
@require_POST
def manage_polygons_save(request, landslide_id):
    """Persist in-app polygon geometry edits for one landslide.

    JSON body: {updates:[{db_id, geometry}], inserts:[{role, geometry}],
    deletes:[db_id, ...]} where geometry is a GeoJSON Polygon (WGS84) from
    Terra Draw. Pre-edit rows are snapshotted to landslide_polygons_history;
    all writes run in one transaction, then the rule cascade recomputes
    area/centroid/size/class, the edit is audited, and caches are invalidated.
    Returns {ok, summary} or {ok:false, error}."""
    from . import derived

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON body.'}, status=400)

    updates = payload.get('updates') or []
    inserts = payload.get('inserts') or []
    deletes = payload.get('deletes') or []

    conn = _get_conn()
    try:
        cur = conn.cursor()

        cur.execute("SELECT landslide_type FROM landslides WHERE id = %s", (landslide_id,))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return JsonResponse({'ok': False, 'error': 'Landslide not found.'}, status=404)
        ls_type = row[0]
        primary_role = 'source' if ls_type == 'catastrophic' else 'body'

        cur.execute("SELECT id FROM landslide_polygons WHERE landslide_id = %s",
                    (landslide_id,))
        current_ids = {r[0] for r in cur.fetchall()}

        try:
            del_ids = {int(d) for d in deletes}
            upd_ids = {int(u['db_id']) for u in updates}
        except (TypeError, ValueError, KeyError):
            conn.rollback()
            return JsonResponse({'ok': False, 'error': 'Malformed update/delete ids.'}, status=400)

        bad = (del_ids | upd_ids) - current_ids
        if bad:
            conn.rollback()
            return JsonResponse(
                {'ok': False, 'error': f'Polygon id(s) {sorted(bad)} are not on this landslide.'},
                status=400)

        # A landslide must retain at least one polygon.
        if not (current_ids - del_ids) and not inserts:
            conn.rollback()
            return JsonResponse(
                {'ok': False, 'error': 'A landslide must keep at least one polygon. '
                                       'Deprecate the record instead of deleting all geometry.'},
                status=400)

        def _area_m2(geojson_str):
            cur.execute(f"SELECT ST_Area(ST_Transform({_GEOM_WRITE_EXPR}, 3338))",
                        (geojson_str,))
            return cur.fetchone()[0] or 0.0

        n_upd = n_ins = n_del = 0

        # ---- DELETE (snapshot first) ----
        for did in del_ids:
            _snapshot_polygon(cur, did, landslide_id, 'delete', request.user)
            cur.execute("DELETE FROM landslide_polygons WHERE id = %s", (did,))
            n_del += 1

        # ---- UPDATE geometry (skip true no-ops; snapshot before rewrite) ----
        for u in updates:
            did = int(u['db_id'])
            gj = json.dumps(u['geometry'])
            cur.execute(f"SELECT ST_Equals(geom, {_GEOM_WRITE_EXPR}) "
                        f"FROM landslide_polygons WHERE id = %s", (gj, did))
            if cur.fetchone()[0]:
                continue  # unchanged — never rewrite (keeps round-trip pristine)
            if _area_m2(gj) < _POLY_MIN_AREA_M2:
                conn.rollback()
                return JsonResponse(
                    {'ok': False, 'error': f'Polygon {did} collapsed to near-zero area.'},
                    status=400)
            _snapshot_polygon(cur, did, landslide_id, 'update', request.user)
            cur.execute(f"UPDATE landslide_polygons SET geom = {_GEOM_WRITE_EXPR} "
                        f"WHERE id = %s", (gj, did))
            n_upd += 1

        # ---- INSERT new polygons (role required; is_primary inferred) ----
        cur.execute("SELECT role FROM landslide_polygons "
                    "WHERE landslide_id = %s AND is_primary", (landslide_id,))
        existing_primary_roles = {r[0] for r in cur.fetchall()}
        for ins in inserts:
            role = (ins.get('role') or '').strip().lower()
            if role not in _POLY_ROLES:
                conn.rollback()
                return JsonResponse(
                    {'ok': False, 'error': f'Invalid role "{role}". Use source / body / deposit.'},
                    status=400)
            gj = json.dumps(ins.get('geometry'))
            if _area_m2(gj) < _POLY_MIN_AREA_M2:
                conn.rollback()
                return JsonResponse(
                    {'ok': False, 'error': 'A drawn polygon has near-zero area.'}, status=400)
            # Primary only if it's the type's primary role and none exists yet.
            is_primary = (role == primary_role) and (role not in existing_primary_roles)
            if is_primary:
                existing_primary_roles.add(role)
            cur.execute(
                f"INSERT INTO landslide_polygons (landslide_id, role, is_primary, geom) "
                f"VALUES (%s, %s, %s, {_GEOM_WRITE_EXPR})",
                (landslide_id, role, is_primary, gj))
            n_ins += 1

        # Recompute area/centroid/size_inclusion/class from the new geometry.
        derived.apply_rules_for_landslide(cur, landslide_id)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return JsonResponse(
            {'ok': False, 'error': f'Save failed — no changes were saved. {exc}'},
            status=500)
    finally:
        _put_conn(conn)

    from .models import LandslideEditMeta
    LandslideEditMeta.objects.update_or_create(
        landslide_id=landslide_id, defaults={'last_edited_by': request.user})
    _invalidate('features', 'home_counts', 'unclassified_count',
                'timed_events', 'timeline_events', 'slug_map', 'slug_for_id')

    # Return the recomputed geometry-derived fields + the current polygons so the
    # editor can refresh them in place WITHOUT a page reload — a reload would
    # discard any unsaved edits the user has in the form (e.g. description).
    _DERIVED_COLS = ['area_body', 'area_source', 'area_deposit',
                     'centroid_lat', 'centroid_lon', 'centroid_albers_x', 'centroid_albers_y',
                     'volume_body', 'volume_source', 'volume_deposit',
                     'volume_estimated', 'volume_preferred', 'size_inclusion', 'landslide_class']
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(_DERIVED_COLS)} FROM landslides WHERE id = %s",
                    (landslide_id,))
        row = cur.fetchone()
        derived_vals = dict(zip(_DERIVED_COLS, row)) if row else {}
        cur.execute("""
            SELECT json_build_object('type', 'FeatureCollection', 'features',
                COALESCE(json_agg(json_build_object(
                    'type', 'Feature', 'geometry', ST_AsGeoJSON(geom, 15)::json,
                    'properties', json_build_object('db_id', id, 'role', role, 'is_primary', is_primary)
                )), '[]'::json))
            FROM landslide_polygons WHERE landslide_id = %s
        """, (landslide_id,))
        polygons = cur.fetchone()[0]
        conn.rollback()
    finally:
        _put_conn(conn)

    return JsonResponse({'ok': True,
                         'summary': {'updated': n_upd, 'inserted': n_ins, 'deleted': n_del},
                         'derived': derived_vals, 'polygons': polygons})


# ---------------------------------------------------------------------------
# Per-field autosave (edit/review form). Each scalar field saves on blur via
# manage_edit_field; rule-input fields re-run the cascade and return the
# updated derived columns so the form refreshes in place.
# ---------------------------------------------------------------------------
_HUMAN_DATE_FORMATS = ('%Y-%m-%d', '%d-%b-%Y', '%d %b %Y', '%d-%B-%Y',
                       '%d %B %Y', '%m/%d/%Y')


def _parse_human_date(s):
    import datetime as _dt
    s = (s or '').strip()
    if not s:
        return None
    for fmt in _HUMAN_DATE_FORMATS:
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f'Unrecognized date "{s}" — try 14-Sep-2010.')


def _coerce_field_value(udt, value):
    """Coerce a JSON value to the column's Python type for a single-field save.
    Raises ValueError with an editor-friendly message on bad input."""
    import datetime as _dt
    if value is None:
        return None
    if udt == 'bool':
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in ('true', 't', 'yes', 'y', '1', 'on'):
            return True
        if s in ('false', 'f', 'no', 'n', '0', '', 'off'):
            return False
        raise ValueError(f'Expected yes/no, got "{value}".')
    if udt == 'date':
        return _parse_human_date(value) if isinstance(value, str) else value
    if udt == 'timestamptz':
        s = str(value).strip()
        return _dt.datetime.fromisoformat(s) if s else None
    if udt in ('int4', 'int8'):
        s = str(value).strip()
        return int(s) if s else None
    if udt == 'float8':
        s = str(value).strip()
        return float(s) if s else None
    s = value if isinstance(value, str) else str(value)
    s = s.strip()
    return s or None


@inventory_editor_required
@require_POST
def manage_edit_field(request, landslide_id):
    """Autosave a single editable field. Body: {name, value}. Returns
    {ok, derived?} or {ok:false, error}. Re-runs the rule cascade (and returns
    the refreshed derived columns) when the saved field feeds a rule."""
    from . import derived as _derived
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON body.'}, status=400)
    name = (payload.get('name') or '').strip()

    # Columns that feed a rule (cascade on edit) vs columns a rule writes
    # (editing one is a manual override — don't cascade and clobber it).
    rule_inputs, rule_targets = set(), set()
    for fn in _derived.RULES.values():
        rule_targets.add(fn.target_column)
        for inp in getattr(fn, 'inputs', ()):
            col = inp.split('.', 1)[1] if '.' in inp else inp
            if '.' not in inp or inp.startswith('landslides.'):
                rule_inputs.add(col)

    conn = _get_conn()
    derived_vals = None
    try:
        cur = conn.cursor()
        cols = _discover_editable_columns(cur)
        col = next((c for c in cols if c['name'] == name), None)
        if not col:
            conn.rollback()
            return JsonResponse({'ok': False, 'error': 'Field is not editable.'}, status=400)
        try:
            val = _coerce_field_value(col['udt'], payload.get('value'))
        except ValueError as e:
            conn.rollback()
            return JsonResponse({'ok': False, 'error': str(e)}, status=400)
        if col.get('max_length') and isinstance(val, str) and len(val) > col['max_length']:
            conn.rollback()
            return JsonResponse({'ok': False, 'error': f'Too long (max {col["max_length"]}).'}, status=400)

        # Names must be unique (the disambiguation standard guarantees it): block
        # a rename that collides with another non-deprecated record, comparing on
        # the same normalized key as the import/draw paths (case + whitespace
        # insensitive). This is the rename-side counterpart to the draw-commit
        # attach hard-block — manage_edit_field is a generic per-field UPDATE, so
        # without this a rename to an existing name slips through silently.
        if name == 'unique_name' and isinstance(val, str) and val.strip():
            cur.execute(
                "SELECT id, unique_name FROM landslides "
                "WHERE id != %s AND deprecated_at IS NULL "
                "AND regexp_replace(lower(btrim(unique_name)), '\\s+', ' ', 'g') "
                "  = regexp_replace(lower(btrim(%s)), '\\s+', ' ', 'g')",
                (landslide_id, val))
            dup = cur.fetchone()
            if dup:
                conn.rollback()
                return JsonResponse({'ok': False, 'error': (
                    f'"{val}" is already used by landslide #{dup[0]} — names must be '
                    f'unique. Add a disambiguator per the naming standard '
                    f'(e.g. "{val} 2", or a letter/year).')}, status=409)

        # name is whitelisted from information_schema (not user-supplied SQL).
        cur.execute(f"UPDATE landslides SET {name} = %s WHERE id = %s", (val, landslide_id))
        if cur.rowcount == 0:
            conn.rollback()
            return JsonResponse({'ok': False, 'error': 'Record not found.'}, status=404)

        cascaded = (name in rule_inputs) and (name not in rule_targets)
        if cascaded:
            _derived.apply_rules_for_landslide(cur, landslide_id)
            _DERIVED_COLS = ['area_body', 'area_source', 'area_deposit', 'size_inclusion',
                             'landslide_class', 'creep_behavior', 'insar_creep',
                             'volume_estimated', 'volume_preferred', 'volume_method',
                             'centroid_lat', 'centroid_lon']
            cur.execute(f"SELECT {', '.join(_DERIVED_COLS)} FROM landslides WHERE id = %s",
                        (landslide_id,))
            row = cur.fetchone()
            derived_vals = dict(zip(_DERIVED_COLS, row)) if row else None
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return JsonResponse({'ok': False, 'error': f'Save failed: {exc}'}, status=500)
    finally:
        _put_conn(conn)

    from .models import LandslideEditMeta
    LandslideEditMeta.objects.update_or_create(
        landslide_id=landslide_id, defaults={'last_edited_by': request.user})
    _invalidate('features', 'home_counts', 'unclassified_count', 'flagged_count',
                'timed_events', 'timeline_events', 'slug_map', 'slug_for_id')

    # Render dates back in the human format for the field's own display refresh.
    out_val = val.strftime('%d-%b-%Y') if hasattr(val, 'strftime') and col['udt'] == 'date' else None
    return JsonResponse({'ok': True, 'derived': derived_vals, 'value': out_val})


@inventory_editor_required
def manage_new(request):
    """Create a brand-new landslide by drawing it in the browser.

    GET  → render the draw page (imagery map + Terra Draw + name/type/role form).
    POST → JSON {unique_name, landslide_type, polygons:[{role, geometry}]}.
           The drawn data is shaped into the same upload form the file-import
           path produces and run through `apply_import`, so it shares one
           insert + collision-detection + validation pipeline. Drawn Polygons
           are wrapped as MultiPolygon to match the landslide_polygons column.
           On success the record is inserted PENDING (reviewed_at NULL); the
           rule cascade is run so it's map-ready, and the editor is sent into
           the review flow. Returns {ok, redirect} or {ok:false, error}."""
    from . import derived
    from .io_geojson import apply_import, ImportError_

    if request.method != 'POST':
        return render(request, 'inventory/manage_new.html', {})

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON body.'}, status=400)

    unique_name = (payload.get('unique_name') or '').strip()
    ls_type     = (payload.get('landslide_type') or '').strip().lower()
    drawn       = payload.get('polygons') or []

    if not unique_name:
        return JsonResponse({'ok': False, 'error': 'A unique name is required.'}, status=400)
    if ls_type not in ('slow', 'catastrophic'):
        return JsonResponse({'ok': False, 'error': 'Landslide type must be slow or catastrophic.'}, status=400)
    if not drawn:
        return JsonResponse({'ok': False, 'error': 'Draw at least one polygon.'}, status=400)
    for d in drawn:
        if (d.get('role') or '').strip().lower() not in _POLY_ROLES:
            return JsonResponse({'ok': False, 'error': 'Each polygon needs a role (source / body / deposit).'}, status=400)
        g = d.get('geometry') or {}
        if g.get('type') not in ('Polygon', 'MultiPolygon'):
            return JsonResponse({'ok': False, 'error': 'Each polygon must have polygon geometry.'}, status=400)

    # Shape the drawn data into the file-import upload form. A synthetic string
    # id ties the polygons to the not-yet-inserted landslide; apply_import
    # treats string ids as new records (same as a fresh file upload).
    synth_id = 'draw-1'
    landslides_fc = {'type': 'FeatureCollection', 'features': [{
        'type': 'Feature', 'geometry': None,
        'properties': {'id': synth_id, 'unique_name': unique_name,
                       'landslide_type': ls_type},
    }]}
    poly_feats = []
    for i, d in enumerate(drawn, 1):
        g = d['geometry']
        # Coerce Polygon → MultiPolygon to match landslide_polygons.geom typmod
        # (apply_import inserts the geometry verbatim via ST_GeomFromGeoJSON).
        if g['type'] == 'Polygon':
            g = {'type': 'MultiPolygon', 'coordinates': [g['coordinates']]}
        poly_feats.append({
            'type': 'Feature', 'geometry': g,
            'properties': {'id': f'{synth_id}-p{i}', 'landslide_id': synth_id,
                           'role': d['role'].strip().lower()},
        })
    polygons_fc = {'type': 'FeatureCollection', 'features': poly_feats}

    try:
        apply_import(landslides_fc, polygons_fc, request.user,
                     common_fields=_editor_identity_fields(request.user))
    except ImportError_ as e:
        # Name-exact collision (unique_name taken) and other blocks land here.
        return JsonResponse({'ok': False, 'error': str(e)}, status=409)
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': f'Create failed — nothing saved. {exc}'}, status=500)

    # apply_import doesn't run the rule cascade; do it now so the new record has
    # area/centroid/class populated and shows on the map once reviewed.
    conn = _get_conn()
    new_id = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM landslides WHERE unique_name = %s", (unique_name,))
        r = cur.fetchone()
        if r:
            new_id = r[0]
            derived.apply_rules_for_landslide(cur, new_id)
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        _put_conn(conn)

    _invalidate('features', 'home_counts', 'unclassified_count',
                'timed_events', 'timeline_events', 'slug_map', 'slug_for_id')

    redirect_url = (reverse('inventory:manage_review', kwargs={'landslide_id': new_id})
                    if new_id else reverse('inventory:manage_list'))
    return JsonResponse({'ok': True, 'landslide_id': new_id, 'redirect': redirect_url})


# ---------------------------------------------------------------------------
# Draw-on-the-main-map: provisional landslide components, staged server-side.
#
# Editors draw polygons on the inventory map and assign each a name + role.
# Finished components are staged in `provisional_polygons` (per editor) so a
# reload/crash doesn't lose work. Polygons sharing a unique_name become one
# landslide on commit (reusing the import synthesize-by-name path). See the
# migrate_provisional_polygons command.
# ---------------------------------------------------------------------------
_PROV_DISPERSED_M = 5000.0   # same-name components farther apart than this → warn


def _draw_geom_to_multi(geom):
    """A drawn GeoJSON Polygon → MultiPolygon (the landslide_polygons typmod).
    MultiPolygon passes through unchanged. Returns None on anything else."""
    if not isinstance(geom, dict):
        return None
    if geom.get('type') == 'MultiPolygon':
        return geom
    if geom.get('type') == 'Polygon':
        return {'type': 'MultiPolygon', 'coordinates': [geom.get('coordinates')]}
    return None


def _provisional_rows(cur, editor_id):
    """This editor's staged components, geometry as GeoJSON."""
    cur.execute("""
        SELECT id, unique_name, role, ST_AsGeoJSON(geom)::json AS geom, created_at
        FROM provisional_polygons WHERE editor_id = %s
        ORDER BY unique_name, created_at
    """, (editor_id,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _provisional_fcs(rows):
    """Build the (empty landslides_fc, polygons_fc) the import pipeline expects.
    Each polygon is an orphan (landslide_id=None) carrying unique_name + role,
    so `_synthesize_landslides_from_flat_polygons` groups them by name and
    infers landslide_type from the roles."""
    poly_feats = [{
        'type': 'Feature', 'geometry': r['geom'],
        'properties': {'id': f'prov-{r["id"]}', 'landslide_id': None,
                       'unique_name': r['unique_name'], 'role': r['role']},
    } for r in rows]
    return ({'type': 'FeatureCollection', 'features': []},
            {'type': 'FeatureCollection', 'features': poly_feats})


def _draw_existing_landslides(cur, names):
    """{lower(name): (id, landslide_type)} for non-deprecated landslides whose
    unique_name matches any of `names`. A staged group whose name already exists
    is *attached* to that landslide (e.g. drawing a source for a committed
    deposit) rather than creating a duplicate. unique_name is unique, so at most
    one match per name."""
    if not names:
        return {}
    cur.execute("SELECT id, unique_name, landslide_type FROM landslides "
                "WHERE lower(unique_name) = ANY(%s) AND deprecated_at IS NULL",
                ([n.lower() for n in names],))
    return {r[1].lower(): (r[0], r[2]) for r in cur.fetchall()}


def _editor_identity_fields(user):
    """Defaults stamped onto records an editor creates, matching the file-import
    preview's auto-fill: `owner` = username (canonical id), `noted_by` = full
    name (display credit). Applied via apply_import's common_fields."""
    fields = {}
    if getattr(user, 'username', None):
        fields['owner'] = user.username
    full = (user.get_full_name() or '').strip() if hasattr(user, 'get_full_name') else ''
    if full:
        fields['noted_by'] = full
    return fields


@inventory_editor_required
@require_POST
def manage_draw_stage(request):
    """Stage one finished drawn polygon. Body: {unique_name, role, geometry}."""
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON body.'}, status=400)

    name = (payload.get('unique_name') or '').strip()
    role = (payload.get('role') or '').strip().lower()
    multi = _draw_geom_to_multi(payload.get('geometry'))
    if not name:
        return JsonResponse({'ok': False, 'error': 'A name is required.'}, status=400)
    if role not in _POLY_ROLES:
        return JsonResponse({'ok': False, 'error': 'Role must be source / body / deposit.'}, status=400)
    if multi is None:
        return JsonResponse({'ok': False, 'error': 'Polygon geometry required.'}, status=400)

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"""
            INSERT INTO provisional_polygons (editor_id, unique_name, role, geom)
            VALUES (%s, %s, %s, {_GEOM_WRITE_EXPR})
            RETURNING id, ST_AsGeoJSON(geom)::json
        """, (request.user.id, name, role, json.dumps(multi)))
        new_id, geom = cur.fetchone()
        # Warn if the name already belongs to an active landslide.
        cur.execute("SELECT 1 FROM landslides WHERE lower(unique_name) = lower(%s) "
                    "AND deprecated_at IS NULL LIMIT 1", (name,))
        name_in_db = cur.fetchone() is not None
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return JsonResponse({'ok': False, 'error': f'Stage failed: {exc}'}, status=500)
    finally:
        _put_conn(conn)

    return JsonResponse({'ok': True, 'name_in_db': name_in_db, 'component': {
        'id': new_id, 'unique_name': name, 'role': role, 'geometry': geom}})


@inventory_editor_required
@require_safe
def manage_draw_list(request):
    """This editor's staged components (for resume-on-load + the queue panel)."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        rows = _provisional_rows(cur, request.user.id)
        conn.rollback()
    finally:
        _put_conn(conn)
    return JsonResponse({'ok': True, 'components': [
        {'id': r['id'], 'unique_name': r['unique_name'], 'role': r['role'],
         'geometry': r['geom']} for r in rows]})


@inventory_editor_required
@require_POST
def manage_draw_delete(request):
    """Delete staged components. Body: {ids:[...]} or {all:true}."""
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON body.'}, status=400)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if payload.get('all'):
            cur.execute("DELETE FROM provisional_polygons WHERE editor_id = %s",
                        (request.user.id,))
        else:
            ids = payload.get('ids') or []
            try:
                ids = [int(i) for i in ids]
            except (TypeError, ValueError):
                conn.rollback()
                return JsonResponse({'ok': False, 'error': 'Bad ids.'}, status=400)
            if ids:
                cur.execute("DELETE FROM provisional_polygons "
                            "WHERE editor_id = %s AND id = ANY(%s)", (request.user.id, ids))
        n = cur.rowcount
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return JsonResponse({'ok': False, 'error': f'Delete failed: {exc}'}, status=500)
    finally:
        _put_conn(conn)
    return JsonResponse({'ok': True, 'deleted': n})


@inventory_editor_required
@require_safe
def manage_draw_preview(request):
    """Pre-commit report: how the staged components group into landslides, plus
    warnings (so the editor isn't surprised by a block at commit time)."""
    from .io_geojson import compute_diff

    conn = _get_conn()
    try:
        cur = conn.cursor()
        rows = _provisional_rows(cur, request.user.id)
        # Bounding-box diagonal (EPSG:3338, metres) per name — a dispersed
        # same-name group is probably two sites typed with one name.
        cur.execute("""
            SELECT unique_name,
                   sqrt(power(ST_XMax(e) - ST_XMin(e), 2) + power(ST_YMax(e) - ST_YMin(e), 2))
            FROM (SELECT unique_name, ST_Extent(ST_Transform(geom, 3338)) AS e
                  FROM provisional_polygons WHERE editor_id = %s GROUP BY unique_name) s
        """, (request.user.id,))
        spread = {n: (d or 0.0) for n, d in cur.fetchall()}
        names = sorted({r['unique_name'] for r in rows})
        existing = _draw_existing_landslides(cur, names)   # lower(name) -> (id, type)
        # Distance (m, 3338) from each attach group's staged polygons to the
        # existing landslide it would join — a far one is likely a name reuse.
        attach_dist = {}
        attach_names = [n for n in names if n.lower() in existing]
        if attach_names:
            cur.execute("""
                SELECT pp.unique_name,
                       ST_Distance(ST_Transform(ST_Centroid(ST_Collect(pp.geom)), 3338),
                                   ST_Transform(le.geom, 3338))
                FROM provisional_polygons pp
                JOIN LATERAL (
                    SELECT ST_Centroid(ST_Collect(lp.geom)) AS geom
                    FROM landslide_polygons lp JOIN landslides l ON l.id = lp.landslide_id
                    WHERE lower(l.unique_name) = lower(pp.unique_name) AND l.deprecated_at IS NULL
                ) le ON true
                WHERE pp.editor_id = %s AND lower(pp.unique_name) = ANY(%s)
                GROUP BY pp.unique_name, le.geom
            """, (request.user.id, [n.lower() for n in attach_names]))
            for nm, d in cur.fetchall():
                attach_dist[nm.lower()] = d or 0.0
        conn.rollback()
    finally:
        _put_conn(conn)

    if not rows:
        return JsonResponse({'ok': True, 'groups': [], 'has_block': False})

    # Group by name (preserve role multiset) for the display.
    by_name = {}
    for r in rows:
        by_name.setdefault(r['unique_name'], []).append(r['role'])

    # The import collision/block check only applies to NEW names; existing names
    # attach to their landslide and never block.
    create_rows = [r for r in rows if r['unique_name'].lower() not in existing]
    inferred, coll_by_name, has_block = {}, {}, False
    if create_rows:
        landslides_fc, polygons_fc = _provisional_fcs(create_rows)
        diff = compute_diff(landslides_fc, polygons_fc)
        for a in diff['landslides']['would_add']:
            props = (a.get('properties') or a)
            nm = props.get('unique_name')
            if nm:
                inferred[nm] = props.get('landslide_type')
        for c in diff.get('collisions', []):
            coll_by_name.setdefault(c.get('unique_name'), []).append(c)
        has_block = bool(diff.get('has_block'))

    groups = []
    for name, roles in by_name.items():
        warnings = []
        ex = existing.get(name.lower())
        if ex:
            warnings.append(f'→ adds to existing landslide #{ex[0]} ({ex[1] or "?"})')
            if attach_dist.get(name.lower(), 0) > _PROV_DISPERSED_M:
                warnings.append(f'but {attach_dist[name.lower()]/1000:.1f} km away — same name, different place?')
        else:
            if spread.get(name, 0) > _PROV_DISPERSED_M:
                warnings.append(f'components span {spread[name]/1000:.1f} km — same name, far apart?')
            cols = coll_by_name.get(name, [])
            if any(c.get('resolution') == 'block' for c in cols):
                warnings.append('NAME ALREADY EXISTS — will block commit; rename')
            elif cols:
                warnings.append('overlaps an existing landslide — possible duplicate')
        if len(roles) > 4:
            warnings.append(f'{len(roles)} polygons under one name')
        dup = {x for x in roles if roles.count(x) > 1}
        if dup:
            warnings.append('duplicate role(s): ' + ', '.join(sorted(dup)))
        groups.append({'unique_name': name, 'roles': roles,
                       'attach_to': (ex[0] if ex else None),
                       'landslide_type': inferred.get(name) or (ex[1] if ex else None),
                       'warnings': warnings})

    return JsonResponse({'ok': True, 'groups': groups, 'has_block': has_block})


@inventory_editor_required
@require_POST
def manage_draw_commit(request):
    """Commit this editor's staged components, then open the review queue.

    A staged name that does NOT exist yet → a new pending landslide (one per
    name, via the import synthesize path). A staged name that ALREADY exists
    (non-deprecated) → its polygons are *attached* to that landslide (e.g. a
    source drawn for an already-committed deposit), with is_primary inferred —
    so "same name = same landslide" holds across commits."""
    from . import derived
    from .io_geojson import apply_import, ImportError_

    conn = _get_conn()
    far = []
    try:
        cur = conn.cursor()
        rows = _provisional_rows(cur, request.user.id)
        names = sorted({r['unique_name'] for r in rows})
        existing = _draw_existing_landslides(cur, names)
        # How far is each attach group from the existing same-named landslide?
        # A large gap means it's a *different* site sharing a name (the Moose
        # Creek case) — block rather than silently merge.
        attach_names = [n for n in names if n.lower() in existing]
        if attach_names:
            cur.execute("""
                SELECT pp.unique_name,
                       ST_Distance(ST_Transform(ST_Centroid(ST_Collect(pp.geom)), 3338),
                                   ST_Transform(le.geom, 3338))
                FROM provisional_polygons pp
                JOIN LATERAL (
                    SELECT ST_Centroid(ST_Collect(lp.geom)) AS geom
                    FROM landslide_polygons lp JOIN landslides l ON l.id = lp.landslide_id
                    WHERE lower(l.unique_name) = lower(pp.unique_name) AND l.deprecated_at IS NULL
                ) le ON true
                WHERE pp.editor_id = %s AND lower(pp.unique_name) = ANY(%s)
                GROUP BY pp.unique_name, le.geom
            """, (request.user.id, [n.lower() for n in attach_names]))
            far = [(nm, d) for nm, d in cur.fetchall() if (d or 0) > _PROV_DISPERSED_M]
        conn.rollback()
    finally:
        _put_conn(conn)
    if not rows:
        return JsonResponse({'ok': False, 'error': 'Nothing staged to commit.'}, status=400)
    if far:
        nm, d = far[0]
        return JsonResponse({'ok': False, 'error':
            f'"{nm}" already exists ~{d/1000:.0f} km away — that looks like a different '
            f'landslide sharing a name. Give this one a distinct name (e.g. "{nm} 2") '
            f'before committing.'}, status=409)

    create_rows = [r for r in rows if r['unique_name'].lower() not in existing]
    attach_rows = [r for r in rows if r['unique_name'].lower() in existing]
    created_names = sorted({r['unique_name'] for r in create_rows})

    affected_ids = set()

    # 1) New names → create pending landslides via the import pipeline.
    if create_rows:
        landslides_fc, polygons_fc = _provisional_fcs(create_rows)
        try:
            # Landslides are synthesized from the polygons inside apply_import,
            # so common_fields wouldn't reach them; owner/noted_by are stamped
            # post-insert below instead.
            apply_import(landslides_fc, polygons_fc, request.user)
        except ImportError_ as e:
            return JsonResponse({'ok': False, 'error': str(e)}, status=409)
        except Exception as exc:
            return JsonResponse({'ok': False, 'error': f'Commit failed — nothing saved. {exc}'}, status=500)
        # Clear the created rows immediately (apply_import committed the records),
        # so a retry after a later failure can't double-insert them.
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM provisional_polygons WHERE editor_id = %s AND id = ANY(%s)",
                        (request.user.id, [r['id'] for r in create_rows]))
            conn.commit()
        finally:
            _put_conn(conn)

    # 2) Existing names → attach their polygons to that landslide; cascade all
    #    touched records (new + attached); clear the attached staged rows.
    conn = _get_conn()
    try:
        cur = conn.cursor()
        created_ids = []
        if created_names:
            cur.execute("SELECT id FROM landslides WHERE unique_name = ANY(%s)", (created_names,))
            created_ids = [r[0] for r in cur.fetchall()]
            affected_ids.update(created_ids)
        # Stamp the editor's identity on the new records (only where unset), the
        # same auto-fill the file-import preview applies.
        identity = _editor_identity_fields(request.user)
        if created_ids and identity.get('owner'):
            cur.execute("UPDATE landslides SET owner = %s WHERE id = ANY(%s) AND owner IS NULL",
                        (identity['owner'], created_ids))
        if created_ids and identity.get('noted_by'):
            cur.execute("UPDATE landslides SET noted_by = %s WHERE id = ANY(%s) AND noted_by IS NULL",
                        (identity['noted_by'], created_ids))
        attach_primary = {}   # ex_id -> primary role for its type
        for r in attach_rows:
            ex_id, ex_type = existing[r['unique_name'].lower()]
            attach_primary[ex_id] = 'source' if ex_type == 'catastrophic' else 'body'
            # Copy the staged geometry straight over (already MultiPolygon/4326).
            cur.execute("""
                INSERT INTO landslide_polygons (landslide_id, role, geom)
                SELECT %s, %s, geom FROM provisional_polygons WHERE id = %s
            """, (ex_id, r['role'], r['id']))
            affected_ids.add(ex_id)
        # Normalise is_primary to the role convention (catastrophic→source,
        # slow→body) so adding e.g. a source to a deposit-only record makes the
        # source primary and demotes the deposit — the centroid follows.
        for ex_id, primary_role in attach_primary.items():
            cur.execute("UPDATE landslide_polygons SET is_primary = (role = %s) WHERE landslide_id = %s",
                        (primary_role, ex_id))
        for lid in affected_ids:
            derived.apply_rules_for_landslide(cur, lid)
        if attach_rows:
            cur.execute("DELETE FROM provisional_polygons WHERE editor_id = %s AND id = ANY(%s)",
                        (request.user.id, [r['id'] for r in attach_rows]))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return JsonResponse({'ok': False, 'error': f'Commit failed attaching polygons — {exc}'}, status=500)
    finally:
        _put_conn(conn)

    _invalidate('features', 'home_counts', 'unclassified_count',
                'timed_events', 'timeline_events', 'slug_map', 'slug_for_id')

    # Prefer a freshly-created pending record for review; else an attached one.
    next_id = _first_pending_landslide() or (sorted(affected_ids)[0] if affected_ids else None)
    redirect_url = (reverse('inventory:manage_review', kwargs={'landslide_id': next_id})
                    if next_id else reverse('inventory:manage_list'))
    return JsonResponse({'ok': True, 'created': created_names,
                         'attached': sorted({r['unique_name'] for r in attach_rows}),
                         'redirect': redirect_url})


_SUBSET_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]*$')


def _read_subset_form(request):
    """Pull subset fields from a POST request. Returns (data, error)."""
    slug = (request.POST.get('slug') or '').strip().lower()
    name = (request.POST.get('name') or '').strip()
    description   = (request.POST.get('description')   or '').strip() or None
    default_owner = (request.POST.get('default_owner') or '').strip() or None
    citation_info = (request.POST.get('citation_info') or '').strip() or None
    is_publication = bool(request.POST.get('is_publication'))
    if not slug:
        return None, "Slug is required."
    if not _SUBSET_SLUG_RE.match(slug):
        return None, "Slug must be lowercase alphanumeric + hyphens, starting with alphanumeric."
    if not name:
        return None, "Name is required."
    return {
        'slug': slug, 'name': name,
        'description': description, 'default_owner': default_owner,
        'is_publication': is_publication, 'citation_info': citation_info,
    }, None


@inventory_editor_required
def manage_subsets(request):
    """List + create. POST creates a new subset; GET shows the list + new-form."""
    error_msg = None
    if request.method == 'POST':
        data, error_msg = _read_subset_form(request)
        if data:
            conn = _get_conn()
            try:
                cur = conn.cursor()
                try:
                    cur.execute("""
                        INSERT INTO subsets (slug, name, description, default_owner,
                                              is_publication, citation_info)
                        VALUES (%(slug)s, %(name)s, %(description)s, %(default_owner)s,
                                %(is_publication)s, %(citation_info)s)
                    """, data)
                    conn.commit()
                    return redirect('inventory:manage_subsets')
                except psycopg2.IntegrityError as exc:
                    conn.rollback()
                    error_msg = f"Could not create subset: {exc}"
            finally:
                _put_conn(conn)

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT s.id, s.slug, s.name, s.description, s.default_owner,
                   s.is_publication, s.citation_info, s.created_at,
                   COUNT(lps.landslide_id) AS member_count
            FROM subsets s
            LEFT JOIN landslide_subsets lps ON lps.subset_id = s.id
            GROUP BY s.id
            ORDER BY s.is_publication DESC, s.name
        """)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        subsets = [dict(zip(cols, r)) for r in rows]
        conn.rollback()
    finally:
        _put_conn(conn)
    return render(request, 'inventory/manage_subsets.html', {
        'subsets':   subsets,
        'error_msg': error_msg,
        'form':      request.POST if request.method == 'POST' else {},
    })


@inventory_editor_required
def manage_subset_edit(request, slug):
    """Edit one subset's metadata (not memberships — those happen via the
    per-landslide edit form)."""
    error_msg = None
    subset   = None
    new_slug = None
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM subsets WHERE slug = %s", (slug,))
        row = cur.fetchone()
        if not row:
            return redirect('inventory:manage_subsets')
        subset_id = row[0]

        if request.method == 'POST':
            data, error_msg = _read_subset_form(request)
            if data:
                try:
                    cur.execute("""
                        UPDATE subsets SET
                            slug=%(slug)s, name=%(name)s, description=%(description)s,
                            default_owner=%(default_owner)s,
                            is_publication=%(is_publication)s,
                            citation_info=%(citation_info)s
                        WHERE id=%(id)s
                    """, {**data, 'id': subset_id})
                    conn.commit()
                    new_slug = data['slug']
                except psycopg2.IntegrityError as exc:
                    conn.rollback()
                    error_msg = f"Could not update: {exc}"

        if new_slug is None:
            cur.execute("""
                SELECT s.id, s.slug, s.name, s.description, s.default_owner,
                       s.is_publication, s.citation_info,
                       COUNT(lps.landslide_id) AS member_count
                FROM subsets s
                LEFT JOIN landslide_subsets lps ON lps.subset_id = s.id
                WHERE s.id = %s GROUP BY s.id
            """, (subset_id,))
            row = cur.fetchone()
            cols = [d[0] for d in cur.description]
            subset = dict(zip(cols, row))
            conn.rollback()
    finally:
        _put_conn(conn)

    if new_slug is not None:
        _invalidate('features')
        return redirect('inventory:manage_subset_edit', slug=new_slug)
    return render(request, 'inventory/manage_subset_edit.html', {
        'subset':    subset,
        'error_msg': error_msg,
    })


@inventory_editor_required
def manage_subset_delete(request, slug):
    """POST-only: drop a subset. Membership rows cascade. Records survive."""
    if request.method != 'POST':
        return redirect('inventory:manage_subsets')
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM subsets WHERE slug = %s", (slug,))
        conn.commit()
    finally:
        _put_conn(conn)
    _invalidate('features')
    return redirect('inventory:manage_subsets')


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
    # Never bounce back to the preview page itself (a stale ?next= chain after
    # login would otherwise loop here showing the form).
    if not next_url.startswith('/inventory/') or next_url.startswith(request.path):
        next_url = '/inventory/'

    # Already past the barrier (logged in, or password already entered)? Step
    # aside — the middleware lets these through, so showing the form here is
    # just a redirect-chain artifact (e.g. logging in via ?next=/inventory/preview/).
    if request.user.is_authenticated or request.session.get(_PREVIEW_SESSION_KEY):
        return redirect(next_url)

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
