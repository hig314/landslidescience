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
"""
import argparse
import os
import time

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
    # The support test is run at SEVERAL depths, not one, because connected
    # labelling at a single depth glues a noise core to whatever real ground
    # it happens to touch. A Kenai lowland tile has river valleys 30 m below
    # the local background and noise 400 m below it, and at 30 m they label as
    # one object whose highest cell reaches the rim -- so the blob looks
    # supported and 14,500 bad cells stay. Repeat the test at 100 m and the
    # valley's skirt is no longer a candidate, leaving the noise core alone
    # against a rim of real valley floor, which it clears by 70 m.
    #
    # The deeper passes cannot shave real relief, because relief stays
    # connected: a gorge 150 m below its plateau has walls, so the cells just
    # outside the candidate set sit only just above it and the gap stays
    # small. Depth is what varies between passes; support is still what
    # decides.
    stats = []
    for level in (rise, rise * 3.3, rise * 8.3):
        cands = {sign: np.isfinite(dz) & ((dz * sign) > level) for sign in (1, -1)}
        # The rim is drawn only from cells that are not themselves suspect at
        # this level: where noise comes as a sprawling field, the ring around
        # one arm is mostly other arms, and a reference taken from the raw rim
        # is the noise's own level -- the blob then "fits its surroundings"
        # perfectly. It does; they are both wrong.
        susp = cands[1] | cands[-1] | out
        for sign in (1, -1):
            lab, n = ndi.label(cands[sign], structure=np.ones((3, 3)))
            for i in range(1, n + 1):
                sel = lab == i
                if sel.sum() < min_cells:
                    continue
                rim = ndi.binary_dilation(sel, np.ones((5, 5))) & ~susp & np.isfinite(a)
                rv = a[rim]
                # The blob's own edge is a percentile of it, not its single
                # most extreme cell: a sprawling component can send one thin
                # arm up to ground level, and judging 5,000 cells of noise by
                # that one arm says the whole thing is supported. For a real
                # landform this changes nothing -- its shallow end is broad,
                # so the percentile sits right at the rim where the raw
                # extreme did.
                sv = a[sel]
                if sign > 0:
                    edge = float(np.nanpercentile(sv, 10))
                    ref = float(np.nanpercentile(rv, 90)) if rv.size else None
                    d = (edge - ref) if ref is not None else float("inf")
                else:
                    edge = float(np.nanpercentile(sv, 90))
                    ref = float(np.nanpercentile(rv, 10)) if rv.size else None
                    d = (ref - edge) if ref is not None else float("inf")
                stats.append({"cells": int(sel.sum()), "sign": "high" if sign > 0 else "low",
                              "level": level, "edge": edge, "gap": d})
                if d > gap:
                    out |= sel
    return out, stats


def clean(tif, rise=30.0, gap=20.0, write=True):
    """Apply the filter to one tile, in place. -> (removed cells, valid cells)."""
    with rasterio.open(tif) as r:
        z = r.read(1).astype(np.float32)
        nd = r.nodata
        prof = r.profile
    z[z == nd] = np.nan
    valid = int(np.isfinite(z).sum())
    mask, _ = find_floating(z, rise, gap)
    if write and mask.any():
        z[mask] = np.nan
        with rasterio.open(tif, "w", **prof) as d:
            d.write(np.where(np.isfinite(z), z, nd).astype(np.float32), 1)
    return int(mask.sum()), valid


def _job(args):
    tif, rise, gap = args
    try:
        n, valid = clean(tif, rise, gap)
        return os.path.basename(tif), n, valid, None
    except Exception as exc:                                   # noqa: BLE001
        return os.path.basename(tif), 0, 0, str(exc)[:120]


def batch(d, rise, gap, jobs):
    """Every tile in a directory, in parallel. Gridding is untouched, so this
    can be re-run on a built survey without re-gridding it."""
    import glob
    from concurrent.futures import ProcessPoolExecutor
    files = sorted(glob.glob(os.path.join(d, "*.tif")))
    print(f"{len(files)} tiles, {jobs} workers")
    tot = val = bad = 0
    t0 = time.time()
    with ProcessPoolExecutor(jobs) as ex:
        for i, (name, n, v, err) in enumerate(
                ex.map(_job, [(f, rise, gap) for f in files], chunksize=4), 1):
            if err:
                bad += 1
                print(f"  FAILED {name}: {err}")
            tot += n
            val += v
            if i % 200 == 0 or i == len(files):
                el = time.time() - t0
                print(f"  [{i}/{len(files)}] removed {tot} cells "
                      f"({100 * tot / max(val, 1):.4f}% of valid)  "
                      f"{el / 60:.1f} min, eta {(el / i) * (len(files) - i) / 60:.1f} min")
    print(f"done: {tot} cells removed of {val / 1e6:.0f} M valid "
          f"({100 * tot / max(val, 1):.4f}%), {bad} failed, {(time.time() - t0) / 60:.1f} min")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tif", help="a tile, or a directory of tiles with --batch")
    ap.add_argument("--rise", type=float, default=30.0,
                    help="m below/above the local background for the shallowest pass")
    ap.add_argument("--gap", type=float, default=20.0,
                    help="m clear of its own rim to call a blob floating")
    ap.add_argument("--batch", action="store_true", help="treat the path as a directory")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--apply", action="store_true", help="write the cells out as nodata")
    a = ap.parse_args()
    if a.batch:
        return batch(a.tif, a.rise, a.gap, a.jobs)
    with rasterio.open(a.tif) as r:
        z = r.read(1).astype(np.float32)
        nd = r.nodata
        prof = r.profile
    z[z == nd] = np.nan
    mask, stats = find_floating(z, a.rise, a.gap)
    print(f"{a.tif}: {len(stats)} blobs, {int(mask.sum())} floating cells "
          f"({mask.sum() * abs(prof['transform'].a) ** 2 / 1e4:.2f} ha)")
    for s in sorted(stats, key=lambda s: -s["gap"])[:6]:
        print(f"    {s['sign']:4s} {s['cells']:6d} cells  edge {s['edge']:8.1f} m  "
              f"gap {s['gap']:8.1f} m  (level {s['level']:.0f})")
    if a.apply and mask.any():
        z[mask] = np.nan
        with rasterio.open(a.tif, "w", **prof) as d:
            d.write(np.where(np.isfinite(z), z, nd).astype(np.float32), 1)
        print("    written")


if __name__ == "__main__":
    main()
