#!/opt/anaconda3/bin/python3
"""clean_bands.py -- the 2008-2009 Kachemak multibeam ("Hydropalooza") with each depth-band grid's EDGE trimmed
where another band covers it (Hig 2026-10-18: a 6 km seam at 59.53-59.585 N, 151.576 W).

    clean_bands.py [--edge 2] [--report out.json]

The published stack (`bathy_src/kachemak_2008_2009_mllw.vrt`, 66 per-survey, per-depth-band grids from the
BASE node lists, finest on top) is the hosting stream's product and is not touched.  This writes a cleaned copy
beside it for the composites: `kachemak_bands_clean/<band>.tif` + `kachemak_2008_2009_mllw_clean.vrt` +
`kachemak_2008_2009_mllw_nad83_clean.vrt` (the same two-level structure, same order, same pixels elsewhere).

THE DEFECT: a band grid's outermost node column can be far off the bands around it -- H12090's 4 m band 2 of 4
ends at x 580.55 km reading -77 m where H11938 4 m and H12090 8 m agree on -98 m; finest-on-top made it the
surface.  THE RULES: a band's cells within `edge` cells of its own data edge are dropped wherever ANOTHER band
has data there (an edge with nothing else under it is kept: it is the only measurement); and a cell of a finer
band is dropped where it disagrees with the coarser bands under it by more than max(--abs-m 3, --rel 0.05 x depth)
(H12090 4 m band 2 of 4 reads -76 m over a strip where both 8 m bands read -118 m).  The report lists, per
band, the cells dropped and, for its kept edge-adjacent cells, the median and p95 |difference| from the other
bands -- the census for more of the same.
"""
import argparse, json, re, shutil
from pathlib import Path
import numpy as np
import rasterio
from rasterio.windows import from_bounds
from scipy import ndimage

SRC = Path("/Volumes/Nunatak/lidar_build/bathy_src")
INNER, OUTER = SRC / "kachemak_2008_2009_mllw.vrt", SRC / "kachemak_2008_2009_mllw_nad83.vrt"
CLEAN = SRC / "kachemak_bands_clean"
COV_RES = 4.0


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--edge", type=int, default=2); ap.add_argument("--report", default=str(CLEAN / "report.json"))
    ap.add_argument("--abs-m", dest="abs_m", type=float, default=3.0); ap.add_argument("--rel", type=float, default=0.05)
    a = ap.parse_args()
    xml = INNER.read_text()
    files = re.findall(r'<SourceFilename relativeToVRT="1">([^<]+)</SourceFilename>', xml)
    paths = [INNER.parent / f for f in files]
    with rasterio.open(INNER) as v:
        X0, Y1, W, H = v.transform.c, v.transform.f, v.width, v.height
    cw, ch = int(np.ceil(W / COV_RES)), int(np.ceil(H / COV_RES))
    # coverage: how many bands have data in each 4 m cell
    cov = np.zeros((ch, cw), np.int16); masks = {}
    for p in paths:
        with rasterio.open(p) as s:
            a_ = s.read(1); valid = a_ != s.nodata if s.nodata is not None else np.isfinite(a_)
            f = max(int(round(COV_RES / s.res[0])), 1)
            Hc, Wc = -(-valid.shape[0] // f), -(-valid.shape[1] // f)
            pad = np.zeros((Hc * f, Wc * f), bool); pad[:valid.shape[0], :valid.shape[1]] = valid
            vc = pad.reshape(Hc, f, Wc, f).any(axis=(1, 3))
            c0 = int(round((s.transform.c - X0) / COV_RES)); r0 = int(round((Y1 - s.transform.f) / COV_RES))
            rr, cc = slice(max(r0, 0), min(r0 + Hc, ch)), slice(max(c0, 0), min(c0 + Wc, cw))
            sub = vc[rr.start - r0: rr.stop - r0, cc.start - c0: cc.stop - c0]
            cov[rr, cc] += sub.astype(np.int16); masks[p] = (r0, c0, sub.shape, rr, cc, sub)
    CLEAN.mkdir(exist_ok=True); rep = {}
    # the coarser bands: those overlapping a band at a coarser cell size are its reference
    def _ov(p, q):
        with rasterio.open(p) as A, rasterio.open(q) as B_:
            a1, b1 = A.bounds, B_.bounds
            return not (a1.right < b1.left or a1.left > b1.right or a1.top < b1.bottom or a1.bottom > b1.top)
    coarse_all = sorted(paths, key=lambda q: -rasterio.open(q).res[0])
    for p in paths:
        coarse = [q for q in coarse_all if q != p and _ov(p, q)]
        with rasterio.open(p) as s:
            a_ = s.read(1); prof = s.profile; nd = s.nodata
            valid = a_ != nd if nd is not None else np.isfinite(a_)
            edge = valid & ~ndimage.binary_erosion(valid, iterations=a.edge, border_value=0)
            # others' coverage at this band's cells (4 m lattice, own coverage removed)
            r0, c0, shp, rr, cc, sub = masks[p]
            oth = np.zeros(((shp[0]), shp[1]), np.int16); oth[:] = cov[rr, cc] - sub.astype(np.int16)
            f = max(int(round(COV_RES / s.res[0])), 1)
            othf = np.repeat(np.repeat(oth, f, 0), f, 1)
            o = np.zeros(a_.shape, bool); hh, ww = min(othf.shape[0], a_.shape[0]), min(othf.shape[1], a_.shape[1])
            o[:hh, :ww] = othf[:hh, :ww] > 0
            drop = edge & o
            # ...and anywhere it DISAGREES with the coarser bands under it (H12090 4 m band 2 of 4 reads -76 m over a strip
            # where both 8 m bands read -118 m: wider than any edge): |z - coarser| > max(abs_m, rel x depth) is dropped
            if s.res[0] < 8.0:
                from rasterio.vrt import WarpedVRT
                ref = np.full(a_.shape, np.nan, np.float32)
                for q in coarse:
                    if q == p: continue
                    with rasterio.open(q) as cs:
                        if cs.res[0] <= s.res[0]: continue
                        with WarpedVRT(cs, crs=s.crs, transform=s.transform, width=s.width, height=s.height, resampling=rasterio.enums.Resampling.bilinear) as wv:
                            r_ = wv.read(1, masked=True).astype(np.float32).filled(np.nan)
                    ref = np.where(np.isfinite(ref), ref, r_)
                with np.errstate(invalid="ignore"):
                    bad = valid & np.isfinite(ref) & (np.abs(a_ - ref) > np.maximum(a.abs_m, a.rel * np.abs(ref)))
                drop |= bad
                rep_bad = int(bad.sum())
            else:
                rep_bad = 0
            out = np.where(drop, nd, a_).astype(a_.dtype)
            prof.update(compress="zstd", tiled=True, blockxsize=256, blockysize=256) if a_.shape[0] >= 256 and a_.shape[1] >= 256 else prof.update(compress="zstd")
            with rasterio.open(CLEAN / p.name, "w", **prof) as d:
                d.write(out, 1)
            rep[p.name] = dict(res_m=s.res[0], valid=int(valid.sum()), edge=int(edge.sum()), dropped=int(drop.sum()), disagree=rep_bad)
        print(f"  {p.name}: dropped {rep[p.name]['dropped']:,} ({rep_bad:,} disagreeing with a coarser band; {rep[p.name]['edge']:,} edge cells; {rep[p.name]['valid']:,} valid)", flush=True)
    (CLEAN / "README.txt").write_text(__doc__)
    INNER.with_name("kachemak_2008_2009_mllw_clean.vrt").write_text(xml.replace("kachemak_bands/", "kachemak_bands_clean/"))
    OUTER.with_name("kachemak_2008_2009_mllw_nad83_clean.vrt").write_text(
        OUTER.read_text().replace("kachemak_2008_2009_mllw.vrt", "kachemak_2008_2009_mllw_clean.vrt"))
    Path(a.report).write_text(json.dumps(rep, indent=1))
    tot = sum(r["dropped"] for r in rep.values())
    print(f"{len(paths)} bands, {tot:,} edge cells dropped -> {INNER.with_name('kachemak_2008_2009_mllw_nad83_clean.vrt')}")


if __name__ == "__main__":
    main()
