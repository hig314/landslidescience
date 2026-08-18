from django.urls import path, re_path

from . import views

app_name = 'glaciers'

urlpatterns = [
    path('', views.home, name='home'),
    path('pairs/', views.pairs, name='pairs'),
    re_path(r'^data/(?P<name>[A-Za-z0-9_-]+\.(?:json|bin))$', views.tracer_data,
            name='tracer_data'),
]
