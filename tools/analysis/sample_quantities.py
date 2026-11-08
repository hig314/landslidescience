#!/opt/anaconda3/bin/python3
"""Every quantity at every landslide centroid -> inventory/static/inventory/ls_quantities.json

    sample_quantities.py               # sample (needs data/landslide_centroids_3338.json)
    sample_quantities.py --centroids   # refresh that file from the PRODUCTION database first
    sample_quantities.py --centroids --from dev   # ...or from the dev database
    sample_quantities.py nasa coh      # only these keys; every other column is
                                       # carried over from the existing file
                                       # (null for a centroid it did not have)

Name keys when a source is not mounted (the permafrost grids are on R2
since 2026-10-31, see quantities.py): a quantity whose raster is missing
would otherwise be written as all null and the committed values lost. A
centroid the existing file did not have then gets only the named keys;
the file records which keys it lacks (`unsampled: {id: [key, ...]}`), so
the panel can count it as unsampled on those axes rather than treat a
null as "no data there". A full run clears that record.

Supersedes tools/sample_susc.py --values and export_web.py --values (one
file, one script, every axis). The output is
    {"keys": [...], "values": {"<landslide_id>": [v | null, ...]}}
with values in `keys` order, read once by map.js: lw / n10 / dggs are
merged into the point features for the filter sliders, and the analysis
panel draws every landslide at its own values on whichever pair of axes
is up. Re-run (and commit) when landslides are added or moved.

The values are read from the same native rasters the cube was built from
(quantities.py), at the polygon-union centroid in EPSG:3338, so a
landslide and the terrain density behind it are in the same distribution.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform

sys.path.insert(0, str(Path(__file__).resolve().parent))
from quantities import GRID, QUANTITIES, ROOT, alaska_mask      # noqa: E402

CENTROIDS = ROOT / 'data' / 'landslide_centroids_3338.json'
OUT = ROOT / 'inventory' / 'static' / 'inventory' / 'ls_quantities.json'
CENTROID_SQL = ("SELECT landslide_id, ST_X(ST_Transform(ST_Centroid(ST_Collect(geom)), 3338)), "
                "ST_Y(ST_Transform(ST_Centroid(ST_Collect(geom)), 3338)) FROM landslide_polygons GROUP BY landslide_id")


PROD_HOST = 'root@143.198.140.54'


def refresh_centroids(where):
    """Fetch the centroids from production (the default: the file is served by
    prod, keyed by prod's ids, and records added there are the ones a dev
    refresh never sees) or from the dev web container."""
    if where == 'prod':
        sql = CENTROID_SQL.replace('SELECT landslide_id, ', 'SELECT json_agg(json_build_array(landslide_id, x, y)) FROM (SELECT landslide_id, ', 1)
        sql = sql.replace(', 3338)), ', ', 3338)) x, ', 1).replace(', 3338)) FROM', ', 3338)) y FROM', 1) + ') s'
        remote = ('PW=$(docker exec landslidescience-web printenv TETHYS_DB_PASSWORD); '
                  'docker exec -e PGPASSWORD="$PW" monitoring-tethys_db-1 '
                  f'psql -h 127.0.0.1 -U tethys -d landslides -At -c "{sql}"')
        r = subprocess.run(['ssh', PROD_HOST, remote], capture_output=True, text=True, check=True)
        rows = json.loads(r.stdout.strip())
        CENTROIDS.write_text(json.dumps(rows))
        print(f'  {len(rows)} centroids (prod) -> {CENTROIDS}')
        return
    code = ("import json,sys; from inventory import views; c = views._get_conn(); cur = c.cursor(); "
            f"cur.execute({CENTROID_SQL!r}); print(json.dumps([list(r) for r in cur.fetchall()])); views._put_conn(c)")
    r = subprocess.run(['docker', 'exec', 'landslidescience-web-1', 'python', 'manage.py', 'shell', '-c', code],
                       capture_output=True, text=True, check=True)
    rows = json.loads(r.stdout.strip().splitlines()[-1])
    CENTROIDS.write_text(json.dumps(rows))
    print(f'  {len(rows)} centroids (dev) -> {CENTROIDS}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--centroids', action='store_true', help='refresh the centroid file from the database first')
    ap.add_argument('--from', dest='where', choices=('prod', 'dev'), default='prod')
    ap.add_argument('keys', nargs='*', help='sample only these quantities; carry the rest over from the existing file')
    a = ap.parse_args()
    bad_keys = [k for k in a.keys if k not in QUANTITIES]
    if bad_keys:
        sys.exit(f'unknown quantities: {bad_keys}; known: {list(QUANTITIES)}')
    if a.centroids:
        refresh_centroids(a.where)
    pts = json.loads(CENTROIDS.read_text())
    ids = [int(p[0]) for p in pts]
    x = [float(p[1]) for p in pts]; y = [float(p[2]) for p in pts]
    keys = list(QUANTITIES)
    carry = {}
    if a.keys:
        # Carry every other column over from the file as it stands, by id.
        old = json.loads(OUT.read_text())
        pos = {k: i for i, k in enumerate(old['keys'])}
        for k in keys:
            if k in a.keys:
                continue
            if k not in pos:
                print(f'  {k:8s} not in the existing file: null for every record'); continue
            carry[k] = {int(i): row[pos[k]] for i, row in old['values'].items()}
        had = {int(i) for i in old['values']}
        missing = [i for i in ids if i not in had]
        if missing:
            print(f'  {len(missing)} centroids not in the existing file (built {old.get("built")}): '
                  f'only {a.keys} sampled for them')
        # Which keys each record still lacks: the carried keys for a new
        # centroid, and whatever an earlier partial run left, minus this run's.
        unsampled = {int(i): [k for k in ks if k not in a.keys] for i, ks in old.get('unsampled', {}).items()}
        for i in missing:
            unsampled[i] = [k for k in keys if k not in a.keys]
        unsampled = {i: ks for i, ks in unsampled.items() if ks and i in set(ids)}
    else:
        unsampled = {}
    # The lattice cell of each centroid, for the Alaska-only products: the
    # same cell-centre test the cube uses, so a landslide and the terrain
    # behind it are cut at the same line.
    a, _, c, _, e, f = GRID['transform']
    ak = alaska_mask()
    in_ak = np.array([0 <= int((yy - f) // e) < GRID['height'] and 0 <= int((xx - c) // a) < GRID['width']
                      and bool(ak[int((yy - f) // e), int((xx - c) // a)]) for xx, yy in zip(x, y)])
    cols = {}
    for k in keys:
        q = QUANTITIES[k]
        if k in carry:
            cols[k] = [carry[k].get(i) for i in ids]
            print(f'  {k:8s} carried over ({sum(v is not None for v in cols[k])} with a value)'); continue
        if q.get('points'):
            # Evaluated at the centroids by the tool that computed the column.
            pv = json.loads(q['points'].read_text())['values'][k]
            cols[k] = [pv.get(str(i)) for i in ids]
            print(f'  {k:8s} {sum(v is not None for v in cols[k]):5d} of {len(ids)} with a value (from {q["points"].name})')
            continue
        if not q['src'].exists():
            if a.keys:
                sys.exit(f'  {k:8s} MISSING {q["src"]}')
            print(f'  {k:8s} MISSING {q["src"]}'); cols[k] = [None] * len(ids); continue
        with rasterio.open(q['src']) as src:
            if src.crs and src.crs.to_epsg() == 3338:
                sx, sy = x, y
            else:
                sx, sy = transform('EPSG:3338', src.crs, x, y)
            v = np.array([s[0] for s in src.sample(zip(sx, sy))], dtype=np.float64)
            bad = ~np.isfinite(v)
            if src.nodata is not None:
                bad |= v == src.nodata
        for nd in q.get('nodata', []):
            bad |= v == nd
        if 'classes' in q:
            bad |= ~np.isin(v, q['classes'])
        if q.get('alaska_only'):
            bad |= ~in_ak
        v[bad] = np.nan
        cols[k] = [None if np.isnan(t) else (int(t) if q.get('integer') or 'classes' in q else round(float(t), 4)) for t in v]
        print(f'  {k:8s} {int((~bad).sum()):5d} of {len(ids)} with a value')
    if 'aspect' in cols and 'slope' in cols and 'aspect' not in carry:
        # A flat cell has no aspect (the cube masks it the same way; gdaldem
        # writes 0, which would read as north).
        cols['aspect'] = [None if s == 0 else a for a, s in zip(cols['aspect'], cols['slope'])]
    out = {'keys': keys, 'built': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
           'values': {str(i): [cols[k][j] for k in keys] for j, i in enumerate(ids)}}
    if unsampled:
        out['unsampled'] = {str(i): ks for i, ks in sorted(unsampled.items())}
        print(f'  {len(unsampled)} records carry an unsampled-keys list')
    OUT.write_text(json.dumps(out, separators=(',', ':')))
    print(f'wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)')


if __name__ == '__main__':
    main()
