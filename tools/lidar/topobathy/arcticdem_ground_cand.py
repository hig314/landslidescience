#!/opt/anaconda3/bin/python3
"""ArcticDEM ground candidate: the NAVD88 mosaic where it does not stand above 3DEP by more than a tolerance.

    arcticdem_ground_cand.py ARCTICDEM_NAVD88.tif CTX3DEP.vrt OUT.tif [--above-m 1.0]

ArcticDEM is a surface model from optical stereo (2011-2021 strips): canopy, snow and the odd cloud stand on it.
3DEP in Alaska is the IfSAR DTM, 5 m, bare earth by intent. Measured against the KBay lidar bare earth
(2026-10-31, three 20 km windows): where ArcticDEM - 3DEP < 1 m, ArcticDEM matches the lidar as well as or better
than 3DEP (Grewingk window bias -0.08 vs +1.44 m, NMAD 3.0 vs 3.3); where it stands >= 1 m above 3DEP (60 % of the
Homer bench, 64 % of the forested south shore) it is canopy or snow and 3DEP is better everywhere. Hig's rule
(2026-10-31): treat it like IfSAR, and where IfSAR reads lower, IfSAR is the better reading.

So the candidate keeps ArcticDEM only where A - 3DEP < above_m; the rest is nodata and the fallback / the rim
extension take it. Negative differences are kept whatever their size: a lower ArcticDEM is bare ground 3DEP
smoothed over, or ground lost since (a retreating ice margin, a landslide), and joint.py's epoch rule sorts those.
Residual datum bias (ArcticDEM +0.7 m over the Homer bench where the rule passes: low vegetation plus datum) is
left to joint.py's derived_adjust against the lidar.

Windowed; 3DEP is read through a WarpedVRT (bilinear) onto ArcticDEM's grid, never whole (HAZARDS: the 4326 VRT).
"""
import argparse, os, time

import numpy as np, rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arctic"); ap.add_argument("ctx3dep"); ap.add_argument("out")
    ap.add_argument("--above-m", type=float, default=1.0)
    ap.add_argument("--lower", action="store_true",
                    help="inside the gate take the LOWER of ArcticDEM and 3DEP (2026-11-02, Hig: the gate's contour was a seam of "
                         "up to above_m along the hillsides, following ridgelines where the canopy puts ArcticDEM over the gate; "
                         "with the lower of the two the product equals 3DEP on both sides of the contour and is continuous there, "
                         "and ArcticDEM's bare-ground detail stands wherever it reads lower -- Hig's rule of 2026-10-31)")
    a = ap.parse_args()
    A = rasterio.open(a.arctic); T0 = rasterio.open(a.ctx3dep)
    T = WarpedVRT(T0, crs=A.crs, transform=A.transform, width=A.width, height=A.height, resampling=Resampling.bilinear,
                  src_nodata=T0.nodata, nodata=-9999.0)
    prof = A.profile.copy(); prof.update(compress="lzw", predictor=3, tiled=True, blockxsize=512, blockysize=512, bigtiff="YES", nodata=-9999.0)
    n_a = n_keep = n_not = 0
    t0 = time.time()
    with rasterio.open(a.out + ".part.tif", "w", **prof) as O:
        step = 4096
        for r0 in range(0, A.height, step):
            h = min(step, A.height - r0)
            for c0 in range(0, A.width, step):
                w = min(step, A.width - c0)
                win = Window(c0, r0, w, h)
                z = A.read(1, window=win); t = T.read(1, window=win)
                av = z > -9000; tv = t > -9000
                keep = av & ((~tv) | (z - t < a.above_m))     # no 3DEP under it: nothing says it is canopy, keep
                val = np.where(tv, np.minimum(z, t), z) if a.lower else z
                out = np.where(keep, val, -9999.0).astype(np.float32)
                O.write(out, 1, window=win)
                n_a += int(av.sum()); n_keep += int(keep.sum()); n_not += int((av & ~tv).sum())
            print(f"[{time.strftime('%T')}] rows {r0 + h}/{A.height}  kept {n_keep / max(n_a, 1):.1%} of ArcticDEM cells", flush=True)
    os.replace(a.out + ".part.tif", a.out)
    res = abs(A.transform.a)
    print(f"ArcticDEM {n_a * res * res / 1e6:.0f} km2, kept {n_keep * res * res / 1e6:.0f} km2 (A - 3DEP < {a.above_m} m), "
          f"{n_not * res * res / 1e6:.1f} km2 of it with no 3DEP under; {time.time() - t0:.0f} s")
    os.system(f"/opt/homebrew/bin/gdaladdo -q -r average {a.out} 4 16 64 256")


if __name__ == "__main__":
    main()
