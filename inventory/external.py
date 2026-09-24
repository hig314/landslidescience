"""external.py -- third-party landslide inventories, mirrored verbatim.

WHY A TABLE PER INVENTORY RATHER THAN ROWS IN `landslides`
----------------------------------------------------------
These are other people's datasets, not ours. The Alaska DGGS inventory
classifies by material crossed with movement category; the Canadian database
by type, material and size class; we classify by slow-versus-catastrophic plus
a creep behaviour and a resolvable age. There is no honest way to push one
into the other without deciding, per record, things the source did not say.

So each inventory gets its OWN PostGIS table whose columns are the columns the
publisher shipped, and nothing is translated on the way in. The map overlay
then shows exactly what they published, which is the only thing we can claim
to know about their records. Turning one into a row of ours is a separate,
deliberate act with per-field rules (see `derive`, still to be built), and it
goes through the normal import path so it lands in the review queue rather
than straight into the public inventory.

The practical payoff is that adding a third inventory is a registry entry plus
a fetch, not a schema negotiation.

WHY THE COLUMNS ARE DISCOVERED, NOT DECLARED
--------------------------------------------
An ArcGIS feature service publishes its own field list and types, and a CSV
publishes its header. Writing that out by hand once per source would be the
part that rots: the publisher adds a column in v15 and our hand-written DDL
silently drops it. `ensure_table` builds the table from whatever the source
declares at fetch time and adds columns it has not seen before, so a new
release widens the mirror instead of losing data.

CSV COLUMNS ARE ALL TEXT, ON PURPOSE
------------------------------------
A service declares its types, so we use them. A CSV declares nothing, and
sniffing is where faithfulness goes to die: a volume column holding ">1000" or
"1e5" or "" becomes either a parse error or a silent null. Text keeps exactly
what the publisher wrote. Parsing is an interpretation, and interpretation
belongs in derivation, where a per-field rule can say what ">1000" means.
"""
import csv
import io
import json
import re
import urllib.parse
import urllib.request

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .views import _get_conn, _put_conn

_UA = 'landslidescience.org external-inventory mirror'
_HTTP_TIMEOUT = 120


# ---------------------------------------------------------------------------
# The registry. One entry per inventory. Everything a mirror needs is here:
# who published it, under what terms, how to fetch it, and which of their
# columns is the record id and the human label. Adding an inventory is an
# entry; it is not code.
#
# `attribution` is not decoration. Both current sources REQUIRE it -- the DGGS
# licence asks that the source be named and any modifications described, and
# CC-BY-4.0 asks for credit -- so it is carried into the catalogue, the popup
# and anything derived from a record.
# ---------------------------------------------------------------------------
SOURCES = {
    'ak_dggs_dds23': {
        'title': 'Alaska Landslide Inventory (DGGS)',
        'short': 'Alaska DGGS',
        'citation': ('Nicolazzo, J.A., and Larsen, M.C., 2025, Alaska landslide '
                     'inventory database: Alaska Division of Geological & '
                     'Geophysical Surveys Digital Data Series 23, 14 p.'),
        'url': 'https://doi.org/10.14509/31697',
        'licence': ('Alaska DGGS. Source must be indicated and any modifications '
                    'described. Mirrored here unmodified.'),
        'colour': '#f07818',
        'fetch': {
            'kind': 'arcgis',
            'service': ('https://services1.arcgis.com/7HDiw78fcUiM2BWn/arcgis/rest/'
                        'services/Alaska_Landslide_Inventory_Web_Layer/FeatureServer'),
            # Their sublayer ids. The points carry the attributes; the rest are
            # the mapped geometry of the same landslides. Points first because
            # that is what the overlay draws and what derivation reads.
            'layers': {
                'points':   {'id': 0, 'geom': 'POINT',      'label': 'Landslide points'},
                'deposits': {'id': 4, 'geom': 'MULTIPOLYGON', 'label': 'Deposits'},
                'flanks':   {'id': 3, 'geom': 'MULTIPOLYGON', 'label': 'Flanks'},
                'scarps':   {'id': 1, 'geom': 'MULTILINESTRING', 'label': 'Scarps'},
                'toes':     {'id': 2, 'geom': 'MULTILINESTRING', 'label': 'Toes'},
            },
        },
        # Their own identifier, kept so a refetch updates rather than duplicates
        # and so a derived record can point back at the row it came from.
        'id_field': 'points_id',
        'name_field': 'name',
        'primary_layer': 'points',
    },
    'canada_pcld': {
        'title': 'Preliminary Canadian Landslide Database',
        'short': 'Canada PCLD',
        'citation': ('Brideau, M.-A., and others, 2026, Preliminary Canadian '
                     'Landslide Database, version 14.0.'),
        'url': 'https://doi.org/10.5281/zenodo.20371365',
        'licence': 'CC BY 4.0. Credit required.',
        'colour': '#9b59b6',
        'fetch': {
            'kind': 'csv',
            'layers': {
                'points': {
                    'geom': 'POINT',
                    'label': 'Landslide points',
                    'url': ('https://zenodo.org/records/20371365/files/'
                            'Canadian_landslide_database_May2026_version14.csv'
                            '?download=1'),
                    # Their coordinates are plain columns, not a geometry.
                    'lon_field': 'Longitude',
                    'lat_field': 'Latitude',
                },
            },
        },
        'id_field': 'LS_ID',
        'name_field': 'Name',
        'primary_layer': 'points',
    },
}


def source(sid):
    return SOURCES.get(sid)


# ---------------------------------------------------------------------------
# Table naming and schema
# ---------------------------------------------------------------------------
_SAFE = re.compile(r'^[a-z0-9_]+$')


def table_name(sid, layer):
    """`ext_<source>__<layer>`. The double underscore keeps the two halves
    separable by eye and by split(), since both halves may contain one."""
    name = f'ext_{sid}__{layer}'
    if not _SAFE.match(name):
        raise ValueError(f'unsafe table name: {name!r}')
    return name


def _col(name):
    """Fold a publisher's column name into something Postgres will take
    unquoted, without inventing collisions. Their name is kept verbatim in the
    column comment so the popup can label a field the way they label it."""
    c = re.sub(r'[^a-z0-9_]', '_', str(name).strip().lower().lstrip('﻿'))
    c = re.sub(r'_+', '_', c).strip('_')
    if not c:
        c = 'col'
    if c[0].isdigit():
        c = 'c_' + c
    # Reserved-ish names we would rather not fight with.
    if c in ('id', 'geom', 'order', 'references', 'user', 'default'):
        c = c + '_'
    return c[:60]


# ArcGIS declares its types, so we honour them. Anything unrecognised becomes
# text: a mirror that keeps the characters is better than one that refuses the
# row because a type was unexpected.
_ESRI_PG = {
    'esriFieldTypeOID': 'bigint',
    'esriFieldTypeInteger': 'bigint',
    'esriFieldTypeSmallInteger': 'integer',
    'esriFieldTypeBigInteger': 'bigint',
    'esriFieldTypeDouble': 'double precision',
    'esriFieldTypeSingle': 'real',
    'esriFieldTypeDate': 'timestamptz',
}


def _pg_type(esri_type):
    return _ESRI_PG.get(esri_type, 'text')


def ensure_table(cur, sid, layer, columns, geom_type):
    """Create the mirror table if absent, and widen it if the publisher has
    added columns since the last fetch. Never drops or retypes: a column that
    disappears from a release is kept, because the rows that used it are still
    real and the alternative is destroying data on someone else's edit.
    """
    t = table_name(sid, layer)
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {t} (
            id          bigserial PRIMARY KEY,
            ext_id      text,
            geom        geometry({geom_type}, 4326),
            fetched_at  timestamptz NOT NULL DEFAULT now()
        )
    """)
    cur.execute(f'CREATE INDEX IF NOT EXISTS {t}_geom_idx ON {t} USING GIST (geom)')
    cur.execute(f'CREATE INDEX IF NOT EXISTS {t}_ext_id_idx ON {t} (ext_id)')
    cur.execute("""
        SELECT column_name FROM information_schema.columns WHERE table_name = %s
    """, (t,))
    have = {r[0] for r in cur.fetchall()}
    added = []
    for col, pgtype, original in columns:
        if col not in have:
            cur.execute(f'ALTER TABLE {t} ADD COLUMN IF NOT EXISTS {col} {pgtype}')
            added.append(col)
        # Comment on EVERY fetch, not only when the column is first seen. A
        # publisher can rename a field's display alias between releases, and a
        # label written once at creation would keep showing the old phrase for
        # ever while the data underneath moved on.
        cur.execute(f'COMMENT ON COLUMN {t}.{col} IS %s', (original,))
    return t, added


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
def _get(url, timeout=_HTTP_TIMEOUT):
    req = urllib.request.Request(url, headers={'User-Agent': _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def arcgis_fields(service, layer_id):
    """Field list as the service declares it, plus the display aliases. The
    alias is what a human sees in their own app, so it is what our popup
    should say too."""
    meta = json.loads(_get(f'{service}/{layer_id}?f=json'))
    out, aliases, domains = [], {}, {}
    for f in meta.get('fields', []):
        name = f.get('name')
        if not name:
            continue
        col = _col(name)
        out.append((col, _pg_type(f.get('type')), name))
        aliases[col] = f.get('alias') or name
        dom = f.get('domain') or {}
        if dom.get('codedValues'):
            domains[col] = {str(c.get('code')): c.get('name')
                            for c in dom['codedValues']}
    return out, aliases, domains, meta


def arcgis_features(service, layer_id, page=1000):
    """Every feature, paged. Services cap a single response (2000 here), and a
    fetch that silently stops at the cap is the classic way to mirror a third
    of someone's data and not notice, so this pages until the service stops
    setting exceededTransferLimit and cross-checks the total against a
    returnCountOnly query."""
    base = f'{service}/{layer_id}/query'
    total = json.loads(_get(base + '?where=1%3D1&returnCountOnly=true&f=json'))
    expected = total.get('count')
    offset, seen = 0, []
    while True:
        q = urllib.parse.urlencode({
            'where': '1=1', 'outFields': '*', 'returnGeometry': 'true',
            'outSR': 4326, 'f': 'geojson',
            'resultOffset': offset, 'resultRecordCount': page,
        })
        fc = json.loads(_get(f'{base}?{q}'))
        feats = fc.get('features') or []
        if not feats:
            break
        seen.extend(feats)
        offset += len(feats)
        if len(feats) < page and not fc.get('properties', {}).get('exceededTransferLimit'):
            break
        if expected and offset >= expected:
            break
    return seen, expected


def csv_rows(url):
    """Header plus rows, as text. A BOM on the first header cell is common in
    published CSVs and would otherwise make the first column unfindable."""
    raw = _get(url, timeout=300).decode('utf-8-sig', errors='replace')
    rdr = csv.reader(io.StringIO(raw))
    header = next(rdr)
    return [h.strip().lstrip('﻿') for h in header], list(rdr)


# ---------------------------------------------------------------------------
# Serving
# ---------------------------------------------------------------------------
def _populated(props):
    """Drop empty values before they reach the client. The popup's promise is
    'whatever fields they populated', and a panel of forty blank rows says
    nothing while hiding the handful that do."""
    return {k: v for k, v in props.items()
            if v is not None and v != '' and k not in ('geom',)}


@require_http_methods(['GET'])
def api_external_sources(request):
    """The registry, for the layer list. Public: these are published datasets
    and their terms require that we name them, so the credits travel with the
    layer rather than living only in a docstring."""
    out = []
    conn = _get_conn()
    try:
        cur = conn.cursor()
        for sid, s in SOURCES.items():
            layers = []
            for layer, L in s['fetch']['layers'].items():
                t = table_name(sid, layer)
                cur.execute("SELECT to_regclass(%s)", (t,))
                if not cur.fetchone()[0]:
                    continue
                cur.execute(f'SELECT count(*) FROM {t}')
                n = cur.fetchone()[0]
                if not n:
                    continue
                # The publisher's own label for each column, stored as a column
                # comment at fetch time. The popup shows their words, not our
                # folded column names: "movement_category" is what their app
                # calls it, and a reader comparing the two should see the same
                # phrase in both. Falls back to the column name when a source
                # ships no aliases.
                cur.execute("""
                    SELECT a.attname, col_description(a.attrelid, a.attnum)
                      FROM pg_attribute a
                     WHERE a.attrelid = %s::regclass
                       AND a.attnum > 0 AND NOT a.attisdropped
                     ORDER BY a.attnum
                """, (t,))
                fields, order = {}, []
                for col, comment in cur.fetchall():
                    if col in ('id', 'geom', 'ext_id', 'fetched_at'):
                        continue
                    fields[col] = comment or col
                    order.append(col)
                layers.append({'layer': layer, 'label': L['label'],
                               'geom': L['geom'], 'count': n,
                               'fields': fields, 'field_order': order})
            if layers:
                out.append({
                    'id': sid, 'title': s['title'], 'short': s['short'],
                    'citation': s['citation'], 'url': s['url'],
                    'licence': s['licence'], 'colour': s['colour'],
                    'primary_layer': s['primary_layer'], 'layers': layers,
                })
        conn.rollback()
    finally:
        _put_conn(conn)
    return JsonResponse({'sources': out})


@require_http_methods(['GET'])
def api_external_record(request, sid, layer, ext_id):
    """One mirrored record by THE PUBLISHER'S id, for the promoted-from card.

    Looked up by their identifier rather than our mirror row id because the
    mirror table is replaced on every refetch and our ids do not survive it --
    a card keyed on our id would go blank the first time someone refreshed the
    source, which is exactly when provenance matters most.
    """
    s = SOURCES.get(sid)
    if not s or layer not in s['fetch']['layers']:
        return JsonResponse({'error': 'unknown source or layer'}, status=404)
    t = table_name(sid, layer)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass(%s)", (t,))
        if not cur.fetchone()[0]:
            conn.rollback()
            return JsonResponse({'error': 'not mirrored here'}, status=404)
        cur.execute("""
            SELECT a.attname, col_description(a.attrelid, a.attnum)
              FROM pg_attribute a
             WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
             ORDER BY a.attnum
        """, (t,))
        cols, labels = [], {}
        for col, comment in cur.fetchall():
            if col in ('id', 'geom', 'fetched_at'):
                continue
            cols.append(col)
            labels[col] = comment or col
        cur.execute(f'SELECT {", ".join(cols)} FROM {t} WHERE ext_id = %s LIMIT 1',
                    (str(ext_id),))
        row = cur.fetchone()
        conn.rollback()
    finally:
        _put_conn(conn)
    if not row:
        return JsonResponse({'error': 'no such record'}, status=404)
    props = {}
    for c, v in zip(cols, row):
        if v is None or v == '':
            continue
        props[c] = v.isoformat() if hasattr(v, 'isoformat') else v
    return JsonResponse({
        'source': {'id': sid, 'title': s['title'], 'short': s['short'],
                   'citation': s['citation'], 'url': s['url'],
                   'licence': s['licence'], 'colour': s['colour']},
        'layer': layer, 'labels': labels, 'order': cols, 'properties': props,
    })


@require_http_methods(['GET'])
def api_external(request, sid, layer):
    """One layer's features in a bbox, as GeoJSON, with their fields verbatim.

    The bbox is REQUIRED here, unlike the scarps endpoint. That is not an
    inconsistency: there are a few hundred scarps and fifty thousand of these,
    and an unbounded request would ship tens of megabytes to draw a smear.
    """
    s = SOURCES.get(sid)
    if not s or layer not in s['fetch']['layers']:
        return JsonResponse({'error': 'unknown source or layer'}, status=404)
    bbox = request.GET.get('bbox', '')
    try:
        x0, y0, x1, y1 = [float(v) for v in bbox.split(',')]
    except ValueError:
        return JsonResponse(
            {'error': 'bbox=minLon,minLat,maxLon,maxLat is required'}, status=400)
    try:
        limit = min(int(request.GET.get('limit', 4000)), 10000)
    except ValueError:
        limit = 4000

    t = table_name(sid, layer)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass(%s)", (t,))
        if not cur.fetchone()[0]:
            conn.rollback()
            return JsonResponse({'type': 'FeatureCollection', 'features': [],
                                 'not_fetched': True})
        cur.execute("""
            SELECT column_name FROM information_schema.columns
             WHERE table_name = %s AND column_name NOT IN ('geom')
             ORDER BY ordinal_position
        """, (t,))
        cols = [r[0] for r in cur.fetchall()]
        sel = ', '.join(cols)
        cur.execute(f"""
            SELECT ST_AsGeoJSON(geom, 6), {sel}
              FROM {t}
             WHERE geom IS NOT NULL
               AND ST_Intersects(geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326))
             ORDER BY id
             LIMIT %s
        """, (x0, y0, x1, y1, limit + 1))
        rows = cur.fetchall()
        conn.rollback()
    finally:
        _put_conn(conn)

    truncated = len(rows) > limit
    feats = []
    for r in rows[:limit]:
        props = dict(zip(cols, r[1:]))
        for k, v in list(props.items()):
            if hasattr(v, 'isoformat'):
                props[k] = v.isoformat()
        feats.append({'type': 'Feature',
                      'geometry': json.loads(r[0]),
                      'properties': _populated(props)})
    return JsonResponse({'type': 'FeatureCollection', 'features': feats,
                         'truncated': truncated, 'source': sid, 'layer': layer})
