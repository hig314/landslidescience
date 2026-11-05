"""Bathymetric soundings: sparse depths and digitised depth contours (read API).

Schema and the reasoning behind it: management/commands/migrate_soundings.py.
Loading a survey: management/commands/load_soundings.py (a package written by
a per-source ingest script under tools/soundings/). Composite input:
management/commands/export_soundings.py.

READS: public surveys for everyone; a survey with public = false only for
accounts that may see restricted data (auth.can_view_restricted), the same
rule as the gated lidar surveys -- applied to the rows, not just a listing.
There are no write endpoints yet: a survey arrives whole through
load_soundings, so its provenance (the ingest record, the untouched originals)
is never bypassed. Editing a point in the browser (flag, sigma, a new sounding
by clicking the map) is the obvious next step and would go here.

One response carries everything a client needs to draw and explain the
points: features (points and contour lines) with depth, elevation where the
level is known, sigma and flag, plus the surveys and levels they refer to by
id, so a popup can cite its source without a second request. There are few
enough points in a lake survey that the bbox is optional (as api_scarps).

DENSE surveys (sounding_surveys.dense, 2026-11-01: the NOS hydrographic
surveys' digital soundings and the CRM cell centres the topobathy fill infers
the sea bed from, 350 k points): their points are served only with a bbox
(?dense=1&bbox=...), capped at DENSE_MAX features per response (`truncated`
says so), and the viewer fetches them per view from a zoom where a view holds
a sensible number. Without a bbox the response carries the dense surveys in
`surveys` (flagged) with none of their points, so the lake layer loads as it
always did and the client knows what else exists.
"""
import json

from django.http import JsonResponse

from .auth import can_view_restricted
from .views import _get_conn, _put_conn


DENSE_MAX = 80000     # points of dense surveys in one bbox response


def _iso(v):
    return v.isoformat() if v is not None else None


def _visible_surveys(cur, user):
    restricted = can_view_restricted(user)
    cur.execute("""
        SELECT id, slug, title, collector, collected_from, collected_to, method, instrument,
               depth_unit, sigma_m, horizontal_m, licence, citation, public, notes, dense
          FROM sounding_surveys
         WHERE public OR %s
         ORDER BY collected_from NULLS LAST, slug
    """, [restricted])
    keys = ['id', 'slug', 'title', 'collector', 'collected_from', 'collected_to', 'method', 'instrument',
            'depth_unit', 'sigma_m', 'horizontal_m', 'licence', 'citation', 'public', 'notes', 'dense']
    out = []
    for r in cur.fetchall():
        d = dict(zip(keys, r))
        d['collected_from'] = _iso(d['collected_from']); d['collected_to'] = _iso(d['collected_to'])
        out.append(d)
    return out


def api_soundings(request):
    """?bbox=minLon,minLat,maxLon,maxLat (optional), ?survey=<slug> (optional),
    ?dense=1 (with a bbox: the dense surveys' points in it, nothing else),
    ?download=1 for a GeoJSON file. Public surveys for everyone; gated ones
    for data users. Dense surveys' points need a bbox (see the module note)."""
    bbox = request.GET.get('bbox', '')
    want_dense = request.GET.get('dense') in ('1', 'true')
    env_sql, env_params = '', []
    if want_dense and not bbox:
        return JsonResponse({'error': 'dense=1 needs a bbox'}, status=400)
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
        # whose POINTS this response carries: a dense survey's only inside a bbox (dense=1 or a named survey);
        # the survey records themselves are always listed, so the client knows what else exists
        if want_dense:
            pts_ids = [s['id'] for s in surveys if s['dense']]
        elif bbox or slug:
            pts_ids = [s['id'] for s in surveys]
        else:
            pts_ids = [s['id'] for s in surveys if not s['dense']]
        ids = [s['id'] for s in surveys]
        feats, levels = [], []
        truncated = False
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
                       depth_unit, sigma_depth, z, sigma_z, flag, flag_reason, name, notes, depth_bound
                  FROM soundings_z
                 WHERE survey_id = ANY(%s)""" + env_sql + """
                 ORDER BY id
                 LIMIT %s
            """, [pts_ids] + env_params + [DENSE_MAX + 1])
            rows = cur.fetchall()
            if len(rows) > DENSE_MAX:
                rows = rows[:DENSE_MAX]; truncated = True
            for (i, sv, lv, x, y, t, dm, do, du, sd, z, sz, fl, fr, nm, no, bd) in rows:
                feats.append({'type': 'Feature', 'id': i,
                              'geometry': {'type': 'Point', 'coordinates': [round(x, 7), round(y, 7)]},
                              'properties': {'id': i, 'kind': 'sounding', 'survey_id': sv, 'level_id': lv,
                                             'observed_at': _iso(t), 'depth_m': round(dm, 2),
                                             'depth_orig': do, 'depth_unit': du,
                                             'sigma_m': round(sd, 2) if sd is not None else None,
                                             'z': round(z, 2) if z is not None else None,
                                             'sigma_z': round(sz, 2) if sz is not None else None,
                                             'flag': fl, 'flag_reason': fr, 'name': nm, 'notes': no,
                                             'depth_bound': bd}})
            cur.execute("""
                SELECT c.id, c.survey_id, c.level_id, ST_AsGeoJSON(c.geom, 7), c.depth_m, c.depth_orig,
                       c.depth_unit, COALESCE(c.sigma_m, v.sigma_m), l.level_m - c.depth_m, c.flag,
                       c.flag_reason, c.notes
                  FROM sounding_contours c
                  JOIN sounding_surveys v ON v.id = c.survey_id
                  LEFT JOIN sounding_levels l ON l.id = c.level_id
                 WHERE c.survey_id = ANY(%s)""" + env_sql.replace('geom', 'c.geom') + """
                 ORDER BY c.id
            """, [pts_ids] + env_params)
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
    if truncated:
        fc['truncated'] = DENSE_MAX
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
