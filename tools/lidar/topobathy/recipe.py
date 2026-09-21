"""recipe.json: the whole description of a composite.

Sketch (see TOPOBATHY_PLAN.md for the full form):

    {"id": "portage_legacy",
     "root": "/Volumes/...",                 # local paths resolve against it
     "grid": {"from": "base", "width": 30000, "height": 20000},
     "layers": [
        {"name": "ifsar", "source": "IfSAR_crop_1m.tif", "align": "pixel"},
        {"name": "anc",   "source": "Anchorage_...tif", "align": "pixel",
         "mask": {"file": "Anchorage_2015_mask.tif", "align": "pixel"}},
        {"name": "x", "source": "...", "mask": {"auto": ["footprint", {"feather": {"width_m": 20}}]}}
     ]}

`align`: "pixel" stacks by row/column index regardless of georeferencing
(the legacy Portage contract: the masks have no geotags at all); "geo"
(default) reads through a WarpedVRT onto the grid.
"""
from __future__ import annotations
import json
from pathlib import Path
from .grid import Grid


class Recipe:
    def __init__(self, path):
        self.path = Path(path)
        self.d = json.loads(self.path.read_text())
        self.id = self.d["id"]
        self.root = Path(self.d.get("root", self.path.parent))
        self.layers = self.d["layers"]
        if not self.layers:
            raise ValueError("recipe has no layers")
        if "mask" in self.layers[0]:
            raise ValueError("the base layer (first entry) takes no mask")
        names = [l["name"] for l in self.layers]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate layer names: {names}")

    @property
    def work_dir(self) -> Path:
        """Derived rasters and paint live here, never in the repo:
        recipe "work_dir", else $LIDAR_BUILD/composite/<id>."""
        import os
        if "work_dir" in self.d:
            return Path(self.d["work_dir"])
        return Path(os.environ.get("LIDAR_BUILD", "/Volumes/Nunatak/lidar_build")) / "composite" / self.id

    def paint_path(self, layer_name) -> Path:
        return self.work_dir / "masks" / f"{layer_name}_paint.tif"

    def resolve(self, p) -> Path:
        p = Path(p)
        return p if p.is_absolute() else self.root / p

    def grid(self) -> Grid:
        g = self.d.get("grid", {"from": "base"})
        if g.get("from", "base") == "base":
            base = self.layers[0]
            if isinstance(base["source"], dict):
                raise ValueError("grid from base needs a raster base layer")
            return Grid.from_raster(self.resolve(base["source"]),
                                    g.get("width"), g.get("height"))
        raise NotImplementedError("grid spec other than from:base (phase 1: snapped lattice)")
