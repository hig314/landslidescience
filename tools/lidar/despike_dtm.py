#!/usr/bin/env python3
"""Remove floating noise from a DTM tile: clouds and birds the vendor did not
flag, which the ground filter seeded on where there was nothing below, and the
low spikes that outlier detection would have caught had it not been switched
off for speed.

  despike_dtm.py TILE.tif [--rise 30] [--gap 20] [--apply]

WHY NOT A SLOPE OR HEIGHT THRESHOLD
-----------------------------------
The obvious filter is "anything more than N metres above its surroundings",
and it is wrong here: the Kenai Mountains genuinely rise 300 m within 100 m,
so any threshold that clears the lowland noise also shaves real aretes.
Measured across the survey, 362 of 3130 tiles trip such a test and the worst
offenders are mountain tiles reaching 1700-2000 m.

The discriminator is not height, it is SUPPORT. Real terrain is connected
downward: however sharp a peak, its rim stands at a similar height to its
base. A cloud return does not -- its whole boundary drops away. So the test
is per blob: how far above its own rim does it sit. That is scale-free.

Validated (2026-09-17) on four tiles. The two mountain tiles, at 1727 m and
1818 m, lose 122 and 430 cells -- and every removed patch is a speck, the
largest 51 cells, so no arete is touched. The two noisy tiles lose 1.6 and
3.3 ha, whose largest patches are pits 140-180 m below their own rims and
clouds 350-780 m above a 54 m floodplain. Nothing ambiguous in either
direction.
"""import argparse
import numpy as np
import rasterio
from scipy import ndimage as ndi


def _background(a, size=101, step=8):
    """A median over a ~`size`-cell window, computed on a decimated copy.

    The straight filter is unaffordable: 191 s on a 1642-square tile, which
    over 3130 tiles is 21 hours of pure median. It is also more precision than
    the job needs -- the background is only a smooth reference to measure
    departures against, and it must be smooth on a scale far larger than the
    artifacts, or it would follow them. So take a block median at `step`, a
    small median on that, and interpolate back up: half a second, and the same
    reference to within the noise of what it is used for.
    """
    fill = float(np.nanmedian(a))
    f = np.where(np.isfinite(a), a, fill)
    h, w = f.shape
    g = np.pad(f, ((0, (-h) % step), (0, (-w) % step)), mode="edge")
    g = g.reshape(g.shape[0] // step, step, g.shape[1] // step, step)
    small = np.median(g, axis=(1, 3))
    small = ndi.median_filter(small, size=max(3, int(round(size / step)) | 1))
    return ndi.zoom(small, step, order=1)[:h, :w]


def find_floating(a, rise=30.0, gap=20.0, min_cells=3, spike=50.0):
    """-> (mask of bad cells, list of per-blob stats).

    TWO artifacts, two tests, because one test cannot see both.

    SPIKES are single cells or small specks hundreds of metres off, left by
    switching the classifier's outlier detection off for speed. They are found
    by departure from a 5x5 median: measured across the survey, real terrain
    stays within 30 m of its immediate neighbours even on the roughest
    mountain tile (p0.01 of -29 m at 1727 m elevation, -4.6 m at 1818 m),
    while these run to -292 m. A blob test cannot catch them, because
    connected labelling merges a speck into the valley wall beside it and the
    wall's own height then defeats the comparison.

    BLOBS are clouds and bird flocks the vendor never flagged, tens of metres
    across, which the classifier seeded on where nothing lay below. A median
    test cannot catch them, because the median inside a blob is the blob. They
    are found by SUPPORT: real terrain connects downward, so however sharp a
    peak its rim stands near its base, while a cloud's whole boundary drops
    away. That is scale-free, which is what keeps a 1818 m arete safe.
    """
    bg = _background(a)

    # SPIKES, iteratively. One pass cannot clear a cluster wider than the
    # window, because the cluster IS its own 5x5 median. So each pass blanks
    # what it finds and refills those cells from the broad background, which
    # makes the next pass see terrain where the cluster's edge used to be; a
    # patch then erodes inward until it is gone. Four passes clears roughly a
    # 17-cell span, which is wider than anything measured here.
    out = np.zeros(a.shape, bool)
    work = a.copy()
    for _ in range(4):
        med5 = ndi.median_filter(np.where(np.isfinite(work), work, bg), size=5)
        hit = np.isfinite(work) & (np.abs(work - med5) > spike)
        if not hit.any():
            break
        out |= hit
        work[hit] = np.nan

    dz = a - bg
    stats = []
    for sign in (1, -1):
        cand = np.isfinite(dz) & ((dz * sign) > rise)
        lab, n = ndi.label(cand, structure=np.ones((3, 3)))
        for i in range(1, n + 1):
            sel = lab == i
            if sel.sum() < min_cells:
                continue
            rim = ndi.binary_dilation(sel, np.ones((5, 5))) & ~sel & np.isfinite(a)
            rv = a[rim]
            if sign > 0:
                edge = float(np.nanmin(a[sel]))
                ref = float(np.nanpercentile(rv, 90)) if rv.size else None
                d = (edge - ref) if ref is not None else float("inf")
            else:
                edge = float(np.nanmax(a[sel]))
                ref = float(np.nanpercentile(rv, 10)) if rv.size else None
                d = (ref - edge) if ref is not None else float("inf")
            stats.append({"cells": int(sel.sum()), "sign": "high" if sign > 0 else "low",
                          "edge": edge, "gap": d})
            if d > gap:
                out |= sel
    return out, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tif")
    ap.add_argument("--rise", type=float, default=30.0, help="m above the local background to consider")
    ap.add_argument("--gap", type=float, default=20.0, help="m above its own rim to call a blob floating")
    ap.add_argument("--apply", action="store_true", help="write the cells out as nodata")
    a = ap.parse_args()
    with rasterio.open(a.tif) as r:
        z = r.read(1).astype(np.float32)
        nd = r.nodata
        prof = r.profile
    z[z == nd] = np.nan
    mask, stats = find_floating(z, a.rise, a.gap)
    print(f"{a.tif}: {len(stats)} blobs, {int(mask.sum())} floating cells "
          f"({mask.sum() * abs(prof['transform'].a) ** 2 / 1e4:.2f} ha)")
    for s in sorted(stats, key=lambda s: -s["gap"])[:6]:
        print(f"    {s['sign']:4s} {s['cells']:6d} cells  edge {s['edge']:8.1f} m  gap {s['gap']:8.1f} m")
    if a.apply and mask.any():
        z[mask] = np.nan
        with rasterio.open(a.tif, "w", **prof) as d:
            d.write(np.where(np.isfinite(z), z, nd).astype(np.float32), 1)
        print("    written")


if __name__ == "__main__":
    main()
