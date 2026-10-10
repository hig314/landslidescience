"""Reading layers onto the grid: float32 with NaN where invalid, plus a
valid mask.  A layer source is a datasets.json id (its archive COG) or a
local raster path; derived layers are phase 2 work."""
from __future__ import annotations
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window
from .grid import Grid


def _valid(z, nodata):
    v = np.isfinite(z)
    if nodata is not None:
        v &= z != nodata
    return v


def read_pixel(path, grid: Grid, win: Window, band=1):
    """Legacy contract: row/col index, no georeferencing.  Cells outside
    the source's extent are NaN."""
    with rasterio.open(path) as ds:
        r0, c0 = int(win.row_off), int(win.col_off)
        h, w = int(win.height), int(win.width)
        rr = min(h, ds.height - r0)
        cc = min(w, ds.width - c0)
        out = np.full((h, w), np.nan, dtype=np.float32)
        if rr > 0 and cc > 0:
            out[:rr, :cc] = ds.read(band, window=Window(c0, r0, cc, rr)).astype(np.float32)
        nodata = ds.nodata
    valid = _valid(out, nodata)
    out[~valid] = np.nan
    return out, valid


def read_geo(path, grid: Grid, win: Window, resampling="nearest", band=1):
    """Read through a WarpedVRT onto the window's lattice, so any
    georeferenced raster in any CRS lands on the grid."""
    sub = grid.sub(win)
    rs = getattr(Resampling, resampling)
    with rasterio.open(path) as ds:
        with WarpedVRT(ds, crs=sub.crs, transform=sub.transform,
                       width=sub.width, height=sub.height,
                       resampling=rs, src_nodata=ds.nodata,
                       nodata=np.nan, dtype="float32") as vrt:
            z = vrt.read(band).astype(np.float32)
    valid = np.isfinite(z)
    return z, valid


def read_layer(spec, recipe, grid: Grid, win: Window):
    src = spec["source"]
    if isinstance(src, dict):
        raise NotImplementedError(f"derived layer {src.get('derive')} (phase 2)")
    path = recipe.source_path(spec)
    if spec.get("align", "geo") == "pixel":
        z, valid = read_pixel(path, grid, win)
    else:
        z, valid = read_geo(path, grid, win, spec.get("resample", "nearest"))
    return apply_shift(spec, z), valid


def apply_shift(spec, z):
    """`shift_m`: a measured vertical offset applied on read, stated in the
    recipe.  A datum error belongs in the source's vertical_shift_m in
    datasets.json (rebuild the archive); this is the explicit, reported
    stop-gap until that is done - never silent."""
    dz = float(spec.get("shift_m", 0) or 0)
    if dz:
        z = z + np.float32(dz)
    return z
