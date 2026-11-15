#!/usr/bin/env python3
"""solve_box.py -- the single solve (solve.py) over a whole box, tile by tile (2026-11-15; prototype, not in the chain).

    /opt/anaconda3/bin/python3 topobathy/solve_box.py <box> [--tag cm18] [--out-tag s1] [--core 2000] [--halo 400]

Reads the box's finished outputs ($W/<box>/out_<tag>/) and writes $W/<box>/out_<out-tag>/<box>.tif: the product with its water margin
re-solved.  Each tile is solved with a halo and the tiles are FEATHERED across the halo (weight 1 in the core, falling to 0 at the
window's edge), so no tile edge shows.  Only cells the solve treats as free or weighted are replaced; land above the tide, the ice,
confident measurements and the deep modelled sea are the product's own.  Also <box>_kind.tif (what each cell was) and solve.log."""
import argparse, json, sys, time
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
    a = ap.parse_args(); d = S.W / a.box / f"out_{a.tag}"; o = S.W / a.box / f"out_{a.out_tag}"; o.mkdir(parents=True, exist_ok=True)
    lg = open(o / "solve.log", "w")
    def log(s): print(s, flush=True); lg.write(s + "\n"); lg.flush()
    with rasterio.open(d / f"{a.box}.tif") as s:
        cur = s.read(1); prof = s.profile; T = s.transform; res = s.res[0]; H, Wd = cur.shape; nd = s.nodata
    num = np.zeros(cur.shape, np.float32); den = np.zeros(cur.shape, np.float32); kind = np.full(cur.shape, 255, np.uint8); typ = np.zeros(cur.shape, np.uint8)
    nc = int(round(a.core / res)); nh = int(round(a.halo / res)); t0 = time.time(); n_t = n_s = 0; free = 0
    tiles = [(r, c) for r in range(0, H, nc) for c in range(0, Wd, nc)]
    log(f"[{a.box}] {a.tag} -> {a.out_tag}: {H}x{Wd} cells, {len(tiles)} tiles of {a.core:g} m + {a.halo:g} m halo")
    jobs = []
    for r, c in tiles:
        r0, r1, c0, c1 = max(r - nh, 0), min(r + nc + nh, H), max(c - nh, 0), min(c + nc + nh, Wd)
        x0, y1 = T * (c0, r0); x1, y0 = T * (c1, r1)
        jobs.append((r, c, [a.box, f"tile_{r}_{c}", str(x0), str(y0), str(x1), str(y1), "--tag", a.tag, "--nofig", "--nowrite", "--quiet"] + (["--set"] + a.set if a.set else [])))
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
        kind[r0:r1, c0:c1][core] = k[core]; typ[r0:r1, c0:c1][core] = R["typing"][core]
        n_s += 1; free += R["free"]
        log(f"  tile r{r} c{c}: {R['free']} free cells, {dt:.0f} s" + "".join("\n      " + l.strip() for l in R["log"] if any(q in l for q in ("guard:", "lone deep", "still water", "low-tide water:")) and not l.strip().endswith("dropped: 0")))
    out = np.where(den > 1e-6, num / np.maximum(den, 1e-6), cur).astype(np.float32)
    if nd is not None: out = np.where(cur == nd, nd, out).astype(np.float32)
    ch = (den > 1e-6) & (cur != nd if nd is not None else True); dz = (out - cur)[ch]
    prof.update(dtype="float32", compress="ZSTD", tiled=True, BIGTIFF="IF_SAFER")
    with rasterio.open(o / f"{a.box}.tif", "w", **prof) as ds: ds.write(out, 1)
    kp = dict(prof); kp.update(dtype="uint8", nodata=255)
    with rasterio.open(o / f"{a.box}_kind.tif", "w", **kp) as ds: ds.write(kind, 1)
    with rasterio.open(o / f"{a.box}_typing.tif", "w", **kp) as ds: ds.write(typ, 1)
    log(f"[{a.box}] done: {n_s} of {n_t} tiles solved, {free} free cells, {int(ch.sum())} cells replaced ({ch.mean():.1%} of the box); solve - product p1/p50/p99 {np.percentile(dz, [1, 50, 99]).round(2) if dz.size else '-'} m; {time.time() - t0:.0f} s")
    json.dump(dict(box=a.box, tag=a.tag, out_tag=a.out_tag, tiles=n_t, solved=n_s, free=free, replaced=int(ch.sum()), seconds=round(time.time() - t0)), open(o / "solve.json", "w"))


if __name__ == "__main__":
    main()
