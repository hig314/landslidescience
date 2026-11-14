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


# ---- Saved terrain comparisons (/lidar/compare/saved/) ----------------------
# A comparison is its query string; the list keeps the ones the owner chose,
# each under a short hex code. The comparison page posts to compare_save
# (Submit = new code, Update = overwrite the code it was opened from).

import json
import re
import secrets
from urllib.parse import parse_qsl, urlencode

from django.http import Http404, JsonResponse
from django.views.decorators.http import require_POST

from .models import SavedComparison

_CODE_RE = re.compile(r'^[0-9a-f]{3}$')
_DROP_KEYS = {'sv', 'perf'}


def can_save_comparison(user, update=False):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.has_perm('pages.change_savedcomparison' if update else 'pages.add_savedcomparison')


def clean_compare_query(raw):
    """Normalise a comparison's query string: drop sv=/perf=, re-encode."""
    pairs = [(k, v) for k, v in parse_qsl((raw or '').lstrip('?'), keep_blank_values=True)
             if k not in _DROP_KEYS]
    return urlencode(pairs)


def _query_surveys(query):
    out = set()
    for k, v in parse_qsl(query, keep_blank_values=True):
        if k == 'pn':
            for pane in v.split(';'):
                f = pane.split(',')
                if len(f) > 3 and f[3]:
                    out.add(f[3])
    return out


def compare_saved_list(request):
    return render(request, 'pages/lidar_compare_saved.html', {
        'items': SavedComparison.objects.all(),
        'can_edit': can_save_comparison(request.user, update=True),
    })


def compare_short(request, code):
    """/lidar/compare/s/<code>/ → the comparison as last saved, carrying sv=
    so the page offers Update. A link made of the code follows every update."""
    sc = SavedComparison.objects.filter(code=code).first()
    if sc is None:
        raise Http404('No saved comparison with that id')
    q = sc.query + ('&' if sc.query else '') + 'sv=' + sc.code
    return HttpResponseRedirect('/lidar/compare/?' + q)


@require_POST
def compare_save(request):
    try:
        body = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'error': 'bad JSON'}, status=400)
    code = (body.get('code') or '').strip().lower()
    update = bool(code)
    if not can_save_comparison(request.user, update=update):
        return JsonResponse({'error': 'not allowed'}, status=403)
    name = (body.get('name') or '').strip()[:200]
    if not name:
        return JsonResponse({'error': 'give it a name'}, status=400)
    raw = body.get('query') or ''
    if len(raw) > 20000:
        return JsonResponse({'error': 'comparison too long'}, status=400)
    query = clean_compare_query(raw)
    if not query:
        return JsonResponse({'error': 'empty comparison'}, status=400)
    # The list is public: a gated survey's id must not reach it.
    from landslidescience.lidar_serve import gated_ids
    if _query_surveys(query) & set(gated_ids()):
        return JsonResponse({'error': 'a pane names a restricted survey; it cannot go on the public list'},
                            status=400)
    who = request.user.get_username()
    if update:
        if not _CODE_RE.match(code):
            return JsonResponse({'error': 'bad id'}, status=400)
        sc = SavedComparison.objects.filter(code=code).first()
        if sc is None:
            return JsonResponse({'error': f'no saved comparison {code} (deleted?)'}, status=404)
        sc.name, sc.query, sc.saved_by = name, query, who
        sc.save()
    else:
        used = set(SavedComparison.objects.values_list('code', flat=True))
        free = [f'{i:03x}' for i in range(4096) if f'{i:03x}' not in used]
        if not free:
            return JsonResponse({'error': 'all 4096 ids are in use'}, status=409)
        sc = SavedComparison.objects.create(code=secrets.choice(free), name=name, query=query, saved_by=who)
    return JsonResponse({'code': sc.code, 'name': sc.name, 'updated': not not update})
