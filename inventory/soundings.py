"""Bathymetric soundings: sparse depths and digitised depth contours (read API).

Schema and the reasoning behind it: management/commands/migrate_soundings.py.
Loading a survey: management/commands/load_soundings.py (a package written by
a per-source ingest script under tools/soundings/). Composite input:
management/commands/export_soundings.py.

READS: public surveys for everyone; a survey with public = false only for
accounts that may see restricted data (auth.can_view_restricted), the same
rule as the gated lidar surveys -- applied to the rows, not just a listing.
A delivered survey arrives whole through load_soundings, so its provenance (the
ingest record, the untouched originals) is never bypassed. The one way to write
from the browser is HAND-PLACED POINTS (2026-11-17, Hig: "a simple interface to
add new estimated sounding points ... a type that is 'Estimated' by default, and
new types can be created with a '+' button"): editors click /lidar/ to place a
point in the survey HAND_SLUG, which is never a delivery. Each point hangs from
a level of its own (the water surface read where it was placed), so its bed
elevation is derived like every other sounding's (z = level - depth) and the
export carries it to the composites with its type. Only hand-placed points can
be edited or deleted here; a delivered sounding is flagged, never deleted.

One response carries everything a client needs to draw and explain the
points: features (points and contour lines) with depth, elevation where the
level is known, sigma and flag, plus the surveys and levels they refer to by
id, so a popup can cite its source without a second request. There are few
enough points that the bbox is optional (as api_scarps).
"""
import json, uuid

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .auth import can_view_restricted, inventory_editor_required
from .views import _get_conn, _put_conn


def _iso(v):
    return v.isoformat() if v is not None else None


def _visible_surveys(cur, user):
    restricted = can_view_restricted(user)
    cur.execute("""
        SELECT id, slug, title, collector, collected_from, collected_to, method, instrument,
               depth_unit, sigma_m, horizontal_m, licence, citation, public, notes
          FROM sounding_surveys
         WHERE public OR %s
         ORDER BY collected_from NULLS LAST, slug
    """, [restricted])
    keys = ['id', 'slug', 'title', 'collector', 'collected_from', 'collected_to', 'method', 'instrument',
            'depth_unit', 'sigma_m', 'horizontal_m', 'licence', 'citation', 'public', 'notes']
    out = []
    for r in cur.fetchall():
        d = dict(zip(keys, r))
        d['collected_from'] = _iso(d['collected_from']); d['collected_to'] = _iso(d['collected_to'])
        out.append(d)
    return out


def api_soundings(request):
    """?bbox=minLon,minLat,maxLon,maxLat (optional), ?survey=<slug> (optional),
    ?download=1 for a GeoJSON file. Public surveys for everyone; gated ones
    for data users."""
    bbox = request.GET.get('bbox', '')
    env_sql, env_params = '', []
    if bbox:
        try:
            x0, y0, x1, y1 = [float(v) for v in bbox.split(',')]
        except ValueError:
            return JsonResponse({'error': 'bbox must be minLon,minLat,maxLon,maxLat'}, status=400)
        env_sql, env_params = ' AND geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)', [x0, y0, x1, y1]
    conn = _get_conn()
    try:
        cur = conn.cursor()
        try:
            surveys = _visible_surveys(cur, request.user)
        except Exception as e:              # tables not created in this environment yet
            conn.rollback()
            if 'does not exist' in str(e):
                return JsonResponse({'type': 'FeatureCollection', 'features': [], 'surveys': [], 'levels': [],
                                     'note': 'soundings tables not migrated (manage.py migrate_soundings)'})
            raise
        slug = request.GET.get('survey', '')
        if slug:
            surveys = [s for s in surveys if s['slug'] == slug]
        ids = [s['id'] for s in surveys]
        feats, levels = [], []
        if ids:
            cur.execute("""
                SELECT id, survey_id, key, water_body, level_m, datum, sigma_m, observed_on, source
                  FROM sounding_levels WHERE survey_id = ANY(%s) ORDER BY id
            """, [ids])
            for r in cur.fetchall():
                levels.append(dict(id=r[0], survey_id=r[1], key=r[2], water_body=r[3], level_m=r[4], datum=r[5],
                                   sigma_m=r[6], observed_on=_iso(r[7]), source=r[8]))
            cur.execute("""
                SELECT id, survey_id, level_id, ST_X(geom), ST_Y(geom), observed_at, depth_m, depth_orig,
                       depth_unit, sigma_depth, z, sigma_z, flag, flag_reason, name, notes, depth_bound,
                       point_type, created_by
                  FROM soundings_z
                 WHERE survey_id = ANY(%s)""" + env_sql + """
                 ORDER BY id
            """, [ids] + env_params)
            for (i, sv, lv, x, y, t, dm, do, du, sd, z, sz, fl, fr, nm, no, bd, pt, cb) in cur.fetchall():
                feats.append({'type': 'Feature', 'id': i,
                              'geometry': {'type': 'Point', 'coordinates': [round(x, 7), round(y, 7)]},
                              'properties': {'id': i, 'kind': 'sounding', 'survey_id': sv, 'level_id': lv,
                                             'observed_at': _iso(t), 'depth_m': round(dm, 2),
                                             'depth_orig': do, 'depth_unit': du,
                                             'sigma_m': round(sd, 2) if sd is not None else None,
                                             'z': round(z, 2) if z is not None else None,
                                             'sigma_z': round(sz, 2) if sz is not None else None,
                                             'flag': fl, 'flag_reason': fr, 'name': nm, 'notes': no,
                                             'depth_bound': bd, 'point_type': pt, 'created_by': cb}})
            cur.execute("""
                SELECT c.id, c.survey_id, c.level_id, ST_AsGeoJSON(c.geom, 7), c.depth_m, c.depth_orig,
                       c.depth_unit, COALESCE(c.sigma_m, v.sigma_m), l.level_m - c.depth_m, c.flag,
                       c.flag_reason, c.notes
                  FROM sounding_contours c
                  JOIN sounding_surveys v ON v.id = c.survey_id
                  LEFT JOIN sounding_levels l ON l.id = c.level_id
                 WHERE c.survey_id = ANY(%s)""" + env_sql.replace('geom', 'c.geom') + """
                 ORDER BY c.id
            """, [ids] + env_params)
            for (i, sv, lv, g, dm, do, du, sd, z, fl, fr, no) in cur.fetchall():
                feats.append({'type': 'Feature', 'id': 'c%d' % i, 'geometry': json.loads(g),
                              'properties': {'id': i, 'kind': 'contour', 'survey_id': sv, 'level_id': lv,
                                             'depth_m': round(dm, 2), 'depth_orig': do, 'depth_unit': du,
                                             'sigma_m': round(sd, 2) if sd is not None else None,
                                             'z': round(z, 2) if z is not None else None,
                                             'flag': fl, 'flag_reason': fr, 'notes': no}})
        conn.rollback()
    finally:
        _put_conn(conn)
    fc = {'type': 'FeatureCollection', 'features': feats, 'surveys': surveys, 'levels': levels}
    gated = any(not s['public'] for s in surveys)
    if request.GET.get('download'):
        resp = JsonResponse(fc, json_dumps_params={'indent': 1})
        resp['Content-Type'] = 'application/geo+json'
        resp['Content-Disposition'] = 'attachment; filename="soundings_%s.geojson"' % (slug or 'all')
    else:
        resp = JsonResponse(fc)
    # What a response holds depends on who asked, so never a shared cache; a
    # gated one is not kept at all.
    resp['Cache-Control'] = 'private, no-store' if gated else 'private, max-age=60'
    return resp


# ---- hand-placed points (editors, /lidar/) ------------------------------------------------------------------------------
HAND_SLUG = 'hand_placed'
_MAX_NOTES = 2000


def _body(request):
    try:
        return json.loads(request.body or '{}'), None
    except ValueError:
        return None, JsonResponse({'error': 'body must be JSON'}, status=400)


def _hand_survey(cur, who):
    """The survey hand-placed points belong to, made on first use (gated: data users only, like any new survey)."""
    cur.execute('SELECT id FROM sounding_surveys WHERE slug = %s', [HAND_SLUG])
    r = cur.fetchone()
    if r:
        return r[0]
    cur.execute("""
        INSERT INTO sounding_surveys (slug, title, collector, method, sigma_m, public, notes, created_by)
        VALUES (%s, 'Hand-placed points (estimates)', '', 'estimate', 1.0, false,
                'Points placed by hand on /lidar/ by editors: depths inferred, not measured. Each hangs from the water surface read where it was placed.', %s)
        RETURNING id""", [HAND_SLUG, who])
    return cur.fetchone()[0]


def _types(cur):
    cur.execute('SELECT id, name, notes, created_by FROM sounding_point_types ORDER BY id')
    return [dict(id=r[0], name=r[1], notes=r[2], created_by=r[3]) for r in cur.fetchall()]


def _num(v, name, required=False):
    if v is None or v == '':
        if required:
            raise ValueError('%s is required' % name)
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError('%s must be a number' % name)
    if f != f or abs(f) > 1e5:
        raise ValueError('%s is out of range' % name)
    return f


def _point_values(cur, body):
    """Validated (lon, lat, bed_z, level_m, sigma_m, point_type, name, notes) from a request body."""
    lon = _num(body.get('lon'), 'lon', True); lat = _num(body.get('lat'), 'lat', True)
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError('lon / lat out of range')
    bed = _num(body.get('bed_z'), 'bed elevation', True)
    lev = _num(body.get('level_m'), 'water level')
    if lev is None:
        lev = bed                                   # a dry estimate: depth 0, the bed is the level
    if lev < bed:
        raise ValueError('the bed (%.2f m) stands above the water level (%.2f m)' % (bed, lev))
    sig = _num(body.get('sigma_m'), 'sigma')
    if sig is None:
        sig = 1.0
    if not (0 < sig <= 100):
        raise ValueError('sigma must be between 0 and 100 m')
    pt = (body.get('point_type') or 'Estimated').strip()
    cur.execute('SELECT name FROM sounding_point_types WHERE lower(name) = lower(%s)', [pt])
    r = cur.fetchone()
    if not r:
        raise ValueError('unknown point type %r (add it with +)' % pt)
    return lon, lat, bed, lev, sig, r[0], (body.get('name') or '')[:200], (body.get('notes') or '')[:_MAX_NOTES]


@require_http_methods(['GET'])
def api_point_types(request):
    conn = _get_conn()
    try:
        cur = conn.cursor(); out = _types(cur); conn.rollback()
    finally:
        _put_conn(conn)
    return JsonResponse({'types': out})


@inventory_editor_required
@require_http_methods(['POST'])
def api_point_type_create(request):
    body, err = _body(request)
    if err:
        return err
    name = ' '.join((body.get('name') or '').split())[:60]
    if not name:
        return JsonResponse({'error': 'a type needs a name'}, status=400)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute('SELECT id FROM sounding_point_types WHERE lower(name) = lower(%s)', [name])
        if cur.fetchone():
            conn.rollback()
            return JsonResponse({'error': 'a type named %r exists already' % name}, status=409)
        cur.execute('INSERT INTO sounding_point_types (name, notes, created_by) VALUES (%s, %s, %s) RETURNING id',
                    [name, (body.get('notes') or '')[:_MAX_NOTES], request.user.get_username()])
        new_id = cur.fetchone()[0]; out = _types(cur); conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        _put_conn(conn)
    return JsonResponse({'ok': True, 'id': new_id, 'types': out})


@inventory_editor_required
@require_http_methods(['POST'])
def api_point_create(request):
    """{lon, lat, bed_z, level_m?, sigma_m?, point_type?, name?, notes?, level_source?} -> a hand-placed point."""
    body, err = _body(request)
    if err:
        return err
    who = request.user.get_username()
    conn = _get_conn()
    try:
        cur = conn.cursor()
        try:
            lon, lat, bed, lev, sig, pt, nm, no = _point_values(cur, body)
        except ValueError as e:
            conn.rollback(); return JsonResponse({'error': str(e)}, status=400)
        sv = _hand_survey(cur, who)
        cur.execute("""
            INSERT INTO sounding_levels (survey_id, key, water_body, level_m, sigma_m, source, geom)
            VALUES (%s, %s, '', %s, 0.0, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)) RETURNING id""",
                    [sv, 'pt-' + uuid.uuid4().hex[:12], lev,
                     (body.get('level_source') or 'read where the point was placed')[:200], lon, lat])
        lv = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO soundings (survey_id, level_id, geom, depth_m, depth_unit, sigma_m, name, notes, point_type, created_by)
            VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, 'm', %s, %s, %s, %s, %s) RETURNING id""",
                    [sv, lv, lon, lat, lev - bed, sig, nm, no, pt, who])
        new_id = cur.fetchone()[0]; conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        _put_conn(conn)
    return JsonResponse({'ok': True, 'id': new_id})


@inventory_editor_required
@require_http_methods(['POST'])
def api_point_update(request, point_id):
    """The same fields as create (all sent again), or {"delete": true}.  Hand-placed points only."""
    body, err = _body(request)
    if err:
        return err
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""SELECT s.level_id FROM soundings s JOIN sounding_surveys v ON v.id = s.survey_id
                        WHERE s.id = %s AND v.slug = %s""", [point_id, HAND_SLUG])
        r = cur.fetchone()
        if not r:
            conn.rollback(); return JsonResponse({'error': 'no hand-placed point %d' % point_id}, status=404)
        lv = r[0]
        if body.get('delete'):
            cur.execute('DELETE FROM soundings WHERE id = %s', [point_id])
            if lv:
                cur.execute('DELETE FROM sounding_levels WHERE id = %s', [lv])
            conn.commit(); return JsonResponse({'ok': True, 'deleted': point_id})
        try:
            lon, lat, bed, lev, sig, pt, nm, no = _point_values(cur, body)
        except ValueError as e:
            conn.rollback(); return JsonResponse({'error': str(e)}, status=400)
        if lv:
            cur.execute('UPDATE sounding_levels SET level_m = %s, geom = ST_SetSRID(ST_MakePoint(%s, %s), 4326) WHERE id = %s',
                        [lev, lon, lat, lv])
        cur.execute("""UPDATE soundings SET geom = ST_SetSRID(ST_MakePoint(%s, %s), 4326), depth_m = %s, sigma_m = %s,
                              name = %s, notes = %s, point_type = %s, updated_at = now() WHERE id = %s""",
                    [lon, lat, lev - bed, sig, nm, no, pt, point_id])
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        _put_conn(conn)
    return JsonResponse({'ok': True, 'id': point_id})
