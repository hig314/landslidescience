#!/opt/anaconda3/bin/python3
"""Closed pits in the ArcticDEM + 3DEP stack, with a confidence level: the worklist for review (flags only: nothing here changes a surface).

    arcticdem_pits.py ARCTICDEM_GROUND.tif CTX3DEP.vrt MAD.vrt OUT.geojson X0 Y0 X1 Y1 [--lidar COG]

Hig, 2026-11-03: a steep-sided closed basin only ArcticDEM shows is likely false (a bad scene), but 3DEP misses narrow canyons by 100 m, and most such pits are real.  Scored against
KBay lidar (SHORE_REVIEW_WORKFLOW.md 9a; pits >= 5 m deep, 637 on 38 tiles that did not pick the rules, 20 false):
  medium  3DEP median slope < 5 deg, 3DEP >= 5 m above the bottom, no pit in 3DEP: 5 of 7 false on the clean tiles (16 of 16 on the windows that picked it)
  low     ArcticDEM mad >= 1.5 x its surroundings on < 10 deg, or >= 4 x: 1 of 8 false on the clean tiles
No rule yet clears the high bar (90 %), so no level here is rejected without review."""
import argparse, json, time
import numpy as np, rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from scipy import ndimage as ndi
from skimage.morphology import reconstruction

CRS = "EPSG:6334"; RES = 2.0; TILE = 4000.0; OVER = 200.0


def read(path, tr, n, nearest=False):
    with rasterio.open(path) as d, WarpedVRT(d, crs=CRS, transform=tr, width=n, height=n, src_nodata=d.nodata, nodata=-9999.0,
                                             resampling=Resampling.nearest if nearest else Resampling.bilinear) as v:
        a = v.read(1).astype(np.float32)
    a[a <= -9000] = np.nan
    return a


def fill(s):
    s = np.where(np.isfinite(s), s, np.nanmin(s) - 1)          # voids drain
    seed = s.copy(); seed[1:-1, 1:-1] = s.max()
    return reconstruction(seed, s, method="erosion")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arctic"); ap.add_argument("ctx3dep"); ap.add_argument("mad"); ap.add_argument("out")
    ap.add_argument("bounds", nargs=4, type=float); ap.add_argument("--lidar"); ap.add_argument("--min-depth", type=float, default=5.0)
    a = ap.parse_args()
    x0, y0, x1, y1 = a.bounds; feats = []; n = int((TILE + 2 * OVER) / RES); m0 = int(OVER / RES); t0 = time.time(); done = 0
    for yc in np.arange(y0, y1, TILE):
        for xc in np.arange(x0, x1, TILE):
            tr = from_origin(xc - OVER, yc + TILE + OVER, RES, RES)
            A = read(a.arctic, tr, n)
            if np.isfinite(A).mean() < 0.01:
                continue
            T = read(a.ctx3dep, tr, n)
            if not np.isfinite(T).any() or np.nanmax(T) <= 0:
                continue
            S = np.where(np.isfinite(A), A, T)
            D = fill(S) - S
            lab, k = ndi.label(D >= 2.0, structure=np.ones((3, 3)))
            if not k:
                continue
            idx = np.arange(1, k + 1); dm = ndi.maximum(D, lab, idx)
            if not (dm >= a.min_depth).any():
                continue
            MA = read(a.mad, tr, n, nearest=True); DT = fill(T) - T; L = read(a.lidar, tr, n) if a.lidar else None
            gy, gx = np.gradient(np.nan_to_num(T), RES); st = np.degrees(np.arctan(np.hypot(gx, gy))); st = ndi.median_filter(st[::5, ::5], size=9); st = ndi.zoom(st, 5, order=1)[:n, :n]
            cnt = ndi.sum(D >= 2.0, lab, idx); pos = ndi.maximum_position(D, lab, idx); obj = ndi.find_objects(lab)
            for i in np.flatnonzero(dm >= a.min_depth):
                r, c = pos[i]
                if not (m0 <= r < n - m0 and m0 <= c < n - m0) or not np.isfinite(A[r, c]):     # each pit once, in its core tile; a bottom on 3DEP is not ArcticDEM's
                    continue
                sl = obj[i]; p = 8; s2 = (slice(max(sl[0].start - p, 0), sl[0].stop + p), slice(max(sl[1].start - p, 0), sl[1].stop + p)); m = lab[s2] == i + 1; ring = ndi.binary_dilation(m, iterations=p) & ~m
                with np.errstate(all="ignore"):
                    mp = float(np.nanmedian(MA[s2][m])) if np.isfinite(MA[s2][m]).any() else float("nan")
                    mr = float(np.nanmedian(MA[s2][ring])) if ring.any() and np.isfinite(MA[s2][ring]).any() else float("nan")
                ratio = mp / mr if mr > 0 else float("nan"); above = float(T[r, c] - S[r, c]); slope = float(st[r, c]); inT = float(DT[r, c])
                if slope < 5 and above >= 5 and inT < 1:
                    level = "medium"
                elif (slope < 10 and ratio >= 1.5) or ratio >= 4:
                    level = "low"
                else:
                    continue
                x, y = tr * (c + 0.5, r + 0.5)
                pr = dict(level=level, depth_m=round(float(dm[i]), 1), area_m2=int(cnt[i] * RES * RES), bottom_m=round(float(A[r, c]), 1), dep3_above_m=round(above, 1), dep3_slope_deg=round(slope, 1), mad_ratio=round(ratio, 2) if np.isfinite(ratio) else None)
                if L is not None and np.isfinite(L[r, c]):
                    pr["lidar_m"] = round(float(L[r, c]), 1)
                feats.append(dict(type="Feature", geometry=dict(type="Point", coordinates=[round(x, 1), round(y, 1)]), properties=pr))
            done += 1
            print(f"[{time.strftime('%T')}] tile {round(xc)} {round(yc)}: {len(feats)} pits so far, {time.time() - t0:.0f} s", flush=True)
            json.dump(dict(type="FeatureCollection", crs=dict(type="name", properties=dict(name=CRS)), features=feats), open(a.out, "w"))
    json.dump(dict(type="FeatureCollection", crs=dict(type="name", properties=dict(name=CRS)), features=feats), open(a.out, "w"))
    print(f"{len(feats)} pits: {sum(f['properties']['level'] == 'medium' for f in feats)} medium, {sum(f['properties']['level'] == 'low' for f in feats)} low; {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
