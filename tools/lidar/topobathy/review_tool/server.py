#!/opt/anaconda3/bin/python3
"""Water review tool: browse a run's flags and polygons over the dev surface, decide them, write the review files.

  server.py --run /Volumes/Nunatak/lidar_build/composite/joint_v3/out_run20j [--survey joint_v3j] [--port 8767]

WATER_REVIEW_PLAN.md W3, as a local page (mask-tool pattern: Flask on the Mac, never the droplet). It reads
  <run>/water_flags.geojson      the run's flags (kind, numbers, a suggested disposition, a stable id)
  <run>/polygons_auto.geojson    the run's automatic water polygons (body, kind, level, agree)
  tools/lidar/water/review.geojson    dispositions already given (merged onto the flags by id)
  tools/lidar/water/polygons.geojson  reviewed polygons (only status 'reviewed' is read by the chain)
  tools/lidar/water/mapped_hig_lakes.geojson  the mapped prior, for context
and the dev viewer's pyramids (data/lidar_dev/pmtiles/<survey>*.pmtiles, served here with Range so the page
needs no Django). It writes the two review files the chain reads (water.py / shore.py / composite.py):
  review.geojson    every flag the reviewer decided, with `disposition` (island | noise | polygon_edit | defer),
                    `note`, `reviewed_at`; flags no longer raised by the current run are kept (`stale: true`)
  polygons.geojson  reviewed polygons: kind (lake | river | sea | island), optional level_m, name, status 'reviewed'
Both are written whole, atomically (tmp + rename), so a crash never leaves a half file. Nothing is deleted:
clearing a disposition removes the flag from review.geojson; withdrawing a polygon sets status 'withdrawn'.
"""
import argparse, json, os, sys, time
from pathlib import Path
from flask import Flask, jsonify, request, send_file, send_from_directory, abort

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
WATER = REPO / 'tools' / 'lidar' / 'water'
DEV_PMTILES = Path(os.environ.get('LIDAR_DEV_PMTILES', str(REPO.parent / 'landslidescience' / 'data' / 'lidar_dev' / 'pmtiles')))
DEV_CATALOG = DEV_PMTILES.parent / 'catalog.geojson'

app = Flask(__name__, static_folder=None)
A = None


def _read(path):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {'type': 'FeatureCollection', 'features': []}


def _write(path, fc):
    path = Path(path)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(fc, separators=(',', ':')))
    os.replace(tmp, path)


def _survey():
    cat = _read(DEV_CATALOG)
    for f in cat.get('features', []):
        p = f['properties']
        if p.get('id') == A.survey:
            return dict(id=p['id'], title=p.get('title'), bounds=p.get('bounds'), min_zoom=p.get('min_zoom', 8),
                        max_zoom=p.get('max_zoom', 16), tide_levels=p.get('tide_levels'),
                        surfaces=[s['kind'] for s in p.get('surfaces', [])])
    return dict(id=A.survey, title=A.survey, bounds=None, min_zoom=8, max_zoom=16, tide_levels=None, surfaces=[])


@app.route('/')
def index():
    return send_file(HERE / 'index.html')


@app.route('/api/state')
def state():
    run = Path(A.run)
    flags = _read(run / 'water_flags.geojson')
    auto = _read(run / 'polygons_auto.geojson')
    review = _read(WATER / 'review.geojson')
    reviewed = _read(WATER / 'polygons.geojson')
    mapped = _read(WATER / 'mapped_hig_lakes.geojson') if A.mapped else {'type': 'FeatureCollection', 'features': []}
    by_id = {f['properties'].get('id'): f for f in review['features']}
    seen = set()
    for f in flags['features']:
        p = f['properties']
        seen.add(p.get('id'))
        r = by_id.get(p.get('id'))
        if r:
            rp = r['properties']
            p['disposition'] = rp.get('disposition', '')
            p['note'] = rp.get('note', '')
            p['reviewed_at'] = rp.get('reviewed_at')
        else:
            p.setdefault('disposition', '')
            p.setdefault('note', '')
    # decisions on flags the current run no longer raises: kept, shown apart
    stale = []
    for fid, r in by_id.items():
        if fid not in seen:
            r = json.loads(json.dumps(r))
            r['properties']['stale'] = True
            stale.append(r)
    pmtiles = {k: f'/pmtiles/{A.survey}{s}.pmtiles' for k, s in (('dem', ''), ('slope', '_slope'), ('water', '_wsurf'), ('sea', '_sea'))
               if (DEV_PMTILES / f'{A.survey}{s}.pmtiles').is_file()}
    return jsonify(dict(run=str(run), survey=_survey(), pmtiles=pmtiles, flags=flags, stale=stale, auto=auto,
                        reviewed=reviewed, mapped=mapped,
                        files=dict(review=str(WATER / 'review.geojson'), polygons=str(WATER / 'polygons.geojson'))))


@app.route('/api/review', methods=['POST'])
def save_review():
    """Body: {features: [flag features with disposition/note]} -- the whole decided set from the page.
    A flag with an empty disposition is dropped from the file (the suggestion stands)."""
    body = request.get_json(force=True)
    now = time.strftime('%Y-%m-%dT%H:%M:%S')
    out = []
    for f in body.get('features', []):
        p = f.get('properties', {})
        if not p.get('disposition'):
            continue
        p = dict(p)
        p['reviewed_at'] = p.get('reviewed_at') or now
        p['run'] = p.get('run') or Path(A.run).name
        out.append(dict(type='Feature', geometry=f['geometry'], properties=p))
    fc = dict(type='FeatureCollection', name='review', features=out)
    _write(WATER / 'review.geojson', fc)
    return jsonify(dict(ok=True, n=len(out), path=str(WATER / 'review.geojson'), at=now))


@app.route('/api/polygons', methods=['POST'])
def save_polygons():
    """Body: {features: [...]} -- the whole reviewed set (status reviewed | withdrawn)."""
    body = request.get_json(force=True)
    now = time.strftime('%Y-%m-%dT%H:%M:%S')
    out = []
    for f in body.get('features', []):
        p = dict(f.get('properties', {}))
        p.setdefault('status', 'reviewed')
        p['updated_at'] = now
        if p.get('kind') not in ('lake', 'river', 'sea', 'island'):
            abort(400, f"kind must be lake | river | sea | island, got {p.get('kind')!r}")
        out.append(dict(type='Feature', geometry=f['geometry'], properties=p))
    fc = dict(type='FeatureCollection', name='polygons', features=out)
    _write(WATER / 'polygons.geojson', fc)
    return jsonify(dict(ok=True, n=len(out), path=str(WATER / 'polygons.geojson'), at=now))


@app.route('/pmtiles/<name>')
def pmtiles(name):
    if '/' in name or not name.endswith('.pmtiles'):
        abort(404)
    # send_file(conditional=True) answers Range requests, which is all pmtiles.js needs
    return send_file(DEV_PMTILES / name, mimetype='application/vnd.pmtiles', conditional=True)


def main():
    global A
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', required=True, help='a run archive: out_run20j/ (water_flags.geojson, polygons_auto.geojson)')
    ap.add_argument('--survey', default='joint_v3j', help='dev survey id whose pyramids to draw under the flags')
    ap.add_argument('--port', type=int, default=8767)
    ap.add_argument('--no-mapped', dest='mapped', action='store_false', help='skip the 3 MB mapped-lakes layer')
    A = ap.parse_args()
    if not (Path(A.run) / 'water_flags.geojson').is_file():
        sys.exit(f'{A.run}/water_flags.geojson not found')
    print(f'run {A.run}\nsurvey {A.survey} from {DEV_PMTILES}\nwrites {WATER}/review.geojson and polygons.geojson')
    print(f'open http://127.0.0.1:{A.port}/')
    app.run(host='127.0.0.1', port=A.port, debug=False, threaded=True)


if __name__ == '__main__':
    main()
