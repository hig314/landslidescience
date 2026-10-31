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


def api_reviewed(request):
    if not _installed():
        return JsonResponse({'error': 'the quantity cube is not installed on this server'}, status=503)
    from . import views
    rv = _reviewed()
    if rv['mask'] is None:
        return JsonResponse({'version': rv['version'], 'cells': None, 'outside': []})
    # The landslides change too; key their test on the inventory's version as well.
    key = (rv['version'], views._data_version)
    hit = _cache.get('outside')
    if not hit or hit['key'] != key:
        meta = _cube()
        conn = views._get_conn()
        try:
            cur = conn.cursor()
            # The same centroid tools/analysis/sample_quantities.py samples at.
            cur.execute("SELECT landslide_id, ST_X(c), ST_Y(c) FROM (SELECT landslide_id, "
                        "ST_Transform(ST_Centroid(ST_Collect(geom)), 3338) c FROM landslide_polygons "
                        "GROUP BY landslide_id) s")
            rows = cur.fetchall()
        finally:
            views._put_conn(conn)
        a, _, c, _, e, f = meta['transform']
        outside = []
        for lid, x, y in rows:
            if x is None:
                continue
            col, row = int((x - c) // a), int((y - f) // e)
            inside = 0 <= row < meta['height'] and 0 <= col < meta['width'] and rv['mask'][row, col]
            if not inside:
                outside.append(lid)
        land = np.asarray(meta['land'])
        hit = _cache['outside'] = {'key': key, 'outside': sorted(outside), 'cells': int((land & rv['mask']).sum())}
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
    if not _installed():
        return JsonResponse({'error': 'the quantity cube is not installed on this server'}, status=503)
    meta = _cube()
    kx, ky = request.GET.get('x', ''), request.GET.get('y', '')
    if kx not in meta['quantities'] or ky not in meta['quantities']:
        return JsonResponse({'error': 'unknown quantity'}, status=400)
    rv = _reviewed()
    key = f'{kx}|{ky}|{rv["version"]}'
    if key not in _cache:
        qx, qy = meta['quantities'][kx], meta['quantities'][ky]
        x = _positions(qx, np.asarray(_grid(kx))); y = _positions(qy, np.asarray(_grid(ky)))
        ok = np.asarray(meta['land']) & np.isfinite(x) & np.isfinite(y)
        if rv['mask'] is not None:
            ok &= rv['mask']
        ex, ey = _axis(qx), _axis(qy)
        # The top edge is inclusive so an integer quantity's maximum (81 of 81)
        # lands in its own bin rather than falling off the axis.
        h, _, _ = np.histogram2d(np.clip(x[ok], ex[0], ex[-1] - 1e-9), np.clip(y[ok], ey[0], ey[-1] - 1e-9), bins=[ex, ey])
        h = h.T.astype(np.int64)                      # [y][x]
        _cache[key] = {'x': qx, 'y': qy, 'xsize': len(ex) - 1, 'ysize': len(ey) - 1,
                       'grid': h.ravel().tolist(), 'max': int(h.max()), 'total': int(h.sum()),
                       'reviewed': rv['version']}
    return JsonResponse(_cache[key])


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
