"""Generate QGIS .qml style files for the inventory layers.

Colors match the inventory map exactly — sourced from `map_settings` at
export time so admin customizations propagate to the styles. Two layers:

  build_qml_points(settings)
      Categorized renderer keyed on `landslide_class`. Catastrophic-with-
      precursory-creep classes get a colored halo (outline) matching the
      precursor color. Slow classes get a thin gray outline; others none.

  build_qml_polygons(settings)
      Rule-based renderer. One rule per landslide_class with the matching
      fill color (and a gray outline), plus an ELSE catch-all. No role-
      based override, so source/deposit polygons get the same per-class
      color as their parent. (Requires the `landslide_class` column to be
      present — true for the flat polygon export, and true for the
      normalized export once joined to landslides in QGIS with no prefix.)

The output is QGIS 3.x QML using the modern Option-based property style.
"""

# ---------------------------------------------------------------------------
# Class color mapping — mirrors inventory/static/inventory/js/map.js and
# inventory/views.py _CLASS_COLOR.
# Keys: landslide_class string from the DB.
# Values: ('fill_setting_key', 'halo_setting_key' | None) — looked up from
# map_settings to allow admin overrides.
# ---------------------------------------------------------------------------
# Order matches the inventory legend nav (home.html / views.py group orders):
#   Slow active → Slow other → Catastrophic recent (since 2012) → Catastrophic other
_CLASS_FILL_KEYS = {
    # Slow — active
    'Slow Obvious creep':                ('color_obvious',  None),
    'Slow Patchy obvious creep':         ('color_obvious',  None),
    # Slow — other
    'Slow Subtle creep':                 ('color_subtle',   None),
    'Slow Geomorph creep':               ('color_geomorph', None),
    'Small slow landslide':              ('color_geomorph', None),
    # Large catastrophic since 2012 (precursory creep variants, then plain)
    'Catastrophic Obvious creep':        ('color_cat',      'color_obvious'),
    'Catastrophic Patchy obvious creep': ('color_cat',      'color_obvious'),
    'Catastrophic Subtle creep':         ('color_cat',      'color_subtle'),
    'Catastrophic Geomorph creep':       ('color_cat',      'color_geomorph'),
    'Catastrophic Cryptic':              ('color_cat',      None),
    # Other catastrophic landslides (pale-blue dot — see _PALE_BLUE)
    'Catastrophic Modern':               (None,             None),
    'Catastrophic Holocene':             (None,             None),
    'Small catastrophic landslide':      (None,             None),
}
_PALE_BLUE = '#96b8df'

_DEFAULTS = {
    'color_geomorph': '#d3e9cf',
    'color_subtle':   '#faf075',
    'color_obvious':  '#f69fa1',
    'color_cat':      '#3f67b1',
    'stroke_color':   '#a1a1a1',
    'fill_opacity':   '0.35',
    'line_width':     '1.5',
}

_SLOW_CLASSES = {c for c, (fill, _) in _CLASS_FILL_KEYS.items() if c.startswith('Slow') or c.startswith('Small slow')}


def _setting(settings, key):
    return settings.get(key) or _DEFAULTS[key]


def _hex_to_rgba(hex_color, alpha=1.0):
    """'#RRGGBB' + alpha (0..1) → 'r,g,b,a' with a in 0-255."""
    h = hex_color.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f'{r},{g},{b},{round(alpha * 255)}'


# ---------------------------------------------------------------------------
# QML building blocks
# ---------------------------------------------------------------------------

def _marker_symbol(name, fill_hex, outline_hex, outline_width_pt=0.4, size_pt=6.4, outline_alpha=1.0):
    return f'''<symbol type="marker" name="{name}" alpha="1" force_rhr="0" clip_to_extent="1">
  <layer class="SimpleMarker" enabled="1" pass="0" locked="0">
    <Option type="Map">
      <Option name="name" type="QString" value="circle"/>
      <Option name="color" type="QString" value="{_hex_to_rgba(fill_hex)}"/>
      <Option name="outline_color" type="QString" value="{_hex_to_rgba(outline_hex, outline_alpha)}"/>
      <Option name="outline_style" type="QString" value="solid"/>
      <Option name="outline_width" type="QString" value="{outline_width_pt}"/>
      <Option name="outline_width_unit" type="QString" value="Point"/>
      <Option name="size" type="QString" value="{size_pt}"/>
      <Option name="size_unit" type="QString" value="Point"/>
      <Option name="scale_method" type="QString" value="diameter"/>
      <Option name="horizontal_anchor_point" type="QString" value="1"/>
      <Option name="vertical_anchor_point" type="QString" value="1"/>
    </Option>
  </layer>
</symbol>'''


def _fill_symbol(name, fill_hex, outline_hex, alpha=0.35, outline_width_mm=0.5):
    return f'''<symbol type="fill" name="{name}" alpha="1" force_rhr="0" clip_to_extent="1">
  <layer class="SimpleFill" enabled="1" pass="0" locked="0">
    <Option type="Map">
      <Option name="color" type="QString" value="{_hex_to_rgba(fill_hex, alpha)}"/>
      <Option name="style" type="QString" value="solid"/>
      <Option name="outline_color" type="QString" value="{_hex_to_rgba(outline_hex)}"/>
      <Option name="outline_style" type="QString" value="solid"/>
      <Option name="outline_width" type="QString" value="{outline_width_mm}"/>
      <Option name="outline_width_unit" type="QString" value="MM"/>
      <Option name="joinstyle" type="QString" value="bevel"/>
    </Option>
  </layer>
</symbol>'''


def _xml_escape(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


# ---------------------------------------------------------------------------
# Points QML (categorized by landslide_class)
# ---------------------------------------------------------------------------

def build_qml_points(settings):
    """Generate a QML for the landslides.geojson Points layer.

    Categorized by landslide_class. Catastrophic-with-precursor classes get
    a halo outline matching the precursor color.
    """
    stroke = _setting(settings, 'stroke_color')

    categories = []
    symbols = []
    for idx, (cls, (fill_key, halo_key)) in enumerate(_CLASS_FILL_KEYS.items()):
        if fill_key is None:
            fill_hex = _PALE_BLUE
        else:
            fill_hex = _setting(settings, fill_key)

        outline_alpha = 1.0
        if halo_key:
            outline_hex = _setting(settings, halo_key)
            outline_width = 0.9  # halo
        elif cls in _SLOW_CLASSES:
            # Match map.js .cls-dot-slow: semi-transparent black for definition.
            outline_hex = '#000000'
            outline_alpha = 0.35
            outline_width = 0.25
        else:
            outline_hex = stroke
            outline_width = 0.3

        categories.append(
            f'<category render="true" symbol="{idx}" '
            f'value="{_xml_escape(cls)}" label="{_xml_escape(cls)}" type="string"/>'
        )
        symbols.append(_marker_symbol(str(idx), fill_hex, outline_hex, outline_width, 6.4, outline_alpha))

    # Default category for any class not above
    default_idx = len(symbols)
    categories.append(
        f'<category render="true" symbol="{default_idx}" '
        f'value="" label="(unclassified)" type="string"/>'
    )
    symbols.append(_marker_symbol(str(default_idx), '#bbbbbb', '#666666', 0.2, 5.0))

    return _wrap_qml(
        renderer_type='categorizedSymbol',
        renderer_body=(
            f'<categories>\n{chr(10).join(categories)}\n</categories>\n'
            f'<symbols>\n{chr(10).join(symbols)}\n</symbols>\n'
        ),
        renderer_attrs='attr="landslide_class"',
    )


# ---------------------------------------------------------------------------
# Polygons QML (rule-based: role first, then landslide_class)
# ---------------------------------------------------------------------------

def build_qml_polygons(settings):
    """Generate a QML for the landslide_polygons layer.

    Rule-based renderer with one rule per landslide_class (color matching the
    points symbology), plus an ELSE catch-all. No role-based override — every
    polygon is colored solely by its landslide_class so source/deposit
    polygons of catastrophic landslides get the same per-class color as their
    parent (matches the inventory map).

    Requires the `landslide_class` column to be present. True for the flat
    polygon export. For the normalized polygon export, the user must first
    join the landslides table in QGIS with empty join-field-prefix so the
    column appears as `landslide_class` (not `landslides_landslide_class`).
    """
    stroke   = _setting(settings, 'stroke_color')
    opacity  = float(_setting(settings, 'fill_opacity'))
    line_w   = float(_setting(settings, 'line_width')) * 0.3  # px → mm-ish

    rules = []
    symbols = []

    # One rule per landslide_class
    sym_idx = 0
    for cls, (fill_key, _halo_key) in _CLASS_FILL_KEYS.items():
        if fill_key is None:
            fill_hex = _PALE_BLUE
        else:
            fill_hex = _setting(settings, fill_key)
        rules.append(
            f'<rule symbol="{sym_idx}" key="r_cls_{sym_idx}" '
            f'filter="&quot;landslide_class&quot; = &apos;{_xml_escape(cls)}&apos;" '
            f'label="{_xml_escape(cls)}"/>'
        )
        symbols.append(_fill_symbol(str(sym_idx), fill_hex, stroke, alpha=opacity, outline_width_mm=line_w))
        sym_idx += 1

    # Fallback (catch-all)
    rules.append(
        f'<rule symbol="{sym_idx}" key="r_default" filter="ELSE" label="(other)"/>'
    )
    symbols.append(_fill_symbol(str(sym_idx), '#cccccc', stroke, alpha=opacity, outline_width_mm=line_w))

    return _wrap_qml(
        renderer_type='RuleRenderer',
        renderer_body=(
            f'<rules key="r_root">\n{chr(10).join(rules)}\n</rules>\n'
            f'<symbols>\n{chr(10).join(symbols)}\n</symbols>\n'
        ),
        renderer_attrs='',
    )


# ---------------------------------------------------------------------------
# Survey-circles QML — outlined only (no fill), thin/bold by update_total,
# numerical label on circles with > 0 landslides identified.
# ---------------------------------------------------------------------------

def build_qml_survey_circles():
    """Generate a QML for the survey_circles layer.

    - Rule 1: update_total > 0 → bold black outline + label of update_total
    - Rule 2: ELSE             → thin black outline
    Fill is disabled (style="no") so the basemap shows through.
    """
    def outline_symbol(name, width_mm):
        return (
            f'<symbol type="fill" name="{name}" alpha="1" force_rhr="0" clip_to_extent="1">'
            f'<layer class="SimpleFill" enabled="1" pass="0" locked="0">'
            f'<Option type="Map">'
            f'<Option name="color" type="QString" value="0,0,0,0"/>'
            f'<Option name="style" type="QString" value="no"/>'
            f'<Option name="outline_color" type="QString" value="0,0,0,255"/>'
            f'<Option name="outline_style" type="QString" value="solid"/>'
            f'<Option name="outline_width" type="QString" value="{width_mm}"/>'
            f'<Option name="outline_width_unit" type="QString" value="MM"/>'
            f'<Option name="joinstyle" type="QString" value="bevel"/>'
            f'</Option></layer></symbol>'
        )

    rules = (
        '<rules key="r_root">'
        '<rule symbol="0" key="r_with" filter="&quot;update_total&quot; &gt; 0" label="With landslides"/>'
        '<rule symbol="1" key="r_no"   filter="ELSE" label="No landslides"/>'
        '</rules>'
    )
    symbols = (
        '<symbols>'
        + outline_symbol('0', 0.8)   # bold for circles with hits
        + outline_symbol('1', 0.2)   # thin for empty circles
        + '</symbols>'
    )
    renderer = (
        '<renderer-v2 type="RuleRenderer" forceraster="0" enableorderby="0" symbollevels="0">'
        + rules + symbols +
        '</renderer-v2>'
    )

    # Labeling: show update_total only where > 0; the CASE evaluates to NULL
    # for zero counts, which QGIS draws as no label.
    # Modern QGIS QML format puts the expression on text-style/@fieldName with
    # isExpression="1", and requires drawLabels="1" on <rendering> as the
    # master enable switch.
    label_expr = ('CASE WHEN &quot;update_total&quot; &gt; 0 '
                  'THEN &quot;update_total&quot; ELSE NULL END')
    labeling = (
        '<labeling type="simple">'
        '<settings calloutType="simple">'
        f'<text-style fontFamily="Sans Serif" namedStyle="Bold" fontSize="9" '
        f'fontSizeUnit="Point" fontWeight="75" fontItalic="0" '
        f'textOpacity="1" textColor="0,0,0,255" '
        f'isExpression="1" fieldName="{label_expr}">'
        '<text-buffer bufferDraw="1" bufferSize="1.2" bufferSizeUnits="MM" '
        'bufferColor="255,255,255,255" bufferOpacity="1" bufferJoinStyle="64"/>'
        '<text-mask maskEnabled="0"/>'
        '<background shapeDraw="0"/>'
        '<shadow shadowDraw="0"/>'
        '<dd_properties><Option type="Map">'
        '<Option name="name" value=""/>'
        '<Option name="properties"/>'
        '<Option name="type" value="collection"/>'
        '</Option></dd_properties>'
        '<substitutions/>'
        '</text-style>'
        '<text-format formatNumbers="0" plussign="0" decimals="0" '
        'multilineAlign="3" useMaxLineLengthForAutoWrap="1" wrapChar="" '
        'autoWrapLength="0" addDirectionSymbol="0" reverseDirectionSymbol="0"/>'
        '<placement placement="1" centroidWhole="1" centroidInside="1" '
        'polygonPlacementFlags="2" placementFlags="10" overrunDistance="0" '
        'overrunDistanceUnit="MM" maxCurvedCharAngleIn="25" maxCurvedCharAngleOut="-25" '
        'offsetType="0" priority="5" yOffset="0" xOffset="0" offsetUnits="MM" '
        'rotationUnit="AngleDegrees" rotationAngle="0" '
        'quadOffset="4" preserveRotation="1" geometryGeneratorEnabled="0" '
        'predefinedPositionOrder="TR,TL,BR,BL,R,L,TSR,BSR" repeatDistance="0" '
        'repeatDistanceUnit="MM" dist="0" distUnits="MM" layerType="PolygonGeometry"/>'
        '<rendering drawLabels="1" scaleVisibility="0" scaleMin="1" scaleMax="10000000" '
        'obstacle="0" obstacleType="1" obstacleFactor="1" labelPerPart="0" '
        'mergeLines="0" upsidedownLabels="0" displayAll="0" minFeatureSize="0" '
        'limitNumLabels="0" maxNumLabels="2000" fontMinPixelSize="3" '
        'fontMaxPixelSize="10000" fontLimitPixelSize="0" zIndex="0"/>'
        '<dd_properties><Option type="Map">'
        '<Option name="name" value=""/>'
        '<Option name="properties"/>'
        '<Option name="type" value="collection"/>'
        '</Option></dd_properties>'
        '<callout type="simple"><Option type="Map">'
        '<Option name="anchorPoint" value="pole_of_inaccessibility"/>'
        '</Option></callout>'
        '</settings>'
        '</labeling>'
    )

    return (
        "<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n"
        '<qgis version="3.34.0" styleCategories="Symbology|Labeling">\n'
        + renderer + '\n'
        + labeling + '\n'
        '<layerOpacity>1</layerOpacity>\n'
        '</qgis>\n'
    )


def _wrap_qml(renderer_type, renderer_body, renderer_attrs):
    return f'''<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34.0" styleCategories="Symbology">
  <renderer-v2 type="{renderer_type}" forceraster="0" enableorderby="0" symbollevels="0" {renderer_attrs}>
{renderer_body}
  </renderer-v2>
  <layerOpacity>1</layerOpacity>
</qgis>
'''
