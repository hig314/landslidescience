"""Trailing-edge velocity composites for the /glaciers tracer bundles.

The annual composite cubes end at a partial final year; the image-pair
DATACUBES (datacubes/v2-updated-october2024/) are near-real-time. This
script pulls the AOI's recent image pairs and composites QUARTERLY median
vx/vy fields, appended to the bundle as <slug>_recent.bin.gz + a 'recent'
block in <slug>.json. The engine uses them past the last annual mid-year —
real observed sub-annual motion instead of the climatological seasonal fit.

Datacube layout (verified): 100 km tiles, 120 m, EPSG:3413 for Alaska,
name ITS_LIVE_vel_EPSG3413_G0120_X{cx}_Y{cy}.zarr, time dim 'mid_date',
chunks [20000, 10, 10] — optimized for point reads, so an area sweep is
chunk-column iteration. Needed chunks are PREFETCHED concurrently (the
retrying HttpZarrStore from build_glacier_tracers), then composited
per 10x10 column to bound memory.

Quarterly medians: pairs weighted equally, |v| > 20 km/yr discarded,
quarters with < MIN_PAIRS pairs per pixel -> NoData. Ice mask reuses the
main bundle's landice.

Usage: python tools/build_glacier_recent.py [slug ...]   (default: all)
"""
import datetime as dt
import gzip
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import zarr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_glacier_tracers import (AOIS, GRID, HttpZarrStore, S3_HTTP, TILE,
                                   aoi_grid, tile_center_for, _to4326)

DATACUBE_PREFIX = 'datacubes/v2-updated-october2024'
OUT_DIR = Path(__file__).resolve().parent.parent / 'data' / 'glaciers'
CUTOFF = dt.date(2024, 1, 1)     # composite quarters from here forward
MIN_PAIRS = 3                    # per-pixel pairs needed for a quarter
VMAX = 20000.0
PREFETCH_WORKERS = 16


def datacube_url(cx, cy):
    import requests
    lon, lat = _to4326.transform(cx, cy)
    cands = []
    for la in {int(lat / 10) * 10, math.floor(lat / 10) * 10}:
        for lo in {int(lon / 10) * 10, math.floor(lon / 10) * 10}:
            ns = 'N' if la >= 0 else 'S'
            ew = 'E' if lo >= 0 else 'W'
            cands.append(f'{ns}{abs(la):02d}{ew}{abs(lo):03d}')
    for dname in cands:
        url = (f'{S3_HTTP}/{DATACUBE_PREFIX}/{dname}/'
               f'ITS_LIVE_vel_EPSG3413_G0120_X{int(cx)}_Y{int(cy)}.zarr')
        try:
            if requests.head(url + '/.zgroup', timeout=20).status_code == 200:
                return url
        except requests.RequestException:
            continue
    return None


class PrefetchStore(HttpZarrStore):
    """HttpZarrStore with a shared in-memory cache; chunk keys can be
    prefetched concurrently before zarr reads them serially."""

    def __init__(self, base_url):
        super().__init__(base_url)
        self.cache = {}

    def __getitem__(self, key):
        if key in self.cache:
            v = self.cache[key]
            if v is None:
                raise KeyError(key)
            return v
        try:
            v = super().__getitem__(key)
        except KeyError:
            self.cache[key] = None
            raise
        self.cache[key] = v
        return v

    def prefetch(self, keys):
        def grab(k):
            try:
                self[k]
            except KeyError:
                pass
        with ThreadPoolExecutor(PREFETCH_WORKERS) as ex:
            list(ex.map(grab, keys))


def _mid_dates(g):
    """mid_date coordinate as numpy datetime64[D]."""
    t = np.asarray(g['mid_date'])
    units = g['mid_date'].attrs.get('units', '')
    if np.issubdtype(t.dtype, np.datetime64):
        return t.astype('datetime64[D]')
    # 'days since YYYY-MM-DD ...' (possibly fractional)
    base = np.datetime64(units.split('since')[1].strip().split()[0], 'D') \
        if 'since' in units else np.datetime64('1970-01-01', 'D')
    return (base + t.astype('timedelta64[D]')).astype('datetime64[D]')


def quarters_from(dates):
    """Sorted list of (label, start, end) quarters covering CUTOFF..max(date)."""
    out = []
    d = dt.date(CUTOFF.year, 1, 1)
    dmax = dates.max().astype(dt.date)
    while d <= dmax:
        q_end = (dt.date(d.year + (d.month + 2) // 12, ((d.month + 2) % 12) + 1, 1)
                 if d.month + 3 > 12 else dt.date(d.year, d.month + 3, 1))
        if q_end > CUTOFF:
            out.append((d.year + (d.month - 1) / 12 + 0.125, d, q_end))
        d = q_end
    return out


def build(slug):
    name, bbox, center, zoom = AOIS[slug]
    x0, y0, x1, y1 = aoi_grid(bbox)
    nx = int(round((x1 - x0) / GRID))
    ny = int(round((y1 - y0) / GRID))
    hdr_path = OUT_DIR / f'{slug}.json'
    hdr = json.loads(hdr_path.read_text())
    print(f'== {slug}: recent composites on {nx}x{ny}@120m')

    centers = set()
    cx = math.floor(x0 / TILE) * TILE + TILE / 2
    while cx < x1 + TILE / 2:
        cy = math.floor(y0 / TILE) * TILE + TILE / 2
        while cy < y1 + TILE / 2:
            centers.add((cx, cy))
            cy += TILE
        cx += TILE

    quarters = None
    acc = None      # {qi: {'vx': sum arrays...}} — filled per cube column

    for (ccx, ccy) in sorted(centers):
        url = datacube_url(ccx, ccy)
        if url is None:
            print(f'  cube X{int(ccx)}_Y{int(ccy)}: none (no ice tile) — skip')
            continue
        print(f'  cube {url.rsplit("/", 1)[-1]}')
        store = PrefetchStore(url)
        g = zarr.open(store, mode='r')
        dates = _mid_dates(g)
        recent = np.nonzero(dates >= np.datetime64(CUTOFF, 'D'))[0]
        if recent.size == 0:
            print('    no recent pairs — skip')
            continue
        i0, i1 = int(recent.min()), int(recent.max())
        print(f'    {recent.size} pairs since {CUTOFF} '
              f'(idx {i0}..{i1} of {dates.size}; newest {dates.max()})')
        if quarters is None:
            quarters = quarters_from(dates)
            nq = len(quarters)
            acc = {
                'sum_kept': None,
                'vx': np.full((nq, ny, nx), np.nan, np.float32),
                'vy': np.full((nq, ny, nx), np.nan, np.float32),
            }

        gx = np.asarray(g['x']); gy = np.asarray(g['y'])
        ix = np.round((gx - (x0 + GRID / 2)) / GRID).astype(int)
        iy = np.round(((y0 + GRID * (ny - 0.5)) - gy) / GRID).astype(int)
        mx = (ix >= 0) & (ix < nx)
        my = (iy >= 0) & (iy < ny)
        if not mx.any() or not my.any():
            print('    (no overlap)')
            continue
        sx0 = int(np.argmax(mx)); sx1 = int(len(mx) - np.argmax(mx[::-1]))
        sy0 = int(np.argmax(my)); sy1 = int(len(my) - np.argmax(my[::-1]))

        vxa, vya = g['vx'], g['vy']
        tchunk, ychunk, xchunk = vxa.chunks
        tc0, tc1 = i0 // tchunk, i1 // tchunk
        yc0, yc1 = sy0 // ychunk, (sy1 - 1) // ychunk
        xc0, xc1 = sx0 // xchunk, (sx1 - 1) // xchunk

        keys = []
        for var in ('vx', 'vy'):
            for tc in range(tc0, tc1 + 1):
                for yc in range(yc0, yc1 + 1):
                    for xc in range(xc0, xc1 + 1):
                        keys.append(f'{var}/{tc}.{yc}.{xc}')
        print(f'    prefetching {len(keys)} chunks '
              f'({tc1-tc0+1}t x {yc1-yc0+1}y x {xc1-xc0+1}x x 2 vars) ...')
        store.prefetch(keys)

        fillx = vxa.fill_value
        d_recent = dates[i0:i1 + 1]
        q_masks = [(qi, (d_recent >= np.datetime64(qs, 'D')) &
                        (d_recent < np.datetime64(qe, 'D')))
                   for qi, (qc, qs, qe) in enumerate(quarters)]

        flip = 1 if iy[sy0] < iy[sy1 - 1] else -1
        for yc in range(yc0, yc1 + 1):
            for xc in range(xc0, xc1 + 1):
                ys = slice(max(sy0, yc * ychunk), min(sy1, (yc + 1) * ychunk))
                xs = slice(max(sx0, xc * xchunk), min(sx1, (xc + 1) * xchunk))
                if ys.start >= ys.stop or xs.start >= xs.stop:
                    continue
                col_vx = np.asarray(vxa[i0:i1 + 1, ys, xs], np.float32)
                col_vy = np.asarray(vya[i0:i1 + 1, ys, xs], np.float32)
                for col in (col_vx, col_vy):
                    if fillx is not None:
                        col[col == np.float32(fillx)] = np.nan
                    col[np.abs(col) > VMAX] = np.nan
                dst_y = iy[ys][::flip] if flip == -1 else iy[ys]
                dst_x = ix[xs]
                src_rows = col_vx[:, ::flip, :] if flip == -1 else col_vx
                src_rows_y = col_vy[:, ::flip, :] if flip == -1 else col_vy
                with np.errstate(all='ignore'):
                    for qi, qm in q_masks:
                        if qm.sum() < MIN_PAIRS:
                            continue
                        cnt = np.isfinite(src_rows[qm]).sum(axis=0)
                        medx = np.nanmedian(src_rows[qm], axis=0)
                        medy = np.nanmedian(src_rows_y[qm], axis=0)
                        medx[cnt < MIN_PAIRS] = np.nan
                        medy[cnt < MIN_PAIRS] = np.nan
                        acc['vx'][qi, dst_y.min():dst_y.max() + 1,
                                  dst_x.min():dst_x.max() + 1] = medx
                        acc['vy'][qi, dst_y.min():dst_y.max() + 1,
                                  dst_x.min():dst_x.max() + 1] = medy
        store.cache.clear()

    if quarters is None:
        raise SystemExit(f'{slug}: no datacubes with recent pairs found.')

    # Drop leading/trailing empty quarters, downsample to the bundle's 240 m
    # grid (block mean), int16, gzip.
    valid_q = [qi for qi in range(len(quarters))
               if np.isfinite(acc['vx'][qi]).sum() > 500]
    if not valid_q:
        raise SystemExit(f'{slug}: no quarters with usable coverage.')
    q0, q1 = min(valid_q), max(valid_q)
    centers_q = [quarters[qi][0] for qi in range(q0, q1 + 1)]

    def block2(a):
        py = (-a.shape[-2]) % 2
        px = (-a.shape[-1]) % 2
        pad = [(0, 0)] * (a.ndim - 2) + [(0, py), (0, px)]
        a = np.pad(a, pad, constant_values=np.nan)
        s = a.shape
        a = a.reshape(s[:-2] + (s[-2] // 2, 2, s[-1] // 2, 2))
        with np.errstate(all='ignore'):
            return np.nanmean(np.nanmean(a, axis=-1), axis=-2)

    vxq = block2(acc['vx'][q0:q1 + 1]).astype(np.float32)
    vyq = block2(acc['vy'][q0:q1 + 1]).astype(np.float32)
    nyq, nxq = vxq.shape[-2:]
    if nxq != hdr['grid']['nx'] or nyq != hdr['grid']['ny']:
        raise SystemExit(f'{slug}: grid mismatch vs bundle '
                         f'({nxq}x{nyq} vs {hdr["grid"]["nx"]}x{hdr["grid"]["ny"]})')

    NODATA = -32768
    offsets, pos = {}, 0
    with gzip.open(OUT_DIR / f'{slug}_recent.bin.gz', 'wb', compresslevel=8) as f:
        for nm, a in (('vx', vxq), ('vy', vyq)):
            i16 = np.where(np.isnan(a), NODATA,
                           np.clip(np.round(a), -32000, 32000)).astype('<i2')
            offsets[nm] = [pos, int(i16.size)]
            pos += i16.size
            f.write(i16.tobytes())

    hdr['recent'] = {
        'bin': f'{slug}_recent.bin',
        'quarters': centers_q,      # decimal-year centers
        'offsets': offsets,
        'dtype': 'int16', 'nodata': NODATA, 'scale': 1,
        'built': dt.date.today().isoformat(),
    }
    hdr_path.write_text(json.dumps(hdr))
    gz_mb = (OUT_DIR / f'{slug}_recent.bin.gz').stat().st_size / 1e6
    cov = [float(np.isfinite(vxq[k]).mean()) for k in range(len(centers_q))]
    print(f'  wrote {slug}_recent.bin.gz ({gz_mb:.1f} MB), '
          f'{len(centers_q)} quarters {centers_q[0]:.2f}..{centers_q[-1]:.2f}, '
          f'coverage/quarter {[round(c, 2) for c in cov]}')


if __name__ == '__main__':
    targets = sys.argv[1:] or list(AOIS)
    for slug in targets:
        build(slug)
