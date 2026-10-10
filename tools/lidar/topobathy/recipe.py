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

    def obs_paint_path(self, layer_name, axis) -> Path:
        """Hand corrections to an observation's class or quality (axis =
        "class" | "sigma"); they belong to the observation, not the composite."""
        return self.work_dir / "obs" / layer_name / f"{axis}_paint.tif"

    def paint_paths(self):
        """Every paint file this recipe can read, for the provenance stamp."""
        ps = []
        for L in self.layers:
            ps.append(self.paint_path(L["name"]))
            ps += [self.obs_paint_path(L["name"], ax) for ax in ("class", "sigma")]
        return ps

    @property
    def out_dir(self) -> Path:
        return self.work_dir / "out"

    @property
    def sha(self) -> str:
        import hashlib
        return hashlib.sha256(self.path.read_bytes()).hexdigest()[:16]

    def resolve(self, p) -> Path:
        p = Path(p)
        return p if p.is_absolute() else self.root / p

    # ---- datasets.json sources
    _manifest = None

    @classmethod
    def manifest(cls):
        """tools/lidar/datasets.json as {id: entry}, loaded once."""
        if cls._manifest is None:
            mp = Path(__file__).resolve().parents[1] / "datasets.json"
            cls._manifest = {e["id"]: e for e in json.loads(mp.read_text())["datasets"]}
        return cls._manifest

    def dataset(self, source):
        """The manifest entry when `source` is a dataset id, else None."""
        return self.manifest().get(source) if isinstance(source, str) else None

    def source_path(self, spec) -> Path:
        """A layer's raster: a dataset id resolves to its archive COG
        ($LIDAR_BUILD/cog/<id>.tif, already NAVD88 with vertical_shift_m
        applied); anything else is a path relative to `root`."""
        import os
        src = spec["source"]
        if self.dataset(src) is not None and not self.resolve(src).exists():
            cog = Path(os.environ.get("LIDAR_COG_OUT", Path(os.environ.get("LIDAR_BUILD", "/Volumes/Nunatak/lidar_build")) / "cog"))
            p = cog / f"{src}.tif"
            if not p.exists():
                raise FileNotFoundError(f"dataset {src}: archive COG missing at {p} (run build_lidar.py {src} --stage archive)")
            return p
        return self.resolve(src)

    def layer_date(self, spec):
        """A layer's date: the recipe's own `date`/`year`, else the manifest's year."""
        d = spec.get("date", spec.get("year"))
        if d is None:
            ds = self.dataset(spec.get("source"))
            if ds is not None:
                d = ds.get("year")
        return d

    def grid(self) -> Grid:
        """{"from": "base", width, height}: the base raster's lattice (Portage);
        {"epsg": 6334, "res_m": 1.0, "bounds": [xmin, ymin, xmax, ymax]}: a
        lattice snapped OUTWARD to whole multiples of res_m, so two composites
        at the same resolution share cell edges and a tile pinned the same
        way lands on the same cells (unsnapped bounds gave 51 cm seams in the
        Kenai work)."""
        g = self.d.get("grid", {"from": "base"})
        if "bounds" in g:
            import math
            from rasterio.crs import CRS
            from rasterio.transform import from_origin
            res = float(g.get("res_m", 1.0))
            x0, y0, x1, y1 = g["bounds"]
            x0, y0 = math.floor(x0 / res) * res, math.floor(y0 / res) * res
            x1, y1 = math.ceil(x1 / res) * res, math.ceil(y1 / res) * res
            return Grid(CRS.from_epsg(int(g["epsg"])), from_origin(x0, y1, res, res),
                        int(round((x1 - x0) / res)), int(round((y1 - y0) / res)))
        if g.get("from", "base") == "base":
            base = self.layers[0]
            if isinstance(base["source"], dict):
                raise ValueError("grid from base needs a raster base layer")
            return Grid.from_raster(self.source_path(base), g.get("width"), g.get("height"))
        raise ValueError(f"grid spec not understood: {g}")
