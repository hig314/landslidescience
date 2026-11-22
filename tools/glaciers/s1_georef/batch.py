"""Stack + simulate + match every site video, one at a time (stack.py peaks
near 10 GB on a 130-frame video, so no parallelism). Resumable: a video whose
match.json exists is skipped.

usage: batch.py LIST [--half 30]
LIST lines: "<n> /glaciers/video/<id>.mp4 [lon, lat] <name>" (the anchor point)
Writes /Volumes/Nunatak/glacier_s1_georef/v/<id>/ and ref/<id>.npz, rgi json."""
import argparse, json, math, re, subprocess, sys, time
from pathlib import Path
ROOT = Path('/Volumes/Nunatak/glacier_s1_georef')
HERE = Path(__file__).resolve().parent
PY = '/opt/anaconda3/bin/python'


def run(*args, log):
    log.write('> ' + ' '.join(str(x) for x in args[:4]) + '\n'); log.flush()
    subprocess.run([str(x) for x in args], check=True, stdout=log, stderr=subprocess.STDOUT)


def rgi_export(box, out):
    sql = ("import json\nfrom inventory.views import _get_conn,_put_conn\nc=_get_conn();cur=c.cursor()\n"
           "cur.execute(\"select id,name,ST_AsGeoJSON(ST_Transform(geom,3857),1) from glaciers where geom && ST_MakeEnvelope(%f,%f,%f,%f,4326)\")\n"
           "print(json.dumps([[x,y,json.loads(g)] for x,y,g in cur.fetchall()]))\nc.rollback();_put_conn(c)\n") % tuple(box)
    o = subprocess.run(['docker', 'exec', 'landslidescience-web-1', 'python', 'manage.py', 'shell', '-c', sql],
                       capture_output=True, text=True, check=True)
    out.write_text(o.stdout.strip().splitlines()[-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('list'); ap.add_argument('--half', type=float, default=30.0)
    a = ap.parse_args()
    rows = []
    for ln in open(a.list):
        m = re.match(r'\s*\d+\s+(\S+)\s+\[([-\d.]+),\s*([-\d.]+)\]\s*(.*)', ln)
        if m:
            rows.append((m.group(1), float(m.group(2)), float(m.group(3)), m.group(4).strip()))
    (ROOT / 'v').mkdir(exist_ok=True)
    for i, (src, lon, lat, name) in enumerate(rows):
        vid_id = Path(src).stem
        d = ROOT / 'v' / vid_id; d.mkdir(exist_ok=True)
        if (d / 'match.json').exists():
            continue
        (d / 'meta.json').write_text(json.dumps(dict(id=vid_id, src=src, anchor=[lon, lat], name=name)))
        t0 = time.time()
        with open(d / 'run.log', 'w') as log:
            try:
                mp4 = ROOT / 'videos' / (vid_id + '.mp4')
                if not mp4.exists():
                    run('curl', '-sf', '-o', mp4, 'https://landslidescience.org' + src, log=log)
                if not (d / 'stack.npz').exists():
                    run(PY, '-I', HERE / 'stack.py', mp4, d, log=log)
                dlat = a.half / 111.0; dlon = a.half / (111.0 * math.cos(math.radians(lat)))
                box = [lon - dlon, lat - dlat, lon + dlon, lat + dlat]
                ref = ROOT / 'ref' / (vid_id + '.npz')
                if not ref.exists():
                    run(PY, '-I', HERE / 'sim.py', *box, ref, '--res', 20, log=log)
                rgi = ROOT / 'ref' / (vid_id + '_rgi.json')
                if not rgi.exists():
                    rgi_export(box, rgi)
                run(PY, '-I', HERE / 'match.py', d / 'stack.npz', ref, 'asc', d / 'match.json', '--merc', 10, log=log)
                print('%2d/%d %s %-28s %.0f s' % (i + 1, len(rows), vid_id, name, time.time() - t0), flush=True)
            except Exception as e:
                print('%2d/%d %s %-28s FAILED %s' % (i + 1, len(rows), vid_id, name, e), flush=True)


if __name__ == '__main__':
    main()
