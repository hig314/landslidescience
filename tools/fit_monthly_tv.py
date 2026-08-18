"""Monthly velocity per cell by TV-regularized inversion of raw image pairs.

Replaces the parametric fit (v0 + trend + annual sinusoid), which could not
represent a REGIME CHANGE: a cell running 3 km/yr until 2019 and then stopping
has no good member of that family, so it came out as a compromised mean, a
spurious trend, or whichever regime won the robust vote — which is why the
ice front's retreat never appeared no matter how the filtering was tuned.

Two ideas, both from Hig:

1. CLOSURE. A measurement does not observe an instant; it observes the MEAN
   over its own interval. So each pair contributes one linear equation
       sum_m (overlap_m / duration) * v_m  =  v_measured
   and a long-baseline pair is exactly the constraint that the short hops
   inside it must sum to the long hop. No pair is assigned to a month, and
   none is discarded for spanning several.

2. TOTAL VARIATION, not smoothness. The regularizer penalizes
   |v_{m+1} - v_m| (L1), NOT its square. That distinction is the whole
   point: L1/TV preserves step changes while suppressing noise, so a cell
   that stops in 2019 gets a STEP and a noisy cell gets smoothed — no
   change-point detection, no regime logic, no parametric family to violate.

CONTEXT-AWARE SCREENING (Hig's visual discriminator, made computable):
near-stationary values are artifacts in areas of fast motion but are
indistinguishable from real slow motion where nothing is fast. So a
measurement is judged against its LOCAL, TIME-VARYING context — a robust
speed from short pairs (which do not skip/lock), spatially smoothed, per
year. The smoothing IS the spatial-coherence test: an isolated zero sees
fast context and is suspect, while a coherent patch of zeros sees slow
context and is believed. Retreat therefore resolves itself — once a whole
neighbourhood transitions, the zeros become the new context and stop being
flagged.

Usage:
    python tools/fit_monthly_tv.py --test            # a few diagnostic cells
    python tools/fit_monthly_tv.py                   # full grid
"""
import datetime as dt
import gzip
import json
import sys
from pathlib import Path

import numpy as np
import zarr
from scipy import ndimage, sparse

ROOT = Path(__file__).resolve().parent.parent / 'data' / 'glaciers' / 'experiments'
T0, T1 = 2015.0, 2025.5          # inversion window
DT_MONTH = 1.0 / 12
CTX_SMOOTH = 3                   # +/- cells for the context neighbourhood
CTX_SHORT_DT = 64.0              # days — pairs trusted to define context
CTX_FAST = 150.0                 # m/yr — above this a cell counts as "fast context"
CTX_ZERO_FRAC = 0.15             # below this fraction of context, a value is suspect
MIN_OBS = 30
VMAX = 20000.0
LAM = 0.35                       # TV strength, relative to the data term
N_IRLS = 6
TUKEY_C = 3.0
TOL_FLOOR = 25.0
TOL_FRAC = 0.35
ERR_BASE_M = 25.5
# ERR_FLOOR caps the LONG-pair weight advantage. The 1/dt error model gives a
# 546-day pair ~1000x the weight of a 16-day one; in a MONTHLY inversion that
# is fatal, because long pairs constrain multi-month SUMS while short pairs
# are what pin individual months. Weighted 1000:1, the monthly detail goes
# under-determined and the solution oscillates wildly between values that all
# satisfy the sums (observed: 53 -> 4450 -> 4300 -> 72 m/yr in adjacent
# years). Floored here to hold the ratio near 20:1.
ERR_FLOOR = 120.0
DT_BINS = [0.0, 16.0, 32.0, 64.0, 128.0, 256.0, np.inf]
MIN_BIN = 8
SENSOR_DOWN = {'5': 0.25, '7': 0.45}
TEST_CELLS = [(100, 114), (60, 95), (75, 90), (90, 88), (139, 121), (79, 20)]


def month_grid():
    n = int(round((T1 - T0) / DT_MONTH))
    edges = T0 + np.arange(n + 1) * DT_MONTH
    return edges, n


def build_design(t1, t2, edges):
    """Sparse interval-mean operator (CSR): row k spreads over the months it
    spans, weighted by overlap fraction. This is the closure constraint.
    Built ONCE — it depends only on measurement times, not on the cell."""
    n = len(edges) - 1
    rows, cols, vals = [], [], []
    for k in range(len(t1)):
        a, b = t1[k], t2[k]
        dur = max(b - a, 1e-9)
        m0 = max(0, int(np.floor((a - edges[0]) / DT_MONTH)))
        m1 = min(n - 1, int(np.floor((b - edges[0]) / DT_MONTH)))
        for m in range(m0, m1 + 1):
            ov = min(b, edges[m + 1]) - max(a, edges[m])
            if ov > 1e-9:
                rows.append(k); cols.append(m); vals.append(ov / dur)
    A = sparse.csr_matrix((vals, (rows, cols)), shape=(len(t1), n))
    return A, n


def solve_tv(AtA_base, Atb, x0, n, lam_scale, w_tv=None, iters=N_IRLS):
    """min ||W(Ax-b)||^2 + lam*|Dx|_1 via IRLS on the TV term."""
    x = x0.copy()
    D_i = np.arange(n - 1)
    for _ in range(iters):
        d = np.diff(x)
        u = 1.0 / np.maximum(np.abs(d), 5.0)      # 5 m/yr = TV dead-band
        if w_tv is not None:
            u = u * w_tv
        M = AtA_base.copy()
        # D^T diag(u) D  (tridiagonal)
        M[D_i, D_i] += lam_scale * u
        M[D_i + 1, D_i + 1] += lam_scale * u
        M[D_i, D_i + 1] -= lam_scale * u
        M[D_i + 1, D_i] -= lam_scale * u
        M[np.diag_indices(n)] += 1e-6 * max(1.0, np.trace(M) / n)
        try:
            x = np.linalg.solve(M, Atb)
        except np.linalg.LinAlgError:
            return None
    return x


def fit_cell(A, n_m, vx, vy, base_w, dt_d, ctx_ok, ctx_fast):
    """Returns (mx, my, n_used, resid) monthly series, or None.

    All heavy algebra is sparse: A^T W A is a 126x126 normal matrix assembled
    at C speed, so the per-cell cost is a handful of small dense solves.
    """
    valid = np.isfinite(vx) & np.isfinite(vy)
    if valid.sum() < MIN_OBS:
        return None
    w = base_w.copy()
    w[~valid] = 0.0
    spd = np.hypot(np.nan_to_num(vx), np.nan_to_num(vy))

    # (a) EMPIRICAL dt CAP, per cell — the one-sided bin test that worked in
    # the parametric fit: project onto the short-baseline flow direction and
    # cut at the first bin whose median falls a MAD below the reference.
    # Skip/lock bias is strictly downward, so the test is one-sided.
    ref = None
    stats = []
    for b in range(len(DT_BINS) - 1):
        mb = valid & (dt_d >= DT_BINS[b]) & (dt_d < DT_BINS[b + 1])
        if mb.sum() >= MIN_BIN:
            med = float(np.median(spd[mb]))
            mad = max(float(np.median(np.abs(spd[mb] - med))) * 1.4826, TOL_FLOOR * 0.5)
            stats.append((b, med, mad))
            if ref is None:
                ref = (b, med, mad)
        else:
            stats.append((b, None, None))
    dt_cap = np.inf
    if ref is not None:
        thresh = ref[1] - ref[2]
        for b, med, mad in stats:
            if b <= ref[0] or med is None:
                continue
            if med < thresh:
                dt_cap = DT_BINS[b]
                break
    w[dt_d > max(dt_cap, 32.0)] = 0.0

    # (b) CONTEXT SCREEN — HARD exclusion, not a soft multiplier. A 0.02
    # multiplier on a pair carrying 1000x base weight still outvotes the good
    # data; that is how the trunk came out at ~100 m/yr against a ~4000 m/yr
    # truth.
    suspect = ctx_fast & (spd < CTX_ZERO_FRAC * ctx_ok)
    w[suspect] = 0.0
    if (w > 0).sum() < MIN_OBS:
        return None

    yx = np.nan_to_num(vx)
    yy = np.nan_to_num(vy)
    mx = np.zeros(n_m); my = np.zeros(n_m)
    rx = ry = None
    for _ in range(3):
        W = sparse.diags(w)
        AtA = (A.T @ W @ A).toarray()
        lam_scale = LAM * max(np.trace(AtA) / n_m, 1e-6)
        bx = A.T @ (w * yx)
        by = A.T @ (w * yy)
        nx_ = solve_tv(AtA, bx, mx, n_m, lam_scale)
        ny_ = solve_tv(AtA, by, my, n_m, lam_scale)
        if nx_ is None or ny_ is None:
            return None
        mx, my = nx_, ny_
        predx = A @ mx
        predy = A @ my
        rx = yx - predx
        ry = yy - predy
        sp = np.hypot(predx, predy) + 1e-9
        ux, uy = predx / sp, predy / sp
        ra = rx * ux + ry * uy
        rc = -rx * uy + ry * ux
        tol = np.maximum(TOL_FLOOR, TOL_FRAC * sp)
        zc = np.hypot(ra / (1.6 * tol), rc / tol)
        u = np.clip(zc / TUKEY_C, 0, 1)
        w = base_w * (1 - u * u) ** 2
        w[~valid] = 0.0
        w[suspect] = 0.0
        w[dt_d > max(dt_cap, 32.0)] = 0.0
        if (w > 0).sum() < MIN_OBS:
            break

    used = int((w > 0.05 * base_w.max()).sum())
    m = w > 0
    resid = float(np.sqrt(np.average((rx ** 2 + ry ** 2)[m], weights=w[m]))) if m.any() else np.nan
    return mx, my, used, resid


def main(test_only):
    z = zarr.open(str(ROOT / 'columbia_pairs.zarr'), mode='r')
    g = z.attrs['grid']
    ny, nx = g['ny'], g['nx']
    base = dt.date(1970, 1, 1)
    md = np.asarray(z['mid_date'])
    tmid = np.array([(base + dt.timedelta(days=float(x))).year +
                     ((base + dt.timedelta(days=float(x))).timetuple().tm_yday - 1) / 365.25
                     for x in md])
    ddt = np.asarray(z['date_dt']).astype(np.float64)
    sel = np.nonzero((tmid >= T0 - 0.5) & (tmid <= T1))[0]
    tmid_s, ddt_s = tmid[sel], ddt[sel]
    t1 = tmid_s - ddt_s / 2 / 365.25
    t2 = tmid_s + ddt_s / 2 / 365.25
    edges, n_m = month_grid()
    A, n_m = build_design(t1, t2, edges)
    sig = np.maximum(ERR_FLOOR, ERR_BASE_M * 365.25 / np.maximum(ddt_s, 1.0))
    base_w = 1.0 / sig ** 2
    try:
        s1 = np.asarray(z['satellite_img1'])[sel].astype(str)
        s2 = np.asarray(z['satellite_img2'])[sel].astype(str)
        for mid, f in SENSOR_DOWN.items():
            base_w[np.char.startswith(s1, mid) | np.char.startswith(s2, mid)] *= f
    except Exception:
        pass
    print(f'== monthly TV inversion: {sel.size} pairs, {n_m} months '
          f'{T0}-{T1}, grid {nx} x {ny}', flush=True)

    # ---- CONTEXT FIELD: robust short-pair speed per year, spatially smoothed
    yrs = np.arange(int(T0), int(T1) + 1)
    ctx = np.full((len(yrs), ny, nx), np.nan, np.float32)
    short = ddt_s <= CTX_SHORT_DT
    for yi, y in enumerate(yrs):
        k = np.nonzero(short & (tmid_s >= y) & (tmid_s < y + 1))[0]
        if k.size < 3:
            continue
        idx = sel[k]
        acc = np.zeros((ny, nx)); cnt = np.zeros((ny, nx))
        for a in range(0, idx.size, 800):
            kk = idx[a:a + 800]
            vx = np.asarray(z['vx'].get_orthogonal_selection((kk, slice(None), slice(None)))).astype('f4')
            vy = np.asarray(z['vy'].get_orthogonal_selection((kk, slice(None), slice(None)))).astype('f4')
            bad = (vx == -32767) | (vy == -32767)
            s = np.hypot(vx, vy); s[bad] = np.nan
            with np.errstate(all='ignore'):
                acc += np.nansum(s, axis=0); cnt += np.isfinite(s).sum(axis=0)
        with np.errstate(all='ignore'):
            ctx[yi] = np.where(cnt >= 3, acc / np.maximum(cnt, 1), np.nan)
        # Spatial smoothing IS the coherence test: an isolated zero sees its
        # fast neighbours and stays suspect; a coherent patch of zeros sees
        # only zeros and is believed. Implemented as a windowed upper-quartile
        # ("is anything near here moving fast?") via ndimage — the equivalent
        # Python loop was 292k percentile calls and never finished.
        c = ctx[yi]
        filled = np.where(np.isfinite(c), c, 0.0)
        cnt_ok = np.isfinite(c).astype(np.float32)
        k = 2 * CTX_SMOOTH + 1
        pf = ndimage.percentile_filter(filled, 75, size=k, mode='nearest')
        cover = ndimage.uniform_filter(cnt_ok, size=k, mode='nearest')
        ctx[yi] = np.where(cover > 0.15, pf, np.nan)
        print(f'   context {y}: {int(np.isfinite(ctx[yi]).sum())} cells, '
              f'median {np.nanmedian(ctx[yi]):.0f} m/yr', flush=True)
    yr_of = np.clip(np.searchsorted(yrs, np.floor(tmid_s)) , 0, len(yrs) - 1)

    cells = TEST_CELLS if test_only else None
    out_mx = np.full((n_m, ny, nx), np.nan, np.float32)
    out_my = np.full((n_m, ny, nx), np.nan, np.float32)
    out_n = np.zeros((ny, nx), np.int32)
    done = 0

    def run_cell(i, j, vx, vy):
        cvals = ctx[yr_of, i, j]
        ctx_ok = np.nan_to_num(cvals, nan=0.0)
        ctx_fast = ctx_ok > CTX_FAST
        return fit_cell(A, n_m, vx, vy, base_w, ddt_s, ctx_ok, ctx_fast)

    if test_only:
        for (i, j) in cells:
            vx = np.asarray(z['vx'][:, i, j])[sel].astype(np.float64)
            vy = np.asarray(z['vy'][:, i, j])[sel].astype(np.float64)
            bad = (vx == -32767) | (vy == -32767) | (np.abs(vx) > VMAX)
            vx[bad] = np.nan; vy[bad] = np.nan
            r = run_cell(i, j, vx, vy)
            if r is None:
                print(f'cell {(i,j)}: no fit'); continue
            mx, my, used, resid = r
            sp = np.hypot(mx, my)
            print(f'\ncell {(i,j)}  used {used} obs, resid {resid:.0f} m/yr')
            for y in range(int(T0), int(T1)):
                k = int((y + 0.5 - T0) / DT_MONTH)
                k2 = int((y + 0.05 - T0) / DT_MONTH)
                if k < n_m:
                    print(f'    {y}: Jan {sp[min(k2,n_m-1)]:7.0f}   Jul {sp[k]:7.0f} m/yr')
        return

    for y0 in range(0, ny, 10):
        y1 = min(ny, y0 + 10)
        vxb = np.asarray(z['vx'][:, y0:y1, :])[sel].astype(np.float64)
        vyb = np.asarray(z['vy'][:, y0:y1, :])[sel].astype(np.float64)
        bad = (vxb == -32767) | (vyb == -32767) | (np.abs(vxb) > VMAX)
        vxb[bad] = np.nan; vyb[bad] = np.nan
        for r in range(y1 - y0):
            for c in range(nx):
                res = run_cell(y0 + r, c, vxb[:, r, c], vyb[:, r, c])
                if res is None:
                    continue
                mx, my, used, resid = res
                out_mx[:, y0 + r, c] = mx
                out_my[:, y0 + r, c] = my
                out_n[y0 + r, c] = used
                done += 1
        print(f'   rows {y1}/{ny}  fitted {done}', flush=True)

    NOD = -32768
    def pack(a):
        return np.where(np.isfinite(a), np.clip(np.round(a), -32000, 32000), NOD).astype('<i2')
    with gzip.open(ROOT / 'columbia_monthly.bin.gz', 'wb', compresslevel=6) as f:
        f.write(pack(out_mx).tobytes())
        f.write(pack(out_my).tobytes())
    (ROOT / 'columbia_monthly.json').write_text(json.dumps({
        'grid': g, 't0': T0, 'dt': DT_MONTH, 'n_months': n_m,
        'nodata': NOD, 'bin': 'columbia_monthly.bin',
        'fitted_cells': int(done),
    }))
    np.savez(ROOT / 'columbia_monthly_diag.npz', n=out_n, ctx=ctx, years=yrs)
    sz = (ROOT / 'columbia_monthly.bin.gz').stat().st_size / 1e6
    print(f'   fitted {done} cells, wrote {sz:.1f} MB gz')


if __name__ == '__main__':
    main('--test' in sys.argv)
