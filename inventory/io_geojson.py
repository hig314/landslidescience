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
import difflib
import json
import unicodedata

from .views import _get_conn, _put_conn

EXPORT_FORMAT_VERSION = 1

# Columns we don't round-trip — server-managed (Postgres auto / induction /
# supersede-merge flow), never written or exported via the normal upload path.
LANDSLIDES_AUTO_COLS = ('created_at', 'updated_at', 'reviewed_at',
                        'deprecated_at', 'superseded_by')


def normalize_name(s):
    """Canonical form of a unique_name for *comparison*: NFC unicode, trimmed,
    internal whitespace collapsed. Capitalization is preserved (proper nouns)."""
    if s is None:
        return ''
    return ' '.join(unicodedata.normalize('NFC', str(s)).split())


def name_key(s):
    """Case-insensitive comparison key — catches case/whitespace-only diffs."""
    return normalize_name(s).casefold()


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


def _route_single_fc(fc):
    """Route one FeatureCollection into the (landslides_fc, polygons_fc) pair.

    First feature's geometry type decides: Polygon/MultiPolygon → polygons
    file (flat-polygon mode); anything else → landslides file (the legacy
    single-file upload shape).
    """
    empty_fc = lambda: {'type': 'FeatureCollection', 'features': []}
    first_geom = None
    for f in (fc.get('features') or []):
        if f.get('geometry'):
            first_geom = f['geometry'].get('type')
            break
    if first_geom in ('Polygon', 'MultiPolygon'):
        return empty_fc(), fc
    return fc, empty_fc()


def _read_gdal_to_fc(file_bytes, filename, is_zip):
    """Read a non-GeoJSON GIS upload (shp zip / gpkg / kml / kmz) into a
    GeoJSON FeatureCollection via GDAL (pyogrio).

    Writes the bytes to a temp file so GDAL can mmap it (and so /vsizip/
    works for shapefile zips). Lazy-imports pyogrio + shapely to keep
    the GDAL/numpy load off the hot request path. `force_2d=True` strips
    altitude/Z coordinates that KML carries by default but PostGIS doesn't
    want for our 2D polygons.
    """
    import os
    import tempfile
    from pyogrio.raw import read as pyogrio_read
    from shapely import wkb as _wkb

    suffix = '.zip' if is_zip else ('.' + filename.rsplit('.', 1)[-1] if '.' in filename else '.bin')
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(file_bytes)
    tmp.close()
    path = ('/vsizip/' + tmp.name) if is_zip else tmp.name
    try:
        try:
            meta, _fids, geometry, field_data = pyogrio_read(
                path, read_geometry=True, force_2d=True)
        except Exception as e:
            raise ImportError_(f'Could not read {filename!r} via pyogrio: {e}')
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    # PostGIS ST_GeomFromGeoJSON assumes EPSG:4326. Sniff the source CRS
    # and refuse anything else — reprojection belongs in QGIS, not here.
    crs_label = (meta.get('crs') or '').lower()
    if crs_label and 'epsg:4326' not in crs_label:
        raise ImportError_(
            f'Source CRS of {filename!r} is {meta.get("crs")!r}. PostGIS '
            'expects EPSG:4326 (WGS84 lat/lon). Reproject in QGIS — '
            'Vector → Save Features As… → CRS: EPSG:4326 — and re-upload.'
        )

    # meta['fields'] is a numpy ndarray of column names; can't use `or []`.
    fields_arr = meta.get('fields')
    fields = list(fields_arr) if fields_arr is not None else []
    # Shapefile DBF caps field names at 10 chars, so common landslide
    # columns get truncated on write. Map the unambiguous cases back to
    # their canonical names so the synthesizer can see them. Ambiguous
    # truncations (e.g. `landslide_` could be type or class) are NOT
    # auto-mapped — the editor needs to use a clearer field name in
    # those cases or upload as GPKG/KML/GeoJSON.
    SHAPEFILE_ALIASES = {
        'unique_nam': 'unique_name',
        'descriptio': 'description',
        'volume_met': 'volume_method',
        'volume_pre': 'volume_preferred',
        'creep_beha': 'creep_behavior',
    }
    fields = [SHAPEFILE_ALIASES.get(f, f) for f in fields]
    n = 0 if geometry is None else len(geometry)
    out = []
    for i in range(n):
        wkb_bytes = geometry[i]
        if wkb_bytes is None or len(wkb_bytes) == 0:
            geom = None
        else:
            shape = _wkb.loads(bytes(wkb_bytes))
            geom = shape.__geo_interface__
        props = {fld: _python_scalar(field_data[j][i])
                 for j, fld in enumerate(fields)}
        # KML / GeoPackage features often lack an explicit `id` property.
        # The downstream diff uses id to classify new vs. existing rows,
        # so synthesize a string id here so each feature is uniquely
        # addressable through the import pipeline.
        if not props.get('id'):
            props['id'] = f'upload-{i+1}'
        out.append({
            'type': 'Feature',
            'id': props['id'],
            'geometry': geom,
            'properties': props,
        })
    return {'type': 'FeatureCollection', 'features': out}


def _python_scalar(v):
    """Convert numpy scalars to plain Python so json.dumps can serialize them.
    Datetime, NaN, and bytes get normalized into JSON-compatible values."""
    import math
    try:
        item = v.item()
    except AttributeError:
        item = v
    if isinstance(item, float) and math.isnan(item):
        return None
    if isinstance(item, bytes):
        return item.decode('utf-8', errors='replace')
    if hasattr(item, 'isoformat'):
        return item.isoformat()
    return item


def parse_upload(file_bytes, filename=''):
    """Parse an upload into the (landslides_fc, polygons_fc, manifest) triple.

    Returns (landslides_fc, polygons_fc, manifest_or_None).
    Raises ImportError_ with a descriptive message on bad input.

    Accepted shapes:
    - Zip with landslides.geojson + landslide_polygons.geojson (the full
      round-trip pair; optionally a manifest.json).
    - Zip with only landslide_polygons(_flat).geojson — polygons file used
      as-is; landslides FC is empty. New polygons with a unique_name get
      grouped by name during compute_diff and one landslide synthesized
      per group.
    - Zip containing a shapefile (.shp + .dbf + .shx + .prj) — read via
      fiona, then routed by geometry type.
    - Single .geojson, .gpkg, .kml, or .kmz — read directly, routed by
      first feature's geometry type. Polygon/MultiPolygon → polygons
      file; Point/null → landslides file.

    Required CRS for non-GeoJSON formats: EPSG:4326. GeoJSON is assumed
    to be EPSG:4326 already.
    """
    import io
    import zipfile

    empty_fc = lambda: {'type': 'FeatureCollection', 'features': []}
    name_lower = (filename or '').lower()

    # Try as zip
    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes), 'r')
    except zipfile.BadZipFile:
        zf = None

    if zf is not None:
        names = set(zf.namelist())
        has_geojson = any(n.lower().endswith('.geojson') for n in names)
        has_shapefile = any(n.lower().endswith('.shp') for n in names)
        has_gpkg = any(n.lower().endswith('.gpkg') for n in names)
        has_kml  = any(n.lower().endswith(('.kml', '.kmz')) for n in names)

        if has_geojson:
            # Legacy / round-trip GeoJSON zip.
            polygons_member = None
            for cand in ('landslide_polygons.geojson', 'landslide_polygons_flat.geojson'):
                if cand in names:
                    polygons_member = cand
                    break
            ls_member = 'landslides.geojson' if 'landslides.geojson' in names else None
            if polygons_member is None and ls_member is None:
                raise ImportError_(
                    'Zip contains .geojson file(s) but neither landslides.geojson nor '
                    'landslide_polygons(_flat).geojson at the top level. '
                    f'Found: {sorted(names)}'
                )
            landslides_fc = empty_fc()
            if ls_member:
                with zf.open(ls_member) as f:
                    landslides_fc = json.load(f)
            polygons_fc = empty_fc()
            if polygons_member:
                with zf.open(polygons_member) as f:
                    polygons_fc = json.load(f)
            manifest = None
            if 'manifest.json' in names:
                with zf.open('manifest.json') as f:
                    manifest = json.load(f)
        elif has_shapefile or has_gpkg or has_kml:
            # GDAL-readable zipped format — convert via fiona.
            fc = _read_gdal_to_fc(file_bytes, filename or 'upload.zip', is_zip=True)
            landslides_fc, polygons_fc = _route_single_fc(fc)
            manifest = None
        else:
            raise ImportError_(
                'Zip does not contain a recognized GIS file. Supported: '
                '.geojson, .shp (+ .dbf/.shx/.prj), .gpkg, .kml, .kmz. '
                f'Found: {sorted(names)}'
            )
    else:
        # Single file
        if name_lower.endswith(('.gpkg', '.kml', '.kmz')):
            fc = _read_gdal_to_fc(file_bytes, filename, is_zip=False)
            landslides_fc, polygons_fc = _route_single_fc(fc)
            manifest = None
        elif name_lower.endswith('.shp'):
            raise ImportError_(
                'Shapefiles must be uploaded as a zip containing .shp, .dbf, '
                '.shx, and .prj together (QGIS: Vector → Save Features As… '
                '→ format ESRI Shapefile, then zip all the output files).'
            )
        else:
            # Treat as GeoJSON (default for .geojson, .json, or unknown).
            try:
                fc = json.loads(file_bytes)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                raise ImportError_(
                    f'Not a recognized format. Supported: .geojson, .gpkg, '
                    f'.kml, .kmz, zipped shapefile, or a zip of the GeoJSON '
                    f'round-trip pair. Parse error: {e}'
                )
            landslides_fc, polygons_fc = _route_single_fc(fc)
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
        # Booleans arrive as native bool, numeric 1/0, or strings depending on
        # the source format. A naive bool(val) turns the *string* "false" / "0"
        # / "no" into True (non-empty string), silently flipping flags like
        # molards. Parse string forms explicitly; leave unrecognized as NULL.
        if isinstance(val, str):
            s = val.strip().lower()
            if s in ('true', 't', 'yes', 'y', '1'):
                return True
            if s in ('false', 'f', 'no', 'n', '0', ''):
                return False
            return None
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


def _synthesize_landslides_from_flat_polygons(landslides_fc, polygons_fc,
                                               ls_types, existing_ls_ids):
    """Group orphan polygons by unique_name and synthesize one landslide per group.

    "Orphan" = polygon whose landslide_id doesn't resolve to a DB row or an
    upload-side landslide. If such a polygon carries a `unique_name`
    property, all matching polygons get grouped and a landslide entry is
    synthesized for them. Landslide-level properties (landslide_type,
    description, etc.) are read from the polygon's own property bag — this
    is exactly what the flat polygons export shape produces.

    Mutates both feature collections in place. Returns a list of human-
    readable warnings (which polygons got grouped, any conflicting
    landslide-level values across the group).

    Idempotent on a previously-synthesized pair: re-running sees the synth
    landslides already present in landslides_fc and skips them.
    """
    warnings = []

    upload_ls_ids = {f.get('id') or f.get('properties', {}).get('id')
                     for f in landslides_fc['features']}
    resolvable = set(existing_ls_ids) | upload_ls_ids

    # Properties we'll lift from polygons into the synthesized landslide.
    # Polygon-only fields (id, landslide_id, role, area, thickness, etc.)
    # and table auto-cols are NOT pulled.
    polygon_only = {'id', 'polygon_id', 'landslide_id', 'role',
                    'is_primary', 'area', 'thickness', 'polygon_volume',
                    'geom'}
    landslide_cols = {c for c in ls_types
                      if c not in LANDSLIDES_AUTO_COLS and c != 'id'
                      and c not in polygon_only}

    orphans_by_name = {}
    for feat in polygons_fc['features']:
        props = feat.get('properties') or {}
        ls_ref = props.get('landslide_id')
        if ls_ref in resolvable:
            continue
        uname = (props.get('unique_name') or '').strip()
        if not uname:
            continue
        orphans_by_name.setdefault(uname, []).append(feat)

    if not orphans_by_name:
        return warnings

    # Avoid colliding with any existing upload-side string ids (e.g., a
    # re-staged upload would already have synth-* present).
    used_synth = {x for x in upload_ls_ids
                  if isinstance(x, str) and x.startswith('synth-')}
    next_idx = 1
    def _next_synth_id():
        nonlocal next_idx
        while True:
            sid = f'synth-{next_idx}'
            next_idx += 1
            if sid not in used_synth:
                used_synth.add(sid)
                return sid

    for uname, polys in orphans_by_name.items():
        synth_id = _next_synth_id()
        # Compose landslide-level properties from polygon property bags.
        # First non-empty value wins; conflicting non-empty values get a
        # warning so the editor can spot data-entry mistakes.
        ls_props = {'id': synth_id, 'unique_name': uname}
        for poly in polys:
            pprops = poly.get('properties') or {}
            for k, v in pprops.items():
                if k not in landslide_cols:
                    continue
                if v is None or v == '':
                    continue
                if k in ls_props and ls_props[k] != v:
                    warnings.append(
                        f'synthesized {uname!r}: conflicting {k!r} '
                        f'({ls_props[k]!r} vs {v!r}); keeping first')
                    continue
                ls_props[k] = v

        # Infer landslide_type from polygon roles when the upload didn't
        # supply it. The convention used everywhere else in the inventory:
        #   source / deposit → catastrophic
        #   body             → slow
        # If neither matches (e.g., a polygon with an unknown or missing
        # role), leave it unset and let the validation error surface.
        if not ls_props.get('landslide_type'):
            roles = {(p.get('properties') or {}).get('role') for p in polys}
            if 'source' in roles or 'deposit' in roles:
                ls_props['landslide_type'] = 'catastrophic'
                warnings.append(
                    f"synthesized {uname!r}: inferred landslide_type='catastrophic' "
                    f"from polygon roles {sorted(r for r in roles if r)}")
            elif 'body' in roles:
                ls_props['landslide_type'] = 'slow'
                warnings.append(
                    f"synthesized {uname!r}: inferred landslide_type='slow' "
                    f"from polygon roles {sorted(r for r in roles if r)}")

        landslides_fc['features'].append({
            'type': 'Feature',
            'id': synth_id,
            'geometry': None,
            'properties': ls_props,
        })
        for poly in polys:
            poly.setdefault('properties', {})['landslide_id'] = synth_id
        warnings.append(
            f'synthesized landslide {uname!r} (id={synth_id}) from '
            f'{len(polys)} polygon(s)')
    return warnings


# Explicit controlled-vocab aliases the fuzzy/plural rules don't catch
# (irregular plurals; add unambiguous synonyms/abbreviations here as they show
# up in real uploads). Keys are alnum-lowercased; values are canonical.
_VOCAB_ALIASES = {
    'bodies': 'body',
}


def _normalize_controlled_vocab(landslides_fc, polygons_fc):
    """Case-fold and strip whitespace on controlled-vocabulary fields so the
    importer accepts "Deposit", "DEPOSIT ", "Slow ", etc. without forcing
    the editor to match exact casing. Mutates both FCs in place. Returns
    a list of human-readable warnings about normalizations performed."""
    warnings = []

    def _norm_against(value, canonical_set):
        """Generously map a controlled-vocab value to its canonical form:
        case-insensitive + trim, then alnum-only, depluralize, and finally a
        conservative fuzzy (typo) match (difflib ratio ≥ 0.8, unambiguous only).
        Returns (normalized_or_original, changed). Unknown/ambiguous values are
        left as-is so validation flags them; every correction emits a warning."""
        if value is None:
            return None, False
        raw = str(value)
        s = raw.strip().lower()
        if s == '':
            return None, False
        if s in canonical_set:
            return s, (s != raw)
        # Strip anything but letters/digits ("Source.", "head-scarp" → "headscarp").
        cleaned = ''.join(ch for ch in s if ch.isalnum())
        if cleaned in canonical_set:
            return cleaned, True
        # Aliases the fuzzy/plural rules miss (irregular plurals, common synonyms).
        alias = _VOCAB_ALIASES.get(cleaned)
        if alias in canonical_set:
            return alias, True
        if cleaned.endswith('s') and cleaned[:-1] in canonical_set:   # plural
            return cleaned[:-1], True
        # Typo correction: accept only a single close match (avoid guessing
        # between e.g. two similar canonical terms).
        matches = difflib.get_close_matches(cleaned, sorted(canonical_set), n=2, cutoff=0.8)
        if len(matches) == 1:
            return matches[0], True
        return value, False  # unknown / ambiguous — leave alone for validation

    polygon_roles = {'source', 'body', 'deposit'}
    landslide_types = {'slow', 'catastrophic'}

    for feat in polygons_fc.get('features') or []:
        props = feat.get('properties') or {}
        if 'role' in props:
            normed, changed = _norm_against(props['role'], polygon_roles)
            if changed:
                warnings.append(f'polygon role: {props["role"]!r} → {normed!r}')
                props['role'] = normed
        # landslide_type may live on polygons in the flat-upload shape.
        if 'landslide_type' in props:
            normed, changed = _norm_against(props['landslide_type'], landslide_types)
            if changed:
                warnings.append(f'polygon landslide_type: {props["landslide_type"]!r} → {normed!r}')
                props['landslide_type'] = normed

    for feat in landslides_fc.get('features') or []:
        props = feat.get('properties') or {}
        if 'landslide_type' in props:
            normed, changed = _norm_against(props['landslide_type'], landslide_types)
            if changed:
                warnings.append(f'landslide_type: {props["landslide_type"]!r} → {normed!r}')
                props['landslide_type'] = normed

    return warnings


def compute_diff(landslides_fc, polygons_fc):
    """Compare upload against current DB.

    Mutates both FCs in place to (1) normalize controlled-vocab fields
    (role, landslide_type — case-folded against the canonical set so the
    importer is tolerant of "Deposit"/"deposit "/etc.) and (2) synthesize
    landslides for flat-polygon uploads (orphan polygons grouped by
    unique_name → one new landslide). The mutation is intentional: the
    normalized + synthesized features carry through to the apply step.
    Idempotent — re-running sees synth ids already present and skips
    re-creation.

    Returns:
        {
          'landslides':         {'updates': [...], 'would_add': [...], 'unchanged': N, 'warnings': [...]},
          'landslide_polygons': {'updates': [...], 'would_add': [...], 'unchanged': N, 'warnings': [...]},
        }
    """
    norm_warnings = _normalize_controlled_vocab(landslides_fc, polygons_fc)

    conn = _get_conn()
    try:
        cur = conn.cursor()
        ls_types = _column_types(cur, 'landslides')
        po_types = _column_types(cur, 'landslide_polygons')
        cur.execute("SELECT id FROM landslides")
        existing_ls_ids = {r[0] for r in cur.fetchall()}
        synth_warnings = _synthesize_landslides_from_flat_polygons(
            landslides_fc, polygons_fc, ls_types, existing_ls_ids)
        ls_diff = _diff_landslides(cur, landslides_fc['features'], ls_types)
        ls_diff['warnings'].extend(synth_warnings)
        # The polygon diff needs to know which upload-side landslide ids will
        # be created during apply, so it can validate landslide_id references
        # on new polygons against either DB or new-in-upload candidates.
        new_landslide_ids = {a['id'] for a in ls_diff['would_add']}
        po_diff = _diff_polygons(cur, polygons_fc['features'], po_types,
                                  new_landslide_ids=new_landslide_ids)
        # Split normalization warnings to where they apply.
        ls_diff['warnings'].extend(w for w in norm_warnings if 'landslide_type' in w
                                                                and 'polygon' not in w)
        po_diff['warnings'].extend(w for w in norm_warnings if 'polygon' in w
                                                                or 'role' in w)
        # Flag would-add records that collide with existing data (by name or
        # location) so the preview can route them to review/merge.
        collisions = _detect_collisions(cur, ls_diff['would_add'], polygons_fc)
        conn.rollback()
    finally:
        _put_conn(conn)
    has_block = any(c['resolution'] == 'block' for c in collisions)
    return {'landslides': ls_diff, 'landslide_polygons': po_diff,
            'collisions': collisions, 'has_block': has_block}


# Spatial near-duplicate thresholds (see the induction plan).
# NEAR_M gates candidates by polygon proximity (more robust than centroid-to-
# centroid, which is sensitive to how each centroid was defined); IoU is the
# real duplicate test.
COLLISION_NEAR_M = 200
COLLISION_IOU = 0.80           # polygon-pair IoU above this flags a duplicate
COLLISION_IDENTICAL_IOU = 0.999  # at/above this the upload IS the same landslide → update in place


def _detect_collisions(cur, would_add, polygons_fc):
    """Flag would-add landslides that collide with existing non-deprecated
    records — by NAME (case/whitespace-insensitive key) or by LOCATION (centroid
    within COLLISION_CENTROID_M metres AND some uploaded↔existing polygon pair
    with IoU > COLLISION_IOU, in EPSG:3338). Report-only: returns one dict per
    colliding upload for the preview to surface.
    """
    if not would_add:
        return []

    # Name index of existing active/pending records (deprecated names are retired).
    cur.execute("SELECT id, unique_name FROM landslides "
                "WHERE deprecated_at IS NULL AND unique_name IS NOT NULL")
    by_key = {}
    for nid, nname in cur.fetchall():
        by_key.setdefault(name_key(nname), []).append((nid, nname))

    # Uploaded polygons grouped by their landslide_id (GeoJSON, EPSG:4326).
    polys_by_ls = {}
    for feat in (polygons_fc.get('features') or []):
        geom = feat.get('geometry')
        lid = (feat.get('properties') or {}).get('landslide_id')
        if geom is not None and lid is not None:
            polys_by_ls.setdefault(lid, []).append(json.dumps(geom))

    iou_expr = ("ST_Area(ST_Intersection(np.geom, cand.geom)) "
                "/ NULLIF(ST_Area(ST_Union(np.geom, cand.geom)), 0)")
    spatial_sql = f"""
        WITH newp AS (
            SELECT ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(g), 4326)) AS g4326
            FROM unnest(%s::text[]) AS g
        ),
        nu4326 AS (SELECT ST_Union(g4326) AS u FROM newp),
        np3338 AS (SELECT ST_Transform(g4326, 3338) AS geom FROM newp),
        cand AS (   -- candidate polygons within NEAR_M of the upload (geography metres)
            SELECT p.landslide_id, ST_MakeValid(ST_Transform(p.geom, 3338)) AS geom
            FROM landslide_polygons p
            JOIN landslides l ON l.id = p.landslide_id AND l.deprecated_at IS NULL
            CROSS JOIN nu4326
            WHERE ST_DWithin(p.geom::geography, nu4326.u::geography, %s)
        )
        SELECT l.id, l.unique_name, MAX({iou_expr}) AS iou
        FROM cand
        JOIN landslides l ON l.id = cand.landslide_id
        CROSS JOIN np3338 np
        GROUP BY l.id, l.unique_name
        HAVING MAX({iou_expr}) > %s
        ORDER BY iou DESC
    """

    collisions = []
    for a in would_add:
        upload_id = a['id']
        uname = a.get('unique_name') or ''
        name_matches = [
            {'id': nid, 'stored_name': stored, 'kind': 'exact' if stored == uname else 'case'}
            for nid, stored in by_key.get(name_key(uname), [])
        ]
        spatial_matches = []
        geoms = polys_by_ls.get(upload_id)
        if geoms:
            cur.execute(spatial_sql, (geoms, COLLISION_NEAR_M, COLLISION_IOU))
            for cid, cname, iou in cur.fetchall():
                spatial_matches.append({'id': cid, 'unique_name': cname, 'iou': round(float(iou), 3)})
        if not (name_matches or spatial_matches):
            continue
        # Classify the resolution (one source of truth for preview + apply):
        #   update — polygons identical (IoU ≥ identical): same landslide
        #            re-uploaded → update the existing record in place.
        #   block  — name-exact dup with non-identical geometry → would violate
        #            the unique_name constraint; must rename/merge first.
        #   review — case/whitespace name dup or a near (not identical) overlap
        #            → surfaced for the editor; not auto-resolved or blocked.
        best = max(spatial_matches, key=lambda m: m['iou'], default=None)
        if best and best['iou'] >= COLLISION_IDENTICAL_IOU:
            resolution, identical_id = 'update', best['id']
        elif any(m['kind'] == 'exact' for m in name_matches):
            resolution, identical_id = 'block', None
        else:
            resolution, identical_id = 'review', None
        collisions.append({
            'upload_id':       upload_id,
            'unique_name':     uname,
            'name_matches':    name_matches,
            'spatial_matches': spatial_matches,
            'resolution':      resolution,
            'identical_id':    identical_id,
        })
    return collisions


_VALID_LANDSLIDE_TYPES = ('slow', 'catastrophic')


def _diff_landslides(cur, features, types):
    cols = [c for c in types if c not in LANDSLIDES_AUTO_COLS]
    cur.execute(f"SELECT {', '.join(cols)} FROM landslides")
    db_by_id = {row[0]: dict(zip(cols, row)) for row in cur.fetchall()}

    updates, would_add, unchanged, warnings = [], [], 0, []
    seen_ids = set()
    for feat in features:
        feat_id = feat.get('id') or feat.get('properties', {}).get('id')
        if feat_id is None:
            warnings.append('feature with no id (skipped — give new landslides any unused id so polygons can reference them)')
            continue
        seen_ids.add(feat_id)
        props = feat.get('properties', {})
        unknown = set(props) - set(types)
        if unknown:
            warnings.append(f'id={feat_id}: unknown columns ignored: {sorted(unknown)}')

        if feat_id not in db_by_id:
            # New record candidate. Required fields: unique_name +
            # landslide_type. Surface validation problems here so the
            # preview UI can refuse to apply a bad upload.
            new_errors = []
            uname = (props.get('unique_name') or '').strip()
            if not uname:
                new_errors.append('missing unique_name')
            ltype = (props.get('landslide_type') or '').strip()
            if ltype not in _VALID_LANDSLIDE_TYPES:
                new_errors.append(f'landslide_type must be one of {_VALID_LANDSLIDE_TYPES}, got {ltype!r}')
            would_add.append({
                'id':            feat_id,
                'unique_name':   uname or '?',
                'landslide_type': ltype or None,
                'errors':        new_errors,
            })
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


_VALID_POLYGON_ROLES = ('source', 'body', 'deposit')


def _diff_polygons(cur, features, types, new_landslide_ids=None):
    """Polygon diff: like landslides, but geometry compared via ST_Equals.

    new_landslide_ids: set of upload-side ids that the landslide-diff has
      already classified as new candidates. Used to validate that each new
      polygon's landslide_id resolves either to an existing DB row or to
      one of these new-in-upload landslides.
    """
    new_landslide_ids = new_landslide_ids or set()
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
    cur.execute("SELECT id FROM landslides")
    existing_landslide_ids = {r[0] for r in cur.fetchall()}

    updates, would_add, unchanged, warnings = [], [], 0, []
    seen_ids = set()
    for feat in features:
        feat_id = feat.get('id') or feat.get('properties', {}).get('id')
        if feat_id is None:
            warnings.append('polygon with no id (skipped — assign any unused id)')
            continue
        seen_ids.add(feat_id)
        props = feat.get('properties', {})
        if feat_id not in db_by_id:
            # New polygon — validate required fields + landslide_id reference.
            new_errors = []
            geom = feat.get('geometry')
            if not geom or geom.get('type') not in ('Polygon', 'MultiPolygon'):
                new_errors.append(f'geometry must be Polygon or MultiPolygon (got {geom.get("type") if geom else None!r})')
            else:
                # Defer detailed PostGIS validity check to apply step
                # (ST_IsValid before INSERT) to keep the diff cheap.
                pass
            ls_ref = props.get('landslide_id')
            if ls_ref is None:
                new_errors.append('missing landslide_id')
            elif ls_ref not in existing_landslide_ids and ls_ref not in new_landslide_ids:
                new_errors.append(
                    f'landslide_id={ls_ref!r} does not refer to an existing landslide '
                    'or a new landslide in this upload')
            role = props.get('role')
            if role not in _VALID_POLYGON_ROLES:
                new_errors.append(f'role must be one of {_VALID_POLYGON_ROLES}, got {role!r}')
            would_add.append({
                'id':            feat_id,
                'landslide_id':  ls_ref,
                'role':          role,
                'errors':        new_errors,
            })
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


def apply_import(landslides_fc, polygons_fc, user, subset_slug=None, common_fields=None):
    """Apply the diff: UPDATE matched features, INSERT new ones.

    Diff-driven: re-runs `compute_diff` so an unchanged round-trip is a no-op.
    `common_fields` (dict of column → value) is applied to every newly-
    inserted landslide, overriding whatever the upload supplied. Use it
    for things like owner, landslide_type, date_min, etc. that the
    editor wants uniform across the batch.

    `subset_slug` is optional. If given, each new landslide also gets a
    membership row in `landslide_subsets`. Owner is no longer derived
    from the subset's default_owner; it must be set via common_fields.

    Refuses to apply if any new landslide or polygon has unresolved
    validation errors — bad uploads fail at the apply step rather than
    silently dropping records.
    """
    from .models import LandslideEditMeta

    common_fields = common_fields or {}

    # If common_fields supplies a value for a required column (landslide_type),
    # back-fill it onto each pending new landslide BEFORE the diff runs so
    # the validation can see it. This lets editors satisfy missing required
    # fields via the apply form without having to fix the upload.
    if common_fields:
        for feat in landslides_fc.get('features') or []:
            fid = feat.get('id') or feat.get('properties', {}).get('id')
            if not isinstance(fid, str):
                continue  # only synthesized / new (string id) records get the override
            props = feat.setdefault('properties', {})
            for col, val in common_fields.items():
                if col in ('id', 'unique_name'):
                    continue
                props[col] = val

    diff = compute_diff(landslides_fc, polygons_fc)

    blocking = []
    for a in diff['landslides']['would_add']:
        for e in a.get('errors') or []:
            blocking.append(f"landslide id={a['id']}: {e}")
    for a in diff['landslide_polygons']['would_add']:
        for e in a.get('errors') or []:
            blocking.append(f"polygon id={a['id']}: {e}")
    if blocking:
        raise ImportError_('Upload has validation errors; refusing to apply:\n  '
                            + '\n  '.join(blocking))

    # Identical re-imports → update-in-place. A would-add landslide whose
    # polygons are essentially identical (IoU ≥ IDENTICAL_IOU) to an existing
    # record is the SAME landslide being re-applied (the master-file workflow):
    # update that record rather than inserting a duplicate (which would also hit
    # the unique_name constraint). Maps upload-side id → existing landslide id.
    identical_to = {c['upload_id']: c['identical_id']
                    for c in diff.get('collisions', []) if c['resolution'] == 'update'}

    # 'block' collisions (name-exact dup, non-identical geometry) would violate
    # the unique_name DB constraint — refuse up front with an actionable message
    # instead of letting the INSERT 500.
    name_conflicts = [
        f"{c['unique_name']!r} already exists as record #{c['name_matches'][0]['id']}"
        for c in diff.get('collisions', []) if c['resolution'] == 'block'
    ]
    if name_conflicts:
        raise ImportError_(
            'Upload would duplicate existing names (unique_name must be unique). '
            'Rename these — or use the merge/supersede flow once available — '
            'before applying:\n  ' + '\n  '.join(name_conflicts))

    ls_by_id = {(f.get('id') or f.get('properties', {}).get('id')): f
                for f in landslides_fc.get('features', [])}
    po_by_id = {(f.get('id') or f.get('properties', {}).get('id')): f
                for f in polygons_fc.get('features', [])}

    affected_landslide_ids = set()
    summary = {
        'landslides_updated':  0, 'polygons_updated':  0,
        'landslides_inserted': 0, 'polygons_inserted': 0,
        'landslides_matched_updated': 0,   # identical re-imports updated in place
        'matched_updates': [],             # (existing_id, name) for the done page
        'skipped': 0,
    }

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

        # ---- INSERT new landslides ----
        # Subset is optional; if supplied, every new landslide gets a
        # membership row. Owner is no longer derived from the subset's
        # default_owner — the editor must set it explicitly (via the
        # common-fields form on the apply page).
        subset_id = None
        if subset_slug:
            cur.execute("SELECT id FROM subsets WHERE slug = %s", (subset_slug,))
            row = cur.fetchone()
            if not row:
                raise ImportError_(f'No subset with slug {subset_slug!r}.')
            subset_id = row[0]

        # Map upload-side landslide ids → freshly-allocated DB ids so
        # polygons can rewrite their landslide_id references.
        upload_to_real = {}
        insertable_landslide_cols = [c for c in ls_types
                                      if c not in LANDSLIDES_AUTO_COLS and c != 'id']

        for a in diff['landslides']['would_add']:
            upload_id = a['id']
            if upload_id in identical_to:
                continue  # identical re-import — handled as an update below
            feat  = ls_by_id[upload_id]
            props = feat.get('properties', {})
            cols, vals = [], []
            for col in insertable_landslide_cols:
                if col in props:
                    cols.append(col)
                    vals.append(_coerce(ls_types[col], props[col]))
            cols_csv = ', '.join(cols)
            placeholders = ', '.join(['%s'] * len(vals))
            cur.execute(
                f"INSERT INTO landslides ({cols_csv}) VALUES ({placeholders}) RETURNING id",
                vals,
            )
            real_id = cur.fetchone()[0]
            upload_to_real[upload_id] = real_id
            if subset_id is not None:
                cur.execute(
                    "INSERT INTO landslide_subsets (landslide_id, subset_id) "
                    "VALUES (%s, %s)",
                    (real_id, subset_id),
                )
            summary['landslides_inserted'] += 1
            affected_landslide_ids.add(real_id)

        # ---- Identical re-imports: UPDATE the matched existing record ----
        # The upload's polygons are essentially identical to this existing
        # record's, so it's the same landslide re-applied. Refresh its non-geom
        # columns from the upload (only columns the upload supplies; geometry is
        # unchanged so polygons are skipped below). The existing id/history/
        # review status are preserved.
        for upload_id, existing_id in identical_to.items():
            upload_to_real[upload_id] = existing_id   # so any new polys would map here
            feat  = ls_by_id.get(upload_id)
            props = (feat or {}).get('properties', {})
            sets, vals = [], []
            for col in insertable_landslide_cols:
                if col in props:
                    sets.append(f'{col} = %s')
                    vals.append(_coerce(ls_types[col], props[col]))
            if sets:
                vals.append(existing_id)
                cur.execute(f"UPDATE landslides SET {', '.join(sets)} WHERE id = %s", vals)
            if subset_id is not None:
                cur.execute(
                    "INSERT INTO landslide_subsets (landslide_id, subset_id) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (existing_id, subset_id),
                )
            summary['landslides_matched_updated'] += 1
            summary['matched_updates'].append((existing_id, props.get('unique_name')))
            affected_landslide_ids.add(existing_id)

        # ---- INSERT new polygons ----
        # Polygon landslide_id either points at an upload-side id we just
        # allocated above, or at a DB row that already existed at diff time.
        insertable_polygon_cols = [c for c in po_types
                                    if c not in ('id', 'geom', 'landslide_id')]
        for a in diff['landslide_polygons']['would_add']:
            feat  = po_by_id[a['id']]
            props = feat.get('properties', {})
            ls_ref = props.get('landslide_id')
            if ls_ref in identical_to:
                continue  # identical re-import — geometry already in DB, skip
            real_ls_id = upload_to_real.get(ls_ref, ls_ref)
            cols = ['landslide_id', 'geom']
            vals = [real_ls_id, json.dumps(feat['geometry'])]
            placeholders = ['%s', 'ST_GeomFromGeoJSON(%s)']
            for col in insertable_polygon_cols:
                if col in props:
                    cols.append(col)
                    placeholders.append('%s')
                    vals.append(_coerce(po_types[col], props[col]))
            cur.execute(
                f"INSERT INTO landslide_polygons ({', '.join(cols)}) "
                f"VALUES ({', '.join(placeholders)})",
                vals,
            )
            summary['polygons_inserted'] += 1
            affected_landslide_ids.add(real_ls_id)

        # Re-assert the is_primary convention (catastrophic→ONE source,
        # deposits never primary; slow→body) on every touched record —
        # uploads and draw-staged polygons may carry no is_primary at all,
        # or values that violate the manifest convention.
        from . import derived
        for ls_id in affected_landslide_ids:
            derived.normalize_primary(cur, ls_id)

        conn.commit()
    except ImportError_:
        conn.rollback()
        raise
    except Exception as exc:
        # Any DB-level failure (e.g. a unique/constraint violation we didn't
        # pre-check) → roll back fully and surface a clean message rather than a
        # 500. Nothing is saved.
        conn.rollback()
        raise ImportError_(f'Apply failed — no changes were saved. Database error:\n  {exc}') from exc
    finally:
        _put_conn(conn)

    for ls_id in affected_landslide_ids:
        LandslideEditMeta.objects.update_or_create(
            landslide_id=ls_id,
            defaults={'last_edited_by': user},
        )

    return summary

