#!/opt/anaconda3/bin/python3
"""Every quantity at every landslide centroid -> inventory/static/inventory/ls_quantities.json

    sample_quantities.py               # sample (needs data/landslide_centroids_3338.json)
    sample_quantities.py --centroids   # refresh that file from the dev database first

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
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform

sys.path.insert(0, str(Path(__file__).resolve().parent))
from quantities import QUANTITIES, ROOT      # noqa: E402

CENTROIDS = ROOT / 'data' / 'landslide_centroids_3338.json'
OUT = ROOT / 'inventory' / 'static' / 'inventory' / 'ls_quantities.json'
CENTROID_SQL = ("SELECT landslide_id, ST_X(ST_Transform(ST_Centroid(ST_Collect(geom)), 3338)), "
                "ST_Y(ST_Transform(ST_Centroid(ST_Collect(geom)), 3338)) FROM landslide_polygons GROUP BY landslide_id")


def refresh_centroids():
    """Ask the dev web container (which has the database) for the centroids."""
    code = ("import json,sys; from inventory import views; c = views._get_conn(); cur = c.cursor(); "
            f"cur.execute({CENTROID_SQL!r}); print(json.dumps([list(r) for r in cur.fetchall()])); views._put_conn(c)")
    r = subprocess.run(['docker', 'exec', 'landslidescience-web-1', 'python', 'manage.py', 'shell', '-c', code],
                       capture_output=True, text=True, check=True)
    rows = json.loads(r.stdout.strip().splitlines()[-1])
    CENTROIDS.write_text(json.dumps(rows))
    print(f'  {len(rows)} centroids -> {CENTROIDS}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--centroids', action='store_true', help='refresh the centroid file from the dev database first')
    a = ap.parse_args()
    if a.centroids:
        refresh_centroids()
    pts = json.loads(CENTROIDS.read_text())
    ids = [int(p[0]) for p in pts]
    x = [float(p[1]) for p in pts]; y = [float(p[2]) for p in pts]
    keys = list(QUANTITIES)
    cols = {}
    for k in keys:
        q = QUANTITIES[k]
        if not q['src'].exists():
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
        v[bad] = np.nan
        cols[k] = [None if np.isnan(t) else (int(t) if q.get('integer') or 'classes' in q else round(float(t), 4)) for t in v]
        print(f'  {k:8s} {int((~bad).sum()):5d} of {len(ids)} with a value')
    if 'aspect' in cols and 'slope' in cols:
        # A flat cell has no aspect (the cube masks it the same way; gdaldem
        # writes 0, which would read as north).
        cols['aspect'] = [None if s == 0 else a for a, s in zip(cols['aspect'], cols['slope'])]
    out = {'keys': keys, 'values': {str(i): [cols[k][j] for k in keys] for j, i in enumerate(ids)}}
    OUT.write_text(json.dumps(out, separators=(',', ':')))
    print(f'wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)')


if __name__ == '__main__':
    main()
