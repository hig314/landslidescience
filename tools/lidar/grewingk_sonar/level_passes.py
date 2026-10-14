#!/usr/bin/env python3
"""Level the 2023 Grewingk Lake multibeam pass by pass, and put it in the DEV /lidar/ catalogue.

    PATH=/opt/homebrew/bin:$PATH /opt/anaconda3/bin/python3 tools/lidar/grewingk_sonar/level_passes.py [--no-viewer]

WHY.  Katie Hughes' Norbit-on-a-packraft survey (2023-05-18/19, Qinsy, 106 .las passes of about a
minute each; heights from boat GNSS, ITRF2008 -> NAD83(2011) -> NAVD88 via GEOID12B, PPK after the
fact per Hig) is tight WITHIN a pass (0.07-0.09 m in a 2 m cell) but passes disagree where they
overlap: median 0.15 m, 90th percentile 0.77 m, up to 2 m.  One constant per pass explains the
overlaps to 0.05 m, i.e. the error is a vertical shift of the whole pass -- the signature of a
GNSS height that wandered (PPK float / unresolved segments, or heights from the real-time
navigation), not of depth error.  Found 2026-10-13 comparing the sonar against Ed Berg's 2017
Lowrance track at Hig's request.

WHAT.  1. Grid every pass alone onto a common 2 m lattice (mean, count >= 4).
        2. For every pair of passes sharing >= 30 cells, the median difference.
        3. Least squares: one offset per pass, weighted by sqrt(min(shared cells, 500)), with the
           offsets constrained to sum to zero -- so the survey's MEAN level is Katie's, unchanged.
           Leveling fixes relative heights only; the absolute level needs the PPK trajectory.
           A pass that overlaps no other keeps offset 0 (listed in offsets.json).
        4. Re-grid all points at 1 m on the delivered grid's lattice twice, identically: as
           delivered (raw) and with each pass's offset subtracted (levelled).  The raw regrid is the
           apples-to-apples reference -- /lidar/'s difference layer between the two is exactly the
           leveling.

OUT (Nunatak, $OUT):  passes_2m/<pass>.tif, offsets.json, raw_1m.tif, levelled_1m.tif, plus the
two dev entries grewingk_sonar_2023_regrid and grewingk_sonar_2023_levelled (pmtiles in
data/lidar_dev/pmtiles/, never published: dev only, and the survey itself is gated).
"""
import argparse, glob, itertools, json, os, subprocess, sys
from pathlib import Path
import numpy as np
import rasterio

REPO = Path(__file__).resolve().parents[3]
LAS = Path('/Volumes/Nunatak/Landslides/Grewingk/Sonar/Katie_results/Grewingk_Laz')
DELIVERED = LAS.parent / '1m_Grewingk.tif'
OUT = Path(os.environ.get('GS_OUT', '/Volumes/Nunatak/lidar_build/grewingk_sonar_level'))
PDAL = '/opt/homebrew/bin/pdal'
NODATA = -9999.0


def lattice():
    with rasterio.open(DELIVERED) as r:
        t, w, h = r.transform, r.width, r.height
    x0, y1 = t.c, t.f
    return x0, y1 - h * abs(t.e), x0 + w * t.a, y1          # minx, miny, maxx, maxy


def name(f):
    return Path(f).stem.replace(' - - ', '_')


def grid(src, dst, res, bounds, offset=0.0):
    """pdal: mean (and count) of Z on a res-m lattice over bounds; Z - offset first."""
    pipe = [str(src)]
    if offset:
        pipe.append({'type': 'filters.assign', 'value': 'Z = Z - (%.6f)' % offset})
    pipe.append({'type': 'writers.gdal', 'filename': str(dst), 'resolution': res, 'radius': res * 0.7072,
                 'output_type': 'mean,count', 'data_type': 'float32', 'nodata': NODATA,
                 'bounds': pdal_bounds(bounds, res)})
    return pipe


def pdal_bounds(b, res):
    # writers.gdal makes floor((max-min)/res)+1 cells from minx/miny, so a max on the lattice edge
    # adds a row and a column (and shifts the top edge up one cell): give it max - res.
    return '([%r,%r],[%r,%r])' % (b[0], b[2] - res, b[1], b[3] - res)


def run_pipe(pipe):
    subprocess.run([PDAL, 'pipeline', '--stdin'], input=json.dumps(pipe), text=True, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def offsets(files, bounds):
    d2 = OUT / 'passes_2m'; d2.mkdir(parents=True, exist_ok=True)
    M = []
    for f in files:
        dst = d2 / (name(f) + '.tif')
        if not dst.exists():
            run_pipe(grid(f, dst, 2.0, bounds))
        with rasterio.open(dst) as r:
            m, c = r.read(1), r.read(2)
        M.append(np.where((c >= 4) & (m != NODATA), m, np.nan))
    M = np.array(M)
    pairs = []
    for i, j in itertools.combinations(range(len(files)), 2):
        ok = np.isfinite(M[i]) & np.isfinite(M[j])
        if ok.sum() >= 30:
            d = M[i][ok] - M[j][ok]
            pairs.append((i, j, int(ok.sum()), float(np.median(d))))
    linked = sorted({p[0] for p in pairs} | {p[1] for p in pairs})
    k = {s: n for n, s in enumerate(linked)}
    A = np.zeros((len(pairs) + 1, len(linked))); b = np.zeros(len(pairs) + 1); w = np.ones(len(pairs) + 1)
    for r, (i, j, n, d) in enumerate(pairs):
        A[r, k[i]], A[r, k[j]], b[r], w[r] = 1, -1, d, np.sqrt(min(n, 500))
    A[-1, :], w[-1] = 1, 100.0                                  # sum of offsets = 0
    x = np.linalg.lstsq(A * w[:, None], b * w, rcond=None)[0]
    before = np.array([p[3] for p in pairs]); after = before - A[:-1] @ x
    off = {name(files[s]): (round(float(x[k[s]]), 4) if s in k else 0.0) for s in range(len(files))}
    rep = dict(passes=len(files), pairs=len(pairs), unlinked=[name(files[s]) for s in range(len(files)) if s not in k],
               pair_median_abs_before=round(float(np.median(abs(before))), 3),
               pair_p90_abs_before=round(float(np.percentile(abs(before), 90)), 3),
               pair_max_abs_before=round(float(abs(before).max()), 3),
               pair_median_abs_after=round(float(np.median(abs(after))), 3),
               pair_p90_abs_after=round(float(np.percentile(abs(after), 90)), 3),
               offset_sd=round(float(np.std(x)), 3), offset_min=round(float(x.min()), 3), offset_max=round(float(x.max()), 3),
               sign='levelled Z = delivered Z - offset', offsets=off)
    (OUT / 'offsets.json').write_text(json.dumps(rep, indent=1))
    return rep


def merged(files, off, dst, bounds):
    """All passes into one 1 m mean grid, each pass shifted by its offset (0 = raw)."""
    readers = []
    for n, f in enumerate(files):
        o = off.get(name(f), 0.0) if off else 0.0
        readers.append({'type': 'readers.las', 'filename': str(f), 'tag': 'r%d' % n})
        if o:
            readers.append({'type': 'filters.assign', 'inputs': ['r%d' % n], 'value': 'Z = Z - (%.6f)' % o, 'tag': 'a%d' % n})
    tags = [('a%d' % n) if (off and off.get(name(f), 0.0)) else ('r%d' % n) for n, f in enumerate(files)]
    pipe = readers + [{'type': 'filters.merge', 'inputs': tags, 'tag': 'm'},
                      {'type': 'writers.gdal', 'inputs': ['m'], 'filename': str(dst) + '.tmp.tif', 'resolution': 1.0,
                       'radius': 0.7072, 'output_type': 'mean', 'data_type': 'float32', 'nodata': NODATA,
                       'bounds': pdal_bounds(bounds, 1.0)}]
    run_pipe(pipe)
    subprocess.run(['/opt/homebrew/bin/gdal_translate', '-q', '-of', 'COG', '-a_srs', 'EPSG:6334', '-a_nodata', str(NODATA),
                    '-co', 'COMPRESS=DEFLATE', '-co', 'PREDICTOR=3', str(dst) + '.tmp.tif', str(dst)], check=True)
    os.remove(str(dst) + '.tmp.tif')


def viewer(rep):
    os.environ['LIDAR_BUILD'] = str(OUT / 'viewer_build')
    os.environ['LIDAR_PM_OUT'] = str(REPO / 'data' / 'lidar_dev' / 'pmtiles')
    sys.path.insert(0, str(REPO / 'tools' / 'lidar')); import build_lidar as B
    from rasterio.warp import transform_bounds
    env = B.clean_env()
    feats = []
    for sid, src, title, note in (
        ('grewingk_sonar_2023_regrid', OUT / 'raw_1m.tif', 'Grewingk Lake 2023 sonar -- regridded as delivered (dev)',
         'All 106 passes re-gridded here at 1 m (mean, 0.71 m radius) on the delivered grid\'s lattice, heights untouched: '
         'the reference for the levelled copy, made the same way so their difference is only the leveling.'),
        ('grewingk_sonar_2023_levelled', OUT / 'levelled_1m.tif', 'Grewingk Lake 2023 sonar -- levelled pass by pass (dev)',
         'Each pass shifted by one constant from a least-squares fit of pass-to-pass overlaps (offsets sum to zero, so the '
         'mean level is unchanged). Overlap disagreement: median %.2f m / 90th pct %.2f m before, %.2f / %.2f m after; '
         'offsets sd %.2f m, range %+.2f..%+.2f m. tools/lidar/grewingk_sonar/level_passes.py; offsets.json beside the grid.'
         % (rep['pair_median_abs_before'], rep['pair_p90_abs_before'], rep['pair_median_abs_after'], rep['pair_p90_abs_after'],
            rep['offset_sd'], rep['offset_min'], rep['offset_max']))):
        ds = dict(id=sid, title=title, min_zoom=8, max_zoom=17, target_epsg=6334)
        pm = B.build_pmtiles(ds, B.build_web(ds, src, env))
        spm = B.build_pmtiles(ds, B.build_slope_web(ds, src, env, B.merc_bounds(src, env)), suffix='_slope')
        with rasterio.open(src) as s:
            b = transform_bounds(s.crs, 'EPSG:4326', *s.bounds); a = s.read(1); ok = a != NODATA
        feats.append({'type': 'Feature',
                      'geometry': {'type': 'Polygon', 'coordinates': [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]]},
                      'properties': dict(id=sid, title=title, region='Kenai Peninsula', year=2023, product='Bathymetry (sonar)',
                                         native_res_m=1.0, coverage_km2=round(float(ok.sum()) * 1e-6, 3), horizontal_crs='EPSG:6334',
                                         vertical_datum='NAVD88 (GEOID12B)', min_zoom=8, max_zoom=17, bounds=[round(v, 6) for v in b],
                                         pmtiles_url=f'/lidar/dev/pmtiles/{sid}.pmtiles', pmtiles_bytes=pm.stat().st_size,
                                         slope_url=f'/lidar/dev/pmtiles/{sid}_slope.pmtiles', slope_bytes=spm.stat().st_size,
                                         slope_step=0.5, tiles_url=None, slope_tiles_url=None, fill_mode='holes', ortho_url=None,
                                         cog_url=None, z_min=float(a[ok].min()), z_max=float(a[ok].max()),
                                         source='Katie Hughes, 2023 Norbit multibeam (Qinsy .las), gated; dev comparison only',
                                         notes=note)})
    cat = REPO / 'data' / 'lidar_dev' / 'catalog.geojson'
    fc = json.loads(cat.read_text())
    ids = {f['properties']['id'] for f in feats}
    fc['features'] = [f for f in fc['features'] if f['properties']['id'] not in ids] + feats
    tmp = cat.with_suffix('.tmp'); tmp.write_text(json.dumps(fc)); os.replace(tmp, cat)
    print('dev catalogue: %d surveys' % len(fc['features']))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--no-viewer', action='store_true')
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    files = sorted(glob.glob(str(LAS / '*.las')))
    bounds = lattice()
    rep = offsets(files, bounds)
    print({k: v for k, v in rep.items() if k != 'offsets'})
    for dst, off in ((OUT / 'raw_1m.tif', None), (OUT / 'levelled_1m.tif', rep['offsets'])):
        merged(files, off, dst, bounds)
    if not a.no_viewer:
        viewer(rep)


if __name__ == '__main__':
    main()
