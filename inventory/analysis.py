"""The analysis panel's server side: the quantity cube (ANALYSIS_TOOL_PLAN.md).

data/quantities/ holds one Float32 .npy per axis-capable quantity -- the
native product point-sampled at the centre of every cell of the 1 km
EPSG:3338 lattice (tools/analysis/build_quantity_cube.py; the registry is
tools/analysis/quantities.py) -- plus land.npy and grid.json. Memory-mapped
once per process. Three endpoints:

  GET api/analysis/quantities/
      -> {quantities: {key: {key, overlay, label, units, res, note, min,
          max, bins, classes?, integer?}}, cell_km2: 1}
      what this server can put on an axis (the browser crosses it with
      the overlay registry: an overlay whose values.key is here is pickable).
  GET api/analysis/density/?x=<key>&y=<key>
      -> {x: {...}, y: {...}, xsize, ysize, grid: [row-major y*xsize+x],
          max, total, reviewed}
      the 2-D histogram of every land cell INSIDE THE REVIEWED AREA with a
      value for both, computed on request (5.9 M cells, tens of ms) and
      cached per pair (and reviewed-area version) for the process. An
      ordinal quantity (DGGS) bins by class position.
  GET api/analysis/density/?x&y&view=w,s,e,n   |   &circle=lon,lat,r_km
      -> the same for a SUBSET of the reviewed area (the map view, or the
         sample circle), not cached, plus subset: {kind, cells, ids,
         stats: {x, y}, outline (circle only)}. The unit is the 1 km cell:
         a cell is in when its centre is, and a landslide when its cell is,
         so a rate's two halves share one footprint (Hig, 2026-10-31).
  GET api/analysis/reviewed/
      -> {version, cells, outside: [landslide ids]}
      the reviewed area (table reviewed_areas, migrate_reviewed_areas): its
      land cells, and the landslides whose centroid's lattice cell is
      outside it, which the panel leaves out of its counts. Ground nobody
      has searched must neither dilute a rate nor add landslides to it.
  GET api/analysis/sample/?lon=&lat=&r=<km>&q=<key>,<key>
      -> {n, lon: [...], lat: [...], <key>: [...], ...}
      the lattice cells inside a circle (r <= 50 km), columnar; the sample
      "blotch" for a quantity that has no client-readable value tiles.
"""
import json
from pathlib import Path

import numpy as np
from django.conf import settings
from django.http import JsonResponse

CUBE = Path(settings.BASE_DIR) / 'data' / 'quantities'
R_MAX_KM = 50.0      # the panel's sample is up to 100 km across
_cache = {}


def _cube():
    if 'meta' not in _cache:
        meta = json.loads((CUBE / 'grid.json').read_text())
        meta['arrays'] = {}
        meta['land'] = np.load(CUBE / 'land.npy', mmap_mode='r')
        _cache['meta'] = meta
    return _cache['meta']


def _grid(key):
    meta = _cube()
    if key not in meta['arrays']:
        meta['arrays'][key] = np.load(CUBE / f'{key}.npy', mmap_mode='r')
    return meta['arrays'][key]


def _reviewed():
    """The reviewed area on the lattice: {version, mask (bool, H x W) | None}.

    The union of reviewed_areas, rasterised at cell centres (the cell's
    value is its centre's, so the cell is in when its centre is), checked
    against the table on every call (one cheap query) and rebuilt only when
    a row was added or removed. No table yet (migrate_reviewed_areas not
    run) = no mask: every land cell counts, as before.
    """
    from . import views
    conn = views._get_conn()
    try:
        cur = conn.cursor()
        try:
            cur.execute("SELECT count(*), coalesce(max(id), 0), coalesce(max(added_at)::text, '') FROM reviewed_areas")
        except Exception:
            conn.rollback()
            return {'version': None, 'mask': None}
        version = '%s.%s.%s' % cur.fetchone()
        hit = _cache.get('reviewed')
        if hit and hit['version'] == version:
            return hit
        cur.execute("SELECT ST_AsGeoJSON(ST_Transform(geom, 3338)) FROM reviewed_areas")
        shapes = [(json.loads(r[0]), 1) for r in cur.fetchall()]
    finally:
        views._put_conn(conn)
    meta = _cube()
    mask = None
    if shapes:
        from affine import Affine
        from rasterio.features import rasterize
        mask = rasterize(shapes, out_shape=(meta['height'], meta['width']), transform=Affine(*meta['transform'][:6]),
                         fill=0, dtype='uint8').astype(bool)
    _cache['reviewed'] = {'version': version, 'mask': mask}
    _cache.pop('outside', None)
    return _cache['reviewed']


def _ls_cells():
    """Every landslide's lattice cell: {key, ids, row, col, ing, lon, lat}.

    The cell holding the polygon-union centroid (the point
    tools/analysis/sample_quantities.py samples at), and that cell's CENTRE
    in lon/lat. A landslide counts in a subset exactly when its cell does --
    the same cell-centre rule as the terrain -- so the numerator and the
    denominator of a rate share one footprint. Cached per inventory version.
    """
    from . import views
    key = views._data_version
    hit = _cache.get('lscells')
    if hit and hit['key'] == key:
        return hit
    conn = views._get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT landslide_id, ST_X(c), ST_Y(c) FROM (SELECT landslide_id, "
                    "ST_Transform(ST_Centroid(ST_Collect(geom)), 3338) c FROM landslide_polygons "
                    "GROUP BY landslide_id) s WHERE c IS NOT NULL")
        rows = cur.fetchall()
    finally:
        views._put_conn(conn)
    meta = _cube()
    a, _, c, _, e, f = meta['transform']
    ids = np.array([r[0] for r in rows], dtype=np.int64)
    x = np.array([r[1] for r in rows], dtype=np.float64); y = np.array([r[2] for r in rows], dtype=np.float64)
    col = np.floor((x - c) / a).astype(np.int64); row = np.floor((y - f) / e).astype(np.int64)
    ing = (row >= 0) & (row < meta['height']) & (col >= 0) & (col < meta['width'])
    lon = lat = np.zeros(0)
    if len(ids):
        from rasterio.warp import transform
        lon, lat = transform(meta['crs'], 'EPSG:4326', list(c + (col + 0.5) * a), list(f + (row + 0.5) * e))
        lon, lat = np.array(lon), np.array(lat)
    _cache['lscells'] = {'key': key, 'ids': ids, 'row': np.clip(row, 0, meta['height'] - 1),
                         'col': np.clip(col, 0, meta['width'] - 1), 'ing': ing, 'lon': lon, 'lat': lat}
    return _cache['lscells']


def _rv_mask():
    """The reviewed area as a bool lattice (every cell when there is no table)."""
    rv = _reviewed()
    if rv['mask'] is None:
        meta = _cube()
        return rv['version'], np.ones((meta['height'], meta['width']), dtype=bool)
    return rv['version'], rv['mask']


def _land_lonlat():
    """lon/lat of the centre of every land cell (flat index), for the view test. ~28 MB, once per process."""
    if 'landll' not in _cache:
        from rasterio.warp import transform
        meta = _cube()
        a, _, c, _, e, f = meta['transform']
        idx = np.flatnonzero(np.asarray(meta['land'])).astype(np.int64)
        rows, cols = np.divmod(idx, meta['width'])
        lon, lat = transform(meta['crs'], 'EPSG:4326', list(c + (cols + 0.5) * a), list(f + (rows + 0.5) * e))
        _cache['landll'] = {'idx': idx, 'lon': np.array(lon, dtype=np.float32), 'lat': np.array(lat, dtype=np.float32)}
    return _cache['landll']


def _in_view(lon, lat, w, s, e, n):
    """map.js makeViewportTest, for arrays: the same wrap, so the server and the map agree."""
    ok = (lat >= s) & (lat <= n)
    if not (np.isfinite(w) and np.isfinite(e)) or e - w >= 360:
        return ok
    wrap = lambda v: ((v + 180) % 360 + 360) % 360 - 180      # noqa: E731
    lw, le, v = wrap(w), wrap(e), wrap(lon)
    return ok & (((v >= lw) & (v <= le)) if lw <= le else ((v >= lw) | (v <= le)))


def _parse_subset(request):
    """('view', (w, s, e, n)) | ('circle', (lon, lat, r_km)) | None; ValueError on a bad one."""
    if request.GET.get('view'):
        w, s, e, n = (float(t) for t in request.GET['view'].split(','))
        return 'view', (w, s, e, n)
    if request.GET.get('circle'):
        lon, lat, r = (float(t) for t in request.GET['circle'].split(','))
        return 'circle', (lon, lat, min(R_MAX_KM, max(0.5, r)))
    return None


def _subset_cells(kind, args, mask):
    """Flat indices of the land cells of the reviewed area in the subset, the
    landslide ids in it, and for a circle its window (for the outline)."""
    meta = _cube()
    L = _ls_cells()
    in_rv = L['ing'] & mask[L['row'], L['col']]
    if kind == 'view':
        ll = _land_lonlat()
        sel = mask.ravel()[ll['idx']] & _in_view(ll['lon'], ll['lat'], *args)
        flat = ll['idx'][sel]
        ls_in = in_rv & _in_view(L['lon'], L['lat'], *args)
        return flat, L['ids'][ls_in], None
    from rasterio.warp import transform
    lon, lat, r_km = args
    a, _, c, _, e, f = meta['transform']
    xs, ys = transform('EPSG:4326', meta['crs'], [lon], [lat])
    cx, cy, r = xs[0], ys[0], r_km * 1000.0
    col0, col1 = max(0, int((cx - r - c) / a)), min(meta['width'], int((cx + r - c) / a) + 1)
    row0, row1 = max(0, int((cy + r - f) / e)), min(meta['height'], int((cy - r - f) / e) + 1)
    if col1 <= col0 or row1 <= row0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64), None
    rows, cols = np.mgrid[row0:row1, col0:col1]
    disc = ((c + (cols + 0.5) * a - cx) ** 2 + (f + (rows + 0.5) * e - cy) ** 2) <= r * r
    win = disc & mask[row0:row1, col0:col1]
    land = win & np.asarray(meta['land'][row0:row1, col0:col1])
    flat = (rows[land] * meta['width'] + cols[land]).astype(np.int64)
    lr, lc = L['row'] - row0, L['col'] - col0
    inwin = in_rv & (lr >= 0) & (lr < row1 - row0) & (lc >= 0) & (lc < col1 - col0)
    ls_in = np.zeros(len(L['ids']), dtype=bool)
    ls_in[inwin] = win[lr[inwin], lc[inwin]]
    return flat, L['ids'][ls_in], (row0, col0, land)


def _outline(window):
    """The counted cells of a circle as GeoJSON polygons in lon/lat (the blocky
    footprint the panel draws on the map, so what was counted is visible)."""
    from affine import Affine
    from rasterio.features import shapes
    from rasterio.warp import transform_geom
    meta = _cube()
    row0, col0, land = window
    a, _, c, _, e, f = meta['transform']
    t = Affine(a, 0, c + col0 * a, 0, e, f + row0 * e)
    feats = [{'type': 'Feature', 'properties': {}, 'geometry': transform_geom(meta['crs'], 'EPSG:4326', g)}
             for g, v in shapes(land.astype(np.uint8), mask=land, transform=t) if v]
    return {'type': 'FeatureCollection', 'features': feats}


def _stats(q, v):
    """Per-axis summary of a subset's cells: 10/50/90 % (or the modal class)."""
    v = v[np.isfinite(v)]
    if not len(v):
        return None
    if q.get('classes'):
        vals, n = np.unique(v, return_counts=True)
        return {'mode': float(vals[np.argmax(n)])}
    p10, p50, p90 = np.percentile(v, [10, 50, 90])
    return {'p10': float(p10), 'p50': float(p50), 'p90': float(p90)}


def api_reviewed(request):
    if not _installed():
        return JsonResponse({'error': 'the quantity cube is not installed on this server'}, status=503)
    rv = _reviewed()
    if rv['mask'] is None:
        return JsonResponse({'version': rv['version'], 'cells': None, 'outside': []})
    L = _ls_cells()
    key = (rv['version'], L['key'])
    hit = _cache.get('outside')
    if not hit or hit['key'] != key:
        inside = L['ing'] & rv['mask'][L['row'], L['col']]
        land = np.asarray(_cube()['land'])
        hit = _cache['outside'] = {'key': key, 'outside': sorted(int(i) for i in L['ids'][~inside]),
                                   'cells': int((land & rv['mask']).sum())}
    return JsonResponse({'version': rv['version'], 'cells': hit['cells'], 'outside': hit['outside']})


def _installed():
    return (CUBE / 'grid.json').is_file()


def _axis(q):
    """Bin edges for a quantity: its range, or class positions for an ordinal one."""
    if q.get('classes'):
        return np.arange(len(q['classes']) + 1, dtype=np.float64)
    return np.linspace(q['min'], q['max'], q['bins'] + 1)


def _positions(q, v):
    """Values -> axis coordinates (class index for an ordinal quantity, + 0.5)."""
    if not q.get('classes'):
        return v
    out = np.full(v.shape, np.nan, dtype=np.float32)
    for i, c in enumerate(q['classes']):
        out[v == c] = i + 0.5
    return out


def api_quantities(request):
    if not _installed():
        return JsonResponse({'error': 'the quantity cube is not installed on this server', 'quantities': {}}, status=503)
    return JsonResponse({'quantities': _cube()['quantities'], 'cell_km2': 1.0})


def api_density(request):
    """The terrain density of a pair; with view= or circle=, of that subset of
    the reviewed area too, plus the landslides in it (by the same cell rule),
    each axis's summary and, for a circle, the counted cells' outline."""
    if not _installed():
        return JsonResponse({'error': 'the quantity cube is not installed on this server'}, status=503)
    meta = _cube()
    kx, ky = request.GET.get('x', ''), request.GET.get('y', '')
    if kx not in meta['quantities'] or ky not in meta['quantities']:
        return JsonResponse({'error': 'unknown quantity'}, status=400)
    try:
        sub = _parse_subset(request)
    except ValueError:
        return JsonResponse({'error': 'view is w,s,e,n and circle is lon,lat,r_km'}, status=400)
    version, mask = _rv_mask()
    qx, qy = meta['quantities'][kx], meta['quantities'][ky]
    ex, ey = _axis(qx), _axis(qy)

    def hist(x, y):
        ok = np.isfinite(x) & np.isfinite(y)
        # The top edge is inclusive so an integer quantity's maximum (81 of 81)
        # lands in its own bin rather than falling off the axis.
        h, _, _ = np.histogram2d(np.clip(x[ok], ex[0], ex[-1] - 1e-9), np.clip(y[ok], ey[0], ey[-1] - 1e-9), bins=[ex, ey])
        h = h.T.astype(np.int64)                      # [y][x]
        return {'xsize': len(ex) - 1, 'ysize': len(ey) - 1, 'grid': h.ravel().tolist(), 'max': int(h.max()), 'total': int(h.sum())}

    if sub is None:
        key = f'{kx}|{ky}|{version}'
        if key not in _cache:
            x = _positions(qx, np.asarray(_grid(kx))); y = _positions(qy, np.asarray(_grid(ky)))
            land = np.asarray(meta['land']) & mask
            _cache[key] = dict(hist(x[land], y[land]), x=qx, y=qy, reviewed=version)
        return JsonResponse(_cache[key])
    kind, args = sub
    flat, ids, window = _subset_cells(kind, args, mask)
    vx = np.asarray(_grid(kx)).ravel()[flat]; vy = np.asarray(_grid(ky)).ravel()[flat]
    out = dict(hist(_positions(qx, vx), _positions(qy, vy)), x=qx, y=qy, reviewed=version)
    out['subset'] = {'kind': kind, 'args': list(args), 'cells': int(len(flat)), 'ids': [int(i) for i in ids],
                     'stats': {'x': _stats(qx, vx), 'y': _stats(qy, vy)}}
    if window is not None:
        out['subset']['outline'] = _outline(window)
    return JsonResponse(out)


def api_sample(request):
    if not _installed():
        return JsonResponse({'error': 'the quantity cube is not installed on this server'}, status=503)
    try:
        lon = float(request.GET['lon']); lat = float(request.GET['lat'])
        r_km = min(R_MAX_KM, max(0.5, float(request.GET.get('r', 10))))
    except (KeyError, ValueError):
        return JsonResponse({'error': 'lon, lat and r are required'}, status=400)
    meta = _cube()
    keys = [k for k in request.GET.get('q', '').split(',') if k in meta['quantities']]
    from rasterio.warp import transform
    a, _, c, _, e, f = meta['transform']
    xs, ys = transform('EPSG:4326', meta['crs'], [lon], [lat])
    cx, cy = xs[0], ys[0]
    r = r_km * 1000.0
    col0, col1 = max(0, int((cx - r - c) / a)), min(meta['width'], int((cx + r - c) / a) + 1)
    row0, row1 = max(0, int((cy + r - f) / e)), min(meta['height'], int((cy - r - f) / e) + 1)
    out = {'n': 0, 'lon': [], 'lat': []}
    for k in keys:
        out[k] = []
    if col1 <= col0 or row1 <= row0:
        return JsonResponse(out)
    rows, cols = np.mgrid[row0:row1, col0:col1]
    px = c + (cols + 0.5) * a; py = f + (rows + 0.5) * e
    inside = ((px - cx) ** 2 + (py - cy) ** 2 <= r * r) & np.asarray(meta['land'][row0:row1, col0:col1])
    px, py = px[inside], py[inside]
    lons, lats = transform(meta['crs'], 'EPSG:4326', list(map(float, px)), list(map(float, py)))
    out['n'] = len(px)
    out['lon'] = [round(v, 5) for v in lons]; out['lat'] = [round(v, 5) for v in lats]
    for k in keys:
        v = np.asarray(_grid(k)[row0:row1, col0:col1])[inside]
        out[k] = [None if not np.isfinite(t) else round(float(t), 4) for t in v]
    return JsonResponse(out)
