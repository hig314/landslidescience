#!/opt/anaconda3/bin/python3
"""ArcticDEM on HIGH, STEEP ground, where the < 1 m gate (arcticdem_ground_cand.py) throws it out (Hig 2026-11-16: "using ArcticDEM as
better than 3DEP for high steep terrain would be good. We'll need to validate the results").

    arcticdem_alpine.py ARCTICDEM_NAVD88.tif CTX3DEP.vrt OUT.tif [--min-elev 300] [--min-slope 20] [--bias 1.4]

THE MEASUREMENT (review_1114/adem_gate.py, 2026-11-16; lidar bare earth in kj_r4c1 r4c2 r4c3 r5c3 r5c4 r6c4, 10 m cells, only where the
gate rejects ArcticDEM -- 56 % of the lidar ground): at 3DEP elevation >= 300 m and 3DEP slope >= 20 deg, ArcticDEM reads +1.1..+2.0 m
over the lidar (median about +1.4) with NMAD 1.3-4.7 m, 3DEP -1.6..-6.7 m under it with NMAD 1.8-7.3 m (the IfSAR's steep-slope
weakness), and ArcticDEM less 1 m is the closer of the two on 60-73 % of cells in every band.  Below 300 m (forest) ArcticDEM reads
+5.7..+14 m (canopy): never.  Flat ground above 600 m: +6 m (snow, ice): not taken (the slope test excludes it).

THE LAYER: in the region R = {3DEP >= min_elev and 3DEP slope (at 10 m) >= min_slope}, smoothed to a weight w (Gaussian, feather_m), the
cells the gate REJECTS (A - 3DEP >= above_m; the gate's own cells stay in the arcticdem layer) take
    3DEP + w x (max(A - bias, 3DEP) - 3DEP)
-- max(., 3DEP): at the gate's contour (A - 3DEP = above_m) the value is 3DEP's, which is what the gate's own cells (the lower of the
two, --lower) read there, so the two layers meet without a step; w: the region's edge fades to 3DEP over feather_m, no step there either.
Cells with w < 0.02 are nodata (the fallback, 3DEP, takes them).

NOT ON THE ICE (--exclude, validation of 2026-11-16, review_1114/adem_alpine_val.py): on or within 100 m of the 2020 glacier outline
(NSIDC G10040) ArcticDEM less 1.4 m still reads +2.4..+3.8 m over the 2023 lidar and loses to 3DEP (closer on 34-39 % of cells): the
ice thinned between its 2011-2021 strips and the lidar.  Off the ice it wins in every box tested, held-out ones included (r4c4: RMS
3.12 vs 4.25 m, closer on 65 %; r5c4 3.39 vs 4.71, 67 %; r6c4 3.58 vs 4.93, 71 %).  The outline, buffered exclude_buffer_m, is cut out.  Windowed with a halo; 3DEP is read through a bilinear WarpedVRT
onto ArcticDEM's grid (HAZARDS: never the 4326 VRT whole).
"""
import argparse, os, time
import numpy as np, rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window
from scipy import ndimage


def rd(ds, win):
    """A window that may run past the raster's edge, padded with -9999 (a WarpedVRT permits no boundless read)."""
    c0, r0, w, h = int(win.col_off), int(win.row_off), int(win.width), int(win.height)
    cc0, rr0, cc1, rr1 = max(c0, 0), max(r0, 0), min(c0 + w, ds.width), min(r0 + h, ds.height)
    out = np.full((h, w), -9999.0, np.float32)
    if cc1 > cc0 and rr1 > rr0:
        out[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] = ds.read(1, window=Window(cc0, rr0, cc1 - cc0, rr1 - rr0))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arctic"); ap.add_argument("ctx3dep"); ap.add_argument("out")
    ap.add_argument("--min-elev", type=float, default=300.0); ap.add_argument("--min-slope", type=float, default=20.0)
    ap.add_argument("--bias", type=float, default=1.4); ap.add_argument("--above-m", type=float, default=1.0)
    ap.add_argument("--slope-m", type=float, default=10.0); ap.add_argument("--feather-m", type=float, default=30.0)
    ap.add_argument("--only", help="x0,y0,x1,y1 (a test window)")
    ap.add_argument("--exclude", help="polygons (GeoJSON in the raster's CRS) cut out of the region: the glacier outline")
    ap.add_argument("--exclude-buffer-m", type=float, default=100.0)
    a = ap.parse_args()
    A = rasterio.open(a.arctic); T0 = rasterio.open(a.ctx3dep); res = A.res[0]
    T = WarpedVRT(T0, crs=A.crs, transform=A.transform, width=A.width, height=A.height, resampling=Resampling.bilinear, src_nodata=T0.nodata, nodata=-9999.0)
    c_lo, r_lo, c_hi, r_hi = 0, 0, A.width, A.height
    if a.only:
        x0, y0, x1, y1 = (float(v) for v in a.only.split(",")); inv = ~A.transform
        c_lo, r_hi = (int(v) for v in inv * (x0, y0)); c_hi, r_lo = (int(v) for v in inv * (x1, y1))
        c_lo, r_lo = max(c_lo, 0), max(r_lo, 0); c_hi, r_hi = min(c_hi, A.width), min(r_hi, A.height)
    W_, H_ = c_hi - c_lo, r_hi - r_lo
    prof = A.profile.copy(); prof.update(width=W_, height=H_, transform=A.window_transform(Window(c_lo, r_lo, W_, H_)), compress="lzw", predictor=3,
                                         tiled=True, blockxsize=512, blockysize=512, bigtiff="YES", nodata=-9999.0, dtype="float32")
    EX = []
    if a.exclude:
        import json
        from shapely.geometry import shape
        EX = [shape(f["geometry"]).buffer(a.exclude_buffer_m) for f in json.load(open(a.exclude))["features"]]
    from rasterio import features as _feat
    halo = int(4 * a.feather_m / res) + int(2 * a.slope_m / res) + 4; step = 4096; n = dict(region=0, taken=0); t0 = time.time()
    with rasterio.open(a.out + ".part.tif", "w", **prof) as O:
        for r0 in range(r_lo, r_hi, step):
            h = min(step, r_hi - r0)
            for c0 in range(c_lo, c_hi, step):
                w = min(step, c_hi - c0)
                win = Window(c0 - halo, r0 - halo, w + 2 * halo, h + 2 * halo)
                z = rd(A, win); t = rd(T, win)
                av = z > -9000; tv = (t > -9000) & (t != 0)               # 3DEP's 0 m is its sea
                tf = np.where(tv, t, 0.0).astype(np.float32)
                ts = ndimage.gaussian_filter(tf, a.slope_m / res / 2.0)   # ~10 m support for the slope
                gy, gx = np.gradient(ts, res); slope = np.degrees(np.arctan(np.hypot(gx, gy)))
                reg = tv & (t >= a.min_elev) & (slope >= a.min_slope)
                if EX:
                    wt_ = A.window_transform(win)
                    reg &= ~_feat.rasterize([(g, 1) for g in EX], out_shape=reg.shape, transform=wt_).astype(bool)
                wgt = ndimage.gaussian_filter(reg.astype(np.float32), a.feather_m / res / 2.0)
                gate_rej = av & tv & (z - t >= a.above_m)
                val = t + wgt * (np.maximum(z - a.bias, t) - t)
                keep = gate_rej & (wgt >= 0.02)
                out = np.where(keep, val, -9999.0).astype(np.float32)[halo:halo + h, halo:halo + w]
                O.write(out, 1, window=Window(c0 - c_lo, r0 - r_lo, w, h))
                n["region"] += int(reg[halo:halo + h, halo:halo + w].sum()); n["taken"] += int(keep[halo:halo + h, halo:halo + w].sum())
            print(f"[{time.strftime('%T')}] rows {r0 + h - r_lo}/{H_}  region {n['region'] * res * res / 1e6:.1f} km2, taken {n['taken'] * res * res / 1e6:.1f} km2", flush=True)
    os.replace(a.out + ".part.tif", a.out)
    print(f"done {time.time() - t0:.0f} s", n)


if __name__ == "__main__":
    main()
