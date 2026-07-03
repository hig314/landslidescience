from django.urls import path, re_path

from . import trace_views, views

app_name = 'inventory'

urlpatterns = [
    path('', views.home, name='home'),
    path('preview/', views.preview_login, name='preview_login'),
    path('methods/', views.methods, name='methods'),
    path('naming/', views.naming, name='naming'),
    path('api/features/', views.api_features, name='api_features'),
    path('api/polygons/', views.api_polygons, name='api_polygons'),
    path('api/provisional/', views.api_provisional, name='api_provisional'),
    path('api/landslide/<int:landslide_id>/', views.api_detail, name='api_detail'),
    path('api/settings/', views.api_settings, name='api_settings'),
    path('api/qms/', views.api_qms_search, name='api_qms_search'),
    path('api/qms/promoted/', views.api_qms_promoted, name='api_qms_promoted'),
    path('api/qms/promote/', views.api_qms_promote, name='api_qms_promote'),
    path('api/qms/<int:qms_id>/', views.api_qms_detail, name='api_qms_detail'),
    path('api/qms/<int:qms_id>/unpromote/', views.api_qms_unpromote, name='api_qms_unpromote'),
    # Trace rasters — editor-uploaded GeoTIFF overlays (all editor-gated,
    # incl. tiles; see inventory/trace_views.py).
    path('api/trace_rasters/', trace_views.trace_list, name='trace_list'),
    path('api/trace_rasters/upload/', trace_views.trace_upload, name='trace_upload'),
    path('api/trace_rasters/<int:raster_id>/status/', trace_views.trace_status,
         name='trace_status'),
    path('api/trace_rasters/<int:raster_id>/rebuild/', trace_views.trace_rebuild,
         name='trace_rebuild'),
    path('api/trace_rasters/<int:raster_id>/delete/', trace_views.trace_delete,
         name='trace_delete'),
    path('api/trace_rasters/<int:raster_id>/link/', trace_views.trace_link,
         name='trace_link'),
    re_path(r'^tiles/trace/(?P<raster_id>\d+)/(?P<z>\d+)/(?P<x>\d+)/(?P<y>\d+)\.png$',
            trace_views.trace_tile, name='trace_tile'),
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
    # The trailing slash on <slug>/ is REQUIRED so that the HTML's
    # `./static/...` relative paths resolve correctly. Without it, the
    # browser treats the slug as a filename and the relative paths
    # resolve one level too shallow. Django's APPEND_SLASH middleware
    # 301-redirects the no-slash form to the slashed form.
    path('archive/', views.snapshot_index, name='snapshot_index'),
    re_path(r'^archive/(?P<slug>[a-z0-9][a-z0-9-]*)/$',
            views.snapshot_serve, name='snapshot_root'),
    re_path(r'^archive/(?P<slug>[a-z0-9][a-z0-9-]*)/(?P<rest>.+)$',
            views.snapshot_serve, name='snapshot_path'),
    path('export/', views.export_download, name='export'),
    # Rules pages — list and detail are public (view-only); only the apply
    # endpoint requires editor permissions.
    path('rules/',               views.manage_rules,        name='rules'),
    path('rules/<str:name>/',    views.manage_rule_detail,  name='rule_detail'),
    path('manage/', views.manage_list, name='manage_list'),
    path('manage/settings/', views.manage_settings, name='manage_settings'),
    path('manage/subsets/', views.manage_subsets, name='manage_subsets'),
    path('manage/subsets/<slug:slug>/', views.manage_subset_edit, name='manage_subset_edit'),
    path('manage/subsets/<slug:slug>/delete/', views.manage_subset_delete, name='manage_subset_delete'),
    path('manage/rules/<str:name>/apply/', views.manage_rule_apply, name='rule_apply'),
    path('manage/import/', views.manage_import, name='manage_import'),
    path('manage/import/apply/', views.manage_import_apply, name='manage_import_apply'),
    path('manage/new/', views.manage_new, name='manage_new'),
    path('manage/draw/stage/', views.manage_draw_stage, name='manage_draw_stage'),
    path('manage/draw/list/', views.manage_draw_list, name='manage_draw_list'),
    path('manage/draw/delete/', views.manage_draw_delete, name='manage_draw_delete'),
    path('manage/draw/preview/', views.manage_draw_preview, name='manage_draw_preview'),
    path('manage/draw/commit/', views.manage_draw_commit, name='manage_draw_commit'),
    path('manage/<int:landslide_id>/', views.manage_edit, name='manage_edit'),
    path('manage/<int:landslide_id>/delete/', views.manage_delete, name='manage_delete'),
    path('manage/<int:landslide_id>/review/', views.manage_review, name='manage_review'),
    path('manage/<int:landslide_id>/polygons/', views.manage_polygons_save,
         name='manage_polygons_save'),
    path('manage/<int:landslide_id>/field/', views.manage_edit_field,
         name='manage_edit_field'),
    path('manage/<int:landslide_id>/fetch_planet/', views.manage_edit_fetch_planet,
         name='manage_edit_fetch_planet'),
    # Catchall slug deep-link — must be LAST so named routes resolve first.
    # Trailing slash optional. The regex matches only slug-shaped tokens.
    re_path(r'^(?P<slug>[a-z0-9][a-z0-9-]*)/?$', views.slug_redirect, name='slug_redirect'),
]
