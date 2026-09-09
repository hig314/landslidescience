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

from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_safe
from django.views.static import serve as static_serve

from .auth import can_view_restricted, inventory_editor_required, is_inventory_editor
from .models import TraceRaster

MAX_UPLOAD_BYTES = 250 * 1024 * 1024
STALL_MINUTES = 30


def _spawn_bake(raster_id):
    from . import raster_tiles
    threading.Thread(target=raster_tiles.process, args=(raster_id,),
                     daemon=True, name=f'trace-bake-{raster_id}').start()


def _baked_at(raster_id, render):
    """Mtime of the tile directory, as an integer: the tile URLs carry it as
    ?v= so a re-bake (new render mode) defeats the year-long immutable cache
    on tiles that are otherwise addressed identically."""
    import os
    from . import raster_tiles
    try:
        return int(os.stat(raster_tiles.mode_dir(raster_id, render)).st_mtime)
    except OSError:
        return None


def _tiles_for(r):
    """Where the map gets this row's tiles for its current mode: a pre-baked
    PMTiles archive (one ranged route) or the server-baked XYZ pyramid."""
    from . import raster_tiles
    if raster_tiles.pmtiles_path(r.pk, r.render).exists():
        return {'kind': 'pmtiles', 'url': f'/inventory/tiles/trace/{r.pk}/{r.render}.pmtiles'}
    return {'kind': 'xyz', 'url': f'/inventory/tiles/trace/{r.pk}/{{z}}/{{x}}/{{y}}.png'}


def _row_json(r):
    # Stall = still 'processing' STALL_MINUTES after the bake BEGAN. The bake
    # writes a .started marker; without one (a bake that never got going,
    # or a row from before markers) fall back to the upload time.
    from . import raster_tiles
    started = raster_tiles.bake_started_at(r.pk, r.render)
    since = (datetime.datetime.fromtimestamp(started, tz=datetime.timezone.utc)
             if started else r.created_at)
    stalled = (r.status == TraceRaster.STATUS_PROCESSING
               and since < timezone.now() - datetime.timedelta(minutes=STALL_MINUTES))
    return {
        'id': r.pk,
        'title': r.title,
        'status': r.status,
        'stalled': stalled,
        'error': r.error_message or None,
        'image_date': r.image_date.isoformat() if r.image_date else None,
        'source_note': r.source_note or None,
        'render': r.render,
        'baked_at': _baked_at(r.pk, r.render),
        'tiles': _tiles_for(r),
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
    # Rows from before render modes existed carry 'auto' and a pyramid sitting
    # directly under their directory. Resolve the mode from the file once and
    # file the pyramid under it, so tile serving and mode reuse are exact.
    from . import raster_tiles
    for r in TraceRaster.objects.filter(render='auto'):
        try:
            render = raster_tiles.resolve_render(r.original.path, 'auto')
        except Exception:
            continue
        raster_tiles.adopt_legacy(r.pk, render)
        TraceRaster.objects.filter(pk=r.pk).update(render=render)
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

    render = request.POST.get('render', 'auto')
    if render not in dict(TraceRaster.RENDER_CHOICES):
        return JsonResponse({'ok': False, 'error': 'Unknown render mode.'}, status=400)

    # Pre-baked locally (tools/imagery/bake_trace.py): a PMTiles archive whose
    # header carries bounds and zooms, named <stem>.<mode>.pmtiles. Register
    # it ready; nothing to bake, nothing for the droplet's CPU to do.
    if f.name.lower().endswith('.pmtiles'):
        from . import raster_tiles
        import re as _re
        m = _re.search(r'\.(nrg|rgb|gray)\.pmtiles$', f.name, _re.I)
        mode = m.group(1).lower() if m else (render if render != 'auto' else 'nrg')
        row = TraceRaster.objects.create(
            title=(title or _re.sub(r'\.(nrg|rgb|gray)\.pmtiles$', '', f.name, flags=_re.I))[:200],
            original='', image_date=image_date, source_note=source_note[:300],
            render=mode, uploaded_by=request.user, status=TraceRaster.STATUS_PROCESSING)
        dest = raster_tiles.pmtiles_path(row.pk, mode)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(dest, 'wb') as out:
                for chunk in f.chunks():
                    out.write(chunk)
            meta = raster_tiles.read_pmtiles_header(dest)
        except ValueError as exc:
            shutil.rmtree(raster_tiles.tiles_dir(row.pk), ignore_errors=True)
            row.delete()
            return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
        TraceRaster.objects.filter(pk=row.pk).update(
            status=TraceRaster.STATUS_READY, error_message='', **meta)
        row.refresh_from_db()
        return JsonResponse({'ok': True, 'id': row.pk, 'raster': _row_json(row)})

    row = TraceRaster.objects.create(
        title=title[:200], original=f, image_date=image_date,
        source_note=source_note[:300], render=render, uploaded_by=request.user)

    # Fast synchronous pre-flight so a bad file fails NOW with a clear message
    # instead of after a background bake. Anything unopenable/ungeoreferenced
    # is rolled back entirely.
    try:
        from . import raster_tiles
        info = raster_tiles.probe(row.original.path)
        # Bounds now, not after the bake: the client zooms to the image and
        # draws its footprint while the tiles are still cooking.
        TraceRaster.objects.filter(pk=row.pk).update(**info)
        row.refresh_from_db()
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
    render = request.POST.get('render') or r.render
    if render not in dict(TraceRaster.RENDER_CHOICES):
        return JsonResponse({'ok': False, 'error': 'Unknown render mode.'}, status=400)
    has_original = bool(r.original) and r.original.storage.exists(r.original.name)
    if has_original:
        try:
            render = raster_tiles.resolve_render(r.original.path, render)
        except Exception:
            return JsonResponse({'ok': False, 'error': 'Could not read the original.'}, status=500)
    elif render == 'auto':
        render = r.render
    # Already baked in that mode (server pyramid or a pre-baked archive)?
    # Just switch — this also rescues a row wrongly left 'processing' (a bake
    # thread killed by a server restart) when a finished pyramid exists.
    if raster_tiles.mode_available(raster_id, render):
        TraceRaster.objects.filter(pk=raster_id).update(
            render=render, status=TraceRaster.STATUS_READY, error_message='')
        r.refresh_from_db()
        return JsonResponse({'ok': True, 'reused': True, 'raster': _row_json(r)})
    if not has_original:
        return JsonResponse({'ok': False, 'error':
                             f'No {render} version of this pre-baked image yet — bake it '
                             'locally (tools/imagery/bake_trace.py --render ' + render +
                             ') and upload that file.'}, status=409)
    # One bake at a time per row: a second request while one is running
    # would rmtree the directory the first is writing into.
    if r.status == TraceRaster.STATUS_PROCESSING and not _row_json(r)['stalled']:
        return JsonResponse({'ok': False, 'error': 'Still baking — wait for it to finish.'},
                            status=409)
    TraceRaster.objects.filter(pk=raster_id).update(
        status=TraceRaster.STATUS_PROCESSING, error_message='', render=render)
    _spawn_bake(raster_id)
    return JsonResponse({'ok': True, 'reused': False})


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
def trace_pmtiles(request, raster_id, render):
    """A pre-baked archive for one mode. Range requests, editor-only, immutable
    per (id, mode). Same range machinery as the lidar pyramids."""
    if not is_inventory_editor(request.user):
        return HttpResponseForbidden()
    from landslidescience.lidar_serve import serve_ranged
    from . import raster_tiles
    path = raster_tiles.pmtiles_path(raster_id, render)
    if not path.is_file():
        raise Http404
    return serve_ranged(request, path, 'application/vnd.pmtiles',
                        'private, max-age=31536000, immutable')


@require_safe
def trace_tile(request, raster_id, z, x, y):
    """Serve one baked tile. Viewers and editors only (403, not a login
    redirect — this is an <img>-style fetch from MapLibre, not a navigable
    page). Cache is `private`: tiles are immutable for a given raster id, but
    must not land in shared caches."""
    if not can_view_restricted(request.user):
        return HttpResponseForbidden()
    from . import raster_tiles
    try:
        render = TraceRaster.objects.values_list('render', flat=True).get(pk=raster_id)
    except TraceRaster.DoesNotExist:
        raise Http404
    raster_tiles.adopt_legacy(raster_id, render)   # no-op once moved
    resp = static_serve(request, f'{z}/{x}/{y}.png',
                        document_root=str(raster_tiles.mode_dir(raster_id, render)))
    resp['Cache-Control'] = 'private, max-age=31536000, immutable'
    return resp
