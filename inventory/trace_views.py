"""Trace-raster endpoints: editor-uploaded GeoTIFF overlays for in-app tracing.

Self-contained on purpose — views.py is the (large) landslide-data module;
everything trace-raster lives here + raster_tiles.py (processing) + the
TraceRaster model. All endpoints are editor-only, including the tiles: the
public map never fetches the registry (the map.js fetch is gated on
window._isInventoryEditor), snapshots exclude it for the same reason, and a
non-editor hitting a tile URL gets a 403.

Uploads bake in a background thread (see raster_tiles.py for why), so every
handler here is quick. A container restart mid-bake leaves a row stuck in
'processing' — rows older than STALL_MINUTES are flagged `stalled` in list
JSON, and the Rebuild endpoint / rebuild_trace_rasters command re-bake from
the stored original.
"""
import datetime
import json
import shutil
import threading

from django.http import HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_safe
from django.views.static import serve as static_serve

from .auth import inventory_editor_required, is_inventory_editor
from .models import TraceRaster

MAX_UPLOAD_BYTES = 250 * 1024 * 1024
STALL_MINUTES = 30


def _spawn_bake(raster_id):
    from . import raster_tiles
    threading.Thread(target=raster_tiles.process, args=(raster_id,),
                     daemon=True, name=f'trace-bake-{raster_id}').start()


def _row_json(r):
    stalled = (r.status == TraceRaster.STATUS_PROCESSING
               and r.created_at < timezone.now() - datetime.timedelta(minutes=STALL_MINUTES))
    return {
        'id': r.pk,
        'title': r.title,
        'status': r.status,
        'stalled': stalled,
        'error': r.error_message or None,
        'image_date': r.image_date.isoformat() if r.image_date else None,
        'source_note': r.source_note or None,
        'bounds_w': r.bounds_w, 'bounds_s': r.bounds_s,
        'bounds_e': r.bounds_e, 'bounds_n': r.bounds_n,
        'min_zoom': r.min_zoom, 'max_zoom': r.max_zoom,
        'tile_count': r.tile_count,
        'original_bytes': (r.original.size
                           if r.original and r.original.storage.exists(r.original.name)
                           else None),
        'landslide_id': r.landslide_id,
        'uploaded_by': r.uploaded_by.username if r.uploaded_by else None,
        'created_at': r.created_at.isoformat(),
    }


@inventory_editor_required
@require_safe
def trace_list(request):
    return JsonResponse({'rasters': [_row_json(r) for r in TraceRaster.objects.all()]})


@inventory_editor_required
@require_safe
def trace_status(request, raster_id):
    try:
        r = TraceRaster.objects.get(pk=raster_id)
    except TraceRaster.DoesNotExist:
        return JsonResponse({'error': 'not found'}, status=404)
    return JsonResponse(_row_json(r))


@inventory_editor_required
@require_POST
def trace_upload(request):
    f = request.FILES.get('file')
    if not f:
        return JsonResponse({'ok': False, 'error': 'No file received.'}, status=400)
    if f.size > MAX_UPLOAD_BYTES:
        return JsonResponse({'ok': False, 'error':
                             f'File is {f.size // (1024 * 1024)} MB — the cap is '
                             f'{MAX_UPLOAD_BYTES // (1024 * 1024)} MB. Downsample or crop it.'},
                            status=400)

    title = (request.POST.get('title') or '').strip() or f.name.rsplit('.', 1)[0]
    source_note = (request.POST.get('source_note') or '').strip()
    image_date = None
    raw_date = (request.POST.get('image_date') or '').strip()
    if raw_date:
        try:
            image_date = datetime.date.fromisoformat(raw_date)
        except ValueError:
            return JsonResponse({'ok': False, 'error': 'Image date must be YYYY-MM-DD.'},
                                status=400)

    row = TraceRaster.objects.create(
        title=title[:200], original=f, image_date=image_date,
        source_note=source_note[:300], uploaded_by=request.user)

    # Fast synchronous pre-flight so a bad file fails NOW with a clear message
    # instead of after a background bake. Anything unopenable/ungeoreferenced
    # is rolled back entirely.
    try:
        from . import raster_tiles
        raster_tiles.probe(row.original.path)
    except ValueError as exc:
        row.original.delete(save=False)
        row.delete()
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except Exception:
        row.original.delete(save=False)
        row.delete()
        return JsonResponse({'ok': False, 'error':
                             'Raster support is unavailable on the server '
                             '(rasterio failed to load).'}, status=500)

    _spawn_bake(row.pk)
    return JsonResponse({'ok': True, 'id': row.pk, 'raster': _row_json(row)})


@inventory_editor_required
@require_POST
def trace_rebuild(request, raster_id):
    from . import raster_tiles
    try:
        r = TraceRaster.objects.get(pk=raster_id)
    except TraceRaster.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'not found'}, status=404)
    if not (r.original and r.original.storage.exists(r.original.name)):
        return JsonResponse({'ok': False, 'error':
                             'Original file is missing — delete this row and re-upload.'},
                            status=409)
    TraceRaster.objects.filter(pk=raster_id).update(
        status=TraceRaster.STATUS_PROCESSING, error_message='')
    _spawn_bake(raster_id)
    return JsonResponse({'ok': True})


@inventory_editor_required
@require_POST
def trace_delete(request, raster_id):
    from . import raster_tiles
    try:
        r = TraceRaster.objects.get(pk=raster_id)
    except TraceRaster.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'not found'}, status=404)
    shutil.rmtree(raster_tiles.tiles_dir(raster_id), ignore_errors=True)
    if r.original:
        r.original.delete(save=False)
    r.delete()
    return JsonResponse({'ok': True})


@inventory_editor_required
@require_POST
def trace_link(request, raster_id):
    """Set/clear the provenance link to the landslide this image was traced
    into. Body: {"landslide_id": <int|null>}."""
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON body.'}, status=400)
    lid = payload.get('landslide_id')
    if lid is not None and (not isinstance(lid, int) or lid <= 0):
        return JsonResponse({'ok': False, 'error': 'landslide_id must be a positive '
                             'integer or null.'}, status=400)
    updated = TraceRaster.objects.filter(pk=raster_id).update(landslide_id=lid)
    if not updated:
        return JsonResponse({'ok': False, 'error': 'not found'}, status=404)
    return JsonResponse({'ok': True, 'landslide_id': lid})


@require_safe
def trace_tile(request, raster_id, z, x, y):
    """Serve one baked tile. Editor-only (403, not a login redirect — this is
    an <img>-style fetch from MapLibre, not a navigable page). Cache is
    `private`: tiles are immutable for a given raster id, but must not land
    in shared caches."""
    if not is_inventory_editor(request.user):
        return HttpResponseForbidden()
    from .raster_tiles import tiles_dir
    resp = static_serve(request, f'{z}/{x}/{y}.png',
                        document_root=str(tiles_dir(raster_id)))
    resp['Cache-Control'] = 'private, max-age=31536000, immutable'
    return resp
