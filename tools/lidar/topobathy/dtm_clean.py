#!/opt/anaconda3/bin/python3
"""A DTM with no point cloud as observations: keep only what was measured.

    dtm_clean.py DEM --out DIR --name chugachmiut [--win x0 y0 x1 y1] [--block 4000] [--halo 200]
        [--facet-tol 0.001 --facet-min-m2 400] [--flat-range 0.01 --flat-min-m2 2000 --min-level 0.5 --sea-tol 0.05]

Writes DIR/<name>_clean.tif (the DEM with nodata where it is not a measurement) and DIR/<name>_mask.tif (uint8:
0 kept, 1 TIN facet, 2 hydro-flattened water, 3 the sea at 0 m) plus DIR/<name>_clean.json (areas, parameters).
The cleaned DEM then enters topobathy/joint.py as a GRID source (cls ground, derived: true, sigma_m ~0.3): one
observation per cell, calibrated to the measured lidar it overlaps by the fuse's smooth derived_adjust field,
never able to reject a measured source. The holes it leaves (lakes, facets) are what the joint layer's hole rule
and the fill are for: a hydro-flattened lake becomes a no-return hole with a level rim, i.e. water.

Why two tests (TOPOBATHY_PLAN.md "A point-first build", item 3): a vendor bridges its data gaps with a TIN, planar
to the millimetre -- the Laplacian is ~0 inside a facet (axes.facets), and a facet of 400 m2 or more is a gap, not
ground; a hydro-flattened lake is a patch constant to the centimetre (flat_water.py), which no real ground is.
Blocks with a halo bound the memory (the Chugachmiut mosaic is 33,629 x 29,040 cells); a patch is judged by its
area within the halo'd block, so the halo must exceed the facet and flat-patch sizes that matter (200 cells = 200 m
at 1 m).
"""
import argparse, json, time
from pathlib import Path
import numpy as np
import rasterio
from rasterio.windows import Window
from scipy import ndimage


def facet_mask(z, valid, res, tol, min_m2):
    zz = np.nan_to_num(np.where(valid, z, np.nan), nan=0.0).astype(np.float64)
    lap = np.abs(ndimage.laplace(zz))
    flat = valid & (lap < tol)
    lab, n = ndimage.label(flat)
    if n == 0: return np.zeros(z.shape, bool)
    area = ndimage.sum(np.ones(lab.shape, np.float32), lab, np.arange(1, n + 1)) * res * res
    big = np.zeros(n + 1, bool); big[1:] = area >= min_m2
    m = big[lab]
    return ndimage.binary_dilation(m, iterations=1) & valid     # facet edges are just as far from data


def flat_water_mask(z, valid, res, rng, min_m2, min_level, sea_tol):
    """2 = hydro-flattened water (3x3 range < rng, patch >= min_m2, level > min_level), 3 = the sea (|level| < sea_tol)."""
    zz = np.where(valid, z, np.nan)
    mx = ndimage.maximum_filter(np.nan_to_num(zz, nan=-1e9), size=3); mn = ndimage.minimum_filter(np.nan_to_num(zz, nan=1e9), size=3)
    flat = valid & ((mx - mn) < rng)
    lab, n = ndimage.label(flat)
    out = np.zeros(z.shape, np.uint8)
    if n == 0: return out
    idx = np.arange(1, n + 1)
    area = ndimage.sum(np.ones(lab.shape, np.float32), lab, idx) * res * res
    lev = ndimage.median(zz, lab, idx)
    code = np.zeros(n + 1, np.uint8)
    keep = area >= min_m2
    code[1:][keep & (np.abs(lev) < sea_tol)] = 3
    code[1:][keep & (np.abs(lev) >= sea_tol) & (lev > min_level)] = 2
    return code[lab]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dem'); ap.add_argument('--out', required=True); ap.add_argument('--name', required=True)
    ap.add_argument('--win', type=float, nargs=4, metavar=('X0', 'Y0', 'X1', 'Y1'))
    ap.add_argument('--block', type=int, default=4000); ap.add_argument('--halo', type=int, default=200)
    ap.add_argument('--facet-tol', type=float, default=1e-3); ap.add_argument('--facet-min-m2', type=float, default=400.0)
    ap.add_argument('--flat-range', type=float, default=0.01); ap.add_argument('--flat-min-m2', type=float, default=2000.0)
    ap.add_argument('--min-level', type=float, default=0.5); ap.add_argument('--sea-tol', type=float, default=0.05)
    ap.add_argument('--no-facets', action='store_true')
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True); t0 = time.time()
    with rasterio.open(a.dem) as s:
        res = s.res[0]; nd = s.nodata
        if a.win:
            r0, c0 = s.index(a.win[0], a.win[3]); r1, c1 = s.index(a.win[2], a.win[1])
            r0, c0 = max(r0, 0), max(c0, 0); r1, c1 = min(r1, s.height), min(c1, s.width)
        else:
            r0, c0, r1, c1 = 0, 0, s.height, s.width
        H, W = r1 - r0, c1 - c0
        prof = dict(s.profile, height=H, width=W, transform=s.window_transform(Window(c0, r0, W, H)), compress='deflate', tiled=True,
                    blockxsize=512, blockysize=512, BIGTIFF='IF_SAFER')
        prof.pop('predictor', None)
        pz = dict(prof, dtype='float32', nodata=-9999.0); pm = dict(prof, dtype='uint8', nodata=255)
        counts = np.zeros(4, np.int64)
        with rasterio.open(out / f'{a.name}_clean.tif', 'w', **pz) as dz, rasterio.open(out / f'{a.name}_mask.tif', 'w', **pm) as dm:
            for br in range(0, H, a.block):
                for bc in range(0, W, a.block):
                    hr0, hc0 = max(br - a.halo, 0), max(bc - a.halo, 0)
                    hr1, hc1 = min(br + a.block + a.halo, H), min(bc + a.block + a.halo, W)
                    z = s.read(1, window=Window(c0 + hc0, r0 + hr0, hc1 - hc0, hr1 - hr0)).astype(np.float32)
                    valid = np.isfinite(z) & (z > -1e30)
                    if nd is not None: valid &= z != nd
                    m = np.zeros(z.shape, np.uint8)
                    if valid.any():
                        fw = flat_water_mask(z, valid, res, a.flat_range, a.flat_min_m2, a.min_level, a.sea_tol)
                        m[fw > 0] = fw[fw > 0]
                        if not a.no_facets:
                            fm = facet_mask(z, valid & (m == 0), res, a.facet_tol, a.facet_min_m2)
                            m[fm & (m == 0)] = 1
                    m[~valid] = 255
                    zc = np.where(valid & (m == 0), z, -9999.0).astype(np.float32)
                    # the core of the block
                    cr0, cc0 = br - hr0, bc - hc0; cr1, cc1 = cr0 + min(a.block, H - br), cc0 + min(a.block, W - bc)
                    win = Window(bc, br, cc1 - cc0, cr1 - cr0)
                    dz.write(zc[cr0:cr1, cc0:cc1], 1, window=win); dm.write(m[cr0:cr1, cc0:cc1], 1, window=win)
                    core = m[cr0:cr1, cc0:cc1]
                    for k in range(4): counts[k] += int((core == k).sum())
                print(f'  rows {br}-{min(br + a.block, H)} of {H}  {time.time() - t0:.0f} s', flush=True)
    km2 = {k: round(float(c) * res * res / 1e6, 3) for k, c in zip(('kept', 'facet', 'flat_water', 'sea'), counts)}
    rep = dict(dem=a.dem, name=a.name, res_m=res, window=a.win, km2=km2, params=dict(facet_tol=a.facet_tol, facet_min_m2=a.facet_min_m2,
               flat_range=a.flat_range, flat_min_m2=a.flat_min_m2, min_level=a.min_level, sea_tol=a.sea_tol, facets=not a.no_facets),
               seconds=round(time.time() - t0))
    (out / f'{a.name}_clean.json').write_text(json.dumps(rep, indent=1))
    print(json.dumps(km2))


if __name__ == '__main__':
    main()
