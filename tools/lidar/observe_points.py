#!/opt/anaconda3/bin/python3
"""observe_points.py - the observe stage from the point cloud (TOPOBATHY_PLAN,
Revision 2): bin a survey's points into per-cell EVIDENCE rasters, then
derive the observation's class raster from them by stated rules.

  observe_points.py stats    --survey kbay|grewingk --bounds x0,y0,x1,y1 --out DIR [--res 2] [--jobs 6]
  observe_points.py classify --out DIR [--rules kbay|grewingk] [--dtm archive.tif]

`stats` writes DIR/cellstats.tif (EPSG:6334, `res` m cells, bands below)
by running one PDAL pipeline per tile (COPC tiles for KBay on Powder,
DGGS LAZ tiles for Grewingk on Nunatak) that crops to the box, thins, and
writes an UNCOMPRESSED LAS 1.4 (point format 6) which numpy parses
directly - no laspy in this environment, and the record layout is fixed.
Flight-line tiles overlap, so counts accumulate across tiles.

Bands (all per cell):
  1 n_all        returns of any class          9  zmin_ground   lowest class-2 return (m)
  2 n_ground     class 2 (+20 ignored ground)  10 zmin_unclass  lowest class-1 return
  3 n_unclass    class 1                       11 zmin_water    lowest class-9 return
  4 n_veg        classes 3,4,5                 12 int_ground    mean intensity, class 2
  5 n_water      class 9                       13 int_unclass   mean intensity, class 1
  6 n_built      class 6                       14 int_water     mean intensity, class 9
  7 n_noise      classes 7, 18                 15 zmin_all      lowest return of any class
  8 n_snow       class 21                      16 zmax_low      highest return within 0.3 m of zmin_all (flatness of the lowest surface)

`classify` turns them into DIR/class.tif with the topobathy class codes
(axes.CLASSES: ground 1, water 2, bed 3, ice 4, canopy 6, built 7,
modelled 8, snow 9, void 0) - the rules are in `RULES`, one set per survey,
and are deliberately simple and printed with their outcome so they can be
argued with.  The composite recipe reads class.tif through its `raster`
class step.
"""
import argparse, json, os, subprocess, sys, tempfile, time
from pathlib import Path
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parent))

BANDS = ["n_all", "n_ground", "n_unclass", "n_veg", "n_water", "n_built", "n_noise", "n_snow",
         "zmin_ground", "zmin_unclass", "zmin_water", "int_ground", "int_unclass", "int_water", "zmin_all", "zmax_low"]
EPSG = 6334

SURVEYS = {
    "kbay": dict(kind="copc", tiles="/Volumes/Powder/lidar_src/kbay_2023_laz/tiles",
                 index="/Volumes/Nunatak/lidar_build/kbay_reclass_test/full/tileindex.json",
                 resolution=0.7,            # COPC octree cut: ~2 pts/m2 of the ~25 flown
                 date="2023-08-01"),
    "grewingk": dict(kind="las", tiles="/Volumes/Nunatak/lidar_build/kbay_reclass_test/grewingk_2021/laz",
                     index="/Volumes/Nunatak/lidar_build/kbay_reclass_test/grewingk_2021/las_index/data/lidar_las_index/rdf2023-2-grewingk-lidar-2021-las-index.shp",
                     decimate=4,            # every 4th point of ~33/m2
                     date="2021-10-12"),
}

# LAS 1.4 point data record format 6: 30 bytes
PF6 = np.dtype([("X", "<i4"), ("Y", "<i4"), ("Z", "<i4"), ("intensity", "<u2"), ("ret", "u1"), ("flags", "u1"),
                ("cls", "u1"), ("user", "u1"), ("angle", "<i2"), ("src", "<u2"), ("gps", "<f8")])


def read_las_pf6(path):
    """X, Y, Z (m), intensity, classification, return number, number of returns from an uncompressed LAS 1.4 / PF6."""
    with open(path, "rb") as f:
        hdr = f.read(375)
        if hdr[:4] != b"LASF":
            raise ValueError(f"{path}: not LAS")
        off = int.from_bytes(hdr[96:100], "little"); fmt = hdr[104]; reclen = int.from_bytes(hdr[105:107], "little")
        if fmt != 6 or reclen != 30:
            raise ValueError(f"{path}: expected point format 6 / 30 bytes, got {fmt} / {reclen}")
        n = int.from_bytes(hdr[247:255], "little")
        sx, sy, sz = np.frombuffer(hdr[131:155], "<f8"); ox, oy, oz = np.frombuffer(hdr[155:179], "<f8")
        f.seek(off)
        rec = np.frombuffer(f.read(n * 30), PF6, n)
    x = rec["X"] * sx + ox; y = rec["Y"] * sy + oy; z = (rec["Z"] * sz + oz).astype(np.float32)
    return x, y, z, rec["intensity"].astype(np.float32), rec["cls"], rec["ret"] & 0x0F, (rec["ret"] >> 4) & 0x0F


# --------------------------------------------------------------- tiles

def tiles_for(survey, box):
    s = SURVEYS[survey]
    x0, y0, x1, y1 = box
    if s["kind"] == "copc":
        idx = json.loads(Path(s["index"]).read_text())
        return [str(Path(s["tiles"]) / k) for k, v in idx.items()
                if not (v["maxx"] < x0 or v["minx"] > x1 or v["maxy"] < y0 or v["miny"] > y1)]
    gj = json.loads(subprocess.run(["/opt/homebrew/bin/ogr2ogr", "-f", "GeoJSON", "-t_srs", f"EPSG:{EPSG}", "/vsistdout/", s["index"]],
                                   capture_output=True, text=True).stdout)
    out, missing = [], []
    for ft in gj["features"]:
        g = ft["geometry"]; rings = g["coordinates"] if g["type"] == "Polygon" else [r for p in g["coordinates"] for r in p]
        xs = [c[0] for r in rings for c in r]; ys = [c[1] for r in rings for c in r]
        if max(xs) < x0 or min(xs) > x1 or max(ys) < y0 or min(ys) > y1:
            continue
        p = Path(s["tiles"]) / ft["properties"]["FileName"].replace(".las", ".laz")
        (out if p.exists() else missing).append(str(p))
    if missing:
        print(f"  {len(missing)} Grewingk tiles over the box are NOT local (fetch with kbay_reclass_test/grewingk_2021/fetch_one.sh): "
              + ", ".join(Path(m).name for m in missing[:8]) + (" ..." if len(missing) > 8 else ""), file=sys.stderr)
    return out


def _pipeline(survey, tile, box, dst):
    s = SURVEYS[survey]
    x0, y0, x1, y1 = box
    if s["kind"] == "copc":
        stages = [{"type": "readers.copc", "filename": tile, "resolution": s["resolution"], "bounds": f"([{x0},{x1}],[{y0},{y1}])"}]
    else:
        stages = [{"type": "readers.las", "filename": tile}, {"type": "filters.decimation", "step": s["decimate"]},
                  {"type": "filters.crop", "bounds": f"([{x0},{x1}],[{y0},{y1}])"}]
    stages.append({"type": "writers.las", "filename": dst, "minor_version": 4, "dataformat_id": 6, "compression": False,
                   "forward": "scale,offset"})
    return {"pipeline": stages}


def _tile_job(args):
    survey, tile, box, res, tmpdir = args
    dst = str(Path(tmpdir) / (Path(tile).stem + ".las"))
    pj = dst + ".json"; Path(pj).write_text(json.dumps(_pipeline(survey, tile, box, dst)))
    r = subprocess.run(["pdal", "pipeline", pj], capture_output=True, text=True)
    if r.returncode:
        return tile, None, r.stderr[-400:]
    if not Path(dst).exists() or Path(dst).stat().st_size < 400:
        return tile, None, "no points"
    x, y, z, inten, cls, rn, nr = read_las_pf6(dst)
    os.remove(dst); os.remove(pj)
    if x.size == 0:
        return tile, None, "no points"
    acc = bin_points(x, y, z, inten, cls, box, res)
    if acc is None:
        return tile, None, "no points in box"
    return tile, acc, None


def bin_points(x, y, z, inten, cls, box, res):
    """Accumulators for one tile on the tile's own sub-box of the grid (so a
    worker never holds the whole box): dict of 2-D arrays plus the (r0, c0)
    offset where they sit."""
    x0, y0, x1, y1 = box
    W = int(round((x1 - x0) / res)); H = int(round((y1 - y0) / res))
    col = np.floor((x - x0) / res).astype(np.int64); row = np.floor((y1 - y) / res).astype(np.int64)
    ok = (col >= 0) & (col < W) & (row >= 0) & (row < H)
    col, row, z, inten, cls = col[ok], row[ok], z[ok], inten[ok], cls[ok]
    if col.size == 0:
        return None
    r0, c0 = int(row.min()), int(col.min()); h = int(row.max()) - r0 + 1; w = int(col.max()) - c0 + 1
    flat = (row - r0) * w + (col - c0); n = w * h
    out = {"_off": (r0, c0, h, w)}
    def count(sel): return np.bincount(flat[sel], minlength=n).reshape(h, w)
    def zmin(sel):
        m = np.full(n, np.inf, np.float32)
        if sel.any(): np.minimum.at(m, flat[sel], z[sel])
        return m.reshape(h, w)
    def isum(sel): return np.bincount(flat[sel], weights=inten[sel], minlength=n).reshape(h, w)
    g = (cls == 2) | (cls == 20); u = cls == 1; v = (cls >= 3) & (cls <= 5); wa = cls == 9; b = cls == 6
    no = (cls == 7) | (cls == 18); sn = cls == 21
    out["n_all"] = count(np.ones(cls.shape, bool)); out["n_ground"] = count(g); out["n_unclass"] = count(u); out["n_veg"] = count(v)
    out["n_water"] = count(wa); out["n_built"] = count(b); out["n_noise"] = count(no); out["n_snow"] = count(sn)
    out["zmin_ground"] = zmin(g); out["zmin_unclass"] = zmin(u); out["zmin_water"] = zmin(wa)
    out["isum_ground"] = isum(g); out["isum_unclass"] = isum(u); out["isum_water"] = isum(wa)
    notnoise = ~no
    zm = zmin(notnoise); out["zmin_all"] = zm
    # highest return within 0.3 m of this tile's cell minimum (overlapping tiles: merged by max, a
    # slight overestimate where two lines' minima differ - fine for a flatness band)
    near = notnoise & (z <= zm.ravel()[flat] + 0.3)
    zl = np.full(n, -np.inf, np.float32)
    if near.any(): np.maximum.at(zl, flat[near], z[near])
    out["zmax_low"] = zl.reshape(h, w)
    return out


def cmd_stats(a):
    box = tuple(float(v) for v in a.bounds.split(","))
    res = float(a.res)
    tiles = tiles_for(a.survey, box)
    print(f"[{a.survey}] {len(tiles)} tiles over {box}, {res} m cells")
    if not tiles:
        sys.exit("no tiles")
    w = int(round((box[2] - box[0]) / res)); h = int(round((box[3] - box[1]) / res)); n = w * h
    def init(k):
        if k.startswith("n_"): return np.zeros((h, w), np.int64)
        if k.startswith("zmin"): return np.full((h, w), np.inf, np.float32)
        if k == "zmax_low": return np.full((h, w), -np.inf, np.float32)
        return np.zeros((h, w), np.float64)
    acc = {k: init(k) for k in ["n_all", "n_ground", "n_unclass", "n_veg", "n_water", "n_built", "n_noise", "n_snow",
                                "zmin_ground", "zmin_unclass", "zmin_water", "isum_ground", "isum_unclass", "isum_water", "zmin_all", "zmax_low"]}
    t0 = time.time()
    with tempfile.TemporaryDirectory(dir="/Volumes/Nunatak/lidar_build/tmp" if Path("/Volumes/Nunatak/lidar_build/tmp").exists() else None) as tmp:
        jobs = [(a.survey, t, box, res, tmp) for t in tiles]
        import multiprocessing as mp
        ctx = mp.get_context("fork")
        with ctx.Pool(a.jobs) as pool:
            for k, (tile, part, err) in enumerate(pool.imap_unordered(_tile_job, jobs), 1):
                if part is None:
                    print(f"  {k}/{len(tiles)} {Path(tile).name}: {err}"); continue
                r0, c0, hh, ww = part["_off"]; sl = (slice(r0, r0 + hh), slice(c0, c0 + ww))
                for key, val in part.items():
                    if key == "_off": continue
                    if key.startswith("zmin"): np.minimum(acc[key][sl], val, out=acc[key][sl])
                    elif key == "zmax_low": np.maximum(acc[key][sl], val, out=acc[key][sl])
                    else: acc[key][sl] += val
                print(f"  {k}/{len(tiles)} {Path(tile).name}: {int(part['n_all'].sum()):,} pts  {time.time() - t0:5.0f}s", flush=True)
    bands = {}
    for k in BANDS:
        if k.startswith("int_"):
            cnt = acc["n_" + k[4:]]; s = acc["isum_" + k[4:]]
            bands[k] = np.where(cnt > 0, s / np.maximum(cnt, 1), 0).astype(np.float32)
        elif k == "zmax_low":
            bands[k] = np.where(np.isfinite(acc[k]), acc[k], -9999).astype(np.float32)
        elif k.startswith("zmin"):
            bands[k] = np.where(np.isfinite(acc[k]), acc[k], -9999).astype(np.float32)
        else:
            bands[k] = np.minimum(acc[k], 2 ** 31 - 1).astype(np.float32)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    prof = dict(driver="GTiff", crs=CRS.from_epsg(EPSG), transform=from_origin(box[0], box[3], res, res), width=w, height=h,
                count=len(BANDS), dtype="float32", nodata=-9999, tiled=True, blockxsize=512, blockysize=512, compress="ZSTD", predictor=3, BIGTIFF="IF_SAFER")
    with rasterio.open(out / "cellstats.tif", "w", **prof) as ds:
        for i, k in enumerate(BANDS, 1):
            ds.write(bands[k], i); ds.set_band_description(i, k)
    meta = dict(survey=a.survey, date=SURVEYS[a.survey]["date"], bounds=box, res_m=res, tiles=len(tiles), bands=BANDS,
                thinning=SURVEYS[a.survey].get("resolution", SURVEYS[a.survey].get("decimate")), seconds=round(time.time() - t0),
                points=int(acc["n_all"].sum()))
    (out / "cellstats.json").write_text(json.dumps(meta, indent=1))
    cov = (acc["n_all"] > 0).mean()
    print(f"wrote {out / 'cellstats.tif'}: {meta['points']:,} points, {cov:.3f} of cells with returns, {meta['seconds']}s")


# ------------------------------------------------------------ classify

def rules_kbay(B, res, dtm=None, fill=None, aux=None):
    """KBay 2023 (NIR, August, leaf-on): the laser gets almost nothing back from water, so
    NO RETURNS is the water/void signal; class 9 marks the few surface returns; class 6
    marks structures.  Ground = the reclass DTM's observation wherever it has one and
    nothing says otherwise (class 2 is what the reclass built from, and its under-canopy
    ground is better than the vendor's).  `aux` carries the reclass's own masks when given
    (2026-10-10, kbay_reclass_test/model/water.py): 'struct' (vendor 6/17 cells, excluded
    from its ground and planed under -> built), 'water' (cells whose floor is the water)
    and 'level' (its local water level) -> water where the DTM sits at that level."""
    from scipy import ndimage
    from topobathy.axes import CLASSES
    n_all, n_g, n_u, n_w, n_b, n_sn = (B[k] for k in ("n_all", "n_ground", "n_unclass", "n_water", "n_built", "n_snow"))
    c = np.zeros(n_all.shape, np.uint8)
    has_dtm = np.ones(n_all.shape, bool) if dtm is None else np.isfinite(dtm)
    # 1. observed ground: the DTM exists there and the cell has returns
    c[has_dtm & (n_all > 0)] = CLASSES["ground"]
    # 2. no returns at all: nothing observed - void.  Opened so single empty cells inside
    #    dense data (shadowed cells, thinning) do not punch holes; blobs >= min_area stay void
    empty = n_all == 0
    empty = ndimage.binary_opening(empty, iterations=1)
    lab, k = ndimage.label(empty)
    if k:
        sizes = ndimage.sum(np.ones_like(lab), lab, np.arange(1, k + 1))
        small = np.isin(lab, np.flatnonzero(sizes * res * res < 50) + 1)   # < 50 m2: a gap, keep the DTM
        empty &= ~small
    c[empty] = CLASSES["void"]
    # 3. the reclass's fill (its own fill_mask) is a modelled surface - Hig 2026-10-10: meaningless, mask out
    if fill is not None:
        c[fill] = CLASSES["void"]
    # 4. water surface returns: class 9 outnumbers ground in the cell
    c[(n_w > 0) & (n_w >= n_g)] = CLASSES["water"]
    # 5. structures: class 6 present and ground returns sparse -> built (never bare earth);
    #    the reclass's struct_mask is the same evidence at 1 m, closed, and authoritative
    c[(n_b > 0) & (n_b >= n_g)] = CLASSES["built"]
    if aux and aux.get("struct") is not None:
        c[aux["struct"] & has_dtm] = CLASSES["built"]
    if aux and aux.get("water") is not None and aux.get("level") is not None and dtm is not None:
        at_level = aux["water"] & np.isfinite(aux["level"]) & np.isfinite(dtm) & (np.abs(dtm - aux["level"]) <= 0.3)
        c[at_level & (c != CLASSES["void"])] = CLASSES["water"]
    # 6. vendor snow (class 21; none on the 2023-08-01 lines, present on the 2024 ones)
    c[n_sn > n_g] = CLASSES["snow"]
    return c


def rules_grewingk(B, res, dtm=None, fill=None, aux=None):
    """Grewingk 2021 (NIR, 12 October): the vendor's ground class stops at ~830 m; above
    that the surface is class 1 (unclassified) and the DTM still carries it.  Water (class 9)
    is the lake at 48.8 m.  Snow/ice: NOT decided here yet - cells where the DTM surface
    comes only from unclassified returns are marked `snow` as the HYPOTHESIS to examine
    (October, high ground), with the evidence bands beside it; replace with an intensity /
    roughness rule once looked at."""
    from scipy import ndimage
    from topobathy.axes import CLASSES
    n_all, n_g, n_u, n_w, n_no = (B[k] for k in ("n_all", "n_ground", "n_unclass", "n_water", "n_noise"))
    c = np.zeros(n_all.shape, np.uint8)
    has_dtm = np.ones(n_all.shape, bool) if dtm is None else np.isfinite(dtm)
    real = n_all - n_no
    c[has_dtm & (real > 0)] = CLASSES["ground"]
    # water: class 9 present, outnumbering ground
    c[(n_w > 0) & (n_w >= n_g)] = CLASSES["water"]
    # the hypothesis: DTM surface from unclassified returns only, at region scale (opened, >= 2000 m2)
    unc = has_dtm & (n_g == 0) & (n_u > 0)
    unc = ndimage.binary_opening(unc, iterations=2)
    lab, k = ndimage.label(unc)
    if k:
        sizes = ndimage.sum(np.ones_like(lab), lab, np.arange(1, k + 1))
        unc &= ~np.isin(lab, np.flatnonzero(sizes * res * res < 2000) + 1)
    c[unc & (c == CLASSES["ground"])] = CLASSES["snow"]
    # nothing but noise, or nothing at all: void
    c[has_dtm & (real == 0)] = CLASSES["void"]
    return c


RULES = {"kbay": rules_kbay, "grewingk": rules_grewingk}


def cmd_classify(a):
    from topobathy.axes import CLASS_NAMES
    from topobathy.stack import read_geo
    from topobathy.grid import Grid
    out = Path(a.out)
    with rasterio.open(out / "cellstats.tif") as ds:
        B = {ds.descriptions[i - 1]: ds.read(i) for i in range(1, ds.count + 1)}
        prof = ds.profile; transform = ds.transform; res = ds.res[0]
        grid = Grid(ds.crs, ds.transform, ds.width, ds.height)
    meta = json.loads((out / "cellstats.json").read_text())
    rules = a.rules or meta["survey"]
    dtm = None
    if a.dtm:
        dtm, dv = read_geo(a.dtm, grid, grid.full(), "nearest"); dtm[~dv] = np.nan
    fill = None
    if a.fill:
        fr, fv = read_geo(a.fill, grid, grid.full(), "nearest"); fill = fv & (np.rint(fr) == 2)
    aux = {}
    for key, path, kind in (("struct", a.struct, "mask"), ("water", a.water, "mask"), ("level", a.level, "value")):
        if path:
            r, v = read_geo(path, grid, grid.full(), "nearest")
            aux[key] = (v & (np.rint(r) == 1)) if kind == "mask" else np.where(v, r, np.nan)
    c = RULES[rules](B, res, dtm, fill, aux)
    prof.update(count=1, dtype="uint8", nodata=255, predictor=2)     # 0 is a real class (void), so nodata is 255
    with rasterio.open(out / "class.tif", "w", **prof) as ds:
        ds.write(c, 1)
    tot = max((c > 0).sum(), 1)
    frac = {CLASS_NAMES[int(k)]: round(float(v) / tot, 4) for k, v in zip(*np.unique(c[c > 0], return_counts=True))}
    void_km2 = round(float(((c == 0) & (B["n_all"] >= 0)).sum()) * res * res / 1e6, 3)
    print(f"[{meta['survey']}] class.tif by rules '{rules}': {frac}  (void incl. no-data {void_km2} km2)")
    (out / "class.json").write_text(json.dumps(dict(survey=meta["survey"], rules=rules, fractions=frac, dtm=a.dtm, fill=a.fill,
                                                    struct=a.struct, water=a.water, level=a.level), indent=1))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("stats"); s.add_argument("--survey", required=True, choices=list(SURVEYS)); s.add_argument("--bounds", required=True)
    s.add_argument("--out", required=True); s.add_argument("--res", type=float, default=2.0); s.add_argument("--jobs", type=int, default=6)
    c = sp.add_parser("classify"); c.add_argument("--out", required=True); c.add_argument("--rules", choices=list(RULES))
    c.add_argument("--dtm", help="the survey's archive DTM: 'observed' means the DTM has a value there")
    c.add_argument("--fill", help="a fill mask raster (2 = filled) to void, e.g. the KBay reclass fill_mask.tif")
    c.add_argument("--struct", help="structure mask (1 = building/bridge cells) -> built, e.g. the KBay reclass struct_mask.tif")
    c.add_argument("--water", help="water-cell mask (1 = the floor is the water), e.g. the KBay reclass water_mask.tif")
    c.add_argument("--level", help="local water level raster, e.g. the KBay reclass water_level.tif (with --water: DTM at the level -> water)")
    a = p.parse_args()
    {"stats": cmd_stats, "classify": cmd_classify}[a.cmd](a)


if __name__ == "__main__":
    main()
