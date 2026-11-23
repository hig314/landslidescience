#!/usr/bin/env python3
"""Incremental publish of the bay mosaic to the DEV viewer (2026-11-23, Hig: "build the code for 5 and start using that as new
boxes publish").  The whole publish (mosaic_blend.py + dev_viewer_add.py) re-blended every box, rewrote the whole COG and cut
every tile of both pyramids each time: 15-25 min a publish.  This one keeps the blended crops and the two tile trees and redoes
only what a changed box can touch:

  1. a crop is STALE when it is missing or any box blended into it (itself, the neighbours whose build window reaches it) has a
     product newer than the crop; only stale crops are re-blended (the blend is mosaic_blend.py's, cell for cell);
  2. elevation tiles are recut only inside the stale crops' extent, snapped OUTWARD to whole tiles at every zoom, from a VRT of
     ALL crops (so a recut tile is whole, never half a tile over a full one); the old tiles in that range are deleted first;
  3. slope is kept per crop too (gdaldem on the crop + 8 m of its neighbours); stale crops and the crops touching them are
     recomputed, and slope tiles recut over that extent;
  4. the two trees are packed to MBTiles -> PMTiles as before, and the catalogue entry rewritten (z range and coverage from
     per-crop statistics kept beside each crop).

    publish_inc.py BOXES.json WORK MOS ID --title .. --notes .. [--year 2023] [--max-zoom 16] [--full]

BOXES.json: the boxes to show (publish_s3.sh's "latest code only" list); WORK: $W; MOS: where crops/ live; ID: the dev survey id.
--full discards crops and tiles first.  The first run over an existing crops/ + tiles/ pair from the whole publish adopts them.
"""
import argparse, json, math, os, shutil, subprocess, sys, time
from datetime import datetime
from pathlib import Path
import numpy as np
import rasterio
from rasterio.windows import from_bounds, Window
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

REPO = Path(os.environ.get("LS_REPO", "/Users/Hig/Claude_projects/landslidescience"))
RES = 2.0; STRIP = 1000; EPSG = 6334; SLOPE_BUF = 8.0
T0 = time.time()
def log(m): print(f"[{time.time() - T0:6.0f}s] {m}", flush=True)


def blend(b, nb, prod, dst):
    """mosaic_blend.py's crop: every neighbour weighted by its distance to its own build window's edge, clamped at the margin."""
    x0, y0, x1, y1 = b["crop"]; bx0, by0, bx1, by1 = b["build"]
    margin = float(min(x0 - bx0, y0 - by0, bx1 - x1, by1 - y1)); W = int(round((x1 - x0) / RES)); H = int(round((y1 - y0) / RES))
    tmp = dst.with_suffix(".tmp.tif"); zmin, zmax, n = np.inf, -np.inf, 0
    with rasterio.open(tmp, "w", driver="GTiff", width=W, height=H, count=1, dtype="float32", crs=f"EPSG:{EPSG}", transform=from_origin(x0, y1, RES, RES),
                       nodata=-9999.0, tiled=True, compress="ZSTD", BIGTIFF="IF_SAFER") as ds:
        for r0 in range(0, H, STRIP):
            r1 = min(H, r0 + STRIP); sy1 = y1 - r0 * RES; sy0 = y1 - r1 * RES
            num = np.zeros((r1 - r0, W), np.float64); den = np.zeros((r1 - r0, W), np.float64)
            xc = x0 + (np.arange(W) + 0.5) * RES; yc = sy1 - (np.arange(r1 - r0) + 0.5) * RES
            for m in nb:
                nx0, ny0, nx1, ny1 = m["build"]; ix0, ix1 = max(x0, nx0), min(x1, nx1); iy0, iy1 = max(sy0, ny0), min(sy1, ny1)
                if ix1 <= ix0 or iy1 <= iy0: continue
                with rasterio.open(prod[m["id"]]) as s:
                    win = from_bounds(ix0, iy0, ix1, iy1, s.transform); win = Window(int(round(win.col_off)), int(round(win.row_off)), int(round(win.width)), int(round(win.height)))
                    a = s.read(1, window=win).astype(np.float64); nd = s.nodata; a = np.where(a == nd, np.nan, a) if nd is not None else a
                c0 = int(round((ix0 - x0) / RES)); rr0 = int(round((sy1 - iy1) / RES)); hh, ww = a.shape; hh = min(hh, r1 - r0 - rr0); ww = min(ww, W - c0)
                if hh <= 0 or ww <= 0: continue
                a = a[:hh, :ww]
                dx = np.minimum(xc[c0:c0 + ww] - nx0, nx1 - xc[c0:c0 + ww]); dy = np.minimum(yc[rr0:rr0 + hh] - ny0, ny1 - yc[rr0:rr0 + hh])
                w = np.clip(np.minimum(dx[None, :], dy[:, None]) / margin, 0.0, 1.0); ok = np.isfinite(a)
                num[rr0:rr0 + hh, c0:c0 + ww][ok] += (w * a)[ok]; den[rr0:rr0 + hh, c0:c0 + ww][ok] += w[ok]
            good = den > 1e-9; z = np.where(good, num / np.maximum(den, 1e-9), -9999.0).astype(np.float32)
            if good.any(): zmin = min(zmin, float(z[good].min())); zmax = max(zmax, float(z[good].max())); n += int(good.sum())
            ds.write(z, 1, window=Window(0, r0, W, r1 - r0))
    return tmp, dict(z_min=zmin if n else None, z_max=zmax if n else None, cells=n)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("boxes"); ap.add_argument("work"); ap.add_argument("mos"); ap.add_argument("id")
    ap.add_argument("--title", required=True); ap.add_argument("--notes", default=""); ap.add_argument("--year", type=int, default=2023)
    ap.add_argument("--min-zoom", type=int, default=8); ap.add_argument("--max-zoom", type=int, default=16); ap.add_argument("--tag", default="s3")
    ap.add_argument("--dv", default="/Volumes/Nunatak/lidar_build/dev_viewer"); ap.add_argument("--full", action="store_true")
    ap.add_argument("--no-pack", action="store_true", help="stop before MBTiles/PMTiles and the catalogue (testing)")
    a = ap.parse_args()
    boxes = json.load(open(a.boxes)); work = Path(a.work); mos = Path(a.mos); cdir = mos / "crops"; sdir = mos / "slope_crops"
    os.environ["LIDAR_BUILD"] = str(Path(a.dv) / a.id); os.environ["LIDAR_PM_OUT"] = str(REPO / "data" / "lidar_dev" / "pmtiles")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2])); import build_lidar as B
    env = B.clean_env(); G = B.GDAL_BIN; bw = Path(a.dv) / a.id / a.id; tiles = bw / "tiles"; stiles = bw / "tiles_slope"
    if a.full:
        for d in (cdir, sdir, tiles, stiles): shutil.rmtree(d, ignore_errors=True)
    cdir.mkdir(parents=True, exist_ok=True); sdir.mkdir(parents=True, exist_ok=True); bw.mkdir(parents=True, exist_ok=True)
    prod = {b["id"]: work / b["id"] / f"out_{a.tag}" / f"{b['id']}.tif" for b in boxes}; have = [b for b in boxes if prod[b["id"]].exists()]; ids = {b["id"] for b in have}
    first = not tiles.exists() or not any(tiles.iterdir())
    # crops of boxes no longer shown go, and what they covered is recut
    gone = [p for p in cdir.glob("kj_*.tif") if p.stem not in ids and not p.name.endswith(".tmp.tif")]
    def nbs(b):
        x0, y0, x1, y1 = b["crop"]; return [n for n in have if n["build"][0] < x1 and n["build"][2] > x0 and n["build"][1] < y1 and n["build"][3] > y0]
    stale = []
    for b in have:
        c = cdir / f"{b['id']}.tif"; st = cdir / f"{b['id']}.json"; nb = nbs(b); was = json.load(open(st)).get("nb") if st.exists() else None
        if (not c.exists()) or any(prod[n["id"]].stat().st_mtime > c.stat().st_mtime for n in nb) or (was is not None and sorted(was) != sorted(n["id"] for n in nb)): stale.append(b)
    log(f"{len(have)} boxes shown; {len(stale)} crops stale: {' '.join(b['id'][3:] for b in stale)}; {len(gone)} gone")
    ext = []                                                                                   # extents (EPSG:6334) whose tiles are recut
    for p in gone:
        with rasterio.open(p) as s: ext.append(tuple(s.bounds))
        p.unlink(); (cdir / f"{p.stem}.json").unlink(missing_ok=True); (sdir / p.name).unlink(missing_ok=True)
    for b in stale:
        dst = cdir / f"{b['id']}.tif"; nb = nbs(b); tmp, stt = blend(b, nb, prod, dst)
        subprocess.run([str(G / "gdaladdo"), "-q", "-r", "average", str(tmp), "2", "4", "8", "16", "32", "64"], check=True, env=env)
        os.replace(tmp, dst); json.dump(dict(stt, nb=[n["id"] for n in nb]), open(cdir / f"{b['id']}.json", "w")); ext.append(tuple(b["crop"]))
        log(f"  {b['id']} blended with {[n['id'][3:] for n in nb if n['id'] != b['id']]}")
    for b in have:                                                                             # adopted crops: overviews and statistics once
        c = cdir / f"{b['id']}.tif"; st = cdir / f"{b['id']}.json"
        with rasterio.open(c) as s: ov = s.overviews(1)
        if not ov:
            mt = c.stat().st_mtime; subprocess.run([str(G / "gdaladdo"), "-q", "-r", "average", str(c), "2", "4", "8", "16", "32", "64"], check=True, env=env); os.utime(c, (mt, mt))
        if not st.exists():
            with rasterio.open(c) as s:
                zmin, zmax, n = np.inf, -np.inf, 0
                for r0 in range(0, s.height, STRIP):
                    z = s.read(1, window=Window(0, r0, s.width, min(STRIP, s.height - r0))); g = z != -9999.0
                    if g.any(): zmin = min(zmin, float(z[g].min())); zmax = max(zmax, float(z[g].max())); n += int(g.sum())
            json.dump(dict(z_min=zmin if n else None, z_max=zmax if n else None, cells=n, nb=[n_["id"] for n_ in nbs(b)]), open(st, "w"))
    vrt = mos / "mosaic.vrt"; subprocess.run([str(G / "gdalbuildvrt"), "-q", "-overwrite", str(vrt)] + [str(cdir / f"{b['id']}.tif") for b in have], check=True, env=env)
    # slope per crop: stale crops and the crops touching them (their edge cells read the changed neighbour)
    touch = lambda b, e: b["crop"][0] <= e[2] and b["crop"][2] >= e[0] and b["crop"][1] <= e[3] and b["crop"][3] >= e[1]
    sst = [b for b in have if not (sdir / f"{b['id']}.tif").exists() or any(touch(b, e) for e in ext)]
    sext = [tuple(b["crop"]) for b in have if any(touch(b, e) for e in ext)] + [e for e in ext]
    for b in sst:
        x0, y0, x1, y1 = b["crop"]; t1 = sdir / f"{b['id']}.z.tif"; t2 = sdir / f"{b['id']}.s.tif"; dst = sdir / f"{b['id']}.tif"
        subprocess.run([str(G / "gdal_translate"), "-q", "-projwin", str(x0 - SLOPE_BUF), str(y1 + SLOPE_BUF), str(x1 + SLOPE_BUF), str(y0 - SLOPE_BUF), str(vrt), str(t1)], check=True, env=env)
        subprocess.run([str(G / "gdaldem"), "slope", str(t1), str(t2), "-q", "-compute_edges", "-s", "1"], check=True, env=env)
        subprocess.run([str(G / "gdal_translate"), "-q", "-projwin", str(x0), str(y1), str(x1), str(y0), "-co", "TILED=YES", "-co", "COMPRESS=ZSTD", "-co", "PREDICTOR=3", str(t2), str(dst.with_suffix(".tmp.tif"))], check=True, env=env)
        subprocess.run([str(G / "gdaladdo"), "-q", "-r", "average", str(dst.with_suffix(".tmp.tif")), "2", "4", "8", "16", "32", "64"], check=True, env=env)
        os.replace(dst.with_suffix(".tmp.tif"), dst); t1.unlink(); t2.unlink()
    log(f"slope recomputed on {len(sst)} crops")
    svrt = mos / "slope.vrt"; subprocess.run([str(G / "gdalbuildvrt"), "-q", "-overwrite", str(svrt)] + [str(sdir / f"{b['id']}.tif") for b in have], check=True, env=env)

    def recut(src, tdir, extents, kind):
        """recut the tiles of tdir that touch the extents (EPSG:6334), every zoom, from src"""
        if first or not extents:
            if not first: return 0
            with rasterio.open(src) as s: extents = [tuple(s.bounds)]
        mb = [transform_bounds(f"EPSG:{EPSG}", "EPSG:3857", *e, densify_pts=21) for e in extents]
        mx0, my0, mx1, my1 = min(m[0] for m in mb), min(m[1] for m in mb), max(m[2] for m in mb), max(m[3] for m in mb); O = B.MERC_ORIGIN; n = 0
        for z in range(a.max_zoom, a.min_zoom - 1, -1):
            res = B.res_at_zoom(z); span = res * B.TILE_PX
            tx0, tx1 = int(math.floor((mx0 - O) / span)), int(math.ceil((mx1 - O) / span)) - 1; ty0, ty1 = int(math.floor((-O - my1) / span)), int(math.ceil((-O - my0) / span)) - 1
            te = (O + tx0 * span, -O - (ty1 + 1) * span, O + (tx1 + 1) * span, -O - ty0 * span)
            for x in range(tx0, tx1 + 1):                                                    # the old tiles of the range go: a tile now empty must not survive
                xd = tdir / str(z) / str(x)
                if xd.is_dir():
                    for y in range(ty0, ty1 + 1): (xd / f"{y}.png").unlink(missing_ok=True)
            w1 = bw / f"inc_{kind}_z{z}.tif"; w2 = bw / f"inc_{kind}q_z{z}.tif"
            cmd = [str(G / "gdalwarp"), "-q", "-overwrite", "-s_srs", f"EPSG:{EPSG}"] + (["-novshift"] if kind == "elev" else []) + ["-t_srs", "EPSG:3857", "-te", *[f"{v:.10f}" for v in te],
                   "-tr", f"{res:.12f}", f"{res:.12f}", "-r", "bilinear" if z == a.max_zoom else "average", "-dstnodata", "-9999", "-ot", "Float32", "-multi", "-wo", "NUM_THREADS=ALL_CPUS",
                   "-co", "TILED=YES", "-co", "COMPRESS=ZSTD", "-co", "BIGTIFF=YES", str(src), str(w1)]
            subprocess.run(cmd, check=True, env=env)
            (B.encode_terrain_rgb if kind == "elev" else B.quantise_slope)(w1, w2)
            subprocess.run([str(G / "gdal"), "raster", "tile", "-q", "--min-zoom", str(z), "--max-zoom", str(z), "-r", "nearest", "--convention", "xyz", "--skip-blank", "--webviewer", "none",
                            "-j", "ALL_CPUS", "--no-intersection-ok", str(w2), str(tdir)], check=True, env=env, stdout=subprocess.DEVNULL)
            w1.unlink(missing_ok=True); w2.unlink(missing_ok=True); n += (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
        return n
    if first: shutil.rmtree(stiles, ignore_errors=True)
    tiles.mkdir(parents=True, exist_ok=True); stiles.mkdir(parents=True, exist_ok=True)
    n1 = recut(vrt, tiles, ext, "elev"); log(f"elevation tiles recut over {n1} tile slots"); n2 = recut(svrt, stiles, sext, "slope"); log(f"slope tiles recut over {n2} tile slots")
    if a.no_pack: log("--no-pack: stopping before the PMTiles and the catalogue"); return
    if not (ext or first) and (B.OUT_PM / f"{a.id}.pmtiles").exists(): log("nothing changed; the PMTiles stand"); pm = B.OUT_PM / f"{a.id}.pmtiles"; spm = B.OUT_PM / f"{a.id}_slope.pmtiles"
    else:
        ds = dict(id=a.id, title=a.title, min_zoom=a.min_zoom, max_zoom=a.max_zoom, target_epsg=EPSG)
        pm = B.build_pmtiles(ds, tiles); spm = B.build_pmtiles(ds, stiles, suffix="_slope"); log("PMTiles packed")
    stats = [json.load(open(cdir / f"{b['id']}.json")) for b in have]; stats = [s for s in stats if s["cells"]]
    with rasterio.open(vrt) as s: bb = transform_bounds(s.crs, "EPSG:4326", *s.bounds)
    zmin = min(s["z_min"] for s in stats); zmax = max(s["z_max"] for s in stats)
    feat = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[bb[0], bb[1]], [bb[2], bb[1]], [bb[2], bb[3]], [bb[0], bb[3]], [bb[0], bb[1]]]]},
            "properties": dict(id=a.id, title=a.title, region="Kenai Peninsula", year=a.year, product="Topobathy composite (dev)", native_res_m=RES,
                               coverage_km2=round(sum(s["cells"] for s in stats) * RES * RES / 1e6, 2), horizontal_crs=f"EPSG:{EPSG}", vertical_datum="NAVD88 (GEOID12B)",
                               min_zoom=a.min_zoom, max_zoom=a.max_zoom, bounds=[round(x, 6) for x in bb], pmtiles_url=f"/lidar/dev/pmtiles/{a.id}.pmtiles", pmtiles_bytes=pm.stat().st_size,
                               slope_url=f"/lidar/dev/pmtiles/{a.id}_slope.pmtiles", slope_bytes=spm.stat().st_size, slope_step=0.5, tiles_url=None, slope_tiles_url=None, fill_mode="holes",
                               ortho_url=None, cog_url=None, z_min=zmin, z_max=zmax, source="landslidescience.org composite (dev)", notes=a.notes,
                               published=datetime.now().astimezone().isoformat(timespec="seconds"))}
    cat = REPO / "data" / "lidar_dev" / "catalog.geojson"; fc = json.loads(cat.read_text()) if cat.exists() else {"type": "FeatureCollection", "features": []}
    fc["features"] = [x for x in fc["features"] if x["properties"]["id"] != a.id] + [feat]
    tmp = cat.with_suffix(".tmp"); tmp.write_text(json.dumps(fc)); os.replace(tmp, cat)
    log(f"done: {len(stale)} crops re-blended, {n1 + n2} tile slots recut")
    print(f"dev catalogue: {len(fc['features'])} surveys, {a.id} z {zmin:.1f}..{zmax:.1f} m")


if __name__ == "__main__":
    main()
