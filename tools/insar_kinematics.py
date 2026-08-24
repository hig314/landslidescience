#!/usr/bin/env python3
"""Per-landslide kinematic records: rotational-velocity time series + GIS-ready
domain polygons. (Hig, 2026-08-22.)

Builds on insar_domain_rotation_v2.py (distilled blocks, robust rates,
anomaly-seeded domain growth) and insar_rotation_gravity.py (dropline /
expected-pole geometry). What is new here:

1. OMEGA(t) — rotational velocity as a time series with an uncertainty
   envelope, not a single period-average number. The geometry (domain, pole,
   dropline) is held FIXED and only the rate is allowed to vary: Hig's
   "constant geometry, varying rate" model, resolved continuously instead of
   as a single early/late pair.

2. SINGLE-TRACK OMEGA IS IDENTIFIABLE. A rigid rotation writes a spatial
   GRADIENT into the LOS field; a translation writes a CONSTANT offset:
       LOS_i = Omega * l.(p x x_i)  +  (l.b)      <- second term constant in i
   Two unknowns, n blocks. The translation is NOT recoverable from one track
   (its three components collapse into that constant), but Omega is. This
   matters because of (3).

3. THE TRACKS DO NOT SPAN THE SAME EPOCHS. Measured from the cache:
       ascending   2016-08 .. 2020-08
       descending  2018-08 .. 2025-05
   They overlap for only ~2 years. Any two-track fit over "the full record"
   combines an ascending rate averaged over 2016-2020 with a descending rate
   averaged over 2018-2025 — fine if the rate is stationary, biased if it is
   not, and v2 already showed it is not (late/early k = 3.5 at Matanuska D2).
   So every record carries BOTH the naive full-period 3-D fit and a
   COMMON-WINDOW fit with both tracks restricted to the overlap; the
   difference between them is a stated diagnostic, not a hidden assumption.

UNITS: X in metres, rates in mm/yr, so a fit coefficient is mm/(yr*m) =
mrad/yr; everything is REPORTED in microrad/yr (x1000) and deg/kyr.

Outputs, one record per landslide id, into data/kinematics/:
    <id>.json            domain properties, fits, omega(t), hull polygon
    <id>_d<k>_fit.png    block map / observed-vs-predicted / stereonet
    <id>_d<k>_omega.png  Omega(t) with uncertainty envelope
The JSON is the format the Eklutna-style area search will emit per candidate
element, so it is deliberately GIS-shaped: a polygon plus scalar properties.

data/kinematics/ is gitignored and NOT rsynced to production — the Django
page gates on the directory's existence, so this stays a dev surface until
the method is settled.
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from shapely.geometry import shape, Point, MultiPoint

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'data' / 'insar_power'
KIN = ROOT / 'data' / 'kinematics'
L = {'ascending': np.array([-0.613, -0.142, 0.777]),
     'descending': np.array([0.613, -0.142, 0.777])}
LC = {'matanuska': 150.0, 'columbia_a': 330.0}
GRID_M, HALO_M, CAP = 60, 300, 360
WIN_YR, STEP_YR = 2.0, 0.5      # sliding window width / step for Omega(t)
MIN_EP_WIN = 10                 # epochs per point per window to accept a rate
MIN_BLK_WIN = 4                 # blocks per window to attempt a fit
URAD = 1000.0                   # coefficient (mrad/yr) -> microrad/yr
DEG_PER_KYR = 0.0573            # microrad/yr -> deg/kyr
C_ASC, C_DESC, C_SLOPE, C_AXIS = '#0072B2', '#E69F00', '#7f8a86', '#000000'


def point_series(ts, v2):
    """Unwrap-corrected series with ABSOLUTE decimal-year timestamps."""
    t, d, sid = [], [], []
    for i, st in enumerate(ts.get('series', [])):
        for sec, mm in st['points']:
            y = np.datetime64(sec[:10]).astype('datetime64[D]').astype(float) / 365.25 + 1970.0
            t.append(y); d.append(mm); sid.append(i)
    if len(t) < 15:
        return None
    t, d, sid = np.array(t), np.array(d), np.array(sid)
    o = np.argsort(t)
    t, d, sid = t[o], d[o], sid[o]
    d2, ncorr = v2.unwrap_correct(t - t.min(), d, sid)
    return {'t': t, 'd': d2, 'sid': sid, 'ncorr': int(ncorr)}


def window_rate(ps, v2, t0, t1):
    """Huber rate over [t0, t1), or None if too few epochs in the window."""
    m = (ps['t'] >= t0) & (ps['t'] < t1)
    if int(m.sum()) < MIN_EP_WIN:
        return None
    return v2.huber_rate(ps['t'][m] - ps['t'][m].min(), ps['d'][m], ps['sid'][m])


def domain_geometry(Xd):
    """Average dropline + expected gravitational pole from a plane fit."""
    A = np.c_[Xd[:, 0], Xd[:, 1], np.ones(len(Xd))]
    coef = np.linalg.lstsq(A, Xd[:, 2], rcond=None)[0]
    grad = coef[:2]
    slope = math.degrees(math.atan(np.linalg.norm(grad)))
    d_h = -grad / max(np.linalg.norm(grad), 1e-12)
    drop = np.array([d_h[0], d_h[1], -math.tan(math.radians(slope))])
    drop /= np.linalg.norm(drop)
    pole = np.array([-d_h[1], d_h[0], 0.0])
    c = Xd.mean(axis=0)
    up = c + 100 * np.array([-d_h[0], -d_h[1], math.tan(math.radians(slope))])
    if np.cross(pole, up - c)[2] > 0:
        pole = -pole                      # sign convention: +Omega lowers mass
    return {'drop': drop, 'pole': pole, 'slope_deg': slope,
            'drop_az': (math.degrees(math.atan2(d_h[0], d_h[1])) + 360) % 360}


def fit_omega(Xd, rates, pole, drop):
    """Omega about the FIXED expected pole, from whatever tracks are present.

    Both tracks -> 3 params (Omega, dropline translation, vertical).
    One track   -> 2 params (Omega, one lumped constant).
    """
    dirs = sorted({dn for r in rates for dn in ('ascending', 'descending')
                   if r.get(dn) is not None})
    if not dirs:
        return None
    both = len(dirs) > 1
    zhat = np.array([0, 0, 1.0])
    G, R = [], []
    for i, r in enumerate(rates):
        for dn in dirs:
            v = r.get(dn)
            if v is None:
                continue
            l = L[dn]
            row = [float(l @ np.cross(pole, Xd[i]))]
            row += [float(l @ drop), float(l @ zhat)] if both else [1.0]
            G.append(row); R.append(v)
    G, R = np.array(G), np.array(R)
    npar = G.shape[1]
    if len(R) < npar + 2 or np.std(G[:, 0]) < 1e-9:
        return None                        # no spatial leverage on Omega
    coef, *_ = np.linalg.lstsq(G, R, rcond=None)
    resid = R - G @ coef
    dof = max(len(R) - npar, 1)
    cov = (float(resid @ resid) / dof) * np.linalg.pinv(G.T @ G)
    return {'omega': float(coef[0]), 'se': float(math.sqrt(max(cov[0, 0], 0.0))),
            'n_obs': int(len(R)), 'tracks': '+'.join(d[:3] for d in dirs),
            'rms': float(np.sqrt(np.mean(resid ** 2))),
            'r2': 1 - float(resid @ resid) / max(float(((R - R.mean()) ** 2).sum()), 1e-9)}


def gradient_identifiability(Xd, rates, pole):
    """Is the rotation AXIS identifiable, or is the rigid fit inventing it?

    A general linear velocity field is u = A x + b (12 parameters). Each look
    direction observes only the 3-vector l.A and the scalar l.b — four numbers
    per track, so two tracks constrain exactly EIGHT of the twelve. Six
    combinations of A's nine components are observable and three are not, and
    the split does not respect the symmetric/antisymmetric decomposition: the
    strain rate and the rotation are entangled in what we can see.

    The rigid 6-parameter fit resolves this by fiat, forcing the whole observed
    gradient into an antisymmetric tensor. When the slope really is deforming,
    that reprojects strain as rotation and the fitted axis is an artefact of the
    assumption rather than a measurement. The fit can be excellent and the axis
    still meaningless — which is exactly the trap, because the within-model
    covariance then reports a tight, confident, wrong cone.

    So: fit the full linear field too, and compare the axis each model gives.
    If they disagree, the axis is NOT identified and must not be reported as a
    gravitational-consistency verdict. |Omega| about a SPECIFIED axis stays a
    well-posed one-parameter question either way — 'how fast about this axis?'
    is answerable where 'what is the axis?' is not.
    """
    Xc = Xd - Xd.mean(axis=0)
    G6, G12, R = [], [], []
    for i, r in enumerate(rates):
        for dn in ('ascending', 'descending'):
            if r.get(dn) is None:
                continue
            l = L[dn]
            G6.append(np.concatenate([np.cross(Xc[i], l), l]))
            G12.append(np.concatenate([np.outer(l, Xc[i]).ravel(), l]))
            R.append(r[dn])
    if len(R) < 14:
        return None
    G6, G12, R = np.array(G6), np.array(G12), np.array(R)
    c6, *_ = np.linalg.lstsq(G6, R, rcond=None)
    c12, _, rank12, _ = np.linalg.lstsq(G12, R, rcond=None)
    rms6 = float(np.sqrt(np.mean((R - G6 @ c6) ** 2)))
    rms12 = float(np.sqrt(np.mean((R - G12 @ c12) ** 2)))
    A = c12[:9].reshape(3, 3)
    W, S = 0.5 * (A - A.T), 0.5 * (A + A.T)
    om12 = np.array([W[2, 1], W[0, 2], W[1, 0]])

    def ang(w):
        n = np.linalg.norm(w)
        return (math.degrees(math.acos(np.clip(abs((w / n) @ pole), 0, 1)))
                if n > 1e-15 else float('nan'))

    def axis_sep(u, v):
        nu, nv = np.linalg.norm(u), np.linalg.norm(v)
        if nu < 1e-15 or nv < 1e-15:
            return float('nan')
        return math.degrees(math.acos(np.clip(abs((u / nu) @ (v / nv)), 0, 1)))
    sep = axis_sep(c6[:3], om12)
    return {'rank12': int(rank12), 'rms_rigid': rms6, 'rms_linear': rms12,
            'strain_to_rotation': float(np.linalg.norm(S) / max(np.linalg.norm(W), 1e-15)),
            'ang_rigid': ang(c6[:3]), 'ang_linear': ang(om12),
            'axis_shift_deg': sep,
            'axis_identified': bool(sep == sep and sep < 20)}


def reference_field_check(Xd, rates, halo, pole, drop, meas_omega):
    """Does the surrounding 'stable' ground carry a trend that fakes rotation?

    Hig's point (2026-08-23): DISP-S1 builds its displacement time series
    against a reference derived from ground inferred to be stable, so the
    spatially correlated part of what we call noise probably originates far
    outside our domain — it is baseline error, not a local property of the
    slope. Measuring a correlation length from local residuals and calling the
    result an independence scale therefore over-reads it.

    Two distinct consequences, and they need separating:

    VARIANCE. Broadly correlated error is nearly uniform across a domain, and a
    near-uniform offset is absorbed by the translation terms — Omega is a
    GRADIENT estimator and is close to orthogonal to it. Rebuilding the
    covariance from the measured halo correlation instead of assuming white
    noise changes sigma(Omega) by less than 15% at Matanuska, and downward.

    BIAS. A residual TREND across the domain is a different matter: it maps
    straight onto Omega. That is measurable — fit a plane to the halo rates and
    push it through the identical estimator. At Matanuska it accounts for
    -2.7 urad/yr, i.e. it was suppressing the signal rather than creating it.

    Reported as a sensitivity, never applied as a default correction: the halo
    is only 300 m wide, blocks outside the mapped polygon have already been seen
    joining kinematic elements, and subtracting a trend fitted on ground that is
    itself moving would delete real deformation.
    """
    zhat = np.array([0, 0, 1.0])
    trend = {}
    for dn in ('ascending', 'descending'):
        P = [(b['xy'][0], b['xy'][1], b['rate'][dn]) for b in halo
             if b['rate'][dn] is not None]
        if len(P) < 6:
            return None
        A = np.array([[p[0], p[1], 1.0] for p in P])
        y = np.array([p[2] for p in P])
        c, *_ = np.linalg.lstsq(A, y, rcond=None)
        trend[dn] = {'coef': c, 'grad_mm_yr_km': float(math.hypot(c[0], c[1]) * 1000),
                     'az': float((math.degrees(math.atan2(c[0], c[1])) + 360) % 360),
                     'scatter': float(np.std(y - A @ c)), 'n': len(P)}

    def solve(get):
        G, R = [], []
        for i in range(len(Xd)):
            for dn in ('ascending', 'descending'):
                v = get(i, dn)
                if v is None:
                    continue
                l = L[dn]
                G.append([float(l @ np.cross(pole, Xd[i])), float(l @ drop), float(l @ zhat)])
                R.append(v)
        if len(R) < 6:
            return None
        G, R = np.array(G), np.array(R)
        c, *_ = np.linalg.lstsq(G, R, rcond=None)
        return float(c[0]) * URAD

    def plane(i, dn):
        c = trend[dn]['coef']
        return float(c[0] * Xd[i][0] + c[1] * Xd[i][1] + c[2])
    spurious = solve(plane)
    detrended = solve(lambda i, dn: (None if rates[i].get(dn) is None
                                     else rates[i][dn] - plane(i, dn)))
    return {'trend': {k: {kk: vv for kk, vv in v.items() if kk != 'coef'}
                      for k, v in trend.items()},
            'omega_from_trend_alone': spurious,
            'omega_detrended': detrended,
            'trend_share': (abs(spurious) / abs(meas_omega)
                            if meas_omega and abs(meas_omega) > 1e-9 else None)}


def weighted_omega(Xd, sub, pole, drop):
    """Omega with blocks weighted by 1/sigma^2 instead of equally.

    Equal weighting assumes every block is equally trustworthy. It is not: at
    Matanuska per-block rate uncertainty spans 14.5x, and re-fitting with
    inverse-variance weights moves Omega from +7.9 to +14.9 urad/yr — a change
    almost twice the quoted uncertainty. That is a genuine sensitivity of the
    estimator, not of the data, and it is reported rather than silently adopted:
    weights estimated from short series carry their own bias, and down-weighting
    could in principle discard the deforming blocks that carry the signal (here
    it does the opposite, which is reassuring but not a proof).
    """
    zhat = np.array([0, 0, 1.0])
    G, R, W = [], [], []
    for i, b in enumerate(sub):
        for dn in ('ascending', 'descending'):
            v = b['rate'][dn]
            sg = (b.get('sig') or {}).get(dn)
            if v is None or not sg:
                continue
            l = L[dn]
            G.append([float(l @ np.cross(pole, Xd[i])), float(l @ drop), float(l @ zhat)])
            R.append(v); W.append(1.0 / max(sg, 1e-3) ** 2)
    if len(R) < 6:
        return None
    G, R, W = np.array(G), np.array(R), np.array(W)
    Aw = G * W[:, None]
    c, *_ = np.linalg.lstsq(Aw.T @ G, Aw.T @ R, rcond=None)
    res = R - G @ c
    dof = max(len(R) - 3, 1)
    s2 = float((W * res * res).sum()) / dof / max(W.mean(), 1e-12)
    cov = s2 * np.linalg.pinv(G.T @ (G * W[:, None])) * W.mean()
    sig = np.array([1.0 / math.sqrt(w) for w in W])
    return {'omega_urad_yr': float(c[0]) * URAD,
            'se': float(math.sqrt(max(cov[0, 0], 0.0))) * URAD,
            'sigma_min': float(sig.min()), 'sigma_max': float(sig.max())}


def rotation_axis(Xd, rates, geo):
    """Where is the rotation axis, and how tightly is the failure surface curved?

    Hig, 2026-08-23: a rotating block's motion vectors fan by the angle the block
    subtends AT THE AXIS, not by 180 degrees. Only a body wrapping halfway round
    the axis — a hemisphere — has its head and toe moving oppositely; a real slump
    subtends far less, and in the translation limit the fan collapses to a point.
    So the fan width is a measurement of the failure surface's curvature.

    Solve for the axis line of the gravity-constrained model: the locus where the
    rotational velocity cancels the cross-axis translation,
        x_axis = (w x b_perp) / |w|^2 .
    The distance from the block centre to that line is the radius of curvature R,
    the fan is L/R, and for a circular arc the depth below the chord follows as
    R(1 - cos(L/2R)) — an order-of-magnitude failure depth, no more.
    """
    zhat = np.array([0, 0, 1.0])
    pole, drop = geo['pole'], geo['drop']
    G, R = [], []
    for i in range(len(Xd)):
        for dn in ('ascending', 'descending'):
            v = rates[i].get(dn)
            if v is None:
                continue
            l = L[dn]
            G.append([float(l @ np.cross(pole, Xd[i])), float(l @ drop), float(l @ zhat)])
            R.append(v)
    if len(R) < 6:
        return None
    c, *_ = np.linalg.lstsq(np.array(G), np.array(R), rcond=None)
    w = pole * c[0]
    wn = np.linalg.norm(w)
    if wn < 1e-12:
        return None
    b = c[1] * drop + c[2] * zhat
    # NOTE: for this constrained family b is perpendicular to the pole by
    # construction (drop and z both lie in the vertical plane containing the
    # dropline), so there is no screw component to remove. Kept explicit so the
    # step is not mistaken for a result.
    b_perp = b - float(b @ (w / wn)) * (w / wn)
    x_axis = np.cross(w, b_perp) / wn ** 2
    cen = Xd.mean(axis=0)
    d = x_axis - cen
    d_perp = d - float(d @ (w / wn)) * (w / wn)
    Rr = float(np.linalg.norm(d_perp))
    dh = np.array([math.sin(math.radians(geo['drop_az'])),
                   math.cos(math.radians(geo['drop_az']))])
    sd = Xd[:, :2] @ dh
    Lb = float(sd.max() - sd.min())
    fan = Lb / Rr if Rr > 1e-9 else 0.0
    depth = Rr * (1 - math.cos(min(fan, math.pi) / 2)) if Rr > 1e-9 else None
    return {'radius_m': Rr, 'height_above_centre_m': float(d_perp[2]),
            'block_length_m': Lb, 'fan_deg': math.degrees(fan),
            'implied_depth_m': depth,
            # Classify on the FAN, not on R vs L: the fan is what is actually
            # observable on a stereonet and what distinguishes the regimes.
            'regime': ('translation-like — motion vectors nearly parallel'
                       if math.degrees(fan) < 20 else
                       'tightly curved — vectors fan widely'
                       if math.degrees(fan) > 90 else
                       'circular-slump geometry — radius near block length')}


def free_fit(Xd, rates):
    """Unconstrained 6-parameter rigid fit."""
    G, R, tags = [], [], []
    for i, r in enumerate(rates):
        for dn in ('ascending', 'descending'):
            v = r.get(dn)
            if v is None:
                continue
            G.append(np.concatenate([np.cross(Xd[i], L[dn]), L[dn]]))
            R.append(v); tags.append((i, dn))
    if len(R) < 8:
        return None
    G, R = np.array(G), np.array(R)
    coef, _, rank, _ = np.linalg.lstsq(G, R, rcond=None)
    pred = G @ coef
    resid = R - pred
    return {'omega': coef[:3], 'b': coef[3:], 'obs': R, 'pred': pred, 'tags': tags,
            'rank': int(rank), 'rms': float(np.sqrt(np.mean(resid ** 2))),
            'r2': 1 - float(resid @ resid) / max(float(((R - R.mean()) ** 2).sum()), 1e-9)}


def omega_series(blocks, Xd, pole, drop, v2, t_lo, t_hi, only=None):
    """Sliding-window Omega(t). Geometry fixed; only the rate varies.

    Each window re-derives per-block rates from the raw epochs inside it, so
    the series is not a smoothing of the period-average — it is an
    independent fit per window (windows overlap, so adjacent points are
    correlated; the envelope is per-window, not simultaneous).
    """
    ser = []
    t0 = t_lo
    while t0 + WIN_YR <= t_hi + 1e-9:
        t1 = t0 + WIN_YR
        rates = []
        for b in blocks:
            rr = {}
            for dn in ('ascending', 'descending'):
                if only and dn != only:
                    rr[dn] = None
                    continue
                vals = [window_rate(ps, v2, t0, t1) for ps in b['series'][dn]]
                vals = [v for v in vals if v is not None]
                rr[dn] = float(np.median(vals)) if vals else None
            rates.append(rr)
        nb = sum(1 for r in rates if r['ascending'] is not None or r['descending'] is not None)
        if nb >= MIN_BLK_WIN:
            f = fit_omega(Xd, rates, pole, drop)
            if f:
                f.update({'t_mid': round((t0 + t1) / 2, 3), 't0': round(t0, 3),
                          't1': round(t1, 3), 'n_blocks': int(nb),
                          'block_key': tuple(sorted(
                              i for i, r in enumerate(rates)
                              if r['ascending'] is not None or r['descending'] is not None))})
                ser.append(f)
        t0 += STEP_YR
    return ser


def core_series(ser, blocks, Xd, pole, drop, v2):
    """Recompute Omega(t) on a FIXED block set per track-composition era.

    Guards the failure mode where a drifting sample footprint masquerades as
    a rate change: within each group of windows sharing the same track
    composition, keep only blocks present in EVERY window of that group.
    """
    out = []
    groups = {}
    for s in ser:
        groups.setdefault(s['tracks'], []).append(s)
    for tracks, gs in groups.items():
        common = set(gs[0]['block_key'])
        for s in gs[1:]:
            common &= set(s['block_key'])
        if len(common) < MIN_BLK_WIN:
            continue
        idx = sorted(common)
        sub = [blocks[i] for i in idx]
        for s in gs:
            rates = []
            for b in sub:
                rr = {}
                for dn in ('ascending', 'descending'):
                    vals = [window_rate(ps, v2, s['t0'], s['t1']) for ps in b['series'][dn]]
                    vals = [v for v in vals if v is not None]
                    rr[dn] = float(np.median(vals)) if vals else None
                rates.append(rr)
            f = fit_omega(Xd[idx], rates, pole, drop)
            if f:
                out.append({'t_mid': s['t_mid'], 'omega': f['omega'], 'se': f['se'],
                            'tracks': tracks, 'n_blocks': len(idx)})
    return sorted(out, key=lambda r: r['t_mid'])


def stereonet(ax, rg, Xd, rates, fit, geo, U, ident=None, suspect=(), Ucon=None):
    th = np.linspace(0, 2 * np.pi, 200)
    ax.plot(np.sin(th), np.cos(th), 'k-', lw=1)
    for rr in (0.33, 0.66):
        ax.plot(rr * np.sin(th), rr * np.cos(th), color='#ccc', lw=0.5, zorder=0)
    for az_ in range(0, 360, 90):
        ax.text(1.09 * math.sin(math.radians(az_)), 1.09 * math.cos(math.radians(az_)),
                'NESW'[az_ // 90], ha='center', va='center', fontsize=10)
    for r in rates:
        t = r.get('terrain')
        if not t:
            continue
        x, y, _ = rg.stereo_xy(t['aspect'], t['slope'])
        ax.scatter(x, y, marker='v', s=36, c=C_SLOPE, edgecolors=C_SLOPE, zorder=2)
    # WHAT GETS PLOTTED. Total motion is the sum of a bulk translation and the
    # rotational part, and at Matanuska those are the same size (ratio ~1.05).
    # Plotting the TOTAL therefore scatters the points a median 49 deg from the
    # axis instead of the 90 deg a rotation demands, which makes the axis look
    # like it sits among its own motion vectors — the misreading Hig hit.
    #
    # So plot the ROTATIONAL PART, w x (x - x_bar). It is perpendicular to the
    # axis by construction, so it must lie on the great circle drawn below, and
    # the head-down / toe-up split reads directly off it. Bulk translation is a
    # separate statement and gets its own single marker.
    # Plot the GRAVITY-MODEL velocity, not "rotation about the centroid". The
    # latter is origin-dependent and forces the vectors to fan a full 180 deg,
    # which is the artefact behind the earlier claim that head and toe land on
    # the same spot. The constrained model's velocity is a genuine rotation about
    # the fitted axis, so it lies on the great circle below and fans only by the
    # angle the block subtends there.
    rot = Ucon if Ucon is not None else np.cross(
        np.tile(fit['omega'], (len(Xd), 1)), Xd - Xd.mean(axis=0))
    for j in range(len(Xd)):
        if np.linalg.norm(rot[j]) < 1e-12:
            continue
        az, pl = rg.az_plunge(rot[j])
        x, y, up = rg.stereo_xy(az, pl)
        ax.scatter(x, y, marker='o', s=50, c='none' if up else C_ASC,
                   edgecolors=C_ASC, linewidths=1.3, zorder=3)
    # the plane of rotational motion: every point above must fall on it
    om_u = (geo['pole'] if Ucon is not None
            else fit['omega'] / max(np.linalg.norm(fit['omega']), 1e-12))
    e1 = np.cross(om_u, np.array([0., 0., 1.]))
    if np.linalg.norm(e1) < 1e-9:
        e1 = np.array([1., 0., 0.])
    e1 = e1 / np.linalg.norm(e1)
    e2 = np.cross(om_u, e1)
    for t in np.linspace(0, 2 * np.pi, 361):
        v = math.cos(t) * e1 + math.sin(t) * e2
        azg, plg = rg.az_plunge(v)
        xg, yg, _ = rg.stereo_xy(azg, plg)
        ax.scatter(xg, yg, marker='.', s=2, c='#b9c2bf', zorder=1)
    # bulk translation of the whole domain, plotted apart from the rotation
    bulk = U.mean(axis=0)
    if np.linalg.norm(bulk) > 1e-12:
        azb, plb = rg.az_plunge(bulk)
        xb, yb, upb = rg.stereo_xy(azb, plb)
        ax.scatter(xb, yb, marker='D', s=95, c='none' if upb else '#CC79A7',
                   edgecolors='#CC79A7', linewidths=1.8, zorder=5)
    # The fitted axis is drawn ONLY when the full-gradient test says it is
    # identified. Where strain and rotation are entangled (rank 8/12 from two
    # look directions) the rigid fit still returns a tight, confident axis that
    # is an artefact of assuming rigidity — drawing it invites exactly the
    # misreading that it disagrees with gravity. Marked hollow-grey and named
    # as unidentified instead.
    om = fit['omega']
    identified = bool(ident and ident.get('axis_identified'))
    if np.linalg.norm(om) > 1e-12:
        a = om / np.linalg.norm(om)
        for v in (a, -a):
            az, pl = rg.az_plunge(v)
            x, y, up = rg.stereo_xy(az, pl)
            if identified:
                ax.scatter(x, y, marker='s', s=80, c='none' if up else C_AXIS,
                           edgecolors=C_AXIS, linewidths=1.5, zorder=4)
            else:
                ax.scatter(x, y, marker='s', s=70, c='none', edgecolors='#9aa3a0',
                           linewidths=1.2, linestyle=':', zorder=4)
    az, pl = rg.az_plunge(geo['drop'])
    x, y, _ = rg.stereo_xy(az, pl)
    ax.scatter(x, y, marker='v', s=150, c='#000000', zorder=5)
    for v in (geo['pole'], -geo['pole']):
        az, pl = rg.az_plunge(v)
        x, y, _ = rg.stereo_xy(az, max(pl, 0.0))
        ax.scatter(x, y, marker='*', s=230, c='#E69F00', edgecolors='k',
                   linewidths=0.8, zorder=5)
    # LEGEND — restored. The circle is data space, so it sits below the net.
    ax.scatter([], [], marker='v', s=110, c='#000000', label='avg dropline (plane fit)')
    ax.scatter([], [], marker='*', s=150, c='#E69F00', edgecolors='k',
               label='expected gravitational pole')
    ax.scatter([], [], marker='o', s=50, c=C_ASC, edgecolors=C_ASC,
               label='gravity-model motion, downward')
    ax.scatter([], [], marker='o', s=50, c='none', edgecolors=C_ASC, linewidths=1.3,
               label='gravity-model motion, upward — real')
    ax.scatter([], [], marker='D', s=80, c='#CC79A7', edgecolors='#CC79A7',
               label='bulk translation of the domain')
    ax.scatter([], [], marker='.', s=20, c='#b9c2bf',
               label='plane of rotation (⊥ axis)')
    ax.scatter([], [], marker='v', s=36, c=C_SLOPE, edgecolors=C_SLOPE,
               label='3DEP downslope')
    if identified:
        ax.scatter([], [], marker='s', s=80, c=C_AXIS, edgecolors=C_AXIS,
                   label='fitted rotation axis')
    else:
        ax.scatter([], [], marker='s', s=70, c='none', edgecolors='#9aa3a0',
                   linewidths=1.2, label='fitted axis — NOT identified (see caption)')
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.02), fontsize=7.5,
              framealpha=0.95, ncol=2)
    ax.set_xlim(-1.15, 1.15); ax.set_ylim(-1.15, 1.15)
    ax.set_aspect('equal'); ax.axis('off')


def figures(rec, dom, blocks, Xd, rates, geo, fit, ser, core, rg, site, lat0, lon0,
            per_track=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    tag = f"{rec['landslide_id']}_d{dom['k']}"
    U = np.cross(np.tile(fit['omega'], (len(Xd), 1)), Xd) + fit['b']

    # ---- fit figure: block map | observed vs predicted | stereonet --------
    fig, axg = plt.subplots(2, 2, figsize=(13.6, 10.6))
    axes = axg.ravel()
    ax = axes[0]
    g = shape(rec['geom'])
    for part in (g.geoms if g.geom_type == 'MultiPolygon' else [g]):
        ax.plot(*part.exterior.xy, color='k', lw=1, zorder=1)
    allx = [b['lon'] for b in blocks]; ally = [b['lat'] for b in blocks]
    vals = [(b['lon'], b['lat'], (b['rate'].get('ascending') or b['rate'].get('descending')))
            for b in blocks]
    vv = [v for _, _, v in vals if v is not None]
    lim = max(5.0, np.percentile(np.abs(vv), 95)) if vv else 10.0
    sc = ax.scatter([x for x, _, v in vals if v is not None],
                    [y for _, y, v in vals if v is not None],
                    c=[v for _, _, v in vals if v is not None], cmap='PRGn',
                    vmin=-lim, vmax=lim, s=42, edgecolors='#555', linewidths=0.3, zorder=2)
    plt.colorbar(sc, ax=ax, shrink=0.85, label='block LOS rate (mm/yr)')
    hull = [dd for dd in rec['domains'] if dd['k'] == dom['k']][0]['polygon']
    ax.plot([p[0] for p in hull] + [hull[0][0]], [p[1] for p in hull] + [hull[0][1]],
            color='#D55E00', lw=2, zorder=3, label=dom['label'])
    ax.legend(fontsize=8, loc='best')
    ax.set_aspect(1 / math.cos(math.radians(lat0)))
    ax.set_title(f'{site} — distilled blocks: {dom["label"]}', fontsize=10)
    ax.tick_params(labelsize=7)

    ax = axes[1]
    for dn, c, mk in (('ascending', C_ASC, 'o'), ('descending', C_DESC, 's')):
        m = [k for k, (i, d_) in enumerate(fit['tags']) if d_ == dn]
        if m:
            ax.scatter(fit['pred'][m], fit['obs'][m], c=c, marker=mk, s=44,
                       edgecolors='k', linewidths=0.4, label=f'{dn} (n={len(m)})')
    lim2 = max(8, float(np.abs(np.concatenate([fit['obs'], fit['pred']])).max()) * 1.15)
    ax.plot([-lim2, lim2], [-lim2, lim2], 'k:', lw=1)
    ax.set_xlim(-lim2, lim2); ax.set_ylim(-lim2, lim2); ax.set_aspect('equal')
    ax.set_xlabel('predicted LOS rate (mm/yr)'); ax.set_ylabel('observed (mm/yr)')
    ax.set_title(f'rigid fit: rms {fit["rms"]:.1f}, R² {fit["r2"]:.2f}, rank {fit["rank"]}/6',
                 fontsize=10)
    ax.legend(fontsize=8)

    # ---- panel 3: the slump profile -------------------------------------
    # The stereonet shows DIRECTIONS, and lower-hemisphere folding puts a head
    # vector and a toe vector (which are antipodal under a rotation) at the same
    # spot, separable only by fill. So the head-down / toe-up signature — the
    # thing that actually says "gravitational slump" — is invisible there. Plot
    # it directly instead: rotational vertical rate against downslope position.
    ax = axes[2]
    dh = np.array([math.sin(math.radians(geo['drop_az'])),
                   math.cos(math.radians(geo['drop_az']))])
    sd = Xd[:, :2] @ dh
    sd = sd - sd.mean()
    rot = np.cross(np.tile(fit['omega'], (len(Xd), 1)), Xd - Xd.mean(axis=0))
    ax.axhline(0, color='#888', lw=0.8)
    ax.axvline(0, color='#ddd', lw=0.8)
    ax.scatter(sd, U[:, 2], marker='s', s=26, c='none', edgecolors='#b9c2bf',
               linewidths=1.0, label='total vertical rate', zorder=2)
    ax.scatter(sd, rot[:, 2], marker='o', s=46, c=C_ASC, edgecolors='k',
               linewidths=0.3, label='rotational part', zorder=3)
    if len(sd) > 2:
        cf = np.polyfit(sd, rot[:, 2], 1)
        xs = np.linspace(sd.min(), sd.max(), 2)
        ax.plot(xs, np.polyval(cf, xs), color='#D55E00', lw=1.6, zorder=4,
                label=f'{cf[0]*1000:+.1f} mm/yr per km')
    ax.set_xlabel('downslope position (m)  ← head    toe →')
    ax.set_ylabel('vertical rate (mm/yr)')
    ax.set_title('slump profile: head down, toe up?', fontsize=10)
    ax.legend(fontsize=8, loc='best')

    ident = dom.get('identifiability')
    Ucon = None
    zh = np.array([0, 0, 1.0])
    Gc, Rc = [], []
    for i in range(len(Xd)):
        for dn in ('ascending', 'descending'):
            if rates[i].get(dn) is None:
                continue
            l = L[dn]
            Gc.append([float(l @ np.cross(geo['pole'], Xd[i])),
                       float(l @ geo['drop']), float(l @ zh)])
            Rc.append(rates[i][dn])
    if len(Rc) >= 6:
        cc, *_ = np.linalg.lstsq(np.array(Gc), np.array(Rc), rcond=None)
        # Every term here is perpendicular to the expected pole -- the cross
        # product by construction, and both drop and z lie in the vertical plane
        # containing the dropline. So the whole field lands on the great circle,
        # fanning only by the block's angular subtense at the axis.
        Ucon = (np.cross(np.tile(geo['pole'] * cc[0], (len(Xd), 1)), Xd)
                + cc[1] * geo['drop'] + cc[2] * zh)
    stereonet(axes[3], rg, Xd, rates, fit, geo, U, ident, Ucon=Ucon)
    if ident and ident.get('axis_identified'):
        sub = f'axis identified; {dom["ang_pole_deg"]:.0f}° from expected pole'
    else:
        sub = ('rotation axis not identified from 2 look directions\n'
               'Ω about the expected pole is the invariant quantity')
    axes[3].set_title(f'gravitational consistency\n{sub}', fontsize=10)
    plt.tight_layout()
    p1 = KIN / f'{tag}_fit.png'
    plt.savefig(p1, dpi=110, bbox_inches='tight'); plt.close()

    # ---- Omega(t) --------------------------------------------------------
    fig, (ax, axn) = plt.subplots(2, 1, figsize=(10, 6.4), sharex=True,
                                  gridspec_kw={'height_ratios': [3, 1]})
    if ser:
        t = np.array([s['t_mid'] for s in ser])
        om = np.array([s['omega'] for s in ser]) * URAD
        se = np.array([s['se'] for s in ser]) * URAD
        ax.fill_between(t, om - 2 * se, om + 2 * se, color=C_ASC, alpha=0.13, lw=0,
                        label='±2σ')
        ax.fill_between(t, om - se, om + se, color=C_ASC, alpha=0.28, lw=0, label='±1σ')
        ax.plot(t, om, color=C_ASC, lw=1.6, zorder=3)
        for s, tt, oo in zip(ser, t, om):
            both = '+' in s['tracks']
            ax.scatter([tt], [oo], s=54, zorder=4, color=C_ASC if both else 'none',
                       edgecolors=C_ASC, linewidths=1.4,
                       marker='o' if both else 'D')
    for dn, cc_, mk in (('ascending', C_ASC, '^'), ('descending', C_DESC, 'v')):
        q = (per_track or {}).get(dn) or []
        if len(q) < 2:
            continue
        ax.plot([r['t_mid'] for r in q], [r['omega'] * URAD for r in q],
                color=cc_, lw=1.0, alpha=0.85, marker=mk, ms=4, mfc='none', ls='-',
                zorder=6, label=f'{dn} alone')
    if core:
        tc = [s['t_mid'] for s in core]
        oc = [s['omega'] * URAD for s in core]
        ax.plot(tc, oc, color='#000000', lw=1.0, ls='--', zorder=5,
                label='fixed block set (composition control)')
    ax.axhline(0, color='#888', lw=0.8, zorder=1)
    full = dom['omega_urad_yr']
    ax.axhline(full, color='#D55E00', lw=1.2, ls=':', zorder=2,
               label=f'period average {full:+.1f} µrad/yr')
    ax.scatter([], [], s=54, color=C_ASC, edgecolors=C_ASC, marker='o',
               label='both tracks (3-param)')
    ax.scatter([], [], s=54, color='none', edgecolors=C_ASC, linewidths=1.4, marker='D',
               label='single track (Ω still identifiable)')
    ax.set_ylabel('Ω about expected pole (µrad/yr)')
    sec = ax.secondary_yaxis('right', functions=(lambda v: v * DEG_PER_KYR,
                                                 lambda v: v / DEG_PER_KYR))
    sec.set_ylabel('deg/kyr')
    ax.legend(fontsize=7.5, ncol=2, loc='best', framealpha=0.9)
    ax.set_title(f'{site} — {dom["label"]}: rotational velocity, fixed geometry '
                 f'({WIN_YR:.0f}-yr windows, {STEP_YR:.1f}-yr step)\n'
                 f'+Ω lowers the centre of mass; windows overlap so adjacent points '
                 f'are correlated', fontsize=10)
    if ser:
        axn.bar([s['t_mid'] for s in ser], [s['n_blocks'] for s in ser],
                width=STEP_YR * 0.8, color='#bbb', edgecolor='#888', lw=0.4)
    axn.set_ylabel('blocks'); axn.set_xlabel('year')
    axn.tick_params(labelsize=8)
    plt.tight_layout()
    p2 = KIN / f'{tag}_omega.png'
    plt.savefig(p2, dpi=110, bbox_inches='tight'); plt.close()
    return [p1.name, p2.name]


def build_blocks(site, geom, sc, v2, rg):
    """Dense grid -> unwrap-corrected point series -> L_c block medians."""
    g = shape(geom)
    lat0, lon0 = g.centroid.y, g.centroid.x
    gh = g.buffer(HALO_M / 111000.0)
    minx, miny, maxx, maxy = gh.bounds
    dlat = GRID_M / 111000.0
    dlon = GRID_M / (111000.0 * math.cos(math.radians(lat0)))
    pts, lat = [], miny
    while lat <= maxy:
        lon = minx
        while lon <= maxx:
            if gh.contains(Point(lon, lat)):
                pts.append((lat, lon))
            lon += dlon
        lat += dlat
    if len(pts) > CAP:
        rng = np.random.default_rng(3)
        pts = [pts[i] for i in rng.choice(len(pts), CAP, replace=False)]

    P = []
    for lat, lon in pts:
        rec = {'lat': lat, 'lon': lon, 'in': g.contains(Point(lon, lat)), 'ps': {}, 'rate': {}}
        ok = False
        for dn in ('ascending', 'descending'):
            ps = point_series(sc.fetch_ts(lat, lon, dn), v2)
            rec['ps'][dn] = ps
            rec['rate'][dn] = (v2.huber_rate(ps['t'] - ps['t'].min(), ps['d'], ps['sid'])
                               if ps else None)
            ok = ok or ps is not None
        if ok:
            P.append(rec)

    lc = LC.get(site, 150.0)
    XY = np.array([[(r['lon'] - lon0) * 111000 * math.cos(math.radians(lat0)),
                    (r['lat'] - lat0) * 111000] for r in P])
    cells = {}
    for i in range(len(P)):
        cells.setdefault((int(XY[i][0] // lc), int(XY[i][1] // lc)), []).append(i)
    blocks = []
    for members in cells.values():
        b = {'series': {}, 'rate': {}, 'n_pts': len(members),
             'in': any(P[i]['in'] for i in members),
             'lat': float(np.median([P[i]['lat'] for i in members])),
             'lon': float(np.median([P[i]['lon'] for i in members])),
             'xy': np.median([XY[i] for i in members], axis=0),
             'ncorr': sum((P[i]['ps'][d] or {}).get('ncorr', 0)
                          for i in members for d in ('ascending', 'descending'))}
        for dn in ('ascending', 'descending'):
            b['series'][dn] = [P[i]['ps'][dn] for i in members if P[i]['ps'][dn]]
            vals = [P[i]['rate'][dn] for i in members if P[i]['rate'][dn] is not None]
            b['rate'][dn] = float(np.median(vals)) if vals else None
            # Per-block rate uncertainty from its own time-series scatter. Needed
            # because the noise is NOT uniform: a resolution cell that is
            # internally deforming has its scatterers moving relative to ONE
            # ANOTHER, which scrambles the speckle far faster than bulk motion
            # does. Measured at Columbia, where |rate| and series scatter
            # correlate at +0.85; absent at Matanuska (+0.05).
            sd = []
            for ps in b['series'][dn]:
                t = ps['t'] - ps['t'].min()
                span = max(float(t.max() - t.min()), 1e-6)
                A = np.zeros((len(t), 1 + len(np.unique(ps['sid'])))); A[:, 0] = t
                for j, s_ in enumerate(np.unique(ps['sid'])):
                    A[ps['sid'] == s_, 1 + j] = 1
                co, *_ = np.linalg.lstsq(A, ps['d'], rcond=None)
                sd.append(float(np.std(ps['d'] - A @ co)) /
                          max(math.sqrt(len(t)) * span / 2, 1e-6))
            b.setdefault('sig', {})[dn] = float(np.median(sd)) if sd else None
        if b['series']['ascending'] or b['series']['descending']:
            blocks.append(b)
    terr = rg.terrain_at([(b['lat'], b['lon']) for b in blocks])
    X = []
    for b, t in zip(blocks, terr):
        b['terrain'] = t
        X.append([b['xy'][0], b['xy'][1], (t or {}).get('z', 0.0) or 0.0])
    return blocks, np.array(X), lat0, lon0, lc


def track_spans(blocks):
    sp = {}
    for dn in ('ascending', 'descending'):
        tt = [ps['t'] for b in blocks for ps in b['series'][dn]]
        if tt:
            sp[dn] = (float(min(t.min() for t in tt)), float(max(t.max() for t in tt)))
    return sp


def main():
    import importlib.util as iu

    def load(nm, fn):
        s = iu.spec_from_file_location(nm, ROOT / 'tools' / fn)
        m = iu.module_from_spec(s); s.loader.exec_module(m); return m
    sc = load('sc', 'insar_site_characterization.py')
    v2 = load('v2', 'insar_domain_rotation_v2.py')
    rg = load('rg', 'insar_rotation_gravity.py')

    KIN.mkdir(parents=True, exist_ok=True)
    sites = json.load(open(OUT / 'sites.json'))
    for site, meta in sites.items():
        print(f'\n===== {site} (landslide {meta["id"]}) =====', flush=True)
        blocks, X, lat0, lon0, lc = build_blocks(site, meta['geom'], sc, v2, rg)
        print(f'  {len(blocks)} blocks at {lc:.0f} m; '
              f'{sum(b["ncorr"] for b in blocks)} unwrap-slip corrections')
        spans = track_spans(blocks)
        for dn, (a, b_) in spans.items():
            print(f'  {dn:11}: {a:.2f} .. {b_:.2f}')
        ov_lo = max(s[0] for s in spans.values()) if len(spans) > 1 else None
        ov_hi = min(s[1] for s in spans.values()) if len(spans) > 1 else None
        if ov_lo is not None:
            print(f'  two-track overlap: {ov_lo:.2f} .. {ov_hi:.2f} '
                  f'({ov_hi - ov_lo:.1f} yr)')

        brows = [{'ascending': ({'rate': b['rate']['ascending']}
                                if b['rate']['ascending'] is not None else None),
                  'descending': ({'rate': b['rate']['descending']}
                                 if b['rate']['descending'] is not None else None),
                  'in': b['in']} for b in blocks]
        halo = [b['rate'][d] for b in blocks if not b['in']
                for d in ('ascending', 'descending') if b['rate'][d] is not None]
        tol = 1.8 * float(np.std(halo)) if len(halo) >= 5 else 5.0
        v2.tol_len = lc
        # DOMAIN SET. Growth alone is not enough, and Hig's Matanuska review
        # showed why: growth minimises RESIDUAL, so it prefers short, tightly
        # rigid fragments — but a rotation is expressed AS a downslope gradient,
        # so a fragment that does not span head-to-toe cannot express it. At
        # Matanuska the grown pieces (833 m downslope) returned Omega ~ 0 while
        # the mapped extent (1101 m, and NOT rigid: rms 5.5 vs 3.1) returns
        # +7.9 +/- 2.6 urad/yr mass-lowering at 3 sigma. Optimising rigidity
        # destroyed the signal it was meant to find.
        #
        # So the mapped extent is ALWAYS evaluated as its own domain. It is a
        # legitimate physical hypothesis — the geologist's polygon — and Omega
        # about a SPECIFIED axis stays well posed even where the body deforms
        # and the free axis does not.
        grown = v2.grow_domains(X, brows, tol)
        mapped = [i for i, b in enumerate(blocks) if b['in']]
        doms = ([{'idx': mapped, 'label': 'mapped extent'}] if len(mapped) >= 6 else [])
        for j, dm in enumerate(grown, 1):
            dm['label'] = f'grown element {j}'
            doms.append(dm)
        print(f'  halo std {np.std(halo) if halo else float("nan"):.1f} -> tol {tol:.1f}; '
              f'{len(doms)} element(s)')

        rec = {'landslide_id': meta['id'], 'site': site, 'geom': meta['geom'],
               'generated': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
               'block_size_m': lc, 'n_blocks': len(blocks),
               'track_spans': {k: [round(a, 2), round(b_, 2)] for k, (a, b_) in spans.items()},
               'overlap': ([round(ov_lo, 2), round(ov_hi, 2)] if ov_lo is not None else None),
               'window_yr': WIN_YR, 'step_yr': STEP_YR, 'domains': []}

        for k, dm in enumerate(doms):
            idx = dm['idx']
            Xd = X[idx]
            sub = [blocks[i] for i in idx]
            rates = [{'ascending': b['rate']['ascending'],
                      'descending': b['rate']['descending'],
                      'terrain': b['terrain']} for b in sub]
            geo = domain_geometry(Xd)
            fit = free_fit(Xd, rates)
            if fit is None:
                continue
            ident = gradient_identifiability(Xd, rates, geo['pole'])
            halo_blocks = [b for b in blocks if not b['in']]
            con = fit_omega(Xd, rates, geo['pole'], geo['drop'])
            a = fit['omega'] / max(np.linalg.norm(fit['omega']), 1e-12)
            ang = math.degrees(math.acos(np.clip(abs(float(a @ geo['pole'])), 0, 1)))
            n_obs = len(fit['obs'])
            dbic = (n_obs * math.log(max(con['rms'] ** 2, 1e-12)) + 3 * math.log(n_obs)
                    - n_obs * math.log(max(fit['rms'] ** 2, 1e-12)) - 6 * math.log(n_obs))

            # epoch-consistent refit: both tracks restricted to the overlap
            com = None
            if ov_lo is not None and ov_hi - ov_lo > 0.75:
                crates = []
                for b in sub:
                    rr = {'terrain': b['terrain']}
                    for dn in ('ascending', 'descending'):
                        vals = [window_rate(ps, v2, ov_lo, ov_hi) for ps in b['series'][dn]]
                        vals = [v for v in vals if v is not None]
                        rr[dn] = float(np.median(vals)) if vals else None
                    crates.append(rr)
                cf = fit_omega(Xd, crates, geo['pole'], geo['drop'])
                ff = free_fit(Xd, crates)
                if cf:
                    com = {'omega_urad_yr': cf['omega'] * URAD, 'se': cf['se'] * URAD,
                           'rms': cf['rms'], 'r2': cf['r2'], 'n_obs': cf['n_obs'],
                           'tracks': cf['tracks'],
                           'free_rms': ff['rms'] if ff else None,
                           'free_r2': ff['r2'] if ff else None}

            # mean vertical rate + null-space bracket (rank-deficiency honesty)
            U = np.cross(np.tile(fit['omega'], (len(Xd), 1)), Xd) + fit['b']
            G = np.array([np.concatenate([np.cross(Xd[i], L[dn]), L[dn]])
                          for i, r in enumerate(rates)
                          for dn in ('ascending', 'descending') if r[dn] is not None])
            nullv = np.linalg.svd(G)[2][-1]
            Un = np.cross(np.tile(nullv[:3], (len(Xd), 1)), Xd) + nullv[3:]
            scale = float(np.sqrt(np.mean(fit['obs'] ** 2))) / max(float(np.abs(Un).max()), 1e-12)
            shift = abs(float(np.mean(Un[:, 2])) * scale)
            mu = float(np.mean(U[:, 2]))
            # HONESTY ABOUT WHAT THE MEAN VERTICAL RATE IS WORTH.
            # The unobservable direction is 100% translation (verified: the null
            # vector's rotation part is ~1e-18), pointing north-south and tilted
            # ~10 deg above horizontal. So undetectable drift carries an
            # undetectable VERTICAL component, and the domain-mean uz slides
            # with it: at Matanuska it runs +0.8 to -6.4 mm/yr across a
            # plausible range of invisible drift, changing 0.18 mm/yr for every
            # 1 mm/yr of drift. Mass-lowering is therefore NOT determined by the
            # data alone. Omega about a specified axis is exactly invariant
            # under the same shift, because a pure translation has no rotational
            # part to contribute. Omega is the usable test; mean uz is not.
            duz_per_drift = abs(float(nullv[3:][2]) / max(np.linalg.norm(nullv[3:]), 1e-12))
            verdict = ('not determined by the data alone — the invisible drift '
                       f'moves it {duz_per_drift:.2f} mm/yr per mm/yr')

            t_lo = min(s[0] for s in spans.values())
            t_hi = max(s[1] for s in spans.values())
            ser = omega_series(sub, Xd, geo['pole'], geo['drop'], v2, t_lo, t_hi)
            core = core_series(ser, sub, Xd, geo['pole'], geo['drop'], v2)
            # PER-TRACK CONTROL. The combined series changes track composition
            # partway through (asc-only -> both -> desc-only), so an apparent
            # excursion could be a handover artefact. Each track fitted alone,
            # across its own full span, is the test: if asc-only and desc-only
            # agree where they overlap, the excursion is in the ground, not in
            # the geometry mix. Omega alone is identifiable single-track.
            per_track = {dn: omega_series(sub, Xd, geo['pole'], geo['drop'], v2,
                                          spans[dn][0], spans[dn][1], only=dn)
                         for dn in spans}
            t_om = abs(con['omega'] / con['se']) if con and con['se'] > 0 else 0.0
            sense = ('indeterminate (translation limit)' if t_om < 2 else
                     'mass-LOWERING' if con['omega'] > 0 else 'mass-RAISING')

            hull = MultiPoint([(b['lon'], b['lat']) for b in sub]).convex_hull
            hull = hull.buffer(lc / 2 / 111000.0, 2)
            poly = [[round(x, 6), round(y, 6)] for x, y in hull.exterior.coords]

            # downslope extent governs whether a rotation is expressible at
            # all, so it is recorded per domain.
            dh = np.array([math.sin(math.radians(geo['drop_az'])),
                           math.cos(math.radians(geo['drop_az']))])
            sd = Xd[:, :2] @ dh
            d = {'k': k, 'label': dm.get('label', f'element {k}'),
                 'downslope_extent_m': float(sd.max() - sd.min()),
                 'n_blocks': len(idx),
                 'n_in_polygon': sum(1 for b in sub if b['in']),
                 'rms': fit['rms'], 'r2': fit['r2'], 'rank': fit['rank'],
                 'slope_deg': geo['slope_deg'], 'dropline_az': geo['drop_az'],
                 'ang_pole_deg': ang, 'dbic_constrained_minus_free': dbic,
                 'identifiability': ident,
                 'reference_field': None, 'weighted': None, 'rotation_axis': None,
                 'omega_urad_yr': con['omega'] * URAD if con else None,
                 'omega_se_urad_yr': con['se'] * URAD if con else None,
                 'omega_deg_per_kyr': (con['omega'] * URAD * DEG_PER_KYR) if con else None,
                 'sigma': t_om, 'sense': sense,
                 'uz_mean_mm_yr': mu, 'uz_lo': mu - shift, 'uz_hi': mu + shift,
                 'uz_sensitivity_per_drift': duz_per_drift,
                 'verdict': verdict, 'common_window': com,
                 'omega_series': [{k2: s[k2] for k2 in
                                   ('t_mid', 't0', 't1', 'omega', 'se', 'n_obs',
                                    'n_blocks', 'tracks', 'rms', 'r2')} for s in ser],
                 'omega_core': core, 'polygon': poly,
                 'omega_per_track': {dn: [{k2: q[k2] for k2 in
                                           ('t_mid', 'omega', 'se', 'n_blocks')}
                                          for q in v] for dn, v in per_track.items()}}
            d['rotation_axis'] = rotation_axis(Xd, rates, geo)
            ra = d['rotation_axis']
            if ra:
                print(f'    rotation axis {ra["radius_m"]:.0f} m from the block centre '
                      f'({ra["height_above_centre_m"]:+.0f} m vertically); fan L/R = '
                      f'{ra["fan_deg"]:.0f}°; {ra["regime"]}')
            d['weighted'] = weighted_omega(Xd, sub, geo['pole'], geo['drop'])
            if d['weighted']:
                w = d['weighted']
                print(f'    weighting sensitivity: equal-weight Ω '
                      f'{d["omega_urad_yr"]:+.1f}±{d["omega_se_urad_yr"]:.1f} vs '
                      f'inverse-variance {w["omega_urad_yr"]:+.1f}±{w["se"]:.1f} µrad/yr '
                      f'(block σ spans {w["sigma_min"]:.2f}–{w["sigma_max"]:.2f} mm/yr)')
            d['reference_field'] = reference_field_check(
                Xd, rates, halo_blocks, geo['pole'], geo['drop'],
                d['omega_urad_yr'])
            rf = d['reference_field']
            if rf:
                print(f'    reference field: halo trend '
                      f'{rf["trend"]["ascending"]["grad_mm_yr_km"]:.1f}/'
                      f'{rf["trend"]["descending"]["grad_mm_yr_km"]:.1f} mm/yr/km '
                      f'(asc/desc); it alone would fake Ω = '
                      f'{rf["omega_from_trend_alone"]:+.1f}; detrended Ω = '
                      f'{rf["omega_detrended"]:+.1f} µrad/yr')
            rec['domains'].append(d)
            print(f'  [{d["label"]}] {len(idx)} blocks ({d["n_in_polygon"]} in-polygon), '
                  f'downslope extent {d["downslope_extent_m"]:.0f} m, '
                  f'rms {fit["rms"]:.1f}, R² {fit["r2"]:.2f}, rank {fit["rank"]}/6')
            print(f'    Ω = {d["omega_urad_yr"]:+.1f} ± {d["omega_se_urad_yr"]:.1f} µrad/yr '
                  f'({d["omega_deg_per_kyr"]:+.2f}°/kyr, {t_om:.1f}σ) — {sense}')
            if ident:
                print(f'    axis: rigid {ident["ang_rigid"]:.0f}° vs full-gradient '
                      f'{ident["ang_linear"]:.0f}° from expected (shift {ident["axis_shift_deg"]:.0f}°); '
                      f'|S|/|W| {ident["strain_to_rotation"]:.2f}; rank {ident["rank12"]}/12 -> '
                      f'{"axis identified" if ident["axis_identified"] else "AXIS NOT IDENTIFIED"}')
            print(f'    pole {ang:.0f}° from expected; ΔBIC {dbic:+.1f}; '
                  f'mean uz {mu:+.1f} mm/yr — {verdict}')
            if com:
                print(f'    common-window ({ov_lo:.1f}–{ov_hi:.1f}) Ω = '
                      f'{com["omega_urad_yr"]:+.1f} ± {com["se"]:.1f} µrad/yr '
                      f'(vs {d["omega_urad_yr"]:+.1f} full-period)')
            print(f'    Ω(t): {len(ser)} windows, '
                  f'{sum(1 for s in ser if "+" in s["tracks"])} with both tracks')
            d['figs'] = figures(rec, d, blocks, Xd, rates, geo, fit, ser, core,
                                rg, site, lat0, lon0, per_track)

        (KIN / f'{meta["id"]}.json').write_text(json.dumps(rec, indent=1, default=float))
        print(f'  wrote {KIN / f"{meta['id']}.json"}')
    print('\nKINEMATICS-COMPLETE')


if __name__ == '__main__':
    main()
