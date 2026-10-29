#!/opt/anaconda3/bin/python3
"""Vintage-zone seeds for every joint box from the v7 bay product (composite/kbay_r*c*/out/, 2026-10-18):
  sea.tif   the sea: cells of bodies the water stage kinded tidal/sea, plus the tidal lens (fill_mask 7)  -> beach zones
  chan.tif  channel cells (fill_mask 6)                                                                  -> floodplain zones
  eta.tif   the observed water surface over the channel (water_z.tif where chan)                          -> floodplain hand
Each v7 box is read over its crop extent (the margins overlap), pasted into one bay-wide grid at 2 m, then cut to every
joint box's build extent: lidar_build/kbay_joint/seeds/<box>/{sea,chan,eta}.tif.  The seeds are priors for the zones
only (where a survey takes precedence), never data.   make_seeds.py [--v7 composites/kbay_2023/boxes.json]
"""
import argparse, json, sys
from pathlib import Path
import numpy as np, rasterio
from rasterio.transform import from_origin
from rasterio.windows import from_bounds
HERE = Path(__file__).resolve().parent; TL = HERE.parents[1]
sys.path.insert(0, str(TL))
from topobathy.water import TIDAL_KINDS, load_bodies
WORK = Path('/Volumes/Nunatak/lidar_build/composite'); SEEDS = Path('/Volumes/Nunatak/lidar_build/kbay_joint/seeds')
RES = 2.0


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--v7', default=str(TL / 'composites' / 'kbay_2023' / 'boxes.json'))
    a = ap.parse_args()
    v7 = json.load(open(a.v7)); new = json.load(open(HERE / 'boxes.json'))
    X0 = min(b['crop'][0] for b in v7); Y0 = min(b['crop'][1] for b in v7); X1 = max(b['crop'][2] for b in v7); Y1 = max(b['crop'][3] for b in v7)
    H, W = int((Y1 - Y0) / RES), int((X1 - X0) / RES)
    sea = np.zeros((H, W), np.uint8); chan = np.zeros((H, W), np.uint8); eta = np.full((H, W), np.nan, np.float32)
    for b in v7:
        d = WORK / b['id'] / 'out'
        if not (d / 'water_bodies.tif').exists(): print(b['id'], 'no v7 outputs, skipped'); continue
        x0, y0, x1, y1 = b['crop']
        def rd(name, dt=np.float32):
            with rasterio.open(d / name) as s:
                a_ = s.read(1, window=from_bounds(x0, y0, x1, y1, s.transform), boundless=True, fill_value=s.nodata if s.nodata is not None else 0)
                return a_.astype(dt), s.nodata
        bid, _ = rd('water_bodies.tif', np.int32); fm, _ = rd('fill_mask.tif', np.int32); wz, nd = rd('water_z.tif')
        if nd is not None: wz[wz == nd] = np.nan
        try: kinds = {int(k): p['kind'] for k, p in load_bodies(d)[1].items()}
        except Exception as e: print(b['id'], 'bodies:', e); kinds = {}
        mx = int(bid.max()) if bid.size else 0; lut = np.zeros(mx + 1, bool)
        for k, v in kinds.items():
            if 0 < k <= mx and v in TIDAL_KINDS: lut[k] = True
        s_ = lut[np.clip(bid, 0, mx)] | (fm == 7); c_ = fm == 6
        r0 = int((Y1 - y1) / RES); c0 = int((x0 - X0) / RES); h, w = s_.shape
        sea[r0:r0 + h, c0:c0 + w] |= s_.astype(np.uint8); chan[r0:r0 + h, c0:c0 + w] |= c_.astype(np.uint8)
        e_ = eta[r0:r0 + h, c0:c0 + w]; m = c_ & np.isfinite(wz); e_[m] = wz[m]
        print(b['id'], f'sea {s_.sum() * 4 / 1e6:.1f} km2 chan {c_.sum() * 4 / 1e6:.2f} km2', flush=True)
    T = from_origin(X0, Y1, RES, RES)
    for nb in new:
        x0, y0, x1, y1 = nb['build']; d = SEEDS / nb['id']; d.mkdir(parents=True, exist_ok=True)
        r0 = int((Y1 - y1) / RES); r1 = int((Y1 - y0) / RES); c0 = int((x0 - X0) / RES); c1 = int((x1 - X0) / RES)
        rr0, rr1, cc0, cc1 = max(r0, 0), min(r1, H), max(c0, 0), min(c1, W)
        h, w = r1 - r0, c1 - c0
        prof = dict(driver='GTiff', height=h, width=w, count=1, crs='EPSG:6334', transform=from_origin(x0, y1, RES, RES), compress='deflate', tiled=True)
        for name, arr, dt, nd in (('sea', sea, 'uint8', 0), ('chan', chan, 'uint8', 0), ('eta', eta, 'float32', -9999.0)):
            o = np.full((h, w), nd, dtype=dt)
            if rr1 > rr0 and cc1 > cc0:
                sub = arr[rr0:rr1, cc0:cc1]
                if dt == 'float32': sub = np.where(np.isfinite(sub), sub, nd)
                o[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] = sub
            with rasterio.open(d / f'{name}.tif', 'w', **dict(prof, dtype=dt, nodata=nd)) as w_: w_.write(o, 1)
    print(f'seeds for {len(new)} boxes in {SEEDS}')


if __name__ == '__main__':
    main()
