"""Region-wide tracer tiles: every Alaska ITS_LIVE composite cube.

Builds one tracer bundle per 100-km composite cube (the data's own tiling)
into data/glaciers/region/ — same array layout as the site bundles
(vx/vy annual stack + seasonal amp/phase + landice, 240 m int16 gzip) —
plus region_manifest.json (tile geometry index) for the /glaciers tile
manager. Enumerate → filter to the Alaska window → build; resumable
(existing outputs are skipped), so it can churn for hours and be re-run.

Empty-ice cubes are skipped (min ICE_MIN cells). Recent quarterlies are
deliberately NOT built region-wide (datacube chunking makes that ~50x the
cost; they stay a showcase-site feature).

Usage: python tools/build_glacier_region.py [--list-only]
"""
import datetime as _dt
import gzip
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import requests
import zarr
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_glacier_tracers import HttpZarrStore, S3_HTTP

COMPOSITE_PREFIX = 'composites/annual/v2-updated-september2025'
OUT_DIR = Path(__file__).resolve().parent.parent / 'data' / 'glaciers' / 'region'
GRID = 120.0
LON_RANGE = (-180.0, -125.0)
LAT_RANGE = (50.0, 72.0)
ICE_MIN = 200          # 120 m cells; below this the tile isn't worth serving
VMAX = 20000.0

_to4326 = Transformer.from_crs(3413, 4326, always_xy=True)


def _list(prefix, delimiter='/', token=None):
    url = (f'{S3_HTTP}/?list-type=2&prefix={prefix}&delimiter={delimiter}'
           + (f'&continuation-token={requests.utils.quote(token)}' if token else ''))
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.text


def list_common_prefixes(prefix):
    out, token = [], None
    while True:
        xml = _list(prefix, token=token)
        out += re.findall(r'<Prefix>([^<]+)</Prefix>', xml)
        m = re.search(r'<NextContinuationToken>([^<]+)</NextContinuationToken>', xml)
        if not m:
            break
        token = m.group(1)
    return [p for p in out if p != prefix]


def alaska_cubes():
    """[(url, cx, cy, lon, lat), …] for EPSG:3413 cubes in the AK window."""
    cubes = []
    for d in list_common_prefixes(f'{COMPOSITE_PREFIX}/'):
        dname = d.rstrip('/').rsplit('/', 1)[-1]
        # Cheap dir prefilter (names are truncation-based; keep it loose).
        if not re.match(r'^N[4-7]0W1[2-8]0$', dname):
            continue
        for z in list_common_prefixes(d):
            zname = z.rstrip('/').rsplit('/', 1)[-1]
            m = re.match(r'^ITS_LIVE_velocity_EPSG3413_120m_X(-?\d+)_Y(-?\d+)\.zarr$', zname)
            if not m:
                continue
            cx, cy = int(m.group(1)), int(m.group(2))
            lon, lat = _to4326.transform(cx, cy)
            if LON_RANGE[0] <= lon <= LON_RANGE[1] and LAT_RANGE[0] <= lat <= LAT_RANGE[1]:
                cubes.append((f'{S3_HTTP}/{z.rstrip("/")}', cx, cy, lon, lat))
    return sorted(set(cubes), key=lambda c: (c[1], c[2]))


def season_window(g):
    """ITS_LIVE states the seasonal fit's climatology window in the amp/phase
    attributes ("climatological [2014-2024] mean seasonal amplitude ..."). The
    viewer needs it: replaying that one cycle across the 1980s-2000s would
    assert seasonality the product never fitted there. Returns [lo, hi+1] in
    decimal years, or None if the attribute is not in the expected form."""
    for v in ('vx_amp', 'vx_phase'):
        try:
            txt = str(g[v].attrs.get('description', ''))
        except Exception:
            continue
        m = re.search(r'\[(\d{4})\s*-\s*(\d{4})\]', txt)
        if m:
            return [int(m.group(1)), int(m.group(2)) + 1]
    return None


def block2(a, reduce):
    py = (-a.shape[-2]) % 2
    px = (-a.shape[-1]) % 2
    pad = [(0, 0)] * (a.ndim - 2) + [(0, py), (0, px)]
    a = np.pad(a, pad, constant_values=np.nan)
    s = a.shape
    a = a.reshape(s[:-2] + (s[-2] // 2, 2, s[-1] // 2, 2))
    with np.errstate(all='ignore'):
        return reduce(a)


def nanmean22(a):
    return block2(a, lambda b: np.nanmean(np.nanmean(b, axis=-1), axis=-2))


def circmean22(a):
    ang = a * (2 * np.pi / 365.25)
    s = nanmean22(np.sin(ang))
    c = nanmean22(np.cos(ang))
    return (np.arctan2(s, c) % (2 * np.pi)) * (365.25 / (2 * np.pi))


def masked(zarr_arr, sel):
    a = np.asarray(zarr_arr[sel], np.float32)
    fv = zarr_arr.fill_value
    if fv is not None and not (isinstance(fv, float) and np.isnan(fv)) and fv != 0:
        a[a == np.float32(fv)] = np.nan
    return a


def build_cube(url, cx, cy):
    key = f'X{cx}_Y{cy}'
    binp = OUT_DIR / f'{key}.bin.gz'
    jsonp = OUT_DIR / f'{key}.json'
    if binp.exists() and jsonp.exists():
        return 'skip'
    g = zarr.open(HttpZarrStore(url), mode='r')

    ice120 = masked(g['landice'], (slice(None), slice(None)))
    n_ice = int(np.nansum(ice120 > 0))
    if n_ice < ICE_MIN:
        # Marker so re-runs skip the empty cube without re-downloading.
        jsonp.write_text(json.dumps({'key': key, 'empty': True}))
        return 'empty'

    gx = np.asarray(g['x']); gy = np.asarray(g['y'])
    t = np.asarray(g['time'])
    years = [(_dt.date(1970, 1, 1) + _dt.timedelta(days=float(d))).year for d in t]
    if any(years[i] != years[0] + i for i in range(len(years))):
        raise RuntimeError(f'{key}: non-consecutive year axis {years[0]}..{years[-1]}')

    SEASON_WIN = season_window(g)
    acc = {}
    for v in ('vx', 'vy'):
        a = masked(g[v], (slice(None), slice(None), slice(None)))
        a[np.abs(a) > VMAX] = np.nan
        acc[v] = nanmean22(a)
        del a
    for v in ('vx_amp', 'vy_amp'):
        a = masked(g[v], (slice(None), slice(None)))
        a[(a > 5000) | (a < 0)] = np.nan
        acc[v] = nanmean22(a)
    for v in ('vx_phase', 'vy_phase'):
        a = masked(g[v], (slice(None), slice(None)))
        a[(a < 0) | (a > 366.25)] = np.nan
        acc[v] = circmean22(a)
    acc['landice'] = block2(ice120, lambda b: np.nanmax(np.nanmax(b, axis=-1), axis=-2))

    ny, nx = acc['landice'].shape
    NODATA = -32768
    order = ['vx', 'vy', 'vx_amp', 'vy_amp', 'vx_phase', 'vy_phase', 'landice']
    offsets, pos = {}, 0
    with gzip.open(binp, 'wb', compresslevel=8) as f:
        for v in order:
            i16 = np.where(np.isnan(acc[v]), NODATA,
                           np.clip(np.round(acc[v]), -32000, 32000)).astype('<i2')
            offsets[v] = [pos, int(i16.size)]
            pos += i16.size
            f.write(i16.tobytes())

    hdr = {
        'key': key,
        'grid': {'epsg': 3413, 'x0': float(gx[0] - GRID / 2),
                 'y0_north': float(gy[0] + GRID / 2),
                 'dx': GRID * 2, 'nx': nx, 'ny': ny},
        'years': years,
        'season_window': SEASON_WIN,
        'dtype': 'int16', 'nodata': NODATA, 'scale': 1,
        'offsets': offsets,
        'bin': f'{key}.bin',
        'ice_cells': n_ice,
    }
    jsonp.write_text(json.dumps(hdr))
    return f'{pos * 2 / 1e6:.0f}MB raw, {binp.stat().st_size / 1e6:.1f}MB gz, {n_ice} ice'


def assemble_manifest():
    tiles = []
    for p in sorted(OUT_DIR.glob('X*.json')):
        h = json.loads(p.read_text())
        if not h.get('empty'):
            tiles.append(h)
    (OUT_DIR.parent / 'region_manifest.json').write_text(json.dumps({
        'tiles': tiles,
        'built': _dt.date.today().isoformat(),
    }))
    return len(tiles)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cubes = alaska_cubes()
    print(f'{len(cubes)} Alaska cubes to consider', flush=True)
    if '--list-only' in sys.argv:
        for (url, cx, cy, lon, lat) in cubes:
            print(f'  X{cx}_Y{cy}  ({lon:.1f}, {lat:.1f})')
        return
    for i, (url, cx, cy, lon, lat) in enumerate(cubes, 1):
        try:
            res = build_cube(url, cx, cy)
        except Exception as exc:
            res = f'FAILED: {type(exc).__name__}: {exc}'
        print(f'[{i}/{len(cubes)}] X{cx}_Y{cy} ({lon:.1f},{lat:.1f}): {res}', flush=True)
    n = assemble_manifest()
    print(f'manifest: {n} tiles')


if __name__ == '__main__':
    main()
