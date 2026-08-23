#!/usr/bin/env python3
"""Characterize confirmed-real InSAR signals from point sampling, and compare
against the automated velocity mosaic.

Sites: Matanuska Narrows Instability (id 100) and Columbia Fjord A (id 192)
— both carry independently confirmed motion, so they are truth cases for the
question: does careful inspection of DISP-S1 point time series characterize
the signal better than the rasterized velocity mosaic?

Per site:
  1. Probe a ~150 m grid inside the polygon (cap 30 pts) + a control ring
     300–900 m outside (cap 12). Every fetch shares the click-tool cache.
  2. Per-point LOS rates (per-stack offsets), both tracks. Coverage fraction
     is reported — patchiness is a finding, not a nuisance.
  3. The mosaic's answer at the same cells: OPERA value tiles (z12, 8-bit,
     ±30 mm/yr; the same product our map overlay shows), gray -> mm/yr.
  4. Figures: rate maps (asc/desc, in-polygon vs ring), point-vs-raster
     scatter, and representative time series (fastest coherent point vs a
     ring control).

Reads data/insar_power/sites.json (polygons exported from PostGIS).
Outputs to data/insar_power/site_<name>*.png + a stats summary on stdout.
"""
import json
import math
import time
import urllib.request
from io import BytesIO
from pathlib import Path

import numpy as np
from shapely.geometry import shape, Point

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / 'data' / 'insar_ts_cache'
OUT = ROOT / 'data' / 'insar_power'
UPSTREAM = 'https://d2qmcvu7qty7vn.cloudfront.net/timeseries'
TILE_UPSTREAM = 'https://d3g9emy65n853h.cloudfront.net/main/{track}/vel/{z}/{x}/{y}.png'
BUCKET = 'asf-cumulus-prod-opera-products'
GRID_IN_M = 150
RING_M = (300, 900)
MAX_IN, MAX_RING = 30, 12
Z = 12


def fetch_ts(lat, lon, direction):
    key = f'{direction}_{round(lat,4):.4f}_{round(lon,4):.4f}.json'
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / key
    if p.exists():
        return json.loads(p.read_text())
    body = json.dumps({'wkt': f'POINT({round(lon,4)} {round(lat,4)})',
                       'bucket': BUCKET, 'polarization': 'VV',
                       'flightDirection': direction.upper()}).encode()
    req = urllib.request.Request(UPSTREAM, data=body, method='POST',
                                 headers={'User-Agent': 'landslidescience-site-char/1',
                                          'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = json.loads(r.read().decode())
    except Exception:
        raw = {}
    stacks = {}
    for rec in (raw or {}).values():
        if not isinstance(rec, dict):
            continue
        ref, sec = rec.get('reference_datetime'), rec.get('secondary_datetime')
        d = rec.get('short_wavelength_displacement')
        if ref and sec and d is not None:
            stacks.setdefault(ref, []).append([sec, round(float(d) * 1000, 3)])
    out = {'series': [{'ref': k, 'points': sorted(v)} for k, v in sorted(stacks.items())],
           'n': sum(len(v) for v in stacks.values())}
    p.write_text(json.dumps(out))
    time.sleep(0.8)
    return out


def rate_fit(ts):
    """Rate (mm/yr) with per-stack offsets + residual sigma; None if <15 epochs."""
    t, d, sid = [], [], []
    for i, st in enumerate(ts['series']):
        for sec, mm in st['points']:
            t.append(np.datetime64(sec[:10]).astype('datetime64[D]').astype(float) / 365.25)
            d.append(mm); sid.append(i)
    if len(t) < 15:
        return None
    t = np.array(t); t -= t.min()
    d = np.array(d); sid = np.array(sid)
    stacks = np.unique(sid)
    A = np.zeros((len(t), 1 + len(stacks)))
    A[:, 0] = t
    for j, s_ in enumerate(stacks):
        A[sid == s_, 1 + j] = 1
    coef, res, rank, _ = np.linalg.lstsq(A, d, rcond=None)
    resid = d - A @ coef
    return {'rate': float(coef[0]), 'sigma': float(np.std(resid)),
            'n': len(t), 't': t, 'd': d, 'sid': sid}


_tile_cache = {}


def mosaic_value(lat, lon, track):
    """OPERA velocity mosaic value (mm/yr) at a point, or None (no coverage)."""
    n = 2 ** Z
    xf = (lon + 180) / 360 * n
    yf = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    tx, ty = int(xf), int(yf)
    key = (track, tx, ty)
    if key not in _tile_cache:
        url = TILE_UPSTREAM.format(track=track, z=Z, x=tx, y=ty)
        try:
            from PIL import Image
            req = urllib.request.Request(url, headers={'User-Agent': 'landslidescience-site-char/1'})
            with urllib.request.urlopen(req, timeout=30) as r:
                img = np.array(Image.open(BytesIO(r.read())).convert('LA'))
            _tile_cache[key] = img
            time.sleep(0.3)
        except Exception:
            _tile_cache[key] = None
    img = _tile_cache[key]
    if img is None:
        return None
    px = int((xf - tx) * 256); py = int((yf - ty) * 256)
    v, a = img[min(py, 255), min(px, 255)]
    if a == 0 or v == 0:
        return None
    return (v - 1) / 254 * 60 - 30


def probe_points(geom):
    g = shape(geom)
    minx, miny, maxx, maxy = g.bounds
    dlat = GRID_IN_M / 111000.0
    dlon = GRID_IN_M / (111000.0 * math.cos(math.radians((miny + maxy) / 2)))
    inside, ring = [], []
    lat = miny
    while lat <= maxy:
        lon = minx
        while lon <= maxx:
            if g.contains(Point(lon, lat)):
                inside.append((lat, lon))
            lon += dlon
        lat += dlat
    # ring: radial spokes from centroid
    c = g.centroid
    for ang in range(0, 360, 30):
        for rm in (RING_M[0], (RING_M[0] + RING_M[1]) / 2, RING_M[1]):
            la = c.y + rm * math.cos(math.radians(ang)) / 111000.0
            lo = c.x + rm * math.sin(math.radians(ang)) / (111000.0 * math.cos(math.radians(c.y)))
            if not g.buffer(0.002).contains(Point(lo, la)):
                ring.append((la, lo))
    rng = np.random.default_rng(1)
    if len(inside) > MAX_IN:
        inside = [inside[i] for i in rng.choice(len(inside), MAX_IN, replace=False)]
    if len(ring) > MAX_RING:
        ring = [ring[i] for i in rng.choice(len(ring), MAX_RING, replace=False)]
    return inside, ring


def characterize(name, site):
    print(f'\n===== {name} (landslide id {site["id"]}) =====')
    inside, ring = probe_points(site['geom'])
    print(f'probing {len(inside)} in-polygon + {len(ring)} ring points')
    rows = []
    for group, pts in (('in', inside), ('ring', ring)):
        for lat, lon in pts:
            row = {'group': group, 'lat': lat, 'lon': lon}
            for dirn in ('ascending', 'descending'):
                fit = rate_fit(fetch_ts(lat, lon, dirn))
                row[dirn] = fit
                row[f'{dirn}_mosaic'] = mosaic_value(lat, lon,
                                                     'asc' if dirn == 'ascending' else 'desc')
            rows.append(row)

    for group in ('in', 'ring'):
        sub = [r for r in rows if r['group'] == group]
        for dirn in ('ascending', 'descending'):
            rates = [r[dirn]['rate'] for r in sub if r[dirn]]
            cov = len(rates) / len(sub) if sub else 0
            if rates:
                print(f'  {group:4} {dirn:10}: coverage {cov:4.0%}  rate median '
                      f'{np.median(rates):+6.1f}  IQR [{np.percentile(rates,25):+6.1f},'
                      f'{np.percentile(rates,75):+6.1f}] mm/yr  (n={len(rates)})')
            else:
                print(f'  {group:4} {dirn:10}: coverage {cov:4.0%}  — no usable series')

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from shapely.geometry import shape as shp
    g = shp(site['geom'])

    fig, axes = plt.subplots(2, 2, figsize=(15, 11))
    for j, dirn in enumerate(('ascending', 'descending')):
        ax = axes[0][j]
        if g.geom_type == 'MultiPolygon':
            for part in g.geoms:
                ax.plot(*part.exterior.xy, color='k', lw=1)
        else:
            ax.plot(*g.exterior.xy, color='k', lw=1)
        vals = [(r['lon'], r['lat'], r[dirn]['rate']) for r in rows if r[dirn]]
        nov = [(r['lon'], r['lat']) for r in rows if not r[dirn]]
        if vals:
            xs, ys, cs = zip(*vals)
            sc = ax.scatter(xs, ys, c=cs, cmap='PRGn', vmin=-30, vmax=30, s=60,
                            edgecolors='k', linewidths=0.4)
            plt.colorbar(sc, ax=ax, shrink=0.8, label='point rate (mm/yr)')
        if nov:
            xs, ys = zip(*nov)
            ax.scatter(xs, ys, marker='x', c='#bbb', s=30)
        ax.set_title(f'{name} — {dirn} point rates (x = no coverage)')
        ax.set_aspect(1 / math.cos(math.radians(g.centroid.y)))

    ax = axes[1][0]
    for dirn, mk in (('ascending', 'o'), ('descending', 's')):
        pv = [(r[f'{dirn}_mosaic'], r[dirn]['rate']) for r in rows
              if r[dirn] and r[f'{dirn}_mosaic'] is not None]
        if pv:
            mx, py_ = zip(*pv)
            ax.scatter(mx, py_, marker=mk, alpha=0.7, label=f'{dirn} (n={len(pv)})')
    lim = 32
    ax.plot([-lim, lim], [-lim, lim], 'k:', lw=1)
    ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim)
    ax.set_xlabel('mosaic value (mm/yr)'); ax.set_ylabel('point time-series rate (mm/yr)')
    ax.legend(); ax.set_title('point fit vs automated mosaic, same cells')

    ax = axes[1][1]
    best = None
    for r in rows:
        if r['group'] == 'in' and r['ascending']:
            if best is None or abs(r['ascending']['rate']) > abs(best['ascending']['rate']):
                best = r
    ctrl = next((r for r in rows if r['group'] == 'ring' and r['ascending']), None)
    # Colorblind-safe: series differ by SHAPE and FILL, not hue alone
    # (Okabe-Ito blue + open black triangles; Hig is colorblind).
    for r, lbl, kw in ((best, 'fastest in-polygon',
                        dict(marker='o', ms=5, color='#0072B2', ls='none')),
                       (ctrl, 'ring control',
                        dict(marker='^', ms=6, mfc='none', color='#000000',
                             mew=1.1, ls='none'))):
        if not r:
            continue
        f = r['ascending']
        for s_ in np.unique(f['sid']):
            m = f['sid'] == s_
            ax.plot(f['t'][m] + 2016.5, f['d'][m],
                    label=lbl if s_ == f['sid'].min() else None, **kw)
    ax.set_xlabel('year'); ax.set_ylabel('LOS displacement (mm)')
    ax.legend(); ax.set_title('ascending time series (dots only; stacks have own datums)')
    plt.tight_layout()
    out = OUT / f'site_{name}.png'
    plt.savefig(out, dpi=110, bbox_inches='tight')
    print(f'  wrote {out}')


def main():
    sites = json.load(open(OUT / 'sites.json'))
    for name, site in sites.items():
        characterize(name, site)


if __name__ == '__main__':
    main()
