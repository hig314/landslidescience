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


def _wrap_qml(renderer_type, renderer_body, renderer_attrs):
    return f'''<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34.0" styleCategories="Symbology">
  <renderer-v2 type="{renderer_type}" forceraster="0" enableorderby="0" symbollevels="0" {renderer_attrs}>
{renderer_body}
  </renderer-v2>
  <layerOpacity>1</layerOpacity>
</qgis>
'''
