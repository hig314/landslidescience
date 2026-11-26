"""/probability/: site-specific hazard analyses (see projects.py, docs/PROBABILITY.md).

Three routes: the index, a project's page, and a project's data. The gate is
on the DATA as well as the page, for the reason lidar_serve._gate gives: a
page that needs a sign-in over files that do not is a gate in name only.

Where the data is read from, in order:
  1. data/probability/<site>/ on this machine (dev; ProbHaz's
     publish/to_site.py puts it there).
  2. unpublished: the private bucket (drops.r2), key probability/<site>/...,
     fetched by this server and passed on, Range header and all. The droplet
     has no room for a 1.4 GB tile set and the public bucket has no gate.
     Each read is one tile's byte span (tens of kB), so a worker is held only
     briefly.
  3. published: a redirect to the public bucket.
"""
import mimetypes
import re

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import (Http404, HttpResponse, HttpResponseForbidden,
                         HttpResponsePermanentRedirect, HttpResponseRedirect,
                         StreamingHttpResponse)
from django.shortcuts import render
from django.template.loader import select_template

from inventory.auth import can_view_restricted
from landslidescience import lidar_serve

from .projects import PROJECTS

DATA_DIR = settings.BASE_DIR / 'data' / 'probability'
PUBLIC_BASE = getattr(settings, 'PROBABILITY_PUBLIC_BASE', 'https://lidar.landslidescience.org/probability')
_RANGE = re.compile(r'^bytes=\d*-\d*$')
_TYPES = {'.pmtiles': 'application/vnd.pmtiles', '.json': 'application/json', '.geojson': 'application/geo+json'}


def _visible(request, p):
    return p['published'] or can_view_restricted(request.user)


def index(request):
    rows = [dict(p, id=k) for k, p in PROJECTS.items() if _visible(request, p)]
    hidden = len(PROJECTS) - len(rows)
    return render(request, 'probability/index.html',
                  {'projects': rows, 'unpublished_hidden': hidden and not request.user.is_authenticated})


def project(request, site):
    if site != site.lower():
        return HttpResponsePermanentRedirect(f'/probability/{site.lower()}/')
    p = PROJECTS.get(site)
    if p is None:
        raise Http404('no such project')
    if not _visible(request, p):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        return HttpResponseForbidden('This analysis is not published, and this account has no role that may see it.')
    t = select_template([x for x in (p.get('template'), 'probability/base.html') if x])
    return HttpResponse(t.render({'p': p, 'site': site}, request))


def data(request, site, path):
    p = PROJECTS.get(site)
    if p is None:
        raise Http404('no such project')
    if not _visible(request, p):
        return HttpResponseForbidden()
    ctype = _TYPES[path[path.rindex('.'):]]
    # Not public: the answer got past the gate because of who asked.
    cache = 'public, max-age=3600' if p['published'] else 'private, max-age=3600'
    local = DATA_DIR / site / path
    if local.is_file():
        return lidar_serve.serve_ranged(request, local, ctype, cache)
    if p['published']:
        return HttpResponseRedirect(f'{PUBLIC_BASE}/{site}/{path}')
    return _from_private_bucket(request, f'probability/{site}/{path}', ctype, cache)


def _from_private_bucket(request, key, ctype, cache):
    from botocore.exceptions import ClientError
    from drops import r2
    if not r2.configured():
        raise Http404('no local copy and no bucket configured')
    kw = {'Bucket': r2.bucket(), 'Key': key}
    rng = request.headers.get('Range', '').strip()
    if _RANGE.match(rng):
        kw['Range'] = rng
    try:
        o = r2.client().get_object(**kw)
    except ClientError as e:
        code = e.response.get('Error', {}).get('Code')
        if code in ('NoSuchKey', '404', 'NotFound'):
            raise Http404('not uploaded')
        if code == 'InvalidRange':
            return HttpResponse(status=416)
        raise
    resp = StreamingHttpResponse(o['Body'].iter_chunks(256 * 1024),
                                 status=206 if o.get('ContentRange') else 200, content_type=ctype)
    resp['Content-Length'] = str(o['ContentLength'])
    if o.get('ContentRange'):
        resp['Content-Range'] = o['ContentRange']
    if o.get('ETag'):
        resp['ETag'] = o['ETag']
    resp['Accept-Ranges'] = 'bytes'
    resp['Cache-Control'] = cache
    return resp
