#!/usr/bin/env python3
"""Rigid-rotation fit diagnostics + gravitational-consistency stereonet.

Hig's reframing (2026-08-22): fit the 3-D rigid motion to the kinematic
domain FIRST, then ask a separate physical question — is the fitted motion
consistent with gravitational driving? Kinematics and physics as independent
tests, visualized:

  Panel 1  point motion vs best-fit rotation: observed per-point LOS rates
           against the rigid model's predictions, both tracks (the fit-
           quality plot that was missing from the results page).
  Panel 2  lower-hemisphere equal-area stereonet: the fitted model's 3-D
           velocity direction at every domain point (azimuth/plunge),
           overlaid on the local downslope directions from 3DEP, plus the
           rotation axis. Gravitational consistency reads as clustering of
           motion vectors around the downslope field — EXCEPT at a slump
           toe, where upward-plunging motion (open symbols, plotted at the
           antipode) is itself the rotational signature. The scalar check
           is therefore mean vertical rate over the domain (mass lowering),
           not per-point downslope-ness.

3-D positions and slope/aspect come from the USGS 3DEP dynamic service
(getSamples; point + 4 offsets at 30 m -> central differences). Fetch, not
storage, per the DEM plan.

COLUMBIA CAVEAT (Hig): 3DEP over lower Columbia Fjord A predates glacier
retreat — the "ground" there is a former ice surface. Points below
GLACIER_SURFACE_SUSPECT_M are flagged (open markers, excluded from the
gravity stats) rather than silently trusted. Threshold is a stated guess —
adjust when a better ice-extent mask exists.

Domains: Matanuska = boundary-agnostic growth (with real z). Columbia A =
the fast core (asc rate < -8 mm/yr, in/near polygon) — the multi-domain
extraction that would find this automatically is specified in the plan but
not yet built; selecting it by hand here is analysis, not detection.

Run insar_site_characterization.py first (shares its cached fetches).
"""
import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from shapely.geometry import shape, Point

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'data' / 'insar_power'
DEM_SAMPLES = ('https://elevation.nationalmap.gov/arcgis/rest/services/'
               '3DEPElevation/ImageServer/getSamples')
L_ASC = np.array([-0.613, -0.142, 0.777])
L_DESC = np.array([0.613, -0.142, 0.777])
OFF_M = 30.0
GLACIER_SURFACE_SUSPECT_M = 100.0     # Columbia only; stated guess
_dem_cache_path = OUT / 'dem_samples_cache.json'
_dem_cache = json.loads(_dem_cache_path.read_text()) if _dem_cache_path.exists() else {}

# Okabe-Ito (colorblind-safe); track identity also shape-coded everywhere.
C_ASC, C_DESC, C_SLOPE, C_AXIS = '#0072B2', '#E69F00', '#7f8a86', '#000000'


def dem_elevations(lonlats):
    """Batch 3DEP elevations with a disk cache; returns dict key->m."""
    want = [(round(lo, 6), round(la, 6)) for lo, la in lonlats]
    missing = [k for k in want if f'{k[0]},{k[1]}' not in _dem_cache]
    for i in range(0, len(missing), 40):
        chunk = missing[i:i + 40]
        geom = json.dumps({'points': [[lo, la] for lo, la in chunk],
                           'spatialReference': {'wkid': 4326}})
        q = urllib.parse.urlencode({'geometry': geom,
                                    'geometryType': 'esriGeometryMultipoint',
                                    'returnFirstValueOnly': 'true', 'f': 'json'})
        with urllib.request.urlopen(f'{DEM_SAMPLES}?{q}', timeout=60) as r:
            d = json.loads(r.read().decode())
        for s in d.get('samples', []):
            k = f"{round(s['location']['x'],6)},{round(s['location']['y'],6)}"
            try:
                _dem_cache[k] = float(s['value'])
            except (TypeError, ValueError):
                _dem_cache[k] = None
        time.sleep(0.5)
    _dem_cache_path.write_text(json.dumps(_dem_cache))
    return {k: _dem_cache.get(f'{k[0]},{k[1]}') for k in want}


def terrain_at(points):
    """For (lat, lon) points: z, slope deg, aspect deg (downhill azimuth)."""
    reqs = []
    for lat, lon in points:
        dlat = OFF_M / 111000.0
        dlon = OFF_M / (111000.0 * math.cos(math.radians(lat)))
        reqs += [(lon, lat), (lon + dlon, lat), (lon - dlon, lat),
                 (lon, lat + dlat), (lon, lat - dlat)]
    z = dem_elevations(reqs)
    out = []
    for i, (lat, lon) in enumerate(points):
        c, e, w, n, s = [z[(round(x, 6), round(y, 6))] for x, y in reqs[i * 5:(i + 1) * 5]]
        if None in (c, e, w, n, s):
            out.append(None)
            continue
        dzdx = (e - w) / (2 * OFF_M)
        dzdy = (n - s) / (2 * OFF_M)
        slope = math.degrees(math.atan(math.hypot(dzdx, dzdy)))
        aspect = (math.degrees(math.atan2(-dzdx, -dzdy)) + 360) % 360
        out.append({'z': c, 'slope': slope, 'aspect': aspect})
    return out


def rigid_fit(X, rows):
    G, R, tags = [], [], []
    for i, r in enumerate(rows):
        for dirn, l in (('ascending', L_ASC), ('descending', L_DESC)):
            v = r.get(dirn)
            if v is None:
                continue
            G.append(np.concatenate([np.cross(X[i], l), l]))
            R.append(v)
            tags.append((i, dirn))
    G, R = np.array(G), np.array(R)
    coef, res, rank, sv = np.linalg.lstsq(G, R, rcond=None)
    pred = G @ coef
    rms = float(np.sqrt(np.mean((R - pred) ** 2)))
    ss = 1 - float(((R - pred) ** 2).sum()) / max(float(((R - R.mean()) ** 2).sum()), 1e-9)
    return {'omega': coef[:3], 'b': coef[3:], 'obs': R, 'pred': pred,
            'tags': tags, 'rms': rms, 'r2': ss, 'rank': rank}


def az_plunge(v):
    h = math.hypot(v[0], v[1])
    az = (math.degrees(math.atan2(v[0], v[1])) + 360) % 360
    pl = math.degrees(math.atan2(-v[2], h))       # + = downward
    return az, pl


def stereo_xy(az, pl):
    """Lower-hemisphere equal-area; negative plunge -> antipode (flag upper)."""
    upper = pl < 0
    if upper:
        az, pl = (az + 180) % 360, -pl
    # Equal-area: r = sqrt(2) sin((90-pl)/2), which is exactly 1 at pl=0 —
    # a horizontal line plots ON the rim. (The first version divided by
    # sqrt(2) twice and compressed the whole net to r<=0.707; Hig's
    # expected-pole-on-perimeter framing exposed it.)
    r = math.sqrt(2) * math.sin(math.radians(90 - pl) / 2)
    return r * math.sin(math.radians(az)), r * math.cos(math.radians(az)), upper


def analyze(name, site, rows, X, domain_idx, suspect):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    dom_rows = [rows[i] for i in domain_idx]
    dom_X = X[domain_idx]
    fit = rigid_fit(dom_X, dom_rows)
    om, b = fit['omega'], fit['b']
    print(f'  rigid fit: rms {fit["rms"]:.1f} mm/yr  R2 {fit["r2"]:.2f}  '
          f'|omega| {np.linalg.norm(om)*1000:.2f} mrad/yr  rank {fit["rank"]}/6')

    # ---- Hig's geometric framing (2026-08-22) --------------------------
    # AVERAGE dropline from a plane fit over the domain's DEM samples (one
    # smooth surface, not a cloud of 30 m point gradients); expected
    # gravitational pole = HORIZONTAL axis ⊥ dropline -> sits on the
    # stereonet perimeter. Consistency = angle(fitted pole, expected pole)
    # + the SENSE of rotation about it (mass-lowering or not).
    A = np.c_[dom_X[:, 0], dom_X[:, 1], np.ones(len(dom_X))]
    plane_coef = np.linalg.lstsq(A, dom_X[:, 2], rcond=None)[0]
    grad = plane_coef[:2]
    slope_avg = math.degrees(math.atan(np.linalg.norm(grad)))
    d_h = -grad / max(np.linalg.norm(grad), 1e-12)          # horizontal downhill
    drop = np.array([d_h[0], d_h[1], -math.tan(math.radians(slope_avg))])
    drop /= np.linalg.norm(drop)                             # unit dropline
    pole = np.array([-d_h[1], d_h[0], 0.0])                  # horizontal ⊥ dropline
    # Mass-lowering sign convention: rotation about +pole must lower a point
    # UPSLOPE of the centroid. Test empirically; flip if needed.
    up_pt = dom_X.mean(axis=0) + 100 * np.array([-d_h[0], -d_h[1], math.tan(math.radians(slope_avg)) * 1])
    if np.cross(pole, up_pt - dom_X.mean(axis=0))[2] > 0:
        pole = -pole
    ang_pole = math.degrees(math.acos(np.clip(abs(np.dot(
        om / max(np.linalg.norm(om), 1e-12), pole)), 0, 1)))
    sense = float(np.dot(om, pole))
    print(f'  avg slope {slope_avg:.1f} deg, dropline az {(math.degrees(math.atan2(d_h[0], d_h[1]))+360)%360:.0f}')
    print(f'  fitted pole vs expected (horiz ⊥ dropline): {ang_pole:.0f} deg apart; '
          f'sense about expected pole: {"mass-LOWERING" if sense > 0 else "mass-RAISING"}')

    # ---- constrained fit: rotation forced about the expected pole ------
    # u = Omega (pole × x) + t_d dropline + t_z zhat   (3 params). The cost
    # of the constraint vs the free 6-param fit is the honest measure of
    # gravitational consistency — and if it is small, a steep FREE pole was
    # ill-conditioning, not physics.
    zhat = np.array([0, 0, 1.0])
    Gc, Rc = [], []
    for i, r in enumerate(dom_rows):
        for dirn, l in (('ascending', L_ASC), ('descending', L_DESC)):
            v = r.get(dirn)
            if v is None:
                continue
            Gc.append([float(l @ np.cross(pole, dom_X[i])),
                       float(l @ drop), float(l @ zhat)])
            Rc.append(v)
    Gc, Rc = np.array(Gc), np.array(Rc)
    cc, *_ = np.linalg.lstsq(Gc, Rc, rcond=None)
    predc = Gc @ cc
    rmsc = float(np.sqrt(np.mean((Rc - predc) ** 2)))
    nobs = len(Rc)
    bic_free = nobs * math.log(max(fit['rms'] ** 2, 1e-12)) + 6 * math.log(nobs)
    bic_con = nobs * math.log(max(rmsc ** 2, 1e-12)) + 3 * math.log(nobs)
    print(f'  constrained (horiz-axis) fit: rms {rmsc:.1f} vs free {fit["rms"]:.1f} mm/yr; '
          f'dBIC(con-free) {bic_con - bic_free:+.1f} '
          f'({"constraint acceptable — gravity family fits" if bic_con - bic_free < 6 else "constraint costly — free pole is doing real work"})')
    # SENSE is only claimable when Omega is resolved. Per Hig: in the pure-
    # translation limit the pole DIRECTION stays well-defined (⊥ motion) but
    # the sense becomes indeterminate — rotating one way about an axis
    # infinitely far above is the same field as the other way about one
    # infinitely far below. So test |Omega| against its standard error and
    # say "indeterminate (translation limit)" when it doesn't clear.
    dofc = max(nobs - 3, 1)
    covc = (rmsc ** 2 * nobs / dofc) * np.linalg.pinv(Gc.T @ Gc)
    se_om = math.sqrt(max(covc[0, 0], 1e-18))
    t_om = cc[0] / se_om
    if abs(t_om) < 2:
        sense_txt = f'indeterminate (|Omega|/se = {abs(t_om):.1f} < 2 — translation limit)'
    else:
        sense_txt = ('mass-LOWERING' if cc[0] > 0 else 'mass-RAISING') +                     f' (|Omega|/se = {abs(t_om):.1f})'
    print(f'  constrained Omega: {cc[0]*1000:+.3f} ± {se_om*1000:.3f} mrad/yr -> sense {sense_txt}; '
          f'translation (dropline, vertical): ({cc[1]:+.1f}, {cc[2]:+.1f}) mm/yr')
    fit.setdefault('geo', {})
    fit['geo_sense'] = sense_txt
    fit['geo'] = {'drop': drop, 'pole': pole, 'ang_pole': ang_pole,
                  'rms_con': rmsc, 'dbic': bic_con - bic_free, 'omega_con': cc[0]}

    U = np.cross(np.tile(om, (len(dom_X), 1)), dom_X) + b     # mm/yr, 3D
    uz = U[:, 2]
    ok = [j for j, i in enumerate(domain_idx) if i not in suspect]

    # RANK-DEFICIENCY HONESTY. Two LOS geometries leave a null space (N-S
    # family): the minimum-norm solution zeroes the unconstrained component,
    # but any physical conclusion drawn from the 3-D vectors must survive
    # motion along that null space. Measure how mean-uz changes per unit
    # null-space displacement, normalized to a plausible magnitude: the null
    # vector scaled so its largest per-point speed matches the domain's rms
    # observed rate. If the verdict flips inside that range, say so.
    G = []
    for i, r in enumerate(dom_rows):
        for dirn, l in (('ascending', L_ASC), ('descending', L_DESC)):
            if r.get(dirn) is not None:
                G.append(np.concatenate([np.cross(dom_X[i], l), l]))
    G = np.array(G)
    _, sv, Vt = np.linalg.svd(G)
    null_v = Vt[-1]
    U_null = np.cross(np.tile(null_v[:3], (len(dom_X), 1)), dom_X) + null_v[3:]
    scale = np.sqrt(np.mean(fit['obs'] ** 2)) / max(np.abs(U_null).max(), 1e-12)
    uz_shift = float(np.mean(U_null[ok][:, 2])) * scale
    mu = float(np.mean(uz[ok]))
    lo, hi = mu - abs(uz_shift), mu + abs(uz_shift)
    if hi < 0:
        verdict = 'mass-lowering — gravity-consistent (robust to null space)'
    elif lo > 0:
        verdict = 'NOT lowering — inspect (robust to null space)'
    else:
        verdict = 'INDETERMINATE — sign flips within the N-S null space'
    print(f'  mean vertical rate: {mu:+.1f} mm/yr, null-space range [{lo:+.1f}, {hi:+.1f}] -> {verdict}')
    fit['uz_mu'], fit['uz_lo'], fit['uz_hi'], fit['verdict'] = mu, lo, hi, verdict

    fig, axes = plt.subplots(1, 2, figsize=(15, 7.2))

    # ---- panel 1: obs vs predicted -------------------------------------
    ax = axes[0]
    for dirn, c, mk in (('ascending', C_ASC, 'o'), ('descending', C_DESC, 's')):
        m = [k for k, (i, d_) in enumerate(fit['tags']) if d_ == dirn]
        ax.scatter(fit['pred'][m], fit['obs'][m], c=c, marker=mk, s=48,
                   edgecolors='k', linewidths=0.4,
                   label=f'{dirn} (n={len(m)})')
    lim = max(10, np.abs(np.concatenate([fit['obs'], fit['pred']])).max() * 1.15)
    ax.plot([-lim, lim], [-lim, lim], 'k:', lw=1)
    ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim)
    ax.set_xlabel('best-fit rigid rotation: predicted LOS rate (mm/yr)')
    ax.set_ylabel('observed point rate (mm/yr)')
    ax.set_title(f'{name}: point motion vs best-fit rotation\n'
                 f'rms {fit["rms"]:.1f} mm/yr, R² {fit["r2"]:.2f}, n={len(fit["obs"])} obs')
    ax.legend()
    ax.set_aspect('equal')

    # ---- panel 2: stereonet ---------------------------------------------
    ax = axes[1]
    th = np.linspace(0, 2 * np.pi, 200)
    ax.plot(np.sin(th), np.cos(th), 'k-', lw=1)
    for rr in (0.33, 0.66):
        ax.plot(rr * np.sin(th), rr * np.cos(th), color='#ccc', lw=0.5, zorder=0)
    for az_ in range(0, 360, 90):
        x, y, _ = stereo_xy(az_, 0)
        ax.plot([0, x], [0, y], color='#eee', lw=0.5, zorder=0)
        ax.text(1.09 * math.sin(math.radians(az_)), 1.09 * math.cos(math.radians(az_)),
                'NESW'[az_ // 90], ha='center', va='center', fontsize=11)

    # local downslope directions (3DEP), clean points only
    for j, i in enumerate(domain_idx):
        t = rows[i].get('terrain')
        if not t:
            continue
        x, y, up = stereo_xy(t['aspect'], t['slope'])
        hollow = i in suspect
        ax.scatter(x, y, marker='v', s=42, c='none' if hollow else C_SLOPE,
                   edgecolors=C_SLOPE, linewidths=1.0, zorder=2)
    # fitted motion directions. Upward-plunging motion is NOT flagged as
    # suspect: at a rotator's toe, upward motion is real, well-measured, and
    # exactly what net-mass-lowering rotation produces (Hig). Open symbol =
    # upper hemisphere, that is all. Suspect-DEM (stale glacier surface)
    # is a different statement and gets its own marker.
    for j in range(len(dom_X)):
        az, pl = az_plunge(U[j])
        x, y, up = stereo_xy(az, pl)
        if domain_idx[j] in suspect:
            ax.scatter(x, y, marker='x', s=55, c='#8b9793', linewidths=1.6, zorder=3)
        else:
            ax.scatter(x, y, marker='o', s=60,
                       c='none' if up else C_ASC,
                       edgecolors=C_ASC, linewidths=1.4, zorder=3)
    # UNCERTAINTY, sampled honestly from the fit covariance: 400 draws of
    # omega -> faint pole cloud (it elongates toward the LOS-blind axis,
    # which is the point); expected-pole uncertainty from the plane-fit
    # gradient covariance -> faint arc on the rim.
    Gd, Rd = [], []
    for i, rr in enumerate(dom_rows):
        for dirn, l in (('ascending', L_ASC), ('descending', L_DESC)):
            if rr.get(dirn) is not None:
                Gd.append(np.concatenate([np.cross(dom_X[i], l), l]))
                Rd.append(rr[dirn])
    Gd = np.array(Gd)
    dof = max(len(Rd) - 6, 1)
    s2 = fit['rms'] ** 2 * len(Rd) / dof
    cov6 = s2 * np.linalg.pinv(Gd.T @ Gd)
    rngu = np.random.default_rng(11)
    try:
        draws = rngu.multivariate_normal(np.concatenate([om, b]), cov6, 400)[:, :3]
        for w in draws:
            nw = np.linalg.norm(w)
            if nw < 1e-12:
                continue
            azw, plw = az_plunge(w / nw)
            xw, yw, _ = stereo_xy(azw, plw)  # antipode fold handled inside
            ax.scatter(xw, yw, marker='.', s=5, c='#000000', alpha=0.10, zorder=3)
    except np.linalg.LinAlgError:
        pass
    # expected-pole arc from plane-fit gradient uncertainty
    resid_z = dom_X[:, 2] - A @ plane_coef if 'plane_coef' in dir() else None
    Az = np.c_[dom_X[:, 0], dom_X[:, 1], np.ones(len(dom_X))]
    pcoef = np.linalg.lstsq(Az, dom_X[:, 2], rcond=None)[0]
    rz = dom_X[:, 2] - Az @ pcoef
    s2z = float(rz @ rz) / max(len(rz) - 3, 1)
    covg = s2z * np.linalg.pinv(Az.T @ Az)[:2, :2]
    gd = rngu.multivariate_normal(pcoef[:2], covg, 400)
    for ga_, gb_ in gd:
        dhz = -np.array([ga_, gb_])
        nn = np.linalg.norm(dhz)
        if nn < 1e-12:
            continue
        dhz /= nn
        azp = (math.degrees(math.atan2(-dhz[1], dhz[0]))) % 360
        for azq in (azp, (azp + 180) % 360):
            xq, yq, _ = stereo_xy(azq, 0.0)
            ax.scatter(xq, yq, marker='.', s=5, c='#E69F00', alpha=0.12, zorder=4)

    # rotation axis (plot both trends)
    if np.linalg.norm(om) > 1e-9:
        a = om / np.linalg.norm(om)
        for v in (a, -a):
            az, pl = az_plunge(v)
            x, y, up = stereo_xy(az, pl)
            ax.scatter(x, y, marker='s', s=90, c='none' if up else C_AXIS,
                       edgecolors=C_AXIS, linewidths=1.6, zorder=4)
    # Hig's reference geometry: the average dropline and the EXPECTED pole
    geo = fit['geo']
    az, pl = az_plunge(geo['drop'])
    x, y, _ = stereo_xy(az, pl)
    ax.scatter(x, y, marker='v', s=170, c='#000000', edgecolors='k', zorder=5)
    for v in (geo['pole'], -geo['pole']):
        az, pl = az_plunge(v)
        x, y, _ = stereo_xy(az, max(pl, 0.0))
        ax.scatter(x, y, marker='*', s=260, c='#E69F00', edgecolors='k',
                   linewidths=0.8, zorder=5)
    ax.scatter([], [], marker='v', s=120, c='#000000', label='avg dropline (plane fit)')
    ax.scatter([], [], marker='*', s=160, c='#E69F00', edgecolors='k',
               label=f'expected pole ± arc; fitted pole {geo["ang_pole"]:.0f}° away')
    ax.scatter([], [], marker='o', s=60, c=C_ASC, edgecolors=C_ASC, label='motion, downward component')
    ax.scatter([], [], marker='o', s=60, c='none', edgecolors=C_ASC,
               label='motion upward — real; expected at a rotator\'s toe')
    if suspect:
        ax.scatter([], [], marker='x', s=55, c='#8b9793', label='suspect DEM (stale glacier surface)')
    ax.scatter([], [], marker='v', s=42, c=C_SLOPE, edgecolors=C_SLOPE, label='3DEP downslope')
    ax.scatter([], [], marker='s', s=90, c=C_AXIS, edgecolors=C_AXIS,
               label='rotation axis ± sample cloud')
    # Outside the net: the circle is data space (legend was covering it).
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.04), fontsize=8.5,
              framealpha=0.95, ncol=2)
    ax.set_title(f'{name}: gravitational consistency (equal-area, lower hemisphere)\n'
                 f'mean vertical rate {fit["uz_mu"]:+.1f} mm/yr '
                 f'[null-space range {fit["uz_lo"]:+.1f}, {fit["uz_hi"]:+.1f}]')
    ax.set_xlim(-1.15, 1.15); ax.set_ylim(-1.15, 1.15)
    ax.set_aspect('equal'); ax.axis('off')

    plt.tight_layout()
    out = OUT / f'rotation_{name}.png'
    plt.savefig(out, dpi=115, bbox_inches='tight')
    print(f'  wrote {out}')


def main():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'sc', ROOT / 'tools' / 'insar_site_characterization.py')
    sc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sc)

    sites = json.load(open(OUT / 'sites.json'))
    for name, site in sites.items():
        print(f'\n===== {name} =====')
        g = shape(site['geom'])
        lat0, lon0 = g.centroid.y, g.centroid.x
        inside, ring = sc.probe_points(site['geom'])
        rows = []
        for group, pts in (('in', inside), ('ring', ring)):
            for lat, lon in pts:
                row = {'group': group, 'lat': lat, 'lon': lon}
                any_ok = False
                for dirn in ('ascending', 'descending'):
                    fit = sc.rate_fit(sc.fetch_ts(lat, lon, dirn))
                    row[dirn] = fit['rate'] if fit else None
                    any_ok = any_ok or fit is not None
                if any_ok:
                    rows.append(row)
        terr = terrain_at([(r['lat'], r['lon']) for r in rows])
        for r, t in zip(rows, terr):
            r['terrain'] = t
        X = []
        for r in rows:
            e = (r['lon'] - lon0) * 111000 * math.cos(math.radians(lat0))
            n_ = (r['lat'] - lat0) * 111000
            z = (r['terrain'] or {}).get('z', 0.0) or 0.0
            X.append([e, n_, z])
        X = np.array(X)

        if name == 'matanuska':
            # boundary-agnostic growth with real z (same algorithm as the demo)
            import importlib.util as iu
            spec2 = iu.spec_from_file_location('dg', ROOT / 'tools' / 'insar_domain_growth.py')
            dg = iu.module_from_spec(spec2)
            spec2.loader.exec_module(dg)
            n = len(rows)
            best_seed, best_rms = None, np.inf
            for i in range(n):
                d2 = ((X - X[i]) ** 2).sum(axis=1)
                knn = list(np.argsort(d2)[:6])
                rms, _ = dg.rigid_rms(knn, X, rows)
                if rms < best_rms:
                    best_rms, best_seed = rms, knn
            dom = set(best_seed)
            ring_rates = [r[d] for r in rows if r['group'] == 'ring'
                          for d in ('ascending', 'descending') if r[d] is not None]
            tol = 2.5 * float(np.std(ring_rates))
            while True:
                cands = []
                for j in range(n):
                    if j in dom:
                        continue
                    dmin = min(np.linalg.norm(X[j][:2] - X[i][:2]) for i in dom)
                    if dmin > 320:
                        continue
                    rms, _ = dg.rigid_rms(list(dom) + [j], X, rows)
                    cands.append((rms, j))
                if not cands:
                    break
                cands.sort()
                rms, j = cands[0]
                if rms > tol:
                    break
                dom.add(j)
            domain_idx = sorted(dom)
            suspect = set()
            print(f'  domain (growth, real z): {len(domain_idx)} points')
        else:
            # Columbia: the fast core, hand-selected pending multi-domain
            domain_idx = [i for i, r in enumerate(rows)
                          if r['ascending'] is not None and r['ascending'] < -8]
            suspect = {i for i in domain_idx
                       if (rows[i]['terrain'] or {}).get('z', 1e9) is not None
                       and (rows[i]['terrain'] or {}).get('z', 1e9) < GLACIER_SURFACE_SUSPECT_M}
            print(f'  fast core: {len(domain_idx)} points (asc < -8 mm/yr); '
                  f'{len(suspect)} flagged as stale-glacier-surface DEM (z < {GLACIER_SURFACE_SUSPECT_M:.0f} m)')
        if len(domain_idx) >= 4:
            analyze(name, site, rows, X, domain_idx, suspect)
        else:
            print('  too few domain points to fit')


if __name__ == '__main__':
    main()
