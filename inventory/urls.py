from django.urls import path, re_path

from . import views

app_name = 'inventory'

urlpatterns = [
    path('', views.home, name='home'),
    path('preview/', views.preview_login, name='preview_login'),
    path('methods/', views.methods, name='methods'),
    path('api/features/', views.api_features, name='api_features'),
    path('api/polygons/', views.api_polygons, name='api_polygons'),
    path('api/landslide/<int:landslide_id>/', views.api_detail, name='api_detail'),
    path('api/settings/', views.api_settings, name='api_settings'),
    path('api/timed_events/', views.api_timed_events, name='api_timed_events'),
    path('api/timeline_events/', views.api_timeline_events, name='api_timeline_events'),
    path('api/survey_circles/',  views.api_survey_circles,  name='api_survey_circles'),
    # Stable serving URL for archived Planet Story MP4s. Snapshot bundles
    # reference this URL — its shape must not change without redirects.
    re_path(r'^planet/(?P<slug>[A-Za-z0-9_-]+)\.mp4$',
            views.serve_planet_mp4, name='planet_mp4'),
    # Snapshot listings + bundles.
    #   /inventory/archive/                  → snapshots index (public list)
    #   /inventory/archive/<slug>/           → snapshot bundle root
    #   /inventory/archive/<slug>/<rest>     → file inside the bundle
    path('archive/', views.snapshot_index, name='snapshot_index'),
    re_path(r'^archive/(?P<slug>[a-z0-9][a-z0-9-]*)/?$',
            views.snapshot_serve, name='snapshot_root'),
    re_path(r'^archive/(?P<slug>[a-z0-9][a-z0-9-]*)/(?P<rest>.*)$',
            views.snapshot_serve, name='snapshot_path'),
    path('export/', views.export_download, name='export'),
    # Rules pages — list and detail are public (view-only); only the apply
    # endpoint requires editor permissions.
    path('rules/',               views.manage_rules,        name='rules'),
    path('rules/<str:name>/',    views.manage_rule_detail,  name='rule_detail'),
    path('manage/', views.manage_list, name='manage_list'),
    path('manage/settings/', views.manage_settings, name='manage_settings'),
    path('manage/rules/<str:name>/apply/', views.manage_rule_apply, name='rule_apply'),
    path('manage/import/', views.manage_import, name='manage_import'),
    path('manage/import/apply/', views.manage_import_apply, name='manage_import_apply'),
    path('manage/<int:landslide_id>/', views.manage_edit, name='manage_edit'),
    path('manage/<int:landslide_id>/fetch_planet/', views.manage_edit_fetch_planet,
         name='manage_edit_fetch_planet'),
    # Catchall slug deep-link — must be LAST so named routes resolve first.
    # Trailing slash optional. The regex matches only slug-shaped tokens.
    re_path(r'^(?P<slug>[a-z0-9][a-z0-9-]*)/?$', views.slug_redirect, name='slug_redirect'),
]
