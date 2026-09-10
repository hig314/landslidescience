from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path, re_path
from django.views.static import serve as static_serve


# Public since the 2026-09 launch. The companion `<meta name="robots">` tag in
# the base templates was relaxed at the same time — leaving it on `noindex`
# while this file says Allow is the classic way to stay invisible and not
# notice, because a crawler that is allowed to fetch a page still obeys the
# page's own noindex.
#
# What stays closed, and why:
#   auth-gated   /admin/, /inventory/manage/, /traffic/ — a crawler only ever
#                sees the login redirect
#   machine-only /inventory/api/, /s/ — JSON and the analytics beacon; nothing
#                to index and the table payload is ~1 MB a fetch
#   expensive    /inventory/export/ (a 4 MB zip), /tiles/, /lidar/,
#                /inventory/planet/ — pyramids are effectively unbounded URL
#                space and would burn crawl budget and bandwidth for nothing
#   unlisted     /files/ — hosted files are reachable only by someone given
#                the link; indexing them would undo that
#   provisional  /glaciers/ — still experimental; not something to surface in
#                search yet
#   duplicate    /inventory/archive/ — frozen snapshots of the same records as
#                the live inventory; indexing them competes with it
_ROBOTS = """# landslidescience.org
User-agent: *
Allow: /

Disallow: /admin/
Disallow: /inventory/manage/
Disallow: /traffic/
Disallow: /inventory/api/
Disallow: /s/
Disallow: /inventory/export/
Disallow: /inventory/preview/
Disallow: /inventory/login/
Disallow: /inventory/logout/
Disallow: /tiles/
Disallow: /lidar/
Disallow: /inventory/planet/
Disallow: /files/
Disallow: /glaciers/
Disallow: /inventory/archive/
"""


def robots_txt(_request):
    return HttpResponse(_ROBOTS, content_type='text/plain')


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


# Self-hosted ITS_LIVE v2 glacier-velocity composite tiles (NASA MEaSUREs
# ITS_LIVE, Alaska RGI01A; CC0). Built by tools/build_itslive_tiles.sh into
# data/itslive_tiles/<var>/<z>/<x>/<y>.png — pre-colored like the susc tiles;
# same serving/caching contract (?v= cache-buster in map.js, bump on rebuild).
_ITSLIVE_TILES_DIR = settings.BASE_DIR / 'data' / 'itslive_tiles'


def itslive_tile(request, var, z, x, y):
    resp = static_serve(request, f'{var}/{z}/{x}/{y}.png',
                         document_root=str(_ITSLIVE_TILES_DIR))
    resp['Cache-Control'] = 'public, max-age=31536000, immutable'
    return resp


# Hugonnet et al. 2021 glacier elevation-change tiles (dh/dt 2000–2019,
# 100 m, CC BY 4.0). Built by tools/build_hugonnet_tiles.sh into
# data/hugonnet_tiles/dhdt/; same contract as the other self-hosted pyramids.
_HUGONNET_TILES_DIR = settings.BASE_DIR / 'data' / 'hugonnet_tiles'


def hugonnet_tile(request, var, z, x, y):
    resp = static_serve(request, f'{var}/{z}/{x}/{y}.png',
                         document_root=str(_HUGONNET_TILES_DIR))
    resp['Cache-Control'] = 'public, max-age=31536000, immutable'
    return resp


# Our own robust fit of the raw ITS_LIVE image pairs (tools/fit_pair_rasters.py
# -> tools/build_fit_tiles.sh), coloured with the SAME ramps as the standard
# ITS_LIVE overlays so a visual difference is a data difference. Experimental,
# and currently only covers the Columbia test box.
# IceBoost v2.0 ice thickness + derived bed / overdeepenings (Maffezzoli et
# al. 2025, doi:10.5194/gmd-18-2545-2025; CC-BY 4.0). Built by
# tools/prep_iceboost_derived.py + build_iceboost_tiles.sh into
# data/iceboost_tiles/<product>/; same contract as the other pyramids.
_ICEBOOST_TILES_DIR = settings.BASE_DIR / 'data' / 'iceboost_tiles'


def iceboost_tile(request, product, z, x, y):
    resp = static_serve(request, f'{product}/{z}/{x}/{y}.png',
                        document_root=str(_ICEBOOST_TILES_DIR))
    resp['Cache-Control'] = 'public, max-age=31536000, immutable'
    return resp


_FIT_TILES_DIR = settings.BASE_DIR / 'data' / 'glaciers' / 'fit_tiles'


def glacierfit_tile(request, var, z, x, y):
    resp = static_serve(request, f'{var}/{z}/{x}/{y}.png',
                         document_root=str(_FIT_TILES_DIR))
    resp['Cache-Control'] = 'public, max-age=31536000, immutable'
    return resp


# Self-hosted Alaska lidar DEMs. Unlike the pyramids above these are NOT
# loose-file XYZ routes: each dataset is one PMTiles archive read by MapLibre
# over HTTP range requests, so they need the range-capable views in
# lidar_serve.py rather than django.views.static.serve (which, as of Django
# 5.2, ignores Range entirely). Built by tools/lidar/build_lidar.py.
from landslidescience import analytics, lidar_serve  # noqa: E402


urlpatterns = [
    path('robots.txt', robots_txt),
    # First-party analytics beacon. The path is deliberately terse and
    # un-Umami-shaped: filter lists match on `umami` and on `/api/send` under
    # a recognisable host, and this site's audience runs blockers. See
    # landslidescience/analytics.py.
    path('s/t.js', analytics.script),
    path('s/api/send', analytics.send),
    path('s/health', analytics.health),
    # Django-authenticated door to the Umami dashboard. Umami's own
    # login is disabled, so this is the only way in.
    path('traffic/', analytics.dashboard, name='traffic'),
    # Public on purpose — see analytics.optout.
    path('traffic/optout/', analytics.optout, name='traffic_optout'),
    path('lidar/', lidar_serve.preview),
    path('lidar/catalog.geojson', lidar_serve.catalog),
    re_path(r'^lidar/pmtiles/(?P<dataset_id>[a-z0-9_]+)\.pmtiles$',
            lidar_serve.pmtiles),
    re_path(r'^lidar/cog/(?P<dataset_id>[a-z0-9_]+)\.tif$',
            lidar_serve.cog),
    re_path(r'^tiles/susc/(?P<model>lw|n10)/(?P<z>\d+)/(?P<x>\d+)/(?P<y>\d+)\.png$',
            susc_tile),
    re_path(r'^tiles/itslive/(?P<var>v|vamp|dvdt)/(?P<z>\d+)/(?P<x>\d+)/(?P<y>\d+)\.png$',
            itslive_tile),
    re_path(r'^tiles/iceboost/(?P<product>thickness|bed|overdeep)/(?P<z>\d+)/(?P<x>\d+)/(?P<y>\d+)\.png$',
            iceboost_tile),
    re_path(r'^tiles/hugonnet/(?P<var>dhdt|dhdt_smooth)/(?P<z>\d+)/(?P<x>\d+)/(?P<y>\d+)\.png$',
            hugonnet_tile),
    re_path(r'^tiles/glacierfit/(?P<var>v0|amp|trend)/(?P<z>\d+)/(?P<x>\d+)/(?P<y>\d+)\.png$',
            glacierfit_tile),
    path('admin/', admin.site.urls),
    path('inventory/', include('inventory.urls')),
    path('glaciers/', include('glaciers.urls')),
    path('files/', include('files.urls')),
    path('', include('pages.urls')),
]
