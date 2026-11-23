"""Published talks at /decks/<name>/ (docs/DECKS.md).

A deck is a folder built elsewhere (the Presentation project's
tools/publish.py) and copied into data/decks/<name>/ as it is:

    site/          everything a browser may fetch, relative-addressed
    private/       notes.json, the presenter's notes
    publish.json   {"public": bool, "title", "files": [[path, bytes, sha1], ...]}

This module only serves it. Three rules carry the weight:

  * Only what publish.json lists (plus sw.js) is served, always from site/.
    That is the traversal guard, and it is what keeps private/ and
    publish.json itself unreachable.
  * A deck whose publish.json does not say "public": true is a DRAFT: every
    address under it answers the owner only, and 404 (not 403) to anyone
    else, so a draft's name confirms nothing.
  * notes.json answers the owner only, whatever the deck's state.

Owner means superuser, the same rule as owner-only lidar (lidar_serve._gate).
"""
import html
import json
import re
from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.views.decorators.http import require_safe

from .lidar_serve import serve_ranged

DECKS = Path(settings.BASE_DIR) / 'data' / 'decks'
_NAME_RE = re.compile(r'^[A-Za-z0-9_-]+$')
# Set here, not guessed: the container's mimetypes table differs from a Mac's,
# and a service worker or a module served with the wrong type does not run.
_TYPES = {
    '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8', '.json': 'application/json',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.gif': 'image/gif', '.webp': 'image/webp', '.svg': 'image/svg+xml',
    '.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mov': 'video/quicktime',
    '.webm': 'video/webm', '.ttf': 'font/ttf', '.woff2': 'font/woff2',
    '.txt': 'text/plain; charset=utf-8', '.pdf': 'application/pdf',
}
_manifests = {}     # name -> (mtime_ns, manifest); a re-sync is picked up at once


def _is_owner(user):
    return bool(user.is_authenticated and user.is_superuser)


def _manifest(name):
    """publish.json of a deck, with 'paths' (the servable set), or None."""
    if not _NAME_RE.match(name):
        return None
    path = DECKS / name / 'publish.json'
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    hit = _manifests.get(name)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        man = json.loads(path.read_text())
        man['paths'] = frozenset(f[0] for f in man['files']) | {'sw.js'}
        man['public'] = man.get('public') is True      # anything else is a draft
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None
    _manifests[name] = (mtime, man)
    return man


def _visible(request, man):
    return bool(man) and (man['public'] or _is_owner(request.user))


@require_safe
def index(request):
    """The decks this visitor may open: public ones, and drafts for the owner."""
    rows = []
    for d in sorted(DECKS.iterdir()) if DECKS.is_dir() else []:
        man = _manifest(d.name)
        if not _visible(request, man):
            continue
        rows.append('<li><a href="/decks/%s/">%s</a> <span>%s slides, %s%s</span></li>' % (
            d.name, html.escape(str(man.get('title') or d.name)), man.get('slides', '?'),
            html.escape(str(man.get('built', ''))[:10]), '' if man['public'] else ', draft: only you can open it'))
    body = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Talks | landslidescience.org</title>'
            '<style>body{margin:60px auto;max-width:640px;padding:0 20px;font:16px/1.5 -apple-system,Helvetica,Arial,sans-serif;color:#222}'
            'a{color:#1a5fb4;font-weight:600;text-decoration:none}li{margin:8px 0}span{color:#777;font-size:13px;margin-left:8px}</style>'
            '<h1>Talks</h1><ul>%s</ul>%s<p><a href="/">landslidescience.org</a></p></html>'
            % (''.join(rows), '' if rows else '<p>None published yet.</p>'))
    resp = HttpResponse(body)
    resp['Cache-Control'] = 'private, no-cache'         # the list depends on who asks
    return resp


def slash(request, name):
    """/decks/<name> -> /decks/<name>/, whether or not such a deck exists (so
    the redirect says nothing about drafts). The slash is load-bearing: every
    address in a deck is relative, and its service worker's scope is the folder."""
    return HttpResponseRedirect('/decks/%s/' % name)


@require_safe
def serve(request, name, rest=''):
    man = _manifest(name)
    if not _visible(request, man):
        raise Http404(name)
    rest = rest or 'index.html'
    if rest == 'notes.json':
        notes = DECKS / name / 'private' / 'notes.json'
        if not _is_owner(request.user) or not notes.is_file():
            raise Http404(rest)
        resp = HttpResponse(notes.read_bytes(), content_type='application/json')
        resp['Cache-Control'] = 'private, no-store'
        return resp
    if rest not in man['paths']:
        raise Http404(rest)
    path = DECKS / name / 'site' / rest
    ctype = _TYPES.get(path.suffix.lower(), 'application/octet-stream')
    # Files are not content-named, so every one revalidates (cheap: an ETag
    # and a 304); the deck's own service worker makes repeat requests rare.
    resp = serve_ranged(request, path, ctype,
                        'no-cache' if man['public'] else 'private, no-cache')
    # serve_ranged is written for open data; a deck is same-origin only.
    for h in ('Access-Control-Allow-Origin', 'Access-Control-Expose-Headers'):
        if h in resp:
            del resp[h]
    # A deck frames its own figures (site/live/...): the middleware's DENY
    # would blank them. Set here, the middleware leaves it alone.
    resp['X-Frame-Options'] = 'SAMEORIGIN'
    return resp
