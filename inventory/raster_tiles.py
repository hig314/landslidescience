"""Bake an uploaded GeoTIFF into an XYZ PNG tile pyramid (EPSG:3857).

Upload-time only. Runs in a background thread (gunicorn's 30 s default
timeout and 2-worker pool rule out doing this in-request) and always ends in
a terminal TraceRaster status — 'ready' or 'error' — so the UI never hangs on
an exception. Serving is dumb static files (trace_views.trace_tile), so if
this module or the rasterio wheel ever breaks, only *new uploads* break.

rasterio (GDAL bundled in the wheel, pyogrio's raster twin) and numpy are
imported inside functions, never at module import — the web app must start
without them.

Pipeline: validate (must carry a CRS + real geotransform) → WarpedVRT to
EPSG:3857 snapped to the max-zoom tile grid (so every tile is an exact
pixel-aligned window; no boundless reads) → per-band 2–98 % percentile
stretch for non-uint8 imagery (uint16 Planet/Landsat renders black without
it) → write 256px RGBA PNGs, skipping fully-transparent tiles (the
susc-tiles precedent). Zoom range derives from the native ground resolution,
clamped by a total tile budget.
"""
import logging
import math
import shutil

from django.conf import settings

log = logging.getLogger(__name__)

TRACE_TILES_DIR = settings.BASE_DIR / 'data' / 'trace_tiles'

TILE_SIZE = 256
MAX_TILES = 6000          # pyramid budget — clamps max_zoom for huge inputs
MAX_ZOOM_HARD = 19
MIN_ZOOM_FLOOR = 4
ZOOM_SPAN = 7             # min_zoom = max_zoom - ZOOM_SPAN (few tiles at low z)
STRETCH_PCT = (2.0, 98.0)
OVERVIEW_PX = 1024        # decimated read used to sample stretch percentiles

_MERC_MAX = 20037508.342789244   # EPSG:3857 half-world extent, metres


def tiles_dir(raster_id):
    return TRACE_TILES_DIR / str(int(raster_id))


# --- slippy-map math (same formulas as basemaps.js, in metres) -------------

def _tile_span(z):
    return 2.0 * _MERC_MAX / (1 << z)


def _tile_range(z, minx, miny, maxx, maxy):
    """Inclusive tile x/y index ranges covering a 3857 bbox at zoom z."""
    n = 1 << z
    span = _tile_span(z)
    x0 = max(0, min(n - 1, int((minx + _MERC_MAX) / span)))
    x1 = max(0, min(n - 1, int((maxx + _MERC_MAX) / span)))
    y0 = max(0, min(n - 1, int((_MERC_MAX - maxy) / span)))
    y1 = max(0, min(n - 1, int((_MERC_MAX - miny) / span)))
    return x0, x1, y0, y1


def _count_tiles(min_zoom, max_zoom, bounds):
    total = 0
    for z in range(min_zoom, max_zoom + 1):
        x0, x1, y0, y1 = _tile_range(z, *bounds)
        total += (x1 - x0 + 1) * (y1 - y0 + 1)
    return total


# --- entry points -----------------------------------------------------------

def probe(path):
    """Fast pre-flight: openable + georeferenced. Raises ValueError with an
    editor-facing message otherwise. Called synchronously at upload so the
    user gets instant feedback before the background bake starts."""
    import rasterio

    try:
        with rasterio.open(path) as src:
            crs, transform = src.crs, src.transform
    except Exception:
        raise ValueError('Could not read this file as a raster — is it a GeoTIFF?')
    if crs is None or transform is None or transform.is_identity:
        raise ValueError('This raster carries no georeferencing (CRS + transform). '
                         'Export a GeoTIFF with an embedded CRS and try again.')


def process(raster_id):
    """Bake one TraceRaster. Never raises — ends in status ready/error."""
    from django.db import connections

    from .models import TraceRaster

    try:
        row = TraceRaster.objects.get(pk=raster_id)
        out_dir = tiles_dir(raster_id)
        shutil.rmtree(out_dir, ignore_errors=True)   # re-bake starts clean
        meta = _bake(row.original.path, out_dir)
        TraceRaster.objects.filter(pk=raster_id).update(
            status=TraceRaster.STATUS_READY, error_message='', **meta)
        log.info('trace raster %s baked: %s tiles, z%s-%s',
                 raster_id, meta['tile_count'], meta['min_zoom'], meta['max_zoom'])
    except Exception as exc:
        log.exception('trace raster %s bake failed', raster_id)
        TraceRaster.objects.filter(pk=raster_id).update(
            status=TraceRaster.STATUS_ERROR, error_message=str(exc)[:1000])
    finally:
        # This may run in a short-lived thread — don't leak its connections.
        connections.close_all()


# --- the bake ---------------------------------------------------------------

def _bake(src_path, out_dir):
    import numpy as np
    import rasterio
    from rasterio.enums import ColorInterp, Resampling
    from rasterio.transform import from_origin
    from rasterio.vrt import WarpedVRT
    from rasterio.warp import transform_bounds

    with rasterio.open(src_path) as src:
        if src.crs is None or src.transform is None or src.transform.is_identity:
            raise ValueError('Raster carries no georeferencing (CRS + transform).')

        # Probe pass: warped bounds + native resolution in 3857.
        with WarpedVRT(src, crs='EPSG:3857', resampling=Resampling.bilinear) as vrt:
            wb = vrt.bounds
            native_res = max(abs(vrt.transform.a), abs(vrt.transform.e))
        bounds = (max(wb.left, -_MERC_MAX), max(wb.bottom, -_MERC_MAX),
                  min(wb.right, _MERC_MAX), min(wb.top, _MERC_MAX))
        if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            raise ValueError('Raster bounds fall outside the web-mercator world.')

        # Zoom range: finest zoom whose tile resolution beats the native GSD,
        # +1 for tracing headroom; then trim until the pyramid fits the budget.
        max_zoom = math.ceil(math.log2(2 * _MERC_MAX / (TILE_SIZE * native_res))) + 1
        max_zoom = max(MIN_ZOOM_FLOOR + 1, min(MAX_ZOOM_HARD, max_zoom))
        min_zoom = max(MIN_ZOOM_FLOOR, max_zoom - ZOOM_SPAN)
        while (_count_tiles(min_zoom, max_zoom, bounds) > MAX_TILES
               and max_zoom > min_zoom + 1):
            max_zoom -= 1
            min_zoom = max(MIN_ZOOM_FLOOR, max_zoom - ZOOM_SPAN)

        # Bands: RGB (or replicated gray) + alpha. If the source has no alpha
        # band, WarpedVRT adds one tracking the valid warp region — otherwise
        # nodata edges from rotation/reprojection render as black borders.
        src_alpha = ColorInterp.alpha in src.colorinterp
        rgb_idx = (1, 2, 3) if src.count >= 3 else (1,)
        alpha_idx = src.colorinterp.index(ColorInterp.alpha) + 1 if src_alpha else None

        # Bake grid: a VRT snapped to the max-zoom tile grid but covering the
        # FULL min-zoom tile range (tile grids nest, so every tile at every
        # zoom in [min,max] is then an exact pixel-aligned window inside the
        # grid — WarpedVRT forbids boundless reads, and a partial window with
        # out_shape would silently stretch the pixels). The grid is virtual;
        # reads are windowed + decimated, so its nominal size costs nothing.
        mx0, mx1, my0, my1 = _tile_range(min_zoom, *bounds)
        shift = max_zoom - min_zoom
        gx0, gy0 = mx0 << shift, my0 << shift
        gx1 = ((mx1 + 1) << shift) - 1
        gy1 = ((my1 + 1) << shift) - 1
        res = _tile_span(max_zoom) / TILE_SIZE
        grid_w = (gx1 - gx0 + 1) * TILE_SIZE
        grid_h = (gy1 - gy0 + 1) * TILE_SIZE
        grid_origin_x = -_MERC_MAX + gx0 * _tile_span(max_zoom)
        grid_origin_y = _MERC_MAX - gy0 * _tile_span(max_zoom)
        grid_transform = from_origin(grid_origin_x, grid_origin_y, res, res)

        vrt_kwargs = dict(crs='EPSG:3857', transform=grid_transform,
                          width=grid_w, height=grid_h,
                          resampling=Resampling.bilinear)
        if not src_alpha:
            vrt_kwargs['add_alpha'] = True

        with WarpedVRT(src, **vrt_kwargs) as vrt:
            if alpha_idx is None:
                alpha_idx = vrt.count   # the alpha band WarpedVRT appended

            # Stretch parameters from a decimated overview of the DATA window
            # (not the whole grid, which is mostly empty when the image is
            # small relative to a min-zoom tile) — non-uint8 imagery
            # (Planet/Landsat uint16) is black without a stretch.
            data_win = rasterio.windows.Window(
                int((bounds[0] - grid_origin_x) / res),
                int((grid_origin_y - bounds[3]) / res),
                max(1, math.ceil((bounds[2] - bounds[0]) / res)),
                max(1, math.ceil((bounds[3] - bounds[1]) / res)))
            stretch = None
            if src.dtypes[0] != 'uint8':
                ov_shape = (max(1, min(OVERVIEW_PX, int(data_win.height))),
                            max(1, min(OVERVIEW_PX, int(data_win.width))))
                ov = vrt.read(indexes=rgb_idx, window=data_win,
                              out_shape=(len(rgb_idx),) + ov_shape)
                ov_a = vrt.read(indexes=alpha_idx, window=data_win,
                                out_shape=ov_shape)
                valid = ov_a > 0
                stretch = []
                for b in range(len(rgb_idx)):
                    vals = ov[b][valid]
                    if vals.size == 0:
                        raise ValueError('Raster contains no valid (unmasked) pixels.')
                    lo, hi = np.percentile(vals, STRETCH_PCT)
                    stretch.append((float(lo), max(float(hi), float(lo) + 1.0)))

            def to_uint8(band_data, b):
                if stretch is None:
                    return band_data.astype(np.uint8)
                lo, hi = stretch[b]
                return (np.clip((band_data.astype(np.float64) - lo) / (hi - lo), 0, 1)
                        * 255).astype(np.uint8)

            tile_count = 0
            tile_bytes = 0
            png_profile = dict(driver='PNG', width=TILE_SIZE, height=TILE_SIZE,
                               count=4, dtype='uint8')
            for z in range(min_zoom, max_zoom + 1):
                tx0, tx1, ty0, ty1 = _tile_range(z, *bounds)
                scale = 1 << (max_zoom - z)   # grid pixels per output pixel
                for x in range(tx0, tx1 + 1):
                    for y in range(ty0, ty1 + 1):
                        # Window of this tile in the grid-aligned VRT, via
                        # mercator offsets so the alignment stays exact.
                        span = _tile_span(z)
                        wminx = -_MERC_MAX + x * span
                        wmaxy = _MERC_MAX - y * span
                        goffx = round((wminx - grid_origin_x) / res)
                        goffy = round((grid_origin_y - wmaxy) / res)
                        win = rasterio.windows.Window(goffx, goffy,
                                                      TILE_SIZE * scale, TILE_SIZE * scale)
                        a = vrt.read(indexes=alpha_idx, window=win,
                                     out_shape=(TILE_SIZE, TILE_SIZE))
                        if not a.any():
                            continue   # fully transparent — skip, like susc tiles
                        rgb = vrt.read(indexes=rgb_idx, window=win,
                                       out_shape=(len(rgb_idx), TILE_SIZE, TILE_SIZE))
                        out = np.empty((4, TILE_SIZE, TILE_SIZE), dtype=np.uint8)
                        if len(rgb_idx) == 1:
                            g = to_uint8(rgb[0], 0)
                            out[0] = out[1] = out[2] = g
                        else:
                            for b in range(3):
                                out[b] = to_uint8(rgb[b], b)
                        out[3] = np.where(a > 0, 255, 0).astype(np.uint8)

                        tile_path = out_dir / str(z) / str(x) / f'{y}.png'
                        tile_path.parent.mkdir(parents=True, exist_ok=True)
                        with rasterio.open(tile_path, 'w', **png_profile) as dst:
                            dst.write(out)
                        # GDAL's PNG driver may drop a .aux.xml sidecar; we
                        # serve raw pixels only.
                        aux = tile_path.with_suffix('.png.aux.xml')
                        if aux.exists():
                            aux.unlink()
                        tile_count += 1
                        tile_bytes += tile_path.stat().st_size

            if tile_count == 0:
                raise ValueError('No visible tiles produced — the raster may be '
                                 'entirely nodata.')

        w4326 = transform_bounds('EPSG:3857', 'EPSG:4326', *bounds)
        return {
            'bounds_w': w4326[0], 'bounds_s': w4326[1],
            'bounds_e': w4326[2], 'bounds_n': w4326[3],
            'min_zoom': min_zoom, 'max_zoom': max_zoom,
            'tile_count': tile_count, 'tile_bytes': tile_bytes,
        }
