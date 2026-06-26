import mimetypes

from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404

from .models import HostedFile

# Types Python's mimetypes table commonly misses for geo/data files we host.
_EXTRA_TYPES = {
    '.kml': 'application/vnd.google-earth.kml+xml',
    '.kmz': 'application/vnd.google-earth.kmz',
    '.geojson': 'application/geo+json',
    '.gpkg': 'application/geopackage+sqlite3',
}


def _guess_type(name):
    for ext, ctype in _EXTRA_TYPES.items():
        if name.lower().endswith(ext):
            return ctype
    ctype, _ = mimetypes.guess_type(name)
    return ctype or 'application/octet-stream'


def serve(request, name):
    """Serve a HostedFile by its public name at /files/<name>.

    Fully public (no auth, no preview barrier — that middleware only guards
    /inventory/*). Not linked from anywhere, so files are reachable only by
    someone who knows the URL.
    """
    hf = get_object_or_404(HostedFile, name=name)
    try:
        fh = hf.file.open('rb')
    except (FileNotFoundError, ValueError):
        raise Http404("File missing from storage.")
    ctype = hf.content_type or _guess_type(name)
    resp = FileResponse(fh, content_type=ctype)
    disposition = 'inline' if hf.inline else 'attachment'
    resp['Content-Disposition'] = f'{disposition}; filename="{name}"'
    return resp
