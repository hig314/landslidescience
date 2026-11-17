from django.urls import path, re_path

from . import network, views

app_name = 'glaciers'

urlpatterns = [
    path('', views.home, name='home'),
    path('pairs/', views.pairs, name='pairs'),
    re_path(r'^data/(?P<name>[A-Za-z0-9_-]+\.(?:json|bin))$', views.tracer_data,
            name='tracer_data'),
    # Retreat histories (glaciers/sources.py)
    re_path(r'^tiles/(?P<z>\d{1,2})/(?P<x>\d{1,6})/(?P<y>\d{1,6})\.pbf$', views.extent_tile,
            name='extent_tile'),
    path('api/sources/', views.api_sources, name='api_sources'),
    path('api/search/', views.api_search, name='api_search'),
    re_path(r'^api/glacier/(?P<gid>RGI2000-v7\.0-G-\d\d-\d{5})/$', views.api_glacier, name='api_glacier'),
    re_path(r'^api/obs/(?P<obs_id>\d{1,12})/$', views.api_obs, name='api_obs'),
    # The flow network and our face lines (glaciers/network.py). The
    # api/scarps/ paths are the landform-trace editor's contract, which is
    # mounted on this table from /glaciers/ with api = '/glaciers/lines/'.
    path('lines/api/scarps/', network.api_lines, name='lines'),
    path('lines/api/scarps/create/', network.api_line_create, name='line_create'),
    re_path(r'^lines/api/scarps/(?P<line_id>\d+)/$', network.api_line_update, name='line_update'),
    path('lines/api/adopt/', network.api_adopt, name='line_adopt'),
    path('lines/api/draft/', network.api_draft_extension, name='line_draft'),
    path('lines/api/import/', network.api_import_faces, name='line_import'),
    path('lines/api/nodes/', network.api_nodes, name='line_nodes'),
    re_path(r'^lines/api/path/(?P<line_id>\d+)/$', network.api_path, name='line_path'),
    re_path(r'^g/(?P<gid>RGI2000-v7\.0-G-\d\d-\d{5})/$', views.glacier_redirect, name='glacier'),
]
