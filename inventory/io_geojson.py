"""GeoJSON round-trip for the inventory.

Two FeatureCollections, in a zip:
- landslides.geojson:           one Feature per row in `landslides`,
                                geometry: null, all columns in properties.
- landslide_polygons.geojson:   one Feature per row in `landslide_polygons`,
                                geometry: the polygon, properties carry
                                landslide_id/role/area/thickness/etc.

Schema discovery is dynamic (information_schema). The column lists are
returned from each export so the importer knows what to expect.

`created_at` and `updated_at` on landslides are excluded from properties on
export (auto-managed by Postgres) and not written on import.

Import semantics (first-shot scope):
- Features matched by `id` get UPDATEd.
- Features with `id` not present in DB are reported as "would be added" but
  NOT inserted (full INSERT support deferred — needs sequence-reset and
  FK-ordering work).
- DB rows missing from the upload are kept silently (deletion not supported).
- Geometries are compared with PostGIS ST_Equals to avoid false positives
  from JSON precision noise.
"""
import datetime
import decimal
import json

from .views import _get_conn, _put_conn

EXPORT_FORMAT_VERSION = 1

# Columns we don't round-trip — Postgres auto-manages these.
LANDSLIDES_AUTO_COLS = ('created_at', 'updated_at')


class _GeoJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        return super().default(obj)


def _table_columns(cur, table, exclude=()):
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    )
    return [r[0] for r in cur.fetchall() if r[0] not in exclude]


def export_landslides_fc():
    """Build the FeatureCollection for `landslides`.

    Each Feature gets:
    - geometry: Point (WGS84) at the landslide's representative centroid,
      built from the stored `centroid_lat` / `centroid_lon` columns. NULL
      geometry if those columns are unset.
    - properties: every column from the `landslides` table (including the
      four stored centroid_* columns, populated by the centroid rules at
      /inventory/manage/rules/).
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cols = _table_columns(cur, 'landslides', exclude=LANDSLIDES_AUTO_COLS)
        cur.execute(
            f"SELECT {', '.join(cols)} FROM landslides ORDER BY id"
        )
        rows = cur.fetchall()
        conn.rollback()
    finally:
        _put_conn(conn)

    features = []
    for row in rows:
        props = dict(zip(cols, row))
        lat, lon = props.get('centroid_lat'), props.get('centroid_lon')
        geom = (
            {'type': 'Point', 'coordinates': [lon, lat]}
            if lat is not None and lon is not None
            else None
        )
        features.append({
            'type': 'Feature',
            'id': props['id'],
            'geometry': geom,
            'properties': props,
        })
    return {'type': 'FeatureCollection', 'features': features}, cols


def export_polygons_fc():
    """Build the FeatureCollection for `landslide_polygons` (normalized form)."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cols = _table_columns(cur, 'landslide_polygons', exclude=('geom',))
        # max_decimal_digits=15 = full IEEE 754 double precision; default is 9 which
        # rounds enough to fail ST_Equals on round-trip. Full precision adds a few
        # chars per coordinate but compresses well under gzip.
        cur.execute(
            f"""
            SELECT {', '.join(cols)}, ST_AsGeoJSON(geom, 15)::json AS geom_json
            FROM landslide_polygons ORDER BY id
            """
        )
        rows = cur.fetchall()
        conn.rollback()
    finally:
        _put_conn(conn)

    features = []
    for row in rows:
        props = dict(zip(cols, row[:-1]))
        geom = row[-1]
        features.append({
            'type': 'Feature',
            'id': props['id'],
            'geometry': geom,
            'properties': props,
        })
    return {'type': 'FeatureCollection', 'features': features}, cols


def export_polygons_flat_fc():
    """Denormalized polygons FeatureCollection: each polygon Feature carries
    its parent landslide's columns merged in.

    Single-file alternative to (polygons.geojson + landslides.geojson + join).
    Export-only — the importer ignores this file (redundant with the
    normalized pair, and not editable without conflict risk).

    Column conventions:
    - `id`            = polygon's id (also the Feature ID)
    - `polygon_id`    = same as `id`, repeated as a property for clarity
    - `landslide_id`  = parent landslide's id (the link key)
    - polygon-side:   role, is_primary, thickness, area, polygon_volume
    - landslide-side: every column from `landslides` EXCEPT `id` (since
                      landslide_id is the link) and the auto-managed
                      timestamps. The four centroid_* columns come along
                      for the ride from the landslides table.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()

        po_cols = _table_columns(cur, 'landslide_polygons', exclude=('geom',))
        ls_cols = _table_columns(cur, 'landslides',
                                 exclude=('id',) + LANDSLIDES_AUTO_COLS)

        po_select = ', '.join(f'p.{c}' for c in po_cols)
        ls_select = ', '.join(f'l.{c}' for c in ls_cols)

        cur.execute(
            f"""
            SELECT {po_select},
                   {ls_select},
                   ST_AsGeoJSON(p.geom, 15)::json AS geom_json
            FROM landslide_polygons p
            JOIN landslides l ON l.id = p.landslide_id
            ORDER BY p.id
            """
        )
        rows = cur.fetchall()
        conn.rollback()
    finally:
        _put_conn(conn)

    n_po = len(po_cols)
    n_ls = len(ls_cols)

    features = []
    for row in rows:
        po_vals = row[:n_po]
        ls_vals = row[n_po : n_po + n_ls]
        geom    = row[-1]
        props = dict(zip(po_cols, po_vals))
        props['polygon_id'] = props['id']
        for k, v in zip(ls_cols, ls_vals):
            props[k] = v
        features.append({
            'type': 'Feature',
            'id': props['id'],
            'geometry': geom,
            'properties': props,
        })
    flat_cols = po_cols + ['polygon_id'] + ls_cols
    return {'type': 'FeatureCollection', 'features': features}, flat_cols


def export_survey_circles_fc():
    """Build the FeatureCollection for `survey_circles`.

    A set of ~525 random circles used for evaluating inventory completeness
    (each circle is a sample area that was systematically reviewed for
    landslides). Hidden by default on the map; always included in downloads.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cols = _table_columns(cur, 'survey_circles', exclude=('geom',))
        cur.execute(
            f"SELECT {', '.join(cols)}, ST_AsGeoJSON(geom, 15)::json AS g "
            f"FROM survey_circles ORDER BY id"
        )
        rows = cur.fetchall()
        conn.rollback()
    finally:
        _put_conn(conn)

    features = []
    for row in rows:
        props = dict(zip(cols, row[:-1]))
        features.append({
            'type': 'Feature',
            'id': props['id'],
            'geometry': row[-1],
            'properties': props,
        })
    return {'type': 'FeatureCollection', 'features': features}, cols


def _map_settings_dict():
    """Read all map_settings rows as a flat dict."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM map_settings")
        out = dict(cur.fetchall())
        conn.rollback()
    finally:
        _put_conn(conn)
    return out


_DEFAULT_URLS = {
    'map':     'https://landslidescience.org/inventory/',
    'methods': 'https://landslidescience.org/inventory/methods/',
    'repo':    'https://github.com/hig314/landslidescience',
}


def _rules_summary():
    """Build a manifest block summarizing each derived rule.

    Each entry shows the target table + column, the rule's `summary`
    attribute, and a `kind` of 'sql' (the body is a SQL statement
    executed in Postgres) or 'python' (the body is a Python function
    over a row dict). Order follows the registry's dependency order,
    so reading top-to-bottom corresponds to apply-from-scratch order.
    """
    from .derived import RULES
    out = {}
    for name, fn in RULES.items():
        out[name] = {
            'target':  f"{getattr(fn, 'target_table', 'landslides')}.{fn.target_column}",
            'kind':    'sql' if getattr(fn, 'is_sql', False) else 'python',
            'summary': getattr(fn, 'summary', ''),
        }
    return out


def build_export_bundle(urls=None):
    """Return (zip_bytes, filename) for the current inventory state.

    Contents:
      manifest.json
      landslides.geojson                 — normalized records (Point geometry at centroid)
      landslide_polygons.geojson         — normalized polygons (MultiPolygon)
      landslide_polygons_flat.geojson    — denormalized: polygons with parent landslide attrs merged in
      landslides.qml                     — QGIS style for the points layer
      landslide_polygons.qml             — QGIS style for the polygons layer (assumes landslide_class is present)
      landslide_polygons_flat.qml        — byte-identical copy so QGIS auto-loads the same style on the flat file

    `urls` is an optional dict with keys `map` / `methods` / `repo` overriding
    the defaults (production landslidescience.org / GitHub). The view passes
    request-derived absolute URLs so dev exports get dev URLs.

    The flat file is export-only — re-uploading it via /inventory/manage/import/
    silently ignores it (the normalized pair is authoritative).
    """
    import io
    import zipfile
    from .qml import build_qml_points, build_qml_polygons, build_qml_survey_circles

    u = dict(_DEFAULT_URLS)
    if urls:
        u.update({k: v for k, v in urls.items() if v})

    landslides_fc,        landslides_cols    = export_landslides_fc()
    polygons_fc,          polygons_cols      = export_polygons_fc()
    polygons_flat_fc,     polygons_flat_cols = export_polygons_flat_fc()
    circles_fc,           circles_cols       = export_survey_circles_fc()
    settings = _map_settings_dict()
    qml_points   = build_qml_points(settings)
    qml_polygons = build_qml_polygons(settings)
    qml_circles  = build_qml_survey_circles()

    manifest = {
        'export_format_version': EXPORT_FORMAT_VERSION,
        'exported_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'source': {
            'project':     'Alaska Landslide Inventory',
            'map_url':     u['map'],
            'methods_url': u['methods'],
            'repo_url':    u['repo'],
        },
        'polygon_conventions': {
            'roles': 'Each polygon carries a `role` of body, source, or deposit. '
                     'Slow landslides have a single body polygon. Catastrophic '
                     'landslides have one or more source polygons and/or one or '
                     'more deposit polygons.',
            'is_primary': 'Slow landslides flag their body polygon '
                          'is_primary=true. Catastrophic landslides flag '
                          'their source polygon (or one source, when there '
                          'are multiple) is_primary=true; deposit polygons '
                          'are never flagged primary. A small number of '
                          'catastrophic records have no source polygon at '
                          'all — these have no primary polygon, and the '
                          'centroid falls back to the deposit.',
        },
        'coordinate_reference_systems': {
            'feature_geometries': {
                'epsg': 4326,
                'name': 'WGS 84',
                'note': 'Per GeoJSON RFC 7946, all Feature geometries are in WGS84 (EPSG:4326). '
                        'No `crs` field is set — it is deprecated in the current spec and '
                        'consumers should assume EPSG:4326.',
            },
            'computations': {
                'areas':     'PostGIS ST_Area on geometry transformed to EPSG:3338 (NAD83 / Alaska '
                             'Albers, equal-area). The polygon `area` property is in m².',
                'centroids': 'ST_Centroid computed on EPSG:3338 geometry; the resulting point is '
                             'used directly for centroid_albers_x/y (meters) and re-projected to '
                             'WGS84 for centroid_lat/lon and for the Point geometry on '
                             'landslides.geojson features.',
            },
            'projected_properties': {
                'centroid_albers_x': 'EPSG:3338 easting in meters.',
                'centroid_albers_y': 'EPSG:3338 northing in meters.',
                'centroid_lat':      'WGS84 latitude in decimal degrees.',
                'centroid_lon':      'WGS84 longitude in decimal degrees.',
            },
        },
        'rules':            _rules_summary(),
        'files': {
            'landslides.geojson': '1 feature per landslide. Geometry: Point at primary-polygon '
                                  'centroid (slow → body, catastrophic → source then deposit). '
                                  'All columns plus four centroid_* fields in properties.',
            'landslide_polygons.geojson': '1 feature per polygon. Geometry: MultiPolygon (WGS84). '
                                          'Properties carry landslide_id, role, area, polygon_volume, '
                                          'thickness, is_primary.',
            'landslide_polygons_flat.geojson': 'Denormalized: each polygon feature carries the '
                                               'parent landslide attributes merged in. Export-only — '
                                               'silently ignored on import; the normalized pair is '
                                               'authoritative.',
            'landslides.qml': 'QGIS style for landslides.geojson — categorized by landslide_class. '
                              'Auto-loads when the .qml and .geojson share a basename in the same '
                              'directory.',
            'landslide_polygons.qml': 'QGIS style for landslide_polygons.geojson — rule-based by '
                                      'landslide_class. Requires the class column to be present '
                                      '(via a QGIS table-join to landslides on the normalized file).',
            'landslide_polygons_flat.qml': 'Byte-identical copy of landslide_polygons.qml so QGIS '
                                           'auto-loads the same style on the flat file.',
            'survey_circles.geojson': 'Sample circles used for inventory-completeness evaluation '
                                      '(525 multipolygons). Independent of the landslide tables; '
                                      'hidden by default on the map (togglable in the legend).',
            'survey_circles.qml': 'QGIS style for survey_circles.geojson — black outline only '
                                  '(no fill), thin for update_total=0, bold for >0, plus a '
                                  'numerical label of update_total on circles with hits.',
        },
        'tables': {
            'landslides': {
                'count':   len(landslides_fc['features']),
                'columns': landslides_cols,
            },
            'landslide_polygons': {
                'count':   len(polygons_fc['features']),
                'columns': polygons_cols,
            },
            'landslide_polygons_flat': {
                'count':   len(polygons_flat_fc['features']),
                'columns': polygons_flat_cols,
            },
            'survey_circles': {
                'count':   len(circles_fc['features']),
                'columns': circles_cols,
            },
        },
    }

    today = datetime.date.today().strftime('%y%m%d')
    fname = f'landslidescience_inventory_{today}.zip'

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json',                     json.dumps(manifest, indent=2))
        z.writestr('landslides.geojson',                json.dumps(landslides_fc,    cls=_GeoJSONEncoder, indent=2))
        z.writestr('landslide_polygons.geojson',        json.dumps(polygons_fc,      cls=_GeoJSONEncoder, indent=2))
        z.writestr('landslide_polygons_flat.geojson',   json.dumps(polygons_flat_fc, cls=_GeoJSONEncoder, indent=2))
        z.writestr('landslides.qml',                    qml_points)
        z.writestr('landslide_polygons.qml',            qml_polygons)
        z.writestr('landslide_polygons_flat.qml',       qml_polygons)
        z.writestr('survey_circles.geojson',            json.dumps(circles_fc,       cls=_GeoJSONEncoder, indent=2))
        z.writestr('survey_circles.qml',                qml_circles)
    buf.seek(0)
    return buf.read(), fname


# ---------------------------------------------------------------------------
# Import side
# ---------------------------------------------------------------------------

class ImportError_(Exception):
    """Raised on unrecoverable upload-validation errors."""


def parse_upload(file_bytes):
    """Parse an uploaded zip OR a single .geojson into the two FeatureCollections.

    Returns (landslides_fc, polygons_fc, manifest_or_None).
    Raises ImportError_ with a descriptive message on bad input.

    Accepted shapes:
    - A zip with at least landslides.geojson + landslide_polygons.geojson
      (and optionally a manifest.json)
    - A single .geojson file that's the landslides FeatureCollection
      (polygons FC will be {} — typed as "landslides-only" upload)
    """
    import io
    import zipfile

    # Try as zip first
    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes), 'r')
    except zipfile.BadZipFile:
        zf = None

    if zf is not None:
        names = set(zf.namelist())
        if 'landslides.geojson' not in names or 'landslide_polygons.geojson' not in names:
            raise ImportError_(
                'Zip must contain both landslides.geojson and '
                'landslide_polygons.geojson at the top level. '
                f'Found: {sorted(names)}'
            )
        with zf.open('landslides.geojson') as f:
            landslides_fc = json.load(f)
        with zf.open('landslide_polygons.geojson') as f:
            polygons_fc = json.load(f)
        manifest = None
        if 'manifest.json' in names:
            with zf.open('manifest.json') as f:
                manifest = json.load(f)
    else:
        # Single GeoJSON — landslides only.
        try:
            landslides_fc = json.loads(file_bytes)
        except json.JSONDecodeError as e:
            raise ImportError_(f'Not a zip and not valid JSON: {e}')
        polygons_fc = {'type': 'FeatureCollection', 'features': []}
        manifest = None

    for fc, name in [(landslides_fc, 'landslides'), (polygons_fc, 'landslide_polygons')]:
        if fc.get('type') != 'FeatureCollection':
            raise ImportError_(f'{name}: expected FeatureCollection, got {fc.get("type")!r}')
        if not isinstance(fc.get('features'), list):
            raise ImportError_(f'{name}: missing or non-list "features"')

    return landslides_fc, polygons_fc, manifest


# Type coercion: take raw value from JSON and convert to what Postgres expects
# for that column. Returns the value unchanged for text/integer/etc.
def _coerce(udt_name, val):
    if val is None:
        return None
    if udt_name == 'date':
        return datetime.date.fromisoformat(val) if isinstance(val, str) else val
    if udt_name == 'timestamptz':
        if isinstance(val, str):
            return datetime.datetime.fromisoformat(val)
        return val
    if udt_name == 'bool':
        return bool(val)
    if udt_name in ('int4', 'int8'):
        return int(val) if val != '' else None
    if udt_name == 'float8':
        return float(val) if val != '' else None
    return val


def _column_types(cur, table):
    """Return {col_name: udt_name} for the given table."""
    cur.execute(
        """
        SELECT column_name, udt_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    )
    return dict(cur.fetchall())


def compute_diff(landslides_fc, polygons_fc):
    """Compare upload against current DB. Pure-ish (reads DB, doesn't mutate).

    Returns a dict:
        {
          'landslides':         {'updates': [...], 'would_add': [...], 'unchanged': N, 'warnings': [...]},
          'landslide_polygons': {'updates': [...], 'would_add': [...], 'unchanged': N, 'warnings': [...]},
        }
    Each `update` is {id, changes: {col: {old, new}}}.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        ls_types = _column_types(cur, 'landslides')
        po_types = _column_types(cur, 'landslide_polygons')
        ls_diff = _diff_landslides(cur, landslides_fc['features'], ls_types)
        po_diff = _diff_polygons(cur, polygons_fc['features'], po_types)
        conn.rollback()
    finally:
        _put_conn(conn)
    return {'landslides': ls_diff, 'landslide_polygons': po_diff}


def _diff_landslides(cur, features, types):
    cols = [c for c in types if c not in LANDSLIDES_AUTO_COLS]
    cur.execute(f"SELECT {', '.join(cols)} FROM landslides")
    db_by_id = {row[0]: dict(zip(cols, row)) for row in cur.fetchall()}

    updates, would_add, unchanged, warnings = [], [], 0, []
    seen_ids = set()
    for feat in features:
        feat_id = feat.get('id') or feat.get('properties', {}).get('id')
        if feat_id is None:
            warnings.append('feature with no id (skipped — INSERT not yet supported)')
            continue
        seen_ids.add(feat_id)
        props = feat.get('properties', {})
        unknown = set(props) - set(types)
        if unknown:
            warnings.append(f'id={feat_id}: unknown columns ignored: {sorted(unknown)}')

        if feat_id not in db_by_id:
            would_add.append({'id': feat_id, 'unique_name': props.get('unique_name', '?')})
            continue

        db_row = db_by_id[feat_id]
        changes = {}
        for col in cols:
            if col not in props:
                continue  # column not in upload → skip (don't write)
            new = _coerce(types[col], props[col])
            old = db_row[col]
            if new != old:
                changes[col] = {'old': old, 'new': new}
        if changes:
            updates.append({'id': feat_id, 'changes': changes})
        else:
            unchanged += 1

    return {
        'updates': updates,
        'would_add': would_add,
        'unchanged': unchanged,
        'warnings': warnings,
        'db_only_count': len(set(db_by_id) - seen_ids),
    }


def _diff_polygons(cur, features, types):
    """Polygon diff: like landslides, but geometry compared via ST_Equals."""
    non_geom_cols = [c for c in types if c != 'geom']
    cur.execute(
        f"SELECT {', '.join(non_geom_cols)}, ST_AsText(geom) FROM landslide_polygons"
    )
    rows = cur.fetchall()
    db_by_id = {}
    for row in rows:
        d = dict(zip(non_geom_cols, row[:-1]))
        d['_geom_wkt'] = row[-1]
        db_by_id[d['id']] = d

    updates, would_add, unchanged, warnings = [], [], 0, []
    seen_ids = set()
    for feat in features:
        feat_id = feat.get('id') or feat.get('properties', {}).get('id')
        if feat_id is None:
            warnings.append('polygon with no id (skipped)')
            continue
        seen_ids.add(feat_id)
        props = feat.get('properties', {})
        if feat_id not in db_by_id:
            would_add.append({'id': feat_id, 'landslide_id': props.get('landslide_id')})
            continue

        db_row = db_by_id[feat_id]
        changes = {}
        for col in non_geom_cols:
            if col not in props:
                continue
            new = _coerce(types[col], props[col])
            old = db_row[col]
            if new != old:
                changes[col] = {'old': old, 'new': new}

        # Geometry compare: feed the upload's geom JSON back through ST_Equals
        upload_geom = feat.get('geometry')
        if upload_geom is not None:
            cur.execute(
                "SELECT ST_Equals(geom, ST_GeomFromGeoJSON(%s)) FROM landslide_polygons WHERE id = %s",
                (json.dumps(upload_geom), feat_id),
            )
            equal_row = cur.fetchone()
            if equal_row and equal_row[0] is False:
                changes['geom'] = {'old': '<existing geometry>', 'new': '<new geometry>'}

        if changes:
            updates.append({'id': feat_id, 'changes': changes})
        else:
            unchanged += 1

    return {
        'updates': updates,
        'would_add': would_add,
        'unchanged': unchanged,
        'warnings': warnings,
        'db_only_count': len(set(db_by_id) - seen_ids),
    }


def apply_import(landslides_fc, polygons_fc, user):
    """Apply UPDATEs ONLY for features whose values differ from the current DB.

    Diff-driven: re-runs `compute_diff` so an unchanged round-trip is a no-op.
    Records that `would_add` are NOT inserted (INSERT support deferred).
    Audit log entries are written only for landslides that actually changed
    (either their attributes or any of their polygons).
    """
    from .models import LandslideEditMeta

    diff = compute_diff(landslides_fc, polygons_fc)

    ls_by_id = {(f.get('id') or f.get('properties', {}).get('id')): f
                for f in landslides_fc.get('features', [])}
    po_by_id = {(f.get('id') or f.get('properties', {}).get('id')): f
                for f in polygons_fc.get('features', [])}

    affected_landslide_ids = set()
    summary = {'landslides_updated': 0, 'polygons_updated': 0, 'skipped': 0}

    conn = _get_conn()
    try:
        cur = conn.cursor()
        ls_types = _column_types(cur, 'landslides')
        po_types = _column_types(cur, 'landslide_polygons')

        for u in diff['landslides']['updates']:
            feat  = ls_by_id[u['id']]
            props = feat.get('properties', {})
            sets, vals = [], []
            for col in u['changes']:
                if col == 'id' or col in LANDSLIDES_AUTO_COLS:
                    continue
                # Skip columns that don't exist in the current schema
                # (e.g. a re-imported old zip referencing a since-dropped
                # column). Older exports stay forward-compatible.
                if col not in ls_types:
                    continue
                sets.append(f'{col} = %s')
                vals.append(_coerce(ls_types[col], props[col]))
            if sets:
                vals.append(u['id'])
                cur.execute(f"UPDATE landslides SET {', '.join(sets)} WHERE id = %s", vals)
                summary['landslides_updated'] += 1
                affected_landslide_ids.add(u['id'])

        for u in diff['landslide_polygons']['updates']:
            feat  = po_by_id[u['id']]
            props = feat.get('properties', {})
            sets, vals = [], []
            for col in u['changes']:
                if col == 'id':
                    continue
                if col == 'geom':
                    sets.append('geom = ST_GeomFromGeoJSON(%s)')
                    vals.append(json.dumps(feat['geometry']))
                elif col in po_types:
                    sets.append(f'{col} = %s')
                    vals.append(_coerce(po_types[col], props[col]))
            if sets:
                vals.append(u['id'])
                cur.execute(
                    f"UPDATE landslide_polygons SET {', '.join(sets)} WHERE id = %s",
                    vals,
                )
                summary['polygons_updated'] += 1
                ls_id = props.get('landslide_id')
                if ls_id is not None:
                    affected_landslide_ids.add(ls_id)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _put_conn(conn)

    for ls_id in affected_landslide_ids:
        LandslideEditMeta.objects.update_or_create(
            landslide_id=ls_id,
            defaults={'last_edited_by': user},
        )

    return summary

