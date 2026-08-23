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
    r = math.sqrt(2) * math.sin(math.radians(90 - pl) / 2) / math.sqrt(2)
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
    # fitted motion directions
    for j in range(len(dom_X)):
        az, pl = az_plunge(U[j])
        x, y, up = stereo_xy(az, pl)
        hollow = (domain_idx[j] in suspect) or up
        ax.scatter(x, y, marker='o', s=60,
                   c='none' if hollow else C_ASC,
                   edgecolors=C_ASC, linewidths=1.4, zorder=3)
    # rotation axis (plot both trends)
    if np.linalg.norm(om) > 1e-9:
        a = om / np.linalg.norm(om)
        for v in (a, -a):
            az, pl = az_plunge(v)
            x, y, up = stereo_xy(az, pl)
            ax.scatter(x, y, marker='s', s=90, c='none' if up else C_AXIS,
                       edgecolors=C_AXIS, linewidths=1.6, zorder=4)
    ax.scatter([], [], marker='o', s=60, c=C_ASC, edgecolors=C_ASC, label='motion (filled = downward)')
    ax.scatter([], [], marker='o', s=60, c='none', edgecolors=C_ASC, label='motion upward / suspect DEM')
    ax.scatter([], [], marker='v', s=42, c=C_SLOPE, edgecolors=C_SLOPE, label='3DEP downslope')
    ax.scatter([], [], marker='s', s=90, c=C_AXIS, edgecolors=C_AXIS, label='rotation axis')
    ax.legend(loc='lower left', fontsize=8.5, framealpha=0.9)
    ax.set_title(f'{name}: gravitational consistency (equal-area, lower hemisphere)\n'
                 f'mean vertical rate {fit["uz_mu"]:+.1f} mm/yr '
                 f'[null-space range {fit["uz_lo"]:+.1f}, {fit["uz_hi"]:+.1f}]')
    ax.set_xlim(-1.18, 1.18); ax.set_ylim(-1.18, 1.18)
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
