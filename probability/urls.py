from django.urls import path, re_path

from . import views

app_name = 'probability'

urlpatterns = [
    path('', views.index, name='index'),
    # Any case in; views.project redirects to the lowercase address.
    re_path(r'^(?P<site>[A-Za-z0-9_]{1,40})/$', views.project, name='project'),
    re_path(r'^(?P<site>[a-z0-9_]{1,40})/data/(?P<path>(?:tiles|inventory|zonal)/[a-z0-9_]{1,60}\.(?:pmtiles|json|geojson))$',
            views.data, name='data'),
]
