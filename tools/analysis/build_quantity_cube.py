#!/opt/anaconda3/bin/python3
"""Build the quantity cube: every axis-capable quantity point-sampled at the
centre of each cell of the 1 km EPSG:3338 lattice, as Float32 .npy grids.

    build_quantity_cube.py            # every quantity in quantities.py
    build_quantity_cube.py slope coh  # just these

Writes data/quantities/<key>.npy (NaN = no value), land.npy (bool, the
60 m terrain grid's own sea mask) and grid.json (the lattice + the public
part of each quantity's registry entry). inventory/analysis.py memory-maps
them to answer api/analysis/density/ (any pair, on demand) and
api/analysis/sample/ (the lattice cells inside a circle). ~23 MB a
quantity; rsync data/quantities/ to the droplet with the rest of data/.

WHY A POINT SAMPLE AND NOT A MEAN. A density of 1 km means of slope is a
density of a smoother field than the one the landslides are sampled from
(a centroid reads the 60 m slope it sits on), so the two would be drawn
on different distributions and the comparison would be wrong. Sampling
the native raster at the lattice point keeps both in the same
distribution; the lattice is just a systematic 1-in-278 sample of a 60 m
field, which is plenty for a histogram (5.9 M points into ~3,000 bins).

Same-CRS rasters are read in bands of source rows and indexed directly,
so the sample is exact (the source pixel containing the lattice point) and
no overview is ever read. Other CRSs go through a nearest WarpedVRT onto
the lattice, which samples the same way.
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window

sys.path.insert(0, str(Path(__file__).resolve().parent))
from quantities import GRID, LAND_SRC, QUANTITIES, ROOT, public     # noqa: E402

OUT = ROOT / 'data' / 'quantities'
BAND_BYTES = 300 * 2 ** 20          # read the source in bands of about this size
GDAL = Path('/opt/homebrew/bin')


def lattice_centres():
    a, _, c, _, e, f = GRID['transform']
    xs = c + (np.arange(GRID['width']) + 0.5) * a
    ys = f + (np.arange(GRID['height']) + 0.5) * e
    return xs, ys


def ensure_vrt(q):
    """A .vrt source built from a directory of tiles, if not there yet."""
    if q['src'].exists() or 'build_vrt' not in q:
        return
    tiles = sorted(p for p in q['build_vrt'].glob('*.tif'))
    print(f'  building {q["src"].name} from {len(tiles)} tiles')
    lst = q['src'].with_suffix('.txt')
    lst.write_text('\n'.join(str(p) for p in tiles))
    subprocess.run([str(GDAL / 'gdalbuildvrt'), '-q', '-input_file_list', str(lst), str(q['src'])], check=True)


def sample_same_crs(src, xs, ys):
    """Point-sample a raster in the lattice CRS at (xs[j], ys[i]) -> (len ys, len xs)."""
    t = src.transform
    cols = np.floor((xs - t.c) / t.a).astype(np.int64)
    rows = np.floor((ys - t.f) / t.e).astype(np.int64)
    out = np.full((len(ys), len(xs)), np.nan, dtype=np.float32)
    cok = (cols >= 0) & (cols < src.width)
    rok = (rows >= 0) & (rows < src.height)
    if not cok.any() or not rok.any():
        return out
    jj = np.nonzero(cok)[0]
    c0, c1 = int(cols[jj[0]]), int(cols[jj[-1]]) + 1
    ii = np.nonzero(rok)[0]
    # lattice rows per band so a band of source rows stays under BAND_BYTES
    src_rows_per_lat = max(1, int(round(abs(GRID['transform'][4] / t.e))))
    per_band = max(1, int(BAND_BYTES // ((c1 - c0) * np.dtype(src.dtypes[0]).itemsize * src_rows_per_lat)))
    for b0 in range(0, len(ii), per_band):
        sel = ii[b0:b0 + per_band]
        r0, r1 = int(rows[sel[0]]), int(rows[sel[-1]]) + 1
        blk = src.read(1, window=Window(c0, r0, c1 - c0, r1 - r0))
        out[np.ix_(sel, jj)] = blk[rows[sel] - r0][:, cols[jj] - c0]
    return out


def sample_other_crs(src):
    a, _, c, _, e, f = GRID['transform']
    with WarpedVRT(src, crs=GRID['crs'], transform=rasterio.Affine(a, 0, c, 0, e, f),
                   width=GRID['width'], height=GRID['height'], resampling=Resampling.nearest) as v:
        return v.read(1).astype(np.float32)


def build(key, xs, ys):
    q = QUANTITIES[key]
    ensure_vrt(q)
    with rasterio.open(q['src']) as src:
        if src.crs and src.crs.to_epsg() == 3338:
            g = sample_same_crs(src, xs, ys).astype(np.float32)
        else:
            g = sample_other_crs(src)
        bad = ~np.isfinite(g)
        if src.nodata is not None:
            bad |= g == src.nodata
    for nd in q.get('nodata', []):
        bad |= g == nd
    if 'classes' in q:
        bad |= ~np.isin(g, q['classes'])
    g[bad] = np.nan
    np.save(OUT / f'{key}.npy', g)
    ok = np.isfinite(g)
    print(f'  {key:8s} {ok.sum():>9,} valid   {np.nanmin(g):10.3f} .. {np.nanmax(g):10.3f}' if ok.any() else f'  {key:8s} EMPTY')
    return g


def main(argv):
    OUT.mkdir(parents=True, exist_ok=True)
    keys = [k for k in argv if k != 'land'] if argv else list(QUANTITIES)
    xs, ys = lattice_centres()
    for k in keys:
        build(k, xs, ys)
    if not argv or 'land' in argv:
        with rasterio.open(LAND_SRC) as src:
            land = sample_same_crs(src, xs, ys)
            land = np.isfinite(land) & (land != src.nodata)
        np.save(OUT / 'land.npy', land)
        print(f'  land     {land.sum():>9,} cells')
    meta = dict(GRID)
    meta['quantities'] = {k: public(k) for k in QUANTITIES}
    (OUT / 'grid.json').write_text(json.dumps(meta))
    print('wrote', OUT)


if __name__ == '__main__':
    main(sys.argv[1:])
