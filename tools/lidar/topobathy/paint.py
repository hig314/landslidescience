"""Paint files: the record of every hand edit, two 8-bit bands (value,
alpha) on the composite grid.

A paint file always covers the FULL grid.  It is a tiled, ZSTD, sparse
GeoTIFF, so a 30000 x 20000 file with a few painted patches is a few
hundred kilobytes and a save from the mask tool is a windowed write into
the existing file (`r+`), never a merge.  Earlier files were written on a
window's domain box only; `write_paint` migrates one of those into a
full-grid file the first time it is written to, so nothing is lost.

Readers (`masks.read_paint`) go through a WarpedVRT on the same lattice,
which for a file on the grid is a plain window read.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import numpy as np
import rasterio
from rasterio.windows import Window
from .grid import Grid


def _creation(grid: Grid):
    return grid.profile("uint8", None, count=2, predictor=2, SPARSE_OK="TRUE", BIGTIFF="YES")


def _on_grid(ds, grid: Grid):
    """(row_off, col_off) of the file's origin in grid pixels, or None if the
    file is not on this lattice (different resolution or sub-pixel offset)."""
    t, g = ds.transform, grid.transform
    if abs(t.a - g.a) > 1e-9 or abs(t.e - g.e) > 1e-9:
        return None
    col = (t.c - g.c) / g.a
    row = (t.f - g.f) / g.e
    if abs(col - round(col)) > 1e-6 or abs(row - round(row)) > 1e-6:
        return None
    return int(round(row)), int(round(col))


def _ensure_full(path: Path, grid: Grid):
    """Make sure `path` is a full-grid paint file; migrate a domain-box file."""
    if path.exists():
        with rasterio.open(path) as ds:
            full = ds.width == grid.width and ds.height == grid.height and _on_grid(ds, grid) == (0, 0)
            if full and ds.count == 2:
                return
            off = _on_grid(ds, grid)
            if off is None:
                raise ValueError(f"{path} is not on the composite grid; cannot migrate")
            old = ds.read()
        r0, c0 = off
        tmp = path.with_suffix(".migrating.tif")
        with rasterio.open(tmp, "w", **_creation(grid)) as out:
            h, w = old.shape[1:]
            rr = (max(r0, 0), min(r0 + h, grid.height)); cc = (max(c0, 0), min(c0 + w, grid.width))
            if rr[1] > rr[0] and cc[1] > cc[0]:
                sub = old[:, rr[0] - r0:rr[1] - r0, cc[0] - c0:cc[1] - c0]
                out.write(sub, window=Window(cc[0], rr[0], cc[1] - cc[0], rr[1] - rr[0]))
        tmp.replace(path)
        print(f"  paint: migrated {path.name} ({w}x{h} at row {r0}, col {c0}) to the full grid")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(path, "w", **_creation(grid)):
            pass


def write_paint(path, grid: Grid, win: Window, value, alpha):
    """Write (value, alpha) in [0, 1] for one grid window into the paint file."""
    path = Path(path)
    _ensure_full(path, grid)
    v = np.rint(np.clip(value, 0, 1) * 255).astype(np.uint8)
    a = np.rint(np.clip(alpha, 0, 1) * 255).astype(np.uint8)
    with rasterio.open(path, "r+") as ds:
        ds.write(v, 1, window=win)
        ds.write(a, 2, window=win)


def paint_sha(paths) -> str:
    """One hash over the paint files that exist, in a stable order, for the
    composite's provenance stamp."""
    h = hashlib.sha256()
    for p in sorted(str(p) for p in paths if Path(p).exists()):
        h.update(p.encode()); h.update(Path(p).read_bytes())
    return h.hexdigest()[:16]
