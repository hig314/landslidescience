#!/usr/bin/env python3
"""Domain-growth + rotation fits on the DENSIFIED, distilled datasets — with
robust per-point rates and a rate-vs-geometry time split. (Hig, 2026-08-22.)

Three upgrades over the sparse-grid pass:

1. ROBUST PER-POINT RATES. Each DISP-S1 granule is one interferometric pair,
   so a misinterpreted interferogram is one bad epoch — detectable. Per
   stack: (a) epoch-to-epoch steps within ±QUANTUM_TOL of n × 27.7 mm
   (C-band 2π ambiguity) are unwrap slips: corrected by the integer shift
   IF the correction reduces the stack's detrended variance, else left
   alone (no eager snapping); (b) remaining outliers are Huber-downweighted
   (IRLS) in the rate fit. Correction counts are reported — how much
   surgery the data needed is itself a data-quality map. This is what the
   velocity mosaic cannot do: it integrates bad pairs into the rate.

2. DISTILL THEN GROW. Block-median rates at the MEASURED correlation length
   (150 m Matanuska / 330 m Columbia, from the dense pull), then
   multi-domain growth with anomaly-preferring seeds (grow, remove, repeat)
   — the fixes specified after the single-domain demo excluded Columbia's
   fast core. Block z from 3DEP (cached).

3. RATE vs GEOMETRY over time. Constant-geometry / varying-rate model per
   Hig: split epochs into early/late halves, per-half robust rates, per-half
   free rigid fits on the SAME domain. Geometry constancy = correlation of
   the two halves' predicted fields; rate change = amplitude ratio from a
   1-param scaling fit late = k x early. True geometry change is kept in
   mind but NOT given freedom — a low geometry correlation is reported as a
   flag, not fitted.

All series come from the standing cache (dense pull done); only block-center
DEM samples may fetch. Outputs: figures + stats per site.
"""
import json
import math
from pathlib import Path

import numpy as np
from shapely.geometry import shape, Point

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'data' / 'insar_power'
QUANTUM = 27.7          # mm, C-band lambda/2
QUANTUM_TOL = 6.0       # mm
HUBER_C = 1.345
L_ASC = np.array([-0.613, -0.142, 0.777])
L_DESC = np.array([0.613, -0.142, 0.777])
LC = {'matanuska': 150.0, 'columbia_a': 330.0}   # measured (dense pull)
GRID_M, HALO_M, CAP = 60, 300, 360               # match the dense pull


def series_arrays(ts):
    t, d, sid = [], [], []
    for i, st in enumerate(ts['series']):
        for sec, mm in st['points']:
            t.append(np.datetime64(sec[:10]).astype('datetime64[D]').astype(float) / 365.25)
            d.append(mm); sid.append(i)
    if not t:
        return None
    t = np.array(t); d = np.array(d); sid = np.array(sid)
    o = np.argsort(t)
    return t[o] - t[o].min(), d[o], sid[o]


def unwrap_correct(t, d, sid):
    """Integer-quantum slip correction per stack, accepted only if it lowers
    detrended variance. Returns corrected d + number of corrections."""
    d = d.copy()
    ncorr = 0
    for s_ in np.unique(sid):
        m = np.where(sid == s_)[0]
        if len(m) < 6:
            continue
        seg = d[m]
        for k in range(1, len(m)):
            step = seg[k] - seg[k - 1]
            n = round(step / QUANTUM)
            if n != 0 and abs(step - n * QUANTUM) < QUANTUM_TOL:
                trial = seg.copy()
                trial[k:] -= n * QUANTUM
                def dvar(x):
                    A = np.c_[t[m], np.ones(len(m))]
                    r = x - A @ np.linalg.lstsq(A, x, rcond=None)[0]
                    return float(r @ r)
                if dvar(trial) < 0.8 * dvar(seg):
                    seg = trial
                    ncorr += 1
        d[m] = seg
    return d, ncorr


def huber_rate(t, d, sid, epoch_mask=None):
    """Huber-IRLS rate with per-stack offsets; optional epoch subset."""
    if epoch_mask is not None:
        t, d, sid = t[epoch_mask], d[epoch_mask], sid[epoch_mask]
    if len(t) < 10:
        return None
    stacks = np.unique(sid)
    A = np.zeros((len(t), 1 + len(stacks)))
    A[:, 0] = t
    for j, s_ in enumerate(stacks):
        A[sid == s_, 1 + j] = 1
    w = np.ones(len(t))
    coef = None
    for _ in range(6):
        Aw = A * w[:, None]
        coef, *_ = np.linalg.lstsq(Aw.T @ A, Aw.T @ d, rcond=None)
        r = d - A @ coef
        s_mad = 1.4826 * np.median(np.abs(r - np.median(r))) or 1.0
        u = np.abs(r) / (HUBER_C * s_mad)
        w = np.where(u <= 1, 1.0, 1.0 / np.maximum(u, 1e-9))
    return float(coef[0])


def robust_point(ts):
    arr = series_arrays(ts)
    if arr is None or len(arr[0]) < 15:
        return None
    t, d, sid = arr
    d2, ncorr = unwrap_correct(t, d, sid)
    tmid = np.median(t)
    return {'rate': huber_rate(t, d2, sid),
            'rate_early': huber_rate(t, d2, sid, t <= tmid),
            'rate_late': huber_rate(t, d2, sid, t > tmid),
            'ncorr': ncorr, 'n': len(t)}


def rigid_fit(X, rows, key='rate'):
    G, R = [], []
    for i, r in enumerate(rows):
        for dirn, l in (('ascending', L_ASC), ('descending', L_DESC)):
            v = (r.get(dirn) or {}).get(key) if isinstance(r.get(dirn), dict) else None
            if v is None:
                continue
            G.append(np.concatenate([np.cross(X[i], l), l]))
            R.append(v)
    if len(R) < 8:
        return None
    G, R = np.array(G), np.array(R)
    coef, *_ = np.linalg.lstsq(G, R, rcond=None)
    pred = G @ coef
    rms = float(np.sqrt(np.mean((R - pred) ** 2)))
    r2 = 1 - float(((R - pred) ** 2).sum()) / max(float(((R - R.mean()) ** 2).sum()), 1e-9)
    return {'coef': coef, 'obs': R, 'pred': pred, 'rms': rms, 'r2': r2, 'G': G}


def grow_domains(X, rows, tol, max_domains=2):
    """Anomaly-seeded contiguous growth; grow, remove, repeat."""
    def rms_of(idx):
        f = rigid_fit(X[idx], [rows[i] for i in idx])
        return f['rms'] if f else np.inf
    remaining = set(range(len(rows)))
    domains = []
    for _ in range(max_domains):
        if len(remaining) < 4:
            break
        # anomaly-preferring seed: kNN patch around the highest-|rate| block
        def blockrate(i):
            vals = [abs((rows[i].get(d) or {}).get('rate') or 0)
                    for d in ('ascending', 'descending')]
            return max(vals)
        order = sorted(remaining, key=blockrate, reverse=True)
        seed_center = order[0]
        rem = np.array(sorted(remaining))
        d2 = ((X[rem][:, :2] - X[seed_center][:2]) ** 2).sum(axis=1)
        seed = set(rem[np.argsort(d2)[:4]])
        dom = set(seed)
        while True:
            cands = []
            fit_now = rigid_fit(X[sorted(dom)], [rows[i] for i in sorted(dom)])
            for j in remaining - dom:
                dmin = min(np.linalg.norm(X[j][:2] - X[i][:2]) for i in dom)
                if dmin > 1.5 * tol_len:
                    continue
                # Point-residual gate BEFORE the global-rms gate: a quiet
                # halo block barely moves the global rms of a big fit, which
                # is how the first pass swallowed everything. The candidate
                # must itself be predicted well by the refit.
                idx2 = sorted(dom | {j})
                f2 = rigid_fit(X[idx2], [rows[i] for i in idx2])
                if f2 is None:
                    continue
                jpos = idx2.index(j)
                # residuals of the candidate's own observations
                jres = []
                k = 0
                for ii in idx2:
                    for dirn in ('ascending', 'descending'):
                        v = (rows[ii].get(dirn) or {}).get('rate')
                        if v is None:
                            continue
                        if ii == j:
                            jres.append(abs(f2['obs'][k] - f2['pred'][k]))
                        k += 1
                if jres and max(jres) > tol:
                    continue
                cands.append((f2['rms'], j))
            if not cands:
                break
            r, j = min(cands)
            if r > tol:
                break
            dom.add(j)
        f = rigid_fit(X[sorted(dom)], [rows[i] for i in sorted(dom)])
        if f and len(dom) >= 4:
            domains.append({'idx': sorted(dom), 'fit': f})
        remaining -= dom
    return domains


def main():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'sc', ROOT / 'tools' / 'insar_site_characterization.py')
    sc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sc)
    spec2 = importlib.util.spec_from_file_location(
        'rg', ROOT / 'tools' / 'insar_rotation_gravity.py')
    rg = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(rg)

    global tol_len
    sites = json.load(open(OUT / 'sites.json'))
    for name, site in sites.items():
        print(f'\n===== {name}: distilled domain-rotation v2 =====')
        lc = LC[name]
        tol_len = lc
        g = shape(site['geom'])
        lat0, lon0 = g.centroid.y, g.centroid.x
        gh = g.buffer(HALO_M / 111000.0)
        minx, miny, maxx, maxy = gh.bounds
        dlat = GRID_M / 111000.0
        dlon = GRID_M / (111000.0 * math.cos(math.radians(lat0)))
        pts = []
        lat = miny
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

        # robust per-point rates (all cached)
        rows, ncorr_tot, npts_corr = [], 0, 0
        for lat, lon in pts:
            row = {'lat': lat, 'lon': lon, 'in': g.contains(Point(lon, lat))}
            ok = False
            for dirn in ('ascending', 'descending'):
                rp = robust_point(sc.fetch_ts(lat, lon, dirn))
                row[dirn] = rp
                if rp:
                    ok = True
                    ncorr_tot += rp['ncorr']
                    npts_corr += 1 if rp['ncorr'] else 0
            if ok:
                rows.append(row)
        print(f'  {len(rows)} points with data; unwrap-slip corrections: '
              f'{ncorr_tot} steps across {npts_corr} point-series')

        # distill to blocks at measured L_c
        X0 = np.array([[(r['lon'] - lon0) * 111000 * math.cos(math.radians(lat0)),
                        (r['lat'] - lat0) * 111000, 0.0] for r in rows])
        blocks = {}
        for i, r in enumerate(rows):
            key = (int(X0[i][0] // lc), int(X0[i][1] // lc))
            blocks.setdefault(key, []).append(i)
        brows, bX, blatlon = [], [], []
        for key, members in blocks.items():
            rr = {}
            for dirn in ('ascending', 'descending'):
                med = {}
                for k in ('rate', 'rate_early', 'rate_late'):
                    vals = [rows[i][dirn][k] for i in members
                            if rows[i][dirn] and rows[i][dirn][k] is not None]
                    med[k] = float(np.median(vals)) if vals else None
                rr[dirn] = med if med['rate'] is not None else None
            rr['in'] = any(rows[i]['in'] for i in members)
            brows.append(rr)
            bX.append(np.median([X0[i] for i in members], axis=0))
            blatlon.append((float(np.median([rows[i]['lat'] for i in members])),
                            float(np.median([rows[i]['lon'] for i in members]))))
        bX = np.array(bX)
        # block z from 3DEP
        terr = rg.terrain_at(blatlon)
        for j, tr in enumerate(terr):
            bX[j][2] = (tr or {}).get('z', 0.0) or 0.0
            brows[j]['terrain'] = tr
        print(f'  {len(brows)} blocks at {lc:.0f} m')

        # noise tol from out-of-polygon blocks
        ring_rates = [ (b.get(d) or {}).get('rate') for b in brows if not b['in']
                       for d in ('ascending', 'descending')]
        ring_rates = [v for v in ring_rates if v is not None]
        tol = 1.8 * float(np.std(ring_rates)) if len(ring_rates) >= 5 else 5.0
        print(f'  halo-block rate std {np.std(ring_rates):.1f} -> growth tol {tol:.1f} mm/yr')

        domains = grow_domains(bX, brows, tol)
        for di, dm in enumerate(domains):
            f = dm['fit']
            inpoly = sum(1 for i in dm['idx'] if brows[i]['in'])
            print(f'  domain {di + 1}: {len(dm["idx"])} blocks ({inpoly} in-polygon), '
                  f'free fit rms {f["rms"]:.1f} mm/yr, R2 {f["r2"]:.2f}')
            # early/late: shared geometry, changed rate?
            fe = rigid_fit(bX[dm['idx']], [brows[i] for i in dm['idx']], 'rate_early')
            fl = rigid_fit(bX[dm['idx']], [brows[i] for i in dm['idx']], 'rate_late')
            if fe and fl and len(fe['obs']) == len(fl['obs']):
                corr = float(np.corrcoef(fe['pred'], fl['pred'])[0, 1])
                k, *_ = np.linalg.lstsq(fe['pred'][:, None], fl['obs'], rcond=None)
                print(f'    early vs late: geometry correlation r={corr:.2f}; '
                      f'amplitude ratio late/early k={float(k[0]):.2f} '
                      f'({"geometry consistent, rate scaled" if corr > 0.7 else "GEOMETRY FLAG — pattern changed"})')
    print('\nV2-COMPLETE')


if __name__ == '__main__':
    main()
