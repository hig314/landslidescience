#!/usr/bin/env python3
"""PILOT (2026-10-20, Hig): the topobathy from a JOINT point cloud, not from a stack of interpolated rasters.

    OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES /opt/anaconda3/bin/python3 tools/lidar/topobathy/joint_pilot.py units [--workers 3]
    ... joint_pilot.py units2019       # Grewingk 2019 (third survey) into the same units
    ... joint_pilot.py mosaic          # units -> per-survey grids over the window
    ... joint_pilot.py fuse            # cross-check, vintage map, pooled surface, support, sigma
    ... joint_pilot.py support         # joint_nland.tif alone (surveys that saw ground per cell; fuse writes it too)
    ... joint_pilot.py layer           # -> layer_z / layer_class for the composite chain (voids nearest water are water)

Plan: TOPOBATHY_PLAN.md "A point-first build". Window: Grewingk outwash + creek + Hig's two bad-return sites
(2021 at 59.62233,-151.18928; 2023 at 59.58211,-151.17475), x 599-607 km, y 6604-6614 km (EPSG:6334).

Stage UNITS, per 1 km unit (+30 m buffer), each survey's GROUND CANDIDATES gridded at 1 m with their SUPPORT --
a cell with no return has no value, never an interpolation:
  kbay2023   NOAA OCM 10418 COPC (Powder). Ground = the points that support our reclass v2 surface (within
             +/-GROUND_TOL of it at the point) among classes 1/2/22: v2's per-cell decision carried back to the
             points. Water = class 9.
  gk2021     DGGS Grewingk 2021 LAZ. Ground = vendor class 2, z + 0.36 m (measured offset, to be re-checked here).
             Water = class 9. Also the highest return per cell (vegetation height, for the floodplain).
Per survey and cell: n ground, median ground z, min ground z, n water, median water z, max of all returns.
"""
import argparse, json, os, subprocess, sys, time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np

WIN = tuple(int(v) for v in os.environ["JOINT_WIN"].split(",")) if os.environ.get("JOINT_WIN") else (599000, 6604000, 607000, 6614000)   # JOINT_WIN=x0,y0,x1,y1 extends the pilot's window (units already done are skipped)
BUF = 30
OUT = Path(os.environ.get('JOINT_OUT', '/Volumes/Powder/lidar_build/joint_pilot'))   # Powder: Nunatak filled up 2026-10-20 (temp points are GBs a unit)
PDAL = '/opt/homebrew/bin/pdal'
KB_TILES = Path('/Volumes/Powder/lidar_src/kbay_2023_laz/tiles')
KB_INDEX = Path('/Volumes/Nunatak/lidar_build/kbay_reclass_test/full/tileindex.json')
KB_V2 = '/Volumes/Nunatak/lidar_build/cog/kbay_2023_reclass_v2.tif'
GK_LAZ = Path('/Volumes/Nunatak/lidar_build/kbay_reclass_test/grewingk_2021/laz')
GK_INDEX = Path('/Volumes/Nunatak/lidar_build/composite/grewingk/obs/nohf_full/index.csv')
GK_DZ = 0.36
G19_LAZ = Path('/Volumes/Powder/lidar_src/grewingk_2019/dds4/grewingk_2019/laz')   # DGGS RDF 2021-6, flown 2019-06-03
G19_INDEX = Path('/Volumes/Powder/lidar_src/grewingk_2019/tiles.txt')            # name minx miny maxx maxy count
GROUND_TOL = 0.25
GRIDS = ('n_g', 'z_g', 'zmin_g', 'n_w', 'z_w', 'zmax')


def env():
    return {k: v for k, v in os.environ.items() if k not in ('PROJ_LIB', 'PROJ_DATA')}


def units():
    return [(x, y) for x in range(WIN[0], WIN[2], 1000) for y in range(WIN[1], WIN[3], 1000)]


def gk_tiles(b):
    import csv, re
    out = []
    for r in csv.DictReader(open(GK_INDEX)):
        xs = [float(v) for v in re.findall(r'[-\d.]+', r['WKT'])]
        X, Y = xs[0::2], xs[1::2]
        if max(X) > b[0] and min(X) < b[2] and max(Y) > b[1] and min(Y) < b[3]:
            f = GK_LAZ / r['FileName'].replace('.las', '.laz')
            if f.exists(): out.append(f)
    return out


def pull(srcs, reader, b, out, expr, srs=None):
    """PDAL: read the sources inside b, keep `expr`, write X,Y,Z,Classification,ReturnNumber,NumberOfReturns.
    srs: the files' CRS when it is not the working UTM (the Seldovia 2019 COPC is Alaska Albers): the COPC read is
    bounded in that CRS (with a 50 m margin), then reprojected to EPSG:6334 before the crop."""
    if not srcs: return None
    rb = b
    if srs:
        from rasterio.warp import transform_bounds
        rb = transform_bounds('EPSG:6334', srs, b[0] - 50, b[1] - 50, b[2] + 50, b[3] + 50)
    pipe = [{"type": reader, "filename": str(s), "tag": f"r{i}",
             **({"bounds": f"([{rb[0]},{rb[2]}],[{rb[1]},{rb[3]}])"} if reader == 'readers.copc' else {})} for i, s in enumerate(srcs)]
    pipe += [{"type": "filters.merge", "inputs": [f"r{i}" for i in range(len(srcs))]}]
    if srs: pipe += [{"type": "filters.reprojection", "in_srs": srs, "out_srs": "EPSG:6334"}]
    pipe += [{"type": "filters.crop", "bounds": f"([{b[0]},{b[2]}],[{b[1]},{b[3]}])"},
             {"type": "filters.expression", "expression": expr},
             {"type": "writers.arrow", "filename": str(out), "format": "feather"}]
    pj = out.with_suffix('.json'); pj.write_text(json.dumps(pipe))
    subprocess.run([PDAL, 'pipeline', str(pj)], check=True, env=env(), capture_output=True)
    import pyarrow.feather as F
    t = F.read_table(out)
    cols = {c.lower(): c for c in t.column_names}
    g = lambda n: np.asarray(t.column(cols[n.lower()]))
    if 'xyz' in cols:
        xyz = np.asarray(t.column(cols['xyz']).combine_chunks().flatten()).reshape(-1, 3); x, y, z = xyz.T
    else:
        x, y, z = g('X'), g('Y'), g('Z')
    r = dict(x=x, y=y, z=z.astype(np.float64), c=g('Classification'), rn=g('ReturnNumber'), nr=g('NumberOfReturns'))
    out.unlink(); pj.unlink()
    return r


def grid(p, gmask, wmask, b, H, W, zall=True):
    """Per-cell ground count/median/min, water count/median, max of all returns -- no interpolation."""
    col = ((p['x'] - b[0]) // 1).astype(np.int64); row = ((b[3] - p['y']) // 1).astype(np.int64)
    ok = (col >= 0) & (col < W) & (row >= 0) & (row < H)
    cid = row * W + col
    o = {k: np.full(H * W, np.nan, np.float32) for k in GRIDS}
    def med(m, nk, zk, mink=None):
        m = m & ok
        if not m.any(): o[nk][:] = 0; return
        ci, zz = cid[m], p['z'][m]
        srt = np.lexsort((zz, ci)); ci, zz = ci[srt], zz[srt]
        u, start, cnt = np.unique(ci, return_index=True, return_counts=True)
        o[nk][:] = 0; o[nk][u] = cnt
        o[zk][u] = zz[start + cnt // 2]                     # median (upper)
        if mink: o[mink][u] = zz[start]
    med(gmask, 'n_g', 'z_g', 'zmin_g')
    med(wmask, 'n_w', 'z_w')
    if zall:
        m = ok & (p['c'] != 7) & (p['c'] != 18)
        zm = np.full(H * W, -np.inf); np.maximum.at(zm, cid[m], p['z'][m]); zm[~np.isfinite(zm)] = np.nan
        o['zmax'] = zm.astype(np.float32)
    return {k: v.reshape(H, W) for k, v in o.items()}


def do_unit_2019(u):
    """Grewingk 2019 (a third survey, Hig 2026-10-20): vendor class 2 as ground, class 9 water, no shift yet --
    the alignment is measured in the fuse against 2023 and 2021 on stable ground."""
    x, y = u; b = (x - BUF, y - BUF, x + 1000 + BUF, y + 1000 + BUF); H = W = 1000 + 2 * BUF
    d = OUT / 'units' / f'u_{x // 1000}_{y // 1000}'; d.mkdir(parents=True, exist_ok=True)
    if (d / 'gk2019.npz').exists() or (d / 'NO2019').exists(): return u, 'skip'
    ts = []
    for line in open(G19_INDEX):
        n, x0, y0, x1, y1, _ = line.split()
        if float(x1) > b[0] and float(x0) < b[2] and float(y1) > b[1] and float(y0) < b[3]: ts.append(G19_LAZ / n)
    p = pull(ts, 'readers.las', b, d / 'g19.feather', 'Classification != 7 && Classification != 18')
    if p is None or not len(p['z']):
        (d / 'NO2019').write_text(''); return u, 'none'
    g = grid(p, p['c'] == 2, p['c'] == 9, b, H, W)
    np.savez_compressed(d / 'gk2019.npz', **g)
    return u, dict(points=int(len(p['z'])), ground=int((p['c'] == 2).sum()), water=int((p['c'] == 9).sum()), tiles=len(ts))


def do_unit(u):
    import rasterio
    from rasterio.windows import from_bounds
    x, y = u; b = (x - BUF, y - BUF, x + 1000 + BUF, y + 1000 + BUF); H = W = 1000 + 2 * BUF
    d = OUT / 'units' / f'u_{x // 1000}_{y // 1000}'; d.mkdir(parents=True, exist_ok=True)
    if (d / 'DONE').exists(): return u, 'skip'
    t0 = time.time(); info = {}
    # --- KBay 2023: ground = points supporting the reclass v2 surface
    idx = json.load(open(KB_INDEX))
    ts = [KB_TILES / k for k, v in idx.items() if v['maxx'] > b[0] and v['minx'] < b[2] and v['maxy'] > b[1] and v['miny'] < b[3]]
    p = pull(ts, 'readers.copc', b, d / 'kb.feather',
             'Classification != 7 && Classification != 18 && Classification != 6 && Classification != 17')
    if p is not None:
        with rasterio.open(KB_V2) as s:
            v2 = s.read(1, window=from_bounds(*b, s.transform), boundless=True, fill_value=s.nodata).astype(np.float64)
            v2[v2 == s.nodata] = np.nan
        col = np.clip(((p['x'] - b[0]) // 1).astype(int), 0, W - 1); row = np.clip(((b[3] - p['y']) // 1).astype(int), 0, H - 1)
        dz = p['z'] - v2[row, col]
        gm = np.isin(p['c'], (1, 2, 22)) & (np.abs(dz) <= GROUND_TOL)
        g = grid(p, gm, p['c'] == 9, b, H, W)
        np.savez_compressed(d / 'kbay2023.npz', **g)
        info['kbay2023'] = dict(points=int(len(p['z'])), ground=int(gm.sum()), water=int((p['c'] == 9).sum()), tiles=len(ts))
        del p
    # --- Grewingk 2021: vendor ground + 0.36
    p = pull(gk_tiles(b), 'readers.las', b, d / 'gk.feather', 'Classification != 7 && Classification != 18')
    if p is not None and len(p['z']):
        p['z'] = p['z'] + GK_DZ
        g = grid(p, p['c'] == 2, p['c'] == 9, b, H, W)
        np.savez_compressed(d / 'gk2021.npz', **g)
        info['gk2021'] = dict(points=int(len(p['z'])), ground=int((p['c'] == 2).sum()), water=int((p['c'] == 9).sum()))
    info['seconds'] = round(time.time() - t0)
    (d / 'info.json').write_text(json.dumps(info)); (d / 'DONE').write_text('')
    return u, info


# ----------------------------------------------------------------------------------------- recipe-driven units
# (2026-10-29, for the whole-bay run): the surveys come from the recipe's joint.units block instead of the three
# constants above, so a box's recipe names what it reads. joint.py obs_lidar reads the units directory directly
# (src = the units dir, unit_file = <name>.npz), so no mosaic is needed.
#
#   "units": {"out": "/Volumes/Powder/lidar_build/kbay_joint/units", "buf": 30, "workers": 4,
#             "surveys": [
#               {"name": "kbay2023", "reader": "copc", "tiles": ".../kbay_2023_laz/tiles", "index": ".../tileindex.json",
#                "drop": [6, 7, 17, 18], "ground": {"classes": [1, 2, 22], "support": ".../kbay_2023_reclass_v2.tif", "tol_m": 0.25},
#                "water": [9]},
#               {"name": "homer2018", "reader": "copc", "tiles": ".../homer_2018_ncmp", "glob": "*.copc.laz",
#                "ground": {"classes": [2]}, "water": [41, 42], "bed": [40]},          -> homer2018.npz + homer2018_bed.npz
#               {"name": "kenai2008", "reader": "las", "tiles": ".../classified", "glob": "t_*.laz",
#                "index": {"regex": "t_(\\d+)_(\\d+)", "size_m": 2000}, "ground": {"classes": [2]}},
#               {"name": "gk2021", "reader": "las", "tiles": ..., "index": "<csv with WKT>", "shift_m": 0.36, ...}]}
#   index: a JSON {file: {minx, miny, maxx, maxy}} (the `index` stage writes <out>/index_<name>.json with pdal info),
#          a CSV with FileName + WKT (the Grewingk 2021 index), a TXT "name x0 y0 x1 y1 n" (Grewingk 2019), or
#          {"regex", "size_m"} for tiles named by their corner. Missing -> <out>/index_<name>.json (build it first).

def _spec(recipe):
    r = json.load(open(recipe)); u = r['joint']['units']
    u.setdefault('buf', 30); u['out'] = Path(u['out'])
    return u


def _index_path(u, s):
    ix = s.get('index')
    if isinstance(ix, dict) or (ix and Path(ix).exists()): return ix
    return u['out'] / f"index_{s['name']}.json"


def load_index(u, s):
    """-> list of (path, (minx, miny, maxx, maxy))."""
    import csv, re
    ix = _index_path(u, s); tiles = Path(s['tiles']); out = []
    if isinstance(ix, dict):
        rx = re.compile(ix['regex']); sz = float(ix['size_m'])
        for f in sorted(tiles.glob(s.get('glob', '*.laz'))):
            m = rx.search(f.name)
            if m: x, y = float(m.group(1)), float(m.group(2)); out.append((f, (x, y, x + sz, y + sz)))
        return out
    ix = Path(ix)
    if ix.suffix == '.json':
        for k, v in json.load(open(ix)).items():
            f = Path(k) if Path(k).is_absolute() else tiles / k
            out.append((f, (v['minx'], v['miny'], v['maxx'], v['maxy'])))
    elif ix.suffix == '.csv':
        for r in csv.DictReader(open(ix)):
            xs = [float(v) for v in re.findall(r'[-\d.]+', r['WKT'])]; X, Y = xs[0::2], xs[1::2]
            f = tiles / r['FileName'].replace('.las', '.laz')
            out.append((f, (min(X), min(Y), max(X), max(Y))))
    else:
        for line in open(ix):
            p = line.split()
            if len(p) >= 5: out.append((tiles / p[0], tuple(float(v) for v in p[1:5])))
    return [(f, b) for f, b in out if f.exists()]


def build_index(u, s):
    """pdal info --metadata per file -> <out>/index_<name>.json (skipped when the recipe names an existing index)."""
    ix = _index_path(u, s)
    if isinstance(ix, dict) or Path(ix).exists(): return ix, 0
    tiles = Path(s['tiles']); d = {}
    for f in sorted(tiles.glob(s.get('glob', '*.laz'))):
        r = subprocess.run([PDAL, 'info', '--metadata', str(f)], capture_output=True, text=True, env=env())
        try:
            m = json.loads(r.stdout)['metadata']
            bb = (m['minx'], m['miny'], m['maxx'], m['maxy'])
            if s.get('srs'):                                           # index in the working UTM whatever the files use
                from rasterio.warp import transform_bounds
                bb = transform_bounds(s['srs'], 'EPSG:6334', *bb)
            d[str(f.relative_to(tiles))] = dict(minx=bb[0], miny=bb[1], maxx=bb[2], maxy=bb[3], count=m.get('count'))
        except Exception as e:
            print(f'  {f.name}: {type(e).__name__} {str(e)[:60]}', flush=True)
    u['out'].mkdir(parents=True, exist_ok=True); Path(ix).write_text(json.dumps(d, indent=0))
    return ix, len(d)


def _savez(path, G):
    """tmp + rename: a unit file is either whole or absent (a run interrupted mid-write resumes cleanly)."""
    tmp = path.with_name(path.name + '.tmp.npz'); np.savez_compressed(tmp, **G); os.replace(tmp, path)


def do_unit_spec(args):
    """One unit, every survey of the spec: <name>.npz (ground/water) and <name>_bed.npz (bed classes) per survey."""
    import rasterio
    from rasterio.windows import from_bounds
    u, spec, idx = args
    x, y = u; buf = spec['buf']; b = (x - buf, y - buf, x + 1000 + buf, y + 1000 + buf); H = W = 1000 + 2 * buf
    d = spec['out'] / 'units' / f'u_{x // 1000}_{y // 1000}'; d.mkdir(parents=True, exist_ok=True)
    info = {}; t0 = time.time()
    for s in spec['surveys']:
        n = s['name']
        if (d / f'{n}.npz').exists() or (d / f'NO_{n}').exists(): continue
        ts = [f for f, bb in idx[n] if bb[2] > b[0] and bb[0] < b[2] and bb[3] > b[1] and bb[1] < b[3]]
        if not ts: (d / f'NO_{n}').write_text(''); continue
        drop = s.get('drop', [7, 18])
        expr = ' && '.join(f'Classification != {c}' for c in drop) or 'true'
        reader = {'copc': 'readers.copc', 'las': 'readers.las'}[s.get('reader', 'las')]
        try:
            p = pull(ts, reader, b, d / f'{n}.feather', expr, srs=s.get('srs'))
        except subprocess.CalledProcessError as e:
            info[n] = dict(error=str(e)[:120]); continue
        if p is None or not len(p['z']): (d / f'NO_{n}').write_text(''); continue
        if s.get('shift_m'): p['z'] = p['z'] + float(s['shift_m'])
        g = s.get('ground', {'classes': [2]})
        gm = np.isin(p['c'], g.get('classes', [2]))
        if g.get('support'):
            with rasterio.open(g['support']) as r:
                v = r.read(1, window=from_bounds(*b, r.transform), boundless=True, fill_value=r.nodata).astype(np.float64)
                v[v == r.nodata] = np.nan
            col = np.clip(((p['x'] - b[0]) // 1).astype(int), 0, v.shape[1] - 1); row = np.clip(((b[3] - p['y']) // 1).astype(int), 0, v.shape[0] - 1)
            gm &= np.abs(p['z'] - v[row, col]) <= float(g.get('tol_m', 0.25))
        wm = np.isin(p['c'], s.get('water', [9]))
        # a survey whose classes carry no water (Kenai 2008) takes its validated per-cell water classification
        # (water_raster: water_taught.py's class.vrt, 2 = water; held-out precision 0.97): ground returns in a cell the
        # model calls water are WATER returns (2026-10-29, Bluff Point: its sea-surface returns at -0.8 m stood as
        # land, one a cell, between the bed at -6 m -- the pimples and the chaotic terrain)
        if s.get('water_raster'):
            wr = s['water_raster']
            with rasterio.open(wr['path']) as r:
                cv = r.read(1, window=from_bounds(*b, r.transform), boundless=True, fill_value=0, out_shape=(H, W))
            col = np.clip(((p['x'] - b[0]) // 1).astype(int), 0, W - 1); row = np.clip(((b[3] - p['y']) // 1).astype(int), 0, H - 1)
            inw = np.isin(cv[row, col], wr.get('water', [2]))
            info.setdefault(n, {})['taught_water'] = int((gm & inw).sum())
            wm = wm | (gm & inw); gm = gm & ~inw
        G = grid(p, gm, wm, b, H, W); _savez(d / f'{n}.npz', G)
        info[n] = dict(info.get(n, {}), points=int(len(p['z'])), ground=int(gm.sum()), water=int(wm.sum()), tiles=len(ts))
        if s.get('bed'):
            bm = np.isin(p['c'], s['bed'])
            if bm.any():
                B = grid(p, bm, np.zeros(len(p['z']), bool), b, H, W, zall=False); _savez(d / f'{n}_bed.npz', B)
                info[n]['bed'] = int(bm.sum())
        del p
    info['seconds'] = round(time.time() - t0)
    if info: (d / 'info_spec.json').write_text(json.dumps(info))
    return u, info


def units_spec(recipe, workers, win=None):
    spec = _spec(recipe)
    w = win or spec.get('win') or WIN
    idx = {s['name']: load_index(spec, s) for s in spec['surveys']}
    for n, v in idx.items(): print(f'{n}: {len(v)} tiles indexed', flush=True)
    us = [(x, y) for x in range(int(w[0]), int(w[2]), 1000) for y in range(int(w[1]), int(w[3]), 1000)]
    print(f'{len(us)} units over {w} -> {spec["out"]}', flush=True)
    with ProcessPoolExecutor(workers) as ex:
        for u, info in ex.map(do_unit_spec, [(u, spec, idx) for u in us]): print(u, json.dumps(info), flush=True)


def mosaic():
    """Units (cores only) -> one grid per survey and quantity over the window."""
    H, W = WIN[3] - WIN[1], WIN[2] - WIN[0]
    for sv in ('kbay2023', 'gk2021', 'gk2019'):
        M = {k: np.full((H, W), np.nan, np.float32) for k in GRIDS}
        for x, y in units():
            f = OUT / 'units' / f'u_{x // 1000}_{y // 1000}' / f'{sv}.npz'
            if not f.exists(): continue
            g = np.load(f); r0 = WIN[3] - (y + 1000); c0 = x - WIN[0]
            for k in GRIDS: M[k][r0:r0 + 1000, c0:c0 + 1000] = g[k][BUF:BUF + 1000, BUF:BUF + 1000]
        np.savez_compressed(OUT / f'{sv}_grids.npz', **M)
        print(sv, {k: f'{np.isfinite(M[k]).mean():.2f}' for k in ('z_g', 'z_w', 'zmax')}, 'cells with ground/water/any return')


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('stage'); ap.add_argument('--workers', type=int, default=3); ap.add_argument('--tag', default='')
    ap.add_argument('--recipe', help='recipe with a joint.units block (stages index, units)')
    ap.add_argument('--win', type=float, nargs=4, metavar=('X0', 'Y0', 'X1', 'Y1'), help='units window (km-aligned), overrides the spec and JOINT_WIN')
    a = ap.parse_args()
    if a.stage == 'index':
        spec = _spec(a.recipe)
        for s in spec['surveys']:
            ix, n = build_index(spec, s); print(s['name'], ix, f'{n} files' if n else '(existing)', flush=True)
    elif a.stage == 'units' and a.recipe:
        units_spec(a.recipe, a.workers, a.win)
    elif a.stage == 'units':
        us = units(); print(f'{len(us)} units', flush=True)
        with ProcessPoolExecutor(a.workers) as ex:
            for u, info in ex.map(do_unit, us): print(u, json.dumps(info), flush=True)
    elif a.stage == 'units2019':
        us = units(); print(f'{len(us)} units', flush=True)
        with ProcessPoolExecutor(a.workers) as ex:
            for u, info in ex.map(do_unit_2019, us): print(u, json.dumps(info), flush=True)
    elif a.stage == 'mosaic':
        mosaic()
    elif a.stage == 'fuse':
        import joint_fuse; joint_fuse.main(OUT, WIN)
    elif a.stage == 'support':
        import joint_fuse; joint_fuse.support(OUT, WIN)
    elif a.stage == 'layer':
        import joint_fuse; joint_fuse.layer(OUT, WIN, a.tag)


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
