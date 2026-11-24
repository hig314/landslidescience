"""Glacier identity and mirrored glacier-extent sources.

Design note: GLACIERS_PLAN.md. The short version:

* `glaciers` is the identity table, one row per glacier, seeded from RGI 7.0
  region 01 and keyed by `id`, which today is the RGI 7.0 id. NOT the GLIMS
  id, which was the first plan: RGI 7.0 gives one GLIMS id to as many as
  eight separate glaciers (135 ids are shared; a GLIMS entity that RGI split
  keeps its parent's id). The GLIMS id is kept as an attribute and is still
  the first thing a GLIMS row is matched on. Everything else hangs off `id`.
* `glacier_obs` holds what other people published: dated outlines and terminus
  lines, one row per feature, with the publisher's attributes untouched in
  `props`. A handful of columns beside `props` are OUR reading of the row
  (which glacier it belongs to, what kind of thing it is, its date bracket)
  so the map and the glacier card can treat every source alike. Those are
  derived, re-derived on every fetch, and never written back into `props`.
* A refetch REPLACES a source inside one transaction, for the reason the
  landslide mirrors do (inventory/external.py): publishers revise and
  renumber, and an upsert would keep rows the current release dropped.
* Fetches are count-checked. A mirror that is quietly two-thirds complete is
  worse than none.

The sources are shown as layers that can be switched on and off. A separate
consensus layer that editors improve (Hig, 2026-11-16) is the next step and
is deliberately NOT in this table: these rows are other people's statements.

PostGIS in tethys_db, raw psycopg2, like the landslide tables. Schema:
`manage.py migrate_glaciers`, once per environment. Load:
`manage.py fetch_glacier_source <id>`.
"""
import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path

from django.conf import settings

_UA = 'landslidescience.org glacier-extent mirror'
_TIMEOUT = 300
GLIMS_WFS = 'https://www.glims.org/geoserver/ows'
SOURCES_DIR = Path(settings.BASE_DIR) / 'data' / 'glaciers' / 'sources'

# Alaska and its Canadian margin: the box RGI region 01 sits in.
AK_BBOX = (-170.0, 54.0, -128.0, 70.0)

# The identity source. Not a row of SOURCES because it fills `glaciers`, not
# `glacier_obs`, but it is cited the same way.
CENTERLINES = {
    'title': 'RGI 7.0 centerlines product, region 01',
    'short': 'RGI 7.0 centerlines',
    'cite': 'RGI 7.0 Consortium (2023). Centerlines computed with OGGM (Maussion and others '
            '2019) by the method of Kienholz and others (2014).',
    'doi': '10.5067/F6JMOVY5NAVZ',
    'url': 'https://rgidata.org/user_guide/products/centerlines_product.html',
}

RGI7 = {
    'title': 'Randolph Glacier Inventory 7.0, region 01 (Alaska)',
    'short': 'RGI 7.0',
    'cite': 'RGI 7.0 Consortium (2023). Randolph Glacier Inventory – A Dataset of '
            'Global Glacier Outlines, Version 7.0. NSIDC.',
    'doi': '10.5067/F6JMOVY5NAVZ',
    'url': 'https://www.glims.org/RGI/',
    'layer': 'GLIMS:RGI2000-v7.0-G-01_alaska_epsg3857',
}

# id -> description. `kind` is what a row is unless `kind_of` says otherwise.
# `dates(props)` returns (date_min, date_max) as ISO strings: a BRACKET, since
# several of these sources know the year and nothing finer.
S1AUTO = 's1auto'

SOURCES = {
    'glims': {
        'title': 'GLIMS Glacier Database',
        'short': 'GLIMS',
        'what': 'Outlines from many analysts, one per glacier per analysis. '
                'Divides differ between submissions, so areas are comparable '
                'only within one submission.',
        'cite': 'GLIMS Consortium (2005). GLIMS Glacier Database, Version 1. NSIDC. '
                'Raup and others (2007), Global and Planetary Change 56.',
        'doi': '10.7265/N5V98602',
        'url': 'https://www.glims.org/',
        'geom': 'polygon',
        'fetch': {'type': 'glims_wfs', 'layer': 'GLIMS:GLIMS_Glacier_Outlines',
                  'bbox': AK_BBOX},
        'link': 'id_then_overlap',
        'color': '#1f77b4',
    },
    'nsidc0634': {
        'title': 'Alaska tidewater glacier terminus positions (McNabb & Hock)',
        'short': 'McNabb & Hock',
        'what': 'Terminus traces for 50 tidewater glaciers, 1948–2012, from USGS '
                'maps and Landsat scenes.',
        'cite': 'McNabb, R. W. and Hock, R. (2014). Alaska Tidewater Glacier Terminus '
                'Positions, Version 1. NSIDC.',
        'doi': '10.5067/3OOVUDFK3R0W',
        'url': 'https://nsidc.org/data/nsidc-0634/versions/1',
        'geom': 'line',
        'fetch': {'type': 'file', 'file': 'nsidc0634.geojsonl'},
        'link': 'group_nearest', 'group': 'glacier',
        'color': '#d62728',
    },
    'usgs_glacier_bay': {
        'title': 'Glacier Bay historical terminus positions (USGS)',
        'short': 'USGS Glacier Bay',
        'what': 'Terminus positions in Glacier Bay from about 1700 to 2019, compiled '
                'from historical mapping, published reconstructions and lidar.',
        'cite': 'Avdievitch, N. N., Bliss, A. K. and Coe, J. A. (2023). Historical '
                'terminus positions of glaciers in Glacier Bay National Park and '
                'Preserve. U.S. Geological Survey data release.',
        'doi': '10.5066/P9WDA76U',
        'url': 'https://www.sciencebase.gov/catalog/item/64889018d34ef77fcafe39af',
        'geom': 'line',
        'fetch': {'type': 'file', 'file': 'usgs_glacier_bay.geojsonl'},
        'link': 'nearest',
        'color': '#9467bd',
    },
    'usgs_benchmark': {
        'title': 'USGS benchmark glacier boundaries',
        'short': 'USGS benchmark',
        'what': 'Surveyed margins of the USGS benchmark glaciers in Alaska '
                '(Gulkana, Wolverine, Lemon Creek), 1940s to present.',
        'cite': 'McNeil, C. J. and others. Geodetic data for USGS benchmark glaciers: '
                'orthophotos, digital elevation models, glacier boundaries and surveyed '
                'positions. U.S. Geological Survey data release.',
        'doi': '10.5066/P9R8BP3K',
        'url': 'https://www.sciencebase.gov/catalog/item/5d7adbcde4b0c4f70d02a4d6',
        'geom': 'polygon',
        'fetch': {'type': 'file', 'file': 'usgs_benchmark.geojsonl'},
        'link': 'overlap',
        'color': '#2ca02c',
    },
    # Ours, not mirrored: ice faces found in the Sentinel-1 timelapses we
    # build (tools/glaciers/s1_build/faces.py), loaded per build by
    # `manage.py s1_videos faces`, never by fetch_glacier_source. Only the
    # months whose own image shows the face clearly (Hig, 2026-11-23).
    S1AUTO: {
        'title': 'Ice faces detected in our Sentinel-1 timelapses',
        'short': 'S1 auto faces',
        'what': 'Calving faces found automatically in the monthly Sentinel-1 radar '
                'timelapses built here, kept only for the months whose image shows the '
                'face clearly (a bright-to-water step along most of it, wall to wall). '
                'Not hand-checked; an admin can reject a bad one.',
        'cite': 'Detected at landslidescience.org from Copernicus Sentinel-1 RTC '
                '(Microsoft Planetary Computer, sentinel-1-rtc).',
        'doi': '',
        'url': 'https://planetarycomputer.microsoft.com/dataset/sentinel-1-rtc',
        'geom': 'line',
        'fetch': None,
        'link': 'given',
        'color': '#00acc1',
    },
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS glaciers (
    id          text PRIMARY KEY,       -- the RGI 7.0 id, for now
    rgi7_id     text NOT NULL,
    glims_id    text NOT NULL,          -- not unique: see the module docstring
    name        text NOT NULL DEFAULT '',
    area_km2    double precision,
    src_date    date,
    term_type   integer,
    surge_type  integer,
    o2region    text,
    cenlon      double precision,
    cenlat      double precision,
    zmin_m      double precision,
    zmax_m      double precision,
    lmax_m      double precision,
    -- every RGI attribute as published
    props       jsonb NOT NULL,
    geom        geometry(MultiPolygon, 4326) NOT NULL,
    -- Tile geometry, already in Web Mercator so a tile request does no
    -- reprojection: geom_s simplified to about 30 m on the ground (z9-11),
    -- geom_c to about 250 m (z8 and below). A statewide z4 tile took 3.2 s
    -- transforming the full outlines on the fly.
    geom_s      geometry(MultiPolygon, 3857),
    geom_c      geometry(MultiPolygon, 3857)
);
CREATE INDEX IF NOT EXISTS glaciers_geom_idx ON glaciers USING GIST (geom);
CREATE INDEX IF NOT EXISTS glaciers_geom_s_idx ON glaciers USING GIST (geom_s);
CREATE INDEX IF NOT EXISTS glaciers_geom_c_idx ON glaciers USING GIST (geom_c);
CREATE INDEX IF NOT EXISTS glaciers_glims_idx ON glaciers (glims_id);
CREATE INDEX IF NOT EXISTS glaciers_name_idx ON glaciers (lower(name)) WHERE name <> '';

CREATE TABLE IF NOT EXISTS glacier_obs (
    id          bigserial PRIMARY KEY,
    source      text NOT NULL,
    -- the publisher's own identifier where they give one, else a stable
    -- hash of the row; row ids here do not survive a refetch, this does
    ext_id      text NOT NULL,
    props       jsonb NOT NULL,
    geom        geometry(Geometry, 4326) NOT NULL,
    geom_s      geometry(Geometry, 3857),   -- tile geometry, as on `glaciers`
    geom_c      geometry(Geometry, 3857),
    -- ours, derived at fetch:
    kind        text NOT NULL,          -- outline | debris | terminus
    glacier_id  text,                   -- glaciers.id; NULL = not matched
    link_how    text,                   -- id | overlap | nearest
    date_min    date,
    date_max    date,
    label       text NOT NULL DEFAULT '',
    area_km2    double precision
);
CREATE INDEX IF NOT EXISTS glacier_obs_geom_idx ON glacier_obs USING GIST (geom);
CREATE INDEX IF NOT EXISTS glacier_obs_geom_s_idx ON glacier_obs USING GIST (geom_s);
CREATE INDEX IF NOT EXISTS glacier_obs_geom_c_idx ON glacier_obs USING GIST (geom_c);
CREATE INDEX IF NOT EXISTS glacier_obs_source_idx ON glacier_obs (source);
CREATE INDEX IF NOT EXISTS glacier_obs_glacier_idx ON glacier_obs (glacier_id);

-- RGI 7.0 centerlines, as published: the reference flow lines. Each runs
-- down-flow (head to terminus); a tributary's outflow_id is the segment_id,
-- within the same glacier, of the line it joins. They stop at the glacier's
-- own outline. The network we edit is glacier_lines (glaciers/network.py),
-- which ADOPTS rows from here.
CREATE TABLE IF NOT EXISTS glacier_centerlines (
    id          serial PRIMARY KEY,
    glacier_id  text NOT NULL,
    segment_id  integer NOT NULL,
    is_main     boolean NOT NULL,
    outflow_id  integer,
    strahler    integer,
    length_m    double precision,
    geom        geometry(LineString, 4326) NOT NULL,
    geom_s      geometry(LineString, 3857)
);
CREATE INDEX IF NOT EXISTS glacier_centerlines_geom_s_idx ON glacier_centerlines USING GIST (geom_s);
CREATE INDEX IF NOT EXISTS glacier_centerlines_glacier_idx ON glacier_centerlines (glacier_id);

CREATE TABLE IF NOT EXISTS glacier_sources (
    source      text PRIMARY KEY,
    fetched_at  timestamptz NOT NULL DEFAULT now(),
    n           integer NOT NULL,
    n_linked    integer NOT NULL DEFAULT 0,
    year_min    integer,
    year_max    integer
);

-- An admin's "this face is wrong" (2026-11-23). Keyed on (source, ext_id),
-- which survives a refetch or a re-run of the detector; `rejected` on the
-- row is the copy every query filters on, re-applied after each load.
ALTER TABLE glacier_obs ADD COLUMN IF NOT EXISTS rejected boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS glacier_face_rejects (
    source      text NOT NULL,
    ext_id      text NOT NULL,
    rejected_by text NOT NULL,
    rejected_at timestamptz NOT NULL DEFAULT now(),
    note        text NOT NULL DEFAULT '',
    PRIMARY KEY (source, ext_id)
);
"""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _get_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode('utf-8'))


def _wfs_url(layer, **kw):
    q = {'service': 'WFS', 'version': '2.0.0', 'request': 'GetFeature',
         'typeNames': layer, 'outputFormat': 'application/json',
         'srsName': 'EPSG:4326'}
    q.update(kw)
    return GLIMS_WFS + '?' + urllib.parse.urlencode(q)


def _date(s):
    """'2005-09-01T00:00:00Z' -> '2005-09-01'; anything unusable -> None."""
    s = (s or '').strip()
    return s[:10] if len(s) >= 10 and s[4] == '-' and s[7] == '-' else None


def _year_bracket(y):
    try:
        y = int(y)
    except (TypeError, ValueError):
        return None, None
    return '%04d-01-01' % y, '%04d-12-31' % y


def _doy_date(year, doy):
    """Year + day of year -> a date. NSIDC-0634 writes day 000 for a USGS map
    known only to the year; that comes back as the whole-year bracket."""
    import datetime
    try:
        y, d = int(year), int(doy)
    except (TypeError, ValueError):
        return None, None
    if d < 1 or d > 366:
        return _year_bracket(y)
    iso = (datetime.date(y, 1, 1) + datetime.timedelta(days=d - 1)).isoformat()
    return iso, iso


# ---------------------------------------------------------------------------
# Per-source reading of a publisher row: (ext_id, kind, date_min, date_max, label)
# ---------------------------------------------------------------------------
def _geom_hash(geom):
    return hashlib.sha1(json.dumps(geom, separators=(',', ':')).encode()).hexdigest()[:12]


def _read_glims(p, geom):
    lt = p.get('line_type') or ''
    kind = {'glac_bound': 'outline', 'debris_cov': 'debris'}.get(lt)
    if kind is None:
        return None                      # rock outcrops, snow lines: not extent
    d = _date(p.get('src_date'))
    # GLIMS gives no row id over WFS (the feature id is a session artefact),
    # and one analysis can hold several polygons, so the geometry is part
    # of the identity.
    ext = '%s:%s:%s' % (p.get('anlys_id'), lt, _geom_hash(geom))
    label = 'submission %s · %s' % (p.get('subm_id'), p.get('chief_affl') or '')
    return ext, kind, d, d, label


def _read_0634(p, geom):
    d0, d1 = _doy_date(p.get('year'), p.get('doy'))
    return ('%s:%s' % (p.get('glacier'), p.get('id')), 'terminus', d0, d1,
            p.get('lsat_scene') or '')


def _read_glacier_bay(p, geom):
    d0, d1 = _year_bracket(p.get('Year'))
    return ('%s:%s' % (p.get('Year'), _geom_hash(geom)), 'terminus', d0, d1,
            p.get('Prim_Sourc') or '')


def _read_benchmark(p, geom):
    d = _date((p.get('Date') or '').replace('/', '-'))
    d0, d1 = (d, d) if d else _year_bracket(p.get('Year'))
    return ('%s:%s' % (p.get('Glacier'), p.get('Date') or p.get('Year')), 'outline',
            d0, d1, p.get('Glacier') or '')


_READERS = {'glims': _read_glims, 'nsidc0634': _read_0634,
            'usgs_glacier_bay': _read_glacier_bay, 'usgs_benchmark': _read_benchmark}


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
def iter_file(name):
    with open(SOURCES_DIR / name, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip().lstrip('\x1e')
            if line:
                yield json.loads(line)


def iter_glims(layer, bbox, log=lambda s: None, cell=(6.0, 4.0)):
    """Every feature of the layer in the box, each once, plus the server's
    own total for the box as the first item.

    The layer has no primary key over WFS, so startIndex paging has no
    stable order to page through. Instead the box is cut into cells, each
    cell asked for whole, and features that straddle a cell edge are
    recognised by content and yielded once. The caller checks the number
    yielded against the total.
    """
    w, s, e, n = bbox
    total = _get_json(_wfs_url(layer, count=1,
                               bbox='%s,%s,%s,%s,EPSG:4326' % (w, s, e, n))).get('numberMatched')
    yield ('total', total)
    seen = set()
    x = w
    while x < e:
        y = s
        while y < n:
            cb = '%s,%s,%s,%s,EPSG:4326' % (x, y, min(e, x + cell[0]), min(n, y + cell[1]))
            d = _get_json(_wfs_url(layer, bbox=cb))
            feats = d.get('features') or []
            if d.get('numberMatched') not in (None, 'unknown') and \
                    int(d['numberMatched']) != len(feats):
                raise RuntimeError('cell %s: server matched %s, returned %d'
                                   % (cb, d['numberMatched'], len(feats)))
            new = 0
            for f in feats:
                p = f.get('properties') or {}
                key = (p.get('anlys_id'), p.get('line_type'), _geom_hash(f.get('geometry')))
                if key in seen:
                    continue
                seen.add(key)
                new += 1
                yield f
            log('  cell %s: %d features, %d new' % (cb, len(feats), new))
            y += cell[1]
        x += cell[0]


def iter_rgi(log=lambda s: None, page=2000):
    """RGI 7.0 region 01, paged on rgi_id (unique, so the order is stable)."""
    start, total = 0, None
    while True:
        d = _get_json(_wfs_url(RGI7['layer'], count=page, startIndex=start, sortBy='rgi_id'))
        if total is None:
            total = int(d['numberMatched'])
            yield ('total', total)
        feats = d.get('features') or []
        for f in feats:
            yield f
        start += len(feats)
        log('  %d / %d' % (start, total))
        if not feats or start >= total:
            return


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
_POLY = "ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326))),3))"
_LINE = "ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326))),2))"
# Simplification tolerances in Web Mercator metres. At 60 N a Mercator metre
# is half a ground metre, so these are about 30 m and 250 m on the ground.
_TOL_S, _TOL_C = 60, 500


def _simp_poly(tol):
    return ('ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SimplifyPreserveTopology('
            'ST_Transform(geom, 3857), %d)), 3))' % tol)


def set_tile_geom(cur, table, where='TRUE', params=(), poly=True):
    """Fill geom_s / geom_c from geom."""
    if poly:
        cur.execute('UPDATE %s SET geom_s = %s, geom_c = %s WHERE %s'
                    % (table, _simp_poly(_TOL_S), _simp_poly(_TOL_C), where), params)
    else:
        cur.execute('UPDATE %s SET geom_s = ST_Transform(geom, 3857), '
                    'geom_c = ST_Simplify(ST_Transform(geom, 3857), %d) WHERE %s'
                    % (table, _TOL_C // 2, where), params)


def load_rgi(cur, log=lambda s: None, dry=False):
    """Replace `glaciers` with RGI 7.0 region 01. Returns the row count."""
    it = iter_rgi(log)
    total = next(it)[1]
    rows, ids = [], set()
    for f in it:
        p = f['properties']
        gid = p.get('rgi_id')
        if not gid or gid in ids or not p.get('glims_id'):
            raise RuntimeError('RGI id missing or repeated: %r' % (gid,))
        ids.add(gid)
        name = (p.get('glac_name') or '').strip()
        if name.lower() in ('none', 'null'):
            name = ''
        rows.append((gid, gid, p.get('glims_id'), name, p.get('area_km2'), _date(p.get('src_date')),
                     p.get('term_type'), p.get('surge_type'), p.get('o2region'),
                     p.get('cenlon'), p.get('cenlat'), p.get('zmin_m'), p.get('zmax_m'),
                     p.get('lmax_m'), json.dumps(p), json.dumps(f['geometry'])))
    if len(rows) != total:
        raise RuntimeError('RGI: fetched %d, server says %d' % (len(rows), total))
    if dry:
        return len(rows)
    cur.execute('DELETE FROM glaciers')
    sql = ('INSERT INTO glaciers (id, rgi7_id, glims_id, name, area_km2, src_date, term_type, '
           'surge_type, o2region, cenlon, cenlat, zmin_m, zmax_m, lmax_m, props, geom) '
           'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,' + _POLY + ')')
    for i in range(0, len(rows), 500):
        cur.executemany(sql, rows[i:i + 500])
    set_tile_geom(cur, 'glaciers')
    cur.execute('ANALYZE glaciers')
    return len(rows)


def load_centerlines(cur, log=lambda s: None, dry=False):
    """Replace `glacier_centerlines` from the prepared RGI 7.0 L file."""
    rows = []
    for f in iter_file('rgi7_centerlines.geojsonl'):
        p, g = f['properties'], f['geometry']
        if not g or g['type'] != 'LineString':
            continue
        rows.append((p['rgi_g_id'], int(p['segment_id']), bool(p['is_main']),
                     None if p.get('outflow_id') in (None, -1) else int(p['outflow_id']),
                     p.get('strahler_n'), p.get('length_m'), json.dumps(g)))
    if dry:
        return len(rows)
    cur.execute('DELETE FROM glacier_centerlines')
    sql = ('INSERT INTO glacier_centerlines (glacier_id, segment_id, is_main, outflow_id, strahler, '
           'length_m, geom) VALUES (%s,%s,%s,%s,%s,%s, ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)))')
    for i in range(0, len(rows), 1000):
        cur.executemany(sql, rows[i:i + 1000])
    cur.execute('UPDATE glacier_centerlines SET geom_s = ST_Transform(geom, 3857)')
    cur.execute('ANALYZE glacier_centerlines')
    return len(rows)


def load_source(cur, sid, log=lambda s: None, dry=False):
    """Replace one source's rows in `glacier_obs`. Returns a report dict."""
    S = SOURCES[sid]
    reader = _READERS[sid]
    f = S['fetch']
    total = None
    if f['type'] == 'glims_wfs':
        it = iter_glims(f['layer'], f['bbox'], log)
        total = next(it)[1]
    else:
        it = iter_file(f['file'])
    poly = S['geom'] == 'polygon'
    rows, skipped, seen_ext = [], 0, set()
    n_in = 0
    for feat in it:
        n_in += 1
        p, g = feat.get('properties') or {}, feat.get('geometry')
        if not g:
            skipped += 1
            continue
        r = reader(p, g)
        if r is None:
            skipped += 1
            continue
        ext, kind, d0, d1, label = r
        if ext in seen_ext:               # same row twice in the publisher's file
            ext = ext + ':' + _geom_hash(g)
        seen_ext.add(ext)
        rows.append((sid, ext, json.dumps(p), kind, d0, d1, label[:300], json.dumps(g)))
    if total is not None and int(total) != n_in:
        raise RuntimeError('%s: fetched %d, server says %s' % (sid, n_in, total))
    rep = {'source': sid, 'read': n_in, 'kept': len(rows), 'skipped': skipped}
    if dry:
        return rep
    cur.execute('DELETE FROM glacier_obs WHERE source = %s', (sid,))
    sql = ('INSERT INTO glacier_obs (source, ext_id, props, kind, date_min, date_max, label, geom) '
           'VALUES (%s,%s,%s,%s,%s,%s,%s,' + (_POLY if poly else _LINE) + ')')
    for i in range(0, len(rows), 500):
        cur.executemany(sql, rows[i:i + 500])
    # rows whose geometry did not survive validation (a polygon that was a line)
    cur.execute('DELETE FROM glacier_obs WHERE source = %s AND ST_IsEmpty(geom)', (sid,))
    rep['empty_geom'] = cur.rowcount
    if poly:
        cur.execute('UPDATE glacier_obs SET area_km2 = ST_Area(geom::geography) / 1e6 '
                    'WHERE source = %s', (sid,))
    set_tile_geom(cur, 'glacier_obs', 'source = %s', (sid,), poly=poly)
    link(cur, sid)
    apply_rejects(cur, sid)
    rep.update(source_stats(cur, sid))
    cur.execute('ANALYZE glacier_obs')
    return rep


def source_stats(cur, sid):
    """Recount one source into glacier_sources (rejected rows not counted)."""
    cur.execute('SELECT count(*), count(glacier_id), min(extract(year from date_min))::int, '
                'max(extract(year from date_max))::int FROM glacier_obs WHERE source = %s AND NOT rejected', (sid,))
    n, nl, y0, y1 = cur.fetchone()
    cur.execute('INSERT INTO glacier_sources (source, fetched_at, n, n_linked, year_min, year_max) '
                'VALUES (%s, now(), %s, %s, %s, %s) ON CONFLICT (source) DO UPDATE SET '
                'fetched_at = now(), n = EXCLUDED.n, n_linked = EXCLUDED.n_linked, '
                'year_min = EXCLUDED.year_min, year_max = EXCLUDED.year_max', (sid, n, nl, y0, y1))
    return {'n': n, 'linked': nl, 'year_min': y0, 'year_max': y1}


def apply_rejects(cur, sid):
    """Mark a (re)loaded source's rows that an admin has rejected."""
    cur.execute('UPDATE glacier_obs o SET rejected = true FROM glacier_face_rejects r '
                'WHERE o.source = %s AND r.source = o.source AND r.ext_id = o.ext_id', (sid,))
    return cur.rowcount


def set_rejected(cur, obs_id, user, on=True, note=''):
    """Reject (or restore) one face row. Returns (source, ext_id, glacier_id)
    or None if there is no such terminus row."""
    cur.execute("SELECT source, ext_id, glacier_id FROM glacier_obs WHERE id = %s AND kind = 'terminus'",
                (obs_id,))
    r = cur.fetchone()
    if not r:
        return None
    if on:
        cur.execute('INSERT INTO glacier_face_rejects (source, ext_id, rejected_by, note) VALUES (%s,%s,%s,%s) '
                    'ON CONFLICT (source, ext_id) DO UPDATE SET rejected_by = EXCLUDED.rejected_by, '
                    'rejected_at = now(), note = EXCLUDED.note', (r[0], r[1], user, note[:300]))
    else:
        cur.execute('DELETE FROM glacier_face_rejects WHERE source = %s AND ext_id = %s', (r[0], r[1]))
    cur.execute('UPDATE glacier_obs SET rejected = %s WHERE source = %s AND ext_id = %s', (on, r[0], r[1]))
    source_stats(cur, r[0])
    return r


def _merc(lon, lat):
    import math
    R = 6378137.0
    return math.radians(lon) * R, R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def _unmerc(x, y):
    import math
    R = 6378137.0
    return math.degrees(x / R), math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)


def refit_video_faces(cur, video_id):
    """Put a video's S1 auto faces where the video is NOW (Hig, 2026-11-24:
    a face belongs to the video it was found in, so a corrected placement,
    or a new video, carries its faces). Each face keeps its vertices as
    fractions of the frame (props.frame_uv, u across from the west edge, v
    down from the north edge, both in Web Mercator like the frame); this
    redraws them inside the video's current bounds. Returns the count."""
    cur.execute('SELECT georef FROM glacier_videos WHERE id = %s', (video_id,))
    r = cur.fetchone()
    if not r or not r[0] or not r[0].get('bounds'):
        return 0
    w, s_, e, n = r[0]['bounds']
    (x0, y0), (x1, y1) = _merc(w, s_), _merc(e, n)
    cur.execute("SELECT id, props->'frame_uv' FROM glacier_obs WHERE source = %s AND props->>'video_id' = %s "
                "AND props ? 'frame_uv'", (S1AUTO, str(video_id)))
    rows = cur.fetchall()
    for oid, uv in rows:
        g = {'type': 'LineString',
             'coordinates': [[round(c, 7) for c in _unmerc(x0 + u * (x1 - x0), y1 - v * (y1 - y0))] for u, v in uv]}
        cur.execute('UPDATE glacier_obs SET geom = ' + _LINE + ' WHERE id = %s', (json.dumps(g), oid))
    if rows:
        set_tile_geom(cur, 'glacier_obs', "source = %s AND props->>'video_id' = %s", (S1AUTO, str(video_id)), poly=False)
    return len(rows)


def load_s1_faces(cur, build, fc, glacier_id, video_id=None):
    """Replace one build's S1 auto faces (faces.py's faces.geojson) in
    glacier_obs. ext_id = '<build>:<month>', so a rejection made against an
    earlier run still holds for the same month. A face is dated to its
    month (the frame is a monthly mean). Returns a report dict."""
    import calendar
    cur.execute('DELETE FROM glacier_obs WHERE source = %s AND ext_id LIKE %s', (S1AUTO, build + ':%'))
    old = cur.rowcount
    sql = ('INSERT INTO glacier_obs (source, ext_id, props, kind, glacier_id, link_how, date_min, date_max, '
           'label, geom) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,' + _LINE + ')')
    rows = []
    for f in fc['features']:
        p = f['properties']
        y, m = int(p['month'][:4]), int(p['month'][5:7])
        props = dict(p, build=build, video_id=video_id, min_score=fc.get('min_score'))
        rows.append((S1AUTO, '%s:%s' % (build, p['month']), json.dumps(props), 'terminus', glacier_id,
                     'given' if glacier_id else None, '%04d-%02d-01' % (y, m),
                     '%04d-%02d-%02d' % (y, m, calendar.monthrange(y, m)[1]),
                     'S1 auto, score %.2f (%s)' % (p['score'], build), json.dumps(f['geometry'])))
    cur.executemany(sql, rows)
    set_tile_geom(cur, 'glacier_obs', 'source = %s AND ext_id LIKE %s', (S1AUTO, build + ':%'), poly=False)
    nrej = apply_rejects(cur, S1AUTO)
    # drawn where the video is now, not where the build put the frame
    refit = refit_video_faces(cur, video_id) if video_id else 0
    rep = {'build': build, 'replaced': old, 'loaded': len(rows), 'rejected_kept': nrej, 'refit': refit}
    rep.update(source_stats(cur, S1AUTO))
    return rep


# ---------------------------------------------------------------------------
# Linking a source row to a glacier. Re-runnable on its own (after `glaciers`
# is refetched): `fetch_glacier_source --relink`.
# ---------------------------------------------------------------------------
_NEAR_DEG = 0.03                 # candidate search radius (about 3 km N-S)
_NEAR_M = 2000                   # a line further than this from any glacier stays unlinked

_OVERLAP_SQL = """
UPDATE glacier_obs o SET glacier_id = s.gid, link_how = 'overlap'
FROM (SELECT DISTINCT ON (o2.id) o2.id, g.id AS gid
      FROM glacier_obs o2 JOIN glaciers g ON ST_Intersects(o2.geom_s, g.geom_s)
      WHERE o2.source = %s AND o2.glacier_id IS NULL
      ORDER BY o2.id, ST_Area(ST_Intersection(o2.geom_s, g.geom_s)) DESC) s
WHERE o.id = s.id
"""

# The glacier a line belongs to: the one it runs across furthest, else the
# nearest within _NEAR_M.
_NEAREST_SUB = """
(SELECT g.id FROM glaciers g
  WHERE ST_DWithin(g.geom, {line}, %(deg)s)
    AND ST_Distance(g.geom::geography, {line}::geography) <= %(m)s
  ORDER BY ST_Length(ST_Intersection(g.geom, {line})) DESC,
           ST_Distance(g.geom::geography, {line}::geography) ASC
  LIMIT 1)
"""


def link(cur, sid):
    how = SOURCES[sid]['link']
    if how == 'given':            # set by whatever loaded the rows
        return
    cur.execute("UPDATE glacier_obs SET glacier_id = NULL, link_how = NULL WHERE source = %s", (sid,))
    par = {'sid': sid, 'deg': _NEAR_DEG, 'm': _NEAR_M}
    if how == 'id_then_overlap':
        # By GLIMS id where that id names exactly one of our glaciers; a
        # shared id says nothing about which part, so those go by overlap.
        cur.execute("UPDATE glacier_obs o SET glacier_id = g.id, link_how = 'id' "
                    "FROM glaciers g WHERE o.source = %s AND g.glims_id = o.props->>'glac_id' "
                    "AND (SELECT count(*) FROM glaciers g2 WHERE g2.glims_id = g.glims_id) = 1", (sid,))
        cur.execute(_OVERLAP_SQL, (sid,))
    elif how == 'overlap':
        cur.execute(_OVERLAP_SQL, (sid,))
    elif how == 'nearest':
        cur.execute("UPDATE glacier_obs o SET glacier_id = " + _NEAREST_SUB.format(line='o.geom')
                    + ", link_how = 'nearest' WHERE o.source = %(sid)s", par)
        cur.execute("UPDATE glacier_obs SET link_how = NULL WHERE source = %s AND glacier_id IS NULL", (sid,))
    elif how == 'group_nearest':
        # One glacier per publisher group (a file): decided by the group's
        # most recent line, which is the one closest in time to the RGI
        # outline, then applied to every line of the group.
        # A candidate whose RGI name is the group's name wins outright:
        # where two tongues share a front (Gilman and Johns Hopkins), the
        # line alone picked the wrong one.
        grp = SOURCES[sid]['group']
        par['grp'] = grp
        # MATERIALIZED: without it the planner re-ran the nearest-glacier
        # search for every line instead of once per group (10,334 searches
        # against polygons of tens of thousands of vertices: over ten
        # minutes and counting, against a second or two).
        cur.execute("""
            WITH l AS MATERIALIZED (
                SELECT DISTINCT ON (props->>%(grp)s) props->>%(grp)s AS grp, geom
                FROM glacier_obs WHERE source = %(sid)s
                ORDER BY props->>%(grp)s, date_max DESC NULLS LAST),
            s AS MATERIALIZED (
                SELECT l.grp, """ + _NEAREST_SUB.format(line='l.geom').replace(
                    'ORDER BY', "ORDER BY (lower(g.name) = replace(l.grp, '_', ' ') || ' glacier') DESC,") + """ AS gid FROM l)
            UPDATE glacier_obs o SET glacier_id = s.gid, link_how = 'nearest'
            FROM s
            WHERE o.source = %(sid)s AND o.props->>%(grp)s = s.grp AND s.gid IS NOT NULL
        """, par)
