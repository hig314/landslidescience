#!/opt/anaconda3/bin/python3
"""Topobathy composite CLI.

  composite.py build       recipe.json [--tile 2048] [--jobs 4] [--diag] [--no-cog]
  composite.py build       recipe.json --window r0,r1,c0,c1 [--out out.tif] [--masks-dir DIR] [--diag-dir DIR]
  composite.py regress     recipe.json --against legacy.tif --window r0,r1,c0,c1
  composite.py masks       recipe.json --window r0,r1,c0,c1 --out-dir DIR
  composite.py import-mask recipe.json --layer NAME edited.tif [--align geo|pixel]
  composite.py export-mask recipe.json --layer NAME --out mask.tif [--which effective|auto]
  composite.py publish     recipe.json [--write]

Without `--window`, `build` streams the whole grid in tiles (each built with
a halo so no generator sees a tile edge as a layer edge) into the recipe's
work directory:

  <work_dir>/out/<id>.tif         the composite, archive COG (float32, -9999)
  <work_dir>/out/source_index.tif which layer won (uint8, 255 = nothing)
  <work_dir>/out/report.json      what was decided, per layer and per overlap
  <work_dir>/masks/<layer>.tif    effective masks, <layer>_auto.tif the generated ones
  (axes recipes) out/class_now.tif, winner.tif, date_now.tif, sigma.tif, fallback.tif

Water levels that a recipe leaves "auto" are estimated ONCE for the whole
grid in a coarse pre-pass and pinned for every tile, so a tile without the
lake in view cannot disagree with its neighbour about where the water is.
"""
import argparse, json, os, sys, time
from pathlib import Path
import numpy as np
import rasterio
from rasterio.windows import Window

sys.path.insert(0, str(Path(__file__).resolve().parent))
from topobathy import Recipe, fold
from topobathy.stack import read_layer
from topobathy.masks import layer_mask, to_uint8

TILE_BLOCK = 512          # output block size; tiles must be a multiple so a tile write never splits a block


def parse_window(s, grid):
    if not s:
        return grid.full()
    r0, r1, c0, c1 = (int(v) for v in s.split(","))
    return grid.window(r0, r1, c0, c1)


def build_window(recipe, grid, win, verbose=True, halo=None, obs_paint=True):
    """Returns (out, per-layer list of dicts with z, valid, mask, auto).

    Masks are computed on the window plus a halo (default: the recipe's
    `halo_px`, else 128) and cropped back, so erode / feather / despeckle
    never see the window edge as a layer edge.  `obs_paint=False` leaves the
    observations' class / sigma paint unapplied (the mask tool applies it
    itself so it can be erased)."""
    from topobathy.fold import fold_step
    if halo is None:
        halo = int(recipe.d.get("halo_px", 128))
    r0, c0 = int(win.row_off), int(win.col_off)
    r1, c1 = r0 + int(win.height), c0 + int(win.width)
    big = grid.window(r0 - halo, r1 + halo, c0 - halo, c1 + halo)
    cr, cc = r0 - int(big.row_off), c0 - int(big.col_off)          # crop offsets
    crop = (slice(cr, cr + r1 - r0), slice(cc, cc + c1 - c0))
    out, layers = _build_window(recipe, grid, big, verbose, obs_paint)
    out = out[crop]
    for L in layers:
        for k in ("z", "valid", "mask", "auto", "cls", "sigma", "st", "gate", "below"):
            if L.get(k) is not None:
                L[k] = np.ascontiguousarray(L[k][crop])
        if "rules" in L:
            R = L["rules"]
            for k in ("class_now", "date_now", "winner", "fallback"):
                R[k] = np.ascontiguousarray(R[k][crop])
            R["sigma_total"] = np.ascontiguousarray(R["sigma_total"][(slice(None),) + crop])
            R["gate"] = np.ascontiguousarray(R["gate"][(slice(None),) + crop])
        if L["box"] is not None:
            b = L["box"]; L["box"] = (max(b[0] - cr, 0), min(b[1] - cr, r1 - r0), max(b[2] - cc, 0), min(b[3] - cc, c1 - c0))
    return out, layers


def _obs_paint(recipe, L, grid, win):
    """Apply the observation's hand class / sigma corrections, if any."""
    from topobathy.masks import read_paint
    from topobathy import axes
    p = recipe.obs_paint_path(L["name"], "class")
    if p.exists():
        L["cls"] = axes.apply_class_paint(L["cls"], *read_paint(p, grid, win))
    L["info"]["_cls"] = L["cls"]


def _obs_sigma_paint(recipe, L, grid, win):
    from topobathy.masks import read_paint
    from topobathy import axes
    p = recipe.obs_paint_path(L["name"], "sigma")
    if p.exists():
        L["sigma"] = axes.apply_sigma_paint(L["sigma"], *read_paint(p, grid, win))


def _build_window(recipe, grid, win, verbose=True, obs_paint=True):
    from topobathy.fold import fold_step
    from topobathy import axes
    from topobathy.masks import footprint, run_auto, read_paint, effective as _effective, OPS, despeckle, smooth
    layers = []
    t = time.time()
    # pass 1: read everything
    for spec in recipe.layers:
        z, valid = read_layer(spec, recipe, grid, win)
        layers.append(dict(name=spec["name"], spec=spec, z=z, valid=valid, mask=None, auto=None, box=None, info={}))
    target = recipe.d.get("target")
    if target:
        # pass 2: the three axes per observation, against a provisional fold (footprint order) for water levels
        prov = None
        for L in layers:
            below = None if prov is None else prov.copy()
            L["below"] = below
            others = {M["name"]: (M["z"], M["valid"]) for M in layers if M is not L}
            L["cls"] = axes.class_raster(L["spec"], L["z"], L["valid"], below, grid.res, L["info"], others, grid, win)
            if obs_paint: _obs_paint(recipe, L, grid, win)
            else: L["info"]["_cls"] = L["cls"]
            for step in L["spec"].get("sigma", []):          # give raster steps the grid/window to read onto
                if "raster" in step:
                    step["raster"]["path"] = str(recipe.resolve(step["raster"]["path"])); step["raster"]["_grid"] = grid; step["raster"]["_win"] = win
            L["sigma"] = axes.sigma_raster(L["spec"], L["z"], L["valid"], grid.res, L["info"])
            if obs_paint: _obs_sigma_paint(recipe, L, grid, win)
            L["date"] = axes.years(L["spec"].get("date", L["spec"].get("year")))
            if "class_sigma_max" in L["spec"]: L["class_sigma_max"] = float(L["spec"]["class_sigma_max"])
            prov = L["z"].copy() if prov is None else fold_step(prov, L["z"], footprint(L["valid"]))
        if recipe.d.get("rules", "sequential") == "global":
            rules = axes.state_rules(layers, target, recipe.d.get("as_of"), recipe.d.get("rates"),
                                     recipe.d.get("sigma_max", 5.0), recipe.d.get("lowest_tol_m", 0.2))
        else:
            rules = axes.state_rules_sequential(layers, target, recipe.d.get("as_of"), recipe.d.get("rates"),
                                                recipe.d.get("sigma_max", 5.0), recipe.d.get("lowest"), recipe.d.get("margin_m", 0.0))
        layers[0]["rules"] = rules
        post = recipe.d.get("rules_post", [{"despeckle": {"min_area_m2": 2000}}, {"feather": {"width_m": 20}}])
        for i, L in enumerate(layers):
            L["st"] = rules["sigma_total"][i]; L["gate"] = rules["gate"][i]
            if i == 0:
                L["mask"] = L["valid"].astype(np.float32); continue
            m = rules["masks"][i]
            for step in post:
                (name, kw), = step.items()
                if name == "despeckle": m = despeckle(m, kw["min_area_m2"], grid.res)
                elif name == "smooth": m = smooth(m, kw["size_m"], grid.res)
                elif name in OPS: m = OPS[name](m, kw.get("width_m", kw.get("sigma_m", kw.get("dist_m"))), grid.res)
            L["auto"] = m.astype(np.float32)
            p = recipe.paint_path(L["name"])
            paint = read_paint(p, grid, win) if p.exists() else None
            L["mask"] = _effective(L["auto"], L["valid"], paint)
    else:
        lower = None; out = None
        for L in layers:
            spec = L["spec"]; z, valid = L["z"], L["valid"]
            if lower is None:
                L["mask"], lower, out = valid.astype(np.float32), valid.copy(), z.copy()
            else:
                L["mask"], L["auto"], L["box"] = layer_mask(spec, recipe, grid, win, valid, lower, z, out, L["info"])
                lower |= valid; out = fold_step(out, z, L["mask"])
    out = None
    for L in layers:
        out = L["z"].copy() if out is None else fold_step(out, L["z"], L["mask"])
        if verbose:
            m = L["mask"]; info = L["info"]
            extra = "".join(f"  {k} {v:.2f}" for k, v in info.items() if isinstance(v, float) and not k.startswith("_"))
            cls = ""
            if "cls" in L:
                u, n = np.unique(L["cls"][L["valid"]], return_counts=True)
                cls = "  class " + "/".join(f"{axes.CLASS_NAMES[int(k)]}:{v / n.sum():.2f}" for k, v in zip(u, n))
            print(f"  {L['name']:<12} valid {L['valid'].mean():6.3f}  mask>0 {(m > 0).mean():6.3f}"
                  f"  in(0,1) {((m > 0) & (m < 1)).mean():6.4f}{extra}{cls}  {time.time() - t:5.1f}s")
    return out, layers


def write_tif(path, arr, grid, win, dtype="float32", nodata=-9999.0):
    sub = grid.sub(win)
    a = arr
    if dtype == "float32" and nodata is not None:
        a = np.where(np.isfinite(arr), arr, nodata).astype(np.float32)
    with rasterio.open(path, "w", **sub.profile(dtype, nodata)) as ds:
        ds.write(a, 1)


# ------------------------------------------------------------ pre-pass

def _coarse_grid(grid, f):
    from rasterio.transform import Affine
    from topobathy.grid import Grid
    return Grid(grid.crs, grid.transform * Affine.scale(f), -(-grid.width // f), -(-grid.height // f))


def _read_coarse(spec, recipe, grid, f):
    """One layer decimated by f (nearest), on the coarse lattice."""
    from rasterio.enums import Resampling
    from topobathy.stack import read_geo, _valid
    cg = _coarse_grid(grid, f)
    path = recipe.resolve(spec["source"])
    if spec.get("align", "geo") != "pixel":
        return read_geo(path, cg, cg.full(), spec.get("resample", "nearest"))
    out = np.full((cg.height, cg.width), np.nan, np.float32)
    with rasterio.open(path) as ds:
        w, h = min(grid.width, ds.width), min(grid.height, ds.height)
        sub = ds.read(1, window=Window(0, 0, w, h), out_shape=(-(-h // f), -(-w // f)),
                      resampling=Resampling.nearest).astype(np.float32)
        out[:sub.shape[0], :sub.shape[1]] = sub
        nodata = ds.nodata
    valid = _valid(out, nodata); out[~valid] = np.nan
    return out, valid


def water_level_pins(recipe, grid, f=None, verbose=True):
    """Estimate every "auto" water level once, from the whole grid read
    coarsely, and return pins [(layer, kind, step_index, level)] that
    `apply_pins` writes into the step so each tile uses the same level.
    kind is "class" (a class step: water_level on the layer's own z,
    below_level on the fold below) or "mask" (the `water` mask generator,
    on the fold below)."""
    from topobathy import axes
    from topobathy.masks import footprint, water_level_auto as mask_level
    from topobathy.fold import fold_step
    want = []
    for spec in recipe.layers:
        if "water_level_m" in spec:
            continue
        cs = spec.get("class", "ground") if recipe.d.get("target") else []
        if isinstance(cs, str): cs = []
        for i, step in enumerate(cs):
            (name, kw), = step.items()
            if name in ("water_level", "below_level") and kw.get("level", "auto") == "auto":
                want.append((spec["name"], "class", i, name, kw))
        for i, step in enumerate((spec.get("mask") or {}).get("auto", [])):
            if isinstance(step, dict) and "water" in step and step["water"].get("level", "auto") == "auto":
                want.append((spec["name"], "mask", i, "water", step["water"]))
    if not want:
        return []
    if f is None:
        f = max(1, -(-max(grid.width, grid.height) // 2500))
    t = time.time()
    pins = []; prov = None
    for spec in recipe.layers:
        z, valid = _read_coarse(spec, recipe, grid, f)
        for (lname, kind, idx, sname, kw) in want:
            if lname != spec["name"]:
                continue
            if sname == "water_level":
                level = axes.water_level_auto(z, valid, min_frac=kw.get("min_frac", 0.02))
            elif sname == "below_level":
                level = None if prov is None else axes.water_level_auto(prov, valid, min_frac=kw.get("min_frac", 0.02))
            else:
                level = None if prov is None else mask_level(prov, valid)
            if level is not None:
                pins.append((lname, kind, idx, float(level)))
            if verbose:
                print(f"  pre-pass {lname}.{sname}: level {'none' if level is None else f'{level:.2f} m'} (1/{f} sample)")
        prov = z.copy() if prov is None else fold_step(prov, z, footprint(valid))
    if verbose:
        print(f"  pre-pass {time.time() - t:.1f}s")
    return pins


def apply_pins(recipe, pins):
    for lname, kind, idx, level in pins:
        spec = next(L for L in recipe.layers if L["name"] == lname)
        step = spec["class"][idx] if kind == "class" else spec["mask"]["auto"][idx]
        (name, kw), = step.items()
        kw["level"] = level


# ----------------------------------------------------------- streaming

def tiles(grid, size):
    for r0 in range(0, grid.height, size):
        for c0 in range(0, grid.width, size):
            yield grid.window(r0, r0 + size, c0, c0 + size)


DZ_BIN, DZ_MAX = 0.02, 250.0     # overlap-difference histogram: 2 cm bins over +-250 m (a fjord bed against its water surface), tails counted
NB = int(2 * DZ_MAX / DZ_BIN)


def _tile_stats(out, layers, axes_mode):
    """Per-tile partial statistics for report.json (summed across tiles)."""
    from topobathy import axes
    n = len(layers)
    st = dict(layers=[], pairs={}, source=np.zeros(n + 1, np.int64), finite=int(np.isfinite(out).sum()),
              cells=int(out.size), fallback=0)
    for i, L in enumerate(layers):
        m = L["mask"]
        d = dict(valid=int(L["valid"].sum()), shown=int((m > 0.5).sum()), partial=int(((m > 0) & (m < 1)).sum()),
                 info={"_tiles_" + k: [v] for k, v in L["info"].items() if not k.startswith("_") and isinstance(v, float)})
        if axes_mode:
            d["classes"] = np.bincount(L["cls"][L["valid"]].ravel(), minlength=16)[:16].tolist()
        st["layers"].append(d)
        for j in range(i):
            both = L["valid"] & layers[j]["valid"]
            if not both.any():
                continue
            dz = (L["z"] - layers[j]["z"])[both]
            groups = [(None, np.ones(dz.shape, bool))]           # the plain overlap offset
            if axes_mode:
                # per class, only where BOTH observed that class: a bed against a water surface
                # is a depth, not a change, and must not become a "rate"
                ci, cj = L["cls"][both], layers[j]["cls"][both]
                same = ci == cj
                groups += [(int(c), same & (ci == c)) for c in np.unique(ci[same])]
            for c, sel in groups:
                v = dz[sel]
                idx = np.floor((v + DZ_MAX) / DZ_BIN).astype(np.int64)
                h = np.bincount(np.clip(idx, 0, NB - 1), minlength=NB)
                st["pairs"][(i, j, c)] = (h, int((idx < 0).sum()), int((idx >= NB).sum()))
    idx = np.zeros(out.shape, np.uint8)
    for i, L in enumerate(layers[1:], start=1):
        idx[L["mask"] > 0.5] = i
    idx[~np.isfinite(out)] = n          # bucket n = nothing
    st["source"] = np.bincount(idx.ravel(), minlength=n + 1)[:n + 1]
    if axes_mode:
        st["fallback"] = int(layers[0]["rules"]["fallback"].sum())
    return st


def _merge_stats(acc, st):
    if acc is None:
        return st
    acc["finite"] += st["finite"]; acc["cells"] += st["cells"]; acc["fallback"] += st["fallback"]
    acc["source"] = acc["source"] + st["source"]
    for a, b in zip(acc["layers"], st["layers"]):
        for k in ("valid", "shown", "partial"):
            a[k] += b[k]
        if "classes" in b:
            a["classes"] = (np.array(a["classes"]) + np.array(b["classes"])).tolist()
        for k, v in b["info"].items():
            a["info"].setdefault(k, []).extend(v)
    for k, (h, lo, hi) in st["pairs"].items():
        if k in acc["pairs"]:
            h0, lo0, hi0 = acc["pairs"][k]
            acc["pairs"][k] = (h0 + h, lo0 + lo, hi0 + hi)
        else:
            acc["pairs"][k] = (h, lo, hi)
    return acc


def _hist_quantiles(h, lo, hi, qs):
    """Quantiles of a binned distribution (bin centres), tails included as +-DZ_MAX."""
    total = h.sum() + lo + hi
    if total == 0:
        return [None] * len(qs)
    cum = np.cumsum(np.concatenate([[lo], h]))
    out = []
    for q in qs:
        k = q * total
        i = int(np.searchsorted(cum, k, side="left"))
        out.append(float(-DZ_MAX if i == 0 else (DZ_MAX if i > NB else -DZ_MAX + (i - 0.5) * DZ_BIN)))
    return out


def _report(recipe, grid, acc, layers_meta, pins, seconds, axes_mode):
    from topobathy import axes
    from topobathy.paint import paint_sha
    res2 = grid.res * grid.res / 1e6
    names = [L["name"] for L in recipe.layers]
    rep = dict(id=recipe.id, recipe_sha=recipe.sha, paint_sha=paint_sha(recipe.paint_paths()),
               built=time.strftime("%Y-%m-%dT%H:%M:%S"), seconds=round(seconds, 1),
               grid=dict(crs=str(grid.crs), res_m=grid.res, width=grid.width, height=grid.height, bounds=list(grid.bounds())),
               target=recipe.d.get("target"), as_of=recipe.d.get("as_of"),
               water_levels=[dict(layer=l, kind=k, step=i, level_m=round(v, 3)) for l, k, i, v in pins],
               composite=dict(cells=acc["cells"], finite_km2=round(acc["finite"] * res2, 4),
                              fallback_km2=round(acc["fallback"] * res2, 4),
                              source_km2={**{names[i]: round(float(acc["source"][i]) * res2, 4) for i in range(len(names))},
                                          "none": round(float(acc["source"][len(names)]) * res2, 4)}),
               layers=[], overlaps=[])
    for i, (spec, a) in enumerate(zip(recipe.layers, acc["layers"])):
        d = dict(name=spec["name"], date=spec.get("date", spec.get("year")), sigma_m=spec.get("sigma_m"),
                 valid_km2=round(a["valid"] * res2, 4), shown_km2=round(a["shown"] * res2, 4),
                 feathered_km2=round(a["partial"] * res2, 4))
        if "classes" in a:
            tot = max(sum(a["classes"]), 1)
            d["classes"] = {axes.CLASS_NAMES.get(c, str(c)): round(n / tot, 4) for c, n in enumerate(a["classes"]) if n}
        for k, v in a["info"].items():
            if k.startswith("_tiles_"):
                d[k[7:] + "_tile_median"] = round(float(np.median(v)), 3)
        rep["layers"].append(d)
    for (i, j, c), (h, lo, hi) in sorted(acc["pairs"].items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] if kv[0][2] is not None else -1)):
        n = int(h.sum() + lo + hi)
        if n < 100:
            continue
        p16, med, p84 = _hist_quantiles(h, lo, hi, [0.16, 0.5, 0.84])
        o = dict(upper=names[i], lower=names[j], n=n, km2=round(n * res2, 4),
                 median_dz_m=round(med, 3), nmad_m=round(0.5 * (p84 - p16), 3), p16_m=round(p16, 3), p84_m=round(p84, 3),
                 beyond_range=int(lo + hi))
        if c is not None:
            o["class"] = axes.CLASS_NAMES.get(c, str(c))
        di, dj = layers_meta[i].get("date"), layers_meta[j].get("date")
        if c is not None and np.isfinite(di) and np.isfinite(dj) and abs(di - dj) > 0.05:   # same-class pairs only
            o["dt_years"] = round(float(di - dj), 2)
            o["rate_m_per_yr"] = round(med / (di - dj), 3)     # the thinning / aggradation readout
        rep["overlaps"].append(o)
    return rep


class Outputs:
    """The streaming writers: tiled sparse GeoTIFFs on the full grid."""

    def __init__(self, recipe, grid, names, axes_mode, diag):
        self.recipe, self.grid, self.names, self.axes_mode, self.diag = recipe, grid, names, axes_mode, diag
        out = recipe.out_dir; out.mkdir(parents=True, exist_ok=True)
        (recipe.work_dir / "masks").mkdir(parents=True, exist_ok=True)
        extra = dict(SPARSE_OK="TRUE", BIGTIFF="YES")
        self.ds = {}
        def op(key, path, dtype, nodata):
            self.ds[key] = rasterio.open(path, "w", **grid.profile(dtype, nodata, **extra))
        self.raw = out / f"{recipe.id}_raw.tif"
        op("out", self.raw, "float32", -9999.0)
        op("source_index", out / "source_index.tif", "uint8", 255)
        for n in names[1:]:
            op(("mask", n), recipe.work_dir / "masks" / f"{n}.tif", "uint8", None)
            op(("auto", n), recipe.work_dir / "masks" / f"{n}_auto.tif", "uint8", None)
        if axes_mode:
            op("class_now", out / "class_now.tif", "uint8", 0)
            op("winner", out / "winner.tif", "int16", -1)
            op("date_now", out / "date_now.tif", "float32", -9999.0)
            op("sigma", out / "sigma.tif", "float32", -9999.0)
            op("fallback", out / "fallback.tif", "uint8", 0)
            if diag:
                for n in names:
                    op(("class", n), out / f"class_{n}.tif", "uint8", 0)
                    op(("sigma", n), out / f"sigma_{n}.tif", "float32", -9999.0)

    def write(self, win, out, layers):
        w = lambda key, arr: self.ds[key].write(arr, 1, window=win)
        w("out", np.where(np.isfinite(out), out, -9999).astype(np.float32))
        n = len(layers)
        idx = np.zeros(out.shape, np.uint8)
        for i, L in enumerate(layers[1:], start=1):
            idx[L["mask"] > 0.5] = i
        idx[~np.isfinite(out)] = 255
        w("source_index", idx)
        for L in layers[1:]:
            w(("mask", L["name"]), to_uint8(L["mask"]))
            w(("auto", L["name"]), to_uint8(L["auto"] if L["auto"] is not None else L["valid"].astype(np.float32)))
        if self.axes_mode:
            R = layers[0]["rules"]
            w("class_now", R["class_now"]); w("winner", R["winner"].astype(np.int16))
            w("date_now", np.where(np.isfinite(R["date_now"]), R["date_now"], -9999).astype(np.float32))
            win_i = np.clip(R["winner"], 0, n - 1)
            st = np.take_along_axis(R["sigma_total"], win_i[None].astype(np.intp), 0)[0]
            w("sigma", np.where((R["winner"] >= 0) & np.isfinite(st), st, -9999).astype(np.float32))
            w("fallback", R["fallback"].astype(np.uint8))
            if self.diag:
                for L in layers:
                    w(("class", L["name"]), L["cls"])
                    w(("sigma", L["name"]), np.where(np.isfinite(L["sigma"]), L["sigma"], -9999).astype(np.float32))

    def close(self):
        for d in self.ds.values():
            d.close()


def _tile_job(args):
    """One tile, in a worker process: build, return arrays + stats."""
    recipe_path, pins, box, verbose = args
    recipe = Recipe(recipe_path); apply_pins(recipe, pins)
    grid = recipe.grid()
    win = grid.window(*box)
    out, layers = build_window(recipe, grid, win, verbose=verbose)
    axes_mode = "rules" in layers[0]
    stats = _tile_stats(out, layers, axes_mode)
    keep = ("name", "mask", "auto", "valid", "cls", "sigma")
    slim = [{k: L[k] for k in keep if k in L} for L in layers]
    if axes_mode:
        R = layers[0]["rules"]
        slim[0]["rules"] = {k: R[k] for k in ("class_now", "winner", "date_now", "sigma_total", "fallback")}
    return box, out, slim, stats


def cog_translate(src, dst, metadata, env=None):
    """House archive format: COG, ZSTD, predictor, overviews rebuilt, -9999."""
    import subprocess
    from build_lidar import GDAL_BIN, clean_env
    env = env or clean_env()
    cmd = [str(GDAL_BIN / "gdal_translate"), "-of", "COG", "-q"]
    for k, v in metadata.items():
        cmd += ["-mo", f"{k}={v}"]
    cmd += ["-co", "COMPRESS=ZSTD", "-co", "LEVEL=9", "-co", "PREDICTOR=YES",
            "-co", "OVERVIEWS=IGNORE_EXISTING", "-co", "OVERVIEW_RESAMPLING=AVERAGE",
            "-co", "BIGTIFF=YES", "-co", "NUM_THREADS=ALL_CPUS", "-co", "SPARSE_OK=TRUE", str(src), str(dst)]
    subprocess.run(cmd, check=True, env=env)


def build_full(recipe, grid, tile=2048, jobs=4, diag=False, cog=True, verbose=False):
    if tile % TILE_BLOCK:
        sys.exit(f"--tile must be a multiple of {TILE_BLOCK}")
    t0 = time.time()
    names = [L["name"] for L in recipe.layers]
    axes_mode = bool(recipe.d.get("target"))
    print(f"[{recipe.id}] grid {grid.width}x{grid.height} @ {grid.res} m, {len(names)} layers, "
          f"{'axes' if axes_mode else 'mask'} recipe -> {recipe.work_dir}")
    pins = water_level_pins(recipe, grid)
    apply_pins(recipe, pins)
    boxes = [(int(w.row_off), int(w.row_off + w.height), int(w.col_off), int(w.col_off + w.width)) for w in tiles(grid, tile)]
    outputs = Outputs(recipe, grid, names, axes_mode, diag)
    acc = None; layers_meta = None
    jobs_ = [(str(recipe.path), pins, b, verbose) for b in boxes]
    try:
        if jobs > 1:
            import multiprocessing as mp
            ctx = mp.get_context("fork")
            with ctx.Pool(jobs) as pool:
                it = pool.imap_unordered(_tile_job, jobs_)
                for k, (box, out, layers, stats) in enumerate(it, 1):
                    outputs.write(grid.window(*box), out, layers); acc = _merge_stats(acc, stats)
                    print(f"  tile {k}/{len(boxes)} rows {box[0]}-{box[1]} cols {box[2]}-{box[3]}  finite {np.isfinite(out).mean():.3f}  {time.time() - t0:6.0f}s", flush=True)
        else:
            for k, j in enumerate(jobs_, 1):
                box, out, layers, stats = _tile_job(j)
                outputs.write(grid.window(*box), out, layers); acc = _merge_stats(acc, stats)
                print(f"  tile {k}/{len(boxes)} rows {box[0]}-{box[1]} cols {box[2]}-{box[3]}  finite {np.isfinite(out).mean():.3f}  {time.time() - t0:6.0f}s", flush=True)
    finally:
        outputs.close()
    from topobathy import axes
    layers_meta = [dict(date=axes.years(L.get("date", L.get("year")))) for L in recipe.layers]
    rep = _report(recipe, grid, acc, layers_meta, pins, time.time() - t0, axes_mode)
    final = recipe.out_dir / f"{recipe.id}.tif"
    if cog:
        t = time.time()
        cog_translate(outputs.raw, final, {"vertical_shift_m": 0, "composite_recipe": rep["recipe_sha"],
                                           "composite_paint": rep["paint_sha"], "composite_id": recipe.id})
        outputs.raw.unlink()
        rep["cog_seconds"] = round(time.time() - t, 1); rep["seconds"] = round(time.time() - t0, 1)
        print(f"  COG {final} ({final.stat().st_size / 1e6:.0f} MB) in {rep['cog_seconds']}s")
    else:
        rep["raw"] = str(outputs.raw)
    (recipe.out_dir / "report.json").write_text(json.dumps(rep, indent=1))
    print(f"  report {recipe.out_dir / 'report.json'}")
    km = rep["composite"]["source_km2"]
    print("  composite from: " + ", ".join(f"{k} {v:.2f} km2" for k, v in km.items()))
    for o in rep["overlaps"]:
        if "rate_m_per_yr" in o:
            print(f"  {o['upper']} - {o['lower']}{(' [' + o['class'] + ']') if 'class' in o else ''}: "
                  f"median {o['median_dz_m']:+.2f} m over {o['dt_years']:.1f} yr = {o['rate_m_per_yr']:+.2f} m/yr  "
                  f"(nmad {o['nmad_m']:.2f}, {o['km2']:.2f} km2)")
    print(f"done in {time.time() - t0:.0f}s")
    return rep


# ------------------------------------------------------------- commands

def cmd_build(a):
    recipe = Recipe(a.recipe)
    grid = recipe.grid()
    if not a.window:
        return 0 if build_full(recipe, grid, a.tile, a.jobs, a.diag, not a.no_cog, a.verbose) else 1
    win = parse_window(a.window, grid)
    print(f"[{recipe.id}] grid {grid.width}x{grid.height} @ {grid.res} m, window {win}")
    if a.pin:
        pins = water_level_pins(recipe, grid); apply_pins(recipe, pins)
    out, layers = build_window(recipe, grid, win)
    out_path = a.out or f"{recipe.id}_window.tif"
    write_tif(out_path, out, grid, win)
    print(f"wrote {out_path}  finite {np.isfinite(out).mean():.4f}")
    if a.masks_dir:
        d = Path(a.masks_dir); d.mkdir(parents=True, exist_ok=True)
        for L in layers[1:]:
            write_tif(d / f"{L['name']}.tif", to_uint8(L["mask"]), grid, win, "uint8", None)
        # which layer won: the top-most layer whose effective mask > 0.5 (base = 0)
        idx = np.zeros(out.shape, np.uint8)
        for i, L in enumerate(layers[1:], start=1):
            idx[L["mask"] > 0.5] = i
        idx[~np.isfinite(out)] = 255
        write_tif(d / "source_index.tif", idx, grid, win, "uint8", 255)
        print(f"masks + source_index in {d}")
    if a.diag_dir and "rules" in layers[0]:
        d = Path(a.diag_dir); d.mkdir(parents=True, exist_ok=True)
        R = layers[0]["rules"]
        write_tif(d / "class_now.tif", R["class_now"], grid, win, "uint8", 0)
        write_tif(d / "winner.tif", R["winner"].astype(np.int16), grid, win, "int16", -1)
        write_tif(d / "date_now.tif", R["date_now"], grid, win)
        for L in layers:
            write_tif(d / f"class_{L['name']}.tif", L["cls"], grid, win, "uint8", 0)
            write_tif(d / f"sigma_{L['name']}.tif", np.where(np.isfinite(L["sigma"]), L["sigma"], -9999), grid, win)
        json.dump({L["name"]: {k: v for k, v in L["info"].items() if not k.startswith("_")} for L in layers}, open(d / "info.json", "w"), indent=1)
        print(f"diagnostics in {d}")


def cmd_regress(a):
    """Rebuild a window and compare with the legacy merge output.  The
    legacy script had no nodata guard: wherever a mask was > 0 over a
    layer's -9999 (or the base was -9999 and nothing covered it), its
    output is contaminated and excluded from the strict comparison."""
    recipe = Recipe(a.recipe)
    grid = recipe.grid()
    win = parse_window(a.window, grid)
    print(f"[{recipe.id}] regression window {win}")
    out, layers = build_window(recipe, grid, win)
    with rasterio.open(a.against) as ds:
        ref = ds.read(1, window=Window(win.col_off, win.row_off, win.width, win.height)).astype(np.float32)
    # contamination: any masked-in layer invalid there, or base invalid and never fully replaced
    contaminated = ~layers[0]["valid"]
    for L in layers[1:]:
        # legacy used the raw painted mask (auto), not the valid-guarded one
        raw = L["auto"] if L["auto"] is not None else L["mask"]
        contaminated |= (raw > 0) & ~L["valid"]
    ok = ~contaminated & np.isfinite(out) & np.isfinite(ref)
    d = out[ok] - ref[ok]
    print(f"cells {ok.size}  clean {ok.sum()} ({ok.mean():.4f})  contaminated {contaminated.sum()}")
    print(f"clean: max|d| {np.abs(d).max():.6f}  rms {np.sqrt((d**2).mean()):.6f}  "
          f"exact {(d == 0).mean():.4f}  |d|<1e-3 {(np.abs(d) < 1e-3).mean():.4f}")
    # what the legacy output holds in contaminated cells
    if contaminated.any():
        rc = ref[contaminated]
        print(f"contaminated legacy values: -9999-ish {(rc < -9000).mean():.3f}  "
              f"finite&plausible {((rc > -9000) & np.isfinite(rc)).mean():.3f}  nan {np.isnan(rc).mean():.3f}")
        rn = out[contaminated]
        print(f"new output there: finite {np.isfinite(rn).mean():.3f} (guarded fold shows lower layers / NaN)")
    if a.out:
        write_tif(a.out, out - ref, grid, win)
        print(f"wrote difference {a.out}")
    return 0 if np.abs(d).max() < 1e-3 else 1


def cmd_masks(a):
    recipe = Recipe(a.recipe)
    grid = recipe.grid()
    win = parse_window(a.window, grid)
    d = Path(a.out_dir); d.mkdir(parents=True, exist_ok=True)
    for spec in recipe.layers[1:]:
        z, valid = read_layer(spec, recipe, grid, win)
        m, auto, _ = layer_mask(spec, recipe, grid, win, valid)
        write_tif(d / f"{spec['name']}_auto.tif", to_uint8(auto), grid, win, "uint8", None)
        write_tif(d / f"{spec['name']}.tif", to_uint8(m), grid, win, "uint8", None)
        print(f"  {spec['name']}: auto + effective written")


def cmd_import_mask(a):
    """An externally edited 8-bit mask (Photoshop, QGIS, GIMP) becomes paint:
    wherever it differs from the last build's auto mask the edited value is
    painted with alpha 1; where it agrees, any earlier paint is cleared (the
    editor saw the effective mask and kept it).  `--align pixel` ignores the
    file's georeference (Photoshop strips it) and stacks by row/column on
    the composite grid, with a size check."""
    from topobathy.masks import read_mask_file
    from topobathy.paint import write_paint
    recipe = Recipe(a.recipe); grid = recipe.grid()
    if a.layer not in [L["name"] for L in recipe.layers[1:]]:
        sys.exit(f"no such layer (or it is the base): {a.layer}")
    auto_path = recipe.work_dir / "masks" / f"{a.layer}_auto.tif"
    if not auto_path.exists():
        sys.exit(f"no auto mask from a full build at {auto_path}; run `build` first")
    with rasterio.open(a.edited) as ds:
        if a.align == "pixel" and (ds.width, ds.height) != (grid.width, grid.height):
            sys.exit(f"--align pixel needs a {grid.width}x{grid.height} image; got {ds.width}x{ds.height}")
    tol = a.tol / 255.0
    paint_path = recipe.paint_path(a.layer)
    changed = cleared = 0
    for win in tiles(grid, 2048):
        edited, covered = read_mask_file(a.edited, grid, win, a.align)
        if not covered.any():
            continue
        auto, _ = read_mask_file(auto_path, grid, win, "geo")
        differ = covered & (np.abs(edited - auto) > tol)
        value = np.where(differ, edited, 0).astype(np.float32)
        alpha = differ.astype(np.float32)
        # keep whatever paint exists outside the edited file's coverage
        if paint_path.exists():
            from topobathy.masks import read_paint
            v0, a0 = read_paint(paint_path, grid, win)
            value = np.where(covered, value, v0); alpha = np.where(covered, alpha, a0)
            cleared += int((covered & (a0 > 0) & ~differ).sum())
        write_paint(paint_path, grid, win, value, alpha)
        changed += int(differ.sum())
    print(f"{a.layer}: {changed} cells painted from {Path(a.edited).name}, {cleared} earlier paint cells cleared -> {paint_path}")
    print("rebuild (`composite.py build`) to apply")


def cmd_export_mask(a):
    import shutil
    recipe = Recipe(a.recipe)
    src = recipe.work_dir / "masks" / (f"{a.layer}.tif" if a.which == "effective" else f"{a.layer}_auto.tif")
    if not src.exists():
        sys.exit(f"{src} missing; run a full `build` first")
    shutil.copyfile(src, a.out)
    print(f"{a.which} mask of {a.layer} -> {a.out}  (8-bit, 0 = hidden, 255 = shown; edit and `import-mask` it back)")


def dataset_entry(recipe, grid, rep=None):
    """The datasets.json entry for a built composite: archive_mode copy of
    the COG, dev_only until Hig lifts it."""
    from rasterio.warp import transform_bounds
    import math
    b = grid.bounds()
    w, s, e, n = transform_bounds(grid.crs, "EPSG:4326", *b, densify_pts=21)
    lat = 0.5 * (s + n)
    ground_z0 = 2 * math.pi * 6378137 / 256 * math.cos(math.radians(lat))
    max_zoom = int(math.ceil(math.log2(ground_z0 / grid.res)))
    d = recipe.d
    layers = ", ".join(f"{L['name']} ({L.get('date', L.get('year', 'undated'))})" for L in d["layers"])
    rep = rep or (json.loads((recipe.out_dir / "report.json").read_text()) if (recipe.out_dir / "report.json").exists() else {})
    km = rep.get("composite", {}).get("source_km2", {})
    src_note = "; ".join(f"{k} {v:.1f} km2" for k, v in km.items() if v) if km else ""
    as_of = d.get("as_of") or rep.get("as_of")
    year = int(str(as_of)[:4]) if as_of else None
    epsg = grid.crs.to_epsg()
    entry = dict(id=d.get("dataset_id", recipe.id),
                 title=d.get("title", f"{recipe.id}: topobathy composite"),
                 src=str(recipe.out_dir / f"{recipe.id}.tif"),
                 target_epsg=epsg, native_res_m=grid.res, center_lat=round(lat, 3),
                 min_zoom=8, max_zoom=max_zoom, year=year,
                 product="Topobathy composite", archive_mode="copy",
                 vertical_datum=d.get("vertical_datum", "NAVD88 (as the sources; see notes)"),
                 region=d.get("region", ""),
                 source=d.get("source", f"landslidescience.org composite of: {layers}"),
                 dev_only=True,
                 notes=(d.get("note", "") + f" COMPOSITE built {rep.get('built', '?')} by tools/lidar/composite.py from "
                        f"tools/lidar/composites/{recipe.id}/recipe.json (sha {rep.get('recipe_sha', recipe.sha)}, paint "
                        f"{rep.get('paint_sha', '-')}); fold order {layers}. "
                        f"Target {d.get('target', 'mask recipe')} as of {as_of}. "
                        + (f"Composite area by source: {src_note}. " if src_note else "")
                        + "source_index.tif and report.json beside the COG say which layer won where and what was decided."))
    if epsg is None:
        entry["target_epsg"] = None
        entry["notes"] += f" GRID CRS has no EPSG code: {grid.crs.to_wkt()[:80]}..."
    return entry


def cmd_publish(a):
    recipe = Recipe(a.recipe); grid = recipe.grid()
    cog = recipe.out_dir / f"{recipe.id}.tif"
    if not cog.exists():
        sys.exit(f"no composite at {cog}; run `build` first")
    entry = dataset_entry(recipe, grid)
    print(json.dumps(entry, indent=1))
    if a.write:
        manifest = Path(__file__).resolve().parent / "datasets.json"
        data = json.loads(manifest.read_text())
        data["datasets"] = [e for e in data["datasets"] if e["id"] != entry["id"]] + [entry]
        manifest.write_text(json.dumps(data, indent=2) + "\n")
        print(f"\nwrote {entry['id']} into {manifest} (dev_only). Next, on the hosting side:\n"
              f"  python3 tools/lidar/build_lidar.py {entry['id']}\n"
              f"  LIDAR_PMTILES_PUBLIC_BASE=/lidar/pmtiles LIDAR_COG_PUBLIC_BASE=/lidar/cog python3 tools/lidar/make_catalog.py --include-dev")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build"); b.add_argument("recipe"); b.add_argument("--window"); b.add_argument("--out"); b.add_argument("--masks-dir"); b.add_argument("--diag-dir")
    b.add_argument("--tile", type=int, default=2048); b.add_argument("--jobs", type=int, default=4)
    b.add_argument("--diag", action="store_true", help="full build: also write per-layer class/sigma rasters")
    b.add_argument("--no-cog", action="store_true", help="full build: leave the raw tiled GeoTIFF, skip the COG")
    b.add_argument("--pin", action="store_true", help="window build: pin water levels from the whole-grid pre-pass, as a full build does")
    b.add_argument("--verbose", action="store_true", help="full build: per-layer lines for every tile")
    r = sp.add_parser("regress"); r.add_argument("recipe"); r.add_argument("--against", required=True); r.add_argument("--window"); r.add_argument("--out")
    m = sp.add_parser("masks"); m.add_argument("recipe"); m.add_argument("--window"); m.add_argument("--out-dir", required=True)
    im = sp.add_parser("import-mask"); im.add_argument("recipe"); im.add_argument("edited"); im.add_argument("--layer", required=True)
    im.add_argument("--align", default="geo", choices=["geo", "pixel"]); im.add_argument("--tol", type=float, default=1.0, help="difference in 8-bit levels that counts as an edit")
    ex = sp.add_parser("export-mask"); ex.add_argument("recipe"); ex.add_argument("--layer", required=True); ex.add_argument("--out", required=True)
    ex.add_argument("--which", default="effective", choices=["effective", "auto"])
    pb = sp.add_parser("publish"); pb.add_argument("recipe"); pb.add_argument("--write", action="store_true", help="add the entry to datasets.json")
    a = p.parse_args()
    sys.exit({"build": cmd_build, "regress": cmd_regress, "masks": cmd_masks, "import-mask": cmd_import_mask,
              "export-mask": cmd_export_mask, "publish": cmd_publish}[a.cmd](a) or 0)


if __name__ == "__main__":
    main()
