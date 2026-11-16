#!/opt/anaconda3/bin/python3
"""clean_margin.py -- the 2008-2009 Kachemak multibeam with its OUTER MARGIN despiked (Hig 2026-11-16, review of the s1 mosaic:
"odd holes along the shore ... severe noise along the margin of the multibeam", 59.47483,-151.67351 and 59.43576,-151.73682).

    clean_margin.py [--tile-m 4000] [--only x0,y0,x1,y1] [--out DIR]

Input: the band-cleaned stack `bathy_src/kachemak_2008_2009_mllw_nad83_clean.vrt` (clean_bands.py: a band's edge is trimmed where
ANOTHER band covers it, but an edge with nothing under it is kept -- it is the only measurement).  That kept edge is where the noise
is: within 3 m of the data's outer edge |z - local median| reads p95 4-10 m, p99 up to 16 m; 6-20 m in, p95 0.03-0.3 m (both review
points).  The swath's outer beams at grazing angle, and the sparse fringe of single cells beyond the solid swath.

THE RULE: a cell within `band` m of the data edge (the union of all bands; the edge of a hole counts) is compared with a PLANE fitted
by least squares to the INTERIOR cells (farther than `band` from any edge) within `win` m -- a plane, so a steep slope is not read as a
spike.  It is dropped where it lies DEEPER than the plane by more than max(abs_m, rel x depth) (deep_only; 0 = both sides).  An edge cell with fewer than `min_interior` interior cells in its
window (the sparse fringe far from a solid swath) is compared with the median of ALL cells in a window of `win` m instead, at
`fringe_k` x the tolerance.  Interior cells are never touched.  Nothing is moved, only dropped (void); the source stack is untouched.

Output: DIR/tiles/<x0>_<y0>.tif (float32, nodata -9999, the input's grid) + DIR/dropped/<x0>_<y0>.tif (uint8: 1 plane test, 2 fringe
test) + DIR/report.json, and `bathy_src/kachemak_2008_2009_mllw_nad83_margin.vrt` over the tiles (same grid, CRS and nodata as the input).
"""
import argparse, json, os, subprocess, time
from pathlib import Path
import numpy as np
import rasterio
from rasterio.windows import Window
from scipy import ndimage

SRC = Path("/Volumes/Nunatak/lidar_build/bathy_src")
INP = SRC / "kachemak_2008_2009_mllw_nad83_clean.vrt"
OUT = SRC / "kachemak_margin_clean"
VRT = SRC / "kachemak_2008_2009_mllw_nad83_margin.vrt"
P = dict(band_m=5.0, win_m=41.0, deep_only=1.0, abs_m=1.0, rel=0.05, min_interior=30, fringe_k=2.0, halo_m=40.0)


def plane_ref(z, interior, win):
    """At every cell, the value at that cell of the least-squares plane through the interior cells in a win x win window (box sums)."""
    H, W = z.shape; yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    w = interior.astype(np.float64); zz = np.where(interior, z, 0.0).astype(np.float64)
    f = lambda a: ndimage.uniform_filter(a, win, mode="constant") * (win * win)
    # centre the coordinates per cell: sums of (x - x0) etc. from raw sums, so the plane is read at the cell itself
    n = f(w); sx = f(w * xx); sy = f(w * yy); sz = f(zz)
    sxx = f(w * xx * xx); syy = f(w * yy * yy); sxy = f(w * xx * yy); sxz = f(zz * xx); syz = f(zz * yy)
    with np.errstate(invalid="ignore", divide="ignore"):
        mx, my, mz = sx / n, sy / n, sz / n
        cxx = sxx / n - mx * mx; cyy = syy / n - my * my; cxy = sxy / n - mx * my; cxz = sxz / n - mx * mz; cyz = syz / n - my * mz
        det = cxx * cyy - cxy * cxy
        a = (cxz * cyy - cyz * cxy) / det; b = (cyz * cxx - cxz * cxy) / det
        flat = ~(np.abs(det) > 1e-6)                 # a line of interior cells (one row): no slope across it, take the mean
        a = np.where(flat, 0.0, a); b = np.where(flat, 0.0, b)
        ref = mz + a * (xx - mx) + b * (yy - my)
    return ref.astype(np.float32), n


def tile(src, x0, y0, tm, res, nd):
    h = int(P["halo_m"] / res); T = src.transform
    c0 = int(round((x0 - T.c) / res)) - h; r0 = int(round((T.f - (y0 + tm)) / res)) - h; n = int(tm / res) + 2 * h
    z = src.read(1, window=Window(c0, r0, n, n), boundless=True, fill_value=nd).astype(np.float32)
    ok = z != nd
    if not ok[h:-h, h:-h].any():
        return None
    band = int(round(P["band_m"] / res)); win = int(round(P["win_m"] / res)) | 1
    d = ndimage.distance_transform_edt(ok)            # cells to the nearest void (the data edge, a hole's edge too)
    edge = ok & (d <= band); interior = ok & (d > band)
    ref, ni = plane_ref(np.where(ok, z, 0.0), interior, win)
    depth = np.maximum(-z, 0.0); tol = np.maximum(P["abs_m"], P["rel"] * depth)
    drop = np.zeros(z.shape, np.uint8)
    # ONE-SIDED by default (deep_only): the review's holes are pits, and the cells a plane misses ABOVE it on the margin are mostly the
    # upper beach at +2.5..+5 m MLLW, sounded at high tide -- a 41 m plane over a beach's convex profile, not noise (s6b, 2026-11-16)
    miss = (ref - z) if P["deep_only"] else np.abs(z - ref)
    a = edge & (ni >= P["min_interior"]); drop[a & (miss > tol)] = 1
    fr = edge & (ni < P["min_interior"])
    if fr.any():
        # the fringe: a plane through ALL cells in the window, refitted without the cells it misses by more than the tolerance
        zf = np.where(ok, z, 0.0); r1, _ = plane_ref(zf, ok, win)
        keep = ok & ~(np.abs(z - r1) > P["fringe_k"] * tol)
        r2, n2 = plane_ref(zf, keep, win)
        missf = (r2 - z) if P["deep_only"] else np.abs(z - r2)
        drop[fr & (n2 >= 5) & (missf > P["fringe_k"] * tol)] = 2
        drop[fr & (n2 < 5)] = 2                            # a fringe cell with almost nothing in its window that agrees with it: alone, no evidence
    z = np.where(drop > 0, nd, z)
    s = (slice(h, -h), slice(h, -h))
    return z[s], drop[s], dict(cells=int(ok[s].sum()), edge=int(edge[s].sum()), dropped_plane=int((drop[s] == 1).sum()), dropped_fringe=int((drop[s] == 2).sum()))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--tile-m", type=float, default=4000.0); ap.add_argument("--only"); ap.add_argument("--out", default=str(OUT))
    for k in P: ap.add_argument("--" + k.replace("_", "-"), type=float)
    a = ap.parse_args(); P.update({k: getattr(a, k) for k in P if getattr(a, k) is not None}); out = Path(a.out); (out / "tiles").mkdir(parents=True, exist_ok=True); (out / "dropped").mkdir(exist_ok=True)
    rep = dict(params=P, input=str(INP), tiles={}); t0 = time.time()
    with rasterio.open(INP) as src:
        res = src.res[0]; nd = src.nodata; b = src.bounds; tm = a.tile_m
        X0, Y0, X1, Y1 = (b.left, b.bottom, b.right, b.top) if not a.only else tuple(float(v) for v in a.only.split(","))
        xs = np.arange(np.floor(X0 / tm) * tm, X1, tm); ys = np.arange(np.floor(Y0 / tm) * tm, Y1, tm)
        prof = dict(driver="GTiff", dtype="float32", count=1, crs=src.crs, nodata=nd, tiled=True, blockxsize=512, blockysize=512, compress="zstd", predictor=3)
        for y0 in ys:
            for x0 in xs:
                r = tile(src, x0, y0, tm, res, nd)
                if r is None:
                    continue
                z, drop, st = r; k = f"{int(x0)}_{int(y0)}"; tr = rasterio.transform.from_origin(x0, y0 + tm, res, res)
                with rasterio.open(out / "tiles" / f"{k}.tif", "w", width=z.shape[1], height=z.shape[0], transform=tr, **prof) as o:
                    o.write(z, 1)
                if drop.any():
                    with rasterio.open(out / "dropped" / f"{k}.tif", "w", width=z.shape[1], height=z.shape[0], transform=tr, **dict(prof, dtype="uint8", nodata=0, predictor=2)) as o:
                        o.write(drop, 1)
                rep["tiles"][k] = st
                print(f"{k}: {st}", flush=True)
    tot = {k: sum(v[k] for v in rep["tiles"].values()) for k in ("cells", "edge", "dropped_plane", "dropped_fringe")}
    rep.update(total=tot, seconds=round(time.time() - t0)); json.dump(rep, open(out / "report.json", "w"), indent=1)
    if not a.only:
        lst = out / "tiles.txt"; lst.write_text("\n".join(str(p) for p in sorted((out / "tiles").glob("*.tif"))) + "\n")
        subprocess.run(["/opt/homebrew/bin/gdalbuildvrt", "-q", "-overwrite", "-input_file_list", str(lst), "-srcnodata", str(nd), "-vrtnodata", str(nd), str(VRT)], check=True)
    print("total", tot, f"{time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
