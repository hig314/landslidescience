from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path


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


urlpatterns = [
    path('robots.txt', robots_txt),
    path('admin/', admin.site.urls),
    path('inventory/', include('inventory.urls')),
    path('', include('pages.urls')),
]
