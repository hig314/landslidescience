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

# The raw image-pair stack (/glaciers/pairs/, the fitted-pair overlays and the
# "robust pair fit" tracer field) is research scaffolding: it depends on
# multi-hundred-MB sweeps that are not deployed, and it is still changing. It
# is exposed only where those sweeps exist, so production serves the finished
# tracer app and nothing half-built.
def experimental_enabled():
    return (DATA_DIR / 'experiments').is_dir()


def _catalog():
    """[{slug, name, center, zoom}, …] from the bundle headers on disk."""
    out = []
    if DATA_DIR.is_dir():
        for p in sorted(DATA_DIR.glob('*.json')):
            try:
                h = json.loads(p.read_text())
            except (ValueError, OSError):
                continue
            # Only real site bundles. Without this test the regional manifest
            # (and any future sidecar json) showed up as a phantom site in the
            # pulldown, selectable and broken.
            if not (h.get('name') and h.get('center') and h.get('bin')):
                continue
            out.append({'slug': p.stem, 'name': h['name'],
                        'center': h['center'], 'zoom': h.get('zoom', 10)})
    return out


@require_safe
def home(request):
    return render(request, 'glaciers/map.html', {
        'catalog_json': json.dumps(_catalog()),
        'experimental': experimental_enabled(),
    })


@require_safe
def pairs(request):
    """Raw image-pair viewer — the literal counterpart to the tracer app.
    Bundle built by tools/build_pair_vectors.py from a sweep_pairs.py sweep."""
    if not experimental_enabled():
        raise Http404
    exp = DATA_DIR / 'experiments'
    avail = sorted(p.stem[:-len('_vectors')] for p in exp.glob('*_vectors.json')) \
        if exp.is_dir() else []
    sel = request.GET.get('bundle', 'columbia_pairs')
    if avail and sel not in avail:
        sel = avail[0]
    return render(request, 'glaciers/pairs.html', {
        'bundle': sel,
        'bundles_json': json.dumps(avail),
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
