#!/opt/anaconda3/bin/python3
"""Everything the map's Permafrost analysis panel needs, built offline.

    export_web.py            # all three
    export_web.py --grids    # 1. the 1 km grids for the patch endpoint
    export_web.py --values   # 2. per-landslide values
    export_web.py --density  # 3. terrain joint densities

1. data/permafrost/grid.json + <field>.npy  (Float32, EPSG:3338, 1 km)
   The published 1 km cells, read by inventory/permafrost.py when a reader
   samples a patch on the map: elev, north, maat, pzi, magt, std, prob,
   magt_filled (Obu inpainted over ice), gruber_b, obu_b, obu_c. ~260 MB,
   rsynced to the droplet like the other data/ products (not in git).
2. inventory/static/inventory/pf_values.json
   {landslide_id: {pzi, prob, maat, magt, pzi60, prob60, maat60, magt60}}
   sampled at each landslide's polygon centroid (data/landslide_centroids_3338.json,
   same file tools/sample_susc.py uses). Null where no value.
3. inventory/static/inventory/pf_density.json
   {pairs: {"pzi|prob": {...}, "maat|magt": {...}, "pzi60|prob60": {...},
   "maat60|magt60": {...}}} -- 2-D histograms of ALL land in each pair's value
   space (row-major [y*xsize + x]), with the axis ranges. The backdrop that
   turns "landslides are here" into "landslides are here more than terrain
   is", as in the susceptibility scatter.
"""
import argparse, json, sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

ROOT = Path(__file__).resolve().parents[2]
B = Path('/Volumes/Nunatak/permafrost_build')
GRIDS = ROOT / 'data' / 'permafrost'
STATIC = ROOT / 'inventory' / 'static' / 'inventory'
CENTROIDS = ROOT / 'data' / 'landslide_centroids_3338.json'

FIELDS_1KM = {
    'elev': B / 'src/elev_1km.tif', 'north': B / 'src/northness_1km.tif',
    'maat': B / 'src/gruber_maat_3338_1km.tif', 'pzi': B / 'src/gruber_pzi_3338_1km.tif',
    'magt': B / 'src/obu_magtm_3338_1km.tif', 'std': B / 'src/obu_magtstd_3338_1km.tif',
    'prob': B / 'src/obu_perprob_3338_1km.tif', 'magt_filled': B / 'kfit/obu_magt_filled.tif',
    'gruber_b': B / 'kfit/gruber_b.tif', 'obu_b': B / 'kfit/obu_b.tif', 'obu_c': B / 'kfit/obu_c.tif',
}
FIELDS_60M = {'pzi60': B / 'out/gruber_pzi60.tif', 'prob60': B / 'out/obu_prob60.tif',
              'maat60': B / 'out/gruber_maat60.tif', 'magt60': B / 'out/obu_magt60.tif'}
# axis ranges for the densities: fractions on [0, 1], temperatures in C
AXES = {'pzi': (0, 1, 50), 'prob': (0, 1, 50), 'maat': (-24, 8, 64), 'magt': (-16, 8, 48)}
PAIRS = [('pzi', 'prob'), ('maat', 'magt')]


def rd(path):
    with rasterio.open(path) as d:
        a = d.read(1).astype(np.float32)
        a[(a == d.nodata) | ~np.isfinite(a)] = np.nan
        return a, d


def grids():
    GRIDS.mkdir(parents=True, exist_ok=True)
    meta = None
    for k, p in FIELDS_1KM.items():
        a, d = rd(p)
        if meta is None:
            meta = {'crs': 'EPSG:3338', 'width': d.width, 'height': d.height,
                    'transform': list(d.transform)[:6], 'fields': list(FIELDS_1KM),
                    'curve': json.loads((B / 'kfit/pzi_curve.json').read_text())}
        assert (d.width, d.height) == (meta['width'], meta['height']), k
        np.save(GRIDS / f'{k}.npy', a)
        print(f'  {k:12s} {np.isfinite(a).sum():,} valid')
    (GRIDS / 'grid.json').write_text(json.dumps(meta))
    print('wrote', GRIDS)


def values():
    pts = json.loads(CENTROIDS.read_text())
    out = {}
    srcs = {k: rasterio.open(p) for k, p in {**{k: FIELDS_1KM[k] for k in ('pzi', 'prob', 'maat', 'magt')}, **FIELDS_60M}.items()}
    xy = [(x, y) for _, x, y in pts]
    vals = {k: np.array([v[0] for v in d.sample(xy)], dtype=float) for k, d in srcs.items()}
    for k, d in srcs.items():
        vals[k][(vals[k] == d.nodata) | ~np.isfinite(vals[k])] = np.nan
    for i, (lid, _, _) in enumerate(pts):
        out[str(int(lid))] = {k: (None if np.isnan(vals[k][i]) else round(float(vals[k][i]), 4)) for k in srcs}
    (STATIC / 'pf_values.json').write_text(json.dumps(out, separators=(',', ':')))
    n = sum(1 for v in out.values() if v['pzi'] is not None)
    print(f'  {len(out)} landslides, {n} with a 1 km value; wrote pf_values.json')


def hist2d(x, y, ax, ay):
    ok = np.isfinite(x) & np.isfinite(y)
    h, _, _ = np.histogram2d(x[ok], y[ok], bins=[ax[2], ay[2]], range=[[ax[0], ax[1]], [ay[0], ay[1]]])
    return h.T          # row-major [y][x]


def density():
    pairs = {}
    for kx, ky in PAIRS:
        ax, ay = AXES[kx], AXES[ky]
        x, _ = rd(FIELDS_1KM[kx]); y, _ = rd(FIELDS_1KM[ky])
        h = hist2d(x.ravel(), y.ravel(), ax, ay)
        pairs[f'{kx}|{ky}'] = pack(h, ax, ay, kx, ky)
        print(f'  {kx}|{ky}: {int(h.sum()):,} cells')
        # 60 m, chunked
        dx = rasterio.open(FIELDS_60M[kx + '60']); dy = rasterio.open(FIELDS_60M[ky + '60'])
        h = np.zeros((ay[2], ax[2]))
        for r0 in range(0, dx.height, 2048):
            w = Window(0, r0, dx.width, min(2048, dx.height - r0))
            a = dx.read(1, window=w).astype(np.float32); b = dy.read(1, window=w).astype(np.float32)
            a[a == dx.nodata] = np.nan; b[b == dy.nodata] = np.nan
            h += hist2d(a.ravel(), b.ravel(), ax, ay)
        pairs[f'{kx}60|{ky}60'] = pack(h, ax, ay, kx + '60', ky + '60')
        print(f'  {kx}60|{ky}60: {int(h.sum()):,} cells')
    (STATIC / 'pf_density.json').write_text(json.dumps({'pairs': pairs}, separators=(',', ':')))
    print('wrote pf_density.json')


def pack(h, ax, ay, kx, ky):
    return {'x': kx, 'y': ky, 'xsize': ax[2], 'ysize': ay[2], 'xmin': ax[0], 'xmax': ax[1],
            'ymin': ay[0], 'ymax': ay[1], 'grid': [int(v) for v in h.ravel()],
            'max': int(h.max()), 'total': int(h.sum())}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--grids', action='store_true'); ap.add_argument('--values', action='store_true')
    ap.add_argument('--density', action='store_true')
    a = ap.parse_args()
    if not (a.grids or a.values or a.density):
        a.grids = a.values = a.density = True
    if a.grids: print('== grids'); grids()
    if a.values: print('== values'); values()
    if a.density: print('== density'); density()
