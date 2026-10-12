"""Landform traces — a thin CRUD layer over the fault_scarps table.

Generalised 2026-10-11 from scarps to every hand-traced line feature (scarps,
moraine crests, trimlines, strandlines, lineaments): column `kind`, vocabulary
in TRACE_KINDS below, and `traced_on` recording which surfaces the line was
read from. Traced from /lidar/ (where the shading and 3D are) and, for scarps,
from the inventory map's pencil. The API paths keep the scarps name.

(The table keeps its original name; the UI says "scarp", not "fault", because
many of these are of ambiguous or multiple origin — Hig, 2026-09-20.)

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
import re

from django.http import JsonResponse
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods

from .auth import inventory_editor_required
from .views import _get_conn, _put_conn

# ST_CollectionExtract type 2 = lines (1 = points, 3 = polygons).
_SCARP_GEOM_EXPR = ("ST_Multi(ST_CollectionExtract("
                    "ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), 2))")
# The trace vocabulary (2026-10-11: the table generalised from scarps to
# landform traces). Order is display order. `dash` picks the line style
# (None solid, 'dash', 'dot'); colours are chosen to read on grey hillshade
# and on imagery, over a white casing the client draws under every trace.
# Adding a kind is an entry here -- the client builds its styles, pickers and
# labels from this list (it rides the list response as `kinds`). Never
# rename or remove an id that rows use: it is stored data.
TRACE_KINDS = [
    {'id': 'scarp', 'label': 'Scarp', 'color': '#7b3fa0', 'dash': 'dash',
     'hint': 'A break in slope of any origin (tectonic, landslide, other) '
             '-- not a fault map.'},
    {'id': 'moraine', 'label': 'Moraine crest', 'color': '#0277bd', 'dash': None,
     'hint': 'The crest line of a moraine ridge. Say lateral / terminal / '
             'recessional and the age argument in the note.'},
    {'id': 'trimline', 'label': 'Trimline', 'color': '#00897b', 'dash': 'dash',
     'hint': 'A former ice-surface limit: vegetation, weathering or erosion '
             'contrast on a valley wall.'},
    {'id': 'strandline', 'label': 'Strandline', 'color': '#283593', 'dash': 'dot',
     'hint': 'A former shoreline of a lake or the sea (beach ridge, wave-cut '
             'bench).'},
    {'id': 'lineament', 'label': 'Lineament', 'color': '#6d4c41', 'dash': 'dot',
     'hint': 'A linear feature of uncertain origin.'},
]
_KIND_IDS = {k['id'] for k in TRACE_KINDS}
_ID_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,80}$')


def _clean_traced_on(v):
    """The traced_on context, shape-checked: {page, surveys[], basemap}.

    Free-form JSON from the client would be stored for good, so only the
    three known keys survive, each as short id-like strings."""
    if not isinstance(v, dict):
        return None
    out = {}
    if isinstance(v.get('page'), str) and v['page'] in ('lidar', 'inventory'):
        out['page'] = v['page']
    s = v.get('surveys')
    if isinstance(s, list):
        out['surveys'] = [x for x in s if isinstance(x, str) and _ID_RE.match(x)][:20]
    if isinstance(v.get('basemap'), str) and _ID_RE.match(v['basemap']):
        out['basemap'] = v['basemap']
    return out or None


# A trace shorter than this is a stray click, not an observation.
_MIN_LENGTH_M = 5.0
_MAX_NOTES = 4000


_MAX_GROUP_NAME = 120


def _groups(cur):
    """Every group with its trace count (few enough to always send)."""
    cur.execute("""
        SELECT g.id, g.name, g.notes, g.created_by, g.created_at, g.updated_at,
               (SELECT count(*) FROM fault_scarps s WHERE s.group_id = g.id)
          FROM trace_groups g ORDER BY lower(g.name)
    """)
    return [{'id': i, 'name': n, 'notes': no or '', 'created_by': b or '',
             'created_at': c.isoformat() if c else None,
             'updated_at': u.isoformat() if u else None, 'n': k}
            for i, n, no, b, c, u, k in cur.fetchall()]


def _group_id(cur, v):
    """A body's group_id: None, or an id that exists. Raises ValueError."""
    if v is None or v == '':
        return None
    try:
        gid = int(v)
    except (TypeError, ValueError):
        raise ValueError('group_id must be an integer or null')
    cur.execute('SELECT 1 FROM trace_groups WHERE id = %s', (gid,))
    if not cur.fetchone():
        raise ValueError('no such group %d' % gid)
    return gid


def _rows_to_fc(rows, groups=None):
    # `kinds` and `groups` are GeoJSON foreign members: the vocabulary and the
    # groups travel with the data, so a client never draws a kind it has no
    # style for or a group id it cannot name.
    fc = {
        'type': 'FeatureCollection',
        'kinds': TRACE_KINDS,
        'features': [{
            'type': 'Feature',
            'geometry': json.loads(g),
            'properties': {'id': i, 'notes': n or '', 'traced_by': t or '',
                           'kind': k or 'scarp', 'traced_on': on,
                           'group_id': gid, 'group': gname,
                           'created_at': c.isoformat() if c else None,
                           'updated_at': u.isoformat() if u else None,
                           'length_m': round(L, 1) if L is not None else None},
        } for i, g, n, t, c, u, L, k, on, gid, gname in rows],
    }
    if groups is not None:
        fc['groups'] = groups
    return fc


_SELECT = """
    SELECT id, ST_AsGeoJSON(geom, 6), notes, traced_by, created_at, updated_at,
           ST_Length(geom::geography), kind, traced_on, group_id,
           (SELECT name FROM trace_groups g WHERE g.id = fault_scarps.group_id)
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
    kinds = [k for k in request.GET.get('kind', '').split(',') if k]
    if any(k not in _KIND_IDS for k in kinds):
        return JsonResponse({'error': 'unknown kind; known: ' + ', '.join(sorted(_KIND_IDS))},
                            status=400)
    kind_sql, kind_params = ('AND kind = ANY(%s)', [kinds]) if kinds else ('', [])
    group = request.GET.get('group', '')
    if group:
        if not group.isdigit():
            return JsonResponse({'error': 'group must be a group id'}, status=400)
        kind_sql += ' AND group_id = %s'
        kind_params = kind_params + [int(group)]
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
                """ + kind_sql + """
                ORDER BY id
            """, [x0, y0, x1, y1] + kind_params)
        else:
            cur.execute(_SELECT + ' WHERE TRUE ' + kind_sql + ' ORDER BY id', kind_params)
        rows = cur.fetchall()
        groups = _groups(cur)
        fc = _rows_to_fc(rows, groups)
        conn.rollback()
    finally:
        _put_conn(conn)
    if group:
        g = [x for x in groups if x['id'] == int(group)]
        if not g:
            return JsonResponse({'error': 'no such group'}, status=404)
        # One group's output carries the group's own record, so a file on
        # its own still says what the lines are (age, citation, notes).
        fc['group'] = g[0]
    if request.GET.get('download'):
        # A file for GIS: drop the client-only members, name it after the group.
        fc.pop('groups', None)
        name = 'traces_' + (slugify(fc['group']['name']) if group else 'all') + '.geojson'
        resp = JsonResponse(fc, json_dumps_params={'indent': 1})
        resp['Content-Type'] = 'application/geo+json'
        resp['Content-Disposition'] = 'attachment; filename="%s"' % name
        return resp
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
    kind = body.get('kind') or 'scarp'
    if kind not in _KIND_IDS:
        return JsonResponse({'error': 'unknown kind %r' % kind}, status=400)
    traced_on = _clean_traced_on(body.get('traced_on'))
    who = request.user.get_username()

    conn = _get_conn()
    try:
        cur = conn.cursor()
        try:
            gid = _group_id(cur, body.get('group_id'))
        except ValueError as e:
            conn.rollback()
            return JsonResponse({'error': str(e)}, status=400)
        gj = json.dumps(geom)
        # Length is checked on the PROMOTED geometry, so a client that sent
        # something non-linear fails here rather than storing an empty row.
        cur.execute('SELECT ST_Length((' + _SCARP_GEOM_EXPR + ')::geography)', (gj,))
        length = cur.fetchone()[0]
        if not length or length < _MIN_LENGTH_M:
            conn.rollback()
            return JsonResponse(
                {'error': 'that is %s — a trace needs at least %g m of line'
                          % ('not a line' if not length else '%.1f m' % length,
                             _MIN_LENGTH_M)}, status=400)
        cur.execute("""
            INSERT INTO fault_scarps (geom, notes, traced_by, kind, traced_on, group_id)
            VALUES (""" + _SCARP_GEOM_EXPR + """, %s, %s, %s, %s, %s)
            RETURNING id
        """, (gj, notes, who, kind, json.dumps(traced_on) if traced_on else None, gid))
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
    """Edit one trace's notes, kind and/or geometry, or delete it.

    `{"delete": true}` removes it. Otherwise any of `notes` / `kind` /
    `group_id` (an id or null) / `geometry` may be
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
        if 'kind' in body:
            if body['kind'] not in _KIND_IDS:
                conn.rollback()
                return JsonResponse({'error': 'unknown kind %r' % body['kind']}, status=400)
            sets.append('kind = %s')
            params.append(body['kind'])
        if 'group_id' in body:
            try:
                gid = _group_id(cur, body['group_id'])
            except ValueError as e:
                conn.rollback()
                return JsonResponse({'error': str(e)}, status=400)
            sets.append('group_id = %s')
            params.append(gid)
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


# ---- groups ---------------------------------------------------------------
# A group is a named set of traces belonging to one thing (a glaciation, a
# fault, a lake stand): one group per trace, its own notes, outputs per group
# via api/scarps/?group=<id>&download=1. Listed with the traces (`groups`).

def _group_body(request):
    try:
        body = json.loads(request.body or '{}')
    except ValueError:
        return None, JsonResponse({'error': 'body must be JSON'}, status=400)
    return body, None


def _name_taken(cur, name, except_id=None):
    cur.execute('SELECT id FROM trace_groups WHERE lower(name) = lower(%s) AND id IS DISTINCT FROM %s',
                (name, except_id))
    return cur.fetchone() is not None


@inventory_editor_required
@require_http_methods(['POST'])
def api_group_create(request):
    """Create a group: {name, notes?}. Names are unique, ignoring case."""
    body, err = _group_body(request)
    if err:
        return err
    name = ' '.join((body.get('name') or '').split())[:_MAX_GROUP_NAME]
    if not name:
        return JsonResponse({'error': 'a group needs a name'}, status=400)
    notes = (body.get('notes') or '')[:_MAX_NOTES]
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if _name_taken(cur, name):
            conn.rollback()
            return JsonResponse({'error': 'there is already a group called %r' % name}, status=409)
        cur.execute("""INSERT INTO trace_groups (name, notes, created_by)
                       VALUES (%s, %s, %s) RETURNING id""",
                    (name, notes, request.user.get_username()))
        gid = cur.fetchone()[0]
        groups = _groups(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _put_conn(conn)
    return JsonResponse({'ok': True, 'id': gid, 'groups': groups})


@inventory_editor_required
@require_http_methods(['POST'])
def api_group_update(request, group_id):
    """Rename / re-note a group, or {"delete": true} it (its traces are
    ungrouped, never deleted)."""
    body, err = _group_body(request)
    if err:
        return err
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute('SELECT 1 FROM trace_groups WHERE id = %s', (group_id,))
        if not cur.fetchone():
            conn.rollback()
            return JsonResponse({'error': 'no such group'}, status=404)
        if body.get('delete'):
            cur.execute('DELETE FROM trace_groups WHERE id = %s', (group_id,))
        else:
            sets, params = [], []
            if 'name' in body:
                name = ' '.join((body.get('name') or '').split())[:_MAX_GROUP_NAME]
                if not name:
                    conn.rollback()
                    return JsonResponse({'error': 'a group needs a name'}, status=400)
                if _name_taken(cur, name, int(group_id)):
                    conn.rollback()
                    return JsonResponse({'error': 'there is already a group called %r' % name},
                                        status=409)
                sets.append('name = %s'); params.append(name)
            if 'notes' in body:
                sets.append('notes = %s'); params.append((body.get('notes') or '')[:_MAX_NOTES])
            if not sets:
                conn.rollback()
                return JsonResponse({'error': 'nothing to change'}, status=400)
            sets.append('updated_at = now()')
            cur.execute('UPDATE trace_groups SET ' + ', '.join(sets) + ' WHERE id = %s',
                        tuple(params) + (group_id,))
        groups = _groups(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _put_conn(conn)
    return JsonResponse({'ok': True, 'groups': groups})
