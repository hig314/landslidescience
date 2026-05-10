from django.conf import settings
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import Page


def home(request):
    page = get_object_or_404(Page, slug='home')
    return render(request, 'pages/page.html', {'page': page})


def tracyarm2025(request):
    if settings.TRACYARM_YOUTUBE_URL and timezone.now() >= settings.TRACYARM_EMBARGO_LIFT:
        return HttpResponseRedirect(settings.TRACYARM_YOUTUBE_URL)
    page = get_object_or_404(Page, slug='tracyarm2025')
    return render(request, 'pages/page.html', {'page': page})
