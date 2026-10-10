"""Mosaic the full run's units and fill holes the unit edges cut (Hig's hole rule, fill_holes.py).

    python model/mosaic.py                      # whole survey -> full/mosaic/
    python model/mosaic.py --bbox 590 6590 600 6600 --out full/mosaic_test   # km, for a test

WHAT IS ALREADY DONE PER UNIT. apply.py fills every ENCLOSED no-data region of its buffered
1120 m site (harmonic, fill_mask = 2) before the 1 km core is cropped. What it cannot fill is a
hole that reached the edge of its site: inside the unit that looks like margin. Those are the
holes this pass fills -- the ones that are enclosed only at the scale of the whole survey.

HOLE vs MARGIN, the same rule as fill_holes.py: a no-data region connected to the outside of the
grid is margin and stays empty, however narrow its mouth. Connectivity is decided on a COARSE
grid (COARSE m cells, a cell is no-data if ANY of its fine cells is). Pooling with ANY can only
join regions, never split them, so a region called enclosed on the coarse grid is enclosed at
1 m too. The price is conservative: a hole separated from the margin by a wall of data thinner
than COARSE m counts as margin and is left empty -- the direction the rule already leans.

FILL. Harmonic (Laplace) from the rim, as fill_holes.py, vectorised. Small holes: direct solve.
Large ones: solved on the coarse grid first, upsampled as the starting guess, then conjugate
gradients at 1 m. By the maximum principle a harmonic fill never rises above its highest rim
cell nor falls below its lowest, so it cannot make a bump or a pit of its own.

OUTPUTS (EPSG:6334, 1 m, float32, nodata -9999): dtm_final.tif and fill_mask.tif (2 = harmonic
fill, from the unit pass or this one; 0 = measured), then COG copies with overviews.
Units that ended EMPTY (no model ground: edge slivers) have no dtm_final.tif and are skipped.
"""
import argparse, json, os, subprocess, sys, time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window
from scipy import ndimage, sparse
from scipy.sparse.linalg import cg, spsolve

GDAL = '/opt/homebrew/bin'
NODATA = -9999.0
COARSE = 4                  # m per coarse cell for the connectivity test
DIRECT_MAX = 1_500_000      # holes up to this many cells get a direct solve
HERE = Path(__file__).resolve().parent.parent
FULL = HERE / 'full'


def log(msg):
    print(f'{time.strftime("%H:%M:%S")} {msg}', flush=True)


def units(bbox_km=None):
    """Finished units with a surface, as (name, x_km, y_km)."""
    out = []
    for u in sorted(os.listdir(FULL / 'out')):
        if not u.startswith('u_') or u.endswith('.tmp'):
            continue
        d = FULL / 'out' / u
        if not (d / 'DONE').exists() or not (d / 'dtm_final.tif').exists():
            continue
        _, x, y = u.split('_'); x, y = int(x), int(y)
        if bbox_km and not (bbox_km[0] <= x < bbox_km[2] and bbox_km[1] <= y < bbox_km[3]):
            continue
        out.append((u, x, y))
    return out


def build_vrts(us, work):
    work.mkdir(parents=True, exist_ok=True)
    for name in ('dtm_final', 'fill_mask'):
        lst = work / f'{name}.txt'
        lst.write_text('\n'.join(str(FULL / 'out' / u / f'{name}.tif') for u, _, _ in us) + '\n')
        subprocess.run([f'{GDAL}/gdalbuildvrt', '-q', '-overwrite', '-input_file_list', str(lst),
                        str(work / f'{name}.vrt')], check=True)
    return work / 'dtm_final.vrt', work / 'fill_mask.vrt'


def coarse_nan(vrt, strip_rows):
    """ANY-pooled no-data mask on the COARSE grid, read in strips."""
    with rasterio.open(vrt) as src:
        H, W = src.height, src.width
        Hc, Wc = -(-H // COARSE), -(-W // COARSE)
        cn = np.zeros((Hc, Wc), bool)
        for r0 in range(0, H, strip_rows):
            h = min(strip_rows, H - r0)
            a = src.read(1, window=Window(0, r0, W, h), masked=False)
            nan = (a == NODATA) | ~np.isfinite(a)
            ph, pw = -(-h // COARSE) * COARSE, Wc * COARSE
            pad = np.ones((ph, pw), bool)          # padding counts as no-data: it is outside
            pad[:h, :W] = nan
            cn[r0 // COARSE: r0 // COARSE + ph // COARSE] |= pad.reshape(ph // COARSE, COARSE, Wc, COARSE).any(axis=(1, 3))
    return cn, (H, W)


def laplace_solve(z, h, guess=None):
    """Harmonic fill of the cells of h, finite neighbours of the hole as boundary values
    (4-neighbour stencil). Vectorised assembly; direct solve when small, CG otherwise."""
    idx = -np.ones(h.shape, np.int64)
    rr, cc = np.nonzero(h)
    n = rr.size
    idx[rr, cc] = np.arange(n)
    diag = np.zeros(n)
    b = np.zeros(n)
    rows, cols = [], []
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        r2, c2 = rr + dr, cc + dc
        ok = (r2 >= 0) & (r2 < h.shape[0]) & (c2 >= 0) & (c2 < h.shape[1])
        diag[ok] += 1
        k = np.nonzero(ok)[0]
        inner = h[r2[k], c2[k]]
        rows.append(k[inner]); cols.append(idx[r2[k[inner]], c2[k[inner]]])
        bnd = k[~inner]
        b[bnd] += z[r2[bnd], c2[bnd]]
    rows = np.concatenate(rows); cols = np.concatenate(cols)
    A = sparse.csr_matrix((np.r_[-np.ones(rows.size), diag], (np.r_[rows, np.arange(n)], np.r_[cols, np.arange(n)])),
                          shape=(n, n))
    if n <= DIRECT_MAX:
        return spsolve(A.tocsc(), b)
    x0 = guess[rr, cc] if guess is not None else np.full(n, b.sum() / max(diag.sum(), 1))
    M = sparse.diags(1.0 / diag)
    x, info = cg(A, b, x0=x0, M=M, rtol=1e-6, maxiter=4000)
    if info != 0:
        log(f'    cg stopped at maxiter on a {n:,}-cell hole (info {info}); using its last iterate')
    return x


def fill_hole(src, lab_c, i, sl_c, H, W):
    """Fill enclosed coarse component i at 1 m. Returns (window, values or None)."""
    r0 = max(sl_c[0].start * COARSE - 2, 0); r1 = min(sl_c[0].stop * COARSE + 2, H)
    c0 = max(sl_c[1].start * COARSE - 2, 0); c1 = min(sl_c[1].stop * COARSE + 2, W)
    win = Window(c0, r0, c1 - c0, r1 - r0)
    z = src.read(1, window=win).astype('f8')
    nan = (z == NODATA) | ~np.isfinite(z)
    z[nan] = np.nan
    # fine cells whose coarse cell belongs to this component
    comp = np.repeat(np.repeat(lab_c[r0 // COARSE:-(-r1 // COARSE), c0 // COARSE:-(-c1 // COARSE)] == i,
                               COARSE, 0), COARSE, 1)
    comp = comp[r0 % COARSE:r0 % COARSE + (r1 - r0), c0 % COARSE:c0 % COARSE + (c1 - c0)]
    h = nan & comp
    if not h.any():
        return win, None
    rim = ndimage.binary_dilation(h, np.ones((3, 3), bool)) & ~h & np.isfinite(z)
    if not rim.any():
        return win, None
    guess = None
    if h.sum() > DIRECT_MAX:
        # coarse solve as the starting guess: nearest-rim values relaxed on a 1/COARSE grid
        zc = z[::COARSE, ::COARSE].copy(); hc = h[::COARSE, ::COARSE] & ~np.isfinite(zc)
        if hc.any():
            zc[hc] = laplace_solve(np.nan_to_num(zc), hc)
        zc = np.where(np.isfinite(zc), zc, np.nanmean(z[rim]))
        guess = np.kron(zc, np.ones((COARSE, COARSE)))[:z.shape[0], :z.shape[1]]
    vals = np.full(z.shape, np.nan)
    vals[h] = laplace_solve(np.nan_to_num(z), h, guess)
    return win, vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bbox', nargs=4, type=int, metavar=('X0', 'Y0', 'X1', 'Y1'),
                    help='km, unit names inclusive-exclusive (a test area)')
    ap.add_argument('--out', default=str(FULL / 'mosaic'))
    ap.add_argument('--strip', type=int, default=1000, help='rows per read strip')
    ap.add_argument('--no-cog', action='store_true')
    a = ap.parse_args()
    out = Path(a.out); work = out / 'work'
    t0 = time.time()

    us = units(a.bbox)
    if not us:
        sys.exit('no finished units with a surface in that area')
    log(f'{len(us)} units with a surface')
    dtm_vrt, mask_vrt = build_vrts(us, work)

    cn, (H, W) = coarse_nan(dtm_vrt, a.strip)
    log(f'grid {W} x {H} (1 m); coarse {cn.shape[1]} x {cn.shape[0]} ({COARSE} m)')
    lab, n = ndimage.label(cn)
    edge = set(np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]]).tolist()) - {0}
    objs = ndimage.find_objects(lab)
    enclosed = [(i, sl) for i, sl in enumerate(objs, start=1) if sl is not None and i not in edge]
    sizes = ndimage.sum_labels(np.ones_like(cn, np.int32), lab, [i for i, _ in enclosed]) if enclosed else []
    log(f'{n:,} no-data regions; {len(enclosed):,} enclosed (the rest touch the margin)')
    big = sorted(zip(sizes, [i for i, _ in enclosed]), reverse=True)[:5]
    for s, i in big:
        log(f'   largest: region {i}, about {s * COARSE * COARSE / 1e6:.4f} km2 before the fine test')

    fills = []
    filled_cells = 0
    with rasterio.open(dtm_vrt) as src:
        for k, (i, sl) in enumerate(enclosed):
            win, vals = fill_hole(src, lab, i, sl, H, W)
            if vals is not None:
                fills.append((win, vals.astype('f4')))
                filled_cells += int(np.isfinite(vals).sum())
            if (k + 1) % 500 == 0:
                log(f'   {k + 1:,} of {len(enclosed):,} regions')
        profile = src.profile
    log(f'filled {filled_cells:,} cells ({filled_cells / 1e6:.3f} km2) in {len(fills):,} holes')

    profile.update(driver='GTiff', dtype='float32', nodata=NODATA, tiled=True, blockxsize=512, blockysize=512,
                   compress='deflate', predictor=3, bigtiff='yes', num_threads='all_cpus')
    mprof = dict(profile, dtype='uint8', nodata=None, predictor=2)
    out.mkdir(parents=True, exist_ok=True)
    tmp_dtm, tmp_mask = out / 'dtm_final.strip.tif', out / 'fill_mask.strip.tif'
    with rasterio.open(dtm_vrt) as src, rasterio.open(mask_vrt) as msrc, \
         rasterio.open(tmp_dtm, 'w', **profile) as dst, rasterio.open(tmp_mask, 'w', **mprof) as mdst:
        # fills indexed by the strips they touch
        by_strip = {}
        for win, vals in fills:
            for s in range(int(win.row_off) // a.strip, (int(win.row_off) + int(win.height) - 1) // a.strip + 1):
                by_strip.setdefault(s, []).append((win, vals))
        for s, r0 in enumerate(range(0, H, a.strip)):
            h = min(a.strip, H - r0)
            sw = Window(0, r0, W, h)
            z = src.read(1, window=sw)
            m = msrc.read(1, window=sw, boundless=True, fill_value=0)
            for win, vals in by_strip.get(s, []):
                wr0, wc0 = int(win.row_off), int(win.col_off)
                a0, a1 = max(r0, wr0), min(r0 + h, wr0 + int(win.height))
                v = vals[a0 - wr0:a1 - wr0]
                sub = z[a0 - r0:a1 - r0, wc0:wc0 + v.shape[1]]
                msub = m[a0 - r0:a1 - r0, wc0:wc0 + v.shape[1]]
                ok = np.isfinite(v)
                sub[ok] = v[ok]
                msub[ok] = 2
            dst.write(z, 1, window=sw)
            mdst.write(m, 1, window=sw)
    log('strips written')

    meta = {'units': len(us), 'grid': [W, H], 'enclosed_regions': len(enclosed), 'holes_filled': len(fills),
            'cells_filled': filled_cells, 'coarse_m': COARSE, 'seconds': round(time.time() - t0)}
    (out / 'mosaic.json').write_text(json.dumps(meta, indent=1))
    if not a.no_cog:
        for name, rs in (('dtm_final', 'AVERAGE'), ('fill_mask', 'NEAREST')):
            subprocess.run([f'{GDAL}/gdal_translate', '-q', '-of', 'COG', '-co', 'COMPRESS=DEFLATE',
                            '-co', 'PREDICTOR=' + ('3' if name == 'dtm_final' else '2'), '-co', 'BIGTIFF=YES',
                            '-co', 'NUM_THREADS=ALL_CPUS', '-co', f'OVERVIEW_RESAMPLING={rs}',
                            str(out / f'{name}.strip.tif'), str(out / f'{name}.tif')], check=True)
            (out / f'{name}.strip.tif').unlink()
        log('COGs written')
    log(f'done in {time.time() - t0:.0f} s: {json.dumps(meta)}')


if __name__ == '__main__':
    main()
