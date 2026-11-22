"""Timelapse videos linked to segments of the flow network.

A video (a Planet story, or an uploaded Sentinel-1 SAR animation) is attached
to one or more SEGMENTS: the stretch of a flow line between two junctions. One
video may cover several (three small glaciers that feed the same trunk), and a
trunk segment may carry many. A video with no segment is not kept.

WHAT IS STORED
--------------
A segment is not an object in the database: junctions are derived from where
lines meet, and lines get split, fused and redrawn. So a link is stored as an
ANCHOR POINT on the map, and the segment is worked out from it at read time:
the nearest flow line within SNAP_M, then the stretch between the junctions on
either side of the point. An anchor therefore survives the line under it being
split or renamed. If a new junction later lands inside its segment, the anchor
keeps the half it sits on.

WHERE THE BYTES LIVE
--------------------
- kind 'planet': the story's slug. The mp4 is archived and served by the
  inventory's Planet code (`inventory/planet.py`, `/inventory/planet/<slug>.mp4`),
  one copy shared with any landslide record that links the same story. A
  comparison story has no mp4 and is linked out.
- kind 's1' (and any future uploaded kind): the file is saved under
  data/glacier_videos/ and served at /glaciers/video/<name>. It arrives
  either as an upload or as a Google Drive share link (Earth Engine exports
  land in Drive), which is fetched once in a background thread, as Planet
  stories are; the row has no `file` until that finishes, and `fetch_error`
  if it failed. The Drive file must be shared "anyone with the link".

Positions along lines are metric fractions, as everywhere in network.py.
"""
import json
import logging
import re
import secrets
import threading
import urllib.request
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpResponse, HttpResponseNotFound, JsonResponse, StreamingHttpResponse
from django.views.decorators.http import require_http_methods, require_safe

from inventory import planet
from inventory.auth import inventory_editor_required
from inventory.views import _get_conn, _put_conn
from landslidescience.dates import parse_span, span_text

SNAP_M = 400            # an anchor further than this from every flow line is off the network
MAX_UPLOAD = 150 * 1024 * 1024
VIDEO_DIR = Path(settings.BASE_DIR) / 'data' / 'glacier_videos'
_FILE_RE = re.compile(r'^[a-f0-9]{16}\.(mp4|webm|gif)$')
# Static products of a placed video, one image each, the frame's own extent:
# <video stem>_<product>.webp (tools/glaciers/s1_georef/web_products.py).
GEOREF_DIR = VIDEO_DIR / 'georef'
GEOREF_PRODUCTS = [
    {'id': 'median', 'label': 'Median', 'title': 'Median of every month, in the video\'s own colours'},
    {'id': 'range', 'label': 'Brightness range', 'title': 'Brightness over the months as colour: red = the brightest tenth, green = the median, blue = the darkest tenth. Grey is steady; colour is change'},
    {'id': 'variability', 'label': 'Variability', 'title': 'How much brightness changes month to month (temporal standard deviation of log brightness); bright = changeable'},
    {'id': 'sign', 'label': 'Sign persistence', 'title': 'Whether a spot stays brighter (orange) or darker (blue) than its surroundings through the series; white = it flips. Rock and flow bands persist, flowing texture does not'},
    {'id': 'edges', 'label': 'Edge strength', 'title': 'Median edge strength over the months: edges that are there most of the time, whether light-on-dark or dark-on-light'},
]
_GEOREF_RE = re.compile(r'^([a-f0-9]{16})_(%s)\.webp$' % '|'.join(p['id'] for p in GEOREF_PRODUCTS))
_TYPES = {'mp4': 'video/mp4', 'webm': 'video/webm', 'gif': 'image/gif'}

# `upload`: the video is a file we are given, not a link we archive.
KINDS = [
    {'id': 'planet', 'label': 'Planet timelapse', 'color': '#8e24aa', 'upload': False},
    {'id': 's1', 'label': 'Sentinel-1 SAR timelapse', 'color': '#00897b', 'upload': True},
]
_KIND = {k['id']: k for k in KINDS}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS glacier_videos (
    id         serial PRIMARY KEY,
    kind       text NOT NULL,
    slug       text UNIQUE,             -- planet: the story slug
    file       text,                    -- uploaded kinds: name under data/glacier_videos/
    caption    text NOT NULL DEFAULT '',
    date_min   date,
    date_max   date,
    added_by   text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS glacier_video_anchors (
    id       serial PRIMARY KEY,
    video_id integer NOT NULL REFERENCES glacier_videos(id) ON DELETE CASCADE,
    geom     geometry(Point, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS glacier_video_anchors_video_idx ON glacier_video_anchors (video_id);
ALTER TABLE glacier_videos ADD COLUMN IF NOT EXISTS source_url text;
ALTER TABLE glacier_videos ADD COLUMN IF NOT EXISTS fetch_error text;
-- where an s1 video sits on the map, found from its content
-- (tools/glaciers/s1_georef/, docs/GLACIERS.md): {bounds: [w, s, e, n] lon/lat
-- of the frame's edges, grade, products: [...], frames, dropped, ...}.
-- Set by `manage.py s1_videos georef`; the product images are under GEOREF_DIR.
ALTER TABLE glacier_videos ADD COLUMN IF NOT EXISTS georef jsonb;
"""


log = logging.getLogger(__name__)
_DRIVE_ID = re.compile(r'(?:/file/d/|/d/|[?&]id=)([A-Za-z0-9_-]{20,})')
DRIVE_GET = 'https://drive.usercontent.google.com/download?id={id}&export=download&confirm=t'
_UA = {'User-Agent': 'Mozilla/5.0 (video fetch)'}


def drive_id(url):
    """The file id in a Google Drive share link, or None."""
    u = (url or '').strip()
    if 'drive.google.com' not in u and 'drive.usercontent.google.com' not in u:
        return None
    m = _DRIVE_ID.search(u)
    return m.group(1) if m else None


def _sniff(head):
    if head[4:8] == b'ftyp':
        return 'mp4'
    if head[:4] == b'\x1a\x45\xdf\xa3':
        return 'webm'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    return None


def fetch_to_file(url):
    """Download one video to VIDEO_DIR. Returns its stored name. ValueError
    with a message for the editor if it is not a video we can host."""
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    tmp = VIDEO_DIR / (secrets.token_hex(8) + '.part')
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=60) as r, tmp.open('wb') as out:
            head = r.read(16)
            ext = _sniff(head)
            if not ext:
                raise ValueError('the link did not return an .mp4, .webm or .gif file; in Drive, set sharing to '
                                 '"Anyone with the link" and link the file itself, not a folder')
            out.write(head)
            size = len(head)
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise ValueError('the file is over the %d MB limit' % (MAX_UPLOAD // 1048576))
                out.write(chunk)
        name = '%s.%s' % (secrets.token_hex(8), ext)
        tmp.rename(VIDEO_DIR / name)
        return name
    finally:
        tmp.unlink(missing_ok=True)


def _fetch_worker(vid, url):
    from django.db import connections
    name = err = None
    try:
        name = fetch_to_file(url)
    except ValueError as e:
        err = str(e)
    except Exception as e:           # network trouble, a 404, a private file
        log.exception('video fetch failed for %s', vid)
        err = 'the download failed (%s); is the file shared with anyone who has the link?' % type(e).__name__
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute('UPDATE glacier_videos SET file = %s, fetch_error = %s WHERE id = %s RETURNING id', (name, err, vid))
        gone = cur.fetchone() is None
        conn.commit()
        if gone and name:            # deleted while it was downloading
            (VIDEO_DIR / name).unlink(missing_ok=True)
    finally:
        _put_conn(conn)
        connections.close_all()


# ---------------------------------------------------------------------------
# Anchor point -> segment
# ---------------------------------------------------------------------------
def _cuts(cur):
    """{line_id: sorted junction fractions on it}, and each line's name."""
    cur.execute("""SELECT l.id, l.down_id, l.join_frac,
                          COALESCE(NULLIF(l.name, ''), (SELECT g.name FROM glaciers g WHERE g.id = l.glacier_id), ''),
                          l.up_id, l.up_frac
                   FROM glacier_lines l WHERE l.kind = 'flowline'""")
    cuts, names = {}, {}
    for lid, down, frac, name, up, ufrac in cur.fetchall():
        names[lid] = name or ''
        cuts.setdefault(lid, [])
        if down is not None and frac is not None:
            cuts.setdefault(down, []).append(frac)
        if up is not None and ufrac is not None and 0.0 < ufrac < 1.0:
            cuts.setdefault(up, []).append(ufrac)      # where a distributary leaves is a junction too
    for v in cuts.values():
        v.sort()
    return cuts, names


def resolve(cur, points):
    """[[lon, lat], ...] -> one entry per point: None when no flow line is
    within SNAP_M, else {line_id, lo, hi, name, key}. `lo`..`hi` is the
    stretch of the line between the junctions either side of the point."""
    if not points:
        return []
    cuts, names = _cuts(cur)
    cur.execute("""
        SELECT p.i, x.id, x.f
        FROM unnest(%s::float8[], %s::float8[]) WITH ORDINALITY AS p(lon, lat, i)
        CROSS JOIN LATERAL (SELECT ST_Transform(ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326), 3338) AS g,
                                   ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326) AS ll) q
        LEFT JOIN LATERAL (
            SELECT l.id, ST_LineLocatePoint(ST_Transform(ST_GeometryN(l.geom, 1), 3338), q.g) AS f
            FROM glacier_lines l
            WHERE l.kind = 'flowline' AND ST_DWithin(l.geom, q.ll, 0.02)
              AND ST_Distance(ST_Transform(ST_GeometryN(l.geom, 1), 3338), q.g) <= %s
            ORDER BY ST_Distance(ST_Transform(ST_GeometryN(l.geom, 1), 3338), q.g) LIMIT 1) x ON true
        ORDER BY p.i""", ([p[0] for p in points], [p[1] for p in points], SNAP_M))
    out = []
    for _i, lid, f in cur.fetchall():
        if lid is None:
            out.append(None)
            continue
        lo, hi = 0.0, 1.0
        for c in cuts.get(lid, []):
            # a junction within a metre-ish of the point belongs to the
            # stretch below it, so a click on a junction is not ambiguous
            if c <= f + 1e-7:
                lo = max(lo, c)
            else:
                hi = min(hi, c)
        if hi - lo < 1e-6:
            out.append(None)
            continue
        out.append({'line_id': lid, 'lo': lo, 'hi': hi, 'name': names.get(lid, ''),
                    'key': '%d:%.5f' % (lid, lo)})
    return out


def _geoms(cur, segs):
    """Segment geometry and midpoint for a list of resolved segments."""
    if not segs:
        return []
    cur.execute("""
        SELECT ST_AsGeoJSON(ST_Transform(s.g, 4326), 6),
               ST_AsGeoJSON(ST_Transform(ST_LineInterpolatePoint(s.g, 0.5), 4326), 6),
               ST_Length(ST_Transform(s.g, 4326)::geography)
        FROM unnest(%s::int[], %s::float8[], %s::float8[]) WITH ORDINALITY AS p(id, lo, hi, i)
        JOIN glacier_lines l ON l.id = p.id
        CROSS JOIN LATERAL (SELECT ST_LineSubstring(ST_Transform(ST_GeometryN(l.geom, 1), 3338), p.lo, p.hi) AS g) s
        ORDER BY p.i""", ([s['line_id'] for s in segs], [s['lo'] for s in segs], [s['hi'] for s in segs]))
    return [(json.loads(a), json.loads(b), c) for a, b, c in cur.fetchall()]


def _rows(cur, only=None):
    cur.execute("""
        SELECT v.id, v.kind, v.slug, v.file, v.caption, v.date_min, v.date_max, v.added_by,
               ps.story_type, ps.mp4_archived_at IS NOT NULL, v.source_url, v.fetch_error, v.georef
        FROM glacier_videos v LEFT JOIN planet_stories ps ON ps.slug = v.slug
        """ + ('WHERE v.id = %s' if only else '') + ' ORDER BY v.id', ((only,) if only else ()))
    vids = {}
    for vid, kind, slug, fname, cap, d0, d1, who, stype, archived, src_url, ferr, geo in cur.fetchall():
        k = _KIND.get(kind, {'label': kind, 'color': '#555'})
        v = {'id': vid, 'kind': kind, 'kind_label': k['label'], 'color': k['color'],
             'caption': cap, 'date': span_text(d0, d1), 'added_by': who,
             't0': _dec(d0), 't1': _dec(d1, True),
             'src': None, 'media': None, 'link': None, 'state': 'ready', 'segments': []}
        if kind == 'planet':
            v['link'] = planet.STORY_PREFIX + slug
            if archived:
                v['src'], v['media'] = '/inventory/planet/%s.mp4' % slug, 'video'
            elif stype == 'comparison':
                v['state'] = 'comparison'       # a before/after wiper: nothing to host
            else:
                v['state'] = 'archiving'        # probed in the background; a reload shows it
        elif fname:
            v['src'] = '/glaciers/video/' + fname
            v['media'] = 'image' if fname.endswith('.gif') else 'video'
            if geo and geo.get('bounds'):
                stem = fname.rsplit('.', 1)[0]
                v['georef'] = {'bounds': geo['bounds'], 'grade': geo.get('grade'),
                               'products': [dict(p, src='/glaciers/video/georef/%s_%s.webp' % (stem, p['id']))
                                            for p in GEOREF_PRODUCTS if p['id'] in geo.get('products', [])]}
        elif ferr:
            v['state'], v['error'] = 'failed', ferr
        else:
            v['state'] = 'archiving'            # a Drive link still being fetched
        vids[vid] = v
    return vids


def _dec(d, end=False):
    """A date as a decimal year (the plot's time axis)."""
    if not d:
        return None
    return round(d.year + (d.timetuple().tm_yday - (0 if end else 1)) / 365.25, 3)


def listing(cur, only=None):
    """Every video with its resolved segments, plus the map layers."""
    vids = _rows(cur, only)
    cur.execute('SELECT id, video_id, ST_X(geom), ST_Y(geom) FROM glacier_video_anchors ' +
                ('WHERE video_id = %s ' if only else '') + 'ORDER BY id', ((only,) if only else ()))
    anchors = [a for a in cur.fetchall() if a[1] in vids]
    segs = resolve(cur, [[a[2], a[3]] for a in anchors])
    live = [(a, s) for a, s in zip(anchors, segs) if s]
    geoms = _geoms(cur, [s for _a, s in live])
    lines, pts = [], []
    for (a, s), (g, mid, length) in zip(live, geoms):
        v = vids[a[1]]
        if any(x['key'] == s['key'] for x in v['segments']):
            continue                    # two anchors that now fall on one segment
        v['segments'].append({'anchor': [a[2], a[3]], 'line_id': s['line_id'], 'lo': s['lo'], 'hi': s['hi'],
                              'name': s['name'], 'key': s['key'], 'mid': mid['coordinates'],
                              'length_m': round(length)})
        props = {'video_id': v['id'], 'kind': v['kind'], 'color': v['color']}
        lines.append({'type': 'Feature', 'geometry': g, 'properties': props})
        pts.append({'type': 'Feature', 'geometry': mid, 'properties': props})
    for a in anchors:
        vids[a[1]].setdefault('n_anchors', 0)
        vids[a[1]]['n_anchors'] += 1
    return {'videos': list(vids.values()), 'kinds': KINDS,
            'segments': {'type': 'FeatureCollection', 'features': lines},
            'points': {'type': 'FeatureCollection', 'features': pts}}


def on_path(cur, path):
    """Videos whose segments lie on a plotted path (network.api_path's list
    of reaches): each with the stretches of the path it covers, in metres
    from the outlet like the faces."""
    reach = {p['id']: p for p in path}
    out = []
    for v in listing(cur)['videos']:
        spans = []
        for s in v['segments']:
            r = reach.get(s['line_id'])
            if not r:
                continue
            lo, hi = max(s['lo'], r['from_frac']), min(s['hi'], r['to_frac'])
            if hi - lo < 1e-6:
                continue
            spans.append({'a_m': round(r['end_m'] + (r['to_frac'] - hi) * r['full_m'], 1),
                          'b_m': round(r['end_m'] + (r['to_frac'] - lo) * r['full_m'], 1), 'mid': s['mid']})
        if spans:
            out.append({k: v[k] for k in ('id', 'kind', 'kind_label', 'color', 'caption', 'date', 't0', 't1')} | {'spans': spans})
    return out


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
@require_safe
def api_videos(request):
    conn = _get_conn()
    try:
        cur = conn.cursor()
        data = listing(cur)
        conn.rollback()
    finally:
        _put_conn(conn)
    return JsonResponse(data)


@require_safe
def api_segment(request):
    """The segment under a map point: ?lon=&lat=. For the picker."""
    try:
        pt = [float(request.GET['lon']), float(request.GET['lat'])]
    except (KeyError, ValueError):
        return JsonResponse({'error': 'lon and lat are needed'}, status=400)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        s = resolve(cur, [pt])[0]
        if not s:
            conn.rollback()
            return JsonResponse({'error': 'no flow line within %d m of that point' % SNAP_M}, status=404)
        g, mid, length = _geoms(cur, [s])[0]
        conn.rollback()
    finally:
        _put_conn(conn)
    return JsonResponse(dict(s, anchor=pt, geometry=g, mid=mid['coordinates'], length_m=round(length)))


def _save_upload(f):
    ext = (f.name.rsplit('.', 1)[-1] if '.' in f.name else '').lower()
    if ext not in _TYPES:
        raise ValueError('the file must be .mp4, .webm or .gif')
    if f.size > MAX_UPLOAD:
        raise ValueError('the file is %.0f MB; the limit is %d MB' % (f.size / 1048576, MAX_UPLOAD // 1048576))
    head = f.read(16)
    f.seek(0)
    if _sniff(head) != ext:
        raise ValueError('that does not look like a .%s file' % ext)
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    name = '%s.%s' % (secrets.token_hex(8), ext)
    tmp = VIDEO_DIR / (name + '.part')
    with tmp.open('wb') as out:
        for chunk in f.chunks():
            out.write(chunk)
    tmp.rename(VIDEO_DIR / name)
    return name


@inventory_editor_required
@require_http_methods(['POST'])
def api_video_save(request):
    """Create or change one video. multipart/form-data:
    id (omit to create), kind, url (planet), file (uploaded kinds, on create),
    caption, date (see landslidescience.dates), points (JSON [[lon, lat], ...],
    one per segment; every point must land on a flow line and at least one is
    needed), delete=1."""
    P = request.POST
    vid = int(P['id']) if P.get('id', '').isdigit() else None
    new_file, slug, fetch_url = None, None, None
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if vid:
            cur.execute('SELECT kind, file FROM glacier_videos WHERE id = %s', (vid,))
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return JsonResponse({'error': 'no such video'}, status=404)
            kind, old_file = row
            if P.get('delete'):
                cur.execute('DELETE FROM glacier_videos WHERE id = %s', (vid,))
                conn.commit()
                if old_file and _FILE_RE.match(old_file):
                    (VIDEO_DIR / old_file).unlink(missing_ok=True)
                return JsonResponse({'ok': True, 'deleted': vid})
        else:
            kind = P.get('kind', '')
            if kind not in _KIND:
                conn.rollback()
                return JsonResponse({'error': 'choose what kind of video this is'}, status=400)
        try:
            pts = json.loads(P.get('points') or '[]')
            pts = [[float(p[0]), float(p[1])] for p in pts]
        except (ValueError, TypeError, IndexError):
            conn.rollback()
            return JsonResponse({'error': 'points must be [[lon, lat], ...]'}, status=400)
        segs = resolve(cur, pts)
        if not pts or any(s is None for s in segs):
            conn.rollback()
            return JsonResponse({'error': 'a video needs at least one segment, and every chosen point must be on a flow line'},
                                status=400)
        keep, seen = [], set()
        for p, s in zip(pts, segs):
            if s['key'] not in seen:
                seen.add(s['key'])
                keep.append(p)
        try:
            d0, d1 = parse_span(P.get('date'))
        except ValueError as e:
            conn.rollback()
            return JsonResponse({'error': str(e)}, status=400)
        cap = (P.get('caption') or '').strip()[:500]
        if not vid:
            if _KIND[kind]['upload']:
                f = request.FILES.get('file')
                link = (P.get('url') or '').strip()
                if f:
                    try:
                        new_file = _save_upload(f)
                    except ValueError as e:
                        conn.rollback()
                        return JsonResponse({'error': str(e)}, status=400)
                elif link:
                    did = drive_id(link)
                    if not did:
                        conn.rollback()
                        return JsonResponse({'error': 'that is not a Google Drive file link (drive.google.com/file/d/…)'},
                                            status=400)
                    fetch_url = DRIVE_GET.format(id=did)
                else:
                    conn.rollback()
                    return JsonResponse({'error': 'choose the video file, or paste its Google Drive share link'}, status=400)
            else:
                slug = planet.slug_from_url(P.get('url'))
                if not slug:
                    conn.rollback()
                    return JsonResponse({'error': 'that is not a Planet story link (%s…)' % planet.STORY_PREFIX}, status=400)
                cur.execute('SELECT id FROM glacier_videos WHERE slug = %s', (slug,))
                dup = cur.fetchone()
                if dup:
                    conn.rollback()
                    return JsonResponse({'error': 'that story is already linked (video %d); edit it to add segments' % dup[0],
                                         'existing': dup[0]}, status=409)
                cur.execute('INSERT INTO planet_stories (slug) VALUES (%s) ON CONFLICT (slug) DO NOTHING', (slug,))
            cur.execute("""INSERT INTO glacier_videos (kind, slug, file, caption, date_min, date_max, added_by, source_url)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (kind, slug, new_file, cap, d0, d1, request.user.get_username(),
                         (P.get('url') or '').strip()[:500] if fetch_url else None))
            vid = cur.fetchone()[0]
        else:
            cur.execute('UPDATE glacier_videos SET caption = %s, date_min = %s, date_max = %s WHERE id = %s',
                        (cap, d0, d1, vid))
            cur.execute('DELETE FROM glacier_video_anchors WHERE video_id = %s', (vid,))
        for p in keep:
            cur.execute("""INSERT INTO glacier_video_anchors (video_id, geom)
                           VALUES (%s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))""", (vid, p[0], p[1]))
        conn.commit()
    except Exception:
        conn.rollback()
        if new_file:
            (VIDEO_DIR / new_file).unlink(missing_ok=True)
        raise
    finally:
        _put_conn(conn)
    if slug:
        planet.ensure_archived_async(slug)
    if fetch_url:
        threading.Thread(target=_fetch_worker, args=(vid, fetch_url), daemon=True, name='video-fetch-%d' % vid).start()
    return JsonResponse({'ok': True, 'id': vid, 'segments': len(keep)})


_RANGE = re.compile(r'^bytes=(\d*)-(\d*)$')


def _part(f, start, length, block=1 << 16):
    f.seek(start)
    try:
        while length > 0:
            chunk = f.read(min(block, length))
            if not chunk:
                break
            length -= len(chunk)
            yield chunk
    finally:
        f.close()


@require_safe
def serve_georef(request, name):
    """One static product image of a placed video (GEOREF_PRODUCTS)."""
    if not _GEOREF_RE.match(name or ''):
        return HttpResponseNotFound()
    path = GEOREF_DIR / name
    if not path.is_file():
        return HttpResponseNotFound()
    resp = FileResponse(path.open('rb'), content_type='image/webp')
    resp['Cache-Control'] = 'public, max-age=86400'
    return resp


@require_safe
def serve_video(request, name):
    """An uploaded video, with single byte ranges: a player asks for the
    file in pieces, and Safari will not play an mp4 from a server that
    cannot give them. Without this a 60 MB animation was sent whole before
    the first frame."""
    if not _FILE_RE.match(name or ''):
        return HttpResponseNotFound()
    path = VIDEO_DIR / name
    if not path.is_file():
        return HttpResponseNotFound()
    size = path.stat().st_size
    ctype = _TYPES[name.rsplit('.', 1)[-1]]
    m = _RANGE.match(request.headers.get('Range', '').strip())
    if m and (m.group(1) or m.group(2)) and size:
        if m.group(1):
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        else:                                   # bytes=-N: the last N bytes
            start, end = max(size - int(m.group(2)), 0), size - 1
        if start > end or start >= size:
            resp = HttpResponse(status=416)
            resp['Content-Range'] = 'bytes */%d' % size
            return resp
        resp = StreamingHttpResponse(_part(path.open('rb'), start, end - start + 1), status=206, content_type=ctype)
        resp['Content-Range'] = 'bytes %d-%d/%d' % (start, end, size)
        resp['Content-Length'] = str(end - start + 1)
    else:
        resp = FileResponse(path.open('rb'), content_type=ctype)
        resp['Content-Length'] = str(size)
    resp['Accept-Ranges'] = 'bytes'
    resp['Cache-Control'] = 'public, max-age=31536000, immutable'
    return resp
