"""Glacier-dynamics map app (/glaciers/) — sibling of the inventory map.

Deliberately thin: the page reuses the inventory's shared JS modules
(basemaps.js for basemap descriptors/style building, ls_overlays.js for the
ITS_LIVE + Hugonnet overlay definitions) so the two apps cannot drift; the
app-specific code is glaciers.js (Lagrangian tracer engine + minimal UI).

Tracer bundles are built offline by tools/build_glacier_tracers.py into
data/glaciers/ (volume-mounted, gitignored): per glacier a small JSON header
plus a Float32 .bin of annual vx/vy fields, seasonal amplitude/phase, and
the land-ice mask. Served here with immutable caching — the JS carries a
?v= token (TRACER_DATA_V in glaciers.js), bump it when bundles rebuild.

Since 2026-11 the page is also the retreat-history browser: glacier identity
(RGI 7.0) and mirrored extent sources live in PostGIS (glaciers/sources.py)
and reach the map as vector tiles and small JSON endpoints defined at the
bottom of this module. Public, like /inventory/*.
"""
import json
import re
import time
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe

from . import sources

DATA_DIR = Path(settings.BASE_DIR) / 'data' / 'glaciers'

# The raw image-pair stack (/glaciers/pairs/, the fitted-pair overlays and the
# "robust pair fit" tracer field) is research scaffolding: it depends on
# multi-hundred-MB sweeps that are not deployed, and it is still changing. It
# is exposed only where those sweeps exist, so production serves the finished
# tracer app and nothing half-built.
def experimental_enabled():
    return (DATA_DIR / 'experiments').is_dir()


def _catalog():
    """[{slug, name, center, zoom}, …] from the bundle headers on disk."""
    out = []
    if DATA_DIR.is_dir():
        for p in sorted(DATA_DIR.glob('*.json')):
            try:
                h = json.loads(p.read_text())
            except (ValueError, OSError):
                continue
            # Only real site bundles. Without this test the regional manifest
            # (and any future sidecar json) showed up as a phantom site in the
            # pulldown, selectable and broken.
            if not (h.get('name') and h.get('center') and h.get('bin')):
                continue
            out.append({'slug': p.stem, 'name': h['name'],
                        'center': h['center'], 'zoom': h.get('zoom', 10)})
    return out


def _static_v():
    """A token that changes when any script this page loads changes. Dev
    serves static files under fixed names and the browser keeps them: after
    an edit, a normal reload ran an old scarps.js against a new
    gl_network.js and the editing button simply failed (2026-11-16)."""
    base = Path(settings.BASE_DIR)
    newest = 0
    for pat in ('glaciers/static/glaciers/js/*.js', 'inventory/static/inventory/js/*.js'):
        for f in base.glob(pat):
            try:
                newest = max(newest, int(f.stat().st_mtime))
            except OSError:
                pass
    return newest


@require_safe
def home(request):
    return render(request, 'glaciers/map.html', {
        'static_v': _static_v(),
        'catalog_json': json.dumps(_catalog()),
        'experimental': experimental_enabled(),
    })


@require_safe
def pairs(request):
    """Raw image-pair viewer — the literal counterpart to the tracer app.
    Bundle built by tools/build_pair_vectors.py from a sweep_pairs.py sweep."""
    if not experimental_enabled():
        raise Http404
    exp = DATA_DIR / 'experiments'
    avail = sorted(p.stem[:-len('_vectors')] for p in exp.glob('*_vectors.json')) \
        if exp.is_dir() else []
    sel = request.GET.get('bundle', 'columbia_pairs')
    if avail and sel not in avail:
        sel = avail[0]
    return render(request, 'glaciers/pairs.html', {
        'bundle': sel,
        'bundles_json': json.dumps(avail),
    })


@require_safe
def tracer_data(request, name):
    """Serve a tracer bundle file (<slug>.json header or <slug>.bin arrays).
    The URL regex constrains `name` to a safe token — no traversal.

    .bin bundles are stored pre-gzipped (<slug>.bin.gz) and served with
    Content-Encoding: gzip against the .bin URL — the browser inflates and
    the JS sees the raw int16 buffer. ~4-5x smaller on the wire."""
    # Site bundles live in data/glaciers/; experiment bundles (raw
    # image-pair vectors) in data/glaciers/experiments/. Try both — the
    # URL regex already constrains `name` to a safe token.
    for base in (DATA_DIR, DATA_DIR / 'experiments', DATA_DIR / 'region'):
        if (base / name).exists() or (base / (name + '.gz')).exists():
            DIR = base
            break
    else:
        raise Http404
    gz = DIR / (name + '.gz')
    path = DIR / name
    encoding = None
    # Pre-gzipped variants served against the plain URL: originally .bin only
    # (tracer bundles); .json joined when the 26.7 MB IceBridge ARES layer
    # took 17-40 s to fetch uncompressed and read as "not plotting" — the
    # data was fine, the wait was silent. ~5x smaller on the wire.
    if (name.endswith('.bin') or name.endswith('.json')) and gz.exists():
        path, encoding = gz, 'gzip'
    try:
        fh = open(path, 'rb')
    except (FileNotFoundError, NotADirectoryError):
        raise Http404
    ctype = 'application/json' if name.endswith('.json') else 'application/octet-stream'
    resp = FileResponse(fh, content_type=ctype)
    if encoding:
        resp['Content-Encoding'] = encoding
    resp['Cache-Control'] = 'public, max-age=31536000, immutable'
    return resp


# ---------------------------------------------------------------------------
# Retreat histories: glacier identity + mirrored extent sources
# ---------------------------------------------------------------------------
# Tiles and JSON are cached by the browser against a ?v=<stamp> token. The
# stamp is a file because production runs two gunicorn workers and a fetch
# runs in neither: each worker re-reads it when its mtime changes.
STAMP_FILE = DATA_DIR / '.extent_stamp'


def bump_stamp():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STAMP_FILE.write_text(str(int(time.time())))


def _stamp():
    try:
        return STAMP_FILE.read_text().strip() or '0'
    except OSError:
        return '0'


def _conn():
    from inventory.views import _get_conn, _put_conn
    return _get_conn, _put_conn


def _query(sql, params=None, one=False):
    get, put = _conn()
    conn = get()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone() if one else cur.fetchall()
    finally:
        conn.rollback()        # read-only: never leave a transaction open in the pool
        put(conn)


def _cached(resp, seconds=86400):
    resp['Cache-Control'] = 'public, max-age=%d' % seconds
    return resp


@require_safe
def glacier_redirect(request, gid):
    """/glaciers/g/<glacier id>/ -> the map with that glacier selected."""
    return HttpResponseRedirect('/glaciers/#g=' + gid)


# Smallest glacier (km2) drawn at each zoom: a statewide tile would otherwise
# carry twenty-seven thousand polygons, most of them under a pixel.
def _min_area(z):
    return {0: 100, 1: 100, 2: 100, 3: 100, 4: 50, 5: 20, 6: 5, 7: 1, 8: 0.2}.get(z, 0)


_OBS_MINZOOM = 6
# {gcol} is a Web Mercator tile-geometry column and {env} the envelope to test
# it against; at z12 and up the full-detail lon/lat geometry is used instead
# and transformed on the fly (few features per tile by then).
_TILE_SQL = """
WITH b AS (SELECT ST_TileEnvelope(%(z)s, %(x)s, %(y)s) AS m,
                  ST_TileEnvelope(%(z)s, %(x)s, %(y)s, margin => 0.02) AS mm,
                  ST_Transform(ST_TileEnvelope(%(z)s, %(x)s, %(y)s, margin => 0.02), 4326) AS g)
SELECT
 (SELECT ST_AsMVT(q, 'glaciers', 4096, 'geom') FROM (
    SELECT t.id AS gid, t.glims_id, t.name, round(t.area_km2::numeric, 2)::float AS area_km2,
           ST_AsMVTGeom({geom_t}, b.m, 4096, 64, true) AS geom
    FROM glaciers t, b
    WHERE t.{gcol} && b.{env} AND t.area_km2 >= %(amin)s) q WHERE q.geom IS NOT NULL),
 (SELECT ST_AsMVT(q, 'obs', 4096, 'geom') FROM (
    SELECT o.id, o.source, o.kind, o.glacier_id AS gid,
           extract(year FROM o.date_min)::int AS y0,
           extract(year FROM o.date_max)::int AS y1,
           ST_AsMVTGeom({geom_o}, b.m, 4096, 64, true) AS geom
    FROM glacier_obs o, b
    WHERE %(obs)s AND o.{gcol} && b.{env}
      AND (o.area_km2 IS NULL OR o.area_km2 >= %(amin)s)) q WHERE q.geom IS NOT NULL),
 (SELECT ST_AsMVT(q, 'lines', 4096, 'geom') FROM (
    SELECT c.glacier_id AS gid, c.is_main AS main,
           ST_AsMVTGeom(c.geom_s, b.m, 4096, 64, true) AS geom
    FROM glacier_centerlines c, b
    WHERE %(lines)s AND c.geom_s && b.mm AND (c.is_main OR %(z)s >= 10)) q WHERE q.geom IS NOT NULL)
"""

# Small-scale tiles are the expensive ones (a z6 tile holds thousands of
# outlines) and the same few are asked for by every visitor, so each worker
# keeps them. Keyed on the stamp, so a refetch empties it by itself.
_TILE_CACHE = {}
_TILE_CACHE_MAX = 600


@require_safe
def extent_tile(request, z, x, y):
    """One vector tile, three layers: `glaciers` (RGI 7.0 reference
    outlines), `obs` (every mirrored extent row; the page filters by source
    and year) and `lines` (RGI 7.0 centrelines, from z8; tributaries from z10)."""
    z, x, y = int(z), int(x), int(y)
    if z > 16 or x >= (1 << z) or y >= (1 << z):
        raise Http404
    key = (_stamp(), z, x, y)
    body = _TILE_CACHE.get(key) if z <= 9 else None
    if body is None:
        if z >= 12:
            f = dict(gcol='geom', env='g', geom_t='ST_Transform(t.geom, 3857)',
                     geom_o='ST_Transform(o.geom, 3857)')
        else:
            c = 'geom_c' if z <= 8 else 'geom_s'
            f = dict(gcol=c, env='mm', geom_t='t.' + c, geom_o='o.' + c)
        a, b, c = _query(_TILE_SQL.format(**f), {'z': z, 'x': x, 'y': y, 'amin': _min_area(z),
                                                 'obs': z >= _OBS_MINZOOM, 'lines': z >= 8}, one=True)
        body = bytes(a or b'') + bytes(b or b'') + bytes(c or b'')
        if z <= 9:
            if len(_TILE_CACHE) >= _TILE_CACHE_MAX:
                _TILE_CACHE.clear()
            _TILE_CACHE[key] = body
    return _cached(HttpResponse(body, content_type='application/vnd.mapbox-vector-tile'))


@require_safe
def api_sources(request):
    """The source registry with what is loaded, for the layer list and the
    citations."""
    stats = {r[0]: r for r in _query(
        'SELECT source, fetched_at, n, n_linked, year_min, year_max FROM glacier_sources')}
    out = []
    for sid, s in sources.SOURCES.items():
        st = stats.get(sid)
        out.append({
            'id': sid, 'title': s['title'], 'short': s['short'], 'what': s['what'],
            'cite': s['cite'], 'doi': s['doi'], 'url': s['url'], 'geom': s['geom'],
            'color': s['color'],
            'n': st[2] if st else 0, 'n_linked': st[3] if st else 0,
            'year_min': st[4] if st else None, 'year_max': st[5] if st else None,
            'fetched': st[1].date().isoformat() if st else None,
        })
    n_gl = _query('SELECT count(*), count(*) FILTER (WHERE name <> \'\') FROM glaciers', one=True)
    return _cached(JsonResponse({
        'sources': out,
        'identity': {'title': sources.RGI7['title'], 'short': sources.RGI7['short'],
                     'cite': sources.RGI7['cite'], 'doi': sources.RGI7['doi'],
                     'url': sources.RGI7['url'], 'n': n_gl[0], 'n_named': n_gl[1]},
        'stamp': _stamp(),
    }), 300)


@require_safe
def api_search(request):
    """Glaciers by name (or by GLIMS / RGI id). Largest first."""
    q = (request.GET.get('q') or '').strip()[:80]
    if len(q) < 2:
        return JsonResponse({'results': []})
    like = '%' + q.replace('\\', '').replace('%', '').replace('_', ' ').lower() + '%'
    rows = _query("""
        SELECT g.id, g.name, g.glims_id, g.area_km2, g.cenlon, g.cenlat,
               (SELECT count(*) FROM glacier_obs o WHERE o.glacier_id = g.id)
        FROM glaciers g
        WHERE lower(g.name) LIKE %s OR g.glims_id = %s OR g.id = %s
        ORDER BY (lower(g.name) = %s) DESC, g.area_km2 DESC NULLS LAST LIMIT 30""",
                  (like, q.upper(), q, q.lower()))
    return JsonResponse({'results': [
        {'id': r[0], 'name': r[1], 'glims_id': r[2], 'area_km2': r[3],
         'lon': r[4], 'lat': r[5], 'n_obs': r[6]} for r in rows]})


@require_safe
def api_glacier(request, gid):
    """One glacier: identity, its reference outline, and every mirrored
    observation matched to it, as GeoJSON at full detail."""
    g = _query("""
        SELECT id, glims_id, name, area_km2, src_date, term_type, surge_type, o2region,
               zmin_m, zmax_m, lmax_m, ST_AsGeoJSON(geom, 6),
               ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom)
        FROM glaciers WHERE id = %s""", (gid,), one=True)
    if not g:
        raise Http404
    obs = _query("""
        SELECT id, source, ext_id, kind, link_how, date_min, date_max, label, area_km2,
               props, ST_AsGeoJSON(geom, 6),
               ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom)
        FROM glacier_obs WHERE glacier_id = %s
        ORDER BY date_min NULLS LAST, source, area_km2 DESC NULLS LAST, id""", (gid,))
    bbox = [g[12], g[13], g[14], g[15]]
    feats = []
    for o in obs:
        bbox = [min(bbox[0], o[11]), min(bbox[1], o[12]), max(bbox[2], o[13]), max(bbox[3], o[14])]
        feats.append({'type': 'Feature', 'id': o[0], 'geometry': json.loads(o[10]), 'properties': {
            'id': o[0], 'source': o[1], 'ext_id': o[2], 'kind': o[3], 'link_how': o[4],
            'date_min': o[5].isoformat() if o[5] else None,
            'date_max': o[6].isoformat() if o[6] else None,
            'y0': o[5].year if o[5] else None, 'y1': o[6].year if o[6] else None,
            'label': o[7], 'area_km2': o[8], 'props': o[9]}})
    return _cached(JsonResponse({
        'type': 'FeatureCollection', 'features': feats,
        'glacier': {'id': g[0], 'rgi7_id': g[0], 'glims_id': g[1], 'name': g[2], 'area_km2': g[3],
                    'src_date': g[4].isoformat() if g[4] else None, 'term_type': g[5],
                    'surge_type': g[6], 'o2region': g[7], 'zmin_m': g[8], 'zmax_m': g[9],
                    'lmax_m': g[10], 'outline': json.loads(g[11]), 'bbox': bbox},
    }), 300)


@require_safe
def api_obs(request, obs_id):
    """One mirrored row with the publisher's own attributes (the map popup)."""
    o = _query("""
        SELECT o.id, o.source, o.ext_id, o.kind, o.link_how, o.date_min, o.date_max, o.label,
               o.area_km2, o.props, o.glacier_id, g.name
        FROM glacier_obs o LEFT JOIN glaciers g ON g.id = o.glacier_id
        WHERE o.id = %s""", (int(obs_id),), one=True)
    if not o:
        raise Http404
    return _cached(JsonResponse({
        'id': o[0], 'source': o[1], 'ext_id': o[2], 'kind': o[3], 'link_how': o[4],
        'date_min': o[5].isoformat() if o[5] else None,
        'date_max': o[6].isoformat() if o[6] else None,
        'label': o[7], 'area_km2': o[8], 'props': o[9], 'glacier_id': o[10], 'glacier_name': o[11]}), 300)
