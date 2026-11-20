#!/opt/anaconda3/bin/python3
"""landform.py over the whole coverage, in blocks (Hig, 2026-11-19).

Each block is CORE m square with a MARGIN m collar; everything is computed on
the collared block and only the core is written, so a ridge, a floor or the
blur never sees a block edge closer than MARGIN. Per block, in OUT/blocks/:

    <bx>_<by>_<key>.tif    40 m, EPSG:3338, the spread values blurred with a Gaussian of sd SIGMA:
        ridge_R500   radius of the ridges around, m (geometric mean)
        ridge_n500   how that radius changes from the 500 m to the 1 km disc
        valley_F     S_v x W of the floors around, m (geometric mean; glacier-floored reaches left out)
        valley_W     floor width, m (geometric mean, same reaches)
        valley_Sv    valley gradient (geometric mean, same reaches)
    <bx>_<by>.npz          the crest points (disc 500 m) and stream points themselves, core only, Albers metres

Drainage area here is what drains to a point INSIDE its collared block; it only decides which streams are
measured (AMIN_KM2), and is not a discharge.

    landform_state.py --out DIR [--workers 3]         # resumes from DIR/done.txt
    landform_state.py --out DIR --block 37 21         # one block, for a test
    landform_state.py --out DIR --mosaic              # DIR/<key>.vrt over the blocks
"""
import argparse, math, struct, subprocess, sys, time, traceback
from multiprocessing import Pool
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / 'scoops'))
import landform as LF

CORE, MARGIN, DX, SIGMA, F = 50000.0, 5000.0, 10.0, 1000.0, 4
GLACIERS = Path('/Volumes/Nunatak/terrain_build/landform/state/glaciers_3338.wkb')
KEYS = ('ridge_R500', 'ridge_n500', 'valley_F', 'valley_W', 'valley_Sv')
_tree = _geoms = None


def glaciers():
    global _tree, _geoms
    if _tree is None:
        import shapely
        b = GLACIERS.read_bytes()
        out, i = [], 0
        while i < len(b):
            (n,) = struct.unpack_from('<I', b, i)
            out.append(b[i + 4:i + 4 + n]); i += 4 + n
        _geoms = shapely.from_wkb(out)
        _tree = shapely.STRtree(_geoms)
    return _tree, _geoms


def ice_mask(x0, y1, n):
    import shapely
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    tree, geoms = glaciers()
    hit = tree.query(shapely.box(x0, y1 - n * DX, x0 + n * DX, y1))
    if not len(hit):
        return np.zeros((n, n), bool)
    return rasterize([(geoms[i], 1) for i in hit], out_shape=(n, n), transform=from_origin(x0, y1, DX, DX), dtype='uint8').astype(bool)


def layout():
    import shapely
    import statewide as SW
    g = SW.coverage()
    w, s, e, n = g.bounds
    X0, Y1 = math.floor(w / CORE) * CORE, math.ceil(n / CORE) * CORE
    shapely.prepare(g)
    blocks = [(bx, by) for by in range(int(math.ceil((Y1 - s) / CORE))) for bx in range(int(math.ceil((e - X0) / CORE)))
              if g.intersects(shapely.box(X0 + bx * CORE, Y1 - (by + 1) * CORE, X0 + (bx + 1) * CORE, Y1 - by * CORE))]
    return X0, Y1, blocks


def run_block(job):
    X0, Y1, bx, by, out = job
    t0 = time.time()
    try:
        import statewide as SW
        m, nc = int(MARGIN / DX), int(CORE / DX)
        n = nc + 2 * m
        cx0, cy1 = X0 + bx * CORE, Y1 - by * CORE
        x0, y1 = cx0 - MARGIN, cy1 + MARGIN
        dem = SW.block_dem(x0, y1, n, n, DX, tp=512)
        sea = dem <= 0.5
        if (~sea[m:-m, m:-m]).mean() < 1e-4:
            return bx, by, 'sea', time.time() - t0
        ice = ice_mask(x0, y1, n)
        LF.RIDGE_SCALES = (500, 1000)
        rd = LF.ridges(dem, sea, ice, DX)
        rcv, area = LF.route(dem, sea)
        v = LF.valleys(dem, rcv, area, ice, sea, DX)
        sr = LF.spread_ridges(dem, rd[500], DX, ['R', 'n'])
        good = ~v['open'] & (v['ice'] <= 0.3)                   # a wall on both sides, and not a glacier's floor
        nan = lambda a: np.where(good, a, np.nan)
        sv = LF.spread_valleys(dem, rcv, v, dict(F=nan(np.maximum(v['Sv'] * v['W'], 0.1)), W=nan(np.maximum(v['W'], DX)),
                                                 Sv=nan(np.maximum(v['Sv'], 1e-4))))
        del rcv, area
        land = ~sea
        mc, ncc = m // F, nc // F
        for key, arr, log in (('ridge_R500', sr['R'], True), ('ridge_n500', sr['n'], False), ('valley_F', sv['F'], True),
                              ('valley_W', sv['W'], True), ('valley_Sv', sv['Sv'], True)):
            b = LF.blur(np.where(land, arr, np.nan), DX, SIGMA, log, F)
            b = np.where(land[::F, ::F][:b.shape[0], :b.shape[1]], b, np.nan)[mc:mc + ncc, mc:mc + ncc]
            LF.write_tif(out / 'blocks' / f'{bx}_{by}_{key}.tif', b, cx0, cy1, DX * F)
        r = rd[500]
        inr = (r['x'] >= MARGIN) & (r['x'] < MARGIN + CORE) & (r['y'] >= MARGIN) & (r['y'] < MARGIN + CORE)
        inv = (v['x'] >= MARGIN) & (v['x'] < MARGIN + CORE) & (v['y'] >= MARGIN) & (v['y'] < MARGIN + CORE)
        f32 = lambda a: a.astype(np.float32)
        np.savez_compressed(out / 'blocks' / f'{bx}_{by}.npz',
                            rx=x0 + r['x'][inr], ry=y1 - r['y'][inr], rR=f32(r['R'][inr]), rn=f32(r['n'][inr]), rice=r['ice'][inr],
                            vx=x0 + v['x'][inv], vy=y1 - v['y'][inv], vA=f32(v['A'][inv]), vSv=f32(v['Sv'][inv]), vW=f32(v['W'][inv]),
                            vopen=v['open'][inv], vice=f32(v['ice'][inv]))
        return bx, by, f'ok crests {inr.sum()} streams {inv.sum()} land {land[m:-m, m:-m].mean() * 100:.0f}%', time.time() - t0
    except Exception:
        return bx, by, 'FAILED ' + traceback.format_exc()[-600:].replace('\n', ' | '), time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--block', type=int, nargs=2)
    ap.add_argument('--at', help='lon,lat: run the block holding this point')
    ap.add_argument('--mosaic', action='store_true')
    a = ap.parse_args()
    out = Path(a.out); (out / 'blocks').mkdir(parents=True, exist_ok=True)
    if a.mosaic:
        for key in KEYS:
            lst = out / f'{key}.txt'
            lst.write_text('\n'.join(str(p) for p in sorted((out / 'blocks').glob(f'*_{key}.tif'))))
            subprocess.run(['/opt/homebrew/bin/gdalbuildvrt', '-q', '-input_file_list', str(lst), str(out / f'{key}.vrt')], check=True)
            print(key, len(lst.read_text().split()), 'blocks')
        return
    X0, Y1, blocks = layout()
    if a.at:
        from rasterio.warp import transform as tr
        lo, la = [float(k) for k in a.at.split(',')]
        (x,), (y,) = tr('EPSG:4326', 'EPSG:3338', [lo], [la])
        a.block = [int((x - X0) // CORE), int((Y1 - y) // CORE)]
    if a.block:
        print(run_block((X0, Y1, a.block[0], a.block[1], out)))
        return
    done_f = out / 'done.txt'
    done = set(tuple(int(k) for k in ln.split()[:2]) for ln in done_f.read_text().splitlines()) if done_f.exists() else set()
    jobs = [(X0, Y1, bx, by, out) for bx, by in blocks if (bx, by) not in done]
    print(f'{len(blocks)} blocks, {len(jobs)} to do, {a.workers} workers', flush=True)
    t0 = time.time()
    with Pool(a.workers, maxtasksperchild=4) as pool:
        for k, (bx, by, msg, dt) in enumerate(pool.imap_unordered(run_block, jobs), 1):
            print(f'{k}/{len(jobs)} block {bx} {by}: {msg} ({dt:.0f}s, {(time.time() - t0) / 3600:.2f} h)', flush=True)
            if not msg.startswith('FAILED'):
                with open(done_f, 'a') as f:
                    f.write(f'{bx} {by} {msg.split()[0]}\n')


if __name__ == '__main__':
    main()
