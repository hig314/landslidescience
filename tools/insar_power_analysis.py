#!/usr/bin/env python3
"""Phase-0 power analysis for the InSAR high-grading pipeline.

Question: over what envelope of (rate, polygon size, aspect) can Tests A/B of
the high-grading plan (~/.claude/plans/insar-highgrading.md) detect motion at
all, given OPERA DISP-S1 point noise as it actually is? Run BEFORE building
the pipeline so "no detection" partitions into "not moving" vs "couldn't have
seen it" per candidate — and so hopeless candidates are never scored.

Noise is NOT assumed: it is resampled from real control clusters pulled from
coherent, non-landslide slopes via the same ASF point endpoint the click tool
uses (shared disk cache, data/insar_ts_cache/). The noise model has two
empirically-estimated components: a common-mode epoch term (shared across a
cluster — atmosphere/processing residue) and an idiosyncratic per-point term,
both resampled (not fit to a distribution) from detrended control residuals.
Real acquisition dates and reference-stack structure are used verbatim.

Signal models on a planar slope (aspect phi, slope beta):
  translation  u_i = v * s_hat                  (s_hat = downslope unit)
  rotation     u_i = Omega a_hat x (x_i - c)    (axis horizontal, ⊥ aspect,
               depth 0.15L below center; Omega = v/depth so mean surface
               speed ~ v, comparable across models)

Estimators mirror the plan:
  A: regress LOS obs on the surface-parallel prediction (per-stack offsets
     absorb each stack's datum); statistic = |v_hat| / se(v_hat).
  B: two-step — per-point/track rate (with stack offsets), then the LINEAR
     rigid model r_i = l . (omega x x_i + b); statistic = F vs null;
     rotation-vs-translation call = BIC(6-param rigid) < BIC(3-param
     translation) on the same rates.

Detection thresholds are the 95th percentile of each statistic under
pure-noise nulls (same pipeline, no signal) — empirical FPR 5%, not formal
errors. Power = fraction of Monte-Carlo reps detected.

Sentinel-1 LOS geometry (incidence 39 deg, asc heading -13, desc 193,
right-looking): l_asc = [-0.613, -0.142, 0.777], l_desc = [0.613, -0.142,
0.777] (ground->satellite; +LOS = toward satellite). Sanity: uplift -> +0.78
on both; pure-east -> opposite signs. N-S near-blind (|l_N| = 0.14) — the
analysis will SHOW that, which is the point.

Usage: python3 tools/insar_power_analysis.py [--reps 150] [--out data/insar_power]
"""
import argparse
import json
import time
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / 'data' / 'insar_ts_cache'
UPSTREAM = 'https://d2qmcvu7qty7vn.cloudfront.net/timeseries'
BUCKET = 'asf-cumulus-prod-opera-products'

L_ASC = np.array([-0.613, -0.142, 0.777])
L_DESC = np.array([0.613, -0.142, 0.777])

# Control SEED areas (non-landslide terrain). The first blind attempt proved
# the point Hig made when reviewing the plan: fixed offsets found 0 epochs at
# 3 of 4 sites — DISP-S1 point coverage is cell-by-cell patchy, so control
# collection must be ADAPTIVE: probe a neighborhood grid and keep only
# data-bearing cells. Every probe is cached forever, so failed probes cost
# one polite request once.
CONTROL_SEEDS = [
    (61.290, -149.316),   # proven coverage (dev exploration clicks)
    (61.160, -147.947),   # partial coverage found on first attempt
    (61.200, -148.547),   # partial coverage found on first attempt
    (61.142, -148.175),   # Barry Arm area margin (coherent cells nearby)
]
PROBE_STEPS = [(dla, dlo) for dla in (-0.004, -0.002, 0, 0.002, 0.004)
               for dlo in (-0.008, -0.004, 0, 0.004, 0.008)]
MIN_EPOCHS = 25
PTS_PER_CLUSTER = 6


def fetch(lat, lon, direction):
    """Same cache key format as inventory/insar.py — clicks and analysis
    share one cache."""
    key = f'{direction}_{round(lat,4):.4f}_{round(lon,4):.4f}.json'
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / key
    if p.exists():
        return json.loads(p.read_text())
    body = json.dumps({'wkt': f'POINT({round(lon,4)} {round(lat,4)})',
                       'bucket': BUCKET, 'polarization': 'VV',
                       'flightDirection': direction.upper()}).encode()
    req = urllib.request.Request(UPSTREAM, data=body, method='POST',
                                 headers={'User-Agent': 'landslidescience-power-analysis/1',
                                          'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = json.loads(r.read().decode())
    except Exception:
        raw = {}
    # regroup exactly like the proxy
    stacks = {}
    for rec in (raw or {}).values():
        if not isinstance(rec, dict):
            continue
        ref, sec = rec.get('reference_datetime'), rec.get('secondary_datetime')
        d = rec.get('short_wavelength_displacement')
        if ref and sec and d is not None:
            stacks.setdefault(ref, []).append([sec, round(float(d) * 1000, 3)])
    out = {'series': [{'ref': k, 'points': sorted(v)} for k, v in sorted(stacks.items())],
           'n': sum(len(v) for v in stacks.values())}
    p.write_text(json.dumps(out))
    time.sleep(1.0)          # polite pacing on cold fetches
    return out


def to_arrays(ts):
    """series -> (t_years, disp_mm, stack_ids, date_keys); t rel. first epoch."""
    t, d, sid, keys = [], [], [], []
    for i, st in enumerate(ts['series']):
        for sec, mm in st['points']:
            t.append(np.datetime64(sec[:10]).astype('datetime64[D]').astype(float) / 365.25)
            d.append(mm)
            sid.append(i)
            keys.append(str(i) + '|' + sec[:10])
    if not t:
        return None
    t = np.array(t); t -= t.min()
    return t, np.array(d), np.array(sid), keys


def collect_noise():
    """Adaptive control collection -> date-aligned noise banks + template.

    Residuals are aligned BY DATE-KEY (stack|secondary-date), never by index:
    different cells carry different stack subsets, and index alignment would
    smear unrelated epochs together and inflate the common-mode estimate.
    Common mode at an epoch = mean residual across the cluster's points that
    observed that epoch (>=3 required); idiosyncratic = departure from it."""
    clusters = []
    template = None
    for slat, slon in CONTROL_SEEDS:
        cl = {'ascending': [], 'descending': []}
        for dla, dlo in PROBE_STEPS:
            if min(len(cl['ascending']), len(cl['descending'])) >= PTS_PER_CLUSTER:
                break
            for dirn in ('ascending', 'descending'):
                if len(cl[dirn]) >= PTS_PER_CLUSTER:
                    continue
                arr = to_arrays(fetch(slat + dla, slon + dlo, dirn))
                if arr and len(arr[0]) >= MIN_EPOCHS:
                    cl[dirn].append(arr)
                    if template is None and dirn == 'ascending':
                        template = arr
                    elif dirn == 'ascending' and len(arr[0]) > len(template[0]):
                        template = arr
        na, nd = len(cl['ascending']), len(cl['descending'])
        print(f'  seed ({slat:.3f},{slon:.3f}): {na} asc / {nd} desc usable points')
        if na >= 3 and nd >= 3:
            clusters.append(cl)
    if not clusters or template is None:
        raise SystemExit('not enough coherent control data even adaptively')

    common_bank, idio_bank, sigmas = [], [], []
    for cl in clusters:
        for dirn in ('ascending', 'descending'):
            # per-point residual dict keyed by stack|date
            rd = []
            for t, d, sid, keys in cl[dirn]:
                r = np.empty_like(d)
                for stk in np.unique(sid):
                    m = sid == stk
                    if m.sum() >= 3:
                        A = np.c_[t[m], np.ones(m.sum())]
                        r[m] = d[m] - A @ np.linalg.lstsq(A, d[m], rcond=None)[0]
                    else:
                        r[m] = d[m] - d[m].mean()
                rd.append(dict(zip(keys, r)))
            # epochs seen by >=3 points
            from collections import Counter
            cnt = Counter(k for m in rd for k in m)
            shared = [k for k, c in cnt.items() if c >= 3]
            if len(shared) < 10:
                continue
            shared.sort()                        # chronological order matters:
            common = []                          # blocks must be REAL sequences
            idio_rows = {i: [] for i in range(len(rd))}
            for k in shared:
                vals = {i: m[k] for i, m in enumerate(rd) if k in m}
                mu = float(np.mean(list(vals.values())))
                common.append(mu)
                for i in range(len(rd)):
                    idio_rows[i].append(vals[i] - mu if i in vals else np.nan)
            common = np.array(common)
            M = np.array([idio_rows[i] for i in range(len(rd))])
            # keep points with >=80% coverage; fill gaps from the row's own values
            keep = np.isfinite(M).mean(axis=1) >= 0.8
            M = M[keep]
            for row in M:
                bad = ~np.isfinite(row)
                if bad.any():
                    row[bad] = np.random.default_rng(0).choice(row[~bad], bad.sum())
            if not len(M):
                continue
            common_bank.append(common)
            idio_bank.append(M)
            sigmas.append((np.std(common), np.std(M)))
    if not common_bank:
        raise SystemExit('no cluster had >=10 shared epochs — alignment failed')
    sc = float(np.mean([a for a, b in sigmas]))
    si = float(np.mean([b for a, b in sigmas]))
    print(f'noise model from {len(clusters)} clusters ({len(common_bank)} banks): '
          f'common-mode sigma {sc:.1f} mm, idiosyncratic sigma {si:.1f} mm')
    return common_bank, idio_bank, template


BLOCK = 8      # epochs (~3-6 months) — preserves the temporal correlation
               # that independent-epoch resampling destroyed. That first
               # version reported near-perfect power everywhere: the exact
               # precision-exceeds-accuracy illusion this analysis exists to
               # kill. Circular block bootstrap keeps months-scale structure.


def _block_sample(rng, series, n):
    out = np.empty(n)
    i = 0
    L = len(series)
    while i < n:
        start = rng.integers(0, L)
        take = min(BLOCK, n - i)
        idx = (start + np.arange(take)) % L
        out[i:i + take] = series[idx]
        i += take
    return out


def make_noise(rng, n_pts, n_ep, common_bank, idio_bank):
    b = rng.integers(len(common_bank))
    common = _block_sample(rng, common_bank[b], n_ep)
    M = idio_bank[b]
    idio = np.array([_block_sample(rng, M[rng.integers(len(M))], n_ep)
                     for _ in range(n_pts)])
    return common[None, :] + idio


def slope_frame(phi_deg, beta_deg):
    phi, beta = np.radians(phi_deg), np.radians(beta_deg)
    s_hat = np.array([np.cos(beta) * np.sin(phi), np.cos(beta) * np.cos(phi), -np.sin(beta)])
    a_hat = np.array([np.cos(phi), -np.sin(phi), 0.0])     # horizontal, ⊥ aspect
    return s_hat, a_hat


def sample_points(rng, L_m, phi_deg, beta_deg, n_pts):
    """n points on a 30 m grid inside an L x 0.6L ellipse on the slope plane."""
    phi, beta = np.radians(phi_deg), np.radians(beta_deg)
    down = np.array([np.sin(phi), np.cos(phi)])
    cross = np.array([np.cos(phi), -np.sin(phi)])
    pts = []
    tries = 0
    while len(pts) < n_pts and tries < 500:
        tries += 1
        u = rng.uniform(-L_m / 2, L_m / 2)
        w = rng.uniform(-0.3 * L_m, 0.3 * L_m)
        if (u / (L_m / 2)) ** 2 + (w / (0.3 * L_m)) ** 2 > 1:
            continue
        u, w = round(u / 30) * 30, round(w / 30) * 30    # 30 m product grid
        if any(abs(u - p[3]) < 1 and abs(w - p[4]) < 1 for p in pts):
            continue
        xy = u * down + w * cross
        z = -u * np.tan(beta)
        pts.append((xy[0], xy[1], z, u, w))
    return np.array([[p[0], p[1], p[2]] for p in pts])


def simulate_rates(rng, X, model, v, phi_deg, beta_deg, L_m):
    s_hat, a_hat = slope_frame(phi_deg, beta_deg)
    if model == 'translation':
        U = np.tile(v * s_hat, (len(X), 1))
    else:
        depth = max(10.0, 0.15 * L_m)
        c = np.array([0, 0, X[:, 2].mean() - depth])
        omega = (v / depth) * a_hat
        U = np.cross(np.tile(omega, (len(X), 1)), X - c)
    return U


def obs_series(rng, U, template, common_bank, idio_bank, l_vec):
    t, _, sid = template[0], template[1], template[2]
    n_pts, n_ep = len(U), len(t)
    signal = (U @ l_vec)[:, None] * t[None, :]
    noise = make_noise(rng, n_pts, n_ep, common_bank, idio_bank)
    obs = signal + noise
    # each stack has its own (unknown) datum
    for s in np.unique(sid):
        obs[:, sid == s] += rng.normal(0, 5)
    return obs, t, sid


def fit_point_rate(t, d, sid):
    """Rate with per-stack offsets; returns (rate, se)."""
    stacks = np.unique(sid)
    A = np.zeros((len(t), 1 + len(stacks)))
    A[:, 0] = t
    for j, s in enumerate(stacks):
        A[sid == s, 1 + j] = 1
    coef, res, rank, _ = np.linalg.lstsq(A, d, rcond=None)
    dof = len(t) - rank
    if dof <= 0:
        return coef[0], np.inf
    s2 = float(res[0]) / dof if res.size else float(np.var(d - A @ coef)) * len(t) / dof
    cov = s2 * np.linalg.pinv(A.T @ A)
    return coef[0], float(np.sqrt(max(cov[0, 0], 1e-12)))


def stat_A(rng, X, obs_a, obs_d, tmpl_a, tmpl_d, phi_deg, beta_deg):
    """Surface-parallel single-rate fit across all points & tracks."""
    s_hat, _ = slope_frame(phi_deg, beta_deg)
    pa, pd_ = float(L_ASC @ s_hat), float(L_DESC @ s_hat)
    rows_pred, rows_obs = [], []
    for obs, tmpl, proj in ((obs_a, tmpl_a, pa), (obs_d, tmpl_d, pd_)):
        t, sid = tmpl[0], tmpl[2]
        for i in range(len(X)):
            r, se = fit_point_rate(t, obs[i], sid)
            if np.isfinite(se):
                rows_pred.append(proj)
                rows_obs.append(r)
    P, O = np.array(rows_pred), np.array(rows_obs)
    denom = float(P @ P)
    if denom < 1e-9:
        return 0.0
    v_hat = float(P @ O) / denom
    resid = O - v_hat * P
    se = np.sqrt(float(resid @ resid) / max(len(O) - 1, 1) / denom)
    return abs(v_hat) / max(se, 1e-9)


def stat_B(rng, X, obs_a, obs_d, tmpl_a, tmpl_d):
    """Linear rigid-motion fit on per-point rates; F-stat + BIC preference."""
    rows_l, rows_x, rates = [], [], []
    for obs, tmpl, l in ((obs_a, tmpl_a, L_ASC), (obs_d, tmpl_d, L_DESC)):
        t, sid = tmpl[0], tmpl[2]
        for i in range(len(X)):
            r, se = fit_point_rate(t, obs[i], sid)
            if np.isfinite(se):
                rows_l.append(l); rows_x.append(X[i]); rates.append(r)
    Lm = np.array(rows_l); Xm = np.array(rows_x); R = np.array(rates)
    n = len(R)
    if n < 10:
        # 6 params vs <10 observations = perfect-fit theater (the first run
        # "identified rotation" at 60 m with 6 obs). And the obvious fallback
        # — a translation-only F — is blind BY CONSTRUCTION to rotation about
        # the centroid (zero net translation: measured F=0.1 under 190 mm/yr
        # of motion). Small clusters get the honest minimal question, "is
        # anything moving?": mean squared rate. Calibrated per-cell against
        # the null like every other statistic, so its scale is irrelevant.
        return float(np.mean(R ** 2)), None
    # design: d = l . (omega x x + b) = [l x x? careful] -> l.(omega x x) = omega.(x x l)
    G = np.zeros((n, 6))
    G[:, :3] = np.cross(Xm, Lm)          # coefficient of omega
    G[:, 3:] = Lm                        # coefficient of b
    coef6, res6, rank6, _ = np.linalg.lstsq(G, R, rcond=None)
    rss6 = float(res6[0]) if res6.size else float(((R - G @ coef6) ** 2).sum())
    Gt = Lm
    coef3, res3, rank3, _ = np.linalg.lstsq(Gt, R, rcond=None)
    rss3 = float(res3[0]) if res3.size else float(((R - Gt @ coef3) ** 2).sum())
    rss0 = float((R ** 2).sum())
    dof = max(n - rank6, 1)
    F = ((rss0 - rss6) / max(rank6, 1)) / max(rss6 / dof, 1e-12)
    bic6 = n * np.log(max(rss6 / n, 1e-12)) + 6 * np.log(n)
    bic3 = n * np.log(max(rss3 / n, 1e-12)) + 3 * np.log(n)
    return F, (bic6 + 6 < bic3)          # margin: rotation must beat by >6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--reps', type=int, default=150)
    ap.add_argument('--out', default=str(ROOT / 'data' / 'insar_power'))
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(2026)

    common_bank, idio_bank, tmpl_a = collect_noise()
    # descending template: reuse asc dates shifted — good enough for power
    tmpl_d = tmpl_a

    beta = 25.0

    def calibrate(X, phi, n_null=200):
        """Null thresholds FOR THIS cluster geometry — the F distribution
        depends on n (the first run calibrated at one config and applied it
        to all, which is wrong at small clusters)."""
        nA, nB = [], []
        Un = np.zeros((len(X), 3))
        for _ in range(n_null):
            oa = obs_series(rng, Un, tmpl_a, common_bank, idio_bank, L_ASC)[0]
            od = obs_series(rng, Un, tmpl_d, common_bank, idio_bank, L_DESC)[0]
            nA.append(stat_A(rng, X, oa, od, tmpl_a, tmpl_d, phi, beta))
            nB.append(stat_B(rng, X, oa, od, tmpl_a, tmpl_d)[0])
        return float(np.percentile(nA, 95)), float(np.percentile(nB, 95))

    # ---- Grid 1: rate x aspect (translation, L=300 m) --------------------
    rates = [2, 5, 10, 20, 50]
    aspects = list(range(0, 360, 45))
    powerA = np.zeros((len(rates), len(aspects)))
    Xa = {phi: sample_points(rng, 300, phi, beta, 10) for phi in aspects}
    thrA_by = {phi: calibrate(Xa[phi], phi)[0] for phi in aspects}
    print('  grid1 thresholds:', {k: round(v, 1) for k, v in thrA_by.items()})
    for ir, v in enumerate(rates):
        for ia, phi in enumerate(aspects):
            X = Xa[phi]
            det = 0
            for _ in range(args.reps):
                U = simulate_rates(rng, X, 'translation', v, phi, beta, 300)
                oa = obs_series(rng, U, tmpl_a, common_bank, idio_bank, L_ASC)[0]
                od = obs_series(rng, U, tmpl_d, common_bank, idio_bank, L_DESC)[0]
                det += stat_A(rng, X, oa, od, tmpl_a, tmpl_d, phi, beta) > thrA_by[phi]
            powerA[ir, ia] = det / args.reps
        print(f'  grid1 rate {v} done')

    # ---- Grid 2: size x rate (rotation; detection + discrimination) ------
    sizes = [60, 120, 300, 600]
    powerB = np.zeros((len(rates), len(sizes)))
    powerRot = np.zeros((len(rates), len(sizes)))
    Xs = {}
    thrB_by = {}
    for Lm in sizes:
        n_pts = int(np.clip((Lm / 90) ** 2, 3, 16))
        Xs[Lm] = sample_points(rng, Lm, 135, beta, n_pts)
        thrB_by[Lm] = calibrate(Xs[Lm], 135)[1]
    print('  grid2 thresholds:', {k: round(v, 1) for k, v in thrB_by.items()})
    for ir, v in enumerate(rates):
        for isz, Lm in enumerate(sizes):
            X = Xs[Lm]
            if len(X) < 3:
                powerB[ir, isz] = np.nan; powerRot[ir, isz] = np.nan
                continue
            det, rot, decidable = 0, 0, True
            for _ in range(args.reps):
                U = simulate_rates(rng, X, 'rotation', v, 135, beta, Lm)
                oa = obs_series(rng, U, tmpl_a, common_bank, idio_bank, L_ASC)[0]
                od = obs_series(rng, U, tmpl_d, common_bank, idio_bank, L_DESC)[0]
                F, isrot = stat_B(rng, X, oa, od, tmpl_a, tmpl_d)
                if isrot is None:
                    decidable = False
                if F > thrB_by[Lm]:
                    det += 1
                    rot += bool(isrot)   # NOT `is True`: BIC comparison
                                         # yields numpy.bool_, and
                                         # np.bool_(True) is True == False.
                                         # Cost of that identity check: every
                                         # correctly-identified rotation in
                                         # the previous run counted as zero.
            powerB[ir, isz] = det / args.reps
            powerRot[ir, isz] = (rot / args.reps) if decidable else np.nan
        print(f'  grid2 rate {v} done')

    np.savez(out / 'power_grids.npz', rates=rates, aspects=aspects, sizes=sizes,
             powerA=powerA, powerB=powerB, powerRot=powerRot,
             )

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 3, figsize=(17, 4.6))
    im0 = axes[0].imshow(powerA, cmap='viridis', vmin=0, vmax=1, aspect='auto')
    axes[0].set_xticks(range(len(aspects)), [str(a) for a in aspects])
    axes[0].set_yticks(range(len(rates)), [str(r) for r in rates])
    axes[0].set_xlabel('slope aspect (° from N)'); axes[0].set_ylabel('rate (mm/yr)')
    axes[0].set_title('Test A power — translation, L=300 m\n(5% FPR, real-noise resampling)')
    plt.colorbar(im0, ax=axes[0])
    im1 = axes[1].imshow(powerB, cmap='viridis', vmin=0, vmax=1, aspect='auto')
    axes[1].set_xticks(range(len(sizes)), [str(s) for s in sizes])
    axes[1].set_yticks(range(len(rates)), [str(r) for r in rates])
    axes[1].set_xlabel('landslide length (m)'); axes[1].set_ylabel('rate (mm/yr)')
    axes[1].set_title('Test B power — rotation detection (aspect 135°)')
    plt.colorbar(im1, ax=axes[1])
    im2 = axes[2].imshow(powerRot, cmap='magma', vmin=0, vmax=1, aspect='auto')
    axes[2].set_xticks(range(len(sizes)), [str(s) for s in sizes])
    axes[2].set_yticks(range(len(rates)), [str(r) for r in rates])
    axes[2].set_xlabel('landslide length (m)'); axes[2].set_ylabel('rate (mm/yr)')
    axes[2].set_title('P(detected AND identified as rotation)')
    plt.colorbar(im2, ax=axes[2])
    plt.tight_layout()
    plt.savefig(out / 'power_envelope.png', dpi=120, bbox_inches='tight')
    print('wrote', out / 'power_envelope.png')


if __name__ == '__main__':
    main()
