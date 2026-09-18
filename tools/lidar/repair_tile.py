"""Copy a TIFF whose LZW stream is damaged in one place, keeping everything
that still decodes and writing nodata where it does not.

Dropping the whole tile would cost 1500 x 1500 m of a 409 km2 survey for a
fault that occupies a few strips. Read at the file's own block granularity so
a failure loses exactly the strips that are broken and nothing beside them."""
import sys, warnings
warnings.filterwarnings("ignore")
import numpy as np, rasterio
from rasterio.windows import Window

src_path, dst_path = sys.argv[1], sys.argv[2]
with rasterio.open(src_path) as r:
    prof = r.profile.copy()
    H, W, C = r.height, r.width, r.count
    bh = r.block_shapes[0][0]
    prof.update(driver="GTiff", compress="deflate", tiled=True,
                blockxsize=512, blockysize=512, BIGTIFF="YES", nodata=0)
    lost = 0
    with rasterio.open(dst_path, "w", **prof) as d:
        for y in range(0, H, bh):
            h = min(bh, H - y)
            win = Window(0, y, W, h)
            try:
                a = r.read(window=win)
            except Exception:
                a = np.zeros((C, h, W), dtype=prof["dtype"])
                lost += h
            d.write(a, window=win)
print(f"  repaired {dst_path}: {lost} of {H} rows unrecoverable "
      f"({100.0*lost/H:.2f}%, {lost*0.15:.0f} m of the tile)")
