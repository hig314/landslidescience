"""Write the soundings as a points CSV for the topobathy composites.

    python manage.py export_soundings --out data/soundings/export/soundings_20261012.csv
    python manage.py export_soundings --out ... --epsg 6334 --survey riordan_2018_kbsp_lakes
    python manage.py export_soundings --out ... --contour-step 20

Columns: x, y (in --epsg), z (NAVD88 m = level - depth), sigma (m: the
depth's and the level's in quadrature), depth_m, survey, kind (sounding |
contour), id, level_key.  This is the `points` file a composite recipe names
(tools/lidar/topobathy/fill.py reads x, y, z and sigma); a recipe should name a
DATED export, so a build can be repeated after the database moves on.

Rejected rows are left out; suspect ones are kept with sigma doubled. Rows
whose level is unknown have no elevation and are left out (counted). Contour
lines are sampled every --contour-step metres along the line, each sample
carrying the contour's sigma -- a contour is an interpretation between
soundings nobody located, so its sigma is the survey's (set it generously).
Every survey is exported whatever its `public` flag: this is a local build
input, not a publication.
"""
import csv
from pathlib import Path

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Export soundings (and sampled contours) as x,y,z,sigma CSV for the composites'

    def add_arguments(self, parser):
        parser.add_argument('--out', required=True)
        parser.add_argument('--epsg', type=int, default=6334)
        parser.add_argument('--survey', action='append', default=[], help='slug; repeatable; default all')
        parser.add_argument('--contour-step', type=float, default=20.0)

    def handle(self, *args, **o):
        from inventory.views import _get_conn, _put_conn
        conn = _get_conn()
        rows = []
        try:
            cur = conn.cursor()
            sv_sql = ' AND v.slug = ANY(%s)' if o['survey'] else ''
            sv_par = [o['survey']] if o['survey'] else []
            cur.execute("""
                SELECT ST_X(ST_Transform(s.geom, %s)), ST_Y(ST_Transform(s.geom, %s)), s.z,
                       s.sigma_z * CASE WHEN s.flag = 'suspect' THEN 2 ELSE 1 END, s.depth_m, v.slug, s.id, s.level_key
                  FROM soundings_z s JOIN sounding_surveys v ON v.id = s.survey_id
                 WHERE s.flag <> 'rejected' AND s.z IS NOT NULL""" + sv_sql + ' ORDER BY s.id',
                        [o['epsg'], o['epsg']] + sv_par)
            for x, y, z, sg, d, slug, i, lk in cur.fetchall():
                rows.append([x, y, z, sg, d, slug, 'sounding', i, lk])
            cur.execute("""
                SELECT count(*) FROM soundings_z s JOIN sounding_surveys v ON v.id = s.survey_id
                 WHERE s.flag <> 'rejected' AND s.z IS NULL""" + sv_sql, sv_par)
            no_level = cur.fetchone()[0]
            cur.execute("""
                WITH c AS (
                  SELECT c.id, v.slug, l.key AS lk, l.level_m - c.depth_m AS z, c.depth_m,
                         sqrt(power(COALESCE(c.sigma_m, v.sigma_m), 2) + power(COALESCE(l.sigma_m, 0), 2))
                           * CASE WHEN c.flag = 'suspect' THEN 2 ELSE 1 END AS sg,
                         ST_Segmentize(ST_Transform(c.geom, %s), %s) AS g
                    FROM sounding_contours c JOIN sounding_surveys v ON v.id = c.survey_id
                    JOIN sounding_levels l ON l.id = c.level_id
                   WHERE c.flag <> 'rejected' AND l.level_m IS NOT NULL""" + sv_sql + """)
                SELECT ST_X(p.geom), ST_Y(p.geom), c.z, c.sg, c.depth_m, c.slug, c.id, c.lk
                  FROM c, LATERAL ST_DumpPoints(c.g) p ORDER BY c.id, p.path
            """, [o['epsg'], o['contour_step']] + sv_par)
            nc = 0
            for x, y, z, sg, d, slug, i, lk in cur.fetchall():
                rows.append([x, y, z, sg, d, slug, 'contour', i, lk]); nc += 1
            conn.rollback()
        finally:
            _put_conn(conn)
        out = Path(o['out']); out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, 'w', newline='') as fh:
            w = csv.writer(fh)
            w.writerow(['x', 'y', 'z', 'sigma', 'depth_m', 'survey', 'kind', 'id', 'level_key'])
            for r in rows:
                w.writerow(['%.2f' % r[0], '%.2f' % r[1], '%.2f' % r[2], '%.2f' % r[3], '%.2f' % r[4]] + r[5:])
        self.stdout.write(self.style.SUCCESS(
            '%s: %d soundings + %d contour samples (EPSG:%d); %d soundings without a level left out'
            % (out, len(rows) - nc, nc, o['epsg'], no_level)))
