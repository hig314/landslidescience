"""The common lattice every layer and mask is read onto."""
from __future__ import annotations
from dataclasses import dataclass
import rasterio
from rasterio.transform import Affine
from rasterio.windows import Window


@dataclass(frozen=True)
class Grid:
    crs: object
    transform: Affine
    width: int
    height: int

    @classmethod
    def from_raster(cls, path, width=None, height=None) -> "Grid":
        """The Portage case: the grid is the base crop's, optionally clipped
        to a stated size (merge_settings.py had 30000 x 20000 on a
        30001 x 19999 base)."""
        with rasterio.open(path) as ds:
            return cls(ds.crs, ds.transform,
                       width or ds.width, height or ds.height)

    @property
    def res(self) -> float:
        return abs(self.transform.a)

    def full(self) -> Window:
        return Window(0, 0, self.width, self.height)

    def window(self, r0, r1, c0, c1) -> Window:
        r0, c0 = max(r0, 0), max(c0, 0)
        r1, c1 = min(r1, self.height), min(c1, self.width)
        return Window(c0, r0, c1 - c0, r1 - r0)

    def sub(self, win: Window) -> "Grid":
        """The grid of a window of this grid."""
        t = self.transform * Affine.translation(win.col_off, win.row_off)
        return Grid(self.crs, t, int(win.width), int(win.height))

    def bounds(self):
        return rasterio.transform.array_bounds(self.height, self.width, self.transform)

    def profile(self, dtype="float32", nodata=None, **extra):
        p = dict(driver="GTiff", crs=self.crs, transform=self.transform,
                 width=self.width, height=self.height, count=1, dtype=dtype,
                 tiled=True, blockxsize=512, blockysize=512,
                 compress="ZSTD", predictor=3 if dtype.startswith("float") else 2,
                 BIGTIFF="IF_SAFER", nodata=nodata)
        p.update(extra)
        return p
