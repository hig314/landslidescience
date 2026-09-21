"""Masks: 8-bit greyscale on disk (0 = 0.0, 255 = 1.0), float32 in [0, 1]
in memory.  Per layer the effective mask is

    eff = (auto*(1 - alpha) + value*alpha) * valid

where (value, alpha) is the two-band paint raster and `valid` is the
layer's footprint, so paint can never punch nodata into the composite.
"""
from __future__ import annotations
import numpy as np
import rasterio
from rasterio.windows import Window
from scipy import ndimage
from .grid import Grid
from .stack import read_pixel, read_geo

INV255 = np.float32(1.0 / 255.0)


# ---------------------------------------------------------------- files

def read_mask_file(path, grid: Grid, win: Window, align="geo", band=1):
    """An 8-bit mask painted by hand (Photoshop, QGIS, GIMP).  Returns
    (mask in [0,1], covered): `covered` is where the file has cells at all,
    so a mask written on a smaller domain than the grid says nothing
    outside it (the footprint rule applies there, not 0)."""
    if align == "pixel":
        m, covered = read_pixel(path, grid, win, band)
    else:
        m, covered = read_geo(path, grid, win, "nearest", band)
    m = np.where(covered, m, 0).astype(np.float32) * INV255
    return np.clip(m, 0, 1), covered


def read_paint(path, grid: Grid, win: Window, align="geo"):
    """Two-band paint raster: value, alpha.  Missing file or cells outside
    the file = no opinion (alpha 0)."""
    value, _ = read_mask_file(path, grid, win, align, band=1)
    alpha, covered = read_mask_file(path, grid, win, align, band=2)
    alpha[~covered] = 0
    return value, alpha


def to_uint8(m):
    return np.rint(np.clip(m, 0, 1) * 255).astype(np.uint8)


# ----------------------------------------------------------- generators

def footprint(valid):
    return valid.astype(np.float32)


def feather(m, width_m, res_m, shape="cosine"):
    """DEM_Smooth_Blend: distance inward from the mask edge, ramped to 1
    over `width_m`.  Cells at the edge get ~0, so the layer fades in."""
    inside = m > 0.5
    if width_m <= 0 or not inside.any():
        return m
    d = ndimage.distance_transform_edt(inside) * res_m
    t = np.clip(d / width_m, 0, 1).astype(np.float32)
    if shape == "cosine":
        ramp = 0.5 - 0.5 * np.cos(np.pi * t)
    else:
        ramp = t
    return (ramp * inside).astype(np.float32)


def water_level_auto(below, valid, bin_m=0.1):
    """The lidar over a water body is flat at the water level, so the mode
    of the elevation under this layer's footprint is the water level.
    (CoNED uses a known MSL; a lake has no tide gauge, so estimate it.)"""
    z = below[valid & np.isfinite(below)]
    if z.size < 100:
        return None
    lo, hi = np.percentile(z, [0.5, 99.5])
    bins = np.arange(lo, hi + bin_m, bin_m)
    h, e = np.histogram(z, bins)
    i = int(np.argmax(h))
    return float(0.5 * (e[i] + e[i + 1]))


def water(z, valid, below, level="auto", tol_m=0.5):
    """CoNED-style state rule for a bathymetric layer: it counts where the
    surface below is water (flat at the water level, within tol) and the
    layer is beneath that level; a TIN extrapolated over land is above the
    level and drops out.  Where nothing lies below, the footprint rule."""
    if level == "auto":
        level = water_level_auto(below, valid)
    if level is None:
        return footprint(valid), None
    has_below = np.isfinite(below)
    is_water = has_below & (np.abs(below - level) <= tol_m)
    m = valid & ((is_water & (z < level)) | ~has_below)
    return m.astype(np.float32), float(level)


def lowest(z, valid, below, tol_m=0.2):
    """This layer wins where it is lower than the fold below by more than
    tol (sees under canopy / snow); footprint where nothing lies below."""
    has_below = np.isfinite(below)
    m = valid & ((has_below & (z < below - tol_m)) | ~has_below)
    return m.astype(np.float32)


def despeckle(m, min_area_m2, res_m):
    """Drop connected blobs smaller than min_area and fill holes smaller
    than it, so a generated mask is a few clean regions, not confetti."""
    px = max(int(min_area_m2 / (res_m * res_m)), 1)
    b = m > 0.5
    for target in (True, False):          # blobs, then holes
        lab, n = ndimage.label(b == target)
        if n:
            sizes = ndimage.sum(np.ones_like(lab, np.int32), lab, np.arange(1, n + 1))
            small = np.flatnonzero(sizes < px) + 1
            if small.size:
                b[np.isin(lab, small)] = not target
    return b.astype(np.float32)


def smooth(m, size_m, res_m):
    """Majority filter."""
    n = max(int(round(size_m / res_m)), 1) | 1
    return (ndimage.uniform_filter(m, n) > 0.5).astype(np.float32)


# ----------------------------------------------------------- operations

def blur(m, sigma_m, res_m):
    return ndimage.gaussian_filter(m, sigma_m / res_m).astype(np.float32)


def erode(m, dist_m, res_m):
    n = max(int(round(dist_m / res_m)), 1)
    return ndimage.binary_erosion(m > 0.5, iterations=n).astype(np.float32)


def dilate(m, dist_m, res_m):
    n = max(int(round(dist_m / res_m)), 1)
    return ndimage.binary_dilation(m > 0.5, iterations=n).astype(np.float32)


def threshold(m, t):
    return (m >= t).astype(np.float32)


def invert(m):
    return (1.0 - m).astype(np.float32)


OPS = {"blur": blur, "erode": erode, "dilate": dilate, "feather": feather}


def run_auto(steps, valid, res_m, z=None, below=None, info=None):
    """Apply a list like ["footprint", {"water": {"level": "auto"}},
    {"despeckle": {"min_area_m2": 2000}}, {"feather": {"width_m": 20}}]
    and return the auto mask.  `z` is this layer, `below` the fold of the
    layers beneath it (needed by water / lowest).  `info` collects what
    the generators decided (e.g. the water level) for the report."""
    m = None
    for step in steps:
        if step == "footprint":
            m = footprint(valid)
        elif isinstance(step, dict) and len(step) == 1:
            (name, kw), = step.items()
            if name in ("water", "lowest") and (z is None or below is None):
                raise ValueError(f"'{name}' needs the layer and the fold below it")
            if name == "water":
                m, level = water(z, valid, below, kw.get("level", "auto"), kw.get("tol_m", 0.5))
                if info is not None:
                    info["water_level_m"] = level
                continue
            if name == "lowest":
                m = lowest(z, valid, below, kw.get("tol_m", 0.2)); continue
            if m is None:
                raise ValueError(f"'{name}' before 'footprint'")
            if name == "threshold":
                m = threshold(m, kw["t"])
            elif name == "invert":
                m = invert(m)
            elif name == "despeckle":
                m = despeckle(m, kw["min_area_m2"], res_m)
            elif name == "smooth":
                m = smooth(m, kw["size_m"], res_m)
            elif name in OPS:
                arg = kw.get("width_m", kw.get("sigma_m", kw.get("dist_m")))
                m = OPS[name](m, arg, res_m)
            else:
                raise ValueError(f"unknown mask op {name}")
        else:
            raise ValueError(f"bad mask step {step!r}")
    if m is None:
        raise ValueError("auto mask has no steps")
    return m


# --------------------------------------------------------------- domain

def overlap_domain(valid, lower_valid, pad_px=0):
    """The rectangle that exactly encloses where this layer overlaps any
    layer below it (plus padding).  Outside it the mask is trivially the
    footprint: the layer is either the only data or absent.  Returns a
    (r0, r1, c0, c1) slice box in window pixels, or None if no overlap."""
    ov = valid & lower_valid
    if not ov.any():
        return None
    rows = np.flatnonzero(ov.any(axis=1))
    cols = np.flatnonzero(ov.any(axis=0))
    h, w = valid.shape
    return (int(max(rows[0] - pad_px, 0)), int(min(rows[-1] + 1 + pad_px, h)),
            int(max(cols[0] - pad_px, 0)), int(min(cols[-1] + 1 + pad_px, w)))


def domain_box(spec, valid, lower_valid, res_m):
    """Per-layer `domain`: "overlap" (default), "full", or an explicit
    [r0, r1, c0, c1] in grid pixels (window-relative here)."""
    d = spec.get("domain", "overlap")
    h, w = valid.shape
    if d == "full":
        return (0, h, 0, w)
    if d == "overlap":
        pad = int(round(spec.get("domain_pad_m", 50) / res_m))
        return overlap_domain(valid, lower_valid, pad)
    r0, r1, c0, c1 = d
    return (max(r0, 0), min(r1, h), max(c0, 0), min(c1, w))


def inside_box(shape, box):
    ins = np.zeros(shape, bool)
    if box is not None:
        r0, r1, c0, c1 = box
        ins[r0:r1, c0:c1] = True
    return ins


# ------------------------------------------------------------ effective

def apply_paint(auto, value, alpha):
    return (auto * (1.0 - alpha) + value * alpha).astype(np.float32)


def effective(auto, valid, paint=None):
    m = auto if paint is None else apply_paint(auto, *paint)
    return (m * valid).astype(np.float32)


def layer_mask(spec, recipe, grid: Grid, win: Window, valid, lower_valid=None, z=None, below=None, info=None):
    """The effective mask for one layer from its recipe spec.

    Assembly order:  footprint everywhere  ->  inside the domain, the auto
    generator (or a mask file where it has cells)  ->  paint where alpha
    -> times valid.  Returns (effective, auto, box)."""
    if lower_valid is None:
        lower_valid = np.ones_like(valid)
    ms = spec.get("mask") or {}
    fp = footprint(valid)
    box = domain_box(spec, valid, lower_valid, grid.res)
    inside = inside_box(valid.shape, box)
    if "file" in ms:
        m, covered = read_mask_file(recipe.resolve(ms["file"]), grid, win,
                                    ms.get("align", spec.get("align", "geo")))
        auto = np.where(inside & covered, m, fp)
    elif "auto" in ms:
        auto = np.where(inside, run_auto(ms["auto"], valid, grid.res, z, below, info), fp)
    else:                               # no mask spec: footprint (legacy full overlay)
        auto = fp
    paint = None
    p = recipe.resolve(ms["paint"]) if "paint" in ms else recipe.paint_path(spec["name"])
    if True:
        if p.exists():
            value, alpha = read_paint(p, grid, win, ms.get("align", "geo"))
            paint = (value, alpha * inside)
    return effective(auto, valid, paint), auto.astype(np.float32), box
