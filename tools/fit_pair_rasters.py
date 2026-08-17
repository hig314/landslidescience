"""Robust per-cell fit of ITS_LIVE Level-2 pairs -> velocity / seasonality / trend.

One weighted least-squares inversion per grid cell yields all three rasters
the standard composites provide, but built from the raw image pairs with our
own filtering — and structured so that a long-baseline measurement acts as a
CLOSURE CONSTRAINT rather than an independent sample.

Model, per velocity component:

    v(t) = v0 + k*(t - t_ref) + a_cos*cos(2*pi*t) + a_sin*sin(2*pi*t)

A measurement does not observe v at an instant: it observes the MEAN of v
over its own interval [t1, t2]. That mean is analytic, so each pair becomes
one exact linear equation:

    row = [ 1,  tm - t_ref,  Sc,  Ss ]
    Sc  = ( sin(2*pi*t2) - sin(2*pi*t1) ) / (2*pi*dt)
    Ss  = ( cos(2*pi*t1) - cos(2*pi*t2) ) / (2*pi*dt)

This does the right thing automatically: a ~365-day pair has Sc = Ss ~ 0, so
it constrains the MEAN and TREND while saying nothing about seasonality; a
sub-monthly pair carries nearly full seasonal leverage. No pair has to be
assigned to a month, and nothing is thrown away for spanning several.

Robustness (the part the standard product does differently):
  * SEED from short pairs only. Skip/lock blunders cluster near zero and at
    long dt they are the MAJORITY, so any estimator seeded on the full
    population can converge on them and reject the real motion. Short pairs
    do not skip/lock, so they define the reference.
  * Then IRLS (Tukey biweight) over all pairs, weighted by the modelled
    random error (~25.5 m / dt), which is what makes long baselines valuable
    for slow ice. Precision weighting plus robust rejection reproduces
    "long for slow, short for fast" without a hand-set dt cutoff.
  * Residuals are judged in ALONG-/CROSS-flow coordinates with a generous
    directional tolerance, so genuine turning near a retreating face is not
    mistaken for a blunder.
  * Sensor prior: Landsat 5/7 pairs carry a measured ~4-40x higher blunder
    rate here, so they start downweighted rather than excluded.

Outputs (GeoTIFF, EPSG:3413) plus a comparison against ITS_LIVE's own
composite at the same cells:
    <name>_v0.tif      speed at t_ref (m/yr)
    <name>_amp.tif     seasonal amplitude (m/yr)
    <name>_trend.tif   dv/dt (m/yr per yr)
    <name>_n.tif       measurements surviving robust weighting
    <name>_resid.tif   weighted residual RMS (m/yr)

Usage: python tools/fit_pair_rasters.py [--name=columbia_pairs]
                                        [--since=2015] [--ref=2024.5]
"""
import json
import sys
from pathlib import Path

import numpy as np
import zarr

ROOT = Path(__file__).resolve().parent.parent / 'data' / 'glaciers' / 'experiments'
SINCE = 2015.0
T_REF = 2024.5
SEED_DT = 64.0          # days — pairs trusted to define the reference
SEED_DT_FALLBACK = 128.0
MIN_SEED = 12
MIN_TOTAL = 25
ERR_BASE_M = 25.5       # autoRIFT optical displacement error, metres
ERR_FLOOR = 8.0         # m/yr — keeps long pairs from dominating outright
TUKEY_C = 3.0           # tighter than the classic 4.685: blunders here are
                        # a MAJORITY at long dt, not a tail
N_IRLS = 4
# The admissible separation per cell is measured, not assumed — see fit_cell.
DT_CAP_MIN = 32.0       # never restrict below this — short pairs always allowed
MIN_BIN = 8             # measurements needed before a dt bin gets a vote
SEASON_RIDGE = 4.0      # damps the seasonal pair on near-stationary ground,
                        # where 4 free parameters otherwise fit pure noise
# Directional/robust tolerance: sigma = max(floor, frac * |v|)
TOL_FLOOR = 25.0        # m/yr
TOL_FRAC = 0.35
SENSOR_DOWNWEIGHT = {'5': 0.25, '7': 0.45}   # by mission id of either image


def design(t1, t2, t_ref):
    tm = 0.5 * (t1 + t2)
    dt = np.maximum(t2 - t1, 1e-6)
    tp = 2 * np.pi
    Sc = (np.sin(tp * t2) - np.sin(tp * t1)) / (tp * dt)
    Ss = (np.cos(tp * t1) - np.cos(tp * t2)) / (tp * dt)
    return np.column_stack([np.ones_like(tm), tm - t_ref, Sc, Ss]).astype(np.float64)


def solve_w(A, y, w):
    """Weighted least squares, 4 unknowns; None if ill-conditioned."""
    sw = np.sqrt(w)
    Aw = A * sw[:, None]
    yw = y * sw
    try:
        AtA = Aw.T @ Aw
        # Ridge nudge keeps degenerate seasonal columns (few pairs, all one
        # season) from blowing up rather than silently returning nonsense.
        sc = max(1.0, np.trace(AtA) / 4)
        AtA[np.diag_indices(4)] += 1e-6 * sc
        # Extra damping on the seasonal pair only: amplitude must be DEMANDED
        # by the data, not conjured from noise on stationary rock.
        AtA[2, 2] += SEASON_RIDGE * 1e-3 * sc
        AtA[3, 3] += SEASON_RIDGE * 1e-3 * sc
        return np.linalg.solve(AtA, Aw.T @ yw)
    except np.linalg.LinAlgError:
        return None


def fit_cell(A, vx, vy, dt_d, base_w, seed_mask):
    """Returns (cx, cy, n_eff, resid_rms) or None."""
    if seed_mask.sum() < MIN_SEED or vx.size < MIN_TOTAL:
        return None
    cx = solve_w(A[seed_mask], vx[seed_mask], base_w[seed_mask])
    cy = solve_w(A[seed_mask], vy[seed_mask], base_w[seed_mask])
    if cx is None or cy is None:
        return None

    # Per-cell admissible separation, derived EMPIRICALLY rather than from a
    # displacement guess. A fixed "max trackable displacement" fails because
    # skip/lock is governed by the local surface pattern, not distance alone:
    # at Columbia the fast trunk holds agreement to ~600 m of displacement
    # while a medium-speed cell has already degraded by ~175 m. So instead we
    # ask the data where the long baselines start disagreeing with the short
    # ones — which is exactly the closure question, and exactly the recipe
    # ITS_LIVE uses internally (Gardner 2025, Appendix A "MaxDtFilter"):
    #   bin by dt, project onto the short-baseline flow direction, and cut at
    #   the first bin whose median +/- MAD stops overlapping the reference.
    seed_speed = float(np.hypot(cx[0], cy[0]))
    ref_ux, ref_uy = (cx[0] / seed_speed, cy[0] / seed_speed) if seed_speed > 1e-6 else (1.0, 0.0)
    proj = vx * ref_ux + vy * ref_uy          # along reference flow
    edges = [0.0, 16.0, 32.0, 64.0, 128.0, 256.0, np.inf]
    stats = []
    for b in range(len(edges) - 1):
        m = (dt_d >= edges[b]) & (dt_d < edges[b + 1])
        if m.sum() >= MIN_BIN:
            med = float(np.median(proj[m]))
            mad = float(np.median(np.abs(proj[m] - med))) * 1.4826
            stats.append((b, med, max(mad, TOL_FLOOR * 0.5)))
        else:
            stats.append((b, None, None))
    ref = next((st for st in stats if st[1] is not None), None)
    dt_cap = np.inf
    if ref is not None:
        rlo, rhi = ref[1] - ref[2], ref[1] + ref[2]
        for b, med, mad in stats:
            if b <= ref[0] or med is None:
                continue
            if med + mad < rlo or med - mad > rhi:   # bounds no longer overlap
                dt_cap = edges[b]
                break
    dt_cap = max(dt_cap, DT_CAP_MIN)
    adm = dt_d <= dt_cap
    if adm.sum() < MIN_TOTAL:
        adm = seed_mask.copy()
        if adm.sum() < MIN_SEED:
            return None
    A, vx, vy, base_w = A[adm], vx[adm], vy[adm], base_w[adm]

    w = base_w.copy()
    for _ in range(N_IRLS):
        rx = vx - A @ cx
        ry = vy - A @ cy
        # Along-/cross-flow split about the current modelled direction.
        px = A @ cx
        py = A @ cy
        sp = np.hypot(px, py) + 1e-9
        ux, uy = px / sp, py / sp
        r_along = rx * ux + ry * uy
        r_cross = -rx * uy + ry * ux
        tol = np.maximum(TOL_FLOOR, TOL_FRAC * sp)
        # Cross-flow is nearly pure noise, so judge it tighter; along-flow
        # holds real seasonal variability and turning, so stay generous.
        z = np.hypot(r_along / (1.6 * tol), r_cross / tol)
        u = np.clip(z / TUKEY_C, 0, 1)
        w = base_w * (1 - u * u) ** 2
        if w.sum() <= 0:
            return None
        ncx = solve_w(A, vx, w)
        ncy = solve_w(A, vy, w)
        if ncx is None or ncy is None:
            return None
        cx, cy = ncx, ncy

    keep = w > 0.05 * base_w.max()
    n_eff = int(keep.sum())
    if n_eff < MIN_TOTAL:
        return None
    rx = vx - A @ cx
    ry = vy - A @ cy
    rr = np.sqrt(np.average(rx[keep] ** 2 + ry[keep] ** 2,
                            weights=w[keep])) if keep.any() else np.nan
    return cx, cy, n_eff, rr


def main(name, since, t_ref):
    z = zarr.open(str(ROOT / f'{name}.zarr'), mode='r')
    g = z.attrs['grid']
    ny, nx = g['ny'], g['nx']
    import datetime as _dt
    base = _dt.date(1970, 1, 1)
    md = np.asarray(z['mid_date'])
    tmid = np.array([(base + _dt.timedelta(days=float(x))).year +
                     ((base + _dt.timedelta(days=float(x))).timetuple().tm_yday - 1) / 365.25
                     for x in md])
    ddt = np.asarray(z['date_dt']).astype(np.float64)
    sel = np.nonzero(tmid >= since)[0]
    tmid, ddt = tmid[sel], ddt[sel]
    t1 = tmid - ddt / 2 / 365.25
    t2 = tmid + ddt / 2 / 365.25
    A = design(t1, t2, t_ref)
    sig = np.maximum(ERR_FLOOR, ERR_BASE_M * 365.25 / np.maximum(ddt, 1.0))
    base_w = 1.0 / sig ** 2
    try:
        s1 = np.asarray(z['satellite_img1'])[sel]
        s2 = np.asarray(z['satellite_img2'])[sel]
        for mid, f in SENSOR_DOWNWEIGHT.items():
            hit = (np.char.startswith(s1.astype(str), mid) |
                   np.char.startswith(s2.astype(str), mid))
            base_w[hit] *= f
        print(f'   sensor downweighting applied to {int((base_w < 1/sig**2).sum())} pairs')
    except Exception as exc:
        print(f'   (no sensor info: {exc})')
    seed = ddt <= SEED_DT
    if seed.sum() < MIN_SEED * 3:
        seed = ddt <= SEED_DT_FALLBACK
    print(f'== {name}: {sel.size} pairs since {since}, seed pool {int(seed.sum())}, '
          f'grid {nx} x {ny}, t_ref {t_ref}')

    out = {k: np.full((ny, nx), np.nan, np.float32)
           for k in ('v0', 'amp', 'trend', 'n', 'resid', 'phase')}
    BAND = 10
    for y0 in range(0, ny, BAND):
        y1 = min(ny, y0 + BAND)
        vx_b = np.asarray(z['vx'][:, y0:y1, :])[sel].astype(np.float64)
        vy_b = np.asarray(z['vy'][:, y0:y1, :])[sel].astype(np.float64)
        for r in range(y1 - y0):
            for c in range(nx):
                vx = vx_b[:, r, c]
                vy = vy_b[:, r, c]
                ok = (vx != -32767) & (vy != -32767) & (np.abs(vx) < 20000) & (np.abs(vy) < 20000)
                if ok.sum() < MIN_TOTAL:
                    continue
                res = fit_cell(A[ok], vx[ok], vy[ok], ddt[ok], base_w[ok], seed[ok])
                if res is None:
                    continue
                cx, cy, n_eff, rr = res
                i = y0 + r
                out['v0'][i, c] = np.hypot(cx[0], cy[0])
                # Seasonal amplitude of the SPEED-aligned component: project
                # the seasonal vector onto the mean-flow direction, which is
                # what "does it speed up in summer" actually means.
                sp = np.hypot(cx[0], cy[0]) + 1e-9
                ux, uy = cx[0] / sp, cy[0] / sp
                ac = cx[2] * ux + cy[2] * uy
                as_ = cx[3] * ux + cy[3] * uy
                out['amp'][i, c] = np.hypot(ac, as_)
                out['phase'][i, c] = (np.degrees(np.arctan2(as_, ac)) % 360) / 360 * 365.25
                out['trend'][i, c] = cx[1] * ux + cy[1] * uy
                out['n'][i, c] = n_eff
                out['resid'][i, c] = rr
        print(f'   rows {y1}/{ny}', flush=True)

    write_tifs(name, g, out)
    compare(name, g, out)


def write_tifs(name, g, out):
    try:
        from osgeo import gdal, osr
    except ImportError:
        np.savez(ROOT / f'{name}_fit.npz', **out, grid=json.dumps(g))
        print(f'   GDAL unavailable — wrote {name}_fit.npz instead')
        return
    gdal.UseExceptions()
    srs = osr.SpatialReference(); srs.ImportFromEPSG(3413)
    drv = gdal.GetDriverByName('GTiff')
    for k, arr in out.items():
        p = ROOT / f'{name}_{k}.tif'
        ds = drv.Create(str(p), g['nx'], g['ny'], 1, gdal.GDT_Float32,
                        options=['COMPRESS=DEFLATE', 'TILED=YES'])
        ds.SetGeoTransform((g['x0'], g['dx'], 0, g['y0_north'], 0, -g['dx']))
        ds.SetProjection(srs.ExportToWkt())
        b = ds.GetRasterBand(1); b.SetNoDataValue(float('nan')); b.WriteArray(arr)
        ds.FlushCache()
    print(f'   wrote {len(out)} GeoTIFFs to {ROOT}')


def compare(name, g, out):
    """Head-to-head against ITS_LIVE's own composite over the same cells."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from build_glacier_tracers import HttpZarrStore, cube_url, tile_center_for
        import math
        cx0 = math.floor(g['x0'] / 100000.) * 100000. + 50000.
        cy0 = math.floor((g['y0_north'] - g['ny'] * g['dx']) / 100000.) * 100000. + 50000.
        url = cube_url(cx0, cy0)
        if url is None:
            print('   (no composite cube for comparison)')
            return
        c = zarr.open(HttpZarrStore(url), mode='r')
        gx = np.asarray(c['x']); gy = np.asarray(c['y'])
        j0 = int(np.argmin(np.abs(gx - (g['x0'] + g['dx'] / 2))))
        i0 = int(np.argmin(np.abs(gy - (g['y0_north'] - g['dx'] / 2))))
        sl = (slice(i0, i0 + g['ny']), slice(j0, j0 + g['nx']))
        their_v = np.asarray(c['v'][-1][sl]).astype(np.float32)
        their_v[their_v == c['v'].fill_value] = np.nan
        their_amp = np.asarray(c['v_amp'][sl]).astype(np.float32)
        their_amp[their_amp >= 32767] = np.nan
        ours_v, ours_amp = out['v0'], out['amp']
        both = np.isfinite(ours_v) & np.isfinite(their_v)
        print('\n--- ours vs ITS_LIVE composite ---')
        print(f'   cells fitted: {int(np.isfinite(ours_v).sum()):,} of {ours_v.size:,}'
              f'   overlap with theirs: {int(both.sum()):,}')
        if both.sum() > 100:
            d = ours_v[both] - their_v[both]
            print(f'   speed  median ours {np.median(ours_v[both]):.0f} vs theirs '
                  f'{np.median(their_v[both]):.0f} m/yr;  median diff {np.median(d):+.0f}, '
                  f'p5/p95 {np.percentile(d,5):+.0f}/{np.percentile(d,95):+.0f}')
            r = np.corrcoef(ours_v[both], their_v[both])[0, 1]
            print(f'   correlation {r:.3f}')
            ba = both & np.isfinite(ours_amp) & np.isfinite(their_amp)
            if ba.sum() > 100:
                print(f'   amplitude median ours {np.median(ours_amp[ba]):.0f} vs theirs '
                      f'{np.median(their_amp[ba]):.0f} m/yr')
            # "Ridiculous" check: their amplitude exceeding their own speed
            silly_t = np.isfinite(their_amp) & np.isfinite(their_v) & (their_amp > their_v)
            silly_o = np.isfinite(ours_amp) & np.isfinite(ours_v) & (ours_amp > ours_v)
            print(f'   cells where seasonal amplitude EXCEEDS mean speed '
                  f'(implies part-year reversal): theirs {int(silly_t.sum()):,}, '
                  f'ours {int(silly_o.sum()):,}')
    except Exception as exc:
        print(f'   comparison unavailable: {type(exc).__name__}: {exc}')


if __name__ == '__main__':
    name, since, t_ref = 'columbia_pairs', SINCE, T_REF
    for a in sys.argv[1:]:
        if a.startswith('--name='): name = a.split('=', 1)[1]
        elif a.startswith('--since='): since = float(a.split('=', 1)[1])
        elif a.startswith('--ref='): t_ref = float(a.split('=', 1)[1])
    main(name, since, t_ref)
