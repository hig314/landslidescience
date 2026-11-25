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

from .auth import inventory_editor_required, is_inventory_editor
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
        # See PROMOTION RULES below.
        # The explorer (switching the Inventory tab to this source); see EXPLORE.
        'explore': {
            'layer': 'points',
            'name_fields': ['name', 'name2'],
            'type_field': 'mvmt_type', 'type_label': 'Movement type',
            # Their movement codes ("RA: rock avalanche") by the code before ':'.
            'family_by': 'code',
            'families': {
                'RA': 'RA', 'RS-T': 'RSL', 'RS-R': 'RSL', 'RF': 'FALL', 'DF': 'FALL',
                'DFL': 'FLOW', 'F': 'FLOW',
                'ES-T': 'SOIL', 'ES-R': 'SOIL', 'DS-T': 'SOIL', 'DS-R': 'SOIL',
                'EFL': 'SOIL', 'LAT': 'SOIL', 'LIQ': 'SOIL',
                'SOL': 'PF', 'ALD': 'PF', 'RTS': 'PF', 'FDL': 'PF', 'GEL': 'PF',
                'S': 'OTH', 'O': 'OTH', 'C': 'OTH',
            },
            'ring': {'field': 'fld_verify', 'on': 'yes', 'label': 'Ring: field verified'},
            'fade': {'field': 'conf_exist', 'alpha': {'certain': 0.95, 'probable': 0.6, 'questionable': 0.3},
                     'label': 'Fainter: their confidence that it exists is probable or questionable'},
            'facets': [
                {'key': 'cat', 'field': 'mvmt_categ', 'label': 'Movement category (theirs)'},
                {'key': 'exist', 'field': 'conf_exist', 'label': 'Confidence it exists',
                 'order': ['certain', 'probable', 'questionable']},
                {'key': 'ident', 'field': 'conf_ident', 'label': 'Confidence in type',
                 'order': ['certain', 'probable', 'questionable']},
                {'key': 'age', 'field': 'age_range', 'label': 'Age range'},
                {'key': 'mat', 'field': 'material', 'label': 'Material'},
                {'key': 'depth', 'field': 'depth_type', 'label': 'Depth'},
                {'key': 'fld', 'field': 'fld_verify', 'label': 'Field verified'},
            ],
            'colour_by': [['fam', 'Process family'], ['cat', 'Movement category (their scheme)'],
                          ['exist', 'Confidence it exists'], ['age', 'Age range'], ['mat', 'Material']],
            'presets': [
                {'label': 'Everything', 'set': {}},
                {'label': 'Rock avalanches', 'set': {'fam': ['RA']}},
                {'label': 'Rock avalanches and rock slides, certain',
                 'set': {'fam': ['RA', 'RSL'], 'exist': ['certain']}},
                {'label': 'Field verified', 'set': {'fld': ['yes']}},
            ],
            'home': [-150, 61.5, 4.3],
        },
        'promote': {
            # The lead of the provenance sentence; promotion_text() adds
            # ", as <their id>." and the details below.
            'noted_in': ('Noted in the Alaska Landslide Inventory '
                         '(Alaska DGGS Digital Data Series 23, Nicolazzo and '
                         'Larsen, 2025), doi:10.14509/31697'),
            # Their citation is split over two columns: `mapper` usually holds
            # the reference ("Hamilton, T.D., 1979") and `data_sourc` the DGGS
            # publication code ("MF-1122"), but for newer work both carry the
            # reference, and for Swanson's map `mapper` says "machine
            # learning". `dggs_source` folds the combinations into one phrase.
            # `notes` is mostly "Confidence= low" (their conf_* columns say
            # that better), so only notes that say something else travel.
            # Their id is a GUID; their popup and ArcGIS service also show an
            # OBJECTID, which is the number people actually quote.
            'id_note': ' (OBJECTID {objectid})',
            'details': [
                {'label': 'Their source', 'format': 'dggs_source'},
                {'label': 'Their notes', 'field': 'notes', 'quote': True,
                 'skip': r'^\s*Confidence\s*=\s*\w+\s*$'},
            ],
            # Earlier wordings of the sentence, recognised and replaced when a
            # record is linked again or backfilled (2026-11-24).
            'legacy': ['Noted in the Alaska Landslide Inventory (Alaska DGGS '
                       'Digital Data Series 23, Nicolazzo and Larsen, 2025), '
                       'doi:10.14509/31697.'],
        },
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
        'explore': {
            'layer': 'points',
            'name_fields': ['name'],
            'type_field': 'type', 'type_label': 'Type',
            'family_by': 'value',
            'families': {
                'Rock avalanche': 'RA', 'Debris avalanche': 'RA',
                'Rock slope deformation': 'SD', 'Mountain slope deformation': 'SD',
                'Rock slide undifferentiated': 'RSL', 'Rock rotational slide': 'RSL',
                'Rock translational slide': 'RSL', 'Rock irregular slide': 'RSL',
                'Rock spread': 'RSL', 'Rock complex': 'RSL',
                'Rock fall': 'FALL', 'Earth fall': 'FALL', 'Debris fall': 'FALL',
                'Rock topple': 'FALL', 'Ice fall': 'FALL',
                'Debris flow': 'FLOW', 'Debris flood': 'FLOW', 'Flood': 'FLOW',
                'GLOF': 'FLOW', 'Mud flow': 'FLOW',
                'Earth slide': 'SOIL', 'Debris slide': 'SOIL', 'Flowslide': 'SOIL',
                'Earth flow': 'SOIL', 'Earth spread': 'SOIL',
                'Retrogressive thaw slump': 'PF', 'Active layer detachment': 'PF',
                'Deep-seated permafrost landslide': 'PF',
            },
            'ring': {'field': 'deformation_insar_data', 'on': 'yes',
                     'label': 'Ring: deformation seen in InSAR (their flag; no rate given)'},
            'size': {'field': 'size_class', 'scale': {'giant': 1.8, 'large': 1.4},
                     'label': 'Larger dot: their size class is Large or Giant'},
            'facets': [
                {'key': 'insar', 'field': 'deformation_insar_data', 'label': 'Deformation in InSAR'},
                {'key': 'size', 'field': 'size_class', 'label': 'Size class',
                 'order': ['giant', 'large', 'medium', 'small']},
                {'key': 'mat', 'field': 'material', 'label': 'Material'},
                {'key': 'conf', 'field': 'location_confidence', 'label': 'Location confidence',
                 'order': ['high', 'moderate', 'low']},
                {'key': 'ref', 'expr': "CASE WHEN nullif(btrim(reference), '') IS NULL THEN 'no' ELSE 'yes' END",
                 'label': 'Cites a reference'},
                {'key': 'dam', 'field': 'landslide_dammed_lake', 'label': 'Landslide-dammed lake'},
                {'key': 'ploc', 'field': 'point_location', 'label': 'Point placed on'},
                {'key': 'trig', 'field': 'trigger', 'label': 'Trigger'},
                {'key': 'ver', 'field': 'modified_in_version', 'label': 'Last modified in version',
                 'sort': 'num'},
            ],
            'colour_by': [['fam', 'Process family'], ['conf', 'Location confidence'],
                          ['ver', 'Version last modified'], ['mat', 'Material'], ['ref', 'Cites a reference']],
            'presets': [
                {'label': 'Everything', 'set': {}},
                # `type` = only these of their types (within their families).
                {'label': 'Rock avalanches', 'set': {'type': ['Rock avalanche']}},
                {'label': 'Rock and debris avalanches', 'set': {'fam': ['RA']}},
                {'label': 'Slope deformation and rock slides seen in InSAR',
                 'set': {'fam': ['SD', 'RSL'], 'insar': ['yes']}},
                {'label': 'Large and giant', 'set': {'size': ['large', 'giant']}},
            ],
            'home': [-131, 57.5, 4],
        },
        'promote': {
            'noted_in': ('Noted in the Preliminary Canadian Landslide Database '
                         'v14.0 (Brideau and others, 2026), '
                         'doi:10.5281/zenodo.20371365'),
            # About half their records carry a free-text Comment and half a
            # Reference (15,074 and 9,115 of 31,005 in v14.0).
            'details': [
                {'label': 'Their comment', 'field': 'comment', 'quote': True},
                {'label': 'Their reference', 'field': 'reference'},
            ],
            'legacy': ['Noted in the Preliminary Canadian Landslide Database '
                       'v14.0 (Brideau and others, 2026), '
                       'doi:10.5281/zenodo.20371365.'],
            # Whose observation this is. Attributable here because the database
            # has one compiler of record; most inventories do not, which is why
            # this is a per-source rule and not a universal one.
            'noted_by': 'Marc-Andre Brideau',
        },
    },
    'usgs_schaefer2026': {
        'title': 'Deforming bedrock landslides in southern Alaska (PSInSAR, 2019–2021)',
        'short': 'USGS Schaefer 2026',
        'citation': ('Schaefer, L.N., Staley, D.M., Kim, J., Barnhart, K.R., and Lu, Z., '
                     '2026, Supporting data for "Environmental drivers of deforming '
                     'bedrock landslides in southern Alaska": U.S. Geological Survey '
                     'data release.'),
        'url': 'https://doi.org/10.5066/P1B64SZT',
        # The paper: npj Natural Hazards 3, 90, doi:10.1038/s44304-026-00296-3.
        'licence': ('CC0 1.0 (public domain). Cite the data release and Schaefer '
                    'and others (2026), npj Natural Hazards, '
                    'doi:10.1038/s44304-026-00296-3.'),
        # Hard yellow, outline only (Hig, 2026-11-25): yellow = subtle, as in
        # our InSAR/subtle-creep class, but an outline so it is never confused
        # with our yellow-FILLED subtle-creep polygons. Dark casing keeps the
        # line visible over snow and ice, where many of these sit.
        'colour': '#ffd000',
        'style': {
            'fill_opacity': 0.01,   # near-invisible, but the fill still takes clicks
            'line_width': 2.2,
            'casing': '#4a3b00',
            'ink': '#8a6a00',       # headings in text: yellow on white is unreadable
        },
        'fetch': {
            # A zipped shapefile on ScienceBase (item 684afb2cd4be020b699d9944).
            # The URL names one file version; a revised release is a new URL.
            'kind': 'zip_shp',
            'layers': {
                'landslides': {
                    'geom': 'MULTIPOLYGON',
                    'label': 'Landslide outlines',
                    'url': ('https://www.sciencebase.gov/catalog/file/get/'
                            '684afb2cd4be020b699d9944?f=__disk__9a%2Fc9%2F74%2F'
                            '9ac974976ec3e8ae2fe930e8ff9e39cb63ff940b'),
                    'expect': 239,
                    # A shapefile has no display aliases; these are short forms
                    # of their data dictionary (AK_landslides_DataDictionary.csv)
                    # and become the column comments the popup shows.
                    'labels': {
                        'slide_code': 'Landslide id',
                        'vol_s': 'Volume, smaller scenario (m³)',
                        'vol_l': 'Volume, larger scenario (m³)',
                        'hmax_s': 'Maximum height, smaller scenario (m)',
                        'hmax_l': 'Maximum height, larger scenario (m)',
                        'hmean_s': 'Mean height, smaller scenario (m)',
                        'hmean_l': 'Mean height, larger scenario (m)',
                        'point_cnt': 'PSInSAR points in outline',
                        'path_list': 'Sentinel-1 paths',
                        'avg_los': 'Average line-of-sight rate (mm/yr)',
                        'peak_los': 'Peak line-of-sight rate (mm/yr)',
                        'latitude': 'Centroid latitude',
                        'longitude': 'Centroid longitude',
                        'name': 'Name',
                        'citation': 'Citation (if previously identified)',
                        'con_pre': 'Confidence in presence (A high, B moderate, C low)',
                        'con_del': 'Confidence in outline (A high, B moderate, C low)',
                        'shape_area': 'Area (m²)',
                        'fall_line': 'Fall-line aspect (°)',
                        'elevat_hs': 'Headscarp top elevation (m)',
                        'slope_avg': 'Mean slope (°)',
                        'slope_max': 'Maximum slope (°)',
                        'geol_code': 'Geology unit (Wilson and others, 2015)',
                        'dist_fault': 'Distance to Quaternary fault (km)',
                        'dist_glac': 'Distance to glacier (km)',
                        'nlcd_cat': 'Land cover (NLCD)',
                        'perm_cat': 'Permafrost (Jorgenson and others, 2008)',
                        'sus_cat': 'Deep-seated susceptibility class (DGGS 2025)',
                        'ppt_avg': 'Mean annual precipitation, 1981–2010 (mm)',
                        'temp_avg': 'Mean annual air temperature, 1981–2010 (°C)',
                        'magt_2026': 'Mean annual ground temperature 2026, RCP4.5 (°C)',
                        'magt_2036': 'Mean annual ground temperature 2036, RCP4.5 (°C)',
                        'magt_2076': 'Mean annual ground temperature 2076, RCP4.5 (°C)',
                    },
                },
            },
        },
        'id_field': 'slide_code',
        'name_field': 'name',
        'primary_layer': 'landslides',
        'promote': {
            'noted_in': ('Noted in the inventory of deforming bedrock landslides in '
                         'southern Alaska (Schaefer and others, 2026, U.S. Geological '
                         'Survey data release), doi:10.5066/P1B64SZT'),
            # Their ids are bare numbers 1-239; "as landslide 55" reads better.
            'id_prefix': 'landslide ',
            # 56 of 239 were previously identified: their name and the citation
            # (cut at 254 characters by the shapefile format) travel verbatim.
            'details': [
                {'label': 'Their name', 'field': 'name', 'skip': r'(?i)^unnamed$'},
                {'label': 'Their citation', 'field': 'citation'},
                {'label': 'Their confidence', 'format': 'schaefer_conf'},
                {'label': 'Their PSInSAR rate', 'format': 'schaefer_los'},
            ],
            # Where they cite the landslide's first identification, its first
            # author noticed it; otherwise Schaefer's team did.
            'noted_by_from': 'citation',
            'noted_by': 'Lauren Schaefer',
            # Every record is a landslide they saw deforming in PSInSAR, so a
            # record promoted from or linked to one shows InSAR deformation
            # (Hig, 2026-11-24). Filed under insar_other for now: the per-study
            # InSAR columns need rethinking as InSAR inventories multiply (see
            # docs/STATE.md, Open threads).
            'set': {'insar_other': True},
        },
    },
}


# ---------------------------------------------------------------------------
# PROMOTION RULES
#
# What is written onto OUR record when it is promoted from one of theirs, or
# linked to one of theirs after the fact. Deliberately the least clever piece
# of the derivation layer: per-source wording a person decided once, filled
# from the source record's own fields, readable back from the registry.
#
#   ongoing_work   says where the observation came from, so a reader of our
#                  record can find theirs: the source's `noted_in` lead, their
#                  own id for the record ("..., as LS27181."), and any
#                  `details` the source names, each quoted verbatim ("Their
#                  comment: "...""). Quoted so the generated block has an end a
#                  machine can find: a re-link or backfill replaces it in place
#                  and leaves whatever an editor wrote around it alone (Hig
#                  adds "Described in doi:..." and the like beside it). Earlier
#                  wordings are listed in the source's `legacy` and replaced the
#                  same way. (Hig, 2026-11-24: the id and their Comment and
#                  citation were missing from the record.)
#
#   noted_by       on a NEW promoted record is CLEARED, not filled with the
#                  editor who promoted it. Everywhere else in this app a blank
#                  noted_by fills with the person at the keyboard, because they
#                  are the one who noticed it. In a promotion they are not:
#                  somebody else noticed it, and crediting the promoter would
#                  quietly overwrite that. A source with a compiler of record
#                  names them (Canada does); most cannot, and a blank is then
#                  the honest answer. A source may name a field of theirs
#                  that cites where the landslide was first identified
#                  (`noted_by_from`); its first author is then credited
#                  instead (Hig, 2026-11-24). On a LINK to an existing record,
#                  noted_by is only filled when blank: the record already says
#                  who noticed it, possibly independently of the source.
#
#   set            column -> value written on promotion AND on link, e.g.
#                  Schaefer 2026's insar_other = True. Only ever raises a
#                  boolean to True (an existing True is not touched, a False is
#                  overridden by their positive observation); the rule cascade
#                  then runs, since these columns feed derived rules
#                  (insar_other -> insar_creep -> creep behaviour, class).
#                  Unlinking does NOT undo it: the earlier value is not kept.
#
#   links          one row per (our record, their source, their id) in
#                  landslide_external_links: a record may derive from several
#                  outside records (Hig, 2026-11-24), each with its own
#                  sentence. Replaced the external_source / external_id
#                  columns, which held one and are no longer read or written.
#
# `owner` is left alone on purpose. It means who is responsible for the record
# HERE, which really is the editor who promoted it.
# ---------------------------------------------------------------------------
PROMOTE_FIELDS = ('ongoing_work', 'noted_by')


def _dggs_source(row):
    """DGGS's citation, from `mapper` + `data_sourc` (see the registry)."""
    m = ' '.join(str(row.get('mapper') or '').split())
    d = ' '.join(str(row.get('data_sourc') or '').split())
    if not m:
        return d
    if not d:
        return m
    if m.lower() == 'machine learning':
        return f'{d}, machine learning'
    # One is the other with a year added ("Darrow, M.M." / "Darrow, M.M., 2021").
    if d.startswith(m.rstrip('.')):
        return d
    if m.startswith(d.rstrip('.')):
        return m
    return f'{m} ({d})'


_CONF = {'A': 'high', 'B': 'moderate', 'C': 'low'}


def _schaefer_conf(row):
    """Their A/B/C ratings, spelled out from their data dictionary."""
    p, d = (str(row.get('con_pre') or '').strip(), str(row.get('con_del') or '').strip())
    out = []
    if p:
        out.append(f'presence {p}' + (f' ({_CONF[p]})' if p in _CONF else ''))
    if d:
        out.append(f'outline {d}' + (f' ({_CONF[d]})' if d in _CONF else ''))
    return ', '.join(out)


def _schaefer_los(row):
    """Their line-of-sight rates, Sentinel-1 2019-2021 (mm/yr, as published)."""
    a, k = row.get('avg_los'), row.get('peak_los')
    if a is None and k is None:
        return ''
    bits = []
    if a is not None:
        bits.append(f'average {float(a):.1f}')
    if k is not None:
        bits.append(f'peak {float(k):.1f}')
    path = str(row.get('path_list') or '').strip()
    return (', '.join(bits) + ' mm/yr line of sight, Sentinel-1 2019–2021'
            + (f', path {path}' if path else ''))


_DETAIL_FORMATS = {'dggs_source': _dggs_source, 'schaefer_conf': _schaefer_conf,
                   'schaefer_los': _schaefer_los}


def _rules(sid):
    s = SOURCES.get(sid) or {}
    return s, dict(s.get('promote') or {})


def _lead(sid):
    s, r = _rules(sid)
    return r.get('noted_in') or f"Noted in {s.get('title', sid)}"


def promotion_text(sid, ext_id, row):
    """The provenance sentence for our record promoted from / linked to their
    record `ext_id`, whose mirrored fields are `row` (a dict of columns)."""
    _, r = _rules(sid)
    note = ''
    if r.get('id_note'):
        try:
            note = r['id_note'].format(**{k: ('' if v is None else v) for k, v in row.items()})
        except (KeyError, IndexError):
            note = ''
    out = [f'{_lead(sid)}, as {r.get("id_prefix", "")}{ext_id}{note}.']
    for d in r.get('details') or []:
        if 'format' in d:
            v = _DETAIL_FORMATS[d['format']](row)
        else:
            v = row.get(d['field'])
        v = ' '.join(str(v or '').split())
        if not v or (d.get('skip') and re.match(d['skip'], v)):
            continue
        # Curly quotes inside would end the block early; straighten them.
        v = v.replace('“', '"').replace('”', '"')
        out.append(f'{d["label"]}: “{v}”.')
    return ' '.join(out)


def _id_pattern(sid, ext_id=None):
    """The current wording's regex, for one id or (ext_id None) any id."""
    _, r = _rules(sid)
    labels = '|'.join(re.escape(d['label']) for d in r.get('details') or [])
    if ext_id is None:
        idp = r'[^.“]+?'                       # ids and id_notes hold neither
    else:
        idp = re.escape(r.get('id_prefix', '') + str(ext_id)) + r'(?: \([^.)“]*\))?'
    p = re.escape(_lead(sid)) + r', as ' + idp + r'\.'
    if labels:
        p += rf'(?: (?:{labels}): “[^”]*”\.)*'
    return re.compile(p)


def _legacy_patterns(sid):
    """Earlier, id-less wordings (see the source's `legacy`)."""
    s, r = _rules(sid)
    return [re.compile(re.escape(old))
            for old in list(r.get('legacy') or []) + [f"Noted in {s.get('title', sid)}."]]


def _text_patterns(sid):
    """Every wording of this source's sentence, any id (backfill detection)."""
    return [_id_pattern(sid)] + _legacy_patterns(sid)


def merge_promotion_text(sid, ext_id, existing, new):
    """`existing` ongoing_work with the sentence for THIS record of theirs
    replaced by `new`; else an id-less legacy sentence of this source replaced
    (it was written for the one link a record could have then); else `new` put
    in front. Sentences for other links -- another source, or another record
    in the same source -- are left alone: a record of ours may derive from
    several (Hig, 2026-11-24)."""
    existing = (existing or '').strip()
    for pat in [_id_pattern(sid, ext_id)] + _legacy_patterns(sid):
        if pat.search(existing):
            return pat.sub(lambda _m: new, existing, count=1)
    return f'{new} {existing}'.strip()


def remove_promotion_text(sid, ext_id, existing):
    """`existing` with the sentence for this record of theirs taken out."""
    out = _id_pattern(sid, ext_id).sub('', existing or '', count=1)
    return re.sub(r'\s{2,}', ' ', out).strip()


# -- noted_by ---------------------------------------------------------------
_AUTH_USGS = re.compile(r"^\s*([A-Z][\w'\-]+(?: [A-Z][\w'\-]+)?),\s*((?:[A-Z]\.\s*-?)+)")
_AUTH_INIT = re.compile(r"^\s*([A-Z][\w'\-]+)\s+([A-Z]{1,3})\b")


def _first_author(cur, citation):
    """The first author of a citation, as a noted_by value. If someone with
    that surname and first initial is already credited somewhere in noted_by,
    their name is reused as written there ("Bretwood Higman", not "B.
    Higman"), so one person stays one value in the filters. Otherwise
    "C.P. Hults". None when the citation does not open with an author (an
    agency, say), and the source's own rule then applies."""
    c = ' '.join(str(citation or '').split())
    m = _AUTH_USGS.match(c)
    if m:
        surname, initials = m.group(1), re.sub(r'\s+', '', m.group(2))
    else:
        m = _AUTH_INIT.match(c)
        if not m:
            return None
        surname, initials = m.group(1), '.'.join(m.group(2)) + '.'
    first = initials[:1].upper()
    cur.execute("""
        SELECT noted_by, count(*) FROM landslides
         WHERE lower(noted_by) LIKE %s
         GROUP BY 1 ORDER BY 2 DESC
    """, ('%' + surname.lower(),))
    for name, _n in cur.fetchall():
        parts = (name or '').replace('.', ' ').split()
        if len(parts) >= 2 and parts[-1].lower() == surname.lower() \
                and parts[0][:1].upper() == first:
            return name
    return f'{initials} {surname}'


def noted_by_for(cur, sid, row):
    """noted_by for a record promoted from `row`: the source's `noted_by_from`
    field's first author where they cite where the landslide was first
    identified (Hig, 2026-11-24: "If the original source is noted, let's use
    that"), else the source's `noted_by` (its compiler of record), else None,
    which CLEARS the column the draw commit filled with the promoter."""
    _, r = _rules(sid)
    f = r.get('noted_by_from')
    if f and row.get(f):
        who = _first_author(cur, row.get(f))
        if who:
            return who
    return r.get('noted_by')


class PromotionError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def mirrored_row(cur, sid, layer, ext_id):
    """Their record as {column: value}, by THEIR id. None if the mirror has no
    such id, which is how a hand-typed or wrong id is caught before it is
    linked to our record (three DGGS promotions carried the mirror's OBJECTID
    instead of their points_id until 2026-11-24)."""
    t = table_name(sid, layer)
    cur.execute('SELECT to_regclass(%s)', (t,))
    if not cur.fetchone()[0]:
        return None
    cur.execute(f'SELECT * FROM {t} WHERE ext_id = %s LIMIT 1', (str(ext_id),))
    r = cur.fetchone()
    if r is None:
        return None
    row = dict(zip([d[0] for d in cur.description], r))
    row.pop('geom', None)
    return row


LINKS = 'landslide_external_links'


def links_ready(cur):
    cur.execute('SELECT to_regclass(%s)', (LINKS,))
    return bool(cur.fetchone()[0])


def links_of(cur, ls_id):
    """[(source, ext_id), ...] for one landslide, oldest first."""
    cur.execute(f'SELECT source, ext_id FROM {LINKS} WHERE landslide_id = %s '
                f'ORDER BY linked_at, source, ext_id', (ls_id,))
    return [tuple(r) for r in cur.fetchall()]


def apply_promotion(cur, ls_id, sid, layer, ext_id, *, fresh, who=None):
    """Link landslide `ls_id` to their record and write the rule fields, in the
    caller's transaction. `fresh` = the record was just drawn for this
    promotion (noted_by is overwritten); otherwise an existing record is being
    linked (noted_by only filled when blank). Links already on the record stay:
    each has its own sentence in ongoing_work. Returns what changed."""
    if sid not in SOURCES or layer not in SOURCES[sid]['fetch']['layers']:
        raise PromotionError('unknown source or layer', 404)
    if not links_ready(cur):
        raise PromotionError('links table missing: run manage.py migrate_external_links', 503)
    row = mirrored_row(cur, sid, layer, ext_id)
    if row is None:
        raise PromotionError(f'{SOURCES[sid]["short"]} has no record {ext_id} in {layer}', 404)
    cur.execute('SELECT unique_name, ongoing_work, noted_by FROM landslides '
                'WHERE id = %s FOR UPDATE', (ls_id,))
    cur_row = cur.fetchone()
    if cur_row is None:
        raise PromotionError(f'no landslide {ls_id}', 404)
    name, ow, nb = cur_row
    before = links_of(cur, ls_id)
    new_ow = merge_promotion_text(sid, ext_id, ow, promotion_text(sid, ext_id, row))
    rule_nb = noted_by_for(cur, sid, row)
    new_nb = rule_nb if fresh else (nb if (nb or '').strip() else rule_nb)
    cur.execute('UPDATE landslides SET ongoing_work = %s, noted_by = %s, '
                'updated_at = NOW() WHERE id = %s', (new_ow, new_nb, ls_id))
    set_applied = _apply_set_rules(cur, ls_id, sid)
    cur.execute(f'INSERT INTO {LINKS} (landslide_id, source, ext_id, linked_by) '
                f'VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING',
                (ls_id, sid, str(ext_id), who))
    return {'landslide_id': ls_id, 'name': name,
            'external_source': sid, 'external_id': str(ext_id),
            'ongoing_work': new_ow, 'noted_by': new_nb,
            'links': links_of(cur, ls_id), 'set': set_applied,
            'previous': {'links': before, 'ongoing_work': ow, 'noted_by': nb}}


_SET_COLUMN = re.compile(r'^[a-z_][a-z0-9_]*$')


def _apply_set_rules(cur, ls_id, sid):
    """The source's `set` rules onto landslide `ls_id`, then the derived-rule
    cascade if anything changed (as a form save of those fields would). Only
    True is written, and only where the column is not already True. Returns
    {column: True} for what changed."""
    _, r = _rules(sid)
    changed = {}
    for col, val in (r.get('set') or {}).items():
        if val is not True or not _SET_COLUMN.match(col):
            raise PromotionError(f'unsupported set rule {col}={val!r} for {sid}', 500)
        cur.execute(f'UPDATE landslides SET {col} = TRUE '
                    f'WHERE id = %s AND {col} IS DISTINCT FROM TRUE', (ls_id,))
        if cur.rowcount:
            changed[col] = True
    if changed:
        from . import derived
        derived.apply_rules_for_landslide(cur, ls_id)
    return changed


def remove_link(cur, ls_id, sid, ext_id):
    """Undo one link: the row and its sentence. noted_by is left as it is --
    it may have been settled by hand since."""
    if not links_ready(cur):
        raise PromotionError('links table missing: run manage.py migrate_external_links', 503)
    cur.execute(f'DELETE FROM {LINKS} WHERE landslide_id = %s AND source = %s AND ext_id = %s',
                (ls_id, sid, str(ext_id)))
    if not cur.rowcount:
        raise PromotionError(f'landslide {ls_id} is not linked to {sid} {ext_id}', 404)
    cur.execute('SELECT unique_name, ongoing_work FROM landslides WHERE id = %s FOR UPDATE', (ls_id,))
    name, ow = cur.fetchone()
    new_ow = remove_promotion_text(sid, ext_id, ow)
    cur.execute('UPDATE landslides SET ongoing_work = %s, updated_at = NOW() WHERE id = %s',
                (new_ow, ls_id))
    return {'landslide_id': ls_id, 'name': name, 'ongoing_work': new_ow,
            'links': links_of(cur, ls_id)}


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


_NP_PG = {'f': 'double precision', 'i': 'bigint', 'u': 'bigint', 'b': 'boolean'}


def zip_shp_rows(url):
    """A zipped shapefile -> (columns, rows), columns as (col, pgtype, their
    field name) and rows as (GeoJSON geometry, {col: value}). Read with pyogrio
    (GDAL in its wheel, already used by the import flow); 3D geometry is
    flattened, since a Z in a landslide outline is an artefact of their export,
    not a measurement anyone made. Numeric fields keep their declared type."""
    import os
    import tempfile

    import pyogrio.raw
    import shapely
    from shapely.geometry import mapping

    import zipfile

    import time
    import urllib.error

    # ScienceBase answers a busy moment with a 502 or a short "error code: 502"
    # page (often, in 2026-11); a few spaced tries get through.
    data = b''
    for attempt in range(5):
        try:
            data = _get(url)
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == 4:
                raise
            data = b''
        if data[:2] == b'PK':
            break
        time.sleep(5 * (attempt + 1))
    if data[:2] != b'PK':
        raise ValueError(f'not a zip ({len(data)} bytes: {data[:60]!r}); try again')
    fd, path = tempfile.mkstemp(suffix='.zip')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        with zipfile.ZipFile(path) as z:
            shps = [n for n in z.namelist() if n.lower().endswith('.shp')]
        if len(shps) != 1:
            raise ValueError(f'expected one .shp in the zip, found {shps}')
        meta, _fids, geoms, fields = pyogrio.raw.read(f'/vsizip/{path}/{shps[0]}')
    finally:
        os.unlink(path)
    names = list(meta['fields'])
    cols = []
    for name, dt in zip(names, meta['dtypes']):
        kind = getattr(dt, 'kind', None) or str(dt)[:1]
        cols.append((_col(name), _NP_PG.get(kind, 'text'), name))
    rows = []
    for i, wkb in enumerate(geoms):
        g = None
        if wkb is not None:
            g = mapping(shapely.force_2d(shapely.from_wkb(wkb)))
        props = {}
        for (c, t, _), arr in zip(cols, fields):
            v = arr[i]
            if v is None or (t != 'text' and v != v):          # None / NaN
                props[c] = None
            elif t == 'double precision':
                props[c] = float(v)
            elif t == 'bigint':
                props[c] = int(v)
            elif t == 'boolean':
                props[c] = bool(v)
            else:
                props[c] = (str(v) or None)
        rows.append((g, props))
    return cols, rows


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
                    'style': s.get('style') or {},
                    'primary_layer': s['primary_layer'], 'layers': layers,
                })
        conn.rollback()
    finally:
        _put_conn(conn)
    return JsonResponse({'sources': out})


def _promote_or_link(request, sid, layer, ext_id, fresh):
    try:
        body = json.loads((request.body or b'{}').decode('utf-8'))
        ls_id = int(body.get('landslide_id'))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'landslide_id required'}, status=400)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        try:
            if fresh is None:
                applied = remove_link(cur, ls_id, sid, ext_id)
            else:
                applied = apply_promotion(cur, ls_id, sid, layer, ext_id, fresh=fresh,
                                          who=_who(request))
        except PromotionError as e:
            conn.rollback()
            return JsonResponse({'error': str(e)}, status=e.status)
        conn.commit()
    finally:
        _put_conn(conn)
    from .models import LandslideEditMeta
    from .views import _invalidate
    LandslideEditMeta.objects.update_or_create(
        landslide_id=ls_id, defaults={'last_edited_by': request.user})
    _invalidate()
    return JsonResponse({'ok': True, 'landslide_id': ls_id, 'applied': applied})


@inventory_editor_required
@require_http_methods(['POST'])
def api_external_promote(request, sid, layer, ext_id):
    """Apply a source's promotion rules to a landslide just created from it.

    POST {"landslide_id": N}. Editor-only: it writes to `landslides`.

    WHY THIS IS SERVER-SIDE AND NOT THREE CALLS FROM THE BROWSER
    -----------------------------------------------------------
    The rules live in the registry, which is server-side, and they include
    CLEARING a column. The draw commit has already stamped the promoting
    editor into noted_by by the time this runs -- that is the right default
    everywhere else -- so the promotion has to actively undo it. Expressing
    that as a sequence of per-field saves from the browser would mean a half-
    applied promotion whenever one of them failed, with the editor's name left
    on somebody else's observation. One statement, one transaction.
    """
    return _promote_or_link(request, sid, layer, ext_id, fresh=True)


@inventory_editor_required
@require_http_methods(['POST'])
def api_external_link(request, sid, layer, ext_id):
    """Link an EXISTING landslide of ours to their record: the same stamp and
    provenance sentence as a promotion, merged into what the record already
    says, and noted_by only filled when blank. For a record drawn before its
    source record was noticed (Hig, 2026-11-24: drawing a throwaway record to
    promote, deleting it and copying the text by hand lost the stamp).

    POST {"landslide_id": N}. Editor-only."""
    return _promote_or_link(request, sid, layer, ext_id, fresh=False)


@inventory_editor_required
@require_http_methods(['POST'])
def api_external_unlink(request, sid, layer, ext_id):
    """Remove one link from a landslide of ours, with its sentence in
    ongoing_work. POST {"landslide_id": N}. Editor-only. For a wrong link;
    the landslide itself is untouched otherwise."""
    if sid not in SOURCES:
        return JsonResponse({'error': 'unknown source'}, status=404)
    return _promote_or_link(request, sid, layer, ext_id, fresh=None)


@inventory_editor_required
@require_http_methods(['GET'])
def api_landslide_links(request, landslide_id):
    """The outside records a landslide of ours is linked to, for the
    "Promoted from" cards on its edit and review forms:
    {links: [{source, ext_id, layer}]} (layer = the source's primary layer;
    a promotion is recorded by source and id, not layer)."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if not links_ready(cur):
            conn.rollback()
            return JsonResponse({'links': [], 'not_migrated': True})
        rows = links_of(cur, landslide_id)
        conn.rollback()
    finally:
        _put_conn(conn)
    return JsonResponse({'links': [
        {'source': sid, 'ext_id': eid,
         'layer': (SOURCES.get(sid) or {}).get('primary_layer')}
        for sid, eid in rows if sid in SOURCES]})


@inventory_editor_required
@require_http_methods(['GET'])
def api_external_nearby(request, sid, layer, ext_id):
    """Our records nearest their record, for the popup's Link list. Distance is
    from their geometry to our centroid (0 when ours sits inside their
    outline); a suggestion, not a verdict. Deprecated records excluded."""
    if sid not in SOURCES or layer not in SOURCES[sid]['fetch']['layers']:
        return JsonResponse({'error': 'unknown source or layer'}, status=404)
    t = table_name(sid, layer)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f'SELECT 1 FROM {t} WHERE ext_id = %s LIMIT 1', (str(ext_id),))
        if not cur.fetchone():
            conn.rollback()
            return JsonResponse({'error': 'no such record'}, status=404)
        # Distance from THEIR geometry, so one of ours inside their outline is
        # 0 m. A KNN shortlist by their point-on-surface, then the exact sort.
        cur.execute(f"""
            WITH g AS (SELECT geom, ST_PointOnSurface(geom) AS p FROM {t}
                        WHERE ext_id = %s LIMIT 1),
            near AS (
                SELECT l.id, l.unique_name,
                       l.reviewed_at IS NULL AS pending,
                       ST_SetSRID(ST_MakePoint(l.centroid_lon, l.centroid_lat), 4326) AS c
                  FROM landslides l, g
                 WHERE l.deprecated_at IS NULL AND l.centroid_lon IS NOT NULL
                 ORDER BY ST_SetSRID(ST_MakePoint(l.centroid_lon, l.centroid_lat), 4326) <-> g.p
                 LIMIT 20)
            SELECT n.id, n.unique_name, n.pending,
                   round(ST_Distance(n.c::geography, g.geom::geography)) AS d
              FROM near n, g ORDER BY d, n.id LIMIT 6
        """, (str(ext_id),))
        rows = cur.fetchall()
        # Overlap first (Hig, 2026-11-24: "start by assuming it's a landslide
        # that has strong overlap"). Our outlines that touch theirs, all roles
        # of a record unioned: for an outline, the shared area as a share of
        # each and as intersection-over-union; for a point, whether one of our
        # outlines contains it.
        cur.execute(f"""
            WITH g AS (SELECT ST_MakeValid(geom) AS geom FROM {t} WHERE ext_id = %s LIMIT 1),
            cand AS (
                SELECT p.landslide_id, ST_Union(ST_MakeValid(p.geom)) AS u
                  FROM landslide_polygons p
                  JOIN landslides l ON l.id = p.landslide_id AND l.deprecated_at IS NULL, g
                 WHERE p.geom && g.geom AND ST_Intersects(p.geom, g.geom)
                 GROUP BY p.landslide_id)
            SELECT c.landslide_id, l.unique_name, l.reviewed_at IS NULL,
                   ST_Area(ST_Intersection(c.u, g.geom)::geography),
                   ST_Area(c.u::geography), ST_Area(g.geom::geography),
                   ST_Dimension(g.geom)
              FROM cand c JOIN landslides l ON l.id = c.landslide_id, g
        """, (str(ext_id),))
        overlaps = []
        for lid, name, pending, inter, a_ours, a_theirs, dim in cur.fetchall():
            o = {'id': lid, 'name': name, 'pending': pending}
            if dim < 2:
                o.update({'contains': True, 'strong': True, 'score': 1.0})
            else:
                inter = inter or 0.0
                union = (a_ours or 0) + (a_theirs or 0) - inter
                iou = inter / union if union > 0 else 0.0
                of_t = inter / a_theirs if a_theirs else 0.0
                of_o = inter / a_ours if a_ours else 0.0
                # Strong: they substantially share ground. IoU alone undersells
                # one outline nested in another -- their deforming patch inside
                # our larger landslide (Gilbert A: all of theirs inside ours, IoU
                # 0.10) -- so theirs lying mostly inside ours counts, as does a
                # moderate IoU where one shares half or more (Pothole Glacier
                # 2016: 0.56 of theirs, IoU 0.25).
                strong = (iou >= 0.3 or of_t >= 0.8
                          or (max(of_t, of_o) >= 0.5 and iou >= 0.2))
                o.update({'iou': round(iou, 3), 'of_theirs': round(of_t, 3),
                          'of_ours': round(of_o, 3), 'strong': strong,
                          'score': iou + (1.0 if strong else 0.0) + 0.1 * of_t})
            overlaps.append(o)
        overlaps.sort(key=lambda o: (-o['score'], o['id']))
        ids = [r[0] for r in rows] + [o['id'] for o in overlaps]
        links = {}
        if ids and links_ready(cur):
            cur.execute(f'SELECT landslide_id, source, ext_id FROM {LINKS} '
                        f'WHERE landslide_id = ANY(%s) ORDER BY linked_at', (ids,))
            for lid, src, eid in cur.fetchall():
                links.setdefault(lid, []).append([src, eid])
        for o in overlaps:
            o['links'] = links.get(o['id'], [])
            o.pop('score', None)
        conn.rollback()
    finally:
        _put_conn(conn)
    return JsonResponse({'overlaps': overlaps, 'records': [
        {'id': r[0], 'name': r[1], 'pending': r[2], 'distance_m': int(r[3]),
         'links': links.get(r[0], [])}
        for r in rows]})


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
        # A point on the outline for polygon sources (ST_X takes points only);
        # for a point it is the point itself.
        cur.execute(f'SELECT {", ".join(cols)}, ST_X(ST_PointOnSurface(geom)), '
                    f'ST_Y(ST_PointOnSurface(geom)) FROM {t} '
                    f'WHERE ext_id = %s LIMIT 1', (str(ext_id),))
        row = cur.fetchone()
        # ?geom=1: their full outline, for promotion to start from. The map's
        # copy comes out of MapLibre's tiles, clipped and simplified per zoom,
        # so it is not the outline they published. 9 decimals is what Terra
        # Draw and the draw stage accept (see revise.js COORD_PRECISION).
        geom = None
        if row and request.GET.get('geom'):
            cur.execute(f'SELECT ST_AsGeoJSON(geom, 9) FROM {t} WHERE ext_id = %s LIMIT 1',
                        (str(ext_id),))
            g = cur.fetchone()
            geom = json.loads(g[0]) if g and g[0] else None
        # Editors: the record's review too, so the explorer's popup opens with
        # the same Review box the overlay popup has.
        review = (_reviews_for(cur, sid, layer, [str(ext_id)]).get(str(ext_id), {})
                  if row and is_inventory_editor(request.user) else {})
        conn.rollback()
    finally:
        _put_conn(conn)
    if not row:
        return JsonResponse({'error': 'no such record'}, status=404)
    props = {}
    for c, v in zip(cols, row[:len(cols)]):
        if v is None or v == '':
            continue
        props[c] = v.isoformat() if hasattr(v, 'isoformat') else v
    props.update(review)
    lonlat = [row[-2], row[-1]] if row[-2] is not None else None
    return JsonResponse({
        'lonlat': lonlat,
        'source': {'id': sid, 'title': s['title'], 'short': s['short'],
                   'citation': s['citation'], 'url': s['url'],
                   'licence': s['licence'], 'colour': s['colour']},
        'layer': layer, 'labels': labels, 'order': cols, 'properties': props,
        **({'geometry': geom} if geom else {}),
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
        # Editors also get their own review of each record (and whether it was
        # promoted). Never the public: the review is internal (Hig, 2026-11-16).
        reviews = {}
        if is_inventory_editor(request.user) and 'ext_id' in cols:
            ei = cols.index('ext_id') + 1
            reviews = _reviews_for(cur, sid, layer, [r[ei] for r in rows[:limit]])
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
        props = _populated(props)
        if reviews:
            props.update(reviews.get(str(props.get('ext_id')), {}))
        feats.append({'type': 'Feature',
                      'geometry': json.loads(r[0]),
                      'properties': props})
    return JsonResponse({'type': 'FeatureCollection', 'features': feats,
                         'truncated': truncated, 'source': sid, 'layer': layer})


# ---------------------------------------------------------------------------
# REVIEW -- an editor's judgement of one mirrored record
#
# Tables: migrate_external_review (its docstring has the reasoning). Keyed by
# (source, layer, the publisher's id) so a review survives a refetch. A record
# with no row is unreviewed; "promoted" is read live from landslides, never
# stored. Categories are one shared list, grown from the popup's `+`.
#
# Served to editors only: as `_review*` / `_promoted*` properties on the
# features of api_external (the leading underscore cannot collide with a
# publisher column -- `_col` strips it), and through the endpoints below.
# Every function tolerates the tables being absent, so this code can deploy
# before `migrate_external_review` has run in that environment.
# ---------------------------------------------------------------------------
def _review_tables(cur):
    cur.execute("SELECT to_regclass('ext_reviews') IS NOT NULL")
    return cur.fetchone()[0]


def _reviews_for(cur, sid, layer, ext_ids):
    """{ext_id: {_review_id, _review, _review_note, _reviewed_by, _reviewed_at,
    _promoted: [[landslide id, name], ...]}} for the ids given; ids with
    neither a review nor a promotion are absent."""
    ids = sorted({str(x) for x in ext_ids if x is not None})
    out = {}
    if not ids:
        return out
    if _review_tables(cur):
        cur.execute("""
            SELECT r.ext_id, r.category_id, c.label, r.note, r.reviewed_by, r.reviewed_at
              FROM ext_reviews r JOIN ext_review_categories c ON c.id = r.category_id
             WHERE r.source = %s AND r.layer = %s AND r.ext_id = ANY(%s)
        """, (sid, layer, ids))
        for eid, cid, label, note, by, at in cur.fetchall():
            d = out.setdefault(eid, {})
            d.update({'_review_id': cid, '_review': label, '_reviewed_by': by,
                      '_reviewed_at': at.isoformat() if at else None})
            if note:
                d['_review_note'] = note
    # Promotion records only (source, their id), not the layer, so a promotion
    # shows on whichever of the source's layers carries that id.
    if not links_ready(cur):
        return out
    cur.execute(f"""
        SELECT k.ext_id, l.id, l.unique_name FROM {LINKS} k JOIN landslides l ON l.id = k.landslide_id
         WHERE k.source = %s AND k.ext_id = ANY(%s) ORDER BY l.id
    """, (sid, ids))
    for eid, lid, name in cur.fetchall():
        out.setdefault(eid, {}).setdefault('_promoted', []).append([lid, name])
    return out


def _who(request):
    return request.user.get_username() if request.user.is_authenticated else None


@inventory_editor_required
@require_http_methods(['GET', 'POST'])
def api_external_review_categories(request):
    """GET: the shared category list, with how many records each holds.
    POST {"label": "..."}: add one (or return the existing one whose label
    matches ignoring case and outer spaces -- the `+` is pressed mid-review,
    and a near-duplicate category is worse than a silent reuse)."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if not _review_tables(cur):
            conn.rollback()
            return JsonResponse({'error': 'review tables missing: run '
                                 'manage.py migrate_external_review'}, status=503)
        created = None
        if request.method == 'POST':
            try:
                body = json.loads((request.body or b'{}').decode('utf-8'))
            except ValueError:
                body = {}
            label = ' '.join(str(body.get('label') or '').split())[:80]
            if not label:
                conn.rollback()
                return JsonResponse({'error': 'label required'}, status=400)
            cur.execute("""SELECT id FROM ext_review_categories
                            WHERE lower(btrim(label)) = lower(%s)""", (label,))
            row = cur.fetchone()
            if row:
                created = row[0]
            else:
                cur.execute("""INSERT INTO ext_review_categories (label, created_by)
                               VALUES (%s, %s) RETURNING id""", (label, _who(request)))
                created = cur.fetchone()[0]
            conn.commit()
        cur.execute("""
            SELECT c.id, c.label, count(r.ext_id)
              FROM ext_review_categories c
              LEFT JOIN ext_reviews r ON r.category_id = c.id
             GROUP BY c.id, c.label ORDER BY lower(c.label)
        """)
        cats = [{'id': i, 'label': l, 'count': n} for i, l, n in cur.fetchall()]
        conn.rollback()
    finally:
        _put_conn(conn)
    out = {'categories': cats}
    if created is not None:
        out['id'] = created
    return JsonResponse(out)


@inventory_editor_required
@require_http_methods(['POST'])
def api_external_review(request, sid, layer, ext_id):
    """Set one record's review. POST {"category_id": N | null, "note": "..."}.
    null = back to unreviewed (the row is deleted; the log keeps that it was
    reviewed). Note is optional and stored as typed, trimmed."""
    s = SOURCES.get(sid)
    if not s or layer not in s['fetch']['layers']:
        return JsonResponse({'error': 'unknown source or layer'}, status=404)
    try:
        body = json.loads((request.body or b'{}').decode('utf-8'))
        cid = body.get('category_id')
        cid = None if cid in (None, '') else int(cid)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'category_id must be a number or null'}, status=400)
    note = (str(body.get('note') or '').strip() or None)
    eid = str(ext_id)
    who = _who(request)

    conn = _get_conn()
    try:
        cur = conn.cursor()
        if not _review_tables(cur):
            conn.rollback()
            return JsonResponse({'error': 'review tables missing: run '
                                 'manage.py migrate_external_review'}, status=503)
        # The record must exist in the mirror now: a review typed against an id
        # we do not hold is a typo, not a judgement.
        t = table_name(sid, layer)
        cur.execute("SELECT to_regclass(%s)", (t,))
        if not cur.fetchone()[0]:
            conn.rollback()
            return JsonResponse({'error': 'not mirrored here'}, status=404)
        cur.execute(f'SELECT 1 FROM {t} WHERE ext_id = %s LIMIT 1', (eid,))
        if not cur.fetchone():
            conn.rollback()
            return JsonResponse({'error': 'no such record'}, status=404)
        if cid is None:
            cur.execute("""DELETE FROM ext_reviews
                            WHERE source = %s AND layer = %s AND ext_id = %s""",
                        (sid, layer, eid))
            note = None
        else:
            cur.execute('SELECT 1 FROM ext_review_categories WHERE id = %s', (cid,))
            if not cur.fetchone():
                conn.rollback()
                return JsonResponse({'error': f'no category {cid}'}, status=404)
            cur.execute("""
                INSERT INTO ext_reviews (source, layer, ext_id, category_id, note,
                                         reviewed_by, reviewed_at)
                VALUES (%s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (source, layer, ext_id) DO UPDATE
                   SET category_id = EXCLUDED.category_id, note = EXCLUDED.note,
                       reviewed_by = EXCLUDED.reviewed_by, reviewed_at = now()
            """, (sid, layer, eid, cid, note, who))
        cur.execute("""INSERT INTO ext_review_log
                           (source, layer, ext_id, category_id, note, reviewed_by)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (sid, layer, eid, cid, note, who))
        conn.commit()
        rev = _reviews_for(cur, sid, layer, [eid]).get(eid, {})
        conn.rollback()
    finally:
        _put_conn(conn)
    return JsonResponse({'ok': True, 'ext_id': eid, 'review': rev})


# ---------------------------------------------------------------------------
# EXPLORE -- switching the map's Inventory tab to one of these sources
#
# The whole primary layer ships to the browser once (columnar, dictionary-
# encoded, gzipped -- the same trade as /inventory/table/), so every filter,
# count and colour change is local and instant. Each source's `explore` block
# in SOURCES says how: its type field, how its types fall into the shared
# PROCESS FAMILIES below, the fields offered as filters, a second visual
# channel (ring / size / fade), presets, and where the map flies to.
#
# PROCESS FAMILIES are ours, not theirs: a coarse grouping so one colour means
# one kind of process across every outside inventory (a red dot is a rock or
# debris avalanche whoever mapped it). It is a reading aid for the map, never
# written onto their records or ours; their own type is one click away. The
# colours live in ls_colors.js (EXT_FAMILIES), the labels here.
#
# SPELLING FOLD. Publishers' categorical values come in variants that differ
# only in outer spaces or capitals ("High", "High ", " High"; "Small",
# "small"). With `fold_spelling` on (EXPLORE_FOLD_SPELLING, default True; Hig
# 2026-11-16) such values are one filter value, labelled with the commonest
# spelling, and the payload lists the other spellings so the sidebar can say
# so. This is display only: the mirror table and the record popup keep their
# text exactly. Set EXPLORE_FOLD_SPELLING = False to see every spelling as its
# own value. Recorded on /inventory/methods/.
# ---------------------------------------------------------------------------
EXPLORE_FOLD_SPELLING = True

FAMILIES = [
    ('RA',   'Rock or debris avalanche'),
    ('SD',   'Rock / mountain slope deformation'),
    ('RSL',  'Rock slide'),
    ('FALL', 'Fall and topple'),
    ('FLOW', 'Debris flow and flood'),
    ('SOIL', 'Earth and debris slide, earth flow, spread'),
    ('PF',   'Permafrost and periglacial'),
    ('OTH',  'Undifferentiated or other'),
]
_FAM_INDEX = {k: i for i, (k, _l) in enumerate(FAMILIES)}


def _fold(v):
    return str(v).strip().lower()


def family_of(sid, type_value):
    """Process-family key for one of this source's type values."""
    ex = SOURCES[sid].get('explore') or {}
    if type_value is None or str(type_value).strip() == '':
        return 'OTH'
    v = str(type_value).strip()
    fams = ex.get('families', {})
    if ex.get('family_by') == 'code':
        return fams.get(v.split(':', 1)[0].strip(), 'OTH')
    # Matched without regard to outer spaces or case, as the spelling fold is.
    folded = {_fold(k): f for k, f in fams.items()}
    return folded.get(_fold(v), 'OTH')


def _encode_cat(raw, fold):
    """Dictionary-encode one categorical column. Returns (dict, codes,
    variants): `dict` the value labels, `codes` an index per row (-1 = blank),
    `variants` {label: [other spellings]} where the fold merged any."""
    groups, order = {}, []
    for v in raw:
        if v is None or str(v).strip() == '':
            continue
        k = _fold(v) if fold else str(v)
        if k not in groups:
            groups[k] = {}
            order.append(k)
        sv = str(v)
        groups[k][sv] = groups[k].get(sv, 0) + 1
    labels, variants, index = [], {}, {}
    for k in order:
        spell = groups[k]
        best = max(spell, key=lambda x: (spell[x], x))
        label = best.strip() if fold else best
        others = sorted(x for x in spell if x != best)
        index[k] = len(labels)
        labels.append(label)
        if others:
            variants[label] = others
    codes = []
    for v in raw:
        if v is None or str(v).strip() == '':
            codes.append(-1)
        else:
            codes.append(index[_fold(v) if fold else str(v)])
    return labels, codes, variants


def _explore_payload(cur, sid):
    s = SOURCES[sid]
    ex = s['explore']
    t = table_name(sid, ex['layer'])
    fold = EXPLORE_FOLD_SPELLING and ex.get('fold_spelling', True)
    cur.execute("""SELECT column_name FROM information_schema.columns
                    WHERE table_name = %s""", (t,))
    have = {r[0] for r in cur.fetchall()}
    names = [c for c in ex.get('name_fields', []) if c in have]
    name_sql = f"coalesce({', '.join(names)})" if names else 'NULL'
    facets = [f for f in ex['facets'] if f.get('expr') or f.get('field') in have]
    sel = [f'{f["expr"]}' if f.get('expr') else f['field'] for f in facets]
    type_field = ex['type_field'] if ex['type_field'] in have else 'NULL'
    cur.execute(f"""
        SELECT ext_id, {name_sql}, round(ST_X(ST_PointOnSurface(geom))::numeric, 5), round(ST_Y(ST_PointOnSurface(geom))::numeric, 5),
               {type_field}{''.join(', ' + x for x in sel)}
          FROM {t} WHERE geom IS NOT NULL ORDER BY id
    """)
    rows = cur.fetchall()
    n = len(rows)
    out = {
        'source': sid, 'layer': ex['layer'], 'n': n, 'fold_spelling': bool(fold),
        'title': s['title'], 'short': s['short'], 'citation': s['citation'], 'url': s['url'],
        'type_label': ex.get('type_label', 'Type'),
        'families': [{'k': k, 'label': l} for k, l in FAMILIES],
        'ring': ex.get('ring'), 'size': ex.get('size'), 'fade': ex.get('fade'),
        'colour_by': ex.get('colour_by', [['fam', 'Process family']]),
        'presets': ex.get('presets', []), 'home': ex.get('home'),
        'facets': [], 'cols': {}, 'dicts': {}, 'variants': {},
    }
    out['cols']['ext_id'] = [r[0] for r in rows]
    out['cols']['name'] = [r[1] or '' for r in rows]
    out['cols']['lon'] = [float(r[2]) for r in rows]
    out['cols']['lat'] = [float(r[3]) for r in rows]
    tl, tc, tv = _encode_cat([r[4] for r in rows], fold)
    out['dicts']['type'] = tl
    out['cols']['type'] = tc
    out['variants']['type'] = tv
    out['type_family'] = [_FAM_INDEX[family_of(sid, v)] for v in tl]
    for j, f in enumerate(facets):
        lab, codes, var = _encode_cat([r[5 + j] for r in rows], fold)
        key = f['key']
        out['dicts'][key], out['cols'][key], out['variants'][key] = lab, codes, var
        spec = {'key': key, 'label': f['label']}
        if f.get('order'):
            spec['order'] = f['order']
        if f.get('sort'):
            spec['sort'] = f['sort']
        out['facets'].append(spec)
    # The second visual channel reads facet columns by key, so point each at
    # the facet that carries its field.
    by_field = {f.get('field'): f['key'] for f in facets if f.get('field')}
    for ch in ('ring', 'size', 'fade'):
        if out[ch]:
            out[ch] = dict(out[ch], key=by_field.get(out[ch]['field']))
    return out


@require_http_methods(['GET'])
def api_external_explore(request, sid):
    """One source's primary layer, whole, for the Inventory-tab explorer.
    Public, like the mirror itself. Cached per process and keyed on the
    mirror's fetch time, so a refetch (a separate manage.py process, which
    cannot clear our cache) is picked up on the next request."""
    import gzip
    from . import views as _v
    s = SOURCES.get(sid)
    if not s or not s.get('explore'):
        return JsonResponse({'error': 'unknown source'}, status=404)
    t = table_name(sid, s['explore']['layer'])
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass(%s)", (t,))
        if not cur.fetchone()[0]:
            conn.rollback()
            return JsonResponse({'error': 'not mirrored here'}, status=404)
        cur.execute(f'SELECT max(fetched_at) FROM {t}')
        stamp = str(cur.fetchone()[0])
        key = f'ext_explore:{sid}'
        entry = _v._cache.get(key)
        if not entry or entry.get('stamp') != stamp or entry.get('fold') != EXPLORE_FOLD_SPELLING:
            body = json.dumps(_explore_payload(cur, sid), separators=(',', ':'),
                              allow_nan=False).encode('utf-8')
            entry = {'stamp': stamp, 'fold': EXPLORE_FOLD_SPELLING,
                     'raw': body, 'gz': gzip.compress(body, 6)}
            _v._cache[key] = entry
        conn.rollback()
    finally:
        _put_conn(conn)
    from django.http import HttpResponse
    if 'gzip' in request.META.get('HTTP_ACCEPT_ENCODING', ''):
        resp = HttpResponse(entry['gz'], content_type='application/json')
        resp['Content-Encoding'] = 'gzip'
    else:
        resp = HttpResponse(entry['raw'], content_type='application/json')
    resp['Cache-Control'] = 'no-cache'
    resp['Vary'] = 'Accept-Encoding'
    return resp


@inventory_editor_required
@require_http_methods(['GET'])
def api_external_reviews(request, sid, layer):
    """Every review and promotion on one layer, for the explorer's Review
    filter: {categories, reviews: {ext_id: category_id}, promoted: {ext_id:
    [[landslide id, name], ...]}}. Small (one row per reviewed record), so it
    is not cached and a review saved a moment ago is always in it."""
    if sid not in SOURCES or layer not in SOURCES[sid]['fetch']['layers']:
        return JsonResponse({'error': 'unknown source or layer'}, status=404)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cats, revs = [], {}
        if _review_tables(cur):
            cur.execute('SELECT id, label FROM ext_review_categories ORDER BY lower(label)')
            cats = [{'id': i, 'label': l} for i, l in cur.fetchall()]
            cur.execute("""SELECT ext_id, category_id FROM ext_reviews
                            WHERE source = %s AND layer = %s""", (sid, layer))
            revs = {e: c for e, c in cur.fetchall()}
        prom = {}
        if links_ready(cur):
            cur.execute(f"""SELECT k.ext_id, l.id, l.unique_name
                              FROM {LINKS} k JOIN landslides l ON l.id = k.landslide_id
                             WHERE k.source = %s ORDER BY l.id""", (sid,))
            for e, i, nm in cur.fetchall():
                prom.setdefault(e, []).append([i, nm])
        conn.rollback()
    finally:
        _put_conn(conn)
    return JsonResponse({'categories': cats, 'reviews': revs, 'promoted': prom})
