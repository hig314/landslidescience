"""Per-site preparation for the KBay ground model.

    python model/sitekit.py <site> [--step all|clip|dtm|offset|labels|features]

A site is a square in EPSG:6334 with a KBay point file and the Grewingk tiles
that cover it (sites/all_sites.json + sites/grewingk_tiles.txt). patch1 is the
original 1.5 km test patch and uses the files already in place.

Everything is written under site/<name>/. Steps, in order:
  clip      Grewingk tiles merged and cropped to the site          grewingk.laz
  dtm       ground TINs at 1 m on a grid pinned to the site bounds grewingk_dtm.tif, vendor_dtm.tif
  offset    KBay - Grewingk on ground bare in BOTH surveys         offset.json
            (measured per site, not assumed: 0.37 m on patch 1)
  labels    point dz vs Grewingk at the point's exact XY (bilinear) pts.npz
  features  KBay-only features (never Grewingk)                    feat.npz

Water: points in hydroflattened Grewingk cells (flat to 5 cm over 3x3 m) and
KBay class 9 get no training label -- a flattened lake is not a ground truth.
"""
import argparse, json, os, subprocess, sys
import numpy as np
EXCL = (7, 18, 21, 22)   # noise, high noise, SNOW, TEMPORAL EXCLUSION: never ground or surface (stress test 2026-09-26)

ROOT = '/Volumes/Nunatak/lidar_build/kbay_reclass_test'
PDAL = '/opt/homebrew/bin/pdal'
os.chdir(ROOT)


UNIT_M, UNIT_BUF = 1000, 60
def site_info(name):
    if name.startswith('u_'):
        # full-survey work unit u_<E km>_<N km>: 1 km core + UNIT_BUF m buffer (model/fullrun.py)
        _, ex, ny = name.split('_'); x0, y0 = int(ex) * UNIT_M, int(ny) * UNIT_M
        return dict(name=name, bounds=(x0 - UNIT_BUF, x0 + UNIT_M + UNIT_BUF, y0 - UNIT_BUF, y0 + UNIT_M + UNIT_BUF),
                    kbay=f'full/units/{name}.laz', tiles=None, grewingk=None)
    if name == 'patch1':
        return dict(name=name, bounds=(603016, 604516, 6604764, 6606264),
                    kbay='kbay_2024/kbay_patch.laz', tiles=None, grewingk='grewingk_2021/grewingk_patch.laz')
    s = {d['name']: d for d in json.load(open('sites/all_sites.json'))}[name]
    tiles = [l.split()[1] for l in open('sites/grewingk_tiles.txt') if l.split()[0] == name]
    kbay = 'island/kbay_island.laz' if name == 'island' else f'sites/{name}_kbay.laz'
    return dict(name=name, bounds=tuple(s['bounds']), kbay=kbay, tiles=tiles, grewingk=f'site/{name}/grewingk.laz')


def run(pipeline, tag, d):
    p = f'{d}/{tag}.json'
    json.dump(pipeline, open(p, 'w'), indent=1)
    r = subprocess.run([PDAL, 'pipeline', p], capture_output=True, text=True)
    open(f'{d}/{tag}.log', 'w').write(r.stdout + r.stderr)
    if r.returncode:
        sys.exit(f'{tag} failed: {r.stderr[-400:]}')


def step_clip(S, d):
    if S['tiles'] is None:
        return
    x0, x1, y0, y1 = S['bounds']
    run([{"type": "readers.las", "filename": f"grewingk_2021/laz/{t}"} for t in S['tiles']] +
        [{"type": "filters.merge"},
         {"type": "filters.crop", "bounds": f"([{x0},{x1}],[{y0},{y1}])"},
         {"type": "writers.las", "filename": S['grewingk'], "compression": "laszip", "minor_version": 4,
          "dataformat_id": 6, "extra_dims": "all", "forward": "all"}], 'clip', d)


def tin(src, out, S, d, tag):
    x0, x1, y0, y1 = S['bounds']
    run([src, {"type": "filters.range", "limits": "Classification[2:2]"},
         {"type": "filters.delaunay"},
         {"type": "filters.faceraster", "resolution": 1.0, "origin_x": x0, "origin_y": y0,
          "width": int(x1 - x0), "height": int(y1 - y0), "max_triangle_edge_length": 30},
         {"type": "writers.raster", "filename": out, "data_type": "float32",
          "gdalopts": "COMPRESS=DEFLATE,PREDICTOR=3,TILED=YES"}], tag, d)


def step_dtm(S, d):
    tin(S['grewingk'], f'{d}/grewingk_dtm.tif', S, d, 'dtm_grewingk')
    tin(S['kbay'], f'{d}/vendor_dtm.tif', S, d, 'dtm_vendor')
    # highest non-noise KBay return per cell, for canopy height in scoring
    x0, x1, y0, y1 = S['bounds']
    run([S['kbay'], {"type": "filters.range", "limits": "Classification[1:6]"},
         {"type": "writers.gdal", "filename": f"{d}/kbay_max.tif", "resolution": 1, "radius": 0.71,
          "output_type": "max", "origin_x": x0, "origin_y": y0, "width": int(x1 - x0), "height": int(y1 - y0),
          "data_type": "float32", "nodata": -9999}], 'kbay_max', d)


def rd(p):
    import rasterio
    with rasterio.open(p) as s:
        a = s.read(1).astype(np.float64); nd = s.nodata; T = s.transform
    if nd is not None:
        a[a == nd] = np.nan
    return a, T


def flat_mask(G):
    # Hydroflattened water: flat to 5 cm over 3x3 m. NODATA is never 'flat' (the old
    # version filled NaN with a constant, so empty cells read as perfectly flat).
    from scipy import ndimage
    hi = ndimage.maximum_filter(np.nan_to_num(G, nan=9e3), 3)
    lo = ndimage.minimum_filter(np.nan_to_num(G, nan=-9e3), 3)
    return ((hi - lo) < 0.05) & np.isfinite(G)


def step_offset(S, d):
    G, _ = rd(f'{d}/grewingk_dtm.tif'); V, _ = rd(f'{d}/vendor_dtm.tif'); M, _ = rd(f'{d}/kbay_max.tif')
    # bare in BOTH: KBay canopy (max - own ground) < 0.3 m and Grewingk not flattened water
    bare = (M - V < 0.3) & ~flat_mask(G) & np.isfinite(G) & np.isfinite(V)
    e = np.zeros_like(bare); e[30:-30, 30:-30] = True
    dz = (V - G)[bare & e]
    off = dict(offset=float(np.median(dz)) if dz.size > 500 else None, n=int(dz.size),
               iqr=[float(v) for v in np.percentile(dz, [25, 75])] if dz.size else None)
    json.dump(off, open(f'{d}/offset.json', 'w'))
    print(S['name'], 'bare-ground offset', off)


def export_points(S, d):
    out = f'{d}/kbay.feather'
    if not os.path.exists(out):
        run([S['kbay'], {"type": "writers.arrow", "filename": out, "format": "feather"}], 'export', d)
    return out


def offset_grid(d):
    """KBay - Grewingk offset field for a site dir (model/v2.py offset_field), as a 1 m grid.
    Replaced the per-site constant on 2026-09-25: within-site blocks varied 0.04-0.62 m."""
    F, _ = rd(f'{d}/offset_field.tif'); return F


def step_labels(S, d, offset):
    import pyarrow.feather as f
    from scipy import ndimage
    t = f.read_table(export_points(S, d), columns=['xyz', 'Classification', 'ReturnNumber', 'NumberOfReturns', 'Intensity'])
    xyz = np.asarray(t.column('xyz').combine_chunks().flatten()).reshape(-1, 3)
    x, y, z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    G, T = rd(f'{d}/grewingk_dtm.tif')
    row = (y - T.f) / T.e - 0.5; col = (x - T.c) / T.a - 0.5
    ref = ndimage.map_coordinates(np.nan_to_num(G, nan=-1e4), [row, col], order=1, mode='nearest')
    ref[ref < -1000] = np.nan
    flat = ndimage.map_coordinates(flat_mask(G).astype(float), [row, col], order=0, mode='nearest') > 0.5
    Gs = ndimage.uniform_filter(np.nan_to_num(G, nan=np.nanmedian(G)), 3)
    gy, gx = np.gradient(Gs); slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    curv = Gs - ndimage.uniform_filter(Gs, 15)
    cls = np.asarray(t.column('Classification'))
    Fo = offset_grid(d)                                                   # offset FIELD at the point
    off_pt = ndimage.map_coordinates(np.nan_to_num(Fo, nan=np.nanmedian(Fo)), [row, col], order=1, mode='nearest')
    dz = (z - off_pt - ref).astype(np.float32)
    dz[flat | (cls == 9) | np.isin(cls, EXCL)] = np.nan          # no truth over water / noise
    np.savez(f'{d}/pts.npz', x=x, y=y, z=z, dz=dz, cls=cls,
             rn=np.asarray(t.column('ReturnNumber')), nr=np.asarray(t.column('NumberOfReturns')),
             inten=np.asarray(t.column('Intensity')),
             slope=ndimage.map_coordinates(slope, [row, col], order=1, mode='nearest').astype(np.float32),
             curv=ndimage.map_coordinates(curv, [row, col], order=1, mode='nearest').astype(np.float32))
    print(S['name'], 'points', len(x), 'labelled', int(np.isfinite(dz).sum()))


def step_features(S, d):
    """Same features as model/features.py (patch 1), parameterised by site bounds."""
    from scipy import ndimage
    P = dict(np.load(f'{d}/pts.npz'))
    x, y, z = P['x'], P['y'], P['z']
    x0, x1, y0, y1 = S['bounds']; X0, Y0 = float(x0), float(y1)
    NX, NY = int(x1 - x0), int(y1 - y0)
    ci = np.clip((x - X0).astype(int), 0, NX - 1); cj = np.clip((Y0 - y).astype(int), 0, NY - 1)
    cid = cj * NX + ci; cx = X0 + ci + 0.5; cy = Y0 - cj - 0.5
    V, _ = rd(f'{d}/vendor_dtm.tif')
    Vf = np.where(np.isnan(V), np.nanmedian(V), V); Vs = ndimage.uniform_filter(Vf, 5)
    gyr, gx = np.gradient(Vs); gy = -gyr
    out = {}
    out['h_vendor'] = z - ndimage.map_coordinates(Vf, [(Y0 - y) - 0.5, (x - X0) - 0.5], order=1, mode='nearest')
    out['v_slope'] = np.degrees(np.arctan(np.hypot(gx, gy))).ravel()[cid]
    out['v_curv'] = (Vs - ndimage.uniform_filter(Vs, 15)).ravel()[cid]
    GX, GY = gx.ravel()[cid], gy.ravel()[cid]
    usable = ~np.isin(P['cls'], EXCL)                        # noise never sets the low reference

    def low_ref(win):
        zd = np.where(usable, z - (GX * (x - cx) + GY * (y - cy)), np.inf)
        order = np.lexsort((zd, cid)); first = np.r_[True, cid[order][1:] != cid[order][:-1]]
        lo = order[first]; lo = lo[np.isfinite(zd[lo])]
        LX = np.full(NX * NY, np.nan); LY = LX.copy(); LZ = LX.copy()
        LX[cid[lo]] = x[lo]; LY[cid[lo]] = y[lo]; LZ[cid[lo]] = z[lo]
        LX, LY, LZ = [a.reshape(NY, NX) for a in (LX, LY, LZ)]
        ccx = X0 + np.arange(NX)[None, :] + 0.5; ccy = Y0 - np.arange(NY)[:, None] - 0.5
        ref = np.full((NY, NX), np.inf); r = win // 2
        for dj in range(-r, r + 1):
            for di in range(-r, r + 1):
                sh = lambda a: np.roll(np.roll(a, -dj, 0), -di, 1)
                v = sh(LZ) - (gx * (sh(LX) - ccx) + gy * (sh(LY) - ccy))
                ref = np.fmin(ref, np.where(np.isnan(v), np.inf, v))
        ref[np.isinf(ref)] = np.nan
        return z - (ref.ravel()[cid] + GX * (x - cx) + GY * (y - cy))

    out['h_low3'] = low_ref(3); out['h_low7'] = low_ref(7)
    nb = lambda a: ndimage.uniform_filter(a.reshape(NY, NX), 3, mode='constant') * 9
    cnt = np.bincount(cid, minlength=NX * NY).astype(float)
    near = np.bincount(cid, weights=(out['h_low3'] < 0.3).astype(float), minlength=NX * NY)
    top = np.full(NX * NY, -np.inf); np.maximum.at(top, cid, np.nan_to_num(out['h_low3'], nan=-np.inf))
    top = ndimage.maximum_filter(np.where(np.isinf(top), 0, top).reshape(NY, NX), 3).ravel()
    out['nb_count'] = nb(cnt).ravel()[cid]
    out['nb_near_frac'] = (nb(near) / np.maximum(nb(cnt), 1)).ravel()[cid]
    out['canopy'] = top[cid]
    out['is_last'] = (P['rn'] == P['nr']).astype(np.int8)
    out['rn'] = P['rn']; out['nr'] = P['nr']; out['inten'] = P['inten']
    out['vendor_ground'] = (P['cls'] == 2).astype(np.int8)
    np.savez(f'{d}/feat.npz', **{k: np.asarray(v, dtype=np.float32) for k, v in out.items()})
    print(S['name'], 'features', len(x))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('site'); ap.add_argument('--step', default='all')
    a = ap.parse_args()
    S = site_info(a.site); d = f'site/{a.site}'; os.makedirs(d, exist_ok=True)
    steps = ['clip', 'dtm', 'offset', 'labels', 'features'] if a.step == 'all' else [a.step]
    for s in steps:
        if s == 'clip': step_clip(S, d)
        elif s == 'dtm': step_dtm(S, d)
        elif s == 'offset': step_offset(S, d)
        elif s == 'labels':
            # strict offset: ground bare in BOTH surveys (canopy <0.2 m each), slope <20 deg.
            # It varies by site (0.18 meadow .. 0.36 patch1), so a single constant would mislabel.
            strict = json.load(open('site/offsets_strict.json')).get(S['name'], {})
            off = strict.get('median') or json.load(open(f'{d}/offset.json'))['offset'] or 0.37
            print(S['name'], 'using offset', off)
            step_labels(S, d, off)
        elif s == 'features': step_features(S, d)


FLOOR_TOL = 1.0
def return_floor(s, win=5):
    """Lowest real return (noise excluded; snow kept -- it bounds ground from above) over a
    win x win cell window. Ground more than FLOOR_TOL below this is not credible: at the six
    Grewingk sites truth is > 1 m below it in <= 0.23% of cells, > 1.5 m in <= 0.045% (2026-09-26).
    NaN where the window holds no return."""
    S = site_info(s); x0, x1, y0, y1 = S['bounds']; NX, NY = int(x1-x0), int(y1-y0)
    P = np.load(f'site/{s}/pts.npz'); m = ~np.isin(P['cls'], (7, 18, 22))   # class 22 = another flight/tide: not a bound on this epoch's ground
    cid = np.clip((y1-P['y'][m]).astype(int), 0, NY-1)*NX + np.clip((P['x'][m]-x0).astype(int), 0, NX-1)
    F = np.full(NX*NY, np.inf); np.minimum.at(F, cid, P['z'][m])
    from scipy import ndimage
    F = ndimage.minimum_filter(F.reshape(NY, NX), win, mode='nearest'); F[np.isinf(F)] = np.nan
    return F


LOW_TIDE_BELOW = 0.15
def admit_low_tide(x, y, z, cls, bounds, win=15):
    """Class 22 (temporal exclusion) returns admitted back, by two rules (RULE 2 in the body: the
    excluded flight is the only observation). RULE 1: those BELOW the kept water surface become class 2.

    Hig, 2026-09-26: "I do want to pick up low-tide returns in areas where there are also on-water
    returns at higher tides." Where flights at different tides overlap, NV5 keeps one and marks the
    other class 22. When the one they kept is the HIGHER tide, the excluded flight's returns under
    that water are real ground (or a lower water surface) that nothing else saw. Admitted: class 22
    more than LOW_TIDE_BELOW under the kept water level (class 9, per-cell mean, median over win x
    win cells); anything at or above it -- the other flight's own water, vegetation, ground above
    the tide -- stays excluded, and nothing is admitted away from kept water. At the tides patch the
    vendor already kept the LOW tide (2023, water -2.9 m) and the class 22 there is 2024 water at
    -0.07 m, so nothing is admitted; the rule matters where they kept the high tide."""
    x0, x1, y0, y1 = bounds; NX, NY = int(x1 - x0), int(y1 - y0)
    w = cls == 9; e = cls == 22
    if not e.any():
        return cls, 0
    cid = np.clip((y1 - y).astype(int), 0, NY - 1) * NX + np.clip((x - x0).astype(int), 0, NX - 1)
    from scipy import ndimage
    out = cls.copy()
    # RULE 2 -- the excluded flight is the ONLY observation. Hig, 2026-09-26: at the tides patch 58%
    # of the grid was empty while 695k class-22 returns (2024 water at -0.08 m) lay in it. Where
    # the kept flights have no return in a cell's 3x3 neighbourhood, admit the class 22 there:
    # as water (9) if within 0.15 m of the local median of class 22 (a flat surface), else as
    # unclassified (1) for the pipeline to judge like any other return.
    kept = ~np.isin(cls, (7, 18, 21, 22))
    kc = ndimage.maximum_filter(np.bincount(cid[kept], minlength=NX * NY).reshape(NY, NX) > 0, 3).ravel()
    only = e & ~kc[cid]
    if only.any():
        E = np.bincount(cid[only], weights=z[only], minlength=NX * NY); En = np.bincount(cid[only], minlength=NX * NY)
        el = np.where(En > 0, E / np.maximum(En, 1), np.inf).reshape(NY, NX)
        el = ndimage.median_filter(el, 15); el[~np.isfinite(el)] = np.nan
        flat = np.abs(z - el.ravel()[cid]) <= 0.15
        out[only & flat] = 9; out[only & ~flat] = 1
    n2 = int(only.sum())
    if not w.any():
        return out, n2
    W = np.bincount(cid[w], weights=z[w], minlength=NX * NY); Wn = np.bincount(cid[w], minlength=NX * NY)
    lvl = np.where(Wn > 0, W / np.maximum(Wn, 1), np.nan).reshape(NY, NX)
    lvl = ndimage.generic_filter(lvl, np.nanmedian, size=win, mode='nearest') if np.isfinite(lvl).sum() < 2e5 else \
        ndimage.median_filter(np.where(np.isfinite(lvl), lvl, np.inf), win)   # (large grids: inf-padded median)
    lvl[~np.isfinite(lvl)] = np.nan
    L = lvl.ravel()[cid]
    adm = e & ~only & np.isfinite(L) & (z < L - LOW_TIDE_BELOW)
    out[adm] = 2
    return out, int(adm.sum()) + n2
