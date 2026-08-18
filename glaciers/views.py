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

Behind the pre-launch preview password like /inventory/* (middleware.py).
"""
import json
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.views.decorators.http import require_safe

DATA_DIR = Path(settings.BASE_DIR) / 'data' / 'glaciers'


def _catalog():
    """[{slug, name, center, zoom}, …] from the bundle headers on disk."""
    out = []
    if DATA_DIR.is_dir():
        for p in sorted(DATA_DIR.glob('*.json')):
            try:
                h = json.loads(p.read_text())
                out.append({'slug': p.stem, 'name': h.get('name', p.stem),
                            'center': h.get('center'), 'zoom': h.get('zoom', 10)})
            except (ValueError, OSError):
                continue
    return out


@require_safe
def home(request):
    return render(request, 'glaciers/map.html', {
        'catalog_json': json.dumps(_catalog()),
    })


@require_safe
def pairs(request):
    """Raw image-pair viewer — the literal counterpart to the tracer app.
    Bundle built by tools/build_pair_vectors.py from a sweep_pairs.py sweep."""
    return render(request, 'glaciers/pairs.html', {
        'bundle': request.GET.get('bundle', 'columbia_pairs'),
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
    if name.endswith('.bin') and gz.exists():
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
