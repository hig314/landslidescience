from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('tracyarm2025/', views.tracyarm2025, name='tracyarm2025'),
]
