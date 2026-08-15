"""Edge-preserving smoothing for the Hugonnet dh/dt source tiles.

Bilateral filter, NoData-aware: each output pixel is the weight-normalized
mean of its 7x7 neighborhood, weighted by BOTH spatial distance (Gaussian,
sigma 1.5 px = 150 m) and value similarity (Gaussian, sigma 0.75 m/yr).
The value term is what preserves edges: neighbors across a sharp dh/dt step
(glacier margins, tidewater drawdown fronts, surge boundaries) contribute
~nothing, so steps stay crisp while same-regime noise averages out. That
noise reduction is what makes a near-zero-sensitive color ramp honest —
see hugonnet_color_dhdt_smooth.txt.

Runs per source tile in its native UTM grid (100 m), BEFORE the mosaic warp,
so the kernel is physically square. Tiles are independent 1° cells, so
smoothing is one-sided at tile edges (weights renormalize — no bias, just
less smoothing on the outermost pixels).

NoData (-9999) pixels stay NoData; valid pixels never borrow from NoData
neighbors. Pure numpy + GDAL (runs under the QGIS-bundled python).

Usage: python3 smooth_dhdt.py <src_dir> <out_dir>
"""
import sys
from pathlib import Path

import numpy as np
from osgeo import gdal

gdal.UseExceptions()

NODATA = -9999.0
RADIUS = 3            # 7x7 window
SIGMA_SPATIAL = 1.5   # pixels (150 m)
SIGMA_VALUE = 0.75    # m/yr


def bilateral(arr):
    """NoData-aware bilateral filter. arr: 2-D float32 with NaN for NoData."""
    pad = np.pad(arr, RADIUS, constant_values=np.nan)
    num = np.zeros_like(arr, dtype=np.float64)
    den = np.zeros_like(arr, dtype=np.float64)
    for dy in range(-RADIUS, RADIUS + 1):
        for dx in range(-RADIUS, RADIUS + 1):
            shifted = pad[RADIUS + dy: RADIUS + dy + arr.shape[0],
                          RADIUS + dx: RADIUS + dx + arr.shape[1]]
            w_sp = np.exp(-(dx * dx + dy * dy) / (2 * SIGMA_SPATIAL ** 2))
            diff = shifted - arr
            w = w_sp * np.exp(-(diff * diff) / (2 * SIGMA_VALUE ** 2))
            valid = ~np.isnan(shifted)
            w = np.where(valid, w, 0.0)
            num += w * np.where(valid, shifted, 0.0)
            den += w
    out = np.where(den > 0, num / den, np.nan)
    out[np.isnan(arr)] = np.nan   # NoData in stays NoData out
    return out.astype(np.float32)


def process(src_path, dst_path):
    ds = gdal.Open(str(src_path))
    band = ds.GetRasterBand(1)
    arr = band.ReadAsArray().astype(np.float32)
    arr[arr == NODATA] = np.nan
    sm = bilateral(arr)
    sm[np.isnan(sm)] = NODATA

    drv = gdal.GetDriverByName('GTiff')
    out = drv.Create(str(dst_path), ds.RasterXSize, ds.RasterYSize, 1,
                     gdal.GDT_Float32, options=['COMPRESS=DEFLATE', 'TILED=YES'])
    out.SetGeoTransform(ds.GetGeoTransform())
    out.SetProjection(ds.GetProjection())
    ob = out.GetRasterBand(1)
    ob.SetNoDataValue(NODATA)
    ob.WriteArray(sm)
    out.FlushCache()


def main():
    src_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    tifs = sorted(src_dir.glob('*.tif'))
    for i, p in enumerate(tifs, 1):
        dst = out_dir / p.name
        if dst.exists():
            continue
        process(p, dst)
        if i % 25 == 0 or i == len(tifs):
            print(f'  smoothed {i}/{len(tifs)}', flush=True)


if __name__ == '__main__':
    main()
