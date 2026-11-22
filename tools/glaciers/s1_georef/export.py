"""Placed video -> georeferenced products (EPSG:3857 GeoTIFFs, LZW).
  <name>_median_rgb.tif    median of the registered frames (3 x uint8)
  <name>_p90_med_p10.tif   luminance p90 / median / p10 as RGB (uint8)
  <name>_lum_std.tif       temporal std of log luminance (float32)
  <name>_sign.tif          sign persistence, -1..1 (float32)
The label box is nodata. usage: export.py NAME ORBIT [OUTDIR]"""
import sys, json
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_bounds
ROOT = Path('/Volumes/Nunatak/glacier_s1_georef')
name, orbit = sys.argv[1], sys.argv[2]
out = Path(sys.argv[3]) if len(sys.argv) > 3 else ROOT / name / 'geotiff'
out.mkdir(parents=True, exist_ok=True)
st = np.load(ROOT / name / 'stack.npz'); m = json.load(open(ROOT / name / 'match.json'))['lum_' + orbit]
b = m['bounds_3857']; mask = st['mask']
h, w = mask.shape
tr = from_bounds(b[0], b[1], b[2], b[3], w, h)
def nrm(x):
    lo, hi = np.percentile(x[mask], [1, 99]); return (np.clip((x - lo) / (hi - lo), 0, 1) * 254 + 1).astype(np.uint8)
def write(fn, bands, dtype, nodata):
    with rasterio.open(out / fn, 'w', driver='GTiff', width=w, height=h, count=len(bands), dtype=dtype, crs='EPSG:3857',
                       transform=tr, nodata=nodata, compress='lzw', tiled=True) as d:
        for i, x in enumerate(bands, 1):
            x = x.astype(dtype).copy(); x[~mask] = nodata; d.write(x, i)
rgb = st['med_rgb']
write(name + '_median_rgb.tif', [np.maximum(rgb[..., i], 1) for i in range(3)], 'uint8', 0)
write(name + '_p90_med_p10.tif', [nrm(st['lum_p90']), nrm(st['lum_med']), nrm(st['lum_p10'])], 'uint8', 0)
write(name + '_lum_std.tif', [st['lum_std']], 'float32', -9999)
write(name + '_sign.tif', [st['sign']], 'float32', -9999)
meta = dict(name=name, orbit=orbit, bounds_3857=b, ground_pixel_m=m['s'], ncc=m['ncc'], second=m['second'],
            frames=int(st['good'].sum()), dropped=[int(x) for x in st['dropped']])
(out / (name + '_placement.json')).write_text(json.dumps(meta, indent=1))
print(name, 'written', out)
