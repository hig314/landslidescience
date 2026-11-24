"""The glacier flow network we edit, and ice-face positions along it.

Design: GLACIERS_PLAN.md section 0 (Hig, 2026-11-16). The short version:

* The organising structure is a network of centrelines like a river network,
  with feeders. Lines run DOWN-FLOW, continue up-glacier (future ice faces
  will land on them) and down-valley past today's ice (where old faces are).
* What matters for slopes is the calving-face position and the ice surface
  elevation, both read against distance along the network.

One table, `glacier_lines`, holds both things an editor draws:

  kind = 'flowline'  a reach of the network. Either ADOPTED from the RGI 7.0
                     centrelines (glacier_centerlines, the reference copy,
                     which stops at each glacier's outline and at its
                     terminus) or drawn by hand, which is how a line gets
                     past the present terminus: ITS_LIVE has velocity only
                     where there is ice now, so the old bed of Icy Bay or
                     Glacier Bay is drawn.
  kind = 'face'      a dated line along an ice face. A line is all that is
                     asked for: no polygon, and it need not span the fjord.

TOPOLOGY IS SHARED COORDINATES (Hig, 2026-11-16). Two flow lines are joined
where they share a point, and only there:

  a BRANCH       a line's last vertex sits on another line. The editor snaps
                 a dragged or drawn end onto the target, and `normalize()`
                 then makes the point a vertex of BOTH lines, so the join is
                 a coordinate the two geometries have in common, not a
                 distance somebody has to re-measure.
  END TO END     a line's last vertex on another line's first. There is no
                 reason to keep two sections of one flow line apart, so
                 `normalize()` FUSES them into one row, permanently.

The first version joined a line to anything within 300 m of its end. That
found the RGI tributaries' junctions and also joined things nobody meant.
RGI's tributaries do stop short of the line they feed (150 to 330 m short in
Icy Bay), but the product says which line that is (`outflow_id`), so on
adoption `join_rgi()` carries each tributary on to that line and the join
becomes a shared point like any other. No distance is guessed anywhere.
`rebuild()` recomputes every line's downstream line, the junction's position
on it, and its chainage (distance of its downstream end from the network's
outlet) after any change. The table is small (the lines someone has adopted
or drawn), so this is a few milliseconds.

WHY THE API LOOKS LIKE inventory/scarps.py
------------------------------------------
The landform-trace editor (scarps.js: select, drag a vertex, insert at a
midpoint, right-click to finish, undo) is the editing tool this needs, and it
talks to `<api>api/scarps/...`. These views answer the same contract under
/glaciers/lines/, so /glaciers/ mounts that editor on this table unchanged.
The path names say "scarps" for that reason only.
"""
import datetime
import json
import math
import re
import statistics

from django.http import Http404, JsonResponse
from django.views.decorators.http import require_http_methods, require_safe

from inventory.auth import inventory_editor_required
from inventory.views import _get_conn, _put_conn

JOIN_TOL_M = 2.0        # coordinates this close are the same point
NEAR_M = 1500           # a face that does not cross the path counts within this
_MIN_LENGTH_M = 5.0
_DRAFT_BIN_M = 1000      # spacing of a drafted line's vertices
_MAX_NOTES = 4000

# Rides the list response as `kinds`; the editor builds its styles from it.
LINE_KINDS = [
    {'id': 'flowline', 'label': 'Flow line', 'color': '#00b0ff', 'dash': None,
     'hint': 'A reach of the flow network, drawn DOWN-FLOW (start up-glacier, end '
             'down-valley). End it on another flow line to make it a feeder.'},
    {'id': 'face', 'label': 'Ice face', 'color': '#ff4081', 'dash': None,
     'hint': 'The ice front at one date. Any length of it; it does not have to '
             'reach both shores.'},
]
_KIND_IDS = {k['id'] for k in LINE_KINDS}
BASES = ['', 'image', 'map', 'chart', 'photograph', 'moraine', 'account', 'survey']

_GEOM = ("ST_Multi(ST_CollectionExtract("
         "ST_MakeValid(ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))), 2))")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS glacier_lines (
    id          serial PRIMARY KEY,
    kind        text NOT NULL,
    geom        geometry(MultiLineString, 4326) NOT NULL,
    name        text NOT NULL DEFAULT '',
    notes       text NOT NULL DEFAULT '',
    traced_by   text NOT NULL DEFAULT '',
    traced_on   jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    -- flow lines
    origin      text NOT NULL DEFAULT 'manual',    -- manual | rgi7 | draft
    glacier_id  text,                              -- glaciers.id it was adopted from / belongs to
    segment_id  integer,
    is_main     boolean,
    -- derived by rebuild(); never set by hand
    down_id     integer,
    join_frac   double precision,
    end_chain_m double precision,
    network_id  integer,
    length_m    double precision,
    -- faces
    date_min    date,
    date_max    date,
    basis       text NOT NULL DEFAULT '',
    source_note text NOT NULL DEFAULT '',
    -- A face too wide or complex for a crossing to mean anything (Bering,
    -- Malaspina) is PINNED: a position on a flow line set by hand.
    pin_line_id integer,
    pin_frac    double precision
);
CREATE INDEX IF NOT EXISTS glacier_lines_geom_idx ON glacier_lines USING GIST (geom);
CREATE INDEX IF NOT EXISTS glacier_lines_kind_idx ON glacier_lines (kind);
-- A DISTRIBUTARY is a flow line that STARTS on another line: ice leaving a
-- trunk for a second front. Derived by rebuild(), like down_id.
ALTER TABLE glacier_lines ADD COLUMN IF NOT EXISTS up_id integer;
ALTER TABLE glacier_lines ADD COLUMN IF NOT EXISTS up_frac double precision;
"""


# ---------------------------------------------------------------------------
# Dates: a face is known to a day, a month, a year, or a span of years. Read
# and written by the site-wide module, so "18 Aug 2003" works here as anywhere.
# ---------------------------------------------------------------------------
from landslidescience.dates import parse_span as parse_date, span_text as _date_text  # noqa: E402


# ---------------------------------------------------------------------------
# Topology and chainage
# ---------------------------------------------------------------------------
# Positions ALONG a line (join_frac, pin_frac, where a face sits) are
# fractions of its length measured in METRIC_SRID, not in degrees: at 60°N a
# degree of longitude is half a degree of latitude, so a fraction taken in
# lon/lat is skewed on any line that changes direction (the Taan junction sat
# 1.4 km out of place until 2026-11-17). Lengths themselves are geodesic
# (::geography); the projection only supplies the fraction, so its small
# scale error cancels. Alaska Albers; good enough for Yukon and BC too.
METRIC_SRID = 3338

_REBUILD_SQL = """
-- Candidates come from the table through its spatial index (a small box
-- round the point), not from every pair of lines.
WITH f AS (SELECT id, geom, ST_GeometryN(geom, 1) AS line,
                  ST_Transform(ST_GeometryN(geom, 1), 3338) AS lm,
                  ST_EndPoint(ST_GeometryN(geom, 1)) AS e,
                  ST_Transform(ST_EndPoint(ST_GeometryN(geom, 1)), 3338) AS em,
                  ST_StartPoint(ST_GeometryN(geom, 1)) AS s,
                  ST_Transform(ST_StartPoint(ST_GeometryN(geom, 1)), 3338) AS sm
           FROM glacier_lines WHERE kind = 'flowline')
SELECT a.id, b.id, b.frac, ST_Length(a.line::geography), u.id, u.frac
FROM f a LEFT JOIN LATERAL (
    SELECT c.id, ST_LineLocatePoint(ST_Transform(ST_GeometryN(c.geom, 1), 3338), a.em) AS frac
    FROM glacier_lines c
    WHERE c.kind = 'flowline' AND c.id <> a.id AND c.geom && ST_Expand(a.e, 0.001)
      AND ST_DWithin(ST_GeometryN(c.geom, 1)::geography, a.e::geography, %s)
    -- Two feeders that end at the same spot on a trunk are each within reach
    -- of the other's END; the trunk is the one they are not at the end of.
    -- And where a distributary leaves the trunk at that same spot, the
    -- feeder joins the trunk (the line it meets mid-course), not the
    -- distributary's first vertex.
    ORDER BY (ST_LineLocatePoint(ST_Transform(ST_GeometryN(c.geom, 1), 3338), a.em) > 0.985),
             (ST_LineLocatePoint(ST_Transform(ST_GeometryN(c.geom, 1), 3338), a.em) < 0.0005
              AND ST_Distance(ST_StartPoint(ST_GeometryN(c.geom, 1))::geography, a.e::geography) <= 2.0),
             ST_Distance(ST_GeometryN(c.geom, 1)::geography, a.e::geography)
    LIMIT 1) b ON true
LEFT JOIN LATERAL (
    -- A distributary: this line STARTS on line c. Anywhere on c but its own
    -- start (two lines sharing a head are not parent and child); at c's very
    -- end it is a trunk that ends by splitting.
    SELECT c.id, ST_LineLocatePoint(ST_Transform(ST_GeometryN(c.geom, 1), 3338), a.sm) AS frac
    FROM glacier_lines c
    WHERE c.kind = 'flowline' AND c.id <> a.id AND c.geom && ST_Expand(a.s, 0.001)
      AND ST_DWithin(ST_GeometryN(c.geom, 1)::geography, a.s::geography, %s)
      AND ST_Distance(ST_StartPoint(ST_GeometryN(c.geom, 1))::geography, a.s::geography) > %s
      -- Starting at c's END is a fork only if a second line starts there
      -- too. One line starting where others end is a confluence carried on
      -- under a new name (Icy Bay below Tsaa and Guyot), not a distributary.
      AND (ST_Distance(ST_EndPoint(ST_GeometryN(c.geom, 1))::geography, a.s::geography) > %s
           OR EXISTS (SELECT 1 FROM glacier_lines d
                      WHERE d.kind = 'flowline' AND d.id NOT IN (a.id, c.id) AND d.geom && ST_Expand(a.s, 0.001)
                        AND ST_Distance(ST_StartPoint(ST_GeometryN(d.geom, 1))::geography, a.s::geography) <= %s))
    ORDER BY ST_Distance(ST_GeometryN(c.geom, 1)::geography, a.s::geography)
    LIMIT 1) u ON true
"""


# ---- joins as shared coordinates ------------------------------------------
def _nearest_on(coords, pt):
    """Nearest point to `pt` on a polyline: (distance m, segment index,
    t along that segment, [lon, lat])."""
    k = math.cos(math.radians(pt[1]))
    best = None
    for i in range(len(coords) - 1):
        ax, ay = coords[i][0] * k, coords[i][1]
        bx, by = coords[i + 1][0] * k, coords[i + 1][1]
        px, py = pt[0] * k, pt[1]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
        qx, qy = ax + t * dx, ay + t * dy
        d = math.hypot(px - qx, py - qy) * 111195.0
        if best is None or d < best[0]:
            q = [coords[i][0] + t * (coords[i + 1][0] - coords[i][0]),
                 coords[i][1] + t * (coords[i + 1][1] - coords[i][1])]
            best = (d, i, t, q)
    return best


def _pick(coords, i, q):
    """The join point on segment i of a line: an existing vertex if one is
    within JOIN_TOL_M of q, else q itself to be inserted. -> (point, insert?)

    An existing vertex has to win. The editor reads and writes coordinates
    at 6 decimals, so a trunk saved back from the browser has its join
    vertices a few centimetres from where its feeders end; treating that as
    "not a vertex" inserted a new one beside the old at every join, on every
    save (five on one line the first time a vertex was deleted)."""
    for v in (coords[i], coords[i + 1]):
        if _dist_m(v, q) <= JOIN_TOL_M:
            return v, False
    return q, True


def _write(cur, lid, coords):
    cur.execute('UPDATE glacier_lines SET geom = ' + _GEOM + ' WHERE id = %s',
                (json.dumps({'type': 'LineString', 'coordinates': coords}), lid))


def normalize(cur, tol_m=JOIN_TOL_M, only=None):
    """Make every join a shared coordinate, and fuse lines that meet end to
    end. `only`: ids whose ENDS may be moved (default: all); with the larger
    adoption tolerance this is how freshly adopted lines are tidied without
    disturbing anyone's hand work. Returns {fused: {gone_id: survivor_id}}.
    """
    cur.execute("""SELECT id, ST_AsGeoJSON(ST_GeometryN(geom, 1), 7), name, notes, origin, glacier_id
                   FROM glacier_lines WHERE kind = 'flowline' ORDER BY id""")
    L = {r[0]: {'c': json.loads(r[1])['coordinates'], 'name': r[2], 'notes': r[3],
                'origin': r[4], 'gid': r[5]} for r in cur.fetchall()}
    fused, dirty = {}, set()

    def movable(i):
        return only is None or i in only

    # 0. no two consecutive vertices closer than a few decimetres (see _pick)
    for i, r in L.items():
        c = [r['c'][0]]
        for pt in r['c'][1:]:
            if _dist_m(c[-1], pt) > 0.3:
                c.append(pt)
        if len(c) >= 2 and len(c) != len(r['c']):
            r['c'] = c; dirty.add(i)

    # Only a line whose bounding box holds the point can be within reach of
    # it. Without this every edit measured every end against every vertex of
    # every line (3 s an edit at 90 lines, minutes at a thousand).
    pad = max(tol_m, 1.0) / 30000.0          # degrees; generous at any latitude here

    def box(i):
        xs = [p[0] for p in L[i]['c']]; ys = [p[1] for p in L[i]['c']]
        return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
    BOX = {i: box(i) for i in L}

    def near(pt, a):
        return [b for b, (x0, y0, x1, y1) in BOX.items() if b != a and x0 <= pt[0] <= x1 and y0 <= pt[1] <= y1]

    # 1. branches: an end that lands on another line becomes a shared vertex
    for a in sorted(L):
        if not movable(a):
            continue
        end, best = L[a]['c'][-1], None
        for b in near(end, a):
            n = _nearest_on(L[b]['c'], end)
            # at b's END is not a join (two feeders arriving at one spot)
            at_end = n[1] == len(L[b]['c']) - 2 and n[2] > 0.999
            if n[0] <= tol_m and not at_end and (best is None or n[0] < best[1][0]):
                best = (b, n)
        if not best:
            continue
        b, (d, i, t, q) = best
        cb = L[b]['c']
        q, ins = _pick(cb, i, q)
        if ins:
            cb.insert(i + 1, q); dirty.add(b)
        if L[a]['c'][-1] != q:
            L[a]['c'][-1] = list(q); dirty.add(a); BOX[a] = box(a)
    # 1b. forks: a START that lands on another line becomes a shared vertex
    # too (a distributary leaving a trunk). Not at that line's own start.
    for a in sorted(L):
        if not movable(a):
            continue
        start, best = L[a]['c'][0], None
        for b in near(start, a):
            n = _nearest_on(L[b]['c'], start)
            at_start = n[1] == 0 and n[2] < 0.001
            if n[0] <= tol_m and not at_start and (best is None or n[0] < best[1][0]):
                best = (b, n)
        if not best:
            continue
        b, (d, i, t, q) = best
        cb = L[b]['c']
        q, ins = _pick(cb, i, q)
        if ins:
            cb.insert(i + 1, q); dirty.add(b)
        if L[a]['c'][0] != q:
            L[a]['c'][0] = list(q); dirty.add(a); BOX[a] = box(a)
    # 2. end to end: a's last vertex is b's first, a is the only line that
    # ends there and b the only one that starts there (two starting there is
    # a trunk splitting into distributaries, which stay separate lines)
    while True:
        pair = None
        starts, ends = {}, {}
        for i in sorted(L):
            starts.setdefault(tuple(L[i]['c'][0]), []).append(i)
            ends.setdefault(tuple(L[i]['c'][-1]), []).append(i)
        for a in sorted(L):
            k = tuple(L[a]['c'][-1])
            if len(ends[k]) != 1 or len(starts.get(k, [])) != 1:
                continue
            b = starts[k][0]
            # not through a third line: a feeder arriving where a
            # distributary leaves the same trunk is a join and a fork
            if any(list(k) in L[x]['c'] for x in near(k, a) if x != b):
                continue
            if b != a and (movable(a) or movable(b)):
                pair = (a, b)
                break
        if not pair:
            break
        a, b = pair
        A, B = L[a], L.pop(b)
        A['c'] = A['c'] + B['c'][1:]
        BOX.pop(b, None); BOX[a] = box(a)
        A['name'] = A['name'] or B['name']
        A['notes'] = '\n'.join(x for x in (A['notes'], B['notes']) if x)
        A['origin'] = A['origin'] if A['origin'] == B['origin'] else 'mixed'
        A['gid'] = A['gid'] or B['gid']
        fused[b] = a
        for k, v in list(fused.items()):
            if v == b:
                fused[k] = a
        dirty.add(a); dirty.discard(b)
        cur.execute('UPDATE glacier_lines SET name=%s, notes=%s, origin=%s, glacier_id=%s, updated_at=now() '
                    'WHERE id=%s', (A['name'], A['notes'][:_MAX_NOTES], A['origin'], A['gid'], a))
        # A pin on the absorbed line would need its fraction re-measured on
        # the fused one; none can exist yet (no control sets a pin), so say so
        # loudly if that ever changes instead of leaving a wrong position.
        cur.execute('SELECT count(*) FROM glacier_lines WHERE pin_line_id = %s', (b,))
        if cur.fetchone()[0]:
            raise RuntimeError('line %d has faces pinned to it; re-measure the pins before fusing' % b)
        cur.execute('DELETE FROM glacier_lines WHERE id = %s', (b,))
    for i in dirty:
        if i in L:
            _write(cur, i, L[i]['c'])
    return {'fused': fused, 'changed': bool(dirty or fused)}


def split_line(cur, lid, at, name, who, notes=''):
    """Cut flow line `lid` in two at its vertex `at` ([lon, lat], exactly as
    stored): `lid` keeps the upper course and its name, and a new line `name`
    carries the lower course from that vertex to the old end. Returns the new
    line's id. Meant for a point where ANOTHER line also ends (a confluence):
    normalize() fuses two lines that meet end to end with nothing else
    arriving, so a split anywhere else is undone at the next edit.
    The caller runs rebuild() and commits."""
    cur.execute("""SELECT ST_AsGeoJSON(ST_GeometryN(geom, 1), 7) FROM glacier_lines
                   WHERE id = %s AND kind = 'flowline'""", (lid,))
    row = cur.fetchone()
    if not row:
        raise ValueError('no flow line %s' % lid)
    c = json.loads(row[0])['coordinates']
    ks = [k for k, p in enumerate(c) if p == list(at)]
    if len(ks) != 1 or ks[0] in (0, len(c) - 1):
        raise ValueError('the split point must be one interior vertex of the line')
    cur.execute('SELECT count(*) FROM glacier_lines WHERE pin_line_id = %s', (lid,))
    if cur.fetchone()[0]:
        raise RuntimeError('line %d has faces pinned to it; re-measure the pins before splitting' % lid)
    k = ks[0]
    new = _insert(cur, {'type': 'LineString', 'coordinates': c[k:]}, 'flowline', who,
                  name=name, origin='manual', notes=notes[:_MAX_NOTES])
    _write(cur, lid, c[:k + 1])
    cur.execute('UPDATE glacier_lines SET updated_at = now() WHERE id = %s', (lid,))
    return new


def extend_to(cur, lid, target_id):
    """Carry flow line `lid` on from its end to flow line `target_id`, as one
    straight link that meets the target a little DOWN-flow of the nearest
    point (by the width of the gap, so it merges at about 45 degrees rather
    than square on). A first guess to be corrected by hand, for a glacier
    whose published centreline stops at its own terminus but which fed a
    trunk when the ice was thicker. Returns the gap in metres. The caller
    runs normalize() and rebuild() and commits."""
    cur.execute("""
        WITH a AS (SELECT ST_Transform(ST_EndPoint(ST_GeometryN(geom, 1)), 3338) e FROM glacier_lines WHERE id = %s),
             b AS (SELECT ST_Transform(ST_GeometryN(geom, 1), 3338) lm FROM glacier_lines WHERE id = %s)
        SELECT ST_Distance(a.e, b.lm), ST_LineLocatePoint(b.lm, a.e), ST_Length(b.lm) FROM a, b""",
                (lid, target_id))
    gap, frac, length = cur.fetchone()
    f = min(frac + gap / length, 0.98)
    cur.execute("""SELECT ST_X(p), ST_Y(p) FROM (SELECT ST_Transform(ST_LineInterpolatePoint(
                       ST_Transform(ST_GeometryN(geom, 1), 3338), %s), 4326) p
                   FROM glacier_lines WHERE id = %s) q""", (f, target_id))
    q = [round(v, 7) for v in cur.fetchone()]
    cur.execute('SELECT ST_AsGeoJSON(ST_GeometryN(geom, 1), 7) FROM glacier_lines WHERE id = %s', (lid,))
    c = json.loads(cur.fetchone()[0])['coordinates']
    _write(cur, lid, c + [q])
    cur.execute("""UPDATE glacier_lines SET origin = CASE WHEN origin = 'rgi7' THEN 'mixed' ELSE origin END,
                          notes = trim(notes || E'\n' || %s), updated_at = now() WHERE id = %s""",
                ('Beyond the RGI terminus: one straight link to line %d, a first guess to correct by hand.' % target_id, lid))
    return gap


def join_rgi(cur, gid):
    """Carry each adopted RGI tributary of one glacier on to the line RGI
    says it flows into (outflow_id), as a shared vertex. Idempotent."""
    cur.execute("""SELECT l.id, l.segment_id, c.outflow_id, ST_AsGeoJSON(ST_GeometryN(l.geom, 1), 7)
                   FROM glacier_lines l
                   LEFT JOIN glacier_centerlines c ON c.glacier_id = l.glacier_id AND c.segment_id = l.segment_id
                   WHERE l.glacier_id = %s AND l.kind = 'flowline' AND l.segment_id IS NOT NULL""", (gid,))
    rows = {r[1]: {'id': r[0], 'out': r[2], 'c': json.loads(r[3])['coordinates']} for r in cur.fetchall()}
    dirty = set()
    for seg, a in rows.items():
        b = rows.get(a['out'])
        if a['out'] is None or b is None:
            continue
        d, i, t, q = _nearest_on(b['c'], a['c'][-1])
        q, ins = _pick(b['c'], i, q)
        if ins:
            b['c'].insert(i + 1, q); dirty.add(a['out'])
        if a['c'][-1] != q:
            # the published line is kept whole; the link to the join is added
            if d > JOIN_TOL_M:
                a['c'].append(list(q))
            else:
                a['c'][-1] = list(q)
            dirty.add(seg)
    for seg in dirty:
        _write(cur, rows[seg]['id'], rows[seg]['c'])
    return len(dirty)


def rebuild(cur):
    """Recompute down_id / join_frac / up_id / up_frac / end_chain_m /
    network_id / length_m for every flow line."""
    cur.execute(_REBUILD_SQL, (JOIN_TOL_M,) * 5)
    rows = {r[0]: {'down': r[1], 'frac': r[2], 'len': r[3] or 0.0, 'up': r[4], 'ufrac': r[5]}
            for r in cur.fetchall()}
    # A line cannot leave the line it flows into at the very point it joins
    # it (a tributary whose two ends both touch one trunk at one spot is a
    # drawing slip, not a loop); and up-links must not form a cycle.
    for i in sorted(rows):
        seen, j = {i}, rows[i]['up']
        while j is not None:
            if j in seen:
                rows[i]['up'], rows[i]['ufrac'] = None, None
                break
            seen.add(j)
            j = rows[j]['up']
    # A loop (two lines each ending on the other) has no outlet; break it at
    # the higher id so the result is at least a tree.
    for i in sorted(rows):
        seen, j = {i}, rows[i]['down']
        while j is not None:
            if j in seen:
                rows[i]['down'], rows[i]['frac'] = None, None
                break
            seen.add(j)
            j = rows[j]['down']
    memo = {}

    def chain(i):
        if i in memo:
            return memo[i]
        r = rows[i]
        if r['down'] is None:
            memo[i] = (0.0, i)
        else:
            c, net = chain(r['down'])
            memo[i] = (c + (1.0 - r['frac']) * rows[r['down']]['len'], net)
        return memo[i]
    # One network = everything connected by a join OR a fork, so a
    # distributary with its own outlet stays in its parent's network. Named
    # for the outlet most lines drain to (the lowest id on a tie).
    root = {i: i for i in rows}

    def find(i):
        while root[i] != i:
            root[i] = root[root[i]]
            i = root[i]
        return i
    for i, r in rows.items():
        for j in (r['down'], r['up']):
            if j is not None:
                root[find(i)] = find(j)
    votes = {}
    for i in rows:
        votes.setdefault(find(i), {}).setdefault(chain(i)[1], 0)
        votes[find(i)][chain(i)[1]] += 1
    name = {k: sorted(v.items(), key=lambda x: (-x[1], x[0]))[0][0] for k, v in votes.items()}
    for i, r in rows.items():
        cur.execute('UPDATE glacier_lines SET down_id=%s, join_frac=%s, up_id=%s, up_frac=%s, end_chain_m=%s, '
                    'network_id=%s, length_m=%s WHERE id=%s',
                    (r['down'], r['frac'], r['up'], r['ufrac'], chain(i)[0], name[find(i)], r['len'], i))


def _bump():
    from . import views
    views.bump_stamp()


# ---------------------------------------------------------------------------
# The editor's contract (see the module docstring)
# ---------------------------------------------------------------------------
_SELECT = """
    SELECT id, ST_AsGeoJSON(geom, 6), kind, name, notes, traced_by, traced_on, created_at,
           updated_at, ST_Length(geom::geography), origin, glacier_id, is_main, down_id,
           end_chain_m, network_id, date_min, date_max, basis, source_note, pin_line_id, pin_frac, up_id
      FROM glacier_lines
"""


def _fc(rows):
    feats = []
    for (i, g, kind, name, notes, by, on, c, u, L, origin, gid, main, down, chain, net,
         d0, d1, basis, src, pin, pinf, up) in rows:
        feats.append({'type': 'Feature', 'geometry': json.loads(g), 'properties': {
            'id': i, 'kind': kind, 'name': name, 'notes': notes or '', 'traced_by': by or '',
            'traced_on': on, 'group_id': None, 'group': None,
            'created_at': c.isoformat() if c else None,
            'updated_at': u.isoformat() if u else None,
            'length_m': round(L, 1) if L is not None else None,
            'origin': origin, 'glacier_id': gid, 'is_main': main, 'down_id': down, 'up_id': up,
            'end_chain_m': chain, 'network_id': net,
            'date_min': d0.isoformat() if d0 else None, 'date_max': d1.isoformat() if d1 else None,
            'date': _date_text(d0, d1), 'y0': d0.year if d0 else None,
            'basis': basis, 'source_note': src, 'pin_line_id': pin, 'pin_frac': pinf}})
    return {'type': 'FeatureCollection', 'kinds': LINE_KINDS, 'groups': [], 'features': feats}


def _body(request):
    try:
        return json.loads(request.body or '{}')
    except ValueError:
        return None


@require_safe
def api_lines(request):
    """Every line of the network, and every face we have drawn. Public."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_SELECT + ' ORDER BY id')
        fc = _fc(cur.fetchall())
        conn.rollback()
    finally:
        _put_conn(conn)
    fc['bases'] = BASES
    if request.GET.get('download'):
        fc.pop('groups', None)
        resp = JsonResponse(fc, json_dumps_params={'indent': 1})
        resp['Content-Type'] = 'application/geo+json'
        resp['Content-Disposition'] = 'attachment; filename="glacier_lines.geojson"'
        return resp
    return JsonResponse(fc)


def _insert(cur, geom, kind, who, **f):
    gj = json.dumps(geom)
    cur.execute('SELECT ST_Length((' + _GEOM + ')::geography)', (gj,))
    length = cur.fetchone()[0]
    if not length or length < _MIN_LENGTH_M:
        raise ValueError('that is %s; a line needs at least %g m'
                         % ('not a line' if not length else '%.1f m' % length, _MIN_LENGTH_M))
    cols = ['kind', 'traced_by'] + list(f)
    cur.execute('INSERT INTO glacier_lines (geom, ' + ', '.join(cols) + ') VALUES (' + _GEOM +
                ', ' + ', '.join(['%s'] * len(cols)) + ') RETURNING id',
                [gj, kind, who] + [f[k] for k in f])
    return cur.fetchone()[0]


def _placement(cur, face_id):
    """Where a face lands on the network, by the rule api_path plots with:
    the flow line it crosses, else the nearest one within NEAR_M. None if no
    flow line is anywhere near. `km_from_head` is along that one line."""
    cur.execute("""
        SELECT f.id, f.name, ST_Intersects(l.geom, fl.line) AS crosses,
               (SELECT avg(ST_LineLocatePoint(fl.lm, d.geom)) FROM ST_DumpPoints(ST_Transform(l.geom, 3338)) d),
               ST_LineLocatePoint(fl.lm, ST_ClosestPoint(ST_Transform(l.geom, 3338), fl.lm)),
               ST_Distance(l.geom::geography, fl.line::geography) AS dist, f.length_m,
               (SELECT g.name FROM glaciers g WHERE g.id = f.glacier_id)
          FROM glacier_lines l
          JOIN glacier_lines f ON f.kind = 'flowline' AND ST_DWithin(l.geom, f.geom, 0.1)
          CROSS JOIN LATERAL (SELECT ST_GeometryN(f.geom, 1) AS line,
                                     ST_Transform(ST_GeometryN(f.geom, 1), 3338) AS lm) fl
         WHERE l.id = %s
         ORDER BY crosses DESC, dist LIMIT 1""", (face_id,))
    r = cur.fetchone()
    if not r:
        return None
    fid, name, crosses, fmean, fnear, dist, length, gname = r
    how = 'crossing' if crosses else ('projected' if dist <= NEAR_M and 0.001 < fnear < 0.999 else 'off')
    return {'line_id': fid, 'name': name or gname or '', 'how': how, 'dist_m': round(dist),
            'near_m': NEAR_M,
            'km_from_head': round((fmean or 0) * (length or 0) / 1000.0, 2)}


@inventory_editor_required
@require_http_methods(['POST'])
def api_line_create(request):
    body = _body(request)
    if body is None or not body.get('geometry'):
        return JsonResponse({'error': 'geometry required'}, status=400)
    kind = body.get('kind') or 'flowline'
    if kind not in _KIND_IDS:
        return JsonResponse({'error': 'unknown kind %r' % kind}, status=400)
    on = body.get('traced_on')
    conn = _get_conn()
    try:
        cur = conn.cursor()
        # A face traced with the tracing panel arrives with its date, what it
        # was read from and its source. A date that cannot be read must not
        # cost the traced line: the face is saved undated and says so.
        extra, warn = {}, ''
        if kind == 'face':
            if body.get('basis') in BASES:
                extra['basis'] = body['basis']
            if body.get('source_note'):
                extra['source_note'] = str(body['source_note'])[:300]
            if body.get('date'):
                try:
                    extra['date_min'], extra['date_max'] = parse_date(body.get('date'))
                except ValueError as e:
                    warn = 'saved without a date (%s)' % e
        try:
            new_id = _insert(cur, body['geometry'], kind, request.user.get_username(),
                             notes=(body.get('notes') or '')[:_MAX_NOTES],
                             traced_on=json.dumps(on) if isinstance(on, dict) else None, **extra)
        except ValueError as e:
            conn.rollback()
            return JsonResponse({'error': str(e)}, status=400)
        keep = new_id
        if kind == 'flowline':
            keep = normalize(cur)['fused'].get(new_id, new_id)
            rebuild(cur)
        cur.execute(_SELECT + ' WHERE id = %s', (keep,))
        fc = _fc(cur.fetchall())
        placement = _placement(cur, keep) if kind == 'face' else None
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _put_conn(conn)
    _bump()
    # `reload`: other rows changed too (a vertex inserted in a trunk, a line
    # fused away, every chainage); `select`: the row this one now is.
    return JsonResponse({'ok': True, 'id': keep, 'select': keep, 'scarps': fc,
                         'reload': kind == 'flowline', 'placement': placement,
                         'warning': warn})


@inventory_editor_required
@require_http_methods(['POST'])
def api_line_update(request, line_id):
    """Change one line, or delete it. Any of: geometry, kind, name, notes,
    date (text, see parse_date), basis, source_note, reverse (true flips a
    flow line's direction), pin ({line_id, frac} or null), delete."""
    body = _body(request)
    if body is None:
        return JsonResponse({'error': 'body must be JSON'}, status=400)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute('SELECT kind FROM glacier_lines WHERE id = %s', (line_id,))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return JsonResponse({'error': 'no such line'}, status=404)
        topo = False
        if body.get('delete'):
            cur.execute('DELETE FROM glacier_lines WHERE id = %s', (line_id,))
            cur.execute('UPDATE glacier_lines SET pin_line_id = NULL, pin_frac = NULL '
                        'WHERE pin_line_id = %s', (line_id,))
            rebuild(cur)
            conn.commit()
            _bump()
            return JsonResponse({'ok': True, 'deleted': int(line_id)})
        sets, params = [], []
        for k in ('name', 'notes', 'basis', 'source_note'):
            if k in body:
                v = (body.get(k) or '')[:_MAX_NOTES]
                if k == 'basis' and v not in BASES:
                    conn.rollback()
                    return JsonResponse({'error': 'unknown basis %r' % v}, status=400)
                sets.append(k + ' = %s'); params.append(v)
        if 'kind' in body:
            if body['kind'] not in _KIND_IDS:
                conn.rollback()
                return JsonResponse({'error': 'unknown kind %r' % body['kind']}, status=400)
            sets.append('kind = %s'); params.append(body['kind']); topo = True
        if 'date' in body:
            try:
                d0, d1 = parse_date(body.get('date'))
            except ValueError as e:
                conn.rollback()
                return JsonResponse({'error': str(e)}, status=400)
            sets.append('date_min = %s'); params.append(d0)
            sets.append('date_max = %s'); params.append(d1)
        if 'pin' in body:
            pin = body.get('pin') or {}
            sets.append('pin_line_id = %s'); params.append(pin.get('line_id'))
            sets.append('pin_frac = %s'); params.append(pin.get('frac'))
        if body.get('geometry'):
            sets.append('geom = ' + _GEOM); params.append(json.dumps(body['geometry'])); topo = True
        if body.get('reverse'):
            sets.append('geom = ST_Multi(ST_Reverse(ST_GeometryN(geom, 1)))'); topo = True
        if not sets:
            conn.rollback()
            return JsonResponse({'error': 'nothing to change'}, status=400)
        sets.append('updated_at = now()')
        cur.execute('UPDATE glacier_lines SET ' + ', '.join(sets) + ' WHERE id = %s',
                    tuple(params) + (line_id,))
        keep = int(line_id)
        if topo:
            keep = normalize(cur)['fused'].get(keep, keep)
            rebuild(cur)
        cur.execute(_SELECT + ' WHERE id = %s', (keep,))
        fc = _fc(cur.fetchall())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _put_conn(conn)
    _bump()
    return JsonResponse({'ok': True, 'select': keep, 'scarps': fc, 'reload': topo})


# ---------------------------------------------------------------------------
# Getting lines in without drawing each one
# ---------------------------------------------------------------------------
def adopt(cur, gid, name, who):
    """Copy one glacier's RGI centrelines into the network and tidy the
    joins. Returns how many lines. The caller commits."""
    cur.execute("""
        INSERT INTO glacier_lines (kind, geom, name, traced_by, origin, glacier_id, segment_id, is_main)
        SELECT 'flowline', ST_Multi(c.geom),
               CASE WHEN c.is_main THEN %s ELSE '' END, %s, 'rgi7', c.glacier_id, c.segment_id, c.is_main
        FROM glacier_centerlines c WHERE c.glacier_id = %s""", (name, who, gid))
    n = cur.rowcount
    join_rgi(cur, gid)
    normalize(cur)
    rebuild(cur)
    return n


@inventory_editor_required
@require_http_methods(['POST'])
def api_adopt(request):
    """Copy one glacier's RGI 7.0 centrelines into the network, where they
    can be edited, joined and extended. {glacier_id}. Idempotent."""
    body = _body(request) or {}
    gid = body.get('glacier_id') or ''
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute('SELECT name FROM glaciers WHERE id = %s', (gid,))
        g = cur.fetchone()
        if not g:
            conn.rollback()
            return JsonResponse({'error': 'no such glacier'}, status=404)
        cur.execute("SELECT count(*) FROM glacier_lines WHERE glacier_id = %s AND origin = 'rgi7'", (gid,))
        if cur.fetchone()[0]:
            conn.rollback()
            return JsonResponse({'ok': True, 'adopted': 0, 'note': 'already in the network'})
        n = adopt(cur, gid, g[0] or gid, request.user.get_username())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _put_conn(conn)
    _bump()
    return JsonResponse({'ok': True, 'adopted': n})


def _dist_m(a, b):
    k = math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot((a[0] - b[0]) * k, a[1] - b[1]) * 111195.0


@inventory_editor_required
@require_http_methods(['POST'])
def api_draft_extension(request):
    """A first guess at the line beyond a glacier's present terminus, from
    the mirrored face positions matched to it: the midpoints of successively
    older faces, each further out than the last. A DRAFT to be corrected by
    hand (origin 'draft'), offered because the faces already trace where the
    ice was. {glacier_id}."""
    body = _body(request) or {}
    gid = body.get('glacier_id') or ''
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""SELECT id, ST_X(ST_EndPoint(ST_GeometryN(geom,1))), ST_Y(ST_EndPoint(ST_GeometryN(geom,1))), origin
                       FROM glacier_lines WHERE glacier_id = %s AND kind='flowline' AND is_main
                       ORDER BY id LIMIT 1""", (gid,))
        m = cur.fetchone()
        if not m:
            conn.rollback()
            return JsonResponse({'error': 'adopt the glacier\'s centrelines first'}, status=400)
        if m[3] != 'rgi7':
            # a draft fuses onto the main line, which then no longer ends at
            # the present terminus: there is nothing to draft from
            conn.rollback()
            return JsonResponse({'error': 'this glacier\'s main line already runs past its terminus; edit it by hand'}, status=400)
        cur.execute("""SELECT ST_X(p), ST_Y(p) FROM (
                         SELECT ST_LineInterpolatePoint(ST_GeometryN(geom,1), 0.5) p, date_min
                         FROM glacier_obs WHERE glacier_id = %s AND kind = 'terminus' AND NOT rejected) q
                       ORDER BY date_min DESC NULLS LAST""", (gid,))
        end = (m[1], m[2])
        # Faces cluster and wander near a terminus that has sat still for
        # years, and a midpoint moves sideways with how much of the front a
        # line covers; chaining midpoints one by one zig-zagged there (the
        # first version did). So: bin the midpoints by distance from the
        # present terminus, take the median position of each bin, and smooth.
        bins = {}
        for x, y in cur.fetchall():
            d = _dist_m(end, (x, y))
            if d >= _DRAFT_BIN_M * 0.6:
                bins.setdefault(int(d // _DRAFT_BIN_M), []).append((x, y))
        mid = [(statistics.median(p[0] for p in v), statistics.median(p[1] for p in v))
               for _k, v in sorted(bins.items())]
        pts = [end] + mid
        for _ in range(2):          # light smoothing; the ends stay put
            pts = [pts[0]] + [((a[0] + 2 * b[0] + c[0]) / 4, (a[1] + 2 * b[1] + c[1]) / 4)
                              for a, b, c in zip(pts, pts[1:], pts[2:])] + [pts[-1]]
        if len(pts) < 2:
            conn.rollback()
            return JsonResponse({'error': 'no mirrored face lies beyond the present terminus'}, status=400)
        # A draft nobody has touched is replaced; one that has been corrected
        # is somebody's work and is left alone.
        cur.execute("""SELECT count(*) FROM glacier_lines
                       WHERE glacier_id = %s AND origin = 'draft' AND updated_at <> created_at""", (gid,))
        if cur.fetchone()[0]:
            conn.rollback()
            return JsonResponse({'error': 'this glacier\'s drafted line has been edited; delete it first to draft again'}, status=400)
        cur.execute("DELETE FROM glacier_lines WHERE glacier_id = %s AND origin = 'draft'", (gid,))
        cur.execute('SELECT name FROM glaciers WHERE id = %s', (gid,))
        nm = (cur.fetchone() or [''])[0] or gid
        new_id = _insert(cur, {'type': 'LineString', 'coordinates': [list(p) for p in pts]},
                         'flowline', request.user.get_username(),
                         name=nm + ', former bed (draft)', origin='draft', glacier_id=gid,
                         notes='Beyond the RGI terminus: drafted from mirrored face positions '
                               '(median midpoint per km, smoothed); correct by hand.')
        # the draft starts on the main line's last vertex, so this fuses them
        new_id = normalize(cur)['fused'].get(new_id, new_id)
        rebuild(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _put_conn(conn)
    _bump()
    return JsonResponse({'ok': True, 'id': new_id, 'vertices': len(pts)})


@inventory_editor_required
@require_http_methods(['POST'])
def api_import_faces(request):
    """Face lines from a file: a GeoJSON FeatureCollection (or one Feature)
    of LineString / MultiLineString. Per feature, any of these properties:
    `date` (1904 | 1904-08 | 1904-08-15 | "1894 to 1904"), or `year`, or
    `date_min` + `date_max`; `basis`; `source`; `notes` or `name`.
    Nothing else is required: no polygon, no glacier id."""
    body = _body(request)
    if body is None:
        return JsonResponse({'error': 'the file is not JSON'}, status=400)
    feats = body.get('features') if body.get('type') == 'FeatureCollection' else [body]
    who = request.user.get_username()
    made, skipped = 0, []
    conn = _get_conn()
    try:
        cur = conn.cursor()
        for n, f in enumerate(feats or []):
            g, p = (f or {}).get('geometry'), (f or {}).get('properties') or {}
            if not g or g.get('type') not in ('LineString', 'MultiLineString'):
                skipped.append('feature %d: not a line' % (n + 1)); continue
            try:
                if p.get('date_min') or p.get('date_max'):
                    d0 = parse_date(str(p.get('date_min') or p.get('date_max')))[0]
                    d1 = parse_date(str(p.get('date_max') or p.get('date_min')))[1]
                else:
                    d0, d1 = parse_date(str(p.get('date') or p.get('year') or p.get('Year') or ''))
                basis = str(p.get('basis') or '')
                _insert(cur, g, 'face', who, date_min=d0, date_max=d1,
                        basis=basis if basis in BASES else '',
                        source_note=str(p.get('source') or '')[:_MAX_NOTES],
                        notes=str(p.get('notes') or p.get('name') or '')[:_MAX_NOTES])
                made += 1
            except ValueError as e:
                skipped.append('feature %d: %s' % (n + 1, e))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _put_conn(conn)
    _bump()
    return JsonResponse({'ok': True, 'created': made, 'skipped': skipped})


# ---------------------------------------------------------------------------
# Reading the network
# ---------------------------------------------------------------------------
@require_safe
def api_nodes(request):
    """The downstream end of every flow line: an outlet (nothing below it) or
    a junction. Drawn as dots so direction and joins can be seen."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""SELECT id, down_id, ST_X(ST_EndPoint(ST_GeometryN(geom,1))),
                              ST_Y(ST_EndPoint(ST_GeometryN(geom,1)))
                       FROM glacier_lines WHERE kind = 'flowline'""")
        rows = cur.fetchall()
        cur.execute("""SELECT id, ST_X(ST_StartPoint(ST_GeometryN(geom,1))), ST_Y(ST_StartPoint(ST_GeometryN(geom,1)))
                       FROM glacier_lines WHERE kind = 'flowline' AND up_id IS NOT NULL""")
        forks = cur.fetchall()
        conn.rollback()
    finally:
        _put_conn(conn)
    return JsonResponse({'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [x, y]},
         'properties': {'id': i, 'outlet': d is None, 'fork': False}} for i, d, x, y in rows] + [
        # where a distributary leaves its parent
        {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [x, y]},
         'properties': {'id': i, 'outlet': False, 'fork': True}} for i, x, y in forks]})


_FACES_SQL = """
WITH r AS (SELECT ST_GeometryN(geom, 1) AS line, ST_Transform(ST_GeometryN(geom, 1), 3338) AS lm
             FROM glacier_lines WHERE id = %(rid)s),
c AS (
  SELECT 'o' || o.id AS fid, o.source, o.date_min, o.date_max, o.label, o.geom, o.glacier_id AS gid
    FROM glacier_obs o, r
   WHERE o.kind = 'terminus' AND NOT o.rejected AND ST_DWithin(o.geom, r.line, 0.03)
  UNION ALL
  SELECT 'l' || l.id, 'ours', l.date_min, l.date_max,
         CASE WHEN l.source_note <> '' THEN l.source_note ELSE l.notes END, l.geom, NULL::text
    FROM glacier_lines l, r
   WHERE l.kind = 'face' AND l.pin_line_id IS NULL AND ST_DWithin(l.geom, r.line, 0.03))
SELECT c.fid, c.source, c.date_min, c.date_max, c.label,
       ST_Intersects(c.geom, r.line),
       (SELECT avg(ST_LineLocatePoint(r.lm, d.geom)) FROM ST_DumpPoints(ST_Transform(c.geom, 3338)) d),
       ST_LineLocatePoint(r.lm, ST_ClosestPoint(ST_Transform(c.geom, 3338), r.lm)),
       ST_Distance(c.geom::geography, r.line::geography),
       -- Which ONE line a face that crosses nothing is projected onto: a
       -- line of the glacier the face was published for if one is in reach,
       -- else the nearest. NULL when the face crosses some flow line: it
       -- belongs where it crosses and is projected nowhere. Without this,
       -- every face within NEAR_M of a line landed on it, so two glaciers
       -- side by side (Grotto and Moraine/Apron) each plotted the other's.
       CASE WHEN EXISTS (SELECT 1 FROM glacier_lines f WHERE f.kind = 'flowline' AND ST_Intersects(c.geom, f.geom))
            THEN NULL
            ELSE (SELECT f.id FROM glacier_lines f
                   WHERE f.kind = 'flowline' AND ST_DWithin(c.geom, f.geom, 0.03)
                     AND ST_Distance(c.geom::geography, f.geom::geography) <= %(near)s
                   ORDER BY (c.gid IS NOT NULL AND f.glacier_id = c.gid) DESC,
                            ST_Distance(c.geom::geography, f.geom::geography) LIMIT 1) END
FROM c, r
"""


# Outlines on the plot (GLACIERS_PLAN.md §3.1, 2026-11-20). An outline has no
# face line, so it gives one number: where the path LEAVES it, furthest
# down-flow. Only outlines of glaciers whose lines are on the path (never a
# neighbour's: the Tsaa/Grotto rule). For each outline and reach, `f` is the
# fraction along the reach of the lowest point of the reach inside the
# outline; `divide` says that point lies on another glacier's RGI outline,
# i.e. the ice carries on into another glacier there and it is no front.
OUTLINE_DIVIDE_M = 150
_OUTLINES_SQL = """
WITH p AS (
  SELECT u.id, ST_GeometryN(l.geom, 1) AS line, ST_Transform(ST_GeometryN(l.geom, 1), 3338) AS lm
    FROM unnest(%(ids)s::int[]) AS u(id) JOIN glacier_lines l ON l.id = u.id),
o AS (
  SELECT o.id, o.source, o.date_min, o.date_max, o.label, o.glacier_id, o.geom
    FROM glacier_obs o
   WHERE o.kind = 'outline' AND NOT o.rejected AND o.glacier_id = ANY(%(gids)s)),
x AS (
  -- the outline is cut to the line's box before it is reprojected: whole
  -- icefield outlines made this most of the path's time
  SELECT o.id, o.source, o.date_min, o.date_max, o.label, o.glacier_id, p.id AS rid, p.lm,
         (SELECT max(ST_LineLocatePoint(p.lm, d.geom))
            FROM ST_DumpPoints(ST_Intersection(p.lm, ST_Transform(
                 ST_ClipByBox2D(o.geom, ST_Expand(ST_Envelope(p.line), 0.01)), 3338))) d) AS f
    FROM o JOIN p ON ST_Intersects(o.geom, p.line))
SELECT x.id, x.source, x.date_min, x.date_max, x.label, x.rid, x.f,
       EXISTS (SELECT 1 FROM glaciers g, LATERAL (SELECT ST_Transform(ST_LineInterpolatePoint(x.lm, x.f), 4326) AS pt) q
                WHERE g.id <> x.glacier_id AND ST_DWithin(g.geom, q.pt, 0.004)
                  AND ST_DWithin(g.geom::geography, q.pt::geography, %(div)s)) AS divide
  FROM x WHERE x.f IS NOT NULL
"""


def _outline_points(cur, path):
    """The plot's outline points: one per outline that the path leaves at a
    front. Dropped: an exit on another glacier's outline (a divide), and an
    exit at the end of the part of a reach on the path, where the ice goes on
    into the next reach (that reach gives the exit) or the path stops inside
    the ice (no front measured)."""
    gids = sorted({p['glacier_id'] for p in path if p.get('glacier_id')})
    if not gids:
        return {}
    R = {p['id']: p for p in path}
    cur.execute(_OUTLINES_SQL, {'ids': list(R), 'gids': gids, 'div': OUTLINE_DIVIDE_M})
    out = {}
    for oid, src, d0, d1, label, rid, f, divide in cur.fetchall():
        reach = R[rid]
        if f < reach['from_frac'] - 1e-6 or f > reach['to_frac'] - 1e-4 or divide:
            continue
        x = reach['end_m'] + (reach['to_frac'] - f) * reach['full_m']
        fid = 'o%d' % oid
        if fid in out and out[fid]['x_m'] <= x:      # keep the lowest exit on the path
            continue
        out[fid] = {'id': fid, 'source': src, 'how': 'outline', 'reach': rid,
                    'date_min': d0.isoformat() if d0 else None,
                    'date_max': d1.isoformat() if d1 else None,
                    't0': _dec(d0), 't1': _dec(d1, True),
                    'x_m': round(x, 1), 'cross_m': round(x, 1), 'label': label or ''}
    return out


@require_safe
def api_path(request, line_id):
    """The path from one flow line down to its network's outlet, and every
    ice face on it as a distance from the outlet.

    A face that crosses a reach of the path is placed at the MEAN of its
    vertices' positions along that reach (`x_m`): a width-averaged position,
    which Lea and others (2014) found far steadier than the bare centreline
    crossing on an irregular front. The crossing itself is returned too
    (`cross_m`). A face that crosses NO flow line is placed the same way on
    one line only and marked `projected` (a line of the glacier it was
    published for if one is within NEAR_M, else the nearest); a pinned face sits
    where it was pinned. An outline of a glacier on the path gives one point,
    `how = outline`, where the path leaves it (`_outline_points`).
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""SELECT id, name, origin, glacier_id, down_id, join_frac, end_chain_m, length_m,
                              up_id, up_frac
                       FROM glacier_lines WHERE kind = 'flowline'""")
        L = {r[0]: r for r in cur.fetchall()}
        lid = int(line_id)
        if lid not in L:
            raise Http404
        path, lo = [], 0.0          # lo: fraction of this reach above which it is on the path
        i, seen = lid, set()
        while i is not None and i not in seen:
            seen.add(i)
            r = L[i]
            path.append({'id': r[0], 'name': r[1], 'origin': r[2], 'glacier_id': r[3],
                         # length_m is the part ON the path: a line the path
                         # joins part-way down counts only from the join to
                         # its end (full_m is the whole line)
                         'length_m': (1.0 - lo) * (r[7] or 0.0), 'full_m': r[7] or 0.0,
                         'end_m': r[6] or 0.0, 'from_frac': lo, 'to_frac': 1.0})
            lo = r[5] or 0.0
            i = r[4]
        # A distributary's history runs back up its parent: above the fork the
        # two fronts were one. So the path starts at the parent's head, takes
        # the parent as far as the fork (to_frac), then the clicked line.
        # end_m of such a reach is the distance from the outlet AT the fork.
        r, top, seen_up = L[lid], (L[lid][6] or 0.0) + (L[lid][7] or 0.0), {lid}
        while r[8] is not None and r[8] not in seen_up and r[9] is not None:
            par, f = L[r[8]], r[9]
            seen_up.add(par[0])
            if par[0] not in seen:      # a braid that rejoins its parent: the parent is already below
                path.insert(0, {'id': par[0], 'name': par[1], 'origin': par[2], 'glacier_id': par[3],
                                'length_m': f * (par[7] or 0.0), 'full_m': par[7] or 0.0,
                                'end_m': top, 'from_frac': 0.0, 'to_frac': f})
                top += f * (par[7] or 0.0)
            else:
                break
            r = par
        best = {}
        for reach in path:
            cur.execute(_FACES_SQL, {'rid': reach['id'], 'near': NEAR_M})
            for fid, src, d0, d1, label, crosses, fmean, fnear, dist, proj_id in cur.fetchall():
                if fmean is None:
                    continue
                # only the part of a reach at or below where the path joins it
                if max(fmean, fnear) < reach['from_frac'] - 1e-6:
                    continue
                # nor the part of a parent below where the distributary left it
                if min(fmean, fnear) > reach['to_frac'] + 1e-6:
                    continue
                if crosses:
                    how = 'crossing'
                elif proj_id == reach['id'] and dist <= NEAR_M and 0.001 < fnear < 0.999:
                    how = 'projected'
                else:
                    continue
                rank = (0 if crosses else 1, dist)
                if fid in best and best[fid][0] <= rank:
                    continue
                x = reach['end_m'] + (reach['to_frac'] - fmean) * reach['full_m']
                xc = reach['end_m'] + (reach['to_frac'] - fnear) * reach['full_m']
                best[fid] = (rank, {
                    'id': fid, 'source': src, 'how': how, 'reach': reach['id'],
                    'date_min': d0.isoformat() if d0 else None,
                    'date_max': d1.isoformat() if d1 else None,
                    't0': _dec(d0), 't1': _dec(d1, True),
                    'x_m': round(x, 1), 'cross_m': round(xc, 1), 'label': label or ''})
        for fid, v in _outline_points(cur, path).items():
            best[fid] = ((2, 0), v)
        ids = [p['id'] for p in path]
        cur.execute("""SELECT id, date_min, date_max, source_note, notes, pin_line_id, pin_frac
                       FROM glacier_lines WHERE kind = 'face' AND pin_line_id = ANY(%s)""", (ids,))
        for fid, d0, d1, src, notes, pl, pf in cur.fetchall():
            reach = [p for p in path if p['id'] == pl][0]
            if pf is None or pf < reach['from_frac'] - 1e-6 or pf > reach['to_frac'] + 1e-6:
                continue
            x = reach['end_m'] + (reach['to_frac'] - pf) * reach['full_m']
            best['l%d' % fid] = ((0, 0), {
                'id': 'l%d' % fid, 'source': 'ours', 'how': 'pinned', 'reach': pl,
                'date_min': d0.isoformat() if d0 else None, 'date_max': d1.isoformat() if d1 else None,
                't0': _dec(d0), 't1': _dec(d1, True), 'x_m': round(x, 1), 'cross_m': round(x, 1),
                'label': src or notes or ''})
        # only the part of each line that is on the path: from where the
        # path joins it down to its end, not the line's own upper course
        cur.execute("""SELECT ST_AsGeoJSON(ST_Collect(
                              ST_Transform(ST_LineSubstring(ST_Transform(ST_GeometryN(l.geom, 1), 3338),
                                                            LEAST(p.lo, p.hi - 0.000001), p.hi), 4326)
                              ORDER BY p.i), 6)
                       FROM glacier_lines l
                       JOIN unnest(%s::int[], %s::float8[], %s::float8[]) WITH ORDINALITY AS p(id, lo, hi, i)
                         ON p.id = l.id""",
                    (ids, [p['from_frac'] for p in path], [p['to_frac'] for p in path]))
        geom = cur.fetchone()[0]
        from . import videos            # lazy: videos imports this module's siblings
        vids = videos.on_path(cur, path)
        conn.rollback()
    finally:
        _put_conn(conn)
    top = path[0]
    return JsonResponse({
        'line_id': lid, 'reaches': path,
        'total_m': round(top['end_m'] + top['length_m'], 1),
        'geometry': json.loads(geom) if geom else None,
        'faces': sorted((v[1] for v in best.values()), key=lambda f: (f['t0'] or 0, f['x_m'])),
        'videos': vids,
    })


def _dec(d, end=False):
    """A date as a decimal year (for plotting)."""
    if not d:
        return None
    start = datetime.date(d.year, 1, 1)
    n = (datetime.date(d.year + 1, 1, 1) - start).days
    return round(d.year + ((d - start).days + (1 if end else 0)) / n, 4)
