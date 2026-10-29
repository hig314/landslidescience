"""PILOT (2026-10-20): fuse the per-survey ground grids of joint_pilot.py into one surface, cell by cell, with support.

Inputs (OUT/<survey>_grids.npz, 1 m over WIN): n_g, z_g (median ground), zmin_g, n_w, z_w (median water), zmax.

1. ALIGNMENT: each survey against 2023 on stable BARE ground (both >= 2 returns, agree within 1 m, slope < 8 deg,
   nothing > 0.5 m above ground in either, outside the vintage zones); the median is applied (2021: on top of its
   +0.36 m).
2. VINTAGE MAP: the creek's ACTIVE FLOODPLAIN takes 2021 only (Hig: one river, one age) -- the channel cells of the
   inversion prototype, grown over ground < FP_HAND m above the creek's water surface that 2021 sees bare (highest
   return < FP_VEG m above ground, or no returns at all), connected to the channel; BEACHES take 2023 only (the
   sea-connected ground of the water layer, dilated). Elsewhere the surveys POOL.
3. CROSS-CHECK where they pool: R_j = the other survey's ground from its OWN returns within a few metres
   (normalised Gaussian convolution, sigma XC_SIG m). A cell of survey k standing above R_j + tol (vegetation
   counted as ground: the usual error) is rejected; one standing below R_j - tol_low is rejected only if it is
   also a spike in its own survey (low noise is rare and isolated). tol grows with slope. And where the others saw
   OPEN WATER (their water returns within OVER_WATER_M, none of their ground within OVER_WATER_DRY_M, and nothing
   of theirs standing above that water in the cell -- vegetation would) a "ground" cell standing more than
   OVER_WATER_TOL above that water is rejected: dry ground cannot stand above a water surface it is under (Grewingk
   2019 classed the sea surface ground at +4.0 m over 0.5 km2 of Kachemak Bay where 2023 and 2021 saw water at
   +0.6, and a flock or cloud at +360 m; with no other survey's ground there nothing cross-checked them).
4. POOL: the surviving values, weighted n / sigma^2. NO interpolation: a cell with no surviving return is void.
   dist = distance to the nearest supported cell; sigma = the pooled sigma there (voids carry dist only).
N surveys (SURVEYS; Grewingk 2019 added 2026-10-20), each aligned to the first and cross-checked against the others.
Outputs (GeoTIFF, EPSG:6334, 1 m): joint_z, joint_n, joint_src (bit k = survey k, see the report's src_bits), joint_rej
(bit 2k = survey k high, 2k+1 = low), joint_dist, joint_sigma, vintage (0 pooled, 1 = 2023 only, 2 = 2021 only),
water_z (pooled water-surface returns, the water layer's evidence), and joint_report.json.
"""
import json
from pathlib import Path
import numpy as np
from scipy import ndimage

SIG = {'kbay2023': 0.05, 'gk2021': 0.08, 'gk2019': 0.08}
XC_SIG = 2.0              # m, the other survey's local ground
XC_MINW = 1.5             # minimum summed weight (about two supported cells) for R_j to be trusted
TOL = 0.30                # m, + slope term
TOL_LOW = 1.0
OVER_WATER_M = 50.0       # another survey's water returns within this (they are sparse: specular water drops pulses) ...
OVER_WATER_DRY_M = 10.0   # ... none of its ground within this, and nothing of its standing above the water in the cell
OVER_WATER_TOL = 0.5      # m above that water surface (waves, a flat-calm seam) before a "ground" return there is rejected
FP_HAND, FP_VEG = 3.0, 1.0
FP_DIST = 600.0            # m from the channel
FP_ZMAX = 47.0             # below Grewingk Lake (48.9): the lake is not the creek's floodplain
CREEK = '/Volumes/Nunatak/lidar_build/rivers/grewingk/'


def read_to(path, win, res=1.0, resampling='nearest'):
    import rasterio
    from rasterio.warp import reproject, Resampling
    H, W = int((win[3] - win[1]) / res), int((win[2] - win[0]) / res)
    out = np.full((H, W), np.nan, np.float32)
    with rasterio.open(path) as s:
        src = s.read(1).astype(np.float32)
        if s.nodata is not None: src[src == s.nodata] = np.nan
        reproject(src, out, src_transform=s.transform, src_crs=s.crs,
                  dst_transform=rasterio.transform.from_origin(win[0], win[3], res, res), dst_crs=s.crs,
                  resampling=getattr(Resampling, resampling), src_nodata=np.nan, dst_nodata=np.nan)
    return out


def local_ground(z, n, sig=XC_SIG):
    w = np.where(np.isfinite(z), np.minimum(n, 5.0), 0.0).astype(np.float32)
    zw = np.where(w > 0, z, 0).astype(np.float32) * w
    a = ndimage.gaussian_filter(zw, sig, truncate=3); b = ndimage.gaussian_filter(w, sig, truncate=3)
    wsum = b * 2 * np.pi * sig ** 2                     # gaussian_filter normalises its kernel
    R = np.where(wsum >= XC_MINW, a / np.maximum(b, 1e-9), np.nan)
    gy, gx = np.gradient(np.nan_to_num(R, nan=0)); s = np.hypot(gx, gy)
    s[~np.isfinite(R)] = np.nan
    return R.astype(np.float32), s.astype(np.float32)


SURVEYS = ('kbay2023', 'gk2021', 'gk2019')       # the first is the vertical reference
VINTAGE_ONLY = {1: 'kbay2023', 2: 'gk2021'}       # vintage code -> the one survey allowed there (1 beach, 2 floodplain)


def main(OUT, WIN):
    import rasterio
    from rasterio.transform import from_origin
    H, W = WIN[3] - WIN[1], WIN[2] - WIN[0]
    G = {sv: dict(np.load(OUT / f'{sv}_grids.npz')) for sv in SURVEYS if (OUT / f'{sv}_grids.npz').exists()}
    names = [sv for sv in SURVEYS if sv in G]
    A, B = G['kbay2023'], G['gk2021']
    rep = {}
    # ---- 2. vintage map (needed before alignment, to keep the moving ground out of it)
    chan = read_to(CREEK + 'proto2/chan.tif', WIN) > 0
    eta = read_to(CREEK + 'proto2/eta_model.tif', WIN)
    eta[eta < -9000] = np.nan
    seed = chan & np.isfinite(eta)
    idx = ndimage.distance_transform_edt(~seed, return_distances=False, return_indices=True)
    eta_near = eta[idx[0], idx[1]]
    zb = np.where(np.isfinite(B['z_g']), B['z_g'], A['z_g'])
    hand = zb - eta_near
    veg = B['zmax'] - B['z_g']
    # evidence of an open floodplain: 2021 sees it bare, or either survey sees water there. No returns at all is
    # NOT evidence (first pilot run: the lake, the bay and every gap counted as bare -> a 52 km2 'floodplain').
    bare = (np.isfinite(veg) & (veg <= FP_VEG)) | (B['n_w'] > 0) | (A['n_w'] > 0)
    near = ndimage.distance_transform_edt(~chan) <= FP_DIST
    cand = (hand < FP_HAND) & bare & near & (zb < FP_ZMAX)
    cand = ndimage.binary_closing(cand, iterations=2) & near
    lab, n = ndimage.label(cand | chan, structure=np.ones((3, 3)))
    keep = np.unique(lab[chan]); keep = keep[keep > 0]
    fp = np.isin(lab, keep)
    holes = ndimage.binary_fill_holes(fp) & ~fp                    # bars and islands inside it: small ones join
    hl, hn = ndimage.label(holes)
    if hn:
        hs = ndimage.sum(holes, hl, np.arange(1, hn + 1))
        fp |= np.isin(hl, np.flatnonzero(hs <= 5000) + 1)
    sea = read_to('/Volumes/Nunatak/lidar_build/rivers/grewingk/dev/sea.tif', WIN)
    beach = ndimage.binary_dilation(np.isfinite(sea), iterations=20) & (np.nan_to_num(np.where(np.isfinite(A['z_g']), A['z_g'], zb), nan=99) < 8)
    vint = np.zeros((H, W), np.uint8); vint[beach] = 1; vint[fp] = 2
    rep['vintage_km2'] = dict(floodplain_2021=round(fp.sum() / 1e6, 3), beach_2023=round((vint == 1).sum() / 1e6, 3))
    # ---- 1. alignment of each survey to the reference on stable BARE ground: flat, outside the vintage zones, and
    # nothing standing > 0.5 m above ground in either survey (under shrubs the surveys' ground classes disagree by
    # what each called ground -- 2019 vs 2023: +0.25 m on all stable ground, +0.07 m on bare; 2026-10-20)
    gy, gx = np.gradient(np.nan_to_num(A['z_g'], nan=0)); slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    sig = dict(SIG); rep['alignment'] = {}
    for sv in names[1:]:
        S = G[sv]; dz = A['z_g'] - S['z_g']
        st = ((A['n_g'] >= 2) & (S['n_g'] >= 2) & (np.abs(dz) < 1) & (slope < 8) & (vint == 0)
              & ((A['zmax'] - A['z_g']) < 0.5) & ((S['zmax'] - S['z_g']) < 0.5))
        off = float(np.median(dz[st])) if st.sum() > 1000 else 0.0
        nmad = float(1.4826 * np.median(np.abs(dz[st] - off))) if st.sum() > 1000 else 0.2
        rep['alignment'][sv] = dict(cells=int(st.sum()), median_ref_minus=round(off, 3), nmad=round(nmad, 3), applied=round(off, 3))
        for k in ('z_g', 'zmin_g', 'z_w', 'zmax'): S[k] = S[k] + off
        sig[sv] = float(np.hypot(SIG.get(sv, 0.08), nmad / 2))
    # ---- 3. cross-check where pooled: each survey against the others' own returns (n-weighted)
    rej = np.zeros((H, W), np.uint8); pooled = vint == 0; Zs = {}
    for k, sv in enumerate(names):
        S = G[sv]; others = [G[o] for o in names if o != sv]
        no = sum(np.where(np.isfinite(O['z_g']), O['n_g'], 0) for O in others)
        zo = sum(np.where(np.isfinite(O['z_g']), O['z_g'] * O['n_g'], 0) for O in others) / np.maximum(no, 1e-9)
        zo = np.where(no > 0, zo, np.nan)
        R, s = local_ground(zo, no); own, _ = local_ground(S['z_g'], S['n_g'], 1.5)
        tol = TOL + 2.0 * np.nan_to_num(s, nan=0)
        hi = pooled & np.isfinite(S['z_g']) & np.isfinite(R) & (S['z_g'] > R + tol)
        lo = pooled & np.isfinite(S['z_g']) & np.isfinite(R) & (S['z_g'] < R - TOL_LOW - 2.0 * np.nan_to_num(s, nan=0)) & (own - S['z_g'] > 0.5)
        # over open water seen by the others (see 3. above)
        nw = sum(np.where(np.isfinite(O['z_w']), O['n_w'], 0) for O in others)
        zwo = sum(np.where(np.isfinite(O['z_w']), O['z_w'] * O['n_w'], 0) for O in others) / np.maximum(nw, 1e-9)
        hw = nw > 0; hg = no > 0
        if hw.any():
            dwo, iw = ndimage.distance_transform_edt(~hw, return_indices=True)
            dgo = ndimage.distance_transform_edt(~hg) if hg.any() else np.full((H, W), np.inf)
            wlev = zwo[iw[0], iw[1]]     # the nearest water return's level
            zxo = np.full((H, W), np.nan, np.float32)
            for O in others: zxo = np.fmax(zxo, O['zmax'])
            ow = (pooled & np.isfinite(S['z_g']) & ~np.isfinite(R) & (dwo <= OVER_WATER_M) & (dgo > OVER_WATER_DRY_M)
                  & ~(np.nan_to_num(zxo, nan=-1e9) > wlev + OVER_WATER_TOL) & (S['z_g'] > wlev + OVER_WATER_TOL))
            rep.setdefault('over_water_km2', {})[sv] = round(float(ow.sum()) / 1e6, 3)
            hi |= ow
        rej[hi] |= 1 << (2 * k); rej[lo] |= 1 << (2 * k + 1)
        excl = np.zeros((H, W), bool)
        for code, only in VINTAGE_ONLY.items():
            if only != sv: excl |= vint == code
        Zs[sv] = np.where(hi | lo | excl, np.nan, S['z_g'])
    rep['rejected_km2'] = {f'{sv}_{w}': round(float(((rej >> (2 * k + j)) & 1).sum()) / 1e6, 3)
                           for k, sv in enumerate(names) for j, w in ((0, 'high'), (1, 'low'))}
    # ---- 4. pool
    ws = {sv: np.where(np.isfinite(Zs[sv]), G[sv]['n_g'] / sig[sv] ** 2, 0) for sv in names}
    wsum = sum(ws.values())
    Z = np.where(wsum > 0, sum(np.nan_to_num(Zs[sv]) * ws[sv] for sv in names) / np.maximum(wsum, 1e-9), np.nan).astype(np.float32)
    N = sum(np.where(np.isfinite(Zs[sv]), G[sv]['n_g'], 0) for sv in names).astype(np.float32)
    src = sum(np.isfinite(Zs[sv]) * (1 << k) for k, sv in enumerate(names)).astype(np.uint8)
    SIGMA = np.where(wsum > 0, np.maximum(1 / np.sqrt(np.maximum(wsum, 1e-9)), 0.03), np.nan).astype(np.float32)
    DIST = ndimage.distance_transform_edt(~np.isfinite(Z)).astype(np.float32)
    # water surface evidence: per vintage, the allowed survey's water returns; pooled -> 2023, then the others in order
    WZ = np.full((H, W), np.nan, np.float32)
    for sv in names:
        S = G[sv]; allowed = np.ones((H, W), bool)
        for code, only in VINTAGE_ONLY.items():
            if only != sv: allowed &= vint != code
        WZ = np.where(np.isnan(WZ) & allowed, S['z_w'], WZ)
    rep['coverage'] = dict(supported=round(float(np.isfinite(Z).mean()), 3),
                           **{f'with_{sv}': round(float(((src >> k) & 1).mean()), 3) for k, sv in enumerate(names)},
                           void_km2=round(float((~np.isfinite(Z)).sum()) / 1e6, 2), void_over_5m_km2=round(float((DIST > 5).sum()) / 1e6, 2),
                           water_cells_km2=round(float(np.isfinite(WZ).sum()) / 1e6, 2))
    rep['src_bits'] = {sv: 1 << k for k, sv in enumerate(names)}
    prof = dict(driver='GTiff', height=H, width=W, count=1, crs='EPSG:6334', transform=from_origin(WIN[0], WIN[3], 1, 1),
                tiled=True, blockxsize=512, blockysize=512, compress='zstd')
    for name, a, dt, nd in (('joint_z', Z, 'float32', -9999), ('joint_n', N, 'float32', -9999), ('joint_src', src, 'uint8', 0),
                            ('joint_rej', rej, 'uint8', 0), ('joint_dist', DIST, 'float32', -9999), ('joint_sigma', SIGMA, 'float32', -9999),
                            ('vintage', vint, 'uint8', 255), ('water_z', WZ, 'float32', -9999)):
        with rasterio.open(OUT / f'{name}.tif', 'w', **prof, dtype=dt, nodata=nd,
                           **({'predictor': 3} if dt == 'float32' else {})) as d:
            d.write(np.where(np.isfinite(a), a, nd).astype(dt) if dt == 'float32' else a, 1)
    (OUT / 'joint_report.json').write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))
    support(OUT, WIN, G, rej)


def support(OUT, WIN, G=None, rej=None):
    """joint_nland.tif: how many SURVEYS saw ground in the cell -- a ground return that survived the cross-check, whatever
    the vintage map pooled. The composite's floating-ice rule reads it as the pooled layer's count (recipe "land_support"):
    a berg is seen once, an island or a gravel bar by every survey that flew it (2026-10-21: counted as one layer, the
    joint lidar made all of Grewingk's outwash "seen once"). Standalone: joint_pilot.py support (reads the grids again)."""
    import rasterio
    from rasterio.transform import from_origin
    H, W = WIN[3] - WIN[1], WIN[2] - WIN[0]
    if G is None:
        G = {sv: dict(np.load(OUT / f'{sv}_grids.npz')) for sv in SURVEYS if (OUT / f'{sv}_grids.npz').exists()}
    if rej is None:
        with rasterio.open(OUT / 'joint_rej.tif') as d: rej = d.read(1)
    names = [sv for sv in SURVEYS if sv in G]
    nl = np.zeros((H, W), np.uint8)
    for k, sv in enumerate(names):
        S = G[sv]
        ok = (S['n_g'] > 0) & np.isfinite(S['z_g']) & (((rej >> (2 * k)) & 3) == 0)
        nl += ok.astype(np.uint8)
    prof = dict(driver='GTiff', height=H, width=W, count=1, crs='EPSG:6334', transform=from_origin(WIN[0], WIN[3], 1, 1),
                tiled=True, blockxsize=512, blockysize=512, compress='zstd', dtype='uint8', nodata=255)
    with rasterio.open(OUT / 'joint_nland.tif', 'w', **prof) as d: d.write(nl, 1)
    u, c = np.unique(nl, return_counts=True)
    print('joint_nland km2 by count:', {int(a): round(b / 1e6, 2) for a, b in zip(u, c)})


# ---- the chain's layer: ground, water and void kept apart (stage `layer`)
GAP_M = 2.0               # a void within this of ground returns (and nearer ground than water) is a small gap: interpolated
WET_M = 15.0              # a void nearer to water returns than to ground, within this of them, is water (lidar dropout)
STEEP_DEG = 20.0          # a hole with no returns on ground this steep (3DEP, ~20 m) cannot be water: poorer data may fill it
CTX3DEP = '/Volumes/Nunatak/lidar_build/ctx_3dep/all.vrt'
KB_INDEX = '/Volumes/Nunatak/lidar_build/kbay_reclass_test/full/tileindex.json'
GK_INDEX = '/Volumes/Nunatak/lidar_build/composite/grewingk/obs/nohf_full/index.csv'
G19_INDEX = '/Volumes/Powder/lidar_src/grewingk_2019/tiles.txt'


def footprint(WIN):
    """Where either survey flew: the union of its tiles (KBay: each tile's point extent; Grewingk: tile boxes)."""
    import csv, re
    H, W = WIN[3] - WIN[1], WIN[2] - WIN[0]; F = np.zeros((H, W), bool)
    boxes = [(v['minx'], v['miny'], v['maxx'], v['maxy']) for v in json.load(open(KB_INDEX)).values()]
    for r in csv.DictReader(open(GK_INDEX)):
        xs = [float(v) for v in re.findall(r'[-\d.]+', r['WKT'])]; boxes.append((min(xs[0::2]), min(xs[1::2]), max(xs[0::2]), max(xs[1::2])))
    if Path(G19_INDEX).exists():
        boxes += [tuple(float(v) for v in l.split()[1:5]) for l in open(G19_INDEX)]
    for x0, y0, x1, y1 in boxes:
        c0, c1 = int(max(x0 - WIN[0], 0)), int(min(x1 - WIN[0], W)); r0, r1 = int(max(WIN[3] - y1, 0)), int(min(WIN[3] - y0, H))
        if c1 > c0 and r1 > r0: F[r0:r1, c0:c1] = True
    return F


def slope_3dep(WIN, res=5):
    """3DEP slope (deg) over the window, smoothed ~20 m, at 1 m. Read through a warped VRT (the source is 4326)."""
    import rasterio
    from rasterio.vrt import WarpedVRT
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    H, W = (WIN[3] - WIN[1]) // res, (WIN[2] - WIN[0]) // res
    with rasterio.open(CTX3DEP) as s, WarpedVRT(s, crs='EPSG:6334', transform=from_origin(WIN[0], WIN[3], res, res),
                                                 width=W, height=H, resampling=Resampling.bilinear) as v:
        z = v.read(1).astype(np.float32)
        if v.nodata is not None: z[z == v.nodata] = np.nan
    z = ndimage.gaussian_filter(np.nan_to_num(z, nan=float(np.nanmedian(z))), 2)
    gy, gx = np.gradient(z, res)
    return np.kron(np.degrees(np.arctan(np.hypot(gx, gy))), np.ones((res, res), np.float32))


def layer(OUT, WIN, tag=''):
    """joint_z + water_z -> layer_z / layer_class (1 ground, 2 water, 0 void) for the composite chain.

    Hig 2026-10-20: a hole in good lidar is far more likely water than an oversight, so poorer data (3DEP, SfM, the
    fill's land model) must not fill it -- unless the hole cannot be water because the ground there is steep. The
    first pilot run left every hole to the layers below and 3DEP filled the creek channel 2+ m high.
    The rule for a cell with no ground return, in order:
      1. nearer water returns than ground, within WET_M, and nothing standing above the water there -> WATER at the
         returns' smoothed level (water returns over a river are sparse: specular and turbid water drops pulses);
      2. within GAP_M of ground and nearer ground -> a GAP, interpolated (ground);
      3. NO RETURN AT ALL from either survey (water absorbs; a forest hole still has its canopy -- a test that is not
         geometry), inside the surveys' footprint and not open to its edge: a HOLE -> WATER, at the nearest water
         returns' level if it touches them, else at its rim's low ground (10th percentile: a pond is no higher than
         its lowest rim) -- unless 3DEP says the ground is steeper than STEEP_DEG there: then VOID;
      4. the rest (ground hidden under vegetation, beyond the surveys) -> VOID: placeholder, the layers below decide."""
    import rasterio
    with rasterio.open(OUT / 'joint_z.tif') as s:
        prof = s.profile; Z = s.read(1).astype(np.float32); Z[Z == s.nodata] = np.nan
    with rasterio.open(OUT / 'water_z.tif') as s:
        Wz = s.read(1).astype(np.float32); Wz[Wz == s.nodata] = np.nan
    zmax = np.full(Z.shape, np.nan, np.float32)
    for sv in SURVEYS:
        if (OUT / f'{sv}_grids.npz').exists(): zmax = np.fmax(zmax, np.load(OUT / f'{sv}_grids.npz')['zmax'])
    g = np.isfinite(Z); w = np.isfinite(Wz) & ~g
    noret = ~np.isfinite(zmax) & ~g & ~w
    dg = ndimage.distance_transform_edt(~g); dw = ndimage.distance_transform_edt(~w)

    def smooth(v, m, sig):
        a = ndimage.gaussian_filter(np.where(m, v, 0).astype(np.float32), sig)
        b = ndimage.gaussian_filter(m.astype(np.float32), sig)
        return np.where(b > 1e-3, a / np.maximum(b, 1e-9), np.nan)
    lev = smooth(Wz, w, 3.0)
    i = ndimage.distance_transform_edt(~np.isfinite(lev), return_distances=False, return_indices=True)
    lev = lev[i[0], i[1]]
    # 1. dropouts among water returns
    wet = w | (~g & (dw < dg) & (dw <= WET_M) & (noret | (np.nan_to_num(zmax, nan=-1e9) < lev + 1.0)))
    wet = ndimage.binary_opening(wet, iterations=1) | w
    level = np.where(wet, lev, np.nan)
    # 2. small gaps in ground
    gap = ~g & ~wet & (dg <= GAP_M)
    # 3. holes: no returns at all, enclosed by the surveys
    F = footprint(WIN); steep = slope_3dep(WIN) > STEEP_DEG
    hole = noret & ~wet & ~gap
    lab, n = ndimage.label(hole, structure=np.ones((3, 3)))
    rep = {}
    if n:
        ring = ndimage.binary_dilation(hole, structure=np.ones((3, 3))) & ~hole
        rl = ndimage.grey_dilation(lab, size=3) * ring              # the label a rim cell borders (one of them)
        idx = np.arange(1, n + 1)
        open_edge = ndimage.maximum(ndimage.binary_dilation(~F, structure=np.ones((3, 3))), lab, idx) > 0  # reaches outside the footprint
        touches_water = ndimage.maximum(ring & wet, rl, idx) > 0
        # rim ground low level per hole: 10th percentile of ground z on its rim
        m = (rl > 0) & g; ll, zz = rl[m], Z[m]; o = np.lexsort((zz, ll)); ll, zz = ll[o], zz[o]
        u, st, ct = np.unique(ll, return_index=True, return_counts=True)
        rim = np.full(n + 1, np.nan, np.float32); rim[u] = zz[st + (ct // 10)]
        ok = np.zeros(n + 1, bool); ok[1:] = ~open_edge
        inside = ok[lab] & hole
        hl = np.where(touches_water[np.maximum(lab - 1, 0)], lev, rim[lab])
        water_hole = inside & ~steep & np.isfinite(hl)
        wet |= water_hole; level = np.where(water_hole, hl, level)
        rep['holes'] = dict(n=int(n), open_to_edge=int(open_edge.sum()),
                            water_km2=round(float(water_hole.sum()) / 1e6, 3),
                            steep_void_km2=round(float((inside & steep).sum()) / 1e6, 3),
                            edge_void_km2=round(float((hole & ~inside).sum()) / 1e6, 3))
    gz = smooth(Z, g, 1.0)
    out = np.where(g, Z, np.where(wet, level, np.where(gap, gz, np.nan))).astype(np.float32)
    cls = np.where(g | (gap & np.isfinite(gz)), 1, np.where(wet & np.isfinite(level), 2, 0)).astype(np.uint8)
    f = np.isfinite(out)                                    # void placeholder: nearest value (the class says not to show it)
    i = ndimage.distance_transform_edt(~f, return_distances=False, return_indices=True)
    out = out[i[0], i[1]]
    p = dict(prof); p.update(dtype='float32', nodata=-9999, predictor=3)
    with rasterio.open(OUT / f'layer{tag}_z.tif', 'w', **p) as d: d.write(out, 1)
    p.update(dtype='uint8', nodata=255); p.pop('predictor')
    with rasterio.open(OUT / f'layer{tag}_class.tif', 'w', **p) as d: d.write(cls, 1)
    vh = ~g & ~wet & ~gap & ~noret
    rep.update(ground_km2=round(float(g.sum()) / 1e6, 2), gap_km2=round(float((cls == 1).sum() - g.sum()) / 1e6, 2),
               water_returns_km2=round(float(w.sum()) / 1e6, 2), water_km2=round(float((cls == 2).sum()) / 1e6, 2),
               void_km2=round(float((cls == 0).sum()) / 1e6, 2), void_under_vegetation_km2=round(float(vh.sum()) / 1e6, 2),
               footprint_km2=round(float(F.sum()) / 1e6, 2))
    (OUT / f'layer{tag}_report.json').write_text(json.dumps(rep, indent=1)); print(json.dumps(rep)); return rep
