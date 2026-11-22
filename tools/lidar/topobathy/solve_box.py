#!/usr/bin/env python3
"""solve_box.py -- the single solve (solve.py) over a whole box, tile by tile (2026-11-15; prototype, not in the chain).

    /opt/anaconda3/bin/python3 topobathy/solve_box.py <box> [--tag cm18] [--out-tag s1] [--core 2000] [--halo 400]

Reads the box's finished outputs ($W/<box>/out_<tag>/) and writes $W/<box>/out_<out-tag>/<box>.tif: the product with its water margin
re-solved.  Each tile is solved with a halo and the tiles are FEATHERED across the halo (weight 1 in the core, falling to 0 at the
window's edge), so no tile edge shows.  Only cells the solve treats as free or weighted are replaced; land above the tide, the ice,
confident measurements and the deep modelled sea are the product's own.  Also <box>_kind.tif (what each cell was) and solve.log.

--deep-grid M (2026-11-16, Hig's review of s1: casts unmet in deep water, 59.15235,-151.14776; the reef at 59.13227,-152.04271 "picked up
in the soundings and our model, but our model doesn't trust the soundings enough"): the modelled sea deeper than scope_deep_m was cm18's
spline, which the 2 m tiles leave alone (solving it at 2 m would be millions of cells a tile).  First the WHOLE BOX is solved at M metres
with the casts as data (solve.py snd_mode, its scope over the deep sea), in its own process; that answer, upsampled bilinear, replaces the
product's modelled cells below scope_deep_m outside lakes (<box>_deep.tif, kind 6); then the 2 m tiles solve the margin against it with
the deep sea held (snd_scope=0), so the two meet without a step."""
import argparse, json, os, sys, time
from pathlib import Path
import numpy as np, rasterio
from rasterio.windows import Window
sys.path.insert(0, str(Path(__file__).resolve().parent))
import solve as S


def _tile(job):
    r, c, argv = job; t1 = time.time()
    try:
        R = S.main(argv, ret=True)
    except Exception as e:
        return r, c, f"FAILED {type(e).__name__}: {str(e)[:160]}", time.time() - t1
    return r, c, R, time.time() - t1


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("box"); ap.add_argument("--tag", default="cm18"); ap.add_argument("--out-tag", default="s1"); ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--core", type=float, default=2000.0); ap.add_argument("--halo", type=float, default=400.0); ap.add_argument("--set", nargs="*", default=[])
    ap.add_argument("--snd-lakes", action="store_true", help="tiles holding a sounded lake are solved with the casts as data (snd_mode=1); the rest as --set says")
    ap.add_argument("--lake-max", type=float, default=6500.0, help="a sounded lake wider than this with its halo (m) is left to the tiles")
    ap.add_argument("--deep-grid", type=float, default=0.0, help="solve the deep sea over the whole box at this resolution first (m); 0 = off")
    a = ap.parse_args(); d = S.W / a.box / f"out_{a.tag}"; o = S.W / a.box / f"out_{a.out_tag}"; o.mkdir(parents=True, exist_ok=True)
    lg = open(o / "solve.log", "w")
    def log(s): print(s, flush=True); lg.write(s + "\n"); lg.flush()
    with rasterio.open(d / f"{a.box}.tif") as s:
        cur = s.read(1); prof = s.profile; T = s.transform; res = s.res[0]; H, Wd = cur.shape; nd = s.nodata
    curfile = None; deepm = None
    if a.deep_grid > 0:
        from concurrent.futures import ProcessPoolExecutor as PPE
        b_ = rasterio.open(d / f"{a.box}.tif").bounds; t1 = time.time()
        argv_ = [a.box, "deep", str(b_.left), str(b_.bottom), str(b_.right), str(b_.top), "--tag", a.tag, "--nofig", "--nowrite", "--quiet",
                 "--set", *[x for x in a.set if not x.startswith(("snd_scope=", "grid_m=", "snd_mode="))], "snd_mode=1", "snd_scope=1", f"grid_m={a.deep_grid:g}"]
        with PPE(1) as ex1: Rd = ex1.submit(S.main, argv_, True).result()
        if Rd is None: raise SystemExit("deep pass: nothing to solve")
        zc = Rd["z"].astype(np.float64); hc, wc = zc.shape
        from scipy import ndimage as nd_
        rr_ = (np.arange(H) + 0.5) * hc / H - 0.5; cc_ = (np.arange(Wd) + 0.5) * wc / Wd - 0.5
        zfine = nd_.map_coordinates(np.nan_to_num(zc, nan=0.0), np.meshgrid(rr_, cc_, indexing="ij"), order=1, mode="nearest").astype(np.float32)
        okc = nd_.map_coordinates(np.isfinite(zc).astype(np.float32), np.meshgrid(rr_, cc_, indexing="ij"), order=1, mode="nearest") > 0.99
        si_ = rasterio.open(d / "source_index.tif").read(1); lv_ = rasterio.open(d / "fill_water_level.tif").read(1)
        if lv_.shape != cur.shape: lv_ = np.kron(lv_, np.ones((cur.shape[0] // lv_.shape[0],) * 2, lv_.dtype))[:H, :Wd]
        lake_ = (lv_ > -9000) & (lv_ > S.P["tide_max_m"] + 1.0)
        deepm = (si_ == 3) & (cur != nd) & (cur < S.P["scope_deep_m"]) & ~lake_ & okc
        # ...BUT NOT IN THE GAPS OF A MEASURED BED (2026-11-20, Halibut Cove Lagoon): every cell the 16 m pass changed by > 3 m there lay within
        # 8 m of a multibeam stripe -- a coarse answer laid into 10-25 m gaps between 2 m measurements, bumps and pits along every stripe, and
        # the 2 m tiles then bent the stripes to them.  A deep modelled cell within two coarse cells of a layer's cell keeps the product; the
        # tiles bridge such a gap from its two sides.
        n0_ = int(deepm.sum()); deepm &= nd_.distance_transform_edt((si_ == 3) | (cur == nd)).astype(np.float32) * res > 2 * a.deep_grid; n_gap_ = n0_ - int(deepm.sum())
        del si_, lv_, lake_
        cur = np.where(deepm, zfine, cur).astype(np.float32); del zfine
        dp = dict(prof); dp.update(dtype="float32", compress="ZSTD", tiled=True, BIGTIFF="IF_SAFER"); curfile = o / f"{a.box}_deep.tif"
        with rasterio.open(str(curfile) + ".part", "w", **dp) as ds: ds.write(cur, 1)
        os.replace(str(curfile) + ".part", curfile)
        log(f"[{a.box}] deep pass at {a.deep_grid:g} m: {hc}x{wc} cells, {time.time() - t1:.0f} s; {int(deepm.sum())} deep modelled cells ({deepm.mean():.1%} of the box) take it ({n_gap_} more, within {2 * a.deep_grid:g} m of a layer's cell, keep the product)" +
            "".join("\n      " + l.strip() for l in Rd["log"] if any(q in l for q in ("fit - cast", "sparse soundings used", "closed basins", "solve:"))))
    zown = np.full(cur.shape, np.nan, np.float32)
    num = np.zeros(cur.shape, np.float32); den = np.zeros(cur.shape, np.float32); kind = np.full(cur.shape, 255, np.uint8); typ = np.zeros(cur.shape, np.uint8)
    nc = int(round(a.core / res)); nh = int(round(a.halo / res)); t0 = time.time(); n_t = n_s = 0; free = 0
    # the lakes that hold soundings, on the whole box
    from scipy import ndimage
    lab = rasterio.open(d / "shore_label.tif").read(1); lev = rasterio.open(d / "fill_water_level.tif").read(1)
    wet = np.isin(lab, (2, 3)) & (lev > -9000) & (lev > S.P["tide_max_m"] + 1.0); del lab, lev
    L, nl = ndimage.label(wet, structure=np.ones((3, 3), bool)); hit = np.zeros(nl + 1, bool); R_ = json.load(open(d / "recipe.json"))
    for path in R_["fill"]["points"]:
        for row in S._rows(path):
            cc, rr = ~T * (row["x"], row["y"]); rr, cc = int(rr), int(cc)
            if 0 <= rr < H and 0 <= cc < Wd: hit[L[rr, cc]] = True
    hit[0] = False; lk = hit[L].astype(np.uint8); del L
    lkm = lk > 0                                                                           # (--snd-lakes: which tiles hold a sounded lake)
    lp = dict(prof); lp.update(dtype="uint8", nodata=255, compress="ZSTD", tiled=True); lakes = o / f"{a.box}_lakes_solved.tif"
    with rasterio.open(lakes, "w", **lp) as ds: ds.write(lk, 1)
    log(f"  still water above the tide: {wet.mean():.1%} of the box in {nl} pieces; {int(hit.sum())} hold soundings ({lk.mean():.1%} of the box) and are solved")
    del wet, lk
    if a.snd_lakes: log(f"  --snd-lakes: tiles with a sounded lake are solved with the casts as data")
    tiles = [(r, c) for r in range(0, H, nc) for c in range(0, Wd, nc)]
    log(f"[{a.box}] {a.tag} -> {a.out_tag}: {H}x{Wd} cells, {len(tiles)} tiles of {a.core:g} m + {a.halo:g} m halo")
    jobs = []
    for r, c in tiles:
        r0, r1, c0, c1 = max(r - nh, 0), min(r + nc + nh, H), max(c - nh, 0), min(c + nc + nh, Wd)
        x0, y1 = T * (c0, r0); x1, y0 = T * (c1, r1)
        st_ = list(a.set) + (["snd_scope=0"] if a.deep_grid > 0 else [])
        # SOUNDINGS AS DATA WHERE A LAKE HOLDS THEM (2026-11-16): at 2 m the snd mode costs ~15 min a 2.4 km window, too slow for a whole box;
        # the open sea's casts are deep and taken by the deep pass, so the fine mode is spent only on tiles with a sounded lake (Gore, Grewingk)
        if a.snd_lakes and lkm[r0:r1, c0:c1].any():
            st_ = [x for x in st_ if not x.startswith("snd_mode=")] + ["snd_mode=1"]
        jobs.append((r, c, [a.box, f"tile_{r}_{c}", str(x0), str(y0), str(x1), str(y1), "--tag", a.tag, "--lakes", str(lakes), "--nofig", "--nowrite", "--quiet"] + (["--cur", str(curfile)] if curfile else []) + (["--set"] + st_ if st_ else [])))
    from concurrent.futures import ProcessPoolExecutor
    ex = ProcessPoolExecutor(a.workers)
    for r, c, R, dt in ex.map(_tile, jobs):
        r0, r1, c0, c1 = max(r - nh, 0), min(r + nc + nh, H), max(c - nh, 0), min(c + nc + nh, Wd); n_t += 1
        if isinstance(R, str): log(f"  tile r{r} c{c}: {R} -- the product stands there"); continue
        if R is None: continue
        z, k = R["z"], R["kind"]
        if z.shape != (r1 - r0, c1 - c0): log(f"  tile r{r} c{c}: shape {z.shape} != {(r1 - r0, c1 - c0)} -- skipped"); continue
        wy = np.ones(r1 - r0, np.float32); wx = np.ones(c1 - c0, np.float32)                     # feather: 0 at an inner window edge, 1 after the halo; the box's own edge is not feathered
        if r0 > 0: wy = np.minimum(wy, (np.arange(r1 - r0) + 0.5) / (2 * nh))
        if r1 < H: wy = np.minimum(wy, (np.arange(r1 - r0)[::-1] + 0.5) / (2 * nh))
        if c0 > 0: wx = np.minimum(wx, (np.arange(c1 - c0) + 0.5) / (2 * nh))
        if c1 < Wd: wx = np.minimum(wx, (np.arange(c1 - c0)[::-1] + 0.5) / (2 * nh))
        wt = np.clip(np.minimum(wy[:, None], wx[None, :]), 0.0, 1.0); m = k >= 2
        num[r0:r1, c0:c1][m] += (wt * z)[m]; den[r0:r1, c0:c1][m] += wt[m]
        core = np.zeros(k.shape, bool); core[r - r0:r - r0 + nc, c - c0:c - c0 + nc] = True
        kind[r0:r1, c0:c1][core] = k[core]; typ[r0:r1, c0:c1][core] = R["typing"][core]; zown[r0:r1, c0:c1][core] = z[core]
        n_s += 1; free += R["free"]
        log(f"  tile r{r} c{c}: {R['free']} free cells, {dt:.0f} s" + "".join("\n      " + l.strip() for l in R["log"] if any(q in l for q in ("guard:", "lone deep", "still water", "low-tide water:")) and not l.strip().endswith("dropped: 0")))
    # A SOUNDED LAKE IS SOLVED WHOLE (Hig 2026-11-21, Petrof Lake 59.38363,-150.82546): the lake lies across two tiles; one saw 21 of its 26
    # casts, the other 8, each fitted its own depth profile to its own share, and the bed at Hig's point stood at -51.6 m in the box against
    # -40.9 in one window over the whole lake.  Every piece of sounded still water that does not lie inside one tile's core is solved again
    # in ONE window (its extent + the halo, up to --lake-max m a side) with all its casts, and that answer is the lake's: whole inside the
    # water, blended into the tiles' over 60 m outside it.  A larger lake stays with the tiles (said in the log).
    if a.snd_lakes and lkm.any():
        Lk, nlk = ndimage.label(lkm, structure=np.ones((3, 3), bool)); ljobs = []
        for i_, sl_ in enumerate(ndimage.find_objects(Lk), 1):
            if sl_[0].start // nc == (sl_[0].stop - 1) // nc and sl_[1].start // nc == (sl_[1].stop - 1) // nc: continue      # inside one core: that tile had it whole
            r0, r1, c0, c1 = max(sl_[0].start - nh, 0), min(sl_[0].stop + nh, H), max(sl_[1].start - nh, 0), min(sl_[1].stop + nh, Wd)
            if max(r1 - r0, c1 - c0) * res > a.lake_max:
                log(f"  sounded lake {i_} ({int((Lk[sl_] == i_).sum()) * res * res / 1e4:.0f} ha) spans {(r1 - r0) * res:.0f} x {(c1 - c0) * res:.0f} m with its halo: over --lake-max {a.lake_max:g}, left to the tiles"); continue
            x0, y1 = T * (c0, r0); x1, y0 = T * (c1, r1)
            st_ = [x for x in list(a.set) + (["snd_scope=0"] if a.deep_grid > 0 else []) if not x.startswith("snd_mode=")] + ["snd_mode=1"]
            ljobs.append((i_, (r0, r1, c0, c1), [a.box, f"lake_{i_}", str(x0), str(y0), str(x1), str(y1), "--tag", a.tag, "--lakes", str(lakes), "--nofig", "--nowrite", "--quiet"] + (["--cur", str(curfile)] if curfile else []) + ["--set"] + st_))
        for (i_, (r0, r1, c0, c1), _a), (_r, _c, R, dt) in zip(ljobs, ex.map(_tile, [(j[0], 0, j[2]) for j in ljobs])):
            if isinstance(R, str) or R is None: log(f"  sounded lake {i_}: {R} -- the tiles' answer stands there"); continue
            z, k = R["z"], R["kind"]
            if z.shape != (r1 - r0, c1 - c0): log(f"  sounded lake {i_}: shape {z.shape} != {(r1 - r0, c1 - c0)} -- skipped"); continue
            body = Lk[r0:r1, c0:c1] == i_; f_ = np.clip(1.0 - ndimage.distance_transform_edt(~body) * res / 60.0, 0.0, 1.0).astype(np.float32)
            dn_ = den[r0:r1, c0:c1]; za_ = np.where(dn_ > 1e-6, num[r0:r1, c0:c1] / np.maximum(dn_, 1e-6), z)
            m = (k >= 2) & (f_ > 0) & ((dn_ > 1e-6) | body)
            num[r0:r1, c0:c1][m] = (f_ * z + (1.0 - f_) * za_)[m]; den[r0:r1, c0:c1][m] = 1.0
            kind[r0:r1, c0:c1][body] = k[body]; typ[r0:r1, c0:c1][body] = R["typing"][body]; zown[r0:r1, c0:c1][body] = z[body]
            log(f"  sounded lake {i_}: {int(body.sum()) * res * res / 1e4:.0f} ha solved whole in one window of {(r1 - r0) * res:.0f} x {(c1 - c0) * res:.0f} m, {dt:.0f} s" + "".join("\n      " + l.strip() for l in R["log"] if any(q in l for q in ("lake profile", "far from every cast", "no deeper than", "fit - cast", "still water of"))))
        del Lk
    # A cell is replaced only where the tile that OWNS it (its core) types it soft or free.  A window's outermost rows are soft to the tile
    # that sees them as the edge of its data, and where no other tile had a say they went out as fitted: a cross of lines at every window
    # corner, +2.8 m at one on the bay floor off the Spit (Hig's review of 2026-11-15, 59.59694,-151.40752: "box edge artifacts?").
    own = (den > 1e-6) & (kind >= 2) & (kind != 255)
    out = np.where(own, num / np.maximum(den, 1e-6), cur).astype(np.float32)
    if deepm is not None:
        kind[deepm & ~own] = 6                                                                     # the deep sea from the whole-box pass
    # ...and a FIXED MEASURED cell is written as its tile returned it: the product's own cell where the product shows a measured layer, the
    # joint's measurement where the product had modelled over it (the solve held a gravel bar the product had dug a river bed into, and
    # the product's cell was kept here: pits in the solved bed, Hig 2026-11-15)
    fm0 = (kind == 0) & np.isfinite(zown); n_back = int((fm0 & (np.abs(zown - cur) > 0.05)).sum()); out = np.where(fm0, zown, out).astype(np.float32)
    if nd is not None: out = np.where(cur == nd, nd, out).astype(np.float32)
    ch = own & (cur != nd if nd is not None else True); dz = (out - cur)[ch]
    prof.update(dtype="float32", compress="ZSTD", tiled=True, BIGTIFF="IF_SAFER")
    kp = dict(prof); kp.update(dtype="uint8", nodata=255)
    for nm_, arr_, pf_ in ((f"{a.box}.tif", out, prof), (f"{a.box}_kind.tif", kind, kp), (f"{a.box}_typing.tif", typ, kp)):   # written whole, then put in place: a mosaic reading meanwhile sees the old file or the new
        with rasterio.open(o / (nm_ + ".part"), "w", **pf_) as ds: ds.write(arr_, 1)
        os.replace(o / (nm_ + ".part"), o / nm_)
    log(f"[{a.box}] {n_back} fixed measured cells the product had modelled over are back as measured")
    log(f"[{a.box}] done: {n_s} of {n_t} tiles solved, {free} free cells, {int(ch.sum())} cells replaced ({ch.mean():.1%} of the box); solve - product p1/p50/p99 {np.percentile(dz, [1, 50, 99]).round(2) if dz.size else '-'} m; {time.time() - t0:.0f} s")
    json.dump(dict(box=a.box, tag=a.tag, out_tag=a.out_tag, tiles=n_t, solved=n_s, free=free, replaced=int(ch.sum()), seconds=round(time.time() - t0), edgefix=True, rules="1115d", measured_back=n_back), open(o / "solve.json", "w"))


if __name__ == "__main__":
    main()
