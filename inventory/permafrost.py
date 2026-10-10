"""Permafrost patch sampler: the published 1 km cells inside a circle.

Backs the map's Permafrost analysis panel (permafrost.js). The 1 km grids
are small (11 fields x 2706 x 2179 Float32, ~260 MB) and live in
data/permafrost/ as .npy next to grid.json, written by
tools/permafrost/export_web.py; they are memory-mapped once per process.
The 60 m products are not here -- 1.6 G cells each -- the browser reads
those from value-encoded PMTiles (bake_values.py).

GET api/permafrost/patch/?lon=&lat=&r=   (r in km, default 10, max 30)
-> {center: {lon, lat, x, y}, r_m, curve: {...}, lapse: {gruber_b, obu_b,
    obu_c} (medians over the patch), cells: [{x, y, lon, lat, elev, north,
    maat, pzi, magt, std, prob, magt_filled, filled: bool}, ...]}
Null for a field the product does not have at that cell. CRS work goes
through rasterio.warp.transform (rasterio is in the image for the trace
rasters; pyproj is not).
"""
import json
import math
from pathlib import Path

import numpy as np
from django.conf import settings
from django.http import JsonResponse

GRIDS = Path(settings.BASE_DIR) / 'data' / 'permafrost'
R_DEFAULT_KM, R_MAX_KM = 10.0, 30.0
_cache = {}


def _grids():
    if 'meta' not in _cache:
        meta = json.loads((GRIDS / 'grid.json').read_text())
        meta['arrays'] = {k: np.load(GRIDS / f'{k}.npy', mmap_mode='r') for k in meta['fields']}
        _cache['meta'] = meta
    return _cache['meta']


def _num(v):
    return None if (v is None or not np.isfinite(v)) else round(float(v), 4)


def api_patch(request):
    try:
        lon = float(request.GET['lon']); lat = float(request.GET['lat'])
    except (KeyError, ValueError):
        return JsonResponse({'error': 'lon and lat are required'}, status=400)
    try:
        r_km = min(R_MAX_KM, max(0.5, float(request.GET.get('r', R_DEFAULT_KM))))
    except ValueError:
        return JsonResponse({'error': 'bad r'}, status=400)
    if not (GRIDS / 'grid.json').is_file():
        return JsonResponse({'error': 'permafrost grids are not installed on this server'}, status=503)
    from rasterio.warp import transform
    meta = _grids()
    a, _, c, _, e, f = meta['transform']
    xs, ys = transform('EPSG:4326', meta['crs'], [lon], [lat])
    cx, cy = xs[0], ys[0]
    r = r_km * 1000.0
    col0, col1 = int((cx - r - c) / a), int((cx + r - c) / a) + 1
    row0, row1 = int((cy + r - f) / e), int((cy - r - f) / e) + 1
    col0, row0 = max(0, col0), max(0, row0)
    col1, row1 = min(meta['width'], col1), min(meta['height'], row1)
    if col1 <= col0 or row1 <= row0:
        return JsonResponse({'center': {'lon': lon, 'lat': lat, 'x': cx, 'y': cy}, 'r_m': r, 'cells': [],
                             'curve': meta['curve'], 'lapse': {}})
    rows, cols = np.mgrid[row0:row1, col0:col1]
    px = c + (cols + 0.5) * a
    py = f + (rows + 0.5) * e
    inside = (px - cx) ** 2 + (py - cy) ** 2 <= r * r
    arr = meta['arrays']
    vals = {k: np.asarray(arr[k][row0:row1, col0:col1])[inside] for k in meta['fields']}
    land = np.isfinite(vals['maat']) | np.isfinite(vals['magt_filled'])
    px, py = px[inside][land], py[inside][land]
    for k in vals:
        vals[k] = vals[k][land]
    lons, lats = transform(meta['crs'], 'EPSG:4326', list(map(float, px)), list(map(float, py)))
    cells = []
    for i in range(len(px)):
        cells.append({'x': round(float(px[i])), 'y': round(float(py[i])), 'lon': round(lons[i], 5), 'lat': round(lats[i], 5),
                      'elev': _num(vals['elev'][i]), 'north': _num(vals['north'][i]),
                      'maat': _num(vals['maat'][i]), 'pzi': _num(vals['pzi'][i]),
                      'magt': _num(vals['magt'][i]), 'std': _num(vals['std'][i]), 'prob': _num(vals['prob'][i]),
                      'magt_filled': _num(vals['magt_filled'][i]),
                      'filled': not (vals['magt'][i] is not None and np.isfinite(vals['magt'][i]))})

    def med(k):
        v = vals[k][np.isfinite(vals[k])]
        return _num(float(np.median(v))) if len(v) else None
    return JsonResponse({'center': {'lon': lon, 'lat': lat, 'x': round(cx), 'y': round(cy)}, 'r_m': r,
                         'curve': meta['curve'],
                         'lapse': {'gruber_b': med('gruber_b'), 'obu_b': med('obu_b'), 'obu_c': med('obu_c')},
                         'cells': cells})
