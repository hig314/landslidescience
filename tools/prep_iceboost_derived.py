#!/usr/bin/env python3
"""Stage A of the IceBoost pipeline: per-complex derived rasters.

Reads the raw IceBoost v2.0 glacier-complex GeoTIFFs (5-band float32, 100 m,
per-complex UTM; data/iceboost/raw/rgi{1,2}/) and writes three single-band
rasters per complex into data/iceboost/derived/{thickness,bed,overdeep}/:

  thickness  band 1 verbatim.
  bed        orthometric bed = (surface - geoid) - thickness.
             BAND ORDER IS VERIFIED FROM THE DATA, not the Zenodo text (one
             record's description lists a different order): 1=thickness,
             2=error, 3=Jensen gap, 4=surface, 5=geoid — band 4 is the only
             one spanning 0..4000 m and band 5 sits at the +13..+20 m Alaska
             geoid undulation. Surface is treated as ellipsoidal and the
             geoid band subtracted (the band exists precisely for that
             conversion; the per-complex volume_bsl tags need a sea-level
             datum) — worst case if wrong is a ~17 m offset, flagged for
             confirmation against the GMD 2025 paper.
  overdeep   closed-basin depth = fillsinks(bed) - bed, sub-10 m set to 0.
             Computed HERE, per complex, by design: complexes are
             hydrologically separate ice bodies, so basins cannot cross file
             boundaries — 32k small sink-fills are cheap where one
             region-wide 500-Mpixel fill would be painful. Fill is
             morphological reconstruction by erosion with the raster border
             AND every ice cell adjacent to non-ice seeded as drains (the
             margin is where water leaves; without the adjacency seed an
             interior nunatak wall would dam a false basin).

NaN nodata in, -9999 out (gdaldem color-relief's `nv` matching is exact and
NaN never compares equal). Skips outputs that already exist, so re-runs
resume. Run with the system python3 (rasterio + numpy + scipy + skimage);
the QGIS GDAL binaries are only needed for the later warp/tile stages.

Usage: python3 tools/prep_iceboost_derived.py [--workers N]
"""
import argparse
import glob
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import rasterio
from rasterio.enums import Compression

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
RAW = os.path.join(ROOT, 'data', 'iceboost', 'raw')
OUT = os.path.join(ROOT, 'data', 'iceboost', 'derived')
NODATA = -9999.0
MIN_BASIN_DEPTH = 10.0     # m; shallower fill artifacts dropped at source


def process(path):
    from scipy.ndimage import binary_dilation
    from skimage.morphology import reconstruction

    cid = os.path.splitext(os.path.basename(path))[0]
    outs = {k: os.path.join(OUT, k, cid + '.tif')
            for k in ('thickness', 'bed', 'overdeep')}
    if all(os.path.exists(p) for p in outs.values()):
        return 'skip'

    with rasterio.open(path) as r:
        thick = r.read(1)
        surf = r.read(4)
        geoid = r.read(5)
        prof = r.profile

    bed = (surf - geoid) - thick
    finite = np.isfinite(bed) & np.isfinite(thick)

    # Sink fill (reconstruction-by-erosion): seed high except the drains.
    depth = np.zeros_like(bed, dtype=np.float32)
    if finite.any():
        hi = float(np.nanmax(bed[finite])) + 1000.0
        # float64 BEFORE the where(): np.where(finite, bed, hi) with float32
        # `bed` promotes to float32 and rounds `hi` UP to the nearest float32,
        # while full_like on a float64 array keeps the float64 value — leaving
        # seed ~1e-4 BELOW mask at every nodata cell, which reconstruction
        # rejects. Bit us on 3/3 of the first files checked.
        mask = np.where(finite, bed.astype(np.float64), hi)
        seed = np.full_like(mask, hi)
        seed[0, :] = mask[0, :]; seed[-1, :] = mask[-1, :]
        seed[:, 0] = mask[:, 0]; seed[:, -1] = mask[:, -1]
        edge = binary_dilation(~finite) & finite
        seed[edge] = mask[edge]
        assert not np.any(seed < mask), 'seed/mask invariant broken'
        filled = reconstruction(seed, mask, method='erosion')
        depth = (filled - mask).astype(np.float32)
        depth[depth < MIN_BASIN_DEPTH] = 0.0

    prof.update(count=1, dtype='float32', nodata=NODATA,
                compress=Compression.deflate.value, predictor=3, tiled=False)
    for key, arr in (('thickness', thick), ('bed', bed), ('overdeep', depth)):
        a = np.where(finite, arr, NODATA).astype(np.float32)
        with rasterio.open(outs[key], 'w', **prof) as w:
            w.write(a, 1)
    return 'ok'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=6)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(RAW, 'rgi1', 'rgi1', '*.tif')) +
                   glob.glob(os.path.join(RAW, 'rgi2', 'rgi2', '*.tif')))
    if not files:
        sys.exit('no raw files under %s' % RAW)
    for k in ('thickness', 'bed', 'overdeep'):
        os.makedirs(os.path.join(OUT, k), exist_ok=True)

    done = skipped = errs = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for i, res in enumerate(ex.map(process, files, chunksize=64)):
            if res == 'ok':
                done += 1
            elif res == 'skip':
                skipped += 1
            else:
                errs += 1
            if (i + 1) % 2000 == 0:
                print('  %d / %d (new %d, resumed-skip %d)'
                      % (i + 1, len(files), done, skipped), flush=True)
    print('DERIVED-COMPLETE: %d new, %d skipped, %d errors of %d'
          % (done, skipped, errs, len(files)))


if __name__ == '__main__':
    main()
