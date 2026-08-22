"""OPERA DISP-S1 point time-series proxy (the map's "InSAR" click tool).

ASF's displacement portal backs its click-for-time-series chart with a
public, unauthenticated endpoint (reverse-engineered from their app bundle
2026-08-22; verified with a live probe):

    POST https://d2qmcvu7qty7vn.cloudfront.net/timeseries
    {"wkt": "POINT(lon lat)", "bucket": "asf-cumulus-prod-opera-products",
     "polarization": "VV", "flightDirection": "ASCENDING"|"DESCENDING"}

The response is keyed by source DISP-S1 granule (each a ~420 MB NetCDF we
never have to touch), one epoch per granule: reference/secondary datetimes
and `short_wavelength_displacement` in meters (long-wavelength atmosphere/
tectonics already filtered by OPERA upstream).

This proxy exists because (a) browser CORS from our origin is untested and
(b) an undocumented third-party endpoint deserves a cache between it and
every curious click: responses land in data/insar_ts_cache/ with a 7-day
TTL. If ASF restructures, cached points keep serving while we adapt — and
the DAAC granules remain the heavy-but-official fallback. Be polite: this
is their infrastructure; the cache and the single-point request shape keep
us inside the envelope their own portal uses.

Serving shape (regrouped here so the chart stays dumb): epochs grouped by
reference stack, since each stack's displacements are relative to its OWN
reference date — stitching stacks into one continuous series would assert a
cross-stack datum we do not have.

    {"series": [{"ref": iso, "points": [[iso, mm], ...]}, ...],
     "n": total_epochs, "cell": [x, y] | null}
"""
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_safe

log = logging.getLogger(__name__)

UPSTREAM = 'https://d2qmcvu7qty7vn.cloudfront.net/timeseries'
BUCKET = 'asf-cumulus-prod-opera-products'
CACHE_DIR = Path(settings.BASE_DIR) / 'data' / 'insar_ts_cache'
CACHE_TTL_S = 7 * 86400
UPSTREAM_TIMEOUT_S = 45
DIRECTIONS = {'ascending': 'ASCENDING', 'descending': 'DESCENDING'}
_UA = {'User-Agent': 'landslidescience-insar-ts/1',
       'Content-Type': 'application/json'}


def _regroup(raw):
    """Upstream {granule: epoch_record} -> chart series grouped by reference
    stack, points sorted by secondary date, displacement in mm."""
    stacks = {}
    cell = None
    for rec in raw.values():
        if not isinstance(rec, dict):
            continue
        ref = rec.get('reference_datetime')
        sec = rec.get('secondary_datetime')
        disp = rec.get('short_wavelength_displacement')
        if ref is None or sec is None or disp is None:
            continue
        stacks.setdefault(ref, []).append([sec, round(float(disp) * 1000.0, 3)])
        if cell is None and rec.get('x') is not None:
            cell = [rec.get('x'), rec.get('y')]
    series = []
    for ref in sorted(stacks):
        pts = sorted(stacks[ref])
        series.append({'ref': ref, 'points': pts})
    return {'series': series, 'n': sum(len(s['points']) for s in series),
            'cell': cell}


@require_safe
def timeseries(request):
    try:
        lat = float(request.GET.get('lat', ''))
        lon = float(request.GET.get('lon', ''))
    except ValueError:
        return JsonResponse({'error': 'lat/lon required'}, status=400)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return JsonResponse({'error': 'lat/lon out of range'}, status=400)
    direction = request.GET.get('dir', 'ascending')
    if direction not in DIRECTIONS:
        return JsonResponse({'error': 'dir must be ascending|descending'},
                            status=400)

    # The service snaps to its ~30 m product grid, so 4-decimal (~11 m)
    # rounding makes nearby clicks share a cache entry without changing
    # which cell answers.
    key = f'{direction}_{round(lat, 4):.4f}_{round(lon, 4):.4f}.json'
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / key
    if cached.exists() and (time.time() - cached.stat().st_mtime) < CACHE_TTL_S:
        resp = JsonResponse(json.loads(cached.read_text()))
        resp['X-Insar-Cache'] = 'hit'
        return resp

    body = json.dumps({
        'wkt': f'POINT({round(lon, 4)} {round(lat, 4)})',
        'bucket': BUCKET,
        'polarization': 'VV',
        'flightDirection': DIRECTIONS[direction],
    }).encode()
    req = urllib.request.Request(UPSTREAM, data=body, headers=_UA, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT_S) as r:
            raw = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = json.loads(e.read().decode()).get('detail', '')
        except Exception:
            pass
        # ASF answers "no coverage here" (ocean, outside frames) as a 500
        # with this detail string. That is a normal answer, not a failure —
        # return the empty shape so the chart says "no coverage", and cache
        # it so repeat ocean clicks don't re-ask.
        if 'No valid data found' in detail:
            out = {'series': [], 'n': 0, 'cell': None}
            cached.write_text(json.dumps(out))
            return JsonResponse(out)
        log.warning('insar upstream HTTP %s: %s', e.code, detail)
        return JsonResponse({'error': f'ASF service error ({e.code})',
                             'detail': detail}, status=502)
    except Exception as e:
        log.warning('insar upstream failed: %s', e)
        return JsonResponse({'error': 'ASF service unreachable'}, status=502)

    out = _regroup(raw) if isinstance(raw, dict) else {'series': [], 'n': 0,
                                                       'cell': None}
    cached.write_text(json.dumps(out))
    resp = JsonResponse(out)
    resp['X-Insar-Cache'] = 'miss'
    return resp
