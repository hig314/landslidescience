"""Photo drops: create a link, upload through it, browse what arrived.

Three audiences, three gates:
  - creating / closing drops: inventory editors (and superusers)
  - uploading: anyone holding the link, plus the drop's passphrase if set;
    no account, so the collaborator needs nothing but the URL
  - viewing: any signed-in account (every account is a known collaborator)

The upload API hands the browser presigned R2 URLs and records completions;
it never receives file bytes. See drops/static/drops/upload.js for the
client half and drops/models.py for the design note.
"""
import json
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, F, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from inventory.auth import inventory_editor_required, is_inventory_editor

from . import r2, thumbs
from .models import Drop, DropFile, PathError

# Files above this go up in parts (resumable, parallel); below it one PUT.
# Game-cam JPEGs are a few MB, so nearly everything takes the single PUT.
MULTIPART_THRESHOLD = 100 * 1024 * 1024
PAGE_SIZE = 240
THUMB_URL_TTL = 2 * 3600          # the gallery page is re-rendered per visit
FILE_URL_TTL = 2 * 3600
MANIFEST_URL_TTL = 12 * 3600      # long enough to feed a downloader overnight

UPPY_VERSION = '4.13.3'           # v5+ speaks only the Companion protocol


def _session_key(drop):
    return f'drop_ok:{drop.slug}'


def _uploader_ok(request, drop):
    """May this request upload? Open drop, and passphrase cleared if any."""
    if not drop.accepts_uploads():
        return False
    if drop.has_passphrase() and not request.session.get(_session_key(drop)):
        return False
    return True


def _totals(drop):
    agg = drop.files.aggregate(n=Count('id'), size=Sum('size'))
    return {'n': agg['n'] or 0, 'bytes': agg['size'] or 0}


def _json(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return {}


def _err(msg, status=400):
    return JsonResponse({'error': msg}, status=status)


# ---- editor pages -----------------------------------------------------------

@inventory_editor_required
def index(request):
    if request.method == 'POST':
        title = (request.POST.get('title') or '').strip()
        if not title:
            return redirect('drops:index')
        d = Drop(title=title[:200], note=(request.POST.get('note') or '').strip(),
                 created_by=request.user)
        days = (request.POST.get('expires_days') or '').strip()
        if days.isdigit() and int(days) > 0:
            d.expires_at = timezone.now() + timedelta(days=int(days))
        d.set_passphrase((request.POST.get('passphrase') or '').strip())
        d.save()
        return redirect('drops:index')

    rows = []
    for d in Drop.objects.annotate(n=Count('files'), size=Sum('files__size')):
        rows.append({
            'drop': d, 'n': d.n, 'bytes': d.size or 0,
            'upload_url': request.build_absolute_uri(f'/drops/{d.slug}/'),
            'files_url': f'/drops/{d.slug}/files/',
        })
    return render(request, 'drops/index.html', {
        'rows': rows,
        'configured': r2.configured(),
        'bucket': r2.bucket(),
    })


@inventory_editor_required
@require_POST
def toggle(request, slug):
    d = get_object_or_404(Drop, slug=slug)
    d.closed = not d.closed
    d.save(update_fields=['closed'])
    return redirect('drops:index')


# ---- the upload page (link holders) -----------------------------------------

@ensure_csrf_cookie
def upload(request, slug):
    d = get_object_or_404(Drop, slug=slug)
    ctx = {'drop': d, 'totals': _totals(d), 'configured': r2.configured(),
           'can_view': request.user.is_authenticated}

    if not d.accepts_uploads():
        return render(request, 'drops/upload.html', dict(ctx, closed=True))

    if d.has_passphrase() and not request.session.get(_session_key(d)):
        error = ''
        if request.method == 'POST' and 'passphrase' in request.POST:
            if d.check_passphrase(request.POST.get('passphrase')):
                request.session[_session_key(d)] = True
                return redirect(request.path)
            error = 'That passphrase is not right.'
        return render(request, 'drops/gate.html', dict(ctx, error=error))

    ctx['config'] = {
        'api': f'/drops/{d.slug}/api/',
        'multipartThreshold': MULTIPART_THRESHOLD,
        'uppy': UPPY_VERSION,
    }
    return render(request, 'drops/upload.html', ctx)


# ---- upload API --------------------------------------------------------------

def _api_drop(request, slug):
    d = get_object_or_404(Drop, slug=slug)
    if not r2.configured():
        return d, _err('storage is not configured on this server', 503)
    if not _uploader_ok(request, d):
        return d, _err('this drop is not accepting uploads', 403)
    return d, None


@require_GET
def api_existing(request, slug):
    """{path: size} of everything already received — the client skips those,
    which is what makes 're-drop the same folder' a resume."""
    d, bad = _api_drop(request, slug)
    if bad:
        return bad
    return JsonResponse(dict(d.files.values_list('path', 'size')))


@require_GET
def api_status(request, slug):
    d = get_object_or_404(Drop, slug=slug)
    return JsonResponse(_totals(d))


@require_POST
def api_sign(request, slug):
    d, bad = _api_drop(request, slug)
    if bad:
        return bad
    body = _json(request)
    try:
        key = d.key_for(body.get('path'))
    except PathError as e:
        return _err(f'bad path: {e}')
    ctype = (body.get('type') or 'application/octet-stream')[:100]
    return JsonResponse({'method': 'PUT', 'url': r2.presign_put(key), 'fields': {},
                         'headers': {'content-type': ctype}})


def _record(d, rel, content_type=''):
    """Verify an object exists under the drop and upsert its row."""
    key = d.prefix + rel
    h = r2.head(key)
    if h is None:
        return None
    row, created = DropFile.objects.update_or_create(
        drop=d, path=rel,
        defaults={'key': key, 'size': h['size'], 'etag': h['etag'],
                  'content_type': h['content_type'] or content_type,
                  'uploaded_at': timezone.now(),
                  # a re-upload replaces the bytes, so the thumb is stale too
                  'thumb_state': 'pending', 'thumb_key': '', 'thumb_error': ''})
    thumbs.kick()
    return row


@require_POST
def api_record(request, slug):
    d, bad = _api_drop(request, slug)
    if bad:
        return bad
    body = _json(request)
    try:
        rel = d.rel_path_of(d.key_for(body.get('path')))
    except PathError as e:
        return _err(f'bad path: {e}')
    row = _record(d, rel, (body.get('type') or '')[:100])
    if row is None:
        return _err('object not found in storage — upload did not complete', 409)
    if body.get('size') is not None and int(body['size']) != row.size:
        return _err(f'size mismatch: browser {body["size"]}, stored {row.size}', 409)
    return JsonResponse(dict(_totals(d), ok=True, id=row.pk))


@require_POST
def api_mp_create(request, slug):
    d, bad = _api_drop(request, slug)
    if bad:
        return bad
    body = _json(request)
    try:
        key = d.key_for(body.get('path'))
    except PathError as e:
        return _err(f'bad path: {e}')
    upload_id = r2.mp_create(key, (body.get('type') or '')[:100])
    return JsonResponse({'uploadId': upload_id, 'key': key})


def _mp_args(d, body):
    key = body.get('key')
    d.rel_path_of(key)                       # raises PathError if not ours
    upload_id = body.get('uploadId')
    if not isinstance(upload_id, str) or not upload_id:
        raise PathError('missing uploadId')
    return key, upload_id


@require_POST
def api_mp_sign(request, slug):
    d, bad = _api_drop(request, slug)
    if bad:
        return bad
    body = _json(request)
    try:
        key, upload_id = _mp_args(d, body)
        part = int(body.get('partNumber'))
        if not 1 <= part <= 10000:
            raise ValueError
    except (PathError, TypeError, ValueError) as e:
        return _err(f'bad request: {e}')
    return JsonResponse({'url': r2.mp_sign_part(key, upload_id, part), 'headers': {}})


@require_POST
def api_mp_list(request, slug):
    d, bad = _api_drop(request, slug)
    if bad:
        return bad
    try:
        key, upload_id = _mp_args(d, _json(request))
    except PathError as e:
        return _err(f'bad request: {e}')
    return JsonResponse(r2.mp_list_parts(key, upload_id), safe=False)


@require_POST
def api_mp_complete(request, slug):
    d, bad = _api_drop(request, slug)
    if bad:
        return bad
    body = _json(request)
    try:
        key, upload_id = _mp_args(d, body)
        parts = body.get('parts') or []
        if not parts:
            raise PathError('no parts')
    except PathError as e:
        return _err(f'bad request: {e}')
    r2.mp_complete(key, upload_id, parts)
    _record(d, d.rel_path_of(key))
    return JsonResponse({'location': ''})


@require_POST
def api_mp_abort(request, slug):
    d, bad = _api_drop(request, slug)
    if bad:
        return bad
    try:
        key, upload_id = _mp_args(d, _json(request))
    except PathError as e:
        return _err(f'bad request: {e}')
    r2.mp_abort(key, upload_id)
    return JsonResponse({'ok': True})


# ---- viewing (signed-in collaborators) --------------------------------------

def _ordered_files(d):
    # Camera time first (what a time series wants), then path for the rest.
    return d.files.order_by(F('taken_at').asc(nulls_last=True), 'path')


@login_required
def files(request, slug):
    d = get_object_or_404(Drop, slug=slug)
    if not r2.configured():
        return render(request, 'drops/files.html', {
            'drop': d, 'page': None, 'items': [], 'totals': _totals(d),
            'thumbs_pending': 0, 'is_editor': is_inventory_editor(request.user),
            'can_upload': False, 'unconfigured': True})
    paginator = Paginator(_ordered_files(d), PAGE_SIZE)
    page = paginator.get_page(request.GET.get('page'))
    items = []
    for f in page.object_list:
        items.append({
            'f': f,
            'url': r2.presign_get(f.key, FILE_URL_TTL, f.name, inline=True),
            'dl_url': r2.presign_get(f.key, FILE_URL_TTL, f.name, inline=False),
            'thumb_url': (r2.presign_get(f.thumb_key, THUMB_URL_TTL)
                          if f.thumb_state == 'done' and f.thumb_key else ''),
        })
    states = dict(d.files.values_list('thumb_state').annotate(c=Count('id')))
    return render(request, 'drops/files.html', {
        'drop': d, 'page': page, 'items': items, 'totals': _totals(d),
        'thumbs_pending': states.get('pending', 0) + states.get('working', 0),
        'is_editor': is_inventory_editor(request.user),
        'can_upload': d.accepts_uploads(),
    })


@login_required
def manifest(request, slug):
    """One presigned URL per line, for `aria2c -i` / `wget -i` style bulk
    download. Zipping gigabytes through the droplet is not on offer."""
    d = get_object_or_404(Drop, slug=slug)
    if not r2.configured():
        raise Http404('storage not configured')
    lines = [f'# {d.title} — {_totals(d)["n"]} files; links valid '
             f'{MANIFEST_URL_TTL // 3600} h from {timezone.now():%Y-%m-%d %H:%M} UTC',
             '# aria2c -i this_file.txt   (or: wget -i this_file.txt)',
             '# Each URL downloads to the camera\'s own filename; folders are flattened.']
    for f in _ordered_files(d):
        lines.append(r2.presign_get(f.key, MANIFEST_URL_TTL, f.name, inline=False))
    resp = HttpResponse('\n'.join(lines) + '\n', content_type='text/plain; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="{d.slug}_links.txt"'
    return resp
