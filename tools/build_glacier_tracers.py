"""Build Lagrangian tracer bundles for the /glaciers app.

For each configured AOI, crops the ITS_LIVE v2 annual composite cubes
(composites/annual/v2-updated-september2025/ on the public its-live-data
bucket — 120 m EPSG:3413 grid, one whole-tile chunk per year per variable)
and writes:

  data/glaciers/<slug>.json   header: name/center/zoom, grid geometry,
                              years, phase units, byte offsets into the .bin
  data/glaciers/<slug>.bin    little-endian Float32, in order:
                              vx[years][ny][nx], vy[years][ny][nx],
                              vx_amp, vy_amp, vx_phase, vy_phase,
                              landice (0/1) — all [ny][nx]; NoData = NaN

The client engine (glaciers.js) samples velocity as
    v(t) = v_yearblend + amp * cos(2*pi * (doy - phase) / 365.25)
per component: annual means linearly blended between years, with the
climatological seasonal cycle (amplitude + day-of-peak phase) superposed.

AOIs spanning cube boundaries are mosaicked. Velocity units m/yr; phase =
day of year of peak. Run from a venv with: numpy zarr==2.* numcodecs
fsspec aiohttp requests pyproj.

Usage: python tools/build_glacier_tracers.py [slug ...]   (default: all)
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import fsspec
import zarr
from pyproj import Transformer

S3_HTTP = 'https://its-live-data.s3.amazonaws.com'
COMPOSITE_PREFIX = 'composites/annual/v2-updated-september2025'
OUT_DIR = Path(__file__).resolve().parent.parent / 'data' / 'glaciers'
GRID = 120.0        # m
TILE = 100000.0     # cube footprint, m

# AOIs: slug -> (name, lon/lat bbox [w, s, e, n], display center, zoom)
AOIS = {
    'columbia': ('Columbia Glacier', (-147.75, 60.90, -146.50, 61.55),
                 (-147.07, 61.15), 10),
    'grand-plateau': ('Grand Plateau Glacier', (-138.30, 58.85, -137.35, 59.35),
                      (-137.85, 59.05), 10.5),
}

_to3413 = Transformer.from_crs(4326, 3413, always_xy=True)
_to4326 = Transformer.from_crs(3413, 4326, always_xy=True)


def tile_center_for(x, y):
    return (math.floor(x / TILE) * TILE + TILE / 2,
            math.floor(y / TILE) * TILE + TILE / 2)


def cube_url(cx, cy):
    """Resolve a cube's URL. The 10° directory name uses TRUNCATION toward
    zero of the cube-center lon/lat (verified: lon -146.66 → W140, -151.1 →
    W150) — but rather than trust one probe, HEAD the .zgroup across the
    trunc/floor candidates and take whichever exists (None if none do)."""
    import requests
    lon, lat = _to4326.transform(cx, cy)
    cands = []
    for la in {int(lat / 10) * 10, math.floor(lat / 10) * 10}:
        for lo in {int(lon / 10) * 10, math.floor(lon / 10) * 10}:
            ns = 'N' if la >= 0 else 'S'
            ew = 'E' if lo >= 0 else 'W'
            cands.append(f'{ns}{abs(la):02d}{ew}{abs(lo):03d}')
    for dname in cands:
        url = (f'{S3_HTTP}/{COMPOSITE_PREFIX}/{dname}/'
               f'ITS_LIVE_velocity_EPSG3413_120m_X{int(cx)}_Y{int(cy)}.zarr')
        try:
            if requests.head(url + '/.zgroup', timeout=20).status_code == 200:
                return url
        except requests.RequestException:
            continue
    return None


def aoi_grid(bbox):
    """3413 bounding box (snapped to the 120 m grid) covering a lon/lat bbox."""
    w, s, e, n = bbox
    xs, ys = [], []
    for lon in (w, e, (w + e) / 2):
        for lat in (s, n, (s + n) / 2):
            x, y = _to3413.transform(lon, lat)
            xs.append(x); ys.append(y)
    x0 = math.floor(min(xs) / GRID) * GRID
    x1 = math.ceil(max(xs) / GRID) * GRID
    y0 = math.floor(min(ys) / GRID) * GRID
    y1 = math.ceil(max(ys) / GRID) * GRID
    return x0, y0, x1, y1


def build(slug):
    name, bbox, center, zoom = AOIS[slug]
    x0, y0, x1, y1 = aoi_grid(bbox)
    nx = int(round((x1 - x0) / GRID))
    ny = int(round((y1 - y0) / GRID))
    print(f'== {slug}: {nx} x {ny} cells, 3413 box ({x0:.0f},{y0:.0f})..({x1:.0f},{y1:.0f})')

    # Cubes intersecting the box.
    centers = set()
    cx = math.floor(x0 / TILE) * TILE + TILE / 2
    while cx < x1 + TILE / 2:
        cy = math.floor(y0 / TILE) * TILE + TILE / 2
        while cy < y1 + TILE / 2:
            centers.add((cx, cy))
            cy += TILE
        cx += TILE

    years = None
    phase_units = None
    VARS_T = ('vx', 'vy')                                 # [time, y, x]
    VARS_S = ('vx_amp', 'vy_amp', 'vx_phase', 'vy_phase', 'landice')  # [y, x]
    acc = {}

    for (cx, cy) in sorted(centers):
        url = cube_url(cx, cy)
        if url is None:
            print(f'  cube X{int(cx)}_Y{int(cy)}: not found in any 10° dir '
                  f'(likely no ice tile here) — skipping')
            continue
        print(f'  cube {url.rsplit("/", 2)[-2]}/{url.rsplit("/", 1)[-1]}')
        try:
            store = fsspec.get_mapper(url)
            g = zarr.open(store, mode='r')
        except Exception as exc:
            print(f'    SKIP (unreadable): {type(exc).__name__}')
            continue
        gx = np.asarray(g['x'])          # ascending
        gy = np.asarray(g['y'])          # descending
        t = np.asarray(g['time'])        # days since 1970-01-01
        # Proper calendar conversion — int(d // 365.25) drifts on leap years
        # and produced duplicate/skipped year labels.
        import datetime as _dt
        yrs = [(_dt.date(1970, 1, 1) + _dt.timedelta(days=float(d))).year
               for d in t]
        if years is None:
            years = yrs
            n_years = len(years)
            for v in VARS_T:
                acc[v] = np.full((n_years, ny, nx), np.nan, np.float32)
            for v in VARS_S:
                acc[v] = np.full((ny, nx), np.nan, np.float32)
        # Cubes can differ in year span (e.g. one starts 1982, another 1983):
        # align by YEAR VALUE, never by index.
        yr_src = {yr: k for k, yr in enumerate(yrs)}
        yr_map = [(k_dst, yr_src[yr]) for k_dst, yr in enumerate(years) if yr in yr_src]
        if len(yr_map) != len(years):
            print(f'    note: year axes differ ({yrs[0]}-{yrs[-1]} vs '
                  f'{years[0]}-{years[-1]}); aligned {len(yr_map)} common years')

        if phase_units is None:
            try:
                phase_units = g['vx_phase'].attrs.get('units', '')
                print(f'    vx_phase units: "{phase_units}"')
            except Exception:
                phase_units = ''

        # Overlap between this cube's grid and the AOI grid. AOI cell centers:
        # x0 + (i+0.5)*GRID; cube arrays are cell-centered on gx/gy.
        ix = np.round((gx - (x0 + GRID / 2)) / GRID).astype(int)
        iy = np.round(((y0 + GRID * (ny - 0.5)) - gy) / GRID).astype(int)  # row 0 = north
        mx = (ix >= 0) & (ix < nx)
        my = (iy >= 0) & (iy < ny)
        if not mx.any() or not my.any():
            print('    (no overlap)')
            continue
        sx = slice(int(np.argmax(mx)), int(len(mx) - np.argmax(mx[::-1])))
        sy = slice(int(np.argmax(my)), int(len(my) - np.argmax(my[::-1])))
        dx_ix = ix[sx]
        dy_iy = iy[sy]

        flip = 1 if dy_iy[0] < dy_iy[-1] else -1
        for v in VARS_T:
            arr = np.asarray(g[v][:, sy, sx], np.float32)
            for (k_dst, k_src) in yr_map:
                acc[v][k_dst, dy_iy.min():dy_iy.max() + 1,
                       dx_ix.min():dx_ix.max() + 1] = arr[k_src, ::flip, :]
        for v in VARS_S:
            arr = np.asarray(g[v][sy, sx], np.float32)
            acc[v][dy_iy.min():dy_iy.max() + 1, dx_ix.min():dx_ix.max() + 1] \
                = arr[::(1 if dy_iy[0] < dy_iy[-1] else -1), :]

    if years is None:
        raise SystemExit(f'{slug}: no cubes found — check AOI/tile math.')

    # ---- downsample 120 m -> 240 m (block means; halves each axis) --------
    # The full-res 43-year stack is ~180 MB — too heavy for a page load. At
    # 240 m Columbia's trunk still spans 8+ cells; block-averaging also cuts
    # per-pixel noise. nan-aware; phase uses a circular mean (day 364 and
    # day 2 must average to ~day 0, not day 183); landice = any-ice.
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

    for v in ('vx', 'vy', 'vx_amp', 'vy_amp'):
        acc[v] = nanmean22(acc[v])
    for v in ('vx_phase', 'vy_phase'):
        acc[v] = circmean22(acc[v])
    acc['landice'] = block2(acc['landice'],
                            lambda b: np.nanmax(np.nanmax(b, axis=-1), axis=-2))
    GRID_OUT = GRID * 2
    ny_out, nx_out = acc['vx'].shape[-2:]

    # ---- serialize: little-endian int16 (1 m/yr resolution), gzipped ------
    NODATA_I16 = -32768
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    order = ['vx', 'vy', 'vx_amp', 'vy_amp', 'vx_phase', 'vy_phase', 'landice']
    offsets = {}
    pos = 0
    import gzip as _gzip
    with _gzip.open(OUT_DIR / f'{slug}.bin.gz', 'wb', compresslevel=8) as f:
        for v in order:
            a = acc[v]
            i16 = np.where(np.isnan(a), NODATA_I16,
                           np.clip(np.round(a), -32000, 32000)).astype('<i2')
            offsets[v] = [pos, int(i16.size)]
            pos += i16.size
            f.write(i16.tobytes())
    # Serve-side: the Django route sends <slug>.bin.gz for the .bin URL with
    # Content-Encoding: gzip — the browser hands the JS the raw int16 buffer.

    n_ice = int(np.nansum(acc['landice'] > 0))
    header = {
        'name': name, 'center': list(center), 'zoom': zoom,
        'grid': {'epsg': 3413, 'x0': x0, 'y0_north': y0 + ny * GRID,
                 'dx': GRID_OUT, 'nx': nx_out, 'ny': ny_out},
        'years': years,
        'phase_units': phase_units or 'day of year',
        'dtype': 'int16', 'nodata': NODATA_I16, 'scale': 1,
        'offsets': offsets,   # int16 element counts into the decoded .bin
        'bin': f'{slug}.bin',
        'stats': {'ice_cells': n_ice,
                  'v_median_ice': float(np.nanmedian(
                      np.hypot(acc['vx'][-1], acc['vy'][-1])[acc['landice'] > 0]))},
    }
    (OUT_DIR / f'{slug}.json').write_text(json.dumps(header))
    gz_mb = (OUT_DIR / f'{slug}.bin.gz').stat().st_size / 1e6
    print(f'  wrote {slug}.bin.gz ({gz_mb:.1f} MB gz / {pos * 2 / 1e6:.1f} MB raw), '
          f'grid {nx_out}x{ny_out}@240m, years {years[0]}-{years[-1]}, '
          f'{n_ice} ice cells, median speed (last yr) '
          f'{header["stats"]["v_median_ice"]:.1f} m/yr')


if __name__ == '__main__':
    targets = sys.argv[1:] or list(AOIS)
    for slug in targets:
        build(slug)
