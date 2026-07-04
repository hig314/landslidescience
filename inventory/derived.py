"""Derived/computed values for landslide records — authoritative implementations.

Each function in `RULES` is a pure transform from a row dict (one landslide
record, keyed by snake_case column name) to a computed value. The source code
of these functions is rendered verbatim at `/inventory/manage/rules/` so the
displayed rule and the executed rule cannot drift apart.

To add a rule:
  1. Write a function `compute_<column>(row) -> value`.
  2. Set `.target_column`, `.inputs`, and optionally `.summary` attributes.
  3. Register it in `RULES` below.

Workflow per rule (from `/inventory/manage/rules/<name>/`):
  - Preview: see every row where stored != computed, side by side.
  - Apply: UPDATE the column across all disagreeing rows in one transaction,
    invalidate caches, write an audit log entry per touched row.
"""

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Post-2012 catastrophic events get classified by their precursory-creep
# evidence (if any). Pre-2012 catastrophic events fall into Modern vs Holocene.
POST_2012_THRESHOLD_YEAR = 2012

# Rough end of the Little Ice Age in Alaska — divides "Modern" from "Holocene"
# for catastrophic events without finer dating.
LIA_END_YEAR = 1850


# ---------------------------------------------------------------------------
# Rule: insar_creep
# ---------------------------------------------------------------------------

def compute_insar_creep(row):
    """Tri-state OR-merge of the per-study InSAR creep flags.

    Preserves the distinction between "no study has flagged this" and
    "no study has assessed this":
      - True   if any of (insar_schaefer, insar_kim, insar_opera, insar_other)
               is explicitly True.
      - False  if at least one is explicitly False AND none is True
               (i.e., at least one study assessed it and saw no creep).
      - None   if all four are NULL (no study has assessed this site).
    """
    vals = [row.get(c) for c in
            ('insar_schaefer', 'insar_kim', 'insar_opera', 'insar_other')]
    if any(v is True for v in vals):
        return True
    if any(v is False for v in vals):
        return False
    return None

compute_insar_creep.target_table  = 'landslides'
compute_insar_creep.target_column = 'insar_creep'
compute_insar_creep.inputs        = ('insar_schaefer', 'insar_kim',
                                     'insar_opera', 'insar_other')
compute_insar_creep.summary       = ('Tri-state OR-merge of the per-study '
                                     'InSAR creep flags (preserves NULL='
                                     '"not yet assessed").')


# ---------------------------------------------------------------------------
# Rule: creep_behavior
# ---------------------------------------------------------------------------

def compute_creep_behavior(row):
    """The creep-evidence label observed for this landslide.

    The two Planet flags work as a pair, not a simple severity ranking:
      - `planet_labs_creep` says "creep is apparent in Planet imagery"
      - `planet_labs_patchy_creep` is a refinement: "the creep is patchy,
        not uniform across the landslide"

    Conceptually, "obvious creep" is the broader / stronger assertion
    (the whole feature is moving). The patchy flag, when set, *overrides*
    that interpretation to say "actually it's only patchy" — so in the
    compositional rule the patchy flag wins. The order below reflects
    that override semantics:

      1. planet_labs_patchy_creep   → 'Patchy obvious creep'
         (more specific call; supersedes plain obvious when both are set)
      2. planet_labs_creep          → 'Obvious creep'
      3. insar_creep                → 'Subtle creep'
      4. other_subtle_creep is set  → 'Subtle creep'
      5. geomorph_creep             → 'Geomorph creep'
      6. creep_evaluated is TRUE    → 'Cryptic'
         (record was reviewed for precursors and none were found — a
         deliberate null finding, distinct from records where
         creep_evaluated is FALSE which simply means no review yet)
      7. otherwise                  → None

    Feeds into `landslide_class`, which prefixes the label with 'Slow '
    or 'Catastrophic '. The `creep_evaluated` boolean is human-set in
    the data-entry UI and is not itself a rule output.
    """
    if row.get('planet_labs_patchy_creep'):
        return 'Patchy obvious creep'
    if row.get('planet_labs_creep'):
        return 'Obvious creep'
    if row.get('insar_creep'):
        return 'Subtle creep'
    if (row.get('other_subtle_creep') or '').strip():
        return 'Subtle creep'
    if row.get('geomorph_creep'):
        return 'Geomorph creep'
    if row.get('creep_evaluated'):
        return 'Cryptic'
    return None

compute_creep_behavior.target_table  = 'landslides'
compute_creep_behavior.target_column = 'creep_behavior'
compute_creep_behavior.inputs        = ('planet_labs_patchy_creep',
                                        'planet_labs_creep',
                                        'insar_creep',
                                        'other_subtle_creep',
                                        'geomorph_creep',
                                        'creep_evaluated')
compute_creep_behavior.summary       = ('Creep-evidence label. Patchy '
                                        'overrides plain obvious when '
                                        'both Planet flags are set; '
                                        '"Cryptic" when creep_evaluated=TRUE '
                                        'with no positive evidence.')


# ---------------------------------------------------------------------------
# Helpers for landslide_class
# ---------------------------------------------------------------------------

def _resolve_event_era(row):
    """Resolve the temporal era of this landslide, in priority order.

    Returns one of:
      - int  (a 4-digit year)         → caller bucket s into 2012+/Modern/Holocene
      - 'Holocene' or 'Modern'        → explicit era token from year_text
      - None                          → can't determine; caller should leave as-is

    Priority (most authoritative first):
      1. seismic_datetime              → most precise (timestamp of failure)
      2. year_text == 4-digit year     → human-recorded numeric year
      3. year_text == 'Holocene'/'Modern' (case-insensitive substring)
                                       → explicit era for prehistoric / pre-record events
      4. date_min                      → fallback; may be a *recording* date
                                         rather than an event date, so it ranks
                                         below explicit era tokens.
    """
    sd = row.get('seismic_datetime')
    if sd is not None:
        return sd.year if hasattr(sd, 'year') else int(str(sd)[:4])
    yt = (row.get('year_text') or '').strip()
    if yt.isdigit() and len(yt) == 4:
        return int(yt)
    yt_l = yt.lower()
    if 'holocene' in yt_l:
        return 'Holocene'
    if 'modern' in yt_l:
        return 'Modern'
    dm = row.get('date_min')
    if dm is not None:
        return dm.year if hasattr(dm, 'year') else int(str(dm)[:4])
    return None


# ---------------------------------------------------------------------------
# Rule: landslide_class
# ---------------------------------------------------------------------------

def compute_landslide_class(row):
    """Categorical class label for this landslide.

    Decision tree (mirrors the inventory legend / map color scheme):

    For SLOW landslides (`landslide_type='slow'`):
      - If `size_inclusion` is False → "Small slow landslide"
      - Else → "Slow " + creep_behavior, e.g. "Slow Obvious creep"
      - If creep_behavior is null → None (needs review)

    For CATASTROPHIC landslides (`landslide_type='catastrophic'`):
      - If `size_inclusion` is False → "Small catastrophic landslide"
      - Else look at year:
        · 2012+ → "Catastrophic " + creep_behavior if any precursor creep is
          recorded, else just "Catastrophic"
        · 1850-2011 → "Catastrophic Modern"
        · before 1850 → "Catastrophic Holocene"
      - If no numeric year but year_text contains "Holocene" or "Modern",
        use that as the era hint.
      - If neither year nor era hint is resolvable → None (needs review)
    """
    t = (row.get('landslide_type') or '').strip().lower()

    if t == 'slow':
        if not row.get('size_inclusion'):
            return 'Small slow landslide'
        cb = (row.get('creep_behavior') or '').strip()
        return f'Slow {cb}' if cb else None

    if t == 'catastrophic':
        if not row.get('size_inclusion'):
            return 'Small catastrophic landslide'
        era = _resolve_event_era(row)
        if era is None:
            return None
        if era == 'Holocene': return 'Catastrophic Holocene'
        if era == 'Modern':   return 'Catastrophic Modern'
        # era is a numeric year
        if era >= POST_2012_THRESHOLD_YEAR:
            cb = (row.get('creep_behavior') or '').strip()
            return f'Catastrophic {cb}' if cb else 'Catastrophic'
        if era >= LIA_END_YEAR:
            return 'Catastrophic Modern'
        return 'Catastrophic Holocene'

    return None

compute_landslide_class.target_table  = 'landslides'
compute_landslide_class.target_column = 'landslide_class'
compute_landslide_class.inputs        = ('landslide_type', 'size_inclusion',
                                         'creep_behavior', 'seismic_datetime',
                                         'year_text', 'date_min')
compute_landslide_class.summary       = ('Categorical class derived from type, '
                                         'size, creep evidence, and event year.')


# ---------------------------------------------------------------------------
# Size thresholds for size_inclusion
# ---------------------------------------------------------------------------

SLOW_BODY_AREA_THRESHOLD_M2     = 50_000
CAT_SOURCE_AREA_THRESHOLD_M2    = 50_000
CAT_DEPOSIT_AREA_THRESHOLD_M2   = 500_000


# ---------------------------------------------------------------------------
# Rule: polygon_area (SQL — runs in Postgres in EPSG:3338)
# ---------------------------------------------------------------------------

def compute_polygon_area():
    """Per-polygon area in EPSG:3338 (NAD83 / Alaska Albers, equal-area).

    Computed via PostGIS `ST_Area(ST_Transform(geom, 3338))` for accuracy
    at high latitudes — Mercator-based areas can be off by 4-6× at Alaskan
    latitudes. Rounded to whole square meters so re-applies are idempotent.

    Writes to landslide_polygons.area; consumed by area_body / area_source /
    area_deposit (which sum these by role).
    """
    return """
        SELECT id,
               ROUND(ST_Area(ST_Transform(geom, 3338))::numeric, 0)::float8 AS computed
        FROM landslide_polygons
        ORDER BY id
    """

compute_polygon_area.is_sql        = True
compute_polygon_area.target_table  = 'landslide_polygons'
compute_polygon_area.target_column = 'area'
compute_polygon_area.inputs        = ('geom',)
compute_polygon_area.summary       = ('Polygon area in EPSG:3338 / Alaska '
                                      'Albers (square meters).')


# ---------------------------------------------------------------------------
# Rules: aggregate polygon areas onto landslides (one per role)
# ---------------------------------------------------------------------------

def compute_area_body():
    """Sum of body-polygon areas for this landslide (NULL for catastrophic).

    Aggregates `landslide_polygons.area` (which `polygon_area` populates) for
    rows where role='body'. Slow landslides typically have one body polygon
    but the SUM handles multi-polygon cases too.
    """
    return """
        SELECT l.id,
               SUM(p.area) FILTER (WHERE p.role = 'body')::float8 AS computed
        FROM landslides l
        LEFT JOIN landslide_polygons p ON p.landslide_id = l.id
        GROUP BY l.id
        ORDER BY l.id
    """

compute_area_body.is_sql        = True
compute_area_body.target_table  = 'landslides'
compute_area_body.target_column = 'area_body'
compute_area_body.inputs        = ('landslide_polygons.area', 'landslide_polygons.role')
compute_area_body.summary       = 'Sum of body-polygon areas.'


def compute_area_source():
    """Sum of source-polygon areas for this landslide (NULL for slow).

    Aggregates `landslide_polygons.area` for rows where role='source'.
    For multi-source catastrophic landslides the SUM is what size_inclusion
    considers.
    """
    return """
        SELECT l.id,
               SUM(p.area) FILTER (WHERE p.role = 'source')::float8 AS computed
        FROM landslides l
        LEFT JOIN landslide_polygons p ON p.landslide_id = l.id
        GROUP BY l.id
        ORDER BY l.id
    """

compute_area_source.is_sql        = True
compute_area_source.target_table  = 'landslides'
compute_area_source.target_column = 'area_source'
compute_area_source.inputs        = ('landslide_polygons.area', 'landslide_polygons.role')
compute_area_source.summary       = 'Sum of source-polygon areas.'


def compute_area_deposit():
    """Sum of deposit-polygon areas for this landslide (NULL for slow).

    Aggregates `landslide_polygons.area` for rows where role='deposit'.
    """
    return """
        SELECT l.id,
               SUM(p.area) FILTER (WHERE p.role = 'deposit')::float8 AS computed
        FROM landslides l
        LEFT JOIN landslide_polygons p ON p.landslide_id = l.id
        GROUP BY l.id
        ORDER BY l.id
    """

compute_area_deposit.is_sql        = True
compute_area_deposit.target_table  = 'landslides'
compute_area_deposit.target_column = 'area_deposit'
compute_area_deposit.inputs        = ('landslide_polygons.area', 'landslide_polygons.role')
compute_area_deposit.summary       = 'Sum of deposit-polygon areas.'


# ---------------------------------------------------------------------------
# Rule: size_inclusion
# ---------------------------------------------------------------------------

def compute_size_inclusion(row):
    """True if this landslide meets the inventory's size threshold.

    Thresholds (in m², applied to the role-summed polygon areas):
      - Slow:         area_body   ≥ 50,000  m²
      - Catastrophic: area_source ≥ 50,000  m²  OR
                      area_deposit ≥ 500,000 m²
      - Other types:  None (under-review / unclassified)

    Depends on `area_body` / `area_source` / `area_deposit` being populated
    (apply the area rules first; they in turn depend on polygon_area).
    """
    t = (row.get('landslide_type') or '').strip().lower()
    if t == 'slow':
        ab = row.get('area_body') or 0
        return ab >= SLOW_BODY_AREA_THRESHOLD_M2
    if t == 'catastrophic':
        a_src = row.get('area_source')  or 0
        a_dep = row.get('area_deposit') or 0
        return (a_src >= CAT_SOURCE_AREA_THRESHOLD_M2
                or a_dep >= CAT_DEPOSIT_AREA_THRESHOLD_M2)
    return None

compute_size_inclusion.target_table  = 'landslides'
compute_size_inclusion.target_column = 'size_inclusion'
compute_size_inclusion.inputs        = ('landslide_type', 'area_body',
                                        'area_source', 'area_deposit')
compute_size_inclusion.summary       = ('Boolean: does this landslide meet '
                                        'the size threshold for inclusion in '
                                        'the main inventory.')


# ---------------------------------------------------------------------------
# Volume rules — per-polygon estimate, role aggregates, landslide-level
# estimate, and the site-specific-vs-estimate preference.
# ---------------------------------------------------------------------------

# Slow + catastrophic source: V = SLOW_VOLUME_COEFF * Area^SLOW_VOLUME_EXPONENT
# (Hovius-style empirical scaling of source volume with planform area.)
SLOW_VOLUME_COEFF              = 0.1
SLOW_VOLUME_EXPONENT           = 1.5

# Creeping permafrost mass: assumed uniform thickness regardless of role.
PERMAFROST_THICKNESS_M         = 20

# Catastrophic deposit: minimum thickness floor (deposits are spread thin).
CAT_DEPOSIT_THICKNESS_M        = 2


def compute_polygon_volume():
    """Per-polygon estimated volume (m³).

    Branches on parent landslide + polygon role:
      - creeping_permafrost_mass=True   →  PERMAFROST_THICKNESS_M × area
                                           (uniform thickness regardless of role)
      - landslide_type='slow', role='body'           →  0.1 × area^1.5
      - landslide_type='catastrophic', role='source' →  0.1 × area^1.5
      - landslide_type='catastrophic', role='deposit' →  CAT_DEPOSIT_THICKNESS_M
                                                          × area
      - anything else                                →  NULL

    Multi-source catastrophic landslides get each source polygon estimated
    independently; the per-role SUMs are taken in the volume_source/deposit
    rules below.
    """
    return """
        SELECT p.id,
               CASE
                 WHEN l.creeping_permafrost_mass THEN
                   ROUND(20 * p.area)::bigint
                 WHEN l.landslide_type = 'slow' AND p.role = 'body' THEN
                   ROUND(0.1 * POWER(p.area, 1.5))::bigint
                 WHEN l.landslide_type = 'catastrophic' AND p.role = 'source' THEN
                   ROUND(0.1 * POWER(p.area, 1.5))::bigint
                 WHEN l.landslide_type = 'catastrophic' AND p.role = 'deposit' THEN
                   ROUND(2 * p.area)::bigint
                 ELSE NULL
               END AS computed
        FROM landslide_polygons p
        JOIN landslides l ON l.id = p.landslide_id
        ORDER BY p.id
    """

compute_polygon_volume.is_sql        = True
compute_polygon_volume.target_table  = 'landslide_polygons'
compute_polygon_volume.target_column = 'polygon_volume'
compute_polygon_volume.inputs        = ('landslides.creeping_permafrost_mass',
                                        'landslides.landslide_type',
                                        'landslide_polygons.role',
                                        'landslide_polygons.area')
compute_polygon_volume.summary       = ('Per-polygon volume estimate via role-'
                                        'aware thickness × area formulas.')


def compute_volume_body():
    """Sum of body-polygon volume estimates for this landslide."""
    return """
        SELECT l.id,
               SUM(p.polygon_volume) FILTER (WHERE p.role = 'body')::bigint AS computed
        FROM landslides l
        LEFT JOIN landslide_polygons p ON p.landslide_id = l.id
        GROUP BY l.id
        ORDER BY l.id
    """

compute_volume_body.is_sql        = True
compute_volume_body.target_table  = 'landslides'
compute_volume_body.target_column = 'volume_body'
compute_volume_body.inputs        = ('landslide_polygons.polygon_volume',
                                     'landslide_polygons.role')
compute_volume_body.summary       = 'Sum of body-polygon volume estimates.'


def compute_volume_source():
    """Sum of source-polygon volume estimates for this landslide."""
    return """
        SELECT l.id,
               SUM(p.polygon_volume) FILTER (WHERE p.role = 'source')::bigint AS computed
        FROM landslides l
        LEFT JOIN landslide_polygons p ON p.landslide_id = l.id
        GROUP BY l.id
        ORDER BY l.id
    """

compute_volume_source.is_sql        = True
compute_volume_source.target_table  = 'landslides'
compute_volume_source.target_column = 'volume_source'
compute_volume_source.inputs        = ('landslide_polygons.polygon_volume',
                                       'landslide_polygons.role')
compute_volume_source.summary       = 'Sum of source-polygon volume estimates.'


def compute_volume_deposit():
    """Sum of deposit-polygon volume estimates for this landslide."""
    return """
        SELECT l.id,
               SUM(p.polygon_volume) FILTER (WHERE p.role = 'deposit')::bigint AS computed
        FROM landslides l
        LEFT JOIN landslide_polygons p ON p.landslide_id = l.id
        GROUP BY l.id
        ORDER BY l.id
    """

compute_volume_deposit.is_sql        = True
compute_volume_deposit.target_table  = 'landslides'
compute_volume_deposit.target_column = 'volume_deposit'
compute_volume_deposit.inputs        = ('landslide_polygons.polygon_volume',
                                        'landslide_polygons.role')
compute_volume_deposit.summary       = 'Sum of deposit-polygon volume estimates.'


def compute_volume_estimated(row):
    """Landslide-level estimated volume (m³).

    For slow landslides:        volume_body
    For catastrophic landslides: max(volume_source, volume_deposit)
                                  — source vs deposit can disagree because of
                                    deposit run-out / entrainment / removed
                                    material; the larger is the more defensible
                                    estimate for "size of the failure."
    For other / unclassified types: NULL.

    Depends on `volume_body` / `volume_source` / `volume_deposit` being
    populated; those in turn depend on `polygon_volume`.
    """
    t = (row.get('landslide_type') or '').strip().lower()
    if t == 'slow':
        return row.get('volume_body')
    if t == 'catastrophic':
        v_src = row.get('volume_source')  or 0
        v_dep = row.get('volume_deposit') or 0
        v = max(v_src, v_dep)
        return v if v > 0 else None
    return None

compute_volume_estimated.target_table  = 'landslides'
compute_volume_estimated.target_column = 'volume_estimated'
compute_volume_estimated.inputs        = ('landslide_type', 'volume_body',
                                          'volume_source', 'volume_deposit')
compute_volume_estimated.summary       = ('Landslide-level estimate composed '
                                          'from the role-summed volumes.')


# ---------------------------------------------------------------------------
# Centroid rules — primary-polygon centroid in EPSG:3338 (Alaska Albers,
# equal-area) and WGS84 (lat/lon). The "primary polygon" selection mirrors
# what the inventory map uses for dot placement: slow → body, catastrophic
# → source first, then deposit.
# ---------------------------------------------------------------------------

# Shared LATERAL JOIN that picks one polygon per landslide. Inlined into each
# centroid rule via a Python helper so the SQL stays self-contained in the
# function body (visible at /inventory/manage/rules/<name>/).
_CENTROID_LATERAL = """
    FROM landslides l
    LEFT JOIN LATERAL (
        SELECT ST_Centroid(ST_Transform(lp.geom, 3338)) AS albers
        FROM landslide_polygons lp
        WHERE lp.landslide_id = l.id
          AND ((l.landslide_type = 'catastrophic' AND lp.role IN ('source','deposit'))
               OR (l.landslide_type = 'slow' AND lp.role = 'body'))
        ORDER BY
            CASE lp.role WHEN 'source' THEN 0 WHEN 'body' THEN 0 ELSE 1 END,
            lp.is_primary DESC NULLS LAST,
            lp.id
        LIMIT 1
    ) c ON TRUE
"""


def compute_centroid_albers_x():
    """Centroid easting in EPSG:3338 (NAD83 / Alaska Albers, meters).

    Rounded to whole meters. The primary polygon is selected via the shared
    LATERAL: slow → body; catastrophic → source first, fallback deposit. The
    centroid is computed in Albers so it's a true geometric center (Mercator
    centroids drift at Alaskan latitudes).
    """
    return """
        SELECT l.id, ROUND(ST_X(c.albers)::numeric)::bigint AS computed
    """ + _CENTROID_LATERAL + " ORDER BY l.id"

compute_centroid_albers_x.is_sql        = True
compute_centroid_albers_x.target_table  = 'landslides'
compute_centroid_albers_x.target_column = 'centroid_albers_x'
compute_centroid_albers_x.inputs        = ('landslide_polygons.geom',
                                           'landslide_polygons.role',
                                           'landslides.landslide_type')
compute_centroid_albers_x.summary       = ('Primary-polygon centroid X in '
                                           'EPSG:3338, whole meters.')


def compute_centroid_albers_y():
    """Centroid northing in EPSG:3338 (NAD83 / Alaska Albers, meters)."""
    return """
        SELECT l.id, ROUND(ST_Y(c.albers)::numeric)::bigint AS computed
    """ + _CENTROID_LATERAL + " ORDER BY l.id"

compute_centroid_albers_y.is_sql        = True
compute_centroid_albers_y.target_table  = 'landslides'
compute_centroid_albers_y.target_column = 'centroid_albers_y'
compute_centroid_albers_y.inputs        = ('landslide_polygons.geom',
                                           'landslide_polygons.role',
                                           'landslides.landslide_type')
compute_centroid_albers_y.summary       = ('Primary-polygon centroid Y in '
                                           'EPSG:3338, whole meters.')


def compute_centroid_lat():
    """Centroid latitude in WGS84 (decimal degrees, 6 d.p. ≈ 11 cm)."""
    return """
        SELECT l.id, ROUND(ST_Y(ST_Transform(c.albers, 4326))::numeric, 6)::float8 AS computed
    """ + _CENTROID_LATERAL + " ORDER BY l.id"

compute_centroid_lat.is_sql        = True
compute_centroid_lat.target_table  = 'landslides'
compute_centroid_lat.target_column = 'centroid_lat'
compute_centroid_lat.inputs        = ('landslide_polygons.geom',
                                      'landslide_polygons.role',
                                      'landslides.landslide_type')
compute_centroid_lat.summary       = 'Primary-polygon centroid latitude (WGS84).'


def compute_centroid_lon():
    """Centroid longitude in WGS84 (decimal degrees, 6 d.p. ≈ 11 cm)."""
    return """
        SELECT l.id, ROUND(ST_X(ST_Transform(c.albers, 4326))::numeric, 6)::float8 AS computed
    """ + _CENTROID_LATERAL + " ORDER BY l.id"

compute_centroid_lon.is_sql        = True
compute_centroid_lon.target_table  = 'landslides'
compute_centroid_lon.target_column = 'centroid_lon'
compute_centroid_lon.inputs        = ('landslide_polygons.geom',
                                      'landslide_polygons.role',
                                      'landslides.landslide_type')
compute_centroid_lon.summary       = 'Primary-polygon centroid longitude (WGS84).'


def compute_volume_preferred(row):
    """The single best volume to report for this landslide.

    If `volume_site_specific` is set (a manually-entered, independently
    calculated value from a field study or paper), use it. Otherwise fall
    back to the rule-driven `volume_estimated`.

    This is what api_features serves to the map and what downstream consumers
    should treat as the canonical volume.
    """
    vss = row.get('volume_site_specific')
    if vss is not None:
        return vss
    return row.get('volume_estimated')

compute_volume_preferred.target_table  = 'landslides'
compute_volume_preferred.target_column = 'volume_preferred'
compute_volume_preferred.inputs        = ('volume_site_specific',
                                          'volume_estimated')
compute_volume_preferred.summary       = ('Site-specific volume if available, '
                                          'else the estimate.')


# Canonical label written by the volume_method rule when the row is using
# the automated estimate (i.e. has no manual volume_site_specific value).
AUTOMATED_VOLUME_METHOD_LABEL = 'Automated estimate'


def compute_volume_method(row):
    """Standardize volume_method for rows using the automated estimate.

    - If `volume_site_specific` is set, this row was sized by a manual /
      site-specific approach. Keep whatever volume_method text is there
      (typically a one-off description: a paper citation, a DEM-differencing
      note, an exposure measurement, etc.). The rule returns the stored
      value unchanged, so those rows never disagree.
    - Otherwise the row is using the rule-driven estimate. Force a single
      canonical label `AUTOMATED_VOLUME_METHOD_LABEL` so the auto-estimated
      cohort is easy to count and filter.
    """
    if row.get('volume_site_specific') is not None:
        return row.get('volume_method')
    return AUTOMATED_VOLUME_METHOD_LABEL

compute_volume_method.target_table  = 'landslides'
compute_volume_method.target_column = 'volume_method'
compute_volume_method.inputs        = ('volume_site_specific', 'volume_method')
compute_volume_method.summary       = ('Canonical label for auto-estimated '
                                       'rows; pass-through for site-specific.')


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
#
# Order matters: rules later in the list may depend on earlier ones being
# applied first (e.g. size_inclusion depends on area_body/area_source/
# area_deposit, which depend on polygon_area). The /inventory/manage/rules/
# page lists them in this order so an editor can click through top-to-bottom.

RULES = {
    # Geometry → per-polygon area
    'polygon_area':       compute_polygon_area,
    # Per-polygon area → landslide role aggregates
    'area_body':          compute_area_body,
    'area_source':        compute_area_source,
    'area_deposit':       compute_area_deposit,
    # Size threshold → boolean inclusion flag
    'size_inclusion':     compute_size_inclusion,
    # Geometry → primary-polygon centroid (Albers + WGS84)
    'centroid_albers_x':  compute_centroid_albers_x,
    'centroid_albers_y':  compute_centroid_albers_y,
    'centroid_lat':       compute_centroid_lat,
    'centroid_lon':       compute_centroid_lon,
    # Per-polygon volume (uses area + type + creeping_permafrost + role)
    'polygon_volume':     compute_polygon_volume,
    # Per-polygon volume → landslide role aggregates
    'volume_body':        compute_volume_body,
    'volume_source':      compute_volume_source,
    'volume_deposit':     compute_volume_deposit,
    # Per-role volumes → single landslide estimate
    'volume_estimated':   compute_volume_estimated,
    # Site-specific override → final preferred volume
    'volume_preferred':   compute_volume_preferred,
    # Standardize the volume_method label for auto-estimated rows
    'volume_method':      compute_volume_method,
    # Creep evidence chain: roll up specific InSAR flags, then pick the
    # creep_behavior label, then derive the categorical landslide_class.
    'insar_creep':        compute_insar_creep,
    'creep_behavior':     compute_creep_behavior,
    'landslide_class':    compute_landslide_class,
}


# ---------------------------------------------------------------------------
# Diff helper — generalized over Python vs SQL rules + target table.
# ---------------------------------------------------------------------------

def _normalize(v):
    """Treat empty string as None so '' vs NULL doesn't register as a change."""
    return None if v == '' else v


def _equal(old, new, column=None):
    """Equality with a tolerance appropriate to the column's units.

    Centroid columns are compared as a true distance in METERS (lat/lon degrees
    scaled to meters), never as a flat degree tolerance — see _CENTROID_COLS.
    Everything else keeps the 1.0 tolerance, which covers float-rounding noise
    in areas (stored as float8 m², rounded to whole m² in the SQL)."""
    old, new = _normalize(old), _normalize(new)
    if old is None and new is None:
        return True
    if old is None or new is None:
        return False
    if column in _CENTROID_COLS:
        scale = _M_PER_DEG_LAT if column in _CENTROID_DEG_COLS else 1.0  # Albers already m
        return abs(float(old) - float(new)) * scale <= _CENTROID_TOLERANCE_M
    if isinstance(old, float) or isinstance(new, float):
        return abs(float(old) - float(new)) <= 1.0
    return old == new


def _fmt_value(v):
    """Display formatting for a cell value: thousands separators for numbers,
    '(NULL)' for None, plain repr for strings/booleans. Raw values are kept
    alongside in the change dict — only display uses this.
    """
    if v is None:
        return '(NULL)'
    if isinstance(v, bool):
        return 'True' if v else 'False'
    if isinstance(v, int):
        return f'{v:,}'
    if isinstance(v, float):
        if v == int(v):
            return f'{int(v):,}'
        return f'{v:,.2f}'
    return str(v)


def _pct_diff_str(old, new):
    """Signed % difference, formatted for the change-table column.

    Returns '' when the comparison is non-numeric (booleans, strings) or when
    old is None/0 (no defined denominator). Format scales with magnitude so
    tiny diffs don't render as a misleading '0.00%'.
    """
    old, new = _normalize(old), _normalize(new)
    if old is None or new is None:
        return ''
    if isinstance(old, bool) or isinstance(new, bool):
        return ''
    if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
        return ''
    if old == 0:
        return ''
    p = (new - old) / abs(old) * 100
    if abs(p) < 0.01:
        return '≈0%'
    sign = '+' if p > 0 else ''
    if abs(p) < 1:
        return f'{sign}{p:.3f}%'
    if abs(p) < 100:
        return f'{sign}{p:.2f}%'
    return f'{sign}{p:.1f}%'


def _label_select(target_table):
    """SQL fragment + alias for showing a human-readable label per row."""
    if target_table == 'landslides':
        return 't.unique_name', ''
    if target_table == 'landslide_polygons':
        # e.g. "Yale D (body)"  — join to landslides for the parent name
        return ("l.unique_name || ' (' || t.role || ')'",
                'JOIN landslides l ON l.id = t.landslide_id')
    return 'NULL', ''


# Centroid columns are positions, so equality must be judged as a real ground
# DISTANCE in meters — never a flat tolerance on raw degrees. A degree of
# longitude is only ~0.45 of a degree of latitude in meters at 63°N, and the
# generic 1.0 tolerance (meant for areas in m²) treated centroids up to ~1°
# (~100 km) apart as "equal" — so a stored lat/lon that was wrong by a fraction
# of a degree was never corrected, and could sit inconsistent with the Albers
# columns (the mixed-axis centroid that corrupted merged-record 1446). We
# instead convert each centroid column's difference to meters and threshold
# that. lat/lon use meters-per-degree (a conservative equator scale for lon, so
# a real move is never masked); Albers X/Y are already meters.
_CENTROID_COLS = ('centroid_albers_x', 'centroid_albers_y',
                  'centroid_lat', 'centroid_lon')
_CENTROID_DEG_COLS = ('centroid_lat', 'centroid_lon')
_M_PER_DEG_LAT = 111320.0          # meters per degree of latitude (and an upper
                                   # bound for longitude → conservative for lon)
_CENTROID_TOLERANCE_M = 0.5        # two centroids within 0.5 m are the same point


def diff_against_db(cur, rule_name):
    """Compute proposed vs stored values for every row in the rule's target table.

    Returns:
        {
          'agreements': int,
          'changes':    [{'id': N, 'unique_name': str, 'old': v, 'new': v}, ...],
        }
    """
    fn = RULES[rule_name]
    target_table  = getattr(fn, 'target_table', 'landslides')
    target_column = fn.target_column
    label_expr, label_join = _label_select(target_table)

    if getattr(fn, 'is_sql', False):
        # SQL rule: run its query, map id → computed value.
        cur.execute(fn())
        computed = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute(f"""
            SELECT t.id, t.{target_column}, {label_expr}
            FROM {target_table} t {label_join}
            ORDER BY t.id
        """)
        agreements, changes = 0, []
        for row_id, old, label in cur.fetchall():
            new = computed.get(row_id)
            if _equal(old, new, target_column):
                agreements += 1
            else:
                old_n, new_n = _normalize(old), _normalize(new)
                changes.append({
                    'id': row_id,
                    'unique_name': str(label) if label is not None else '',
                    'old': old_n,
                    'new': new_n,
                    'old_fmt': _fmt_value(old_n),
                    'new_fmt': _fmt_value(new_n),
                    'pct_diff': _pct_diff_str(old, new),
                })
        return {'agreements': agreements, 'changes': changes}

    # Python rule.
    cols = sorted(set(fn.inputs) | {target_column, 'id'})
    cur.execute(f"""
        SELECT {', '.join('t.' + c for c in cols)}, {label_expr}
        FROM {target_table} t {label_join}
        ORDER BY t.id
    """)
    rows = cur.fetchall()
    agreements, changes = 0, []
    for raw in rows:
        row = dict(zip(cols, raw[:-1]))
        label = raw[-1]
        new = fn(row)
        if _equal(row[target_column], new, target_column):
            agreements += 1
        else:
            old_n, new_n = _normalize(row[target_column]), _normalize(new)
            changes.append({
                'id': row['id'],
                'unique_name': str(label) if label is not None else '',
                'old': old_n,
                'new': new_n,
                'old_fmt': _fmt_value(old_n),
                'new_fmt': _fmt_value(new_n),
                'pct_diff': _pct_diff_str(row[target_column], new),
            })
    return {'agreements': agreements, 'changes': changes}


def normalize_primary(cur, ls_id):
    """Enforce the is_primary convention on one landslide's polygons
    (manifest `polygon_conventions.is_primary`): slow → the body polygon is
    primary; catastrophic → exactly ONE source polygon is primary and
    deposits are never primary; a catastrophic with no source has NO primary
    (the centroid LATERAL falls back to the deposit by role order).

    Called from every *automatic* geometry-entry path (draw create + attach,
    edit-map polygon save, file import) — NOT from the review form's explicit
    per-row primary radio, which stays the manual escape hatch. When several
    polygons of the primary role exist, an already-flagged one is kept, else
    the lowest id wins (matches the centroid LATERAL's `is_primary DESC, id`
    ordering, so normalization never moves an existing centroid).

    Returns True if any row changed. The caller owns the transaction and is
    expected to run the rule cascade afterwards.
    """
    cur.execute("SELECT landslide_type FROM landslides WHERE id = %s", (ls_id,))
    row = cur.fetchone()
    if not row:
        return False
    primary_role = 'source' if row[0] == 'catastrophic' else 'body'
    cur.execute("SELECT id, role, is_primary FROM landslide_polygons "
                "WHERE landslide_id = %s ORDER BY is_primary DESC NULLS LAST, id",
                (ls_id,))
    polys = cur.fetchall()
    candidates = [p for p in polys if p[1] == primary_role]
    # Desired state: the first candidate (kept-if-already-primary, else lowest
    # id) is primary; everything else is not. No candidates → nothing primary.
    keep_id = candidates[0][0] if candidates else None
    changed = False
    for pid, _role, is_primary in polys:
        want = (pid == keep_id)
        if bool(is_primary) != want:
            cur.execute("UPDATE landslide_polygons SET is_primary = %s WHERE id = %s",
                        (want, pid))
            changed = True
    return changed


def apply_rules_for_landslide(cur, ls_id):
    """Apply every derived RULE to ONE landslide (and its polygons), in
    dependency order (the RULES insertion order: polygon geometry → areas →
    centroids → volumes → class). Used at induction (review-save) so a newly
    inserted record gets the same computed columns the batch rule-apply would
    produce.

    Reuses ``diff_against_db`` per rule (identical compute/normalize as the
    batch ``manage_rule_apply``) and applies only the changes that touch this
    landslide's rows. Each rule re-reads the DB, so later rules see earlier
    rules' writes within the same transaction. The caller owns the transaction
    (commit/rollback). Returns the number of column writes.
    """
    cur.execute("SELECT id FROM landslide_polygons WHERE landslide_id = %s", (ls_id,))
    poly_ids = {r[0] for r in cur.fetchall()}

    writes = 0
    for name, fn in RULES.items():
        target_table = getattr(fn, 'target_table', 'landslides')
        target_column = fn.target_column
        result = diff_against_db(cur, name)
        for ch in result['changes']:
            rid = ch['id']
            if target_table == 'landslides' and rid != ls_id:
                continue
            if target_table == 'landslide_polygons' and rid not in poly_ids:
                continue
            cur.execute(
                f"UPDATE {target_table} SET {target_column} = %s WHERE id = %s",
                (ch['new'], rid),
            )
            writes += 1
    return writes
