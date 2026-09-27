"""Full-survey run of the reclassification chain (apply.py) over KBay 2023/24 (Hig, 2026-09-26).

    python model/fullrun.py --workers 6            # loop until every unit is done
    python model/fullrun.py --status               # counts
    python model/fullrun.py --one u_583_6582       # a single unit, foreground (testing)

UNITS: a 1 km grid (UTM 5N, NAD83(2011)) over every COPC tile footprint (full/tileindex.json,
read from the tile headers by ranged requests). Each unit is processed with a UNIT_BUF (60 m)
buffer -- the widest window any step uses is the blend's 15 m disk and 15-cell medians -- and only
its 1 km core is kept, so neighbouring units meet without seams.

READY: a unit runs only once every COPC tile touching its buffered box is on disk at its full S3
size (fetch.py renames a tile into place only when complete), so this can run alongside the
download and simply waits for tiles.

RESUMABLE: claims are atomic directories (full/claims/<unit>); finished units have
full/out/<unit>/DONE; a unit claimed but not DONE when the runner starts is stale (a crash or a
restart) and is retried. Failures go to full/failed/<unit>.txt and are not retried automatically.
Stop at any time; rerun the same command to continue.

PER UNIT: merge the COPC tiles (bounds-cropped) into full/units/<unit>.laz -> apply.py <unit> ->
crop dtm_final / fill_mask / rock_mask / vendor_dtm / blend_w to the core -> full/out/<unit>/ ->
delete site/<unit> and the unit laz (the intermediates are several GB).

HOLES: apply.py fills enclosed holes within each buffered unit; holes cut by a unit edge are left
for the MOSAIC pass (fill_holes on the assembled grid), which is a separate step at the end.
"""
import argparse, json, os, shutil, subprocess, sys, time, traceback
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

ROOT = Path('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); os.chdir(ROOT); sys.path.insert(0, str(ROOT / 'model'))
import sitekit as K

TILES = Path('/Volumes/Powder/lidar_src/kbay_2023_laz/tiles')
FULL = ROOT / 'full'
for d in ('units', 'out', 'claims', 'failed', 'logs'):
    (FULL / d).mkdir(parents=True, exist_ok=True)
PY = '/opt/anaconda3/bin/python'
KEEP = ['dtm_final.tif', 'fill_mask.tif', 'rock_mask.tif', 'vendor_dtm.tif', 'blend_w.tif', 'timing.json',
        'EMPTY']   # apply.py's note for a unit with no vendor ground: DONE with no dtm_final (mosaic skips it)


def index():
    return json.load(open(FULL / 'tileindex.json'))


def units(idx):
    u = set()
    for v in idx.values():
        for ex in range(int(v['minx'] // 1000), int(v['maxx'] // 1000) + 1):
            for ny in range(int(v['miny'] // 1000), int(v['maxy'] // 1000) + 1):
                u.add(f'u_{ex}_{ny}')
    return sorted(u)


def tiles_for(name, idx):
    x0, x1, y0, y1 = K.site_info(name)['bounds']
    return [k for k, v in idx.items() if v['maxx'] > x0 and v['minx'] < x1 and v['maxy'] > y0 and v['miny'] < y1]


def ready(name, idx):
    ts = tiles_for(name, idx)
    return bool(ts) and all((TILES / t).is_file() and (TILES / t).stat().st_size == idx[t]['size'] for t in ts)


def done(name):
    return (FULL / 'out' / name / 'DONE').exists()


def claim(name):
    try:
        (FULL / 'claims' / name).mkdir(); return True
    except FileExistsError:
        return False


def crop(src, dst, bounds):
    x0, x1, y0, y1 = bounds
    subprocess.run(['/opt/homebrew/bin/gdal_translate', '-q', '-a_srs', 'EPSG:6334', '-projwin', str(x0), str(y1), str(x1), str(y0),
                    '-co', 'COMPRESS=DEFLATE', '-co', 'PREDICTOR=3', '-co', 'TILED=YES', str(src), str(dst)],
                   check=True, env={k: v for k, v in os.environ.items() if k not in ('PROJ_LIB', 'PROJ_DATA')})


def process(name):
    t0 = time.time(); idx = index(); log = FULL / 'logs' / f'{name}.log'
    S = K.site_info(name); x0, x1, y0, y1 = S['bounds']
    core = (x0 + K.UNIT_BUF, x1 - K.UNIT_BUF, y0 + K.UNIT_BUF, y1 - K.UNIT_BUF)
    laz = ROOT / S['kbay']; site = ROOT / 'site' / name; out = FULL / 'out' / name
    try:
        ts = tiles_for(name, idx)
        pipe = [{"type": "readers.copc", "filename": str(TILES / t), "bounds": f"([{x0},{x1}],[{y0},{y1}])", "tag": f"r{i}"}
                for i, t in enumerate(ts)]
        pipe += [{"type": "filters.merge", "inputs": [f"r{i}" for i in range(len(ts))]},
                 {"type": "writers.las", "filename": str(laz), "compression": "laszip", "minor_version": 4,
                  "dataformat_id": 6, "extra_dims": "all", "a_srs": "EPSG:6334+5703", "scale_x": 0.01,
                  "scale_y": 0.01, "scale_z": 0.01, "offset_x": (x0 + x1) // 2, "offset_y": (y0 + y1) // 2, "offset_z": 0}]
        pj = FULL / 'logs' / f'{name}_pull.json'; json.dump(pipe, open(pj, 'w'))
        with open(log, 'w') as lf:
            subprocess.run([K.PDAL, 'pipeline', str(pj)], check=True, stdout=lf, stderr=subprocess.STDOUT)
            lf.write(f'pulled {len(ts)} tiles in {time.time()-t0:.0f}s\n'); lf.flush()
            site.mkdir(parents=True, exist_ok=True)
            subprocess.run([PY, 'model/apply.py', name], check=True, stdout=lf, stderr=subprocess.STDOUT)
        tmp = out.with_name(name + '.tmp'); shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir()
        for f in KEEP:
            p = site / f
            if not p.exists(): continue
            if f.endswith('.tif'): crop(p, tmp / f, core)
            else: shutil.copy(p, tmp / f)
        (tmp / 'DONE').write_text(f'{time.time()-t0:.0f} s, {len(ts)} tiles\n')
        shutil.rmtree(out, ignore_errors=True); tmp.rename(out)
        return name, 'ok', time.time() - t0
    except Exception as e:
        (FULL / 'failed' / f'{name}.txt').write_text(traceback.format_exc() + '\n--- log tail ---\n' +
                                                   (log.read_text()[-3000:] if log.exists() else ''))
        return name, f'FAILED {e}', time.time() - t0
    finally:
        shutil.rmtree(site, ignore_errors=True); laz.unlink(missing_ok=True)


def status(idx):
    us = units(idx); d = sum(done(u) for u in us); f = len(list((FULL / 'failed').glob('*.txt')))
    r = sum(1 for u in us if not done(u) and ready(u, idx))
    print(f'units {len(us)} | done {d} | failed {f} | ready now {r} | waiting on tiles {len(us)-d-r}', flush=True)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--status', action='store_true'); ap.add_argument('--one')
    a = ap.parse_args(); idx = index()
    if a.status: return status(idx)
    if a.one: print(process(a.one)); return
    # stale claims from a previous run (crash / restart): release so they are retried
    for c in (FULL / 'claims').iterdir():
        if not done(c.name) and not (FULL / 'failed' / f'{c.name}.txt').exists():
            c.rmdir()
    us = units(idx); print(f'{len(us)} units', flush=True)
    with ProcessPoolExecutor(a.workers) as ex:
        running = {}
        while True:
            todo = [u for u in us if not done(u) and not (FULL / 'failed' / f'{u}.txt').exists()
                    and not (FULL / 'claims' / u).exists() and ready(u, idx)]
            while todo and len(running) < a.workers:
                u = todo.pop(0)
                if claim(u): running[ex.submit(process, u)] = u
            fin = [f for f in running if f.done()]
            for f in fin:
                n, st, dt = f.result(); del running[f]
                print(f'{time.strftime("%m-%d %H:%M")} {n} {st} {dt:.0f}s', flush=True)
            if not running and not todo:
                left = [u for u in us if not done(u) and not (FULL / 'failed' / f'{u}.txt').exists()]
                if not left: break
                status(idx); time.sleep(300)      # waiting on the download
            else:
                time.sleep(5)
    status(idx); print('ALL UNITS PROCESSED -- next: mosaic + mosaic-level hole fill', flush=True)


if __name__ == '__main__':
    main()
