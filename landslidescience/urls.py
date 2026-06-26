from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path, re_path
from django.views.static import serve as static_serve


def robots_txt(_request):
    """Pre-release: discourage crawling. Belt-and-suspenders with the
    `<meta name="robots" content="noindex, nofollow, noarchive">` tag in
    the inventory + pages base templates. Drop this view once the site is
    ready for indexing.
    """
    body = (
        "# landslidescience.org — pre-release review\n"
        "User-agent: *\n"
        "Disallow: /\n"
    )
    return HttpResponse(body, content_type='text/plain')


# Self-hosted USGS susceptibility value-tiles (Belair et al. 2024, Alaska).
# Built by tools/build_susc_tiles.sh into data/susc_tiles/<model>/<z>/<x>/<y>.png.
# data/ is volume-mounted into the container in both dev and prod, so the same
# route serves the tiles in both. The map recolors the raw value client-side
# (see SUSC_COLOR_RAMP in map.js). Pruned ocean tiles 404 → MapLibre draws
# nothing there, which is correct.
_SUSC_TILES_DIR = settings.BASE_DIR / 'data' / 'susc_tiles'


def susc_tile(request, model, z, x, y):
    resp = static_serve(request, f'{model}/{z}/{x}/{y}.png',
                         document_root=str(_SUSC_TILES_DIR))
    # Immutable: a given (model, z, x, y) is content-stable across rebuilds.
    resp['Cache-Control'] = 'public, max-age=31536000, immutable'
    return resp


urlpatterns = [
    path('robots.txt', robots_txt),
    re_path(r'^tiles/susc/(?P<model>lw|n10)/(?P<z>\d+)/(?P<x>\d+)/(?P<y>\d+)\.png$',
            susc_tile),
    path('admin/', admin.site.urls),
    path('inventory/', include('inventory.urls')),
    path('files/', include('files.urls')),
    path('', include('pages.urls')),
]
