from django.urls import re_path

from . import views

# Mounted under /files/ from the root URLconf. The name token is filename-safe
# (letters, numbers, dot, dash, underscore) so no path traversal is possible.
urlpatterns = [
    re_path(r'^(?P<name>[A-Za-z0-9._-]+)$', views.serve, name='hosted_file'),
]
