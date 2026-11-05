#!/opt/anaconda3/bin/python3
"""Is a steep surface evidence or stitching?  (2026-11-03, Hig: "pick out steep surfaces, then interrogate the stack of data to see whether
that steep surface reflected something for which there's evidence (closely-spaced elevations at substantially different elevations) or if
instead it is just a stitching artifact".)

    step_check.py RECIPE [--out-dir DIR] [--steep-deg 12] [--step-m 0.5] [--json OUT] [--png OUT]

Steep sites: cells whose slope over 4 m passes steep_deg and whose relief across +-10 m passes step_m, one per 10 m block.  At each, the slope
the DATA REQUIRE is the largest (|za - zb| - sa - sb) / |ab| over pairs of data on opposite sides of it, within 200 m (a radius tied to the nearest
datum missed the cast across the water from a shore site, whose pair with the rim is the very evidence).  The data are the observed cells, the DEM's land, and the casts, each with its own uncertainty:
a 0.8 m seam between ArcticDEM and 3DEP is inside their sigmas and requires nothing; a rock wall in one lidar or two casts 20 m apart at -5 and
-30 require what they show.  A site is UNSUPPORTED when the surface is steeper than twice what is required plus 3 degrees.  Its class says
where: modelled (the fill) or at a seam (observed cells beside modelled ones).
"""
import argparse, csv, json, sys
from pathlib import Path
import numpy as np, rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from scipy import ndimage
from scipy.spatial import cKDTree


def steep_sites(Z, res, steep_deg, step_m):
    a = np.nan_to_num(Z, nan=-9999.0)
    gy, gx = np.gradient(a, res); gy2 = (np.roll(a, -1, 0) - np.roll(a, 1, 0)) / (2 * res); gx2 = (np.roll(a, -1, 1) - np.roll(a, 1, 1)) / (2 * res)
    s = np.hypot(gx2, gy2); w = int(round(10 / res)) * 2 + 1
    rel = ndimage.maximum_filter(a, w) - ndimage.minimum_filter(a, w)
    m = np.isfinite(Z) & (s > np.tan(np.radians(steep_deg))) & (rel >= step_m) & (a > -9000); m[:w, :] = m[-w:, :] = False; m[:, :w] = m[:, -w:] = False
    return m, s


def data_points(Z, mask, T, res, prior, water, casts, sig_obs=0.3, sig_dem=1.0):
    """x, y, z, sigma of the stack: observed cells and DEM land on an 8 m lattice, the casts."""
    k = max(int(round(8 / res)), 1); out = []
    rr, cc = np.mgrid[0:Z.shape[0]:k, 0:Z.shape[1]:k]; rr = rr.ravel(); cc = cc.ravel()
    ob = (mask[rr, cc] == 0) & np.isfinite(Z[rr, cc])
    out.append(np.c_[T.c + (cc[ob] + .5) * res, T.f - (rr[ob] + .5) * res, Z[rr[ob], cc[ob]], np.full(ob.sum(), sig_obs)])
    if prior is not None:
        pv = np.isfinite(prior[rr, cc]) & ~ob & (water[rr, cc] == 0)
        out.append(np.c_[T.c + (cc[pv] + .5) * res, T.f - (rr[pv] + .5) * res, prior[rr[pv], cc[pv]], np.full(pv.sum(), sig_dem)])
    if len(casts):
        out.append(casts)
    return np.vstack(out)


def required(sites_xy, D, tree, n_cast):
    rq = np.zeros(len(sites_xy)); rng = np.random.default_rng(0); first_cast = len(D) - n_cast
    for i, p in enumerate(sites_xy):
        nb = np.asarray(tree.query_ball_point(p, 200.0))
        if nb.size < 2:
            continue
        if nb.size > 300:                                   # every cast; of the lattice the nearest (the close pairs) and a sample of the rest
            cs = nb[nb >= first_cast]; ot = nb[nb < first_cast]; d2 = ((D[ot, :2] - p) ** 2).sum(1); o = np.argsort(d2)
            nb = np.r_[cs[:150], ot[o[:150]], rng.choice(ot[o[150:]], min(150, max(ot.size - 150, 0)), replace=False)]
        v = D[nb, :2] - p; dot = v @ v.T; dxy = np.hypot(v[:, None, 0] - v[None, :, 0], v[:, None, 1] - v[None, :, 1])
        dz = np.abs(D[nb, 2][:, None] - D[nb, 2][None, :]) - D[nb, 3][:, None] - D[nb, 3][None, :]
        ok = (dot < 0) & (dxy > 0)
        if ok.any():
            rq[i] = max(0.0, float((dz[ok] / dxy[ok]).max()))
    return rq


def check(Z, mask, T, res, prior, water, casts, steep_deg=12.0, step_m=0.5, thin_m=10.0):
    D = data_points(Z, mask, T, res, prior, water, casts); tree = cKDTree(D[:, :2])
    m, s = steep_sites(Z, res, steep_deg, step_m)
    # the fill and its seams; a steep cell well inside observed ground is the ground's business
    # (nor the prior's own land, mask 8: 3DEP on its hills)
    mod = (mask > 0) & (mask != 8); seam = ndimage.binary_dilation(mod, iterations=2) & (mask == 0)
    m &= (mod | seam)
    rr, cc = np.nonzero(m); k = max(int(thin_m / res), 1)
    _, first = np.unique((rr // k) * 1_000_003 + cc // k, return_index=True); rr, cc = rr[first], cc[first]
    if not len(rr):
        return dict(sites=0, unsupported=0, by_class={}, km=0.0), np.zeros((0, 6))
    xy = np.c_[T.c + (cc + .5) * res, T.f - (rr + .5) * res]
    rq = required(xy, D, tree, len(casts)); sm = s[rr, cc]
    uns = sm > 2 * rq + np.tan(np.radians(3.0))
    names = {0: "seam", 1: "band", 2: "sea", 3: "sea", 5: "lake", 6: "channel", 7: "lens", 8: "ground prior", 9: "intertidal"}
    by = {}
    for v in np.unique(mask[rr, cc]):
        kk = mask[rr, cc] == v; nm = names.get(int(v), str(int(v)))
        e = by.setdefault(nm, dict(sites=0, unsupported=0)); e["sites"] += int(kk.sum()); e["unsupported"] += int((kk & uns).sum())
    # cell-level seam class: the sites beside modelled cells are reported under the observed code
    rep = dict(sites=int(len(rr)), unsupported=int(uns.sum()), km_unsupported=round(float(uns.sum()) * thin_m / 1e3, 2), by_class=by,
               model_deg_p50=round(float(np.degrees(np.arctan(np.median(sm[uns]))) if uns.any() else 0), 1))
    return rep, np.c_[xy, np.degrees(np.arctan(sm)), np.degrees(np.arctan(rq)), uns, mask[rr, cc]]


def load(recipe, out_dir=None):
    r = json.load(open(recipe)); cid = r["id"]
    od = Path(out_dir or f"/Volumes/Nunatak/lidar_build/composite/{cid}/out")
    with rasterio.open(od / f"{cid}.tif") as d:
        Z = d.read(1).astype(np.float32); T = d.transform; crs = d.crs; nd = d.nodata; W_, H_ = d.width, d.height; b = d.bounds
    Z[Z == nd] = np.nan
    with rasterio.open(od / "fill_mask.tif") as d:
        mask = d.read(1)
    F = r["fill"]; prior = None; water = np.zeros(Z.shape, np.uint8)
    if F.get("ground_prior"):
        with rasterio.open(F["ground_prior"]) as s, WarpedVRT(s, crs=crs, transform=T, width=W_, height=H_, resampling=Resampling.bilinear) as v:
            prior = v.read(1).astype(np.float32); prior[prior <= -9000] = np.nan
    if F.get("ground_water"):
        with rasterio.open(F["ground_water"]) as s:
            water = s.read(1)
    cs = []
    for path in F.get("points") or []:
        for row in csv.DictReader(open(path)):
            if row.get("bound") or row.get("z") in (None, ""):
                continue
            x, y = float(row["x"]), float(row["y"])
            if b.left < x < b.right and b.bottom < y < b.top and "crm" not in Path(path).name:
                cs.append((x, y, float(row["z"]), float(row.get("sigma") or 2.0)))
    return Z, mask, T, abs(T.a), prior, water, np.array(cs) if cs else np.zeros((0, 4))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("recipe"); ap.add_argument("--out-dir"); ap.add_argument("--steep-deg", type=float, default=12.0)
    ap.add_argument("--step-m", type=float, default=0.5); ap.add_argument("--json"); ap.add_argument("--csv")
    a = ap.parse_args()
    rep, rows = check(*load(a.recipe, a.out_dir), steep_deg=a.steep_deg, step_m=a.step_m)
    print(json.dumps(rep))
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
    if a.csv:
        np.savetxt(a.csv, rows, delimiter=",", header="x,y,model_deg,required_deg,unsupported,mask", comments="", fmt="%.2f")


if __name__ == "__main__":
    main()
