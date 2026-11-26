"""Talks at /decks/<name>/: stored, shown and edited here (docs/DECKS.md).

A deck is one folder, data/decks/<name>/:

    deck.json      the deck: slides, elements, presenter notes
    meta.json      {"owner", "state": "draft" | "published", "created", "published",
                    "copy_of", "revised": [...]}
    assets/ media/ live/ vendor/      the files its slides use
    history/       earlier deck.json files (one kept per ten minutes of editing)

It gets here once, by rsync from the Presentation project (tools/site.py push,
the end of a Keynote import), and from then on this folder is the deck. The
runtime that draws and edits it (landslidescience/decks_rt/, a copy of that
project's lib/, fonts/ and reveal.js) is served to every deck at /decks/_rt/.

The life of a deck, and what this module enforces:

  draft       Its owner's alone: every address under it answers the owner and
              is 404 (not 403) to anyone else, so a draft's name confirms
              nothing. The owner edits it in place (E): PUT deck.json.
  published   Public, and CONSTANT: nothing writes to it. People cite it, and a
              recording may be synchronised to it.
  revising    Copy a published deck (a new draft), edit the copy, then either
              publish the copy as a deck of its own, or REPLACE the original
              with it. Replacing changes what the public was shown, so it
              takes a typed confirmation and is recorded in meta["revised"].

Presenter notes never leave the server except to the owner: deck.json is sent
without them, and notes.json is owner-only.

Owner: the account named in meta.json, or any superuser. Only superusers
create decks. Every write is CSRF-checked (the editor sends X-CSRFToken).
"""
import hashlib
import html
import json
import os
import re
import logging
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.http import (Http404, HttpResponse, HttpResponseNotAllowed,
                         HttpResponseRedirect, JsonResponse)
from django.middleware.csrf import get_token
from django.views.decorators.http import require_POST, require_safe

from .lidar_serve import serve_ranged

DECKS = Path(settings.BASE_DIR) / 'data' / 'decks'
RT = Path(__file__).resolve().parent / 'decks_rt'
_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]*$')
_FILE_RE = re.compile(r'^(assets|media)/[^/\\]+$')
_UPLOAD_RE = re.compile(r'^assets/[A-Za-z0-9._-]+$')
_DIRS = ('assets', 'media', 'live', 'vendor')        # what a browser may fetch
_MAX_DECK = 8 * 1024 * 1024
_MAX_UPLOAD = 50 * 1024 * 1024       # an image dropped on a slide; movies come by rsync
# Set here, not guessed: the container's mimetypes table differs from a Mac's,
# and a service worker served with the wrong type does not run.
_TYPES = {
    '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8', '.json': 'application/json',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.gif': 'image/gif', '.webp': 'image/webp', '.svg': 'image/svg+xml',
    '.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mov': 'video/quicktime',
    '.webm': 'video/webm', '.ttf': 'font/ttf', '.woff2': 'font/woff2',
    '.txt': 'text/plain; charset=utf-8', '.pdf': 'application/pdf',
}
_cache = {}      # (name, file) -> (mtime_ns, parsed); per worker, re-read when the file changes
log = logging.getLogger(__name__)
# Display copies (see _display). A slide is 1920 px wide; a picture is sent at
# twice the width it is shown at (a 4K projector, a laptop's dense screen),
# times the slide's zoom, rounded up to one of these. Wider than the last: the
# original.
_BUCKETS = (640, 1280, 1920, 2560, 3840)
_DISPLAY = '.display'                 # in the deck's folder; never served by name, rebuilt when missing
_DISPLAY_TYPES = ('.png', '.jpg', '.jpeg', '.webp')
_FOREVER = 'max-age=31536000, immutable'
_display_lock = threading.Lock()      # one picture is decoded at a time: a camera original is ~150 MB of memory


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def _json(name, fname):
    """decks/<name>/<fname> parsed, or None. Cached on the file's mtime."""
    if not _NAME_RE.match(name or ''):
        return None
    path = DECKS / name / fname
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    hit = _cache.get((name, fname))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    _cache[(name, fname)] = (mtime, data)
    return data


def _meta(name):
    """meta.json, or None when there is no such deck. Anything but an explicit
    "published" is a draft: a damaged file fails closed."""
    meta = _json(name, 'meta.json')
    if meta is None or not (DECKS / name / 'deck.json').is_file():
        return None
    return meta


def _published(meta):
    return meta.get('state') == 'published'


def _is_owner(user, meta):
    return bool(user.is_authenticated and (user.is_superuser or user.get_username() == meta.get('owner')))


def _visible(request, meta):
    return meta is not None and (_published(meta) or _is_owner(request.user, meta))


def _write_json(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def _link_tree(src, dst):
    """Copy a deck's files as hard links (a deck's movies are large, and its
    files are never changed in place: an upload replaces the directory entry)."""
    for d in _DIRS:
        for root, _dirs, files in os.walk(src / d):
            for f in files:
                a = Path(root) / f
                b = dst / a.relative_to(src)
                if b.exists():
                    if b.stat().st_size == a.stat().st_size and b.stat().st_mtime_ns == a.stat().st_mtime_ns:
                        continue
                    b.unlink()
                b.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(a, b)
                except OSError:
                    shutil.copy2(a, b)


def _snapshot(folder, tag='', every=600):
    """Keep the current deck.json in history/ (at most one per `every` seconds,
    200 in all), so an edit can be walked back by hand."""
    cur, hist = folder / 'deck.json', folder / 'history'
    if not cur.is_file():
        return
    hist.mkdir(exist_ok=True)
    old = sorted(hist.glob('deck-*.json'), key=lambda p: p.stat().st_mtime)
    if every and old and time.time() - old[-1].stat().st_mtime < every:
        return
    keep = hist / ('deck-%s%s.json' % (time.strftime('%Y%m%d-%H%M%S'), tag))
    shutil.copyfile(cur, keep)                        # its mtime is now: when it was set aside
    for p in old[:max(0, len(old) + 1 - 200)]:
        p.unlink()


def _trash(folder, why):
    trash = DECKS / '.trash'
    trash.mkdir(exist_ok=True)
    os.replace(folder, trash / ('%s-%s-%s' % (folder.name, why, time.strftime('%Y%m%d-%H%M%S'))))


# ---------------------------------------------------------------------------
# The list, and what an owner can do from it
# ---------------------------------------------------------------------------
_PAGE = '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>Talks | landslidescience.org</title>
<style>
body{margin:50px auto;max-width:760px;padding:0 20px;font:16px/1.5 -apple-system,Helvetica,Arial,sans-serif;color:#222}
a{color:#1a5fb4;text-decoration:none} h1{font-size:24px} .deck{border-top:1px solid #ddd;padding:14px 0}
.deck>a{font-weight:600;font-size:18px} .st{color:#777;font-size:13px;margin-left:8px} .st b{color:#b35900}
form{display:inline-block;margin:6px 10px 0 0} input{font:inherit;padding:3px 7px;border:1px solid #aaa;border-radius:4px;width:11em}
button{font:inherit;padding:3px 11px;border:1px solid #888;border-radius:4px;background:#f3f3f3;cursor:pointer}
details{margin-top:8px} summary{color:#a00;cursor:pointer;font-size:14px} .danger{border:1px solid #d99;border-radius:6px;padding:8px 12px;margin-top:6px;font-size:14px}
.danger button{border-color:#a00;color:#a00} .msg{background:#eef6ff;border:1px solid #b6d4f5;border-radius:6px;padding:8px 12px}
.msg.bad{background:#fff0f0;border-color:#e0a0a0} p.small{color:#666;font-size:13px}
</style></head><body><h1>Talks</h1>%s%s%s<p class="small"><a href="/">landslidescience.org</a></p>
<script>var d = location.hash && document.getElementById(location.hash.slice(1)); if (d && d.tagName === "DETAILS") { d.open = true; d.scrollIntoView(); }</script>
</body></html>'''
_NAME_INPUT = '<input name="new" placeholder="name for the copy" pattern="[A-Za-z0-9][A-Za-z0-9_-]*" required> '
_CONFIRM = '<input name="confirm" autocomplete="off" required> '


@require_safe
def index(request):
    """Published decks for anyone; for an owner, their drafts and the actions."""
    csrf = None
    rows = []
    names = sorted(d.name for d in DECKS.iterdir()) if DECKS.is_dir() else []
    for name in names:
        meta = _meta(name)
        if not _visible(request, meta):
            continue
        deck = _json(name, 'deck.json') or {}
        owner = _is_owner(request.user, meta)
        pub = _published(meta)
        state = 'published %s' % html.escape(str(meta.get('published', ''))[:10]) if pub else '<b>draft</b>: only you can open it'
        if meta.get('copy_of') and not pub:
            state += ', a copy of %s' % html.escape(str(meta['copy_of']))
        if pub and meta.get('revised'):
            state += ', revised %s' % html.escape(str(meta['revised'][-1].get('at', ''))[:10])
        row = '<div class="deck"><a href="/decks/%s/">%s</a><span class="st">%s, %d slides%s</span>' % (
            name, html.escape(str(deck.get('title') or name)), name, len(deck.get('slides', [])),
            (', ' + state) if owner or not pub else '')
        if owner:
            csrf = csrf or get_token(request)
            tok = '<input type="hidden" name="csrfmiddlewaretoken" value="%s"><input type="hidden" name="name" value="%s">' % (csrf, name)

            def form(action, label, extra='', tok=tok):
                return ('<form method="post" action="/decks/_do"><input type="hidden" name="action" value="%s">%s%s<button>%s</button></form>'
                        % (action, tok, extra, label))
            row += '<div>'
            if pub:
                row += form('copy', 'Copy to revise', _NAME_INPUT)
                row += '<p class="small">A published deck is constant. To change it, revise a copy.</p>'
            else:
                row += form('publish', 'Publish') + form('copy', 'Copy', _NAME_INPUT)
                orig = _meta(meta.get('copy_of') or '')
                danger = ''
                if orig and _published(orig) and _is_owner(request.user, orig):
                    o = html.escape(meta['copy_of'])
                    danger += ('<div class="danger"><b>Replace the published deck “%s” with this one.</b> Its address stays the same and '
                               'its slides change for everyone, including people who cite it and any recording made against it. '
                               'Meant for small corrections (a misspelled name, an attribution). To publish this as a separate deck, '
                               'use Publish instead. Type <b>%s</b> to confirm:<br>%s</div>') % (
                                   o, o, form('replace', 'Replace %s' % o, _CONFIRM))
                danger += ('<div class="danger"><b>Discard this draft.</b> Type <b>%s</b> to confirm:<br>%s</div>'
                           % (name, form('discard', 'Discard', _CONFIRM)))
                row += '<details id="danger-%s"><summary>Danger zone</summary>%s</details>' % (name, danger)
            row += '</div>'
        rows.append(row + '</div>')
    msg = request.GET.get('msg')
    note = '<p class="msg%s">%s</p>' % (' bad' if request.GET.get('bad') else '', html.escape(msg)) if msg else ''
    new = ''
    if request.user.is_authenticated and request.user.is_superuser:
        csrf = csrf or get_token(request)
        new = ('<div class="deck"><form method="post" action="/decks/_do"><input type="hidden" name="action" value="new">'
               '<input type="hidden" name="csrfmiddlewaretoken" value="%s"><input name="name" placeholder="new_deck_name" '
               'pattern="[A-Za-z0-9][A-Za-z0-9_-]*" required> <button>New empty deck</button></form>'
               '<p class="small">Press E in a draft to edit it. A deck made in Keynote arrives through the import on the Mac.</p></div>' % csrf)
    resp = HttpResponse(_PAGE % (note, ''.join(rows) or '<p>None published yet.</p>', new))
    resp['Cache-Control'] = 'private, no-store'          # the list depends on who asks
    return resp


def _back(msg, bad=False):
    return HttpResponseRedirect('/decks/?msg=%s%s' % (quote(msg), '&bad=1' if bad else ''))


@require_POST
def do(request):
    """The owner's actions from the list. CSRF-checked by the middleware."""
    action, name = request.POST.get('action'), request.POST.get('name', '')
    user = request.user
    if action == 'new':
        if not (user.is_authenticated and user.is_superuser):
            raise Http404()
        if not _NAME_RE.match(name) or (DECKS / name).exists():
            return _back('That name is taken or not usable (letters, digits, - and _).', True)
        folder = DECKS / name
        (folder / 'assets').mkdir(parents=True)
        _write_json(folder / 'deck.json', {'title': name, 'width': 1920, 'height': 1080, 'slides': [
            {'bg': '#000000', 'use': 'elements', 'elements': []}]})
        _write_json(folder / 'meta.json', {'owner': user.get_username(), 'state': 'draft', 'created': _now()})
        return HttpResponseRedirect('/decks/%s/' % name)

    meta = _meta(name)
    if meta is None or not _is_owner(user, meta):
        raise Http404()                                   # as for any address of a deck that is not yours
    folder = DECKS / name
    if action == 'publish':
        if _published(meta):
            return _back('%s is already published.' % name, True)
        _write_json(folder / 'meta.json', dict(meta, state='published', published=_now()))
        return _back('%s is published: anyone can open /decks/%s/.' % (name, name))
    if action == 'copy':
        new = request.POST.get('new', '')
        if not _NAME_RE.match(new) or (DECKS / new).exists():
            return _back('That name for the copy is taken or not usable (letters, digits, - and _).', True)
        dst = DECKS / new
        dst.mkdir(parents=True)
        _link_tree(folder, dst)
        shutil.copy2(folder / 'deck.json', dst / 'deck.json')
        _write_json(dst / 'meta.json', {'owner': user.get_username(), 'state': 'draft', 'created': _now(), 'copy_of': name})
        return _back('%s is a draft copy of %s. Edit it, then publish it as its own deck%s.' % (
            new, name, ', or replace %s with it' % name if _published(meta) else ''))
    if _published(meta):
        return _back('%s is published and is not changed in place.' % name, True)
    if action == 'discard':
        if request.POST.get('confirm') != name:
            return _back('Not discarded: the name typed did not match.', True)
        _trash(folder, 'discarded')
        return _back('%s was discarded (kept in data/decks/.trash on the server).' % name)
    if action == 'replace':
        target = meta.get('copy_of') or ''
        orig = _meta(target)
        if orig is None or not _published(orig) or not _is_owner(user, orig):
            return _back('There is no published deck for %s to replace.' % name, True)
        if request.POST.get('confirm') != target:
            return _back('Not replaced: the name typed did not match.', True)
        dst = DECKS / target
        _snapshot(dst, tag='-replaced', every=0)          # the deck the public had, kept
        _link_tree(folder, dst)
        shutil.copyfile(folder / 'deck.json', dst / 'deck.json.tmp')
        os.replace(dst / 'deck.json.tmp', dst / 'deck.json')
        _write_json(dst / 'meta.json', dict(orig, revised=list(orig.get('revised', [])) + [
            {'at': _now(), 'from': name, 'by': user.get_username()}]))
        _trash(folder, 'merged')
        return _back('%s now shows the revised slides. The version it replaced is kept in its history on the server.' % target)
    raise Http404()


def slash(request, name):
    """/decks/<name> -> /decks/<name>/, whether or not such a deck exists (so
    the redirect says nothing about drafts). The slash is load-bearing: every
    address in a deck is relative, and its service worker's scope is the folder."""
    return HttpResponseRedirect('/decks/%s/' % name)


# ---------------------------------------------------------------------------
# The runtime, shared by every deck
# ---------------------------------------------------------------------------
def _plain(resp):
    # serve_ranged is written for open data; decks are same-origin only.
    for h in ('Access-Control-Allow-Origin', 'Access-Control-Expose-Headers'):
        if h in resp:
            del resp[h]
    return resp


@require_safe
def runtime(request, rest):
    path = (RT / rest).resolve()
    if RT not in path.parents or not path.is_file():
        raise Http404(rest)
    resp = _plain(serve_ranged(request, path, _TYPES.get(path.suffix.lower(), 'application/octet-stream'), 'no-cache'))
    if rest == 'lib/mapsw.js':
        # The worker that keeps what a deck's live maps fetch (docs/DECKS.md,
        # "Practice"). The deck page registers it over the map pages' folders
        # (/inventory/, /lidar/compare/), which lie outside /decks/_rt/: this
        # header is what allows that. It acts only on frames with ?embed=1.
        resp['Service-Worker-Allowed'] = '/'
    return resp


# ---------------------------------------------------------------------------
# One deck
# ---------------------------------------------------------------------------
_SHELL = '''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">%s
  <title>%s</title>
  <link rel="stylesheet" href="/decks/_rt/vendor/reveal.js/dist/reveal.css">
  <link rel="stylesheet" href="/decks/_rt/fonts/fonts.css">
  <link rel="stylesheet" href="/decks/_rt/lib/deck.css">
</head>
<body>
<div class="reveal"><div class="slides"></div></div>%s
<script src="/decks/_rt/vendor/reveal.js/dist/reveal.js"></script>
<script src="/decks/_rt/lib/deck.js"></script>
</body>
</html>
'''
_RT_FILES = ('lib/deck.js', 'lib/deck.css', 'lib/offline.js', 'vendor/reveal.js/dist/reveal.js',
             'vendor/reveal.js/dist/reveal.css', 'fonts/fonts.css')


def _tag(path):
    st = path.stat()
    return [st.st_size, '%x-%x' % (st.st_mtime_ns, st.st_size)]


def _wanted(deck):
    """{path: width bucket} for the pictures and movies a deck's slides name
    (0: the file as it is). A picture's bucket is the widest it is shown
    anywhere in the deck, so one copy serves every use."""
    need = {}
    for s in deck.get('slides', []):
        pz = s.get('panzoom') or {}
        zoom = 1.0
        for k in ('from', 'to'):
            try:
                zoom = max(zoom, float((pz.get(k) or [1])[0] or 1))
            except (TypeError, ValueError, IndexError):
                pass
        for e in s.get('elements', []):
            try:
                shown = float((e.get('fit') or [0, 0, e.get('w')])[2] or 0)
            except (TypeError, ValueError, IndexError):
                shown = 0
            for key in ('src', 'fallback'):
                p = e.get(key)
                if not isinstance(p, str) or not _FILE_RE.match(p):
                    continue
                if e.get('type') == 'video' and key == 'src':
                    need[p] = None
                elif need.get(p, 0) is not None:
                    need[p] = max(need.get(p, 0), shown * 2 * zoom)
    out = {}
    for p, px in need.items():
        out[p] = 0
        if px and p.rsplit('.', 1)[-1].lower() in [t[1:] for t in _DISPLAY_TYPES]:
            out[p] = next((b for b in _BUCKETS if b >= px), 0)
    return out


def _display(folder, rel, bucket):
    """The display copy of picture `rel` at most `bucket` px wide: a WebP made
    from the original, which is never touched. None when the original is the
    thing to send (not a still picture, or the copy would not be smaller).
    Made on first request and kept in .display/, named for the original's size
    and time, so a replaced original gets a new copy."""
    src = folder / rel
    if bucket not in _BUCKETS or src.suffix.lower() not in _DISPLAY_TYPES:
        return None
    try:
        tag = _tag(src)[1]
    except OSError:
        return None
    out = folder / _DISPLAY / ('%s.%s.%d.webp' % (src.name, tag, bucket))
    keep = out.with_suffix('.orig')                   # marks "send the original"
    if out.is_file():
        return out
    if keep.exists():
        return None
    with _display_lock:
        if out.is_file():
            return out
        if keep.exists():
            return None
        try:
            from PIL import Image, ImageOps
            out.parent.mkdir(exist_ok=True)
            for old in out.parent.glob(src.name + '.*'):          # copies of an earlier original of this name
                if not old.name.startswith('%s.%s.' % (src.name, tag)):
                    old.unlink()
            with Image.open(src) as im:
                if getattr(im, 'is_animated', False):
                    raise ValueError('animated')
                icc = im.info.get('icc_profile')
                im = ImageOps.exif_transpose(im)      # a browser turns a camera JPEG by its EXIF; the copy has none
                if im.mode in ('RGBA', 'LA', 'PA') or 'transparency' in im.info:
                    im = im.convert('RGBA')
                    if im.getextrema()[3][0] == 255:  # an alpha channel that hides nothing
                        im = im.convert('RGB')
                elif im.mode != 'RGB':
                    im = im.convert('RGB')
                if im.width > bucket:
                    im = im.resize((bucket, max(1, round(im.height * bucket / im.width))),
                                   Image.LANCZOS, reducing_gap=3.0)
                tmp = out.with_suffix('.tmp')
                im.save(tmp, 'WEBP', quality=90, method=4, **({'icc_profile': icc} if icc else {}))
            if tmp.stat().st_size >= 0.9 * src.stat().st_size:
                tmp.unlink()
                raise ValueError('no smaller')
            os.replace(tmp, out)
            return out
        except Exception as e:                        # whatever is wrong with it, the original still shows
            if not isinstance(e, ValueError):
                log.warning('display copy of %s/%s failed: %s', folder.name, rel, e)
            try:
                keep.write_text(str(e))
            except OSError:
                pass
            return None


def _queries(name, deck):
    """{path: "v=<tag>&w=<bucket>"}: how the page asks for each picture and
    movie. v names the file's version, so the answer can be kept for good; w
    asks for the display copy."""
    folder = DECKS / name
    out = {}
    for p, b in _wanted(deck).items():
        try:
            tag = _tag(folder / p)[1]
        except OSError:
            continue
        out[p] = 'v=%s' % tag + ('&w=%d' % b if b else '')
    return out


def _files(name, deck):
    """Everything a browser needs to show this deck with no connection:
    [[path, bytes, tag, query], ...] for the service worker. Paths are
    relative to the deck, or from the site root for the shared runtime.
    Pictures and movies: only those a slide names, as the page asks for them
    (the display copy, where there is one), so what a slide no longer uses is
    not downloaded."""
    folder = DECKS / name
    out = [['index.html', 2000, None], ['deck.json', (folder / 'deck.json').stat().st_size, None]]
    wanted = _wanted(deck)
    for p, q in sorted(_queries(name, deck).items()):
        size, tag = _tag(folder / p)
        b = wanted.get(p) or 0
        if b:
            made = folder / _DISPLAY / ('%s.%s.%d.webp' % (Path(p).name, tag, b))
            if made.is_file():
                size = made.stat().st_size
            elif not made.with_suffix('.orig').exists():
                size = min(size, 600000)              # not made yet: a guess, for the progress figure only
        out.append([p, size, q, q])
    for d in ('live', 'vendor'):
        for root, _dirs, files in os.walk(folder / d):
            for f in sorted(files):
                p = Path(root) / f
                if not f.startswith('.'):
                    out.append([p.relative_to(folder).as_posix()] + _tag(p))
    rt = list(_RT_FILES)
    used = set()
    for s in deck.get('slides', []):
        for e in s.get('elements', []):
            if e.get('type') == 'text':
                used.update(f for f in [e.get('font')] + [r.get('font') for r in e.get('runs') or []] if f)
    try:
        for line in (RT / 'fonts' / 'fonts.css').read_text().splitlines():
            fam, url = re.search(r'font-family: "([^"]+)"', line), re.search(r'url\(([^)]+)\)', line)
            if fam and url and fam.group(1) in used and 'fonts/' + url.group(1) not in rt:
                rt.append('fonts/' + url.group(1))
    except OSError:
        pass
    for r in rt:
        if (RT / r).is_file():
            out.append(['/decks/_rt/' + r] + _tag(RT / r))
    return out


def _deck_get(name, meta, owner):
    """deck.json as this visitor gets it: never with notes (they travel in
    notes.json, to the owner), and with "site" telling the page what it is."""
    deck = _json(name, 'deck.json')
    if deck is None:
        raise Http404(name)
    out = {k: v for k, v in deck.items() if k not in ('slides', 'site', 'published')}
    out['site'] = {'name': name, 'state': 'published' if _published(meta) else 'draft',
                   'owner': owner, 'edit': owner and not _published(meta)}
    if owner and meta.get('copy_of') and not _published(meta):
        orig = _meta(meta['copy_of'])
        if orig and _published(orig):                  # the menu offers "Replace ..." for this
            out['site']['copy_of'] = meta['copy_of']
    out['site']['files'] = _queries(name, deck)
    out['slides'] = [{k: v for k, v in s.items() if k != 'notes'} for s in deck.get('slides', [])]
    resp = JsonResponse(out, json_dumps_params={'ensure_ascii': False, 'separators': (',', ':')})
    resp['X-Mtime'] = str((DECKS / name / 'deck.json').stat().st_mtime_ns)
    return resp


def _deck_put(request, name):
    if int(request.headers.get('Content-Length') or 0) > _MAX_DECK:
        return HttpResponse(status=413)
    body = request.read(_MAX_DECK + 1)
    try:
        deck = json.loads(body)
        assert isinstance(deck, dict) and isinstance(deck.get('slides'), list) and deck['slides']
        assert all(isinstance(s, dict) for s in deck['slides'])
    except (ValueError, AssertionError):
        return HttpResponse('Not a deck', status=400)
    folder = DECKS / name
    target = folder / 'deck.json'
    base = request.headers.get('X-Base-Mtime')
    if base and str(target.stat().st_mtime_ns) != base:
        return HttpResponse('The deck was changed elsewhere since this page loaded it.', status=409)
    if 'site' in deck or 'published' in deck:              # what the server adds on the way out
        deck.pop('site', None)
        deck.pop('published', None)
        body = json.dumps(deck, ensure_ascii=False).encode()
    if base:
        _snapshot(folder)
    else:                                              # the editor's "keep this page's version" over one saved elsewhere:
        _snapshot(folder, '-replaced', every=0)        # that one is always set aside
    tmp = folder / 'deck.json.tmp'
    tmp.write_bytes(body)
    os.replace(tmp, target)
    # How to ask for each picture now: one that was made larger gets a larger display copy.
    resp = JsonResponse({'files': _queries(name, deck)})
    resp['X-Mtime'] = str(target.stat().st_mtime_ns)
    return resp


def _upload(request, name, rest):
    """PUT assets/<file>: an image dropped on a slide."""
    if int(request.headers.get('Content-Length') or 0) > _MAX_UPLOAD:
        return HttpResponse(status=413)
    target = DECKS / name / rest
    target.parent.mkdir(exist_ok=True)
    tmp = target.with_name(target.name + '.tmp')
    left = _MAX_UPLOAD
    with open(tmp, 'wb') as f:
        while left >= 0:
            chunk = request.read(1 << 20)
            if not chunk:
                break
            left -= len(chunk)
            f.write(chunk)
    if left < 0:
        tmp.unlink()
        return HttpResponse(status=413)
    os.replace(tmp, target)
    return HttpResponse(status=204)


def _copy_in(request, name):
    """POST copy {"from": deck, "files": [...]}: slides or elements pasted from
    another deck bring their files. A name already taken by different content
    gets a new name; the reply maps old to new."""
    try:
        req = json.loads(request.read(1 << 20))
        src, files = req['from'], req['files']
        assert all(isinstance(f, str) and _FILE_RE.match(f) and not f.split('/')[1].startswith('.') for f in files)
    except (ValueError, KeyError, AssertionError, TypeError):
        return HttpResponse(status=400)
    if not isinstance(src, str) or not _visible(request, _meta(src)):
        raise Http404()

    def digest(p):
        h = hashlib.sha1()
        with open(p, 'rb') as fh:
            for block in iter(lambda: fh.read(1 << 20), b''):
                h.update(block)
        return h.digest()

    out = {}
    for f in dict.fromkeys(files):
        a = DECKS / src / f
        if not a.is_file():
            continue
        stem, ext = os.path.splitext(f)
        for cand in [f, '%s-%s%s' % (stem, src, ext)] + ['%s-%s-%d%s' % (stem, src, n, ext) for n in range(2, 50)]:
            b = DECKS / name / cand
            if not b.exists():
                b.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(a, b)
                except OSError:
                    shutil.copy2(a, b)
                out[f] = cand
                break
            if a.stat().st_size == b.stat().st_size and digest(a) == digest(b):
                out[f] = cand
                break
    return JsonResponse({'map': out})


def serve(request, name, rest=''):
    meta = _meta(name)
    if not _visible(request, meta):
        raise Http404(name)
    owner = _is_owner(request.user, meta)
    pub = _published(meta)
    cache = 'no-cache' if pub and not owner else 'private, no-cache'

    if request.method not in ('GET', 'HEAD'):
        # Writes: the owner's, on a draft, and nothing else.
        if not owner:
            raise Http404(name)
        if pub:
            return HttpResponse('A published deck is not changed in place.', status=403)
        if request.method == 'PUT' and rest == 'deck.json':
            return _deck_put(request, name)
        if request.method == 'PUT' and _UPLOAD_RE.match(rest):
            return _upload(request, name, rest)
        if request.method == 'POST' and rest == 'copy':
            return _copy_in(request, name)
        return HttpResponseNotAllowed(['GET', 'HEAD'])

    if rest in ('', 'index.html'):
        deck = _json(name, 'deck.json') or {}
        head = '' if pub else '\n  <meta name="robots" content="noindex">'
        tail = '\n<script>window.DECK_CSRF=%s;</script>' % json.dumps(get_token(request)) if owner else ''
        resp = HttpResponse(_SHELL % (head, html.escape(str(deck.get('title') or name)), tail))
    elif rest == 'deck.json':
        resp = _deck_get(name, meta, owner)
    elif rest == 'notes.json':
        if not owner:
            raise Http404(rest)
        deck = _json(name, 'deck.json') or {}
        resp = JsonResponse({str(i): s['notes'] for i, s in enumerate(deck.get('slides', [])) if s.get('notes')},
                            json_dumps_params={'ensure_ascii': False})
        resp['Cache-Control'] = 'private, no-store'
        return resp
    elif rest == 'sw.js':
        files = _files(name, _json(name, 'deck.json') or {})
        version = hashlib.sha1(json.dumps(files).encode()).hexdigest()[:12]
        sw = (RT / 'lib' / 'sw.js').read_text()
        sw = sw.replace('"__VERSION__"', json.dumps(version)).replace('__FILES__;', json.dumps(files) + ';', 1)
        resp = HttpResponse(sw, content_type='text/javascript; charset=utf-8')
    else:
        # Judged on the RESOLVED path: "assets/../history/x.json" starts in an
        # allowed folder and ends in one that holds presenter notes.
        base = (DECKS / name).resolve()
        path = (base / rest).resolve()
        try:
            parts = path.relative_to(base).parts
        except ValueError:
            raise Http404(rest)
        if len(parts) < 2 or parts[0] not in _DIRS or not path.is_file() or path.name.startswith('.'):
            raise Http404(rest)
        ctype = _TYPES.get(path.suffix.lower(), 'application/octet-stream')
        tag = _tag(path)[1]
        w = request.GET.get('w', '')
        if parts[0] == 'assets' and w.isdigit():      # the display copy, where there is one
            shown = _display(base, path.relative_to(base).as_posix(), int(w))
            if shown:
                path, ctype = shown, 'image/webp'
        # A request that names the file's version (?v=, from deck.json's
        # site.files) is kept by the browser for good; any other revalidates
        # (an ETag and a 304).
        if request.GET.get('v') == tag:
            cache = ('public, ' if pub else 'private, ') + _FOREVER
        resp = _plain(serve_ranged(request, path, ctype, cache))
    resp['Cache-Control'] = cache
    # A deck frames its own figures (live/...): the middleware's DENY would
    # blank them. Set here, the middleware leaves it alone.
    resp['X-Frame-Options'] = 'SAMEORIGIN'
    return resp
