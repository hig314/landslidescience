#!/opt/anaconda3/bin/python3
"""water_taught.py -- WATER in a point cloud that has no water class, taught from surveys that do
(Hig, 2026-10-18: "detection from the point clouds, ideally relying not only on geometry but also return
intensity").  First use: Kenai 2008 (vendor classes 2 / 11-16, no class 9).

    water_taught.py train   --obs obs/kenai_2008 --out obs/kenai_2008/model [--boxes id,id,...]
    water_taught.py predict --obs obs/kenai_2008 --model obs/kenai_2008/model

FEATURES, per 2 m cell, from observe_points.py cellstats (all windows in metres, so the model is resolution-free):
  dens       n_all / the 50 m median of n_all (water returns little: specular off-nadir, absorbed at nadir)
  empty10    share of empty cells within 10 m
  inten      mean intensity of ground + unclassified returns, and inten_rel = inten / its 50 m median
             (near-IR off water is dark; uncalibrated across surveys, hence relative as well as absolute)
  rough10    std of the lowest surface over 10 m (water is level)
  range14    max - min of the lowest surface over 14 m
  lowrel     lowest surface minus its 100 m minimum (water sits at the local bottom)
  gfrac      ground share of the returns (the vendor called most lake-surface returns ground)
  flat       zmax_low - zmin_all (one surface vs a canopy / a step inside the cell)
LABELS (where they are certain, never where they are guessed):
  water  inside a mapped lake or river polygon (water/sources.json), 6 m in from its edge; and
         seabed below -5 m NAVD88 in OCM 2023 (water at any tide)
  land   KBay 2023 ground (obs/kbay_2023_full/class.vrt) with its DTM above 6 m NAVD88 (above every tide), or,
         where KBay did not fly, Homer 2019's DTM above 6 m; more than 20 m from mapped water
Trained on a sample of each box's labelled cells; whole boxes are held out to report precision and recall.
PREDICT writes <box>/class.tif (topobathy codes: 1 ground, 2 water, 0 no evidence) and <box>/pwater.tif
(probability), then class.vrt -- read by a recipe's `raster` class step ("map": {"2": "water"}).
"""
import argparse, json, pickle, sys, time
from pathlib import Path
import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.warp import transform_geom, reproject, Resampling
from scipy import ndimage

TL = Path(__file__).resolve().parents[1]
MAPPED = TL / "water" / "mapped_hig_lakes.geojson"
KBAY_CLASS = "/Volumes/Nunatak/lidar_build/obs/kbay_2023_full/class.vrt"
KBAY_DTM = "/Volumes/Nunatak/lidar_build/cog/kbay_2023_reclass_v2.tif"
OCM = "/Volumes/Nunatak/lidar_build/bathy_src/outer_kachemak/cmp_ocm/cand_2m.tif"
HOMER19 = "/Volumes/Nunatak/lidar_build/cog/homer_2019.tif"
CTX3DEP = "/Volumes/Nunatak/lidar_build/ctx_3dep/all.vrt"
FEATS = ["dens", "empty10", "inten", "inten_rel", "rough10", "range14", "lowrel", "gfrac", "flat"]


def _onto(path, ds, resampling=Resampling.nearest, dtype=np.float32, nodata=np.nan):
    """`path` read onto ds's grid (a WarpedVRT: reads only the window it needs), its nodata as NaN."""
    from rasterio.vrt import WarpedVRT
    with rasterio.open(path) as s, WarpedVRT(s, crs=ds.crs, transform=ds.transform, width=ds.width, height=ds.height,
                                             resampling=resampling) as v:
        a = v.read(1, masked=True)
    return a.astype(dtype).filled(nodata)


def _nanfilter(a, size, fn):
    v = np.isfinite(a)
    if fn == "mean":
        s = ndimage.uniform_filter(np.where(v, a, 0), size); c = ndimage.uniform_filter(v.astype(np.float32), size)
        return np.where(c > 0.2, s / np.maximum(c, 1e-6), np.nan)
    if fn == "std":
        m = _nanfilter(a, size, "mean"); m2 = _nanfilter(a * a, size, "mean")
        return np.sqrt(np.clip(m2 - m * m, 0, None))
    if fn == "max":
        return np.where(ndimage.uniform_filter(v.astype(np.float32), size) > 0.2, ndimage.maximum_filter(np.where(v, a, -1e9), size), np.nan)
    if fn == "min":
        return np.where(ndimage.uniform_filter(v.astype(np.float32), size) > 0.05, ndimage.minimum_filter(np.where(v, a, 1e9), size), np.nan)


def _median_coarse(a, res, win_m):
    """A cheap windowed median: block medians on a win_m/2 lattice, then smoothed back up."""
    f = max(int(round(win_m / 2 / res)), 1)
    H, W = a.shape; Hc, Wc = -(-H // f), -(-W // f)
    pad = np.full((Hc * f, Wc * f), np.nan, np.float32); pad[:H, :W] = a
    with np.errstate(all="ignore"):
        import warnings; warnings.simplefilter("ignore")
        med = np.nanmedian(pad.reshape(Hc, f, Wc, f).transpose(0, 2, 1, 3).reshape(Hc, Wc, f * f), axis=2)
    med = _nanfilter(med, 3, "mean")
    return np.repeat(np.repeat(med, f, 0), f, 1)[:H, :W]


def features(cs_path):
    with rasterio.open(cs_path) as ds:
        B = {ds.descriptions[i]: ds.read(i + 1).astype(np.float32) for i in range(ds.count)}
        res = ds.res[0]; prof = ds.profile
    n = B["n_all"]; za = np.where(B["zmin_all"] > -9000, B["zmin_all"], np.nan); zl = np.where(B["zmax_low"] > -9000, B["zmax_low"], np.nan)
    ng, nu = B["n_ground"], B["n_unclass"]
    inten = np.where(ng + nu > 0, (B["int_ground"] * ng + B["int_unclass"] * nu) / np.maximum(ng + nu, 1), np.nan)
    w = lambda m: max(int(round(m / res)) | 1, 3)
    nmed = _median_coarse(np.where(n > 0, n, np.nan), res, 50.0)
    F = {}
    F["dens"] = n / np.maximum(nmed, 0.5)
    F["empty10"] = ndimage.uniform_filter((n == 0).astype(np.float32), w(10))
    F["inten"] = _nanfilter(inten, w(6), "mean")
    F["inten_rel"] = F["inten"] / np.maximum(_median_coarse(inten, res, 50.0), 0.5)
    F["rough10"] = _nanfilter(za, w(10), "std")
    F["range14"] = _nanfilter(za, w(14), "max") - _nanfilter(za, w(14), "min")
    zs = _nanfilter(za, w(6), "mean")
    F["lowrel"] = zs - _nanfilter(zs, w(100), "min")
    F["gfrac"] = _nanfilter(np.where(n > 0, ng / np.maximum(n, 1), np.nan), w(6), "mean")
    F["flat"] = _nanfilter(zl - za, w(6), "mean")
    X = np.stack([F[k] for k in FEATS], -1).astype(np.float32)
    return X, prof, (n > 0) | (F["empty10"] < 1.0)


def labels(cs_path):
    with rasterio.open(cs_path) as ds:
        g = json.load(open(MAPPED)); shapes = []
        for f in g["features"]:
            if f["properties"].get("Type") in ("Lake", "River") and f.get("geometry"):
                shapes.append((transform_geom("EPSG:4326", f"EPSG:{ds.crs.to_epsg()}", f["geometry"]), 1))
        mw = rasterize(shapes, out_shape=(ds.height, ds.width), transform=ds.transform, fill=0).astype(bool) if shapes else np.zeros((ds.height, ds.width), bool)
        res = ds.res[0]
        water = ndimage.binary_erosion(mw, iterations=max(int(6 / res), 1))
        ocm = _onto(OCM, ds); water |= np.isfinite(ocm) & (ocm < -5.0)
        kc = _onto(KBAY_CLASS, ds, dtype=np.float32); kz = _onto(KBAY_DTM, ds, Resampling.bilinear)
        near_w = ndimage.binary_dilation(mw, iterations=max(int(20 / res), 1))
        # where KBay 2023 did not fly (Homer town), Homer 2019's DTM stands in: anything a DTM puts above 6 m is land
        hz = _onto(HOMER19, ds, Resampling.bilinear)
        # ...and where no lidar DTM reaches (the north row), 3DEP above 10 m (a warped read onto this box's grid only)
        tz = _onto(CTX3DEP, ds, Resampling.bilinear)
        land = (((kc == 1) & np.isfinite(kz) & (kz > 6.0)) | (~np.isfinite(kz) & np.isfinite(hz) & (hz > 6.0))
                | (~np.isfinite(kz) & ~np.isfinite(hz) & np.isfinite(tz) & (tz > 10.0))) & ~near_w & ~water
        lakew = water & mw
        # LOW LAND (2026-10-18: with land labels only above 6 m the model learned "low and flat = water" and called 19 %
        # of the Woz delta's dry ground water): surveyed ground 1.5-6 m, 50 m clear of mapped water, outside OCM's
        # underwater footprint -- the wet meadows and flood plains the rule must learn to leave as land
        far_w = ~ndimage.binary_dilation(mw, iterations=max(int(50 / res), 1))
        zz = np.where(np.isfinite(kz), kz, hz)
        low = (((kc == 1) & np.isfinite(kz)) | (~np.isfinite(kz) & np.isfinite(hz))) & (zz > 1.5) & (zz <= 6.0) & far_w & ~np.isfinite(ocm)
        land |= low
    y = np.full(water.shape, -1, np.int8); y[land] = 0; y[water] = 1
    kind = np.zeros(water.shape, np.int8); kind[lakew] = 1; kind[water & ~lakew] = 2; kind[low & ~water] = 3   # 1 mapped fresh, 2 deep sea, 3 low land
    return y, kind, np.where(np.isfinite(zz), zz, tz)


def cmd_train(a):
    """Sample each box's labelled cells (lakes, sea and land balanced), hold out a 4 km CHECKERBOARD (every other
    4 km square, all boxes) and report precision / recall for lake water and for sea separately, then refit on all."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    obs = Path(a.obs); boxes = sorted(p.parent.name for p in obs.glob("*/cellstats.tif"))
    if a.boxes: boxes = [b for b in boxes if b in a.boxes.split(",")]
    rng = np.random.default_rng(0); Xs, ys, ks, hs, zs = [], [], [], [], []
    for b in boxes:
        t0 = time.time(); cs = obs / b / "cellstats.tif"
        X, prof, has = features(cs); y, kind, zl = labels(cs)
        T = prof["transform"]; H, W = y.shape
        rr, cc = np.mgrid[:H, :W]
        xw = T.c + (cc + 0.5) * T.a; yw = T.f + (rr + 0.5) * T.e
        hold = ((np.floor(xw / 4000) + np.floor(yw / 4000)) % 2 == 1)
        sel = (y >= 0) & has & np.isfinite(X).all(-1)
        picks = []
        for cls_, kd, cap in ((1, 1, 40000), (1, 2, 20000), (0, 0, 50000), (0, 3, 40000)):
            ii = np.flatnonzero((sel & (y == cls_) & (kind == kd)).ravel())
            if ii.size: picks.append(rng.choice(ii, min(cap, ii.size), replace=False))
        if not picks:
            print(f"  {b}: no labels"); continue
        idx = np.concatenate(picks)
        Xs.append(X.reshape(-1, len(FEATS))[idx]); ys.append(y.ravel()[idx]); ks.append(kind.ravel()[idx]); hs.append(hold.ravel()[idx]); zs.append(zl.ravel()[idx])
        print(f"  {b}: lake {int(((y == 1) & (kind == 1) & sel).sum()):,}  sea {int(((y == 1) & (kind == 2) & sel).sum()):,}  land {int(((y == 0) & sel).sum()):,} labelled; sampled {idx.size:,} ({time.time() - t0:.0f}s)", flush=True)
    X = np.concatenate(Xs); y = np.concatenate(ys); k = np.concatenate(ks); h = np.concatenate(hs)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / "samples.npz", X=X, y=y, kind=k, hold=h, z=np.concatenate(zs), feats=np.array(FEATS))   # for diagnosis
    clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.1, max_leaf_nodes=31, random_state=0).fit(X[~h], y[~h])
    p = clf.predict(X[h]); t = y[h]; kk = k[h]; rep = dict(features=FEATS, hold="4 km checkerboard", scores={})
    for name, m in (("lake", (kk == 1) | (t == 0)), ("sea", (kk == 2) | (t == 0)), ("all", np.ones(t.shape, bool))):
        tp = int(((p == 1) & (t == 1) & m).sum()); fp = int(((p == 1) & (t == 0) & m).sum()); fn = int(((p == 0) & (t == 1) & m).sum())
        rep["scores"][name] = dict(precision=round(tp / max(tp + fp, 1), 3), recall=round(tp / max(tp + fn, 1), 3), water=int(((t == 1) & m).sum()))
        print(f"  held-out squares, {name:4s}: precision {rep['scores'][name]['precision']}, recall {rep['scores'][name]['recall']} (water cells {rep['scores'][name]['water']:,}, land {int((t == 0).sum()):,})")
    lf = int(((p == 1) & (t == 0)).sum()); print(f"  land called water: {lf:,} of {int((t == 0).sum()):,} ({lf / max(int((t == 0).sum()), 1) * 100:.2f} %)")
    ll = (t == 0) & (kk == 3); print(f"  LOW land (1.5-6 m) called water: {int((ll & (p == 1)).sum()):,} of {int(ll.sum()):,} ({int((ll & (p == 1)).sum()) / max(int(ll.sum()), 1) * 100:.2f} %)")
    clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.1, max_leaf_nodes=31, random_state=0).fit(X, y)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    pickle.dump(clf, open(out / "model.pkl", "wb")); (out / "report.json").write_text(json.dumps(rep, indent=1))
    print(f"model -> {out / 'model.pkl'}")


def cmd_predict(a):
    clf = pickle.load(open(a.model + "/model.pkl" if Path(a.model).is_dir() else a.model, "rb"))
    obs = Path(a.obs); outs = []
    for cs in sorted(obs.glob("*/cellstats.tif")):
        X, prof, has = features(cs)
        ok = np.isfinite(X).all(-1) & has
        p = np.full(ok.shape, np.nan, np.float32)
        if ok.any():
            p[ok] = clf.predict_proba(X[ok])[:, 1]
        cls = np.zeros(ok.shape, np.uint8); cls[ok & (p < 0.5)] = 1; cls[ok & (p >= float(a.threshold))] = 2
        # a water patch is an area: specks under min_m2 go back to ground
        lab, nl = ndimage.label(cls == 2)
        if nl:
            sz = np.bincount(lab.ravel()) * prof["transform"].a ** 2
            cls[(cls == 2) & np.isin(lab, np.flatnonzero(sz < float(a.min_m2)))] = 1
        pr = dict(prof, count=1, dtype="uint8", nodata=255, predictor=1)
        with rasterio.open(cs.parent / f"{a.name}.tif", "w", **pr) as d: d.write(cls, 1)
        with rasterio.open(cs.parent / f"p{a.name}.tif", "w", **dict(prof, count=1, dtype="float32", nodata=np.nan)) as d: d.write(p, 1)
        outs.append(str(cs.parent / f"{a.name}.tif"))
        print(f"  {cs.parent.name}: water {float((cls == 2).mean()) * 100:.1f} %, ground {float((cls == 1).mean()) * 100:.1f} %, no evidence {float((cls == 0).mean()) * 100:.1f} %", flush=True)
    import subprocess
    (obs / f"{a.name}_list.txt").write_text("\n".join(outs) + "\n")
    subprocess.run(["/opt/homebrew/bin/gdalbuildvrt", "-q", "-input_file_list", str(obs / f"{a.name}_list.txt"), str(obs / f"{a.name}.vrt")], check=True)
    print(f"-> {obs / (a.name + '.vrt')}")


def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    t = sp.add_parser("train"); t.add_argument("--obs", required=True); t.add_argument("--out", required=True); t.add_argument("--boxes")
    p = sp.add_parser("predict"); p.add_argument("--obs", required=True); p.add_argument("--model", required=True)
    p.add_argument("--threshold", default=0.5); p.add_argument("--min-m2", default=200.0)
    p.add_argument("--name", default="class", help="output name: <box>/<name>.tif, <box>/p<name>.tif, <obs>/<name>.vrt "
                   "(KBay / Grewingk already have a class.tif the recipes read: use another name there)")
    a = ap.parse_args()
    {"train": cmd_train, "predict": cmd_predict}[a.cmd](a)


if __name__ == "__main__":
    main()
