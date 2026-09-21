#!/opt/anaconda3/bin/python3
"""Local mask tool: fast interaction with one composite window.

  server.py recipe.json --window r0,r1,c0,c1 [--port 8765]

Runs on the Mac against the mounted stack; never on the droplet.  The
paint raster is the record: every fill lands in memory as (value, alpha)
per layer and `save` writes masks/<layer>_paint.tif in the recipe's work
directory, where `composite.py build` picks it up.  Selections and undo
are session state only.
"""
import argparse, io, sys, threading, time
from pathlib import Path
import numpy as np
from PIL import Image
from scipy import ndimage
from flask import Flask, jsonify, request, send_file, Response

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from topobathy import Recipe
from topobathy.fold import fold_step
from topobathy.masks import feather as feather_mask, apply_paint, to_uint8, despeckle, smooth, OPS
from topobathy import axes as AX
from composite import build_window, parse_window, write_tif

app = Flask(__name__)
LOCK = threading.Lock()
S = None            # the session


# ------------------------------------------------------------- session

class Session:
    def __init__(self, recipe_path, window):
        self.recipe = Recipe(recipe_path)
        self.grid = self.recipe.grid()
        self.win = parse_window(window, self.grid)
        self.sub = self.grid.sub(self.win)
        t = time.time()
        self.out, self.layers = build_window(self.recipe, self.grid, self.win, verbose=True)
        self.names = [L["name"] for L in self.layers]
        h, w = self.out.shape
        self.shape = (h, w)
        # paint: start from whatever the build already folded in (the recipe's paint files)
        self.paint = {}
        for L in self.layers[1:]:
            p = self.recipe.paint_path(L["name"])
            if p.exists():
                from topobathy.masks import read_paint
                v, a = read_paint(p, self.grid, self.win)
            else:
                v, a = np.zeros(self.shape, np.float32), np.zeros(self.shape, np.float32)
            self.paint[L["name"]] = [v, a]
        self.reference = None
        ref = self.recipe.d.get("reference") or self.recipe.d.get("legacy_output")
        if ref:
            from topobathy.stack import read_pixel, read_geo
            rp = self.recipe.resolve(ref)
            if rp.exists():
                align = self.recipe.d.get("reference_align", self.recipe.layers[0].get("align", "geo"))
                z, v = (read_pixel if align == "pixel" else read_geo)(rp, self.grid, self.win)
                self.reference = z
                print(f"reference raster loaded: {rp.name} ({align})")
        self.has_axes = "cls" in self.layers[0]
        self.target = self.recipe.d.get("target")
        # per-axis hand paint: class (value = class code / 8) and sigma (value = band index / 8)
        self.cpaint = {L["name"]: [np.zeros(self.shape, np.float32), np.zeros(self.shape, np.float32)] for L in self.layers}
        self.spaint = {L["name"]: [np.zeros(self.shape, np.float32), np.zeros(self.shape, np.float32)] for L in self.layers}
        self.sel = np.zeros(self.shape, bool)
        # session-only view toggles: dem = whole layer, auto = generated/file mask, paint = hand paint
        self.enabled = {L["name"]: {"dem": True, "auto": True, "paint": True} for L in self.layers}
        self.undo = []
        self.version = 0
        self.below = self._folds_below()
        allz = np.concatenate([L["z"][np.isfinite(L["z"])][::97] for L in self.layers])
        self.zrange = (float(np.percentile(allz, 1)), float(np.percentile(allz, 99)))
        print(f"session ready in {time.time() - t:.1f}s: {w}x{h}, layers {self.names}")

    def effective(self, L):
        en = self.enabled[L["name"]]
        if not en["dem"]:
            return np.zeros(self.shape, np.float32)
        if L["auto"] is None:
            return L["valid"].astype(np.float32)
        auto = L["auto"] if en["auto"] else L["valid"].astype(np.float32)   # auto off = plain footprint
        v, a = self.paint[L["name"]]
        if not en["paint"]:
            a = np.zeros_like(a)
        return apply_paint(auto, v, a) * L["valid"]

    def layer_z(self, L):
        """The base has no mask; toggling it off empties it."""
        if L is self.layers[0] and not self.enabled[L["name"]]["dem"]:
            return np.full(self.shape, np.nan, np.float32)
        return L["z"]

    def _folds_below(self):
        """out_below[i] = fold of layers 0..i-1, for the difference view."""
        below = {}
        out = None
        for L in self.layers:
            below[L["name"]] = None if out is None else out.copy()
            out = self.layer_z(L).copy() if out is None else fold_step(out, L["z"], self.effective(L))
        return below

    SIGMA_BANDS = [0.1, 0.5, 2.0, 99.0]      # good / ok / poor / veto

    def eff_class(self, L):
        v, a = self.cpaint[L["name"]]
        c = L["cls"].copy()
        on = a > 0.5
        c[on] = np.rint(v[on] * 8).astype(np.uint8)
        return c

    def eff_sigma(self, L):
        v, a = self.spaint[L["name"]]
        sg = L["sigma"].copy()
        on = a > 0.5
        band = np.clip(np.rint(v[on] * 8), 0, 3).astype(int)
        sg[on] = np.array(self.SIGMA_BANDS, np.float32)[band]
        return sg

    def recompute_rules(self):
        """Class or sigma changed: rerun state_rules and the post ops -> new auto masks."""
        if not self.has_axes:
            return
        obs = [dict(name=L["name"], z=L["z"], valid=L["valid"], cls=self.eff_class(L), sigma=self.eff_sigma(L), date=L["date"],
                    **({"class_sigma_max": L["class_sigma_max"]} if "class_sigma_max" in L else {})) for L in self.layers]
        d = self.recipe.d
        if d.get("rules", "sequential") == "global":
            rules = AX.state_rules(obs, self.target, d.get("as_of"), d.get("rates"), d.get("sigma_max", 5.0), d.get("lowest_tol_m", 0.2))
        else:
            rules = AX.state_rules_sequential(obs, self.target, d.get("as_of"), d.get("rates"), d.get("sigma_max", 5.0), d.get("lowest"), d.get("margin_m", 0.0))
        self.layers[0]["rules"] = rules
        post = d.get("rules_post", [{"despeckle": {"min_area_m2": 2000}}, {"feather": {"width_m": 20}}])
        for i, L in enumerate(self.layers):
            L["st"] = rules["sigma_total"][i]; L["gate"] = rules["gate"][i]
            if i == 0: continue
            m = rules["masks"][i]
            for step in post:
                (name, kw), = step.items()
                if name == "despeckle": m = despeckle(m, kw["min_area_m2"], self.grid.res)
                elif name == "smooth": m = smooth(m, kw["size_m"], self.grid.res)
                elif name in OPS: m = OPS[name](m, kw.get("width_m", kw.get("sigma_m", kw.get("dist_m"))), self.grid.res)
            L["auto"] = m.astype(np.float32)

    def refold(self):
        out = None
        for L in self.layers:
            out = self.layer_z(L).copy() if out is None else fold_step(out, L["z"], self.effective(L))
        self.out = out
        self.below = self._folds_below()
        self.version += 1

    # ----- rasters the client can look at / wand on
    def ref(self, kind, layer):
        L = self.layers[self.names.index(layer)] if layer else None
        if kind == "out":
            return self.out
        if kind == "z":
            return L["z"]
        if kind == "diff":        # this layer minus what lies below it
            b = self.below[layer]
            return L["z"] - b if b is not None else L["z"] * np.nan
        if kind == "mask":
            return self.effective(L)
        if kind == "auto":
            return L["auto"]
        if kind == "sigma":
            return self.eff_sigma(L)
        if kind == "rough":       # local roughness of the layer (m): flat = water, snow, ice; wand with tol ~0.05-0.2
            return AX.roughness(L["z"], L["valid"], self.grid.res)
        if kind == "slope":       # slope in degrees
            gy, gx = np.gradient(np.nan_to_num(L["z"], nan=0.0), self.grid.res)
            return np.degrees(np.arctan(np.hypot(gx, gy))).astype(np.float32)
        if kind == "sigma_total":
            return L["st"]
        if kind == "refdiff":     # composite minus the reference raster (e.g. the hand-painted merge)
            if self.reference is None:
                raise KeyError("no reference")
            return self.out - self.reference
        raise KeyError(kind)


# -------------------------------------------------------------- images

def hillshade(z, res=1.0, zf=1.0, az=315.0, alt=45.0):
    zz = np.where(np.isfinite(z), z, np.nan)
    zz = np.nan_to_num(zz, nan=np.nanmean(zz) if np.isfinite(zz).any() else 0.0)
    gy, gx = np.gradient(zz * zf, res)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    a, h = np.radians(az), np.radians(alt)
    s = np.sin(h) * np.cos(slope) + np.cos(h) * np.sin(slope) * np.cos(a - np.pi / 2 - aspect)
    img = np.clip(s * 255, 0, 255).astype(np.uint8)
    img[~np.isfinite(z)] = 0
    return img


def png(arr_rgb_or_l):
    im = Image.fromarray(arr_rgb_or_l)
    buf = io.BytesIO(); im.save(buf, "PNG", compress_level=1); buf.seek(0)
    return send_file(buf, mimetype="image/png")


def diverging(d, s):
    """blue (−s) .. white .. red (+s); NaN dark."""
    t = np.clip(d / s, -1, 1)
    r = np.where(t > 0, 255, 255 * (1 + t)); g = 255 * (1 - np.abs(t)); b = np.where(t < 0, 255, 255 * (1 - t))
    rgb = np.stack([r, g, b], -1).astype(np.uint8)
    rgb[~np.isfinite(d)] = 40
    return rgb


def tint(alpha01, rgb):
    a = (np.clip(alpha01, 0, 1) * 255).astype(np.uint8)
    out = np.zeros(alpha01.shape + (4,), np.uint8)
    out[..., 0], out[..., 1], out[..., 2] = rgb
    out[..., 3] = a
    return out


# ------------------------------------------------------------- routes

@app.get("/")
def index():
    return Response((HERE / "index.html").read_text(), mimetype="text/html")


@app.get("/api/state")
def state():
    return jsonify(id=S.recipe.id, width=S.shape[1], height=S.shape[0], res=S.grid.res,
                   window=[int(S.win.row_off), int(S.win.row_off + S.win.height),
                           int(S.win.col_off), int(S.win.col_off + S.win.width)],
                   zrange=S.zrange, has_reference=S.reference is not None,
                   has_axes=S.has_axes, target=S.target, as_of=S.layers[0]["rules"]["as_of"] if S.has_axes else None,
                   classes=AX.CLASS_NAMES, class_rgb=AX.CLASS_RGB, sigma_bands=S.SIGMA_BANDS,
                   reference=str(S.recipe.d.get("reference") or S.recipe.d.get("legacy_output") or ""),
                   layers=[dict(name=L["name"], box=L["box"], valid=float(L["valid"].mean()),
                                enabled=S.enabled[L["name"]],
                                date=(None if not S.has_axes or not np.isfinite(L["date"]) else round(float(L["date"]), 2)),
                                sigma_uniform=(bool(np.all(L["sigma"][L["valid"]] == L["sigma"][L["valid"]].flat[0])) if S.has_axes and L["valid"].any() else True),
                                class_uniform=(bool(len(np.unique(L["cls"][L["valid"]])) <= 1) if S.has_axes and L["valid"].any() else True),
                                cpainted=float((S.cpaint[L["name"]][1] > 0).mean()), spainted=float((S.spaint[L["name"]][1] > 0).mean()),
                                zmin=float(np.nanmin(L["z"])) if L["valid"].any() else None,
                                zmax=float(np.nanmax(L["z"])) if L["valid"].any() else None,
                                shown=float((S.effective(L) > 0.5).mean()),
                                painted=float((S.paint[L["name"]][1] > 0).mean()) if L["name"] in S.paint else 0.0)
                           for L in S.layers],
                   version=S.version, sel=int(S.sel.sum()), undo=len(S.undo))


@app.get("/img/<kind>")
def img(kind):
    layer = request.args.get("layer") or None
    with LOCK:
        if kind == "shade":
            src = S.out if (layer in (None, "out")) else S.ref("z", layer)
            return png(hillshade(src, S.grid.res, float(request.args.get("zf", 1))))
        if kind == "elev":         # elevation ramp clipped to a band; outside the band dark
            src = S.out if (layer in (None, "out")) else S.ref("z", layer)
            lo = float(request.args.get("lo", S.zrange[0])); hi = float(request.args.get("hi", S.zrange[1]))
            t = np.clip((src - lo) / max(hi - lo, 1e-6), 0, 1)
            # viridis-like 5-stop ramp, so a narrow band still reads
            stops = np.array([[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]], np.float32)
            pos = t * 4; i = np.clip(np.floor(pos).astype(int), 0, 3); f = (pos - i)[..., None]
            rgb = (stops[i] * (1 - f) + stops[i + 1] * f).astype(np.uint8)
            outside = ~np.isfinite(src) | (src < lo) | (src > hi)
            rgb[outside] = (25, 25, 28)
            return png(rgb)
        if kind == "diff":
            return png(diverging(S.ref("diff", layer), float(request.args.get("s", 2))))
        if kind == "refdiff":
            return png(diverging(S.ref("refdiff", None), float(request.args.get("s", 2))))
        if kind == "mask":
            return png(tint(S.ref("mask", layer) * 0.55, (255, 140, 0)))
        if kind == "class":
            L = S.layers[S.names.index(layer)]; c = S.eff_class(L)
            rgb = np.zeros(c.shape + (3,), np.uint8)
            for code, col in AX.CLASS_RGB.items(): rgb[c == code] = col
            rgb[~L["valid"]] = NODATA_RGB
            return png(rgb)
        if kind == "classnow":
            c = S.layers[0]["rules"]["class_now"]; rgb = np.zeros(c.shape + (3,), np.uint8)
            for code, col in AX.CLASS_RGB.items(): rgb[c == code] = col
            return png(rgb)
        if kind == "sigma":
            L = S.layers[S.names.index(layer)]
            sg = S.eff_sigma(L) if request.args.get("which") != "total" else L["st"]
            g = np.clip(np.log10(np.maximum(sg, 0.01)) + 2, 0, 4) / 4 * 255   # 0.01 m black .. 100 m white
            rgb = np.repeat(g.astype(np.uint8)[..., None], 3, -1); rgb[~L["valid"]] = NODATA_RGB
            return png(rgb)
        if kind == "paintbw":
            v, a = S.paint[layer]
            return png(paint_rgb(v, a))
        if kind == "maskbw":       # the mask as the document: plain grey, nodata hatched dark
            m = S.ref("auto" if request.args.get("which") == "auto" else "mask", layer)
            g = (np.clip(m, 0, 1) * 255).astype(np.uint8)
            rgb = np.repeat(g[..., None], 3, -1)
            rgb[~S.layers[S.names.index(layer)]["valid"]] = NODATA_RGB
            return png(rgb)
        if kind == "paint":
            v, a = S.paint[layer]
            rgb = np.where(v[..., None] > 0.5, np.array([0, 200, 80]), np.array([220, 30, 60])).astype(np.uint8)
            out = np.dstack([rgb, (a * 160).astype(np.uint8)]); return png(out)
        if kind == "sel":
            edge = S.sel & ~ndimage.binary_erosion(S.sel, iterations=2)
            a = np.where(edge, 1.0, np.where(S.sel, 0.35, 0.0))
            return png(tint(a, (0, 220, 255)))
        if kind == "valid":
            return png(tint(S.layers[S.names.index(layer)]["valid"] * 0.4, (120, 120, 255)))
    return ("unknown image", 404)


NODATA_RGB = (70, 40, 110)      # one colour for nodata everywhere


def thumb_dem(z, mask, size, zrange):
    """Grey ramp for elevation on the shared range, nodata in one colour,
    the same orange tint where the mask lets the layer through."""
    f = max(1, int(np.ceil(max(z.shape) / size)))
    zz = z[::f, ::f]; mm = None if mask is None else mask[::f, ::f]
    lo, hi = zrange
    g = np.clip((zz - lo) / max(hi - lo, 1e-6), 0, 1) * 235 + 10
    rgb = np.repeat(g[..., None], 3, -1)
    if mm is not None:
        t = (mm * 0.55)[..., None]
        rgb = rgb * (1 - t) + np.array([255, 140, 0]) * t
    rgb = rgb.astype(np.uint8)
    rgb[~np.isfinite(zz)] = NODATA_RGB
    return rgb


def thumb_mask(m, size):
    f = max(1, int(np.ceil(max(m.shape) / size)))
    return (np.clip(m[::f, ::f], 0, 1) * 255).astype(np.uint8)


def paint_rgb(v, a, f=1):
    """Paint as the record: value as grey where alpha > 0, transparent-dark
    (checker) where there is no opinion."""
    vv, aa = v[::f, ::f], a[::f, ::f]
    h, w = vv.shape
    yy, xx = np.mgrid[0:h, 0:w]
    checker = np.where(((yy // 8) + (xx // 8)) % 2 == 0, 52, 68).astype(np.float32)
    g = np.clip(vv, 0, 1) * 255
    out = checker * (1 - aa) + g * aa
    return np.repeat(out.astype(np.uint8)[..., None], 3, -1)


@app.get("/thumb/<kind>")
def thumb(kind):
    size = int(request.args.get("size", 128)); layer = request.args.get("layer")
    with LOCK:
        if kind == "composite":
            return png(thumb_dem(S.out, None, size, S.zrange))
        L = S.layers[S.names.index(layer)]
        if kind == "dem":
            m = None if L["auto"] is None else S.effective(L)
            return png(thumb_dem(L["z"], m, size, S.zrange))
        if kind == "mask":
            m = L["valid"].astype(np.float32) if L["auto"] is None else S.effective(L)
            return png(thumb_mask(m, size))
        if kind == "paint":
            v, a = S.paint[layer]; f = max(1, int(np.ceil(max(v.shape) / size)))
            return png(paint_rgb(v, a, f))
        f = max(1, int(np.ceil(max(S.shape) / size)))
        if kind == "class":
            c = S.eff_class(L)[::f, ::f]; rgb = np.zeros(c.shape + (3,), np.uint8)
            for code, col in AX.CLASS_RGB.items(): rgb[c == code] = col
            rgb[~L["valid"][::f, ::f]] = NODATA_RGB
            return png(rgb)
        if kind == "sigma":
            sg = S.eff_sigma(L)[::f, ::f]
            g = np.clip(np.log10(np.maximum(sg, 0.01)) + 2, 0, 4) / 4 * 255
            rgb = np.repeat(g.astype(np.uint8)[..., None], 3, -1); rgb[~L["valid"][::f, ::f]] = NODATA_RGB
            return png(rgb)
    return ("unknown thumb", 404)


def _combine(new, mode):
    if mode == "add": S.sel |= new
    elif mode == "sub": S.sel &= ~new
    else: S.sel = new


@app.get("/api/range")
def zrange_view():
    r0, r1, c0, c1 = (int(float(request.args[k])) for k in ("r0", "r1", "c0", "c1"))
    sub = S.out[max(r0, 0):max(r1, 0), max(c0, 0):max(c1, 0)]
    v = sub[np.isfinite(sub)]
    if v.size == 0:
        return jsonify(lo=None, hi=None)
    lo, hi = np.percentile(v, [1, 99])
    return jsonify(lo=round(float(lo), 1), hi=round(float(hi), 1))


@app.get("/api/profile")
def profile():
    """Elevations along a line, for every layer, the composite and the reference."""
    r0, c0, r1, c1 = (float(request.args[k]) for k in ("r0", "c0", "r1", "c1"))
    n = int(request.args.get("n", 400))
    t = np.linspace(0, 1, n)
    rr = np.clip(np.rint(r0 + (r1 - r0) * t).astype(int), 0, S.shape[0] - 1)
    cc = np.clip(np.rint(c0 + (c1 - c0) * t).astype(int), 0, S.shape[1] - 1)
    dist = (t * np.hypot(r1 - r0, c1 - c0) * S.grid.res).tolist()
    def series(a):
        v = a[rr, cc].astype(float); return [None if not np.isfinite(x) else round(x, 2) for x in v]
    with LOCK:
        out = dict(dist=dist, out=series(S.out), layers={L["name"]: series(L["z"]) for L in S.layers})
        if S.has_axes:
            out["winner"] = S.layers[0]["rules"]["winner"][rr, cc].astype(int).tolist()
        if S.reference is not None:
            out["reference"] = series(S.reference)
    return jsonify(out)


@app.get("/api/why")
def why():
    """The whole decision at one cell, per layer."""
    r, c = int(request.args["row"]), int(request.args["col"])
    if not (0 <= r < S.shape[0] and 0 <= c < S.shape[1]):
        return jsonify(error="outside"), 400
    rows = []
    with LOCK:
        R = S.layers[0].get("rules")
        for i, L in enumerate(S.layers):
            z = float(L["z"][r, c]) if L["valid"][r, c] else None
            row = dict(name=L["name"], z=None if z is None else round(z, 2), shown=round(float(S.effective(L)[r, c]), 2))
            if S.has_axes:
                row.update(cls=AX.CLASS_NAMES[int(S.eff_class(L)[r, c])], gate=float(L["gate"][r, c]),
                           sigma_q=round(float(S.eff_sigma(L)[r, c]), 2) if L["valid"][r, c] else None,
                           sigma_total=round(float(L["st"][r, c]), 2) if L["valid"][r, c] else None,
                           date=L["date"] if np.isfinite(L["date"]) else None)
            rows.append(row)
        out = dict(row=r, col=c, out=round(float(S.out[r, c]), 2) if np.isfinite(S.out[r, c]) else None, layers=rows)
        if S.has_axes:
            out.update(class_now=AX.CLASS_NAMES[int(R["class_now"][r, c])], date_now=round(float(R["date_now"][r, c]), 2),
                       winner=int(R["winner"][r, c]))
        if S.reference is not None and np.isfinite(S.reference[r, c]):
            out["reference"] = round(float(S.reference[r, c]), 2)
    return jsonify(out)


@app.post("/api/wand")
def wand():
    j = request.json
    r, c = int(j["row"]), int(j["col"])
    ref = S.ref(j.get("ref", "diff"), j.get("layer"))
    tol = float(j.get("tol", 0.5))
    with LOCK:
        seed = ref[r, c]
        if not np.isfinite(seed):
            return jsonify(error="seed has no data"), 400
        cand = np.isfinite(ref) & (np.abs(ref - seed) <= tol)
        lab, n = ndimage.label(cand)
        new = lab == lab[r, c]
        _combine(new, j.get("mode", "replace"))
        return jsonify(seed=float(seed), cells=int(new.sum()), sel=int(S.sel.sum()))


@app.post("/api/rect")
def rect():
    j = request.json
    r0, r1 = sorted((int(j["r0"]), int(j["r1"]))); c0, c1 = sorted((int(j["c0"]), int(j["c1"])))
    with LOCK:
        new = np.zeros(S.shape, bool); new[r0:r1, c0:c1] = True
        _combine(new, j.get("mode", "replace"))
        return jsonify(sel=int(S.sel.sum()))


@app.post("/api/modify")
def modify():
    j = request.json; op = j["op"]; n = int(round(float(j.get("dist_m", 5)) / S.grid.res))
    with LOCK:
        if op == "grow": S.sel = ndimage.binary_dilation(S.sel, iterations=max(n, 1))
        elif op == "shrink": S.sel = ndimage.binary_erosion(S.sel, iterations=max(n, 1))
        elif op == "invert": S.sel = ~S.sel
        elif op == "clear": S.sel[:] = False
        elif op == "holes": S.sel = ndimage.binary_fill_holes(S.sel)
        elif op == "valid":   # restrict to where the layer has data
            S.sel &= S.layers[S.names.index(j["layer"])]["valid"]
        return jsonify(sel=int(S.sel.sum()))


@app.post("/api/fill")
def fill():
    """Set the selected region of a layer's paint to value (0/1) with alpha
    feathered in from the selection edge over feather_m; op 'clear' removes
    paint (alpha 0) so the auto mask shows again."""
    j = request.json; name = j["layer"]; op = j.get("op", "set")
    axis = j.get("axis", "weight")
    fm = float(j.get("feather_m", 0)) if axis == "weight" else 0.0     # categorical axes take no feather
    with LOCK:
        if not S.sel.any():
            return jsonify(error="empty selection"), 400
        store = {"weight": S.paint, "class": S.cpaint, "sigma": S.spaint}[axis]
        v, a = store[name]
        S.undo.append((axis, name, v.copy(), a.copy()))
        if len(S.undo) > 12: S.undo.pop(0)
        if op == "clear":
            a[S.sel] = 0
        else:
            val = float(j["value"])
            ramp = feather_mask(S.sel.astype(np.float32), fm, S.grid.res) if fm > 0 else S.sel.astype(np.float32)
            # inside the selection: value applies with alpha = ramp (soft edge inward)
            a_new = np.maximum(a, ramp)
            # blend value where we have new opinion; keep old where old alpha dominates
            wgt = np.where(a_new > 0, ramp / np.maximum(a_new, 1e-6), 0)
            v[:] = np.where(S.sel, v * (1 - wgt) + val * wgt, v).astype(np.float32)
            a[:] = np.where(S.sel, a_new, a).astype(np.float32)
        t = time.time()
        if axis != "weight": S.recompute_rules()
        S.refold()
        return jsonify(version=S.version, refold_s=round(time.time() - t, 3))


# ------------------------------------------------------------ airbrush

def brush_kernel(radius_px, hardness):
    """Dab profile: 1 inside hardness*R, cosine falloff to 0 at R."""
    R = max(radius_px, 0.5)
    n = int(np.ceil(R))
    yy, xx = np.mgrid[-n:n + 1, -n:n + 1]
    r = np.hypot(yy, xx) / R
    h = float(np.clip(hardness, 0, 0.999))
    k = np.where(r <= h, 1.0, 0.5 + 0.5 * np.cos(np.pi * np.clip((r - h) / (1 - h), 0, 1)))
    k[r > 1] = 0
    return k.astype(np.float32), n


class Stroke:
    """One airbrush stroke.  `sb` accumulates flow per dab and is capped at
    opacity (Photoshop semantics: flow = how fast a dab builds, opacity =
    the most one stroke can lay down).  The paint is recomposited from the
    pre-stroke copy on every update so the cap holds however many times
    the cursor crosses a spot."""

    def __init__(self, layer, value, size_m, hardness, opacity, flow, mode, res, axis="weight"):
        self.axis = axis
        self.store = {"weight": S.paint, "class": S.cpaint, "sigma": S.spaint}[axis]
        self.layer, self.value, self.mode = layer, float(value), mode
        self.opacity, self.flow = float(opacity), float(flow)
        self.k, self.n = brush_kernel(size_m / res / 2.0, hardness)
        self.spacing = max(1.0, (size_m / res) * 0.12)
        v, a = self.store[layer]
        self.v0, self.a0 = v.copy(), a.copy()
        self.sb = np.zeros(v.shape, np.float32)
        self.last = None
        self.dirty = None

    def dab(self, r, c):
        h, w = self.sb.shape; n = self.n
        r0, r1 = max(r - n, 0), min(r + n + 1, h); c0, c1 = max(c - n, 0), min(c + n + 1, w)
        if r1 <= r0 or c1 <= c0:
            return
        k = self.k[r0 - r + n:r1 - r + n, c0 - c + n:c1 - c + n]
        sb = self.sb[r0:r1, c0:c1]
        sb += self.flow * k * (1 - sb)
        np.minimum(sb, self.opacity, out=sb)
        d = (r0, r1, c0, c1)
        self.dirty = d if self.dirty is None else (min(d[0], self.dirty[0]), max(d[1], self.dirty[1]),
                                                   min(d[2], self.dirty[2]), max(d[3], self.dirty[3]))

    def add_points(self, pts):
        for r, c in pts:
            if self.last is None:
                self.dab(int(r), int(c))
            else:
                lr, lc = self.last
                dist = float(np.hypot(r - lr, c - lc))
                steps = max(int(dist / self.spacing), 1)
                for i in range(1, steps + 1):
                    t = i / steps
                    self.dab(int(round(lr + (r - lr) * t)), int(round(lc + (c - lc) * t)))
            self.last = (r, c)
        self.composite()

    def composite(self):
        if self.dirty is None:
            return
        r0, r1, c0, c1 = self.dirty
        sb = self.sb[r0:r1, c0:c1]; v0 = self.v0[r0:r1, c0:c1]; a0 = self.a0[r0:r1, c0:c1]
        v, a = self.store[self.layer]
        if self.axis != "weight":                      # categorical: hard-edged, no partial opinion
            sb = (sb > 0.5).astype(np.float32)
        if self.mode == "erase":                       # remove opinion
            a[r0:r1, c0:c1] = a0 * (1 - sb)
        else:                                          # "over" compositing of (value, sb) on (v0, a0)
            an = sb + a0 * (1 - sb)
            vn = np.where(an > 0, (self.value * sb + v0 * a0 * (1 - sb)) / np.maximum(an, 1e-6), v0)
            v[r0:r1, c0:c1] = vn; a[r0:r1, c0:c1] = an


STROKE = {"cur": None}


@app.post("/api/stroke_begin")
def stroke_begin():
    j = request.json
    with LOCK:
        STROKE["cur"] = Stroke(j["layer"], j.get("value", 1), float(j.get("size_m", 20)),
                               float(j.get("hardness", 0.5)), float(j.get("opacity", 1)),
                               float(j.get("flow", 0.3)), j.get("mode", "paint"), S.grid.res, j.get("axis", "weight"))
        return jsonify(ok=True, radius_px=STROKE["cur"].n)


@app.post("/api/stroke_pts")
def stroke_pts():
    st = STROKE["cur"]
    if st is None:
        return jsonify(error="no stroke"), 400
    with LOCK:
        st.add_points(request.json["points"])
        return jsonify(dirty=st.dirty)


@app.post("/api/stroke_end")
def stroke_end():
    st = STROKE["cur"]
    if st is None:
        return jsonify(error="no stroke"), 400
    with LOCK:
        STROKE["cur"] = None
        if st.dirty is None:
            return jsonify(version=S.version)
        S.undo.append((st.axis, st.layer, st.v0, st.a0))
        if len(S.undo) > 12: S.undo.pop(0)
        t = time.time()
        if st.axis != "weight": S.recompute_rules()
        S.refold()
        return jsonify(version=S.version, refold_s=round(time.time() - t, 3), dirty=st.dirty)


@app.post("/api/enable")
def enable():
    """Session view toggle: {layer, part: dem|auto|paint, on}.  Not saved,
    not part of the recipe; the build ignores it."""
    j = request.json
    with LOCK:
        S.enabled[j["layer"]][j["part"]] = bool(j["on"])
        t = time.time(); S.refold()
        return jsonify(version=S.version, refold_s=round(time.time() - t, 3), enabled=S.enabled)


@app.post("/api/undo")
def undo():
    with LOCK:
        if not S.undo:
            return jsonify(error="nothing to undo"), 400
        item = S.undo.pop()
        if len(item) == 3:
            axis, (name, v, a) = "weight", item
        else:
            axis, name, v, a = item
        {"weight": S.paint, "class": S.cpaint, "sigma": S.spaint}[axis][name] = [v, a]
        if axis != "weight": S.recompute_rules()
        S.refold()
        return jsonify(version=S.version, undo=len(S.undo))


@app.post("/api/save")
def save():
    """Write masks/<layer>_paint.tif (value, alpha as 8-bit) over this
    window's domain box for every layer with any paint."""
    written = []
    with LOCK:
        for L in S.layers[1:]:
            v, a = S.paint[L["name"]]
            if not (a > 0).any():
                continue
            box = L["box"] or (0, S.shape[0], 0, S.shape[1])
            r0, r1, c0, c1 = box
            from rasterio.windows import Window
            import rasterio
            sub = S.grid.sub(Window(S.win.col_off + c0, S.win.row_off + r0, c1 - c0, r1 - r0))
            p = S.recipe.paint_path(L["name"]); p.parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(p, "w", **sub.profile("uint8", None, count=2, predictor=2)) as ds:
                ds.write(to_uint8(v[r0:r1, c0:c1]), 1); ds.write(to_uint8(a[r0:r1, c0:c1]), 2)
            written.append(str(p))
        # class / sigma corrections belong to the observation: obs/<layer>/{class,sigma}_paint.tif
        for axis, store in (("class", S.cpaint), ("sigma", S.spaint)):
            for L in S.layers:
                v, a = store[L["name"]]
                if not (a > 0).any(): continue
                from rasterio.windows import Window
                import rasterio
                sub = S.grid.sub(S.win)
                p = S.recipe.work_dir / "obs" / L["name"] / f"{axis}_paint.tif"; p.parent.mkdir(parents=True, exist_ok=True)
                with rasterio.open(p, "w", **sub.profile("uint8", None, count=2, predictor=2)) as ds:
                    ds.write(to_uint8(v), 1); ds.write(to_uint8(a), 2)
                written.append(str(p))
    return jsonify(written=written)


def main():
    global S
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("recipe"); ap.add_argument("--window"); ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    S = Session(a.recipe, a.window)
    print(f"open http://127.0.0.1:{a.port}/")
    app.run(host="127.0.0.1", port=a.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
