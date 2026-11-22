"""Place Drive-folder videos that are not on the site yet (the review
manifest's candidates), from a name-based anchor.

CAND.json: [{id (Drive id), name, site, orbit, glacier, ...}] (cand/strong.json)
ANCHORS.json: {id: [lon, lat]} - the downstream end of the named glacier's
main RGI centreline, as `s1_videos` would anchor it.
Per video, under v/<drive id>/: download once (Drive allows a few downloads a
day per file), stack.py, sim.py (+-30 km), the RGI export, match2.py (the
anchor as its soft prior), fine.py. Resumable; one at a time (stack.py ~5 GB).

usage: cand_batch.py CAND.json ANCHORS.json [--prior-km 3]"""
import argparse, json, math, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from batch import ROOT, HERE, PY, run, rgi_export

DRIVE_GET = 'https://drive.usercontent.google.com/download?id={id}&export=download&confirm=t'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cand'); ap.add_argument('anchors'); ap.add_argument('--prior-km', default='3'); ap.add_argument('--half', type=float, default=30.0)
    a = ap.parse_args()
    cands = json.load(open(a.cand)); anchors = json.load(open(a.anchors))
    for k, c in enumerate(cands):
        i = c['id']; d = ROOT / 'v' / i; d.mkdir(parents=True, exist_ok=True)
        if (d / 'fine.json').exists():
            continue
        lon, lat = anchors[i]
        (d / 'meta.json').write_text(json.dumps(dict(id=i, src='drive:' + i, anchor=[lon, lat], glacier=c.get('glacier'),
                                                     name='%s · Sentinel-1 %s, monthly' % (c['site'], c['orbit'].lower()))))
        t0 = time.time()
        with open(d / 'run.log', 'a') as log:
            try:
                mp4 = ROOT / 'videos' / (i + '.mp4')
                if not mp4.exists():
                    run('curl', '-sfL', '-o', mp4, DRIVE_GET.format(id=i), log=log)
                    if mp4.stat().st_size < 1e6:                 # a quota or error page, not a video
                        mp4.rename(mp4.with_suffix('.html')); raise RuntimeError('Drive returned no video')
                if not (d / 'stack.npz').exists():
                    run(PY, '-I', HERE / 'stack.py', mp4, d, log=log)
                dlat = a.half / 111.0; dlon = a.half / (111.0 * math.cos(math.radians(lat)))
                box = [lon - dlon, lat - dlat, lon + dlon, lat + dlat]
                ref = ROOT / 'ref' / (i + '.npz')
                if not ref.exists():
                    run(PY, '-I', HERE / 'sim.py', *box, ref, '--res', 20, log=log)
                rgi = ROOT / 'ref' / (i + '_rgi.json')
                if not rgi.exists():
                    rgi_export(box, rgi)
                run(PY, '-I', HERE / 'match2.py', d / 'stack.npz', ref, d / 'meta.json', d / 'm2.json', '--prior-km', a.prior_km, log=log)
                run(PY, '-I', HERE / 'fine.py', i, d / 'm2.json', d / 'fine.json', log=log)
                b = json.load(open(d / 'fine.json'))
                print('%2d/%d %-32s %s/%s z %.1f margin %.1f fine %.0f m  %.0f s' % (k + 1, len(cands), c['site'][:32], b['orbit'], b['water'],
                      b['z'], b['margin'], b['shift_m'], time.time() - t0), flush=True)
            except Exception as e:
                print('%2d/%d %-32s FAILED %s' % (k + 1, len(cands), c['site'][:32], e), flush=True)


if __name__ == '__main__':
    main()
