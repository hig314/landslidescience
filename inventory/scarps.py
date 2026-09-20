"""Traced fault scarps — a thin CRUD layer over the fault_scarps table.

Scarps that look tectonic rather than landslide-related turn up constantly
while tracing landslides on the lidar surfaces. This is somewhere to draw the
line and write down what you thought at the moment you thought it. See
inventory/management/commands/migrate_fault_scarps.py for the schema and why
it is deliberately thin.

READS ARE PUBLIC, WRITES ARE EDITOR-ONLY (Hig, 2026-09-20). The first cut
kept reads editor-only on the argument that a half-formed "possible scarp?"
on the public map reads as a claim; Hig decided the traces should be seen by
default, so anyone can list them and read the notes, and the client labels
them as working observations. Tracing, notes and deletion still need an
editor login. A `published` column is still the way to hide one later.

Geometry in, geometry out, is GeoJSON in EPSG:4326. Writes go through
_SCARP_GEOM_EXPR, which mirrors the landslide polygons' _GEOM_WRITE_EXPR: set
the SRID explicitly, make valid, keep only the linear parts, promote to
MultiLineString so the column stays one type whatever the client sent.
"""
import json

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .auth import inventory_editor_required
from .views import _get_conn, _put_conn

# ST_CollectionExtract type 2 = lines (1 = points, 3 = polygons).
_SCARP_GEOM_EXPR = ("ST_Multi(ST_CollectionExtract("
                    "ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), 2))")
# A scarp shorter than this is a stray click, not an observation.
_MIN_LENGTH_M = 5.0
_MAX_NOTES = 4000


def _rows_to_fc(rows):
    return {
        'type': 'FeatureCollection',
        'features': [{
            'type': 'Feature',
            'geometry': json.loads(g),
            'properties': {'id': i, 'notes': n or '', 'traced_by': t or '',
                           'created_at': c.isoformat() if c else None,
                           'updated_at': u.isoformat() if u else None,
                           'length_m': round(L, 1) if L is not None else None},
        } for i, g, n, t, c, u, L in rows],
    }


_SELECT = """
    SELECT id, ST_AsGeoJSON(geom, 6), notes, traced_by, created_at, updated_at,
           ST_Length(geom::geography)
      FROM fault_scarps
"""


@require_http_methods(['GET'])
def api_scarps(request):
    """Scarps in a bbox. ?bbox=minLon,minLat,maxLon,maxLat (optional). Public.

    The bbox is optional here, unlike the landslide polygons endpoint: there
    are few enough of these that "show me everything" is a reasonable ask, and
    a forgotten bbox returning 400 was a real trap in the reviser.
    """
    bbox = request.GET.get('bbox', '')
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if bbox:
            try:
                x0, y0, x1, y1 = [float(v) for v in bbox.split(',')]
            except ValueError:
                return JsonResponse(
                    {'error': 'bbox must be minLon,minLat,maxLon,maxLat'}, status=400)
            cur.execute(_SELECT + """
                WHERE ST_Intersects(geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326))
                ORDER BY id
            """, (x0, y0, x1, y1))
        else:
            cur.execute(_SELECT + ' ORDER BY id')
        fc = _rows_to_fc(cur.fetchall())
        conn.rollback()
    finally:
        _put_conn(conn)
    return JsonResponse(fc)


@inventory_editor_required
@require_http_methods(['POST'])
def api_scarp_create(request):
    """Create one scarp from a drawn LineString."""
    try:
        body = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'error': 'body must be JSON'}, status=400)
    geom = body.get('geometry')
    if not geom:
        return JsonResponse({'error': 'geometry required'}, status=400)
    notes = (body.get('notes') or '')[:_MAX_NOTES]
    who = request.user.get_username()

    conn = _get_conn()
    try:
        cur = conn.cursor()
        gj = json.dumps(geom)
        # Length is checked on the PROMOTED geometry, so a client that sent
        # something non-linear fails here rather than storing an empty row.
        cur.execute('SELECT ST_Length((' + _SCARP_GEOM_EXPR + ')::geography)', (gj,))
        length = cur.fetchone()[0]
        if not length or length < _MIN_LENGTH_M:
            conn.rollback()
            return JsonResponse(
                {'error': 'that is %s — a scarp needs at least %g m of line'
                          % ('not a line' if not length else '%.1f m' % length,
                             _MIN_LENGTH_M)}, status=400)
        cur.execute("""
            INSERT INTO fault_scarps (geom, notes, traced_by)
            VALUES (""" + _SCARP_GEOM_EXPR + """, %s, %s)
            RETURNING id
        """, (gj, notes, who))
        new_id = cur.fetchone()[0]
        cur.execute(_SELECT + ' WHERE id = %s', (new_id,))
        fc = _rows_to_fc(cur.fetchall())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _put_conn(conn)
    return JsonResponse({'ok': True, 'id': new_id, 'scarps': fc})


@inventory_editor_required
@require_http_methods(['POST'])
def api_scarp_update(request, scarp_id):
    """Edit one scarp's notes and/or geometry, or delete it.

    `{"delete": true}` removes it. Otherwise any of `notes` / `geometry` may be
    present; absent fields are left alone, so saving a note cannot silently
    revert a geometry someone else moved.
    """
    try:
        body = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'error': 'body must be JSON'}, status=400)

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute('SELECT 1 FROM fault_scarps WHERE id = %s', (scarp_id,))
        if not cur.fetchone():
            conn.rollback()
            return JsonResponse({'error': 'no such scarp'}, status=404)

        if body.get('delete'):
            cur.execute('DELETE FROM fault_scarps WHERE id = %s', (scarp_id,))
            conn.commit()
            return JsonResponse({'ok': True, 'deleted': scarp_id})

        sets, params = [], []
        if 'notes' in body:
            sets.append('notes = %s')
            params.append((body.get('notes') or '')[:_MAX_NOTES])
        if body.get('geometry'):
            sets.append('geom = ' + _SCARP_GEOM_EXPR)
            params.append(json.dumps(body['geometry']))
        if not sets:
            conn.rollback()
            return JsonResponse({'error': 'nothing to change'}, status=400)
        sets.append('updated_at = now()')
        params.append(scarp_id)
        cur.execute('UPDATE fault_scarps SET ' + ', '.join(sets) + ' WHERE id = %s',
                    tuple(params))
        cur.execute(_SELECT + ' WHERE id = %s', (scarp_id,))
        fc = _rows_to_fc(cur.fetchall())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _put_conn(conn)
    return JsonResponse({'ok': True, 'scarps': fc})
