"""Inventory explorer — the spreadsheet-style page at /inventory/table/.

Self-contained on purpose (like photos.py / trace_views.py); views.py is
already the large landslide-data module.

WHY THE WHOLE TABLE SHIPS TO THE BROWSER
----------------------------------------
The public inventory is ~1,500 records x ~70 columns. As row-JSON that's
~2.8 MB, but columnar with dictionary-encoded text it is well under 1 MB —
small enough to send once and then answer every sort / filter / cross-tab
client-side in milliseconds. That is the whole point of the page: an Excel
autofilter that round-trips to the server on every click is not an Excel
autofilter. `/inventory/manage/` remains the server-paginated record list;
this is the analysis surface.

PAYLOAD SHAPE
-------------
    {
      "n": 1485,
      "version": "<data_version>",
      "editor": false,
      "presets": {"default": [...col names...], ...},
      "groups":  [{"key": "core", "title": "Core"}, ...],
      "columns": [
        {"name": "landslide_class", "label": "Class", "type": "cat",
         "group": "core", "dict": ["Slow Obvious creep", ...]},
        ...
      ],
      "data": {
        "landslide_class": [0, 1, null, ...],   # cat  -> index into dict
        "volume_preferred": [12000, null, ...], # num  -> raw
        "molards":          [1, 0, null, ...],  # bool -> 1 / 0 / null
        "date_min":         ["2010-09-14", ...],# date -> ISO string
        "subsets":          [[0, 2], [0], ...]  # multi -> index arrays
      }
    }

Column *types* are inferred from information_schema plus a cardinality probe,
so a column added to Postgres shows up in the explorer automatically — the
same principle as the edit form's `_discover_editable_columns`. Only the
handful of columns whose inferred type would be wrong (long free text that
happens to be low-cardinality, URL columns) are named explicitly below.

VISIBILITY
----------
Public surface: `public_landslide_filter` hides pending and deprecated
records, exactly like the map. Editors get the full set plus a `status`
column so pending/deprecated are filterable rather than invisible. The two
payloads are cached under separate keys and both are dropped by
`views._invalidate('table_public', 'table_editor')` on any data write.
"""
import gzip
import json

from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe

from . import views
from .auth import is_inventory_editor
from .forms import _FIELD_LABELS

# ---------------------------------------------------------------------------
# Column typing
# ---------------------------------------------------------------------------

# Text columns that must NOT be dictionary-encoded into a checkbox filter even
# if their cardinality is low today — they're prose or per-record identifiers,
# so a value picker would be useless and the dict would just bloat the payload.
_FORCE_TEXT = {
    'unique_name', 'description', 'notes', 'other_subtle_creep',
    'ongoing_work', 'seismic_note', 'seismic_credit', 'flag_reason',
    'default_map_view',
}

# URL columns: rendered as links, filtered as present/absent. Never worth
# showing the string itself in a cell.
_FORCE_LINK = {
    'planet_story_link', 'esri_wayback_link', 'google_images_link',
    'sentinel2_link', 'sentinel1_link',
}

# Above this many distinct values a text column is a free-text ("contains")
# filter rather than a checkbox list. 60 comfortably clears the real
# vocabularies (noted_by is the widest at ~77 including NULL, so it lands on
# the text side; volume_method at ~20 and year_text at ~32 stay pickable).
_CAT_MAX_DISTINCT = 60

# Units, appended to the label so a bare number is never ambiguous. The
# inventory stores areas in m^2 and volumes in m^3 throughout.
_UNITS = {
    'area_body': 'm²', 'area_source': 'm²', 'area_deposit': 'm²',
    'area_total': 'm²',
    'volume_body': 'm³', 'volume_source': 'm³', 'volume_deposit': 'm³',
    'volume_preferred': 'm³', 'volume_estimated': 'm³',
    'volume_site_specific': 'm³',
    'centroid_albers_x': 'm', 'centroid_albers_y': 'm',
    'centroid_lat': '°', 'centroid_lon': '°',
}

# Label overrides on top of forms._FIELD_LABELS (which covers the edit form's
# needs). Only columns whose auto-derived label is actually wrong or too long
# for a table header.
_EXTRA_LABELS = {
    'id': 'ID',
    'unique_name': 'Name',
    'landslide_type': 'Type',
    'landslide_class': 'Class',
    'creep_behavior': 'Creep behaviour',
    'volume_preferred': 'Volume (preferred)',
    'volume_site_specific': 'Volume (site-specific)',
    'volume_estimated': 'Volume (estimated)',
    'area_body': 'Area — body',
    'area_source': 'Area — source',
    'area_deposit': 'Area — deposit',
    'volume_body': 'Volume — body',
    'volume_source': 'Volume — source',
    'volume_deposit': 'Volume — deposit',
    'centroid_albers_x': 'Albers X',
    'centroid_albers_y': 'Albers Y',
    'centroid_lat': 'Latitude',
    'centroid_lon': 'Longitude',
    'catastrophic_failure_years': 'Catastrophic failure years',
    'planet_story_link': 'Planet story',
    'esri_wayback_link': 'Esri Wayback',
    'google_images_link': 'Google Images',
    'sentinel2_link': 'Sentinel-2',
    'sentinel1_link': 'Sentinel-1',
    'default_map_view': 'Default map view',
    # computed
    'year_num': 'Year (resolved)',
    'n_polygons': 'Polygons',
    'area_total': 'Area — total',
    'subsets': 'Subsets',
    'n_photos': 'Photos',
    'n_stories': 'Planet stories',
    'status': 'Status',
}

# ---------------------------------------------------------------------------
# Computed columns — not in `landslides`, cheap to derive, and the ones that
# make the difference between a column dump and something you can explore.
# Each entry: (name, type, group). SQL lives in _COMPUTED_SQL.
# ---------------------------------------------------------------------------
_COMPUTED = [
    ('year_num',   'num',   'event'),
    ('n_polygons', 'num',   'derived'),
    ('area_total', 'num',   'derived'),
    ('subsets',    'multi', 'derived'),
    ('n_photos',   'num',   'derived'),
    ('n_stories',  'num',   'derived'),
    ('status',     'cat',   'meta'),
]

# `year_num` mirrors _FILTER_PROPS_SQL in views.py, which in turn mirrors
# derived._resolve_event_era. Three copies is two too many, but they are
# already out of sync-able today; keeping this one textually identical to the
# map's is the cheapest way to guarantee the table and the map agree on a
# record's age. If that expression moves to a shared constant, this goes with
# it.
_YEAR_NUM_SQL = """
    CASE
        WHEN l.seismic_datetime IS NOT NULL THEN EXTRACT(YEAR FROM l.seismic_datetime)::int
        WHEN l.year_text ~ '^[0-9]{4}$' THEN l.year_text::int
        WHEN l.year_text ILIKE '%holocene%' THEN -1
        WHEN l.year_text ILIKE '%modern%'   THEN 0
        WHEN l.landslide_class LIKE '%Holocene%' THEN -1
        WHEN l.landslide_class LIKE '%Modern%'   THEN 0
        WHEN l.date_min IS NOT NULL THEN EXTRACT(YEAR FROM l.date_min)::int
        ELSE NULL
    END
"""

_COMPUTED_SQL = f"""
    {_YEAR_NUM_SQL} AS year_num,
    (SELECT count(*) FROM landslide_polygons p WHERE p.landslide_id = l.id)
        AS n_polygons,
    (SELECT sum(p.area) FROM landslide_polygons p WHERE p.landslide_id = l.id)
        AS area_total,
    COALESCE((SELECT array_agg(s.slug ORDER BY s.slug)
              FROM landslide_subsets ls JOIN subsets s ON s.id = ls.subset_id
              WHERE ls.landslide_id = l.id), '{{}}') AS subsets,
    (SELECT count(*) FROM landslide_photos ph WHERE ph.landslide_id = l.id)
        AS n_photos,
    (SELECT count(*) FROM landslide_planet_stories ps WHERE ps.landslide_id = l.id)
        AS n_stories,
    CASE WHEN l.deprecated_at IS NOT NULL THEN 'deprecated'
         WHEN l.reviewed_at IS NULL       THEN 'pending'
         ELSE 'active' END AS status
"""

# ---------------------------------------------------------------------------
# Column groups. The first six mirror the edit form's _EDIT_FIELD_GROUPS so
# the two surfaces bucket a column the same way; 'derived' and 'meta' hold
# what the edit form deliberately hides.
# ---------------------------------------------------------------------------
_GROUP_TITLES = [
    ('core',     'Core'),
    ('event',    'Event & timing'),
    ('creep',    'Creep evidence'),
    ('extent',   'Volume & site history'),
    ('imagery',  'Imagery & links'),
    ('review',   'Review flag'),
    ('computed', 'Computed'),
    ('derived',  'Derived here'),
    ('meta',     'Record metadata'),
]

_META_COLS = {'id', 'created_at', 'updated_at', 'reviewed_at',
              'deprecated_at', 'superseded_by', 'inventory_subset'}


def _flatten_group_fields(group):
    """_EDIT_FIELD_GROUPS entries nest `{'block': title, 'fields': [...]}`
    dicts inside their field list — flatten to plain column names."""
    out = []
    for f in group['fields']:
        if isinstance(f, dict):
            out.extend(f.get('fields', []))
        else:
            out.append(f)
    return out


def _group_of_column():
    """column name -> group key, from the edit form's own grouping."""
    m = {}
    for g in views._EDIT_FIELD_GROUPS:
        for name in _flatten_group_fields(g):
            m[name] = g['key']
    for name in _META_COLS:
        m[name] = 'meta'
    for name, _t, grp in _COMPUTED:
        m[name] = grp
    return m


def _label_for(name):
    if name in _EXTRA_LABELS:
        base = _EXTRA_LABELS[name]
    elif name in _FIELD_LABELS:
        base = _FIELD_LABELS[name]
    else:
        base = name.replace('_', ' ')
        base = base[:1].upper() + base[1:]
    unit = _UNITS.get(name)
    return f'{base} ({unit})' if unit else base


# ---------------------------------------------------------------------------
# Presets — named column sets. `default` is what the page opens with; the
# rest are one-click answers to "show me the columns for <question>".
# Names that don't exist in the DB are dropped silently at build time, so a
# preset survives a column rename without 500ing the page.
# ---------------------------------------------------------------------------
_PRESETS = {
    'default': ['unique_name', 'landslide_type', 'landslide_class', 'year_num',
                'area_total', 'volume_preferred', 'creep_behavior', 'country',
                'noted_by', 'subsets', 'n_polygons'],
    'events': ['unique_name', 'landslide_type', 'landslide_class', 'year_num',
               'year_text', 'date_min', 'date_max', 'seismic_datetime',
               'tsunamigenic', 'molards', 'super_elevated_deposits',
               'stream_damming', 'precursory_headscarp',
               'exclusively_supraglacial', 'volume_preferred'],
    'creep': ['unique_name', 'landslide_class', 'creep_evaluated',
              'creep_behavior', 'insar_creep', 'insar_schaefer', 'insar_kim',
              'insar_opera', 'insar_other', 'planet_labs_creep',
              'planet_labs_patchy_creep', 'geomorph_creep',
              'post_2012_activity_increase', 'creeping_permafrost_mass',
              'glacier_contact'],
    'volume': ['unique_name', 'landslide_type', 'landslide_class',
               'size_inclusion', 'area_source', 'area_body', 'area_deposit',
               'area_total', 'volume_source', 'volume_body', 'volume_deposit',
               'volume_estimated', 'volume_site_specific', 'volume_preferred',
               'volume_method'],
    'location': ['unique_name', 'landslide_type', 'country', 'subsets',
                 'centroid_lat', 'centroid_lon', 'centroid_albers_x',
                 'centroid_albers_y', 'n_polygons', 'area_total'],
}


# ---------------------------------------------------------------------------
# Payload build
# ---------------------------------------------------------------------------

def _num(v):
    """JSON-safe number, or None. NaN/Inf would make the payload invalid JSON
    (allow_nan=False), and a float8 column can hold either."""
    if v is None:
        return None
    f = float(v)
    if f != f or f in (float('inf'), float('-inf')):
        return None
    return int(f) if f.is_integer() else f


def _pg_type(udt):
    if udt == 'bool':
        return 'bool'
    if udt in ('int2', 'int4', 'int8', 'float4', 'float8', 'numeric'):
        return 'num'
    if udt == 'date':
        return 'date'
    if udt in ('timestamptz', 'timestamp'):
        return 'datetime'
    return 'text'


def _build_payload(editor):
    conn = views._get_conn()
    try:
        cur = conn.cursor()

        # Column list + Postgres types, in table order (matches the edit form's
        # discovery so a new column appears here for free).
        cur.execute("""
            SELECT column_name, udt_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'landslides'
            ORDER BY ordinal_position
        """)
        db_cols = [(r[0], _pg_type(r[1])) for r in cur.fetchall()]
        # The legacy column superseded by the landslide_subsets join — the
        # `subsets` computed column is the truth. Showing both invites the
        # wrong one being used in an analysis.
        db_cols = [c for c in db_cols if c[0] != 'inventory_subset']

        where = '' if editor else f'WHERE {views.public_landslide_filter("l")}'
        select_cols = ', '.join(f'l.{name}' for name, _t in db_cols)
        cur.execute(f"""
            SELECT {select_cols}, {_COMPUTED_SQL}
            FROM landslides l
            {where}
            ORDER BY l.id
        """)
        rows = cur.fetchall()
        conn.rollback()
    finally:
        views._put_conn(conn)

    all_cols = db_cols + [(n, t) for n, t, _g in _COMPUTED]
    n = len(rows)
    by_name = {name: idx for idx, (name, _t) in enumerate(all_cols)}
    group_map = _group_of_column()

    columns, data = [], {}
    for name, base_type in all_cols:
        col_idx = by_name[name]
        raw = [r[col_idx] for r in rows]

        ctype = base_type
        if name in _FORCE_LINK:
            ctype = 'link'
        elif name in _FORCE_TEXT:
            ctype = 'text'
        elif base_type == 'text':
            distinct = {v for v in raw if v is not None and v != ''}
            ctype = 'cat' if len(distinct) <= _CAT_MAX_DISTINCT else 'text'

        spec = {
            'name': name,
            'label': _label_for(name),
            'type': ctype,
            'group': group_map.get(name, 'computed'),
        }

        if ctype == 'cat':
            vocab = sorted({v for v in raw if v is not None and v != ''})
            index = {v: i for i, v in enumerate(vocab)}
            spec['dict'] = vocab
            data[name] = [index.get(v) if v not in (None, '') else None
                          for v in raw]
        elif ctype == 'multi':
            vocab = sorted({v for arr in raw for v in (arr or [])})
            index = {v: i for i, v in enumerate(vocab)}
            spec['dict'] = vocab
            data[name] = [[index[v] for v in (arr or [])] for arr in raw]
        elif ctype == 'bool':
            data[name] = [None if v is None else (1 if v else 0) for v in raw]
        elif ctype == 'num':
            # float() so Decimal (numeric columns) survives json.dumps, and
            # so int8 volumes don't sprout a spurious .0 in the browser.
            data[name] = [_num(v) for v in raw]
        elif ctype in ('date', 'datetime'):
            data[name] = [None if v is None else v.isoformat() for v in raw]
        else:  # text, link
            data[name] = [v if v else None for v in raw]

        columns.append(spec)

    have = {c['name'] for c in columns}
    presets = {k: [c for c in v if c in have] for k, v in _PRESETS.items()}
    if editor:
        presets['default'] = presets['default'] + ['status']

    return {
        'n': n,
        'version': views._data_version,
        'editor': editor,
        'groups': [{'key': k, 'title': t} for k, t in _GROUP_TITLES],
        'presets': presets,
        'columns': columns,
        'data': data,
    }


@require_safe
def api_table(request):
    """Columnar dump of the whole (visible) inventory. Cached per audience.

    Compressed here rather than left to the proxy: the payload is ~1.1 MB of
    highly repetitive JSON that gzips to ~170 kB, and this is the page's only
    request — a first paint that waits on a megabyte over a field connection
    is the difference between usable and not. Caddy's `encode` directive lives
    in the monitoring stack's config, outside this repo, so relying on it
    would make our page's load time depend on a file we don't deploy. Both
    representations are cached, so compression costs nothing per request.

    Safe to compress despite BREACH: the payload carries no CSRF token, no
    session data, and nothing the requester can inject into it.
    """
    editor = is_inventory_editor(request.user)
    key = 'table_editor' if editor else 'table_public'
    entry = views._cache.get(key)
    if entry is None:
        body = json.dumps(_build_payload(editor), separators=(',', ':'),
                          allow_nan=False).encode('utf-8')
        entry = {'raw': body, 'gz': gzip.compress(body, 6)}
        views._cache[key] = entry

    if 'gzip' in request.META.get('HTTP_ACCEPT_ENCODING', ''):
        resp = HttpResponse(entry['gz'], content_type='application/json')
        resp['Content-Encoding'] = 'gzip'
    else:
        resp = HttpResponse(entry['raw'], content_type='application/json')
    resp['Cache-Control'] = 'no-cache'
    # Two representations under one URL — without this a shared cache could
    # hand the gzipped bytes to a client that never asked for them.
    resp['Vary'] = 'Accept-Encoding, Cookie'
    return resp


def table_page(request):
    return render(request, 'inventory/explore.html', {
        'data_version': views._data_version,
    })
