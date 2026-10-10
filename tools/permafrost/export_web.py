#!/opt/anaconda3/bin/python3
"""The 1 km grids behind the permafrost patch detail, built offline.

    export_web.py            # (= --grids)
    export_web.py --grids

data/permafrost/grid.json + <field>.npy  (Float32, EPSG:3338, 1 km)
   The published 1 km cells, read by inventory/permafrost.py when the
   analysis panel's "Permafrost detail" reads a sample circle: elev, north,
   maat, pzi, magt, std, prob, magt_filled (Obu inpainted over ice),
   gruber_b, obu_b, obu_c. ~260 MB, rsynced to the droplet like the other
   data/ products (not in git).

The per-landslide values and the terrain densities this script also wrote
until 2026-10-10 (pf_values.json, pf_density.json) are superseded by the
quantity cube -- tools/analysis/ (every axis, one sampler, densities on
demand from the server).
"""
import argparse, json, sys
from pathlib import Path

import numpy as np
import rasterio

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


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--grids', action='store_true')
    ap.parse_args()
    print('== grids'); grids()
