#!/opt/anaconda3/bin/python3
"""Daymet 1991-2020 means from the annual windows daymet_fetch.py left.

    prcp_mean.tif   mean annual precipitation, mm
    tmean.tif       mean annual air temperature, C: the mean over years of (tmax_annavg + tmin_annavg) / 2,
                    which is how Daymet itself defines a daily mean

    daymet_normals.py [--src DIR] [--years 1991 2020]
"""
import argparse
from pathlib import Path

import numpy as np
import rasterio


def mean_of(src, var, years):
    acc = cnt = prof = None
    for y in range(years[0], years[1] + 1):
        with rasterio.open(src / f'{var}_{y}.tif') as d:
            a = d.read(1).astype(np.float64)
            prof = prof or d.profile
        ok = a > -9000
        if acc is None:
            acc, cnt = np.zeros(a.shape), np.zeros(a.shape, np.int32)
        acc += np.where(ok, a, 0); cnt += ok
    full = cnt == years[1] - years[0] + 1                    # a cell must have every year
    return np.where(full, acc / np.maximum(cnt, 1), -9999.0), prof


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='/Volumes/Nunatak/climate_src/daymet/annual')
    ap.add_argument('--years', type=int, nargs=2, default=[1991, 2020])
    a = ap.parse_args()
    src = Path(a.src); out = src.parent / 'normals'; out.mkdir(exist_ok=True)
    p, prof = mean_of(src, 'prcp_annttl', a.years)
    tx, _ = mean_of(src, 'tmax_annavg', a.years)
    tn, _ = mean_of(src, 'tmin_annavg', a.years)
    t = np.where((tx > -9000) & (tn > -9000), (tx + tn) / 2, -9999.0)
    prof.update(dtype='float32', nodata=-9999.0, compress='deflate', predictor=3, tiled=True, blockxsize=256, blockysize=256)
    for name, arr in (('prcp_mean', p), ('tmean', t)):
        with rasterio.open(out / f'{name}.tif', 'w', **prof) as d:
            d.write(arr.astype(np.float32), 1)
        v = arr[arr > -9000]
        print(name, arr.shape, 'p1/50/99', np.round(np.percentile(v, [1, 50, 99]), 1), 'max', round(float(v.max()), 1))


if __name__ == '__main__':
    main()
