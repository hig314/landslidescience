#!/usr/bin/env python3
"""The bay mosaic with the boxes FEATHERED across their overlap (2026-11-02, Hig: sharp steps at box margins, e.g.
59.21898 -151.08855 on the crop line between kj_r2c4 and kj_r3c4, where the two boxes' modelled seas differ by 1.9 m --
each box solves its own sea domain with its own spline, so the 2 km overlap carries two surfaces and a hard crop steps
between them).  Every box's product is weighted by its distance to its own BUILD window's edge, clamped at the margin
(build - crop, 2 km): 1 through its crop's interior, falling to 0 at the window's edge, so on a crop line both neighbours
weigh 1 and 2 km in only the owner does.  Observed cells agree between boxes and are untouched by the blend; the modelled
sea crosses the line continuously.  Row strips keep memory small.

    mosaic_blend.py boxes.json WORK_DIR OUT_DIR TAG        -> OUT_DIR/crops/<id>.tif (blended, the crop window), OUT_DIR/mosaic.vrt,
                                                               OUT_DIR/kbay_joint_<TAG>.tif (COG)
    TAG may be a list, newest first (cm17,cm16): each box is taken from the first tag it was run under; the output is named for the first.
"""
import json, sys, subprocess, pathlib
import numpy as np
import rasterio
from rasterio.windows import from_bounds, Window
from rasterio.transform import from_origin

boxes, work, out, tags = json.load(open(sys.argv[1])), pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]), sys.argv[4].split(",")
tag = tags[0]
(out / "crops").mkdir(parents=True, exist_ok=True)
def _prod(i):                    # the first of the tags this box was run under: boxes rerun under a newer tag beside boxes kept from an older one (2026-11-05: only the faulted boxes are rerun)
    for t in tags:
        if (work / i / f"out_{t}" / f"{i}.tif").exists():
            return work / i / f"out_{t}" / f"{i}.tif"
    return work / i / f"out_{tag}" / f"{i}.tif"
prod = {b["id"]: _prod(b["id"]) for b in boxes}
if len(tags) > 1:
    print("boxes by tag:", {t: sorted(i for i, q in prod.items() if q.exists() and q.parent.name == f"out_{t}") for t in tags})
have = [b for b in boxes if prod[b["id"]].exists()]
RES = 2.0; STRIP = 1000
crops = []
for b in have:
    x0, y0, x1, y1 = b["crop"]; bx0, by0, bx1, by1 = b["build"]
    margin = float(min(x0 - bx0, y0 - by0, bx1 - x1, by1 - y1))
    W = int(round((x1 - x0) / RES)); H = int(round((y1 - y0) / RES))
    T = from_origin(x0, y1, RES, RES)
    nb = [n for n in have if n["build"][0] < x1 and n["build"][2] > x0 and n["build"][1] < y1 and n["build"][3] > y0]
    dst = out / "crops" / f"{b['id']}.tif"
    with rasterio.open(dst, "w", driver="GTiff", width=W, height=H, count=1, dtype="float32", crs="EPSG:6334", transform=T,
                       nodata=-9999.0, tiled=True, compress="ZSTD", BIGTIFF="IF_SAFER") as ds:
        for r0 in range(0, H, STRIP):
            r1 = min(H, r0 + STRIP); sy1 = y1 - r0 * RES; sy0 = y1 - r1 * RES
            num = np.zeros((r1 - r0, W), np.float64); den = np.zeros((r1 - r0, W), np.float64)
            # cell-centre coordinates of the strip
            xc = x0 + (np.arange(W) + 0.5) * RES; yc = sy1 - (np.arange(r1 - r0) + 0.5) * RES
            for n in nb:
                nx0, ny0, nx1, ny1 = n["build"]
                ix0, ix1 = max(x0, nx0), min(x1, nx1); iy0, iy1 = max(sy0, ny0), min(sy1, ny1)
                if ix1 <= ix0 or iy1 <= iy0:
                    continue
                with rasterio.open(prod[n["id"]]) as s:
                    win = from_bounds(ix0, iy0, ix1, iy1, s.transform)
                    win = Window(int(round(win.col_off)), int(round(win.row_off)), int(round(win.width)), int(round(win.height)))
                    a = s.read(1, window=win).astype(np.float64); nd = s.nodata
                    a = np.where(a == nd, np.nan, a) if nd is not None else a
                    # place into the strip
                    c0 = int(round((ix0 - x0) / RES)); rr0 = int(round((sy1 - iy1) / RES))
                    hh, ww = a.shape; hh = min(hh, r1 - r0 - rr0); ww = min(ww, W - c0)
                    if hh <= 0 or ww <= 0:
                        continue
                    a = a[:hh, :ww]
                    dx = np.minimum(xc[c0:c0 + ww] - nx0, nx1 - xc[c0:c0 + ww]); dy = np.minimum(yc[rr0:rr0 + hh] - ny0, ny1 - yc[rr0:rr0 + hh])
                    w = np.clip(np.minimum(dx[None, :], dy[:, None]) / margin, 0.0, 1.0)
                    ok = np.isfinite(a)
                    num[rr0:rr0 + hh, c0:c0 + ww][ok] += (w * a)[ok]; den[rr0:rr0 + hh, c0:c0 + ww][ok] += w[ok]
            z = np.where(den > 1e-9, num / np.maximum(den, 1e-9), -9999.0).astype(np.float32)
            ds.write(z, 1, window=Window(0, r0, W, r1 - r0))
    crops.append(str(dst)); print(b["id"], "blended with", [n["id"] for n in nb if n["id"] != b["id"]], flush=True)
subprocess.run(["gdalbuildvrt", "-q", str(out / "mosaic.vrt")] + crops, check=True)
subprocess.run(["gdal_translate", "-q", "-of", "COG", "-co", "COMPRESS=ZSTD", "-co", "BIGTIFF=IF_SAFER", str(out / "mosaic.vrt"), str(out / f"kbay_joint_{tag}.tif")], check=True)
print(len(crops), "boxes in the blended mosaic")
