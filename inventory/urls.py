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
    path('export/', views.export_download, name='export'),
    path('manage/', views.manage_list, name='manage_list'),
    path('manage/settings/', views.manage_settings, name='manage_settings'),
    path('manage/import/', views.manage_import, name='manage_import'),
    path('manage/import/apply/', views.manage_import_apply, name='manage_import_apply'),
    path('manage/<int:landslide_id>/', views.manage_edit, name='manage_edit'),
    # Catchall slug deep-link — must be LAST so named routes resolve first.
    # Trailing slash optional. The regex matches only slug-shaped tokens.
    re_path(r'^(?P<slug>[a-z0-9][a-z0-9-]*)/?$', views.slug_redirect, name='slug_redirect'),
]
