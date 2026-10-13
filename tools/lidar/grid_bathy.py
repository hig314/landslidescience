#!/opt/anaconda3/bin/python3
"""Observe stage: grid sparse soundings into a bathymetric observation,
using the above-water merge to shape the shoreline.

  grid_bathy.py --points P.gpkg --z-field Elevation --subaerial DEM.tif --level 29.29
                --edge edge.gpkg --out-dir obs/portage_bathy [--holdout 4] [--res 4]

Constraints fed to the spline, each with its own sigma:
  soundings        z from the points; sigma by type (sounder / historic estimate)
  waterline        z = level along the shoreline (domain boundary against land)
  slope extension  the land slope just above the waterline continued below it
                   for a short distance, so the shore does not go flat at the
                   first sounding 200 m out
Outputs: bathy.tif (1 m, only inside the domain, <= level), distance.tif
(m to the nearest real sounding), sigma.tif (from type + distance, calibrated
by --holdout), constraints.geojson (what was fed in), report.json.
"""
import argparse, json, subprocess, time
from pathlib import Path
import numpy as np
import rasterio
from rasterio import features
from rasterio.transform import Affine
from scipy import ndimage
from scipy.interpolate import RBFInterpolator

OGR = "/opt/homebrew/bin/ogr2ogr"


def read_geojson(path):
    out = subprocess.run([OGR, "-f", "GeoJSON", "/vsistdout/", str(path)], capture_output=True, text=True)
    return json.loads(out.stdout)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--points", required=True); ap.add_argument("--z-field", default="Elevation")
    ap.add_argument("--type-field", default="Type")
    ap.add_argument("--subaerial", required=True); ap.add_argument("--level", type=float, required=True)
    ap.add_argument("--edge", help="lake edge line/polygon (the domain); else water class + sounding buffer")
    ap.add_argument("--water-class", help="class_now.tif from the subaerial build (2 = water)")
    ap.add_argument("--winner", help="winner.tif from the subaerial build; with --bed-winner, cells of that layer below the level are bed observations")
    ap.add_argument("--bed-winner", type=int, default=-1, help="winner index of a topobathy layer whose sub-level cells are real bed (e.g. 2 = whittier)")
    ap.add_argument("--bed-step", type=float, default=12.0, help="thinning of those bed cells (m)")
    ap.add_argument("--outlier-k", type=float, default=3.0, help="soundings farther than k*MAD (min 8 m) from their neighbours get sigma x4")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--res", type=float, default=4.0, help="spline evaluation resolution (m)")
    ap.add_argument("--ext-m", type=float, default=60.0, help="slope extension distance below the waterline")
    ap.add_argument("--ext-step", type=float, default=15.0)
    ap.add_argument("--shore-step", type=float, default=8.0, help="waterline sampling (m)")
    ap.add_argument("--shore-sigma", type=float, default=1.0, help="sigma of a waterline point (m)")
    ap.add_argument("--ext-sigma", type=float, default=3.0, help="sigma of an extension point at the shore; grows 0.1 m per m")
    ap.add_argument("--max-slope", type=float, default=0.7, help="tan; steeper land (cliffs, ice fronts) is not extended")
    ap.add_argument("--holdout", type=int, default=0, help="hold out every Nth sounding and report error vs distance")
    ap.add_argument("--smoothing", type=float, default=0.0)
    ap.add_argument("--kernel", default="thin_plate_spline", help="RBF kernel: thin_plate_spline | linear | cubic")
    ap.add_argument("--neighbors", type=int, default=0, help="0 = global solve (no patch seams); else local RBF")
    ap.add_argument("--gp-length", type=float, default=300.0, help="gp: Matern length scale start (m); optimised")
    ap.add_argument("--tension", type=float, default=0.3, help="tension: 0 = minimum curvature (bows), 1 = harmonic (cones)")
    ap.add_argument("--method", default="tps", choices=["tps", "linear", "tps_nocon", "linear_nocon", "gp", "gp_nocon", "tension", "tension_nocon", "wtps", "wtps_nocon"],
                    help="tps = thin-plate spline; linear = TIN; _nocon = soundings only, no shoreline constraints")
    a = ap.parse_args()
    t0 = time.time()
    out_dir = Path(a.out_dir); out_dir.mkdir(parents=True, exist_ok=True)

    # ---- the grid: the subaerial DEM's
    with rasterio.open(a.subaerial) as ds:
        dem = ds.read(1).astype(np.float32); T = ds.transform; crs = ds.crs; H, W = dem.shape
        nod = ds.nodata
    if nod is not None: dem[dem == nod] = np.nan
    res = abs(T.a)
    to_rc = lambda x, y: (int((T.f - y) / res), int((x - T.c) / res))   # north-up 1 m grid

    # ---- soundings
    gj = read_geojson(a.points)
    pts = []
    for f in gj["features"]:
        x, y = f["geometry"]["coordinates"][:2]; p = f["properties"]
        z = p.get(a.z_field)
        if z is None: continue
        typ = str(p.get(a.type_field, "")).lower()
        if "depth" in typ: sig = 0.5            # 2024 sounder tracks (hig_depths, pat_depths)
        elif "mayo" in typ: sig = 3.0            # Mayo 1977 survey
        elif "estimate" in typ or "1972" in typ: sig = 5.0
        else: sig = 2.0
        r, c = to_rc(x, y)
        if 0 <= r < H and 0 <= c < W:
            pts.append((x, y, float(z), sig, typ))
    P = np.array([(x, y, z, s) for x, y, z, s, _ in pts], np.float64)
    print(f"soundings in grid: {len(P)} (types: {sorted(set(t for *_, t in pts))})")

    # ---- domain: the lake now
    if a.edge:
        eg = read_geojson(a.edge)
        geoms = []
        for f in eg["features"]:
            g = f["geometry"]
            if g["type"] == "LineString":
                coords = g["coordinates"] + [g["coordinates"][0]]
                g = {"type": "Polygon", "coordinates": [coords]}
            geoms.append(g)
        domain = features.rasterize(geoms, out_shape=(H, W), transform=T, fill=0, default_value=1, dtype="uint8").astype(bool)
        dom_src = "edge polygon"
    else:
        with rasterio.open(a.water_class) as ds: wc = ds.read(1)
        domain = wc == 2
        buf = np.zeros((H, W), bool)
        for x, y, *_ in P: r, c = to_rc(x, y); buf[r, c] = True
        domain |= ndimage.distance_transform_edt(~buf) * res <= 150
        dom_src = "water class + 150 m sounding buffer"
    domain = ndimage.binary_fill_holes(domain)
    if a.water_class:
        with rasterio.open(a.water_class) as ds: wc0 = ds.read(1) == 2
        lab, nlab = ndimage.label(wc0 | domain); big = lab[domain].ravel(); big = big[big > 0]
        if big.size: domain = lab == np.bincount(big).argmax()      # the lake: the connected water body containing the polygon
        domain = ndimage.binary_fill_holes(domain)
    print(f"domain: {dom_src}, {domain.sum() * res * res / 1e6:.2f} km²")

    # ---- what the merge already knows under water: topobathy bed cells are dense observations
    bed = np.zeros((0, 4))
    if a.winner and a.bed_winner >= 0:
        with rasterio.open(a.winner) as ds: wn = ds.read(1)
        cells = domain & (wn == a.bed_winner) & np.isfinite(dem) & (dem < a.level - 0.5)
        cells &= ndimage.binary_erosion(cells, iterations=8)          # stay clear of feathered seams
        st = max(int(a.bed_step / res), 1)
        rr_b, cc_b = np.nonzero(cells[::st, ::st]); rr_b, cc_b = rr_b * st, cc_b * st
        bed = np.array([(T.c + c * res, T.f - r * res, dem[r, c], 0.3) for r, c in zip(rr_b, cc_b)], np.float64)
        bed = bed if len(bed) else np.zeros((0, 4))
        print(f"topobathy bed observations from the merge (winner {a.bed_winner}): {len(bed)} at {a.bed_step} m")

    # ---- the waterline is where the merge crosses the level, not the polygon edge
    wet = np.isfinite(dem) & (dem <= a.level + 0.3)                  # water surface (now honest in the merge) or bed
    if a.water_class:
        with rasterio.open(a.water_class) as ds: wet |= ds.read(1) == 2
    wet |= domain
    dry = np.isfinite(dem) & (dem > a.level + 0.3)
    wet = ndimage.binary_fill_holes(wet)
    shoreline = dry & ndimage.binary_dilation(wet, iterations=1)      # first dry cell next to wet
    # the waterline constraint sits on the wet side of that cell
    print(f"wet area {wet.sum() * res * res / 1e6:.2f} km² (domain {domain.sum() * res * res / 1e6:.2f}); shoreline cells {shoreline.sum()}")
    d_out = ndimage.distance_transform_edt(domain) * res
    gy, gx = np.gradient(d_out.astype(np.float32))                    # inward normal
    rr, cc = np.nonzero(shoreline)
    step = max(int(a.shore_step / res), 1)
    keep = np.arange(len(rr)) % step == 0
    rr, cc = rr[keep], cc[keep]
    # where topobathy bed observations exist within 30 m, the waterline adds nothing: skip it
    near_bed = np.zeros((H, W), bool)
    if len(bed):
        for x, y, *_ in bed: near_bed[to_rc(x, y)] = True
        near_bed = ndimage.distance_transform_edt(~near_bed) * res <= 30
    shore, ext = [], []
    for r, c in zip(rr, cc):
        if near_bed[r, c]: continue
        ny, nx = gy[r, c], gx[r, c]; n = np.hypot(ny, nx)
        if n == 0: continue
        ny, nx = ny / n, nx / n
        x, y = T.c + c * res, T.f - r * res
        shore.append((x, y, a.level, a.shore_sigma))
        zs = []
        for d in (5, 10, 15, 20, 25):
            r2, c2 = int(round(r - ny * d / res)), int(round(c - nx * d / res))
            if 0 <= r2 < H and 0 <= c2 < W and dry[r2, c2]:
                zs.append((d, dem[r2, c2]))
        if len(zs) < 3: continue
        d_arr = np.array([d for d, _ in zs]); z_arr = np.array([z for _, z in zs])
        slope = np.polyfit(d_arr, z_arr, 1)[0]
        if not (0.02 <= slope <= a.max_slope): continue
        for d in np.arange(a.ext_step, a.ext_m + 1e-6, a.ext_step):
            r2, c2 = int(round(r + ny * d / res)), int(round(c + nx * d / res))
            if not (0 <= r2 < H and 0 <= c2 < W and wet[r2, c2]) or near_bed[r2, c2]: break
            ext.append((T.c + c2 * res, T.f - r2 * res, a.level - slope * d, a.ext_sigma + 0.1 * d))
    shore = np.array(shore, np.float64) if shore else np.zeros((0, 4)); ext = np.array(ext, np.float64) if ext else np.zeros((0, 4))
    print(f"waterline points {len(shore)} (from the merge's level crossing), slope-extension points {len(ext)}")

    # ---- soundings that disagree with their neighbours: inflate sigma, report them
    flagged = []
    if len(P) > 8:
        from scipy.spatial import cKDTree
        tree = cKDTree(P[:, :2])
        for i in range(len(P)):
            d_, j_ = tree.query(P[i, :2], k=7)
            j_ = [j for j, dd in zip(j_[1:], d_[1:]) if dd < 200]
            if len(j_) < 3: continue
            nb = P[j_, 2]; med = np.median(nb); mad = 1.4826 * np.median(np.abs(nb - med)) + 1e-6
            resid = P[i, 2] - med
            if abs(resid) > max(a.outlier_k * mad, 8.0) and abs(resid) > 0.15 * abs(med - a.level):
                P[i, 3] *= 4; flagged.append(dict(x=float(P[i, 0]), y=float(P[i, 1]), z=float(P[i, 2]), neighbours_median=float(med), resid=float(resid), sigma=float(P[i, 3])))
        print(f"soundings inconsistent with neighbours (sigma x4): {len(flagged)}")

    # ---- hold-out split
    P_fit, P_test = P, np.zeros((0, 4))
    if a.holdout > 1:
        idx = np.arange(len(P)); test = idx % a.holdout == 0
        P_fit, P_test = P[~test], P[test]

    def dedupe(allp, cell=1.0):
        """RBF needs distinct sites: average anything sharing a 1 m cell."""
        key = np.round(allp[:, :2] / cell).astype(np.int64)
        _, inv = np.unique(key, axis=0, return_inverse=True)
        n = inv.max() + 1
        out = np.zeros((n, 4)); cnt = np.bincount(inv, minlength=n).astype(float)
        for j in range(4): out[:, j] = np.bincount(inv, weights=allp[:, j], minlength=n) / cnt
        return out

    def sites(Pf):
        return dedupe(np.vstack([Pf, shore, ext, bed]) if not a.method.endswith("_nocon") else Pf)

    def coarse_grid():
        k = int(round(a.res / res)); Hc, Wc = H // k, W // k
        yy, xx = np.mgrid[0:Hc, 0:Wc]
        X = np.column_stack([T.c + (xx.ravel() * k + k / 2) * res, T.f - (yy.ravel() * k + k / 2) * res])
        return k, Hc, Wc, X, domain[:Hc * k:k, :Wc * k:k].ravel()

    def finish(zc, k, Hc, Wc):
        zf = ndimage.zoom(np.nan_to_num(zc.reshape(Hc, Wc), nan=a.level), k, order=1)
        full = np.full((H, W), np.nan, np.float32); h2, w2 = min(H, zf.shape[0]), min(W, zf.shape[1])
        full[:h2, :w2] = zf[:h2, :w2]; full[~domain] = np.nan
        return np.minimum(full, a.level - 0.1)

    def trend_fit(allp):
        """Bathymetric trend: depth as a monotone function of distance from the shore
        (a fjord basin is a U); fitted from the sites themselves, so it is data-driven."""
        rc = np.array([to_rc(x, y) for x, y in allp[:, :2]])
        ds = d_out[np.clip(rc[:, 0], 0, H - 1), np.clip(rc[:, 1], 0, W - 1)]
        order = np.argsort(ds); dsort, zsort = ds[order], allp[order, 2]
        # binned medians then a monotone (non-increasing z with distance) smooth
        bins = np.unique(np.quantile(dsort, np.linspace(0, 1, 12)))
        idx = np.clip(np.searchsorted(bins, dsort, side="right") - 1, 0, len(bins) - 2)
        cen = np.array([np.median(dsort[idx == i]) for i in range(len(bins) - 1) if (idx == i).any()])
        med = np.array([np.median(zsort[idx == i]) for i in range(len(bins) - 1) if (idx == i).any()])
        med = np.minimum.accumulate(med)                       # deeper with distance, never shallower
        return lambda d: np.interp(d, cen, med)

    def fit_and_grid(Pf, _ext_filter=True):
        """Two passes: the slope extension is one-sided (the bed is at least as deep as the
        continued land slope, never forced shallower).  Fit without it, keep only the
        extension points lying below that surface, refit."""
        nonlocal ext
        if _ext_filter and len(ext) and not a.method.endswith("_nocon"):
            ext_all = ext; ext = np.zeros((0, 4))
            z0 = fit_and_grid(Pf, _ext_filter=False)
            keep = []
            for x, y, zz, s in ext_all:
                r, c = to_rc(x, y)
                if 0 <= r < H and 0 <= c < W and np.isfinite(z0[r, c]) and zz < z0[r, c] - 0.5:
                    keep.append((x, y, zz, s))
            ext = np.array(keep, np.float64) if keep else np.zeros((0, 4))
            print(f"   one-sided extension: kept {len(ext)} of {len(ext_all)} points (where the unconstrained bed was shallower)")
            out = fit_and_grid(Pf, _ext_filter=False)
            ext = ext_all
            return out
        allp = sites(Pf)
        k, Hc, Wc, X, dom_c = coarse_grid()
        zc = np.full(Hc * Wc, np.nan, np.float32)
        if a.method.startswith("linear"):
            from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
            lin = LinearNDInterpolator(allp[:, :2], allp[:, 2]); near = NearestNDInterpolator(allp[:, :2], allp[:, 2])
            f = lambda Q: np.where(np.isfinite(lin(Q)), lin(Q), near(Q))
            zc[dom_c] = f(X[dom_c]).astype(np.float32)
        elif a.method.startswith("wtps"):
            # thin-plate smoothing spline with PER-SITE weights: [[K + lam*diag(sigma^2), P],[P^T, 0]]
            # a 1972 estimate (sigma 5) or a flagged sounding (sigma 2) pulls the surface far less than a
            # 2024 fix (sigma 0.5); lam scales the whole trade-off (--smoothing, default 1)
            SC = 1000.0                                          # coordinates in km: keeps the system conditioned
            xy = (allp[:, :2] - allp[:, :2].mean(axis=0)) / SC; zv = allp[:, 2]; sg = allp[:, 3]
            m = len(xy); lam = (a.smoothing if a.smoothing > 0 else 1.0) * 1e-3
            r2 = ((xy[:, None, :] - xy[None, :, :]) ** 2).sum(-1)
            K = np.where(r2 > 0, 0.5 * r2 * np.log(np.maximum(r2, 1e-12)), 0.0)
            Pm = np.column_stack([np.ones(m), xy])
            A = np.zeros((m + 3, m + 3)); A[:m, :m] = K + lam * np.diag(sg ** 2); A[:m, m:] = Pm; A[m:, :m] = Pm.T
            rhs = np.concatenate([zv, np.zeros(3)])
            sol = np.linalg.solve(A, rhs); w, cpoly = sol[:m], sol[m:]
            Q = (X[dom_c] - allp[:, :2].mean(axis=0)) / SC
            out = np.empty(len(Q), np.float32)
            for i in range(0, len(Q), 4000):
                q = Q[i:i + 4000]; rq = ((q[:, None, :] - xy[None, :, :]) ** 2).sum(-1)
                Kq = np.where(rq > 0, 0.5 * rq * np.log(np.maximum(rq, 1e-12)), 0.0)
                out[i:i + 4000] = Kq @ w + cpoly[0] + q @ cpoly[1:]
            zc[dom_c] = out
        elif a.method.startswith("tps"):
            rbf = RBFInterpolator(allp[:, :2], allp[:, 2], kernel=a.kernel, neighbors=(a.neighbors or None),
                                  smoothing=max(a.smoothing, 1e-3), degree=1)
            zc[dom_c] = rbf(X[dom_c]).astype(np.float32)
        elif a.method.startswith("gp"):
            # trend + Gaussian-process residual: Matern 5/2 (twice differentiable, no cones at data),
            # per-site nugget = sigma^2 so a 1972 estimate pulls less than a 2024 sounding; sigma out for free
            from sklearn.gaussian_process import GaussianProcessRegressor
            from sklearn.gaussian_process.kernels import Matern, ConstantKernel as C
            trend = trend_fit(allp)
            rc = np.array([to_rc(x, y) for x, y in allp[:, :2]])
            dsite = d_out[np.clip(rc[:, 0], 0, H - 1), np.clip(rc[:, 1], 0, W - 1)]
            resid = allp[:, 2] - trend(dsite)
            x0 = allp[:, :2].mean(axis=0)
            kern = C(np.var(resid), (1e-1, 1e4)) * Matern(length_scale=a.gp_length, length_scale_bounds=(30, 3000), nu=2.5)
            gp = GaussianProcessRegressor(kern, alpha=allp[:, 3] ** 2, normalize_y=False, n_restarts_optimizer=1, random_state=0)
            gp.fit(allp[:, :2] - x0, resid)
            print("   gp kernel:", gp.kernel_)
            Q = X[dom_c] - x0
            mu = np.empty(len(Q), np.float32); sd = np.empty(len(Q), np.float32)
            for i in range(0, len(Q), 20000):
                m_, s_ = gp.predict(Q[i:i + 20000], return_std=True); mu[i:i + 20000] = m_; sd[i:i + 20000] = s_
            dq = d_out[:Hc * k:k, :Wc * k:k].ravel()[dom_c]
            zc[dom_c] = (mu + trend(dq)).astype(np.float32)
            sdc = np.full(Hc * Wc, np.nan, np.float32); sdc[dom_c] = sd
            fit_and_grid.gp_sigma = ndimage.zoom(np.nan_to_num(sdc.reshape(Hc, Wc), nan=0), k, order=1)
        elif a.method.startswith("tension"):
            # Smith & Wessel (1990) continuous-curvature spline in tension, solved directly on the
            # coarse grid: (1 - t) * biharmonic + t * laplacian = 0 away from data, data as soft constraints
            import scipy.sparse as sp
            from scipy.sparse.linalg import spsolve
            n = Hc * Wc; I = lambda r, c: r * Wc + c
            t = float(a.tension)
            rr_, cc_ = np.mgrid[0:Hc, 0:Wc]; ids = (rr_ * Wc + cc_)
            def lap():
                rows, cols, vals = [], [], []
                for dr, dc, w in ((0, 0, -4), (1, 0, 1), (-1, 0, 1), (0, 1, 1), (0, -1, 1)):
                    r2, c2 = rr_ + dr, cc_ + dc; ok = (r2 >= 0) & (r2 < Hc) & (c2 >= 0) & (c2 < Wc)
                    rows.append(ids[ok]); cols.append((r2 * Wc + c2)[ok]); vals.append(np.full(ok.sum(), w, float))
                return sp.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n))
            L = lap(); A = (1 - t) * (L @ L) - t * L
            # data rows: heavy weight at the site cells (inverse variance), pulling z to the observation
            rc = np.array([to_rc(x, y) for x, y in allp[:, :2]]) // k
            rc[:, 0] = np.clip(rc[:, 0], 0, Hc - 1); rc[:, 1] = np.clip(rc[:, 1], 0, Wc - 1)
            sid = rc[:, 0] * Wc + rc[:, 1]
            wgt = 1.0 / np.maximum(allp[:, 3], 0.3) ** 2
            Wd = sp.csr_matrix((wgt, (sid, sid)), shape=(n, n)); b = np.zeros(n); np.add.at(b, sid, wgt * allp[:, 2])
            # outside the domain, tie to the level (weak) so the system is well posed
            outside = ~dom_c; w_out = 1e-3
            Wo = sp.diags(np.where(outside, w_out, 0.0)); b += np.where(outside, w_out * a.level, 0.0)
            M = (A.T @ A) * 1e-2 + Wd + Wo
            zc[:] = spsolve(M.tocsc(), b).astype(np.float32)
        return finish(zc, k, Hc, Wc)

    def dist_to(Pf):
        m = np.zeros((H, W), bool)
        for x, y, *_ in Pf: r, c = to_rc(x, y); m[r, c] = True
        return (ndimage.distance_transform_edt(~m) * res).astype(np.float32)

    report = {"flagged_soundings": flagged, "bed_obs": int(len(bed)), "method": a.method, "kernel": a.kernel, "neighbors": a.neighbors, "shore_step": a.shore_step, "ext_step": a.ext_step, "soundings": int(len(P)), "waterline_pts": int(len(shore)), "extension_pts": int(len(ext)),
              "domain_km2": float(domain.sum() * res * res / 1e6), "domain": dom_src, "level": a.level}
    if a.holdout > 1:
        z_h = fit_and_grid(P_fit); d_h = dist_to(P_fit)
        err, dist = [], []
        for x, y, z, s in P_test:
            r, c = to_rc(x, y)
            if np.isfinite(z_h[r, c]): err.append(z_h[r, c] - z); dist.append(d_h[r, c])
        err, dist = np.array(err), np.array(dist)
        sig_t = np.array([s for x, y, z, s in P_test if np.isfinite(z_h[to_rc(x, y)])])
        good = sig_t <= 0.5
        report["holdout_sounder_only"] = dict(n=int(good.sum()), rmse=float(np.sqrt(np.mean(err[good] ** 2))) if good.any() else None,
                                              by_distance=[dict(d_lo=lo, d_hi=hi, n=int(((dist >= lo) & (dist < hi) & good).sum()),
                                                                 rmse=float(np.sqrt(np.mean(err[(dist >= lo) & (dist < hi) & good] ** 2))))
                                                            for lo, hi in ((0, 50), (50, 100), (100, 200), (200, 1e9)) if ((dist >= lo) & (dist < hi) & good).sum() >= 3])
        print("hold-out, 2024 sounder points only:", report["holdout_sounder_only"])
        bins = [0, 50, 100, 200, 400, 800, 1e9]; rows = []
        for lo, hi in zip(bins[:-1], bins[1:]):
            m = (dist >= lo) & (dist < hi)
            if m.sum() >= 3:
                rows.append(dict(d_lo=lo, d_hi=hi, n=int(m.sum()), rmse=float(np.sqrt(np.mean(err[m] ** 2))),
                                 mae=float(np.mean(np.abs(err[m]))), bias=float(np.mean(err[m]))))
        report["holdout"] = dict(every=a.holdout, n_test=int(len(err)), rmse=float(np.sqrt(np.mean(err ** 2))), by_distance=rows)
        print("hold-out (every %d):" % a.holdout, f"n={len(err)} rmse={np.sqrt(np.mean(err**2)):.2f} m")
        worst = np.argsort(-np.abs(err))[:6]
        tested = [(x, y, z, s) for x, y, z, s in P_test if np.isfinite(z_h[to_rc(x, y)])]
        from scipy.spatial import cKDTree
        tsh = cKDTree(shore[:, :2]) if len(shore) else None; tex = cKDTree(ext[:, :2]) if len(ext) else None
        for i in worst:
            x, y, z, s = tested[i]; r, c = to_rc(x, y)
            nsh = len(tsh.query_ball_point((x, y), 30)) if tsh else 0; nex = len(tex.query_ball_point((x, y), 30)) if tex else 0
            print(f"   worst: ({x:.0f},{y:.0f}) z={z:.1f} sigma={s} pred={z_h[r, c]:.1f} err={err[i]:+.1f} dist_to_data={dist[i]:.0f} m  dem_here={dem[r, c]:.1f} shore_pts<30m={nsh} ext_pts<30m={nex} in_domain={domain[r, c]}")
        for r_ in rows: print(f"   d {r_['d_lo']:>5.0f}-{r_['d_hi']:<6.0f} n {r_['n']:3d}  rmse {r_['rmse']:6.2f}  bias {r_['bias']:+6.2f}")

    z = fit_and_grid(P); dist = dist_to(P)
    # sigma: sounding sigma near data, growing with distance (slope from hold-out if available, else 0.02/m)
    k_d = 0.02
    src_rows = report.get("holdout_sounder_only", {}).get("by_distance") or report.get("holdout", {}).get("by_distance")
    if src_rows and len(src_rows) >= 2:
        rows = src_rows
        dd = np.array([(r_["d_lo"] + min(r_["d_hi"], 1600)) / 2 for r_ in rows]); ee = np.array([r_["rmse"] for r_ in rows])
        k_d = float(max(np.polyfit(dd, ee, 1)[0], 0.002))
    sigma = np.sqrt(0.5 ** 2 + (k_d * dist) ** 2).astype(np.float32)
    if hasattr(fit_and_grid, "gp_sigma"):                     # the posterior std is the honest sigma
        gs = fit_and_grid.gp_sigma; h2, w2 = min(H, gs.shape[0]), min(W, gs.shape[1])
        sigma[:h2, :w2] = np.sqrt(0.5 ** 2 + gs[:h2, :w2] ** 2); report["sigma_source"] = "gp posterior std"
    sigma[~domain] = np.nan
    report["sigma_per_m_of_distance"] = k_d

    prof = dict(driver="GTiff", width=W, height=H, count=1, crs=crs, transform=T, tiled=True, compress="ZSTD", BIGTIFF="IF_SAFER")
    for name, arr, nod_ in (("bathy.tif", z, -9999.0), ("distance.tif", dist, None), ("sigma.tif", sigma, -9999.0)):
        with rasterio.open(out_dir / name, "w", dtype="float32", nodata=nod_, **prof) as ds:
            ds.write(np.where(np.isfinite(arr), arr, nod_ if nod_ is not None else 0).astype(np.float32), 1)
    with rasterio.open(out_dir / "domain.tif", "w", dtype="uint8", nodata=None, **prof) as ds: ds.write(domain.astype(np.uint8), 1)
    feats = [dict(type="Feature", geometry=dict(type="Point", coordinates=[float(x), float(y)]), properties=dict(z=float(zz), sigma=float(s), kind=kind))
             for arr, kind in ((P, "sounding"), (shore, "waterline"), (ext, "extension")) for x, y, zz, s in arr]
    json.dump(dict(type="FeatureCollection", features=feats), open(out_dir / "constraints.geojson", "w"))
    report["seconds"] = round(time.time() - t0, 1)
    json.dump(report, open(out_dir / "report.json", "w"), indent=1)
    print(f"wrote {out_dir}  ({report['seconds']} s)")


if __name__ == "__main__":
    main()
