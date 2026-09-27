"""Version 2 of the KBay ground surface (Hig, 2026-09-25): four changes reviewed together.

  1. OFFSET FIELD instead of one number per site. KBay - Grewingk on ground bare in BOTH
     surveys (highest return < 0.2 m above own ground in each), slope < 20 deg, > 40 m from
     water (DGGS hydroflattened cells -- the 2021 lake stood 0.70 m lower, so shore strips are
     real change). Block medians (50 m) smoothed with a 150 m Gaussian, weighted by support
     and pulled toward the site median where support is thin. Within-site blocks varied
     0.04-0.62 m, so a constant mislabelled parts of every site.
  2. WATER unchanged: water returns within 0.15 m of the local water level ARE the surface
     (no flattening); water-classed returns standing above it are ordinary land candidates.
  3. NO CREST FILL (72% of its promotions at the outwash were vegetation).
  4. SURFACE-BUILT GROUND instead of per-point probabilities:
       a. skeleton   one candidate per CELL x CELL m cell: the lowest return in a frame
                     detrended by the local slope; if it sits > 0.75 m below the cell's
                     next-lowest it is treated as low noise and the next is used. So no
                     patch with returns is left without a ground candidate.
       b. prune      compare each skeleton point with its 4 opposite-neighbour pairs
                     (N-S, E-W, both diagonals), linear interpolation between each pair:
                     above EVERY pair by > tol_up -> a lump (vegetation), removed;
                     below every pair by > tol_pit -> a pit (noise), removed.
                     A ridge lines up with the along-ridge pair, so it survives.
                     tol_up tightens with brush density. Iterated.
       c. densify    add returns within [-0.25, tol_add] m of the pruned skeleton surface;
                     tol_add tight in brush, looser in the open, + slope allowance.
     Brush density = share of returns more than 1 m above the skeleton surface (3x3 m).
Settings are chosen per held-out site from the other five (leave-one-site-out).

    python model/v2.py field              # offset fields for every site
    python model/v2.py sweep              # score settings grid, LOSO choice, build finals
"""
import itertools, json, os, sys
import numpy as np
EXCL = (7, 18, 21, 22)   # noise, high noise, SNOW, TEMPORAL EXCLUSION: never ground or surface (stress test 2026-09-26)
import pyarrow as pa, pyarrow.feather as feather
import rasterio
from scipy import ndimage
from scipy.interpolate import LinearNDInterpolator
sys.path.insert(0, 'model'); import sitekit as K

SITES = ['patch1', 'forest_tall', 'alder', 'meadow_shrub', 'bare_gentle', 'island']
DGGS = '/Volumes/Powder/Alaska GIS/Raster/AK_lidar/2021_Grewingk/2021_Grewingk_v4/Grewingk20211012DTM_1m.tif'


def grid_info(s):
    S = K.site_info(s); x0, x1, y0, y1 = S['bounds']
    return S, x0, x1, y0, y1, int(x1 - x0), int(y1 - y0)


def write_like(s, name, arr, nodata=-9999):
    with rasterio.open(f'site/{s}/grewingk_dtm.tif') as src: prof = src.profile
    prof.update(dtype='float32', nodata=nodata, count=1)
    with rasterio.open(f'site/{s}/{name}', 'w', **prof) as d:
        d.write(np.where(np.isfinite(arr), arr, nodata).astype('float32'), 1)


# ---------------------------------------------------------------- 1. offset field
def offset_field(s):
    from rasterio.windows import from_bounds
    S, x0, x1, y0, y1, NX, NY = grid_info(s); d = f'site/{s}'
    G, _ = K.rd(f'{d}/grewingk_dtm.tif'); V, _ = K.rd(f'{d}/vendor_dtm.tif')
    M, _ = K.rd(f'{d}/kbay_max.tif'); GM, _ = K.rd(f'{d}/grewingk_max.tif')
    with rasterio.open(DGGS) as src:
        D = src.read(1, window=from_bounds(x0, y0, x1, y1, src.transform), out_shape=G.shape).astype(float)
    D[D < -1000] = np.nan
    hi = ndimage.maximum_filter(np.nan_to_num(D, nan=9e3), 3); lo = ndimage.minimum_filter(np.nan_to_num(D, nan=-9e3), 3)
    water = ((hi - lo) < 0.005) & np.isfinite(D)
    far = ndimage.distance_transform_edt(~water) > 40
    Gs = ndimage.uniform_filter(np.nan_to_num(G, nan=np.nanmedian(G)), 3); gy, gx = np.gradient(Gs)
    sl = np.degrees(np.arctan(np.hypot(gx, gy)))
    bare = np.isfinite(G) & np.isfinite(V) & (M - V < 0.2) & (GM - G < 0.2) & (sl < 20) & far
    dz = np.where(bare, V - G, np.nan)
    site_med = float(np.nanmedian(dz))
    B = 50; nby, nbx = -(-NY // B), -(-NX // B)
    med = np.full((nby, nbx), np.nan); n = np.zeros((nby, nbx))
    for i in range(nby):
        for j in range(nbx):
            v = dz[i*B:(i+1)*B, j*B:(j+1)*B]; v = v[np.isfinite(v)]
            if v.size >= 20: med[i, j] = np.median(v); n[i, j] = v.size
    w = np.minimum(n, 400) / 400.0
    sig = 150 / B
    num = ndimage.gaussian_filter(np.nan_to_num(med) * w, sig, mode='nearest')
    den = ndimage.gaussian_filter(w, sig, mode='nearest')
    W0 = 0.15                                                  # prior weight toward the site median
    field_b = (num + W0 * site_med) / (den + W0)
    field = ndimage.zoom(field_b, (NY / nby, NX / nbx), order=1)[:NY, :NX]
    write_like(s, 'offset_field.tif', field)
    # how much better than a constant, on the bare cells themselves
    r_c = dz[bare] - site_med; r_f = (dz - field)[bare]
    iqr = lambda a: float(np.subtract(*np.percentile(a, [75, 25])))
    out = dict(site_median=round(site_med, 3), n_bare=int(bare.sum()), field_min=round(float(field.min()), 3),
               field_max=round(float(field.max()), 3), resid_iqr_const=round(iqr(r_c), 3), resid_iqr_field=round(iqr(r_f), 3))
    print(s, out, flush=True)
    return out


def field(s):
    F, _ = K.rd(f'site/{s}/offset_field.tif'); return F


# ---------------------------------------------------------------- scoring against the field
def score(s, D):
    d = f'site/{s}'
    G, _ = K.rd(f'{d}/grewingk_dtm.tif'); M, _ = K.rd(f'{d}/kbay_max.tif'); F = field(s)
    e = np.zeros_like(G, bool); e[30:-30, 30:-30] = True
    ok = e & np.isfinite(G) & np.isfinite(D) & ~K.flat_mask(G)
    err = D - F - G; can = M - F - G
    Gs = ndimage.uniform_filter(np.nan_to_num(G, nan=np.nanmedian(G)), 3); cu = Gs - ndimage.uniform_filter(Gs, 15)
    out = {}
    for lab, m in [('ALL', ok), ('canopy0.5-2', ok & (can >= 0.5) & (can < 2)), ('canopy2-5', ok & (can >= 2) & (can < 5)),
                   ('canopy5-10', ok & (can >= 5) & (can < 10)), ('canopy>10', ok & (can >= 10)),
                   ('crest', ok & (cu > 1)), ('channel', ok & (cu < -1))]:
        if m.sum() < 200: continue
        out[lab] = dict(high=round(100*float(np.mean(err[m] > 0.5)), 1), low=round(100*float(np.mean(err[m] < -0.5)), 1),
                        med=round(float(np.median(err[m])), 3))
    out['holes_pct'] = round(100*float(np.mean(np.isnan(D[e]))), 2)
    return out


def objective(sc):
    return sc['ALL']['high'] + 1.5 * sc['ALL']['low']       # cutting terrain counts more than leaving veg


# ---------------------------------------------------------------- 4. surface-built ground
class Site:
    """Points and fixed per-site context, loaded once and reused across settings."""
    def __init__(self, s):
        self.s = s; S, x0, x1, y0, y1, NX, NY = grid_info(s); self.S = S
        self.x0, self.y1, self.NX, self.NY = x0, y1, NX, NY
        P = np.load(f'site/{s}/pts.npz')
        x, y, z, c = P['x'], P['y'], P['z'], P['cls']
        keep = ~np.isin(c, EXCL)
        self.x, self.y, self.z, self.c = x[keep], y[keep], z[keep], c[keep]
        x, y, z, c = self.x, self.y, self.z, self.c
        self.cid = np.clip((y1 - y).astype(int), 0, NY-1) * NX + np.clip((x - x0).astype(int), 0, NX-1)
        # water surface returns: within 0.15 m of the local water level
        w = c == 9; self.water = np.zeros(len(z), bool)
        if w.any():
            W = np.zeros(NX*NY); Wn = np.zeros(NX*NY); np.add.at(W, self.cid[w], z[w]); np.add.at(Wn, self.cid[w], 1)
            Wl = ndimage.median_filter(np.where(Wn > 0, W/np.maximum(Wn, 1), np.nan).reshape(NY, NX), 15)
            Wl = np.where(np.isnan(Wl), np.median(z[w]), Wl).ravel()
            self.water = w & (np.abs(z - Wl[self.cid]) <= 0.15)
        self.land = ~self.water
        # slope trend from the vendor TIN (shape only), 5 m smoothing
        V, _ = K.rd(f'site/{s}/vendor_dtm.tif'); Vf = np.where(np.isnan(V), np.nanmedian(V), V)
        gyr, gx = np.gradient(ndimage.uniform_filter(Vf, 5)); self.gx, self.gy = gx, -gyr

    def skeleton(self, cell):
        x, y, z = self.x[self.land], self.y[self.land], self.z[self.land]
        n = int(np.ceil(self.NX / cell)); m = int(np.ceil(self.NY / cell))
        ci = np.clip(((x - self.x0) / cell).astype(int), 0, n-1); cj = np.clip(((self.y1 - y) / cell).astype(int), 0, m-1)
        k = cj * n + ci
        cx = self.x0 + (ci + 0.5) * cell; cy = self.y1 - (cj + 0.5) * cell
        gi = np.clip((cx - self.x0).astype(int), 0, self.NX-1); gj = np.clip((self.y1 - cy).astype(int), 0, self.NY-1)
        GX, GY = self.gx[gj, gi], self.gy[gj, gi]
        zd = z - (GX * (x - cx) + GY * (y - cy))
        order = np.lexsort((zd, k)); ks = k[order]
        first = np.r_[True, ks[1:] != ks[:-1]]
        i1 = order[first]
        # second-lowest in the same cell (if any)
        pos = np.flatnonzero(first); nxt = pos + 1
        has2 = (nxt < len(order)) & (np.r_[ks[1:], -1][pos] == ks[pos])
        pick = i1.copy()
        i2 = order[np.minimum(nxt, len(order)-1)]
        low_noise = has2 & (zd[i2] - zd[i1] > 0.75)
        pick[low_noise] = i2[low_noise]
        cells = k[pick]
        grid = -np.ones(n*m, np.int64); grid[cells] = pick
        return dict(n=n, m=m, grid=grid.reshape(m, n), x=x, y=y, z=z)

    def prune(self, sk, tol_up_dense, tol_up_open, tol_pit, brush_cell):
        g = sk['grid']; x, y, z = sk['x'], sk['y'], sk['z']; m, n = g.shape
        alive = g >= 0
        pairs = [((0, 1), (0, -1)), ((1, 0), (-1, 0)), ((1, 1), (-1, -1)), ((1, -1), (-1, 1))]
        for it in range(4):
            idx = np.where(alive, g, 0)
            X, Y, Z = x[idx], y[idx], z[idx]
            min_r = np.full((m, n), np.inf); max_r = np.full((m, n), -np.inf); npair = np.zeros((m, n), int)
            for (a, b) in pairs:
                # nearest alive neighbour in each direction, up to 2 steps
                def nb(dj, di):
                    """Nearest alive skeleton point 1 (else 2) cells away in direction (dj, di)."""
                    NXa = np.full((m, n), np.nan); NYa = NXa.copy(); NZa = NXa.copy()
                    for step in (2, 1):                      # 1-step overwrites 2-step where alive
                        sj, si = dj*step, di*step
                        dst = (slice(max(0, -sj), m - max(0, sj)), slice(max(0, -si), n - max(0, si)))
                        src = (slice(max(0, sj), m - max(0, -sj)), slice(max(0, si), n - max(0, -si)))
                        tX = np.full((m, n), np.nan); tY = tX.copy(); tZ = tX.copy(); tA = np.zeros((m, n), bool)
                        tX[dst] = X[src]; tY[dst] = Y[src]; tZ[dst] = Z[src]; tA[dst] = alive[src]
                        NXa = np.where(tA, tX, NXa); NYa = np.where(tA, tY, NYa); NZa = np.where(tA, tZ, NZa)
                    return NXa, NYa, NZa
                ax_, ay_, az_ = nb(*a); bx_, by_, bz_ = nb(*b)
                ok = np.isfinite(az_) & np.isfinite(bz_) & alive
                vx, vy = bx_ - ax_, by_ - ay_; L2 = vx*vx + vy*vy
                t = np.clip(((X - ax_)*vx + (Y - ay_)*vy) / np.where(L2 > 0, L2, 1), 0, 1)
                pred = az_ + t * (bz_ - az_)
                r = Z - pred
                min_r = np.where(ok, np.minimum(min_r, r), min_r); max_r = np.where(ok, np.maximum(max_r, r), max_r)
                npair += ok
            # brush density per skeleton cell (fraction of returns > 1 m above the skeleton surface)
            bd = brush_cell
            tol_up = tol_up_open + (tol_up_dense - tol_up_open) * bd
            bump = alive & (npair >= 2) & (min_r > tol_up)
            pit = alive & (npair >= 2) & (max_r < -tol_pit)
            if not (bump | pit).any(): break
            alive &= ~(bump | pit)
        return alive


    def prune_quad(self, sk, tol_up_dense, tol_up_open, tol_pit, brush_cell, rad=2, iters=4):
        """Lump/pit test against a local QUADRATIC surface fitted to the surrounding skeleton
        points (up to (2*rad+1)^2 - 1 neighbours, centre excluded). A ridge makes its flank
        neighbours imply a crest, so a true crest point fits; a vegetation lump stands above
        any smooth surface its neighbours define. Points already flagged drop out of the fits."""
        g = sk['grid']; x, y, z = sk['x'], sk['y'], sk['z']; m, n = g.shape
        alive = g >= 0
        idx = np.where(alive, g, 0); X, Y, Z = x[idx], y[idx], z[idx]
        tol_up = tol_up_open + (tol_up_dense - tol_up_open) * brush_cell
        for it in range(iters):
            ATA = np.zeros((m, n, 6, 6)); ATb = np.zeros((m, n, 6)); cnt = np.zeros((m, n))
            for dj in range(-rad, rad + 1):
                for di in range(-rad, rad + 1):
                    if dj == 0 and di == 0: continue
                    dst = (slice(max(0, -dj), m - max(0, dj)), slice(max(0, -di), n - max(0, di)))
                    src = (slice(max(0, dj), m - max(0, -dj)), slice(max(0, di), n - max(0, -di)))
                    a = np.zeros((m, n), bool); a[dst] = alive[src]
                    dx = np.zeros((m, n)); dy = np.zeros((m, n)); zz = np.zeros((m, n))
                    dx[dst] = X[src] - X[dst]; dy[dst] = Y[src] - Y[dst]; zz[dst] = Z[src]
                    a &= alive
                    B = np.stack([np.ones_like(dx), dx, dy, dx*dx, dx*dy, dy*dy], -1) * a[..., None]
                    ATA += B[..., :, None] * B[..., None, :]; ATb += B * zz[..., None]; cnt += a
            ok = alive & (cnt >= 8)
            # A point with too few alive neighbours to fit could never be tested, so a lone shrub
            # top among pruned cells survived as a cone (Hig, 2026-09-25, 59.52474 -151.15386).
            # Test it against the median of alive points within 4 cells instead.
            thin = alive & ~ok
            if thin.any():
                Za = np.where(alive, Z, np.nan)
                pad = np.pad(Za, 4, constant_values=np.nan)
                win = np.lib.stride_tricks.sliding_window_view(pad, (9, 9))
                med = np.nanmedian(win.reshape(m, n, -1), axis=-1)
                nal = np.sum(np.isfinite(win.reshape(m, n, -1)), axis=-1) - 1
                iso_up = thin & (nal >= 3) & (Z - med > tol_up + 0.5)
                iso_lone = thin & (nal < 3)                  # nothing to stand on: not ground evidence
                alive &= ~(iso_up | iso_lone)
            ATA[ok] += np.eye(6) * 1e-6
            pred = np.full((m, n), np.nan)
            sol = np.linalg.solve(ATA[ok], ATb[ok][..., None])[..., 0]
            pred[ok] = sol[:, 0]                                  # value of the fit at the centre (dx=dy=0)
            r = Z - pred
            bump = ok & (r > tol_up); pit = ok & (r < -tol_pit)
            if not (bump | pit).any(): break
            alive &= ~(bump | pit)
        return alive


    def remove_walled(self, sk, alive, drop, reach):
        """Remove survivors that stand on WALLS in every direction: in each of the 8 directions
        the surface falls by more than `drop` metres to a surviving point within `reach` cells.
        A crown (Hig, 59.58686 -151.17293: 10 m wide, 3-4 m up, single returns, 4 cm plane
        RMS -- it passes every tightness test) drops ~3 m within 1-2 cells on all sides; a real
        summit falls away gradually (~1 m in 6 m) and a crag or ridge continues in at least
        one direction. Iterated from the outside in, so a whole crown goes, not just its rim."""
        g = sk['grid']; z = sk['z']; m, n = g.shape
        dirs = [(0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)]
        for it in range(8):
            Z = np.where(alive, z[np.where(alive, g, 0)], np.nan)
            walled = alive.copy()
            for dj, di in dirs:
                best = np.full((m, n), np.nan)                 # highest survivor-drop found in this direction
                for k in range(1, reach + 1):
                    sj, si = dj*k, di*k
                    dst = (slice(max(0, -sj), m - max(0, sj)), slice(max(0, -si), n - max(0, si)))
                    src = (slice(max(0, sj), m - max(0, -sj)), slice(max(0, si), n - max(0, -si)))
                    nb = np.full((m, n), np.nan); nb[dst] = Z[src]
                    d = Z - nb                                  # drop from this point to the neighbour
                    best = np.where(np.isnan(best), d, np.fmax(best, np.where(np.isnan(d), -np.inf, d)))
                # a direction with no survivor in reach is not a wall (edge of data): keep
                walled &= np.isfinite(best) & (best > drop)
            if not walled.any(): break
            alive = alive & ~walled
        return alive

    def surface(self, sk, alive):
        g = sk['grid']; idx = g[alive]
        px, py, pz = sk['x'][idx], sk['y'][idx], sk['z'][idx]
        f = LinearNDInterpolator(np.c_[px, py], pz)
        gx = self.x0 + np.arange(self.NX) + 0.5; gy = self.y1 - np.arange(self.NY) - 0.5
        XX, YY = np.meshgrid(gx, gy)
        return f(XX, YY)

    def run(self, cell=2.0, tol_up_dense=0.15, tol_up_open=0.5, tol_pit=0.5, tol_add_dense=0.1, tol_add_open=0.3, slope_k=0.3, prune='quad', rad=2, keep_runs=0, wall_drop=0.0, wall_reach=3):
        sk = self.skeleton(cell)
        # first surface from the unpruned skeleton, for brush density
        alive0 = sk['grid'] >= 0
        S0 = self.surface(sk, alive0); S0f = np.where(np.isfinite(S0), S0, np.nanmedian(S0))
        h0 = self.z - S0f.ravel()[self.cid]
        above = np.bincount(self.cid, weights=(h0 > 1.0) & self.land, minlength=self.NX*self.NY).reshape(self.NY, self.NX)
        tot = np.bincount(self.cid, weights=self.land.astype(float), minlength=self.NX*self.NY).reshape(self.NY, self.NX)
        brush = ndimage.uniform_filter(above, 3) / np.maximum(ndimage.uniform_filter(tot, 3), 1e-6)
        m, n = sk['grid'].shape
        bcell = ndimage.zoom(brush, (m / self.NY, n / self.NX), order=1)[:m, :n]
        alive = (self.prune_quad(sk, tol_up_dense, tol_up_open, tol_pit, bcell, rad) if prune == 'quad'
                 else self.prune(sk, tol_up_dense, tol_up_open, tol_pit, bcell))
        if wall_drop:
            alive = self.remove_walled(sk, alive, wall_drop, wall_reach)
        if keep_runs:
            # A shrub is a lump one or two skeleton cells across; a real knob or ridge that the
            # smooth-surface test flags shows up as a CONNECTED RUN of flagged cells. Restore runs.
            flagged = (sk['grid'] >= 0) & ~alive
            lab, nlab = ndimage.label(flagged, structure=np.ones((3, 3)))
            if nlab:
                size = ndimage.sum(flagged, lab, np.arange(1, nlab + 1))
                big = np.r_[False, size >= keep_runs]
                alive |= big[lab]
        S1 = self.surface(sk, alive); S1f = np.where(np.isfinite(S1), S1, np.nanmedian(S1))
        # densify
        row = (self.y1 - self.y) - 0.5; col = (self.x - self.x0) - 0.5
        h = self.z - ndimage.map_coordinates(S1f, [row, col], order=1, mode='nearest')
        sl = np.hypot(self.gx, self.gy).ravel()[self.cid]            # tan(slope)
        b = brush.ravel()[self.cid]
        tol_add = tol_add_open + (tol_add_dense - tol_add_open) * b + slope_k * sl * cell * 0.5
        ground = self.land & (h >= -0.25) & (h <= tol_add)
        idx = sk['grid'][alive]                                        # skeleton points are ground too
        sel = np.zeros(len(self.z), bool); sel |= ground
        return sel, sk, alive, idx, brush

    def dtm(self, sel, sk, idx, tag):
        """TIN of: densified ground + surviving skeleton + water-surface returns."""
        X = np.r_[self.x[sel | self.water], sk['x'][idx]]; Y = np.r_[self.y[sel | self.water], sk['y'][idx]]
        Z = np.r_[self.z[sel | self.water], sk['z'][idx]]
        t = pa.table({'X': X, 'Y': Y, 'Z': Z, 'Classification': np.full(len(X), 2, np.uint8)})
        d = f'site/{self.s}'; fp = f'{d}/v2_{tag}.feather'; feather.write_feather(t, fp)
        K.tin({"type": "readers.arrow", "filename": fp}, f'{d}/dtm_{tag}.tif', self.S, d, f'dtm_{tag}')
        os.remove(fp)
        D, _ = K.rd(f'{d}/dtm_{tag}.tif'); return D


GRID = dict(cell=[1.5, 2.0, 3.0], tol_up_dense=[0.1, 0.2], tol_up_open=[0.35, 0.6],
            tol_add_dense=[0.05, 0.12], tol_add_open=[0.2, 0.35])


def sweep():
    # coordinate search around a default, not the full product (48 x 6 builds would take hours)
    base = dict(cell=2.0, tol_up_dense=0.15, tol_up_open=0.5, tol_pit=0.5, tol_add_dense=0.1, tol_add_open=0.3, slope_k=0.3)
    combos = [dict(base)]
    for k, vals in GRID.items():
        for v in vals:
            c = dict(base); c[k] = v
            if c not in combos: combos.append(c)
    res = {}
    for s in SITES:
        site = Site(s); res[s] = []
        vend, _ = K.rd(f'site/{s}/vendor_dtm.tif'); res_v = score(s, vend)
        for i, c in enumerate(combos):
            sel, sk, alive, idx, brush = site.run(**c)
            D = site.dtm(sel, sk, idx, f'v2c{i}')
            sc = score(s, D); res[s].append(dict(combo=c, score=sc))
            os.remove(f'site/{s}/dtm_v2c{i}.tif')
            print('%-13s c%-2d %s  ALL %4.1f/%4.1f (vendor %4.1f/%4.1f)  crest %s  canopy2-5 %s' % (
                s, i, json.dumps(c), sc['ALL']['high'], sc['ALL']['low'], res_v['ALL']['high'], res_v['ALL']['low'],
                sc.get('crest', {}).get('low'), sc.get('canopy2-5', {}).get('high')), flush=True)
        res[s + '_vendor'] = res_v
        json.dump(dict(combos=combos, res=res), open('site/v2_sweep.json', 'w'), indent=1)
    return combos, res


def choose_and_build(combos, res):
    """Leave-one-site-out: for each site, the combo with the best mean objective on the OTHER sites."""
    out = {}
    for s in SITES:
        others = [o for o in SITES if o != s]
        mean = [np.mean([objective(res[o][i]['score']) for o in others]) for i in range(len(combos))]
        best = int(np.argmin(mean)); c = combos[best]
        site = Site(s); sel, sk, alive, idx, brush = site.run(**c)
        D = site.dtm(sel, sk, idx, 'v2')
        # record what changed relative to the vendor classification, for the review
        vend = site.c == 2
        out[s] = dict(combo=c, score=score(s, D), vendor=res[s + '_vendor'],
                      promoted=int(np.sum(sel & ~vend & site.land)), demoted=int(np.sum(vend & ~sel)),
                      skeleton=int((sk['grid'] >= 0).sum()), pruned=int(((sk['grid'] >= 0) & ~alive).sum()))
        # map of pruned skeleton cells and brush, for review
        write_like(s, 'v2_brush.tif', brush)
        print(s, json.dumps(out[s]), flush=True)
    json.dump(out, open('site/v2_final.json', 'w'), indent=1)


if __name__ == '__main__':
    if sys.argv[1] == 'field':
        json.dump({s: offset_field(s) for s in SITES}, open('site/offset_fields.json', 'w'), indent=1)
    elif sys.argv[1] == 'sweep':
        combos, res = sweep(); choose_and_build(combos, res)
    elif sys.argv[1] == 'build':
        d = json.load(open('site/v2_sweep.json')); choose_and_build(d['combos'], d['res'])
