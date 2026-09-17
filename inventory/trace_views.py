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
        'public': r.public,
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


@require_safe
def trace_list(request):
    """Every raster for an editor; the public, ready ones for anyone else.

    A visitor needs the list, not just the tiles: the map has to know a public
    scene's bounds and zoom range before it can add the layer, and a default
    view that names one has to resolve it.
    """
    if not is_inventory_editor(request.user):
        rows = TraceRaster.objects.filter(public=True, status=TraceRaster.STATUS_READY)
        return JsonResponse({'rasters': [_row_json(r) for r in rows]})
    return _trace_list_editor(request)


def _trace_list_editor(request):
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
    """A pre-baked archive for one mode. Range requests, immutable per
    (id, mode). Same range machinery as the lidar pyramids.

    Editor-only UNLESS the row is marked public. An upload is usually licensed
    imagery held in one copy and stays behind the gate; a Sentinel-2 window is
    openly licensed and is derived data we could rebuild from its linkage, so
    it is served to everyone. That is what lets a landslide's default view open
    on the image that shows it.
    """
    if not is_inventory_editor(request.user):
        if not TraceRaster.objects.filter(pk=raster_id, public=True,
                                          status=TraceRaster.STATUS_READY).exists():
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
    """Serve one baked tile. 403 rather than a login redirect — this is an
    <img>-style fetch from MapLibre, not a navigable page.

    Uploads are for viewers and editors. A row marked `public` is openly
    licensed imagery we hold only as a rebuildable rendering, so it goes to
    everyone and may sit in shared caches; that is what makes it usable as a
    landslide's default view.
    """
    from . import raster_tiles
    try:
        row = TraceRaster.objects.values('render', 'public', 'status').get(pk=raster_id)
    except TraceRaster.DoesNotExist:
        raise Http404
    is_public = row['public'] and row['status'] == TraceRaster.STATUS_READY
    if not is_public and not can_view_restricted(request.user):
        return HttpResponseForbidden()
    render = row['render']
    raster_tiles.adopt_legacy(raster_id, render)   # no-op once moved
    resp = static_serve(request, f'{z}/{x}/{y}.png',
                        document_root=str(raster_tiles.mode_dir(raster_id, render)))
    resp['Cache-Control'] = ('public, max-age=31536000, immutable' if is_public
                             else 'private, max-age=31536000, immutable')
    return resp


# --- Sentinel-2 by date and place ------------------------------------------
# Adding a scene is an editor action, so these two are behind the gate; the
# row they produce is public, because a Copernicus window is openly licensed
# and we hold it only as a rebuildable rendering. See inventory/sentinel.py.

SENTINEL_MAX_HALF_M = 20000.0
_LEVEL_LABEL = {'sentinel-2-l1c': 'Sentinel-2 L1C',
                'sentinel-2-l2a': 'Sentinel-2 L2A'}


def _json_body(request):
    try:
        return json.loads(request.body.decode('utf-8')), None
    except (ValueError, UnicodeDecodeError):
        return None, JsonResponse({'ok': False, 'error': 'Invalid JSON body.'}, status=400)


@inventory_editor_required
@require_POST
def sentinel_search(request):
    """Candidate scenes for a point and a date.

    Body: {"lat":, "lon":, "date": "YYYY-MM-DD", "days": 7}. Returns the
    scenes with their cloud cover so the picker can show which dates are worth
    fetching -- the cover is the whole granule's, which is a hint and not a
    verdict, and the form says so.
    """
    payload, err = _json_body(request)
    if err:
        return err
    try:
        lat = float(payload['lat'])
        lon = float(payload['lon'])
        date = str(payload['date'])[:10]
        days = min(int(payload.get('days') or 7), 60)
    except (KeyError, TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'lat, lon and date (YYYY-MM-DD) '
                             'are required.'}, status=400)
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'Date must be YYYY-MM-DD.'}, status=400)

    from . import sentinel
    collection = sentinel.COLLECTIONS.get(payload.get('level') or sentinel.DEFAULT_LEVEL)
    if collection is None:
        return JsonResponse({'ok': False, 'error': 'Unknown processing level.'}, status=400)
    try:
        scenes = sentinel.search(lon, lat, date, days=days, collection=collection)
    except Exception as exc:                                   # noqa: BLE001
        return JsonResponse({'ok': False, 'error': f'Scene search failed: {exc}'},
                            status=502)
    return JsonResponse({'ok': True, 'collection': collection, 'scenes': [
        {'scene': s['scene'], 'date': s['date'], 'datetime': s['datetime'],
         'cloud_cover': s['cloud_cover']} for s in scenes]})


@inventory_editor_required
@require_POST
def sentinel_add(request):
    """Fetch one scene's window here and bake it, in the background.

    Body: {"lat":, "lon":, "scene":, "date":, "half":, "render":, "title":}.
    Returns the row immediately with status 'processing'; the picker polls
    the usual trace status endpoint from there, exactly as an upload does.
    """
    payload, err = _json_body(request)
    if err:
        return err
    try:
        lat = float(payload['lat'])
        lon = float(payload['lon'])
        scene_id = str(payload['scene'])
        date = str(payload['date'])[:10]
    except (KeyError, TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'lat, lon, scene and date '
                             'are required.'}, status=400)
    half = float(payload.get('half') or 6000)
    if not 500 <= half <= SENTINEL_MAX_HALF_M:
        return JsonResponse({'ok': False, 'error': 'Half-width must be between 500 m '
                             f'and {SENTINEL_MAX_HALF_M:.0f} m.'}, status=400)
    render = payload.get('render') or 'nrg'
    if render not in dict(TraceRaster.RENDER_CHOICES):
        return JsonResponse({'ok': False, 'error': 'Unknown render mode.'}, status=400)

    # The asset URLs have to come from the search, not from the client: they
    # are where we will read bytes from, and a caller-supplied href would make
    # this endpoint fetch anything an editor's browser was told to name.
    from . import sentinel
    collection = sentinel.COLLECTIONS.get(payload.get('level') or sentinel.DEFAULT_LEVEL)
    if collection is None:
        return JsonResponse({'ok': False, 'error': 'Unknown processing level.'}, status=400)
    try:
        scenes = sentinel.search(lon, lat, date, days=1, collection=collection)
    except Exception as exc:                                   # noqa: BLE001
        return JsonResponse({'ok': False, 'error': f'Scene lookup failed: {exc}'},
                            status=502)
    pick = next((s for s in scenes if s['scene'] == scene_id), None)
    if pick is None:
        return JsonResponse({'ok': False, 'error': 'That scene no longer covers this '
                             'point — search again.'}, status=400)

    cc = pick['cloud_cover']
    title = (payload.get('title') or '').strip() or f"Sentinel-2 {pick['date']}"
    row = TraceRaster.objects.create(
        title=title[:200], image_date=datetime.date.fromisoformat(pick['date']),
        render=render, public=True, uploaded_by=request.user,
        status=TraceRaster.STATUS_PROCESSING,
        source_note=(f"Copernicus {_LEVEL_LABEL.get(collection, collection)}"
                     f" · {pick['scene']}"
                     + (f" · cloud {cc:.0f}%" if cc is not None else ''))[:300],
        source_ref={'kind': collection, 'scene': pick['scene'],
                    'datetime': pick['datetime'], 'centre': [lon, lat],
                    'half_m': half, 'cloud_cover': cc, 'assets': pick['assets']})
    threading.Thread(target=sentinel.process, args=(row.pk,), daemon=True,
                     name=f'sentinel-{row.pk}').start()
    return JsonResponse({'ok': True, 'id': row.pk, 'raster': _row_json(row)})
