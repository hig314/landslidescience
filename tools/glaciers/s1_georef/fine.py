"""Last step of a placement: the same consensus match (match2.py), on a
simulation built for this frame at the video's own pixel (10 m Web Mercator)
from Mapterhorn z13 (3DEP in Alaska), so the video is not resampled at all.

match2's search runs on a 20 m-ground (about 40 m mercator) simulation and
refines to a sub-pixel peak there; this re-runs it within PAD of that
placement on a grid four times finer. The shift it finds is reported, and a
shift near PAD means the coarse placement was not the right one to refine.

usage: fine.py ID M2.json OUT.json [--pad-m 1500]
OUT.json: M2.json's best with bounds_3857 replaced and {coarse_bounds, shift_m}."""
import argparse, json, math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np, cv2
import match2
from sim import load_dem, simulate

ROOT = Path('/Volumes/Nunatak/glacier_s1_georef')
R = 6378137.0


def lonlat(x, y):
    return math.degrees(x / R), math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('id'); ap.add_argument('m2'); ap.add_argument('out')
    ap.add_argument('--pad-m', type=float, default=1500.0, help='search radius, mercator m')
    a = ap.parse_args()
    d = ROOT / 'v' / a.id
    st = np.load(d / 'stack.npz')
    best = json.load(open(a.m2))['best']
    b = best['bounds_3857']; pad = a.pad_m
    lo0, la0 = lonlat(b[0] - pad, b[1] - pad); lo1, la1 = lonlat(b[2] + pad, b[3] + pad)
    k = 1 / math.cos(math.radians((la0 + la1) / 2))
    cache = ROOT / 'ref' / ('%s_fine.npz' % a.id)
    if cache.exists() and json.loads(str(np.load(cache)['box'])) == [lo0, la0, lo1, la1]:
        ref = dict(np.load(cache))
    else:
        # Mapterhorn z13 (3DEP in Alaska, ~5 m ground at 60 N); the 3DEP
        # context mosaic on Nunatak leaves out much of the coast (Hubbard: 64 %)
        z, tr, k = load_dem(lo0, la0, lo1, la1, match2.MERC / k, source='mapterhorn', z=13)   # pixel = 10 mercator m
        sim = simulate(z, match2.MERC / k)
        ref = dict(dem=z, transform=np.array(tr)[:6], **sim)
        np.savez_compressed(cache, box=json.dumps([lo0, la0, lo1, la1]), **ref)
    T = ref['transform']
    nccs, (w, h), px = match2.score(st, ref, best['orbit'], 1, None, best['water'])
    S = sum(match2.WEIGHT[p] * match2.zmap(r) for p, r in zip(match2.PRODS, nccs))
    _, mx, _, (bx, by) = cv2.minMaxLoc(S)
    sx, sy = match2.subpix(S, bx, by)
    X0 = T[2] + sx * px; Y1 = T[5] - sy * px
    nb = [X0, Y1 - h * px, X0 + w * px, Y1]
    shift = math.hypot(nb[0] - b[0], nb[3] - b[3]) / k                   # ground m
    out = dict(best, bounds_3857=nb, coarse_bounds=b, shift_m=round(shift, 1), z_fine10=round(float(mx), 2),
               ncc10={p: round(float(np.nan_to_num(r[by, bx])), 3) for p, r in zip(match2.PRODS, nccs)},
               edge=bool(min(sx, sy, S.shape[1] - 1 - sx, S.shape[0] - 1 - sy) < 3))
    json.dump(out, open(a.out, 'w'), indent=1)
    print(json.dumps({k_: out[k_] for k_ in ('shift_m', 'z_fine10', 'ncc10', 'edge')}))


if __name__ == '__main__':
    main()
