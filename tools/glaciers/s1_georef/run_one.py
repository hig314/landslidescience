"""One site video end to end: fetch from landslidescience.org, stack, simulate
3DEP around its anchor, match, overlay, RGI check.
usage: run_one.py NAME SRC_PATH LON LAT ORBIT [--half 45]
Writes /Volumes/Nunatak/glacier_s1_georef/<NAME>/ and ref/<NAME>.npz."""
import argparse, json, math, subprocess, sys
from pathlib import Path
ROOT = Path('/Volumes/Nunatak/glacier_s1_georef')
HERE = Path(__file__).resolve().parent
PY = '/opt/anaconda3/bin/python'
ap = argparse.ArgumentParser()
ap.add_argument('name'); ap.add_argument('src'); ap.add_argument('lon', type=float); ap.add_argument('lat', type=float); ap.add_argument('orbit')
ap.add_argument('--half', type=float, default=45.0, help='half-width of the search box, km')
a = ap.parse_args()
d = ROOT / a.name; d.mkdir(parents=True, exist_ok=True)
vid = ROOT / 'videos' / (a.name + '.mp4')
def run(*args):
    print('>', ' '.join(str(x) for x in args[1:3]), flush=True)
    subprocess.run([str(x) for x in args], check=True)
if not vid.exists():
    run('curl', '-sf', '-o', vid, 'https://landslidescience.org' + a.src)
if not (d / 'stack.npz').exists():
    run(PY, '-I', HERE / 'stack.py', vid, d)
dlat = a.half / 111.0; dlon = a.half / (111.0 * math.cos(math.radians(a.lat)))
box = [a.lon - dlon, a.lat - dlat, a.lon + dlon, a.lat + dlat]
ref = ROOT / 'ref' / (a.name + '.npz')
if not ref.exists():
    run(PY, '-I', HERE / 'sim.py', *box, ref, '--res', 20)
rgi = ROOT / 'ref' / (a.name + '_rgi.json')
if not rgi.exists():
    sql = ("import json\nfrom inventory.views import _get_conn,_put_conn\nc=_get_conn();cur=c.cursor()\n"
           "cur.execute(\"select id,name,ST_AsGeoJSON(ST_Transform(geom,3857),1) from glaciers where geom && ST_MakeEnvelope(%f,%f,%f,%f,4326)\")\n"
           "print(json.dumps([[x,y,json.loads(g)] for x,y,g in cur.fetchall()]))\nc.rollback();_put_conn(c)\n") % tuple(box)
    o = subprocess.run(['docker', 'exec', 'landslidescience-web-1', 'python', 'manage.py', 'shell', '-c', sql], capture_output=True, text=True, check=True)
    rgi.write_text(o.stdout.strip().splitlines()[-1])
run(PY, '-I', HERE / 'match.py', d / 'stack.npz', ref, a.orbit, d / 'match.json')
key = 'lum_' + a.orbit
run(PY, '-I', HERE / 'overlay.py', d / 'stack.npz', ref, d / 'match.json', key, d / 'overlay.jpg')
run(PY, '-I', HERE / 'rgi_check.py', d / 'stack.npz', d / 'match.json', key, rgi, d / 'rgi_check.jpg')
