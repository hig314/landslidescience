from django.urls import path, re_path

from . import views
from .models import SLUG_RE

app_name = 'drops'

S = f'(?P<slug>{SLUG_RE})'

urlpatterns = [
    path('', views.index, name='index'),
    re_path(rf'^{S}/$', views.upload, name='upload'),
    re_path(rf'^{S}/toggle/$', views.toggle, name='toggle'),
    re_path(rf'^{S}/files/$', views.files, name='files'),
    re_path(rf'^{S}/files/links\.txt$', views.manifest, name='manifest'),
    # Upload API, called by drops/static/drops/upload.js.
    re_path(rf'^{S}/api/existing/$', views.api_existing),
    re_path(rf'^{S}/api/status/$', views.api_status),
    re_path(rf'^{S}/api/sign/$', views.api_sign),
    re_path(rf'^{S}/api/record/$', views.api_record),
    re_path(rf'^{S}/api/multipart/create/$', views.api_mp_create),
    re_path(rf'^{S}/api/multipart/sign/$', views.api_mp_sign),
    re_path(rf'^{S}/api/multipart/list/$', views.api_mp_list),
    re_path(rf'^{S}/api/multipart/complete/$', views.api_mp_complete),
    re_path(rf'^{S}/api/multipart/abort/$', views.api_mp_abort),
]
