#!/opt/anaconda3/bin/python3
"""Topobathy composite CLI (phase 1 mockup).

  composite.py build   recipe.json --window r0,r1,c0,c1 [--out out.tif] [--masks-dir DIR]
  composite.py regress recipe.json --against legacy.tif --window r0,r1,c0,c1
  composite.py masks   recipe.json --window r0,r1,c0,c1 --out-dir DIR

`--window` is in grid pixels (rows, cols); omit for the full grid (slow at
30000 x 20000 - this mockup holds the window in memory).
"""
import argparse, json, sys, time
from pathlib import Path
import numpy as np
import rasterio
from rasterio.windows import Window

sys.path.insert(0, str(Path(__file__).resolve().parent))
from topobathy import Recipe, fold
from topobathy.stack import read_layer
from topobathy.masks import layer_mask, to_uint8


def parse_window(s, grid):
    if not s:
        return grid.full()
    r0, r1, c0, c1 = (int(v) for v in s.split(","))
    return grid.window(r0, r1, c0, c1)


def build_window(recipe, grid, win, verbose=True, halo=None):
    """Returns (out, per-layer list of dicts with z, valid, mask, auto).

    Masks are computed on the window plus a halo (default: the recipe's
    `halo_px`, else 128) and cropped back, so erode / feather / despeckle
    never see the window edge as a layer edge."""
    from topobathy.fold import fold_step
    if halo is None:
        halo = int(recipe.d.get("halo_px", 128))
    r0, c0 = int(win.row_off), int(win.col_off)
    r1, c1 = r0 + int(win.height), c0 + int(win.width)
    big = grid.window(r0 - halo, r1 + halo, c0 - halo, c1 + halo)
    cr, cc = r0 - int(big.row_off), c0 - int(big.col_off)          # crop offsets
    crop = (slice(cr, cr + r1 - r0), slice(cc, cc + c1 - c0))
    out, layers = _build_window(recipe, grid, big, verbose)
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


def _build_window(recipe, grid, win, verbose=True):
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
            L["info"]["_cls"] = L["cls"]
            for step in L["spec"].get("sigma", []):          # give raster steps the grid/window to read onto
                if "raster" in step:
                    step["raster"]["path"] = str(recipe.resolve(step["raster"]["path"])); step["raster"]["_grid"] = grid; step["raster"]["_win"] = win
            L["sigma"] = axes.sigma_raster(L["spec"], L["z"], L["valid"], grid.res, L["info"])
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


def cmd_build(a):
    recipe = Recipe(a.recipe)
    grid = recipe.grid()
    win = parse_window(a.window, grid)
    print(f"[{recipe.id}] grid {grid.width}x{grid.height} @ {grid.res} m, window {win}")
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


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build"); b.add_argument("recipe"); b.add_argument("--window"); b.add_argument("--out"); b.add_argument("--masks-dir"); b.add_argument("--diag-dir")
    r = sp.add_parser("regress"); r.add_argument("recipe"); r.add_argument("--against", required=True); r.add_argument("--window"); r.add_argument("--out")
    m = sp.add_parser("masks"); m.add_argument("recipe"); m.add_argument("--window"); m.add_argument("--out-dir", required=True)
    a = p.parse_args()
    sys.exit({"build": cmd_build, "regress": cmd_regress, "masks": cmd_masks}[a.cmd](a) or 0)


if __name__ == "__main__":
    main()
