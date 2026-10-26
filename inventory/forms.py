"""Forms for the inventory editor UI.

The landslide edit form is built dynamically from information_schema so adding
or renaming a Postgres column doesn't require code changes. See
`build_landslide_form_class(cols_meta)` for the factory.
"""
from django import forms


LANDSLIDE_TYPE_CHOICES = [
    ('slow', 'Slow'),
    ('catastrophic', 'Catastrophic'),
]

# Common class values shown in the datalist; not enforced (new values are allowed).
COMMON_CLASS_VALUES = [
    'Slow Obvious creep',
    'Slow Patchy obvious creep',
    'Slow Subtle creep',
    'Slow Geomorph creep',
    'Small slow landslide',
    'Catastrophic Cryptic',
    'Catastrophic Modern',
    'Catastrophic Holocene',
    'Catastrophic Obvious creep',
    'Catastrophic Patchy obvious creep',
    'Catastrophic Subtle creep',
    'Catastrophic Geomorph creep',
    'Small catastrophic landslide',
]

# Columns rendered as multi-line textareas instead of one-line inputs.
_TEXTAREA_COLS = {'description', 'notes', 'seismic_note', 'seismic_credit',
                  'other_subtle_creep', 'ongoing_work'}

# Label overrides. Django derives a label from the column name
# (super_elevated_deposits -> "Super elevated deposits"), which is fine for
# most columns but loses hyphenation and domain capitalization. Only list
# columns whose derived label is actually wrong — the default is good.
_FIELD_LABELS = {
    'insar_schaefer': 'InSAR (Schaefer)',
    'insar_kim': 'InSAR (Kim)',
    'insar_opera': 'InSAR (OPERA)',
    'insar_other': 'InSAR (other)',
    'planet_labs_creep': 'Planet Labs creep',
    'planet_labs_patchy_creep': 'Patchy only',   # nested under Planet Labs creep in the form
    'its_live_creep': 'ITS_LIVE',
    'other_subtle_creep': 'Other subtle creep evidence',
    'features': 'Features',   # under the 'Landslide features' group heading
}

# Short hints shown under a field. Kept for flags whose meaning is not
# self-evident from the label alone.
_FIELD_HELP = {
}


# Hover tooltips (the label's `title`), for what a box means and what ticking
# it does to the derived class -- there when wanted, without a line of grey
# text under every checkbox in a two-column block. The creep section's flags
# are all here because each one feeds a rule (creep_behavior, insar_creep) and
# which label it leads to is the thing an editor cannot see from its name.
# Keep these in step with the rule ladder in derived.compute_creep_behavior
# and the class table on the Methods page; they restate those, nothing more.
_FIELD_TIPS = {
    'creep_evaluated': 'This record has been reviewed for creep. With nothing '
                       'ticked below it reads as Cryptic (reviewed, none '
                       'found) rather than not yet reviewed.',
    'insar_schaefer': 'Creep detected in the Schaefer InSAR survey — counts '
                      'as subtle creep.',
    'insar_kim': 'Creep detected in the Kim InSAR survey — counts as subtle '
                 'creep.',
    'insar_opera': 'Creep apparent in the OPERA DISP-S1 (Sentinel-1) velocity '
                   'mosaics — counts as subtle creep.',
    'insar_other': 'Creep detected by InSAR from any other source — counts '
                   'as subtle creep.',
    'planet_labs_creep': 'Creep is apparent in Planet imagery — counts as '
                         'obvious creep, taken to be landslide-wide unless '
                         '"Patchy only" is ticked.',
    'planet_labs_patchy_creep': 'The creep seen in Planet imagery is confined '
                                'to parts of the feature — counts as patchy '
                                'obvious creep instead of landslide-wide.',
    'its_live_creep': 'Motion shows in the ITS_LIVE velocity fields — counts '
                      'as obvious creep.',
    'geomorph_creep': 'Geomorphic indicators of past or very slow creep '
                      '(e.g. lobate morphology) — counts as geomorph creep '
                      'when no stronger evidence is ticked.',
    'other_subtle_creep': 'Subtle creep evidence other than InSAR: pick any '
                          'that apply, or add a value. Any value counts as '
                          'subtle creep.',
    'stream_damming': 'What the deposit did to the stream it reached: pick '
                      'any that apply, or add a value.',
    'features': 'Observed features of this landslide: pick any that apply, '
                'or add a value. The list shows the features that apply to '
                'this record\'s type.',
}


# Fields a record needs before it shows on the public map properly. These are
# not database requirements: they are what the class rule
# (derived.compute_landslide_class, via compute_creep_behavior and
# _resolve_event_era) needs to produce a class the map knows (map.js
# CLASS_ORDER, views._CLASS_COLOR). Missing one has two outcomes, both silent:
# a NULL class draws magenta as incomplete, and a class the map has no row for
# (a bare 'Catastrophic', 'Slow Cryptic') is filtered out and does not draw at
# all -- Tatonduk River C 2018, 2026-10-26. The form marks each with a red *
# whose tooltip is the text here; "one of" fields say so.
#
# HAND-KEPT: whenever the class rule, the creep_behavior ladder, the era
# resolution or the map's class list changes, re-derive this table -- which
# inputs a displayable class now depends on -- and update the tips.
_SLOW_CREEP_REQ = ('Needed for the map (slow records): at least one creep-'
                   'evidence box or value. Without one the record draws '
                   'magenta as incomplete. "Creep evaluated" alone is not '
                   'enough on a slow record: it gives "Slow Cryptic", which '
                   'the map does not show at all.')
_AGE_REQ = ('Needed for the map (catastrophic records): an age -- a 4-digit '
            'year or Holocene/Modern in Year text, a date min, or a seismic '
            'time; one of the three. Without one the record draws magenta as '
            'incomplete.')
DISPLAY_REQUIRED = {
    'landslide_type': 'Needed for the map: decides which rules classify the record.',
    'year_text': _AGE_REQ,
    'date_min': _AGE_REQ,
    'seismic_datetime': _AGE_REQ,
    'creep_evaluated': ('Needed for the map (full-size catastrophic records '
                        'dated 2012 or later): tick this, or record creep '
                        'evidence below. Without it the class is a bare '
                        '"Catastrophic", which the map does not show at all. '
                        'On slow records see the creep-evidence fields.'),
    'geomorph_creep': _SLOW_CREEP_REQ,
    'insar_schaefer': _SLOW_CREEP_REQ,
    'insar_kim': _SLOW_CREEP_REQ,
    'insar_opera': _SLOW_CREEP_REQ,
    'insar_other': _SLOW_CREEP_REQ,
    'other_subtle_creep': _SLOW_CREEP_REQ,
    'its_live_creep': _SLOW_CREEP_REQ,
    'planet_labs_creep': _SLOW_CREEP_REQ,
}


class _LandslideEditFormBase(forms.Form):
    """Base form: shared validation (date ordering) for the dynamically-built subclass."""
    def clean(self):
        cleaned = super().clean()
        d_min, d_max = cleaned.get('date_min'), cleaned.get('date_max')
        if d_min and d_max and d_max < d_min:
            self.add_error('date_max', 'date_max must be on or after date_min')
        return cleaned


def build_landslide_form_class(cols_meta, all_optional=False, exclude=None):
    """Build a Form class with one field per landslide column.

    `cols_meta` is a list of dicts produced by views._discover_editable_columns:
        [{'name': 'unique_name', 'udt': 'text', 'nullable': False, 'max_length': None}, ...]

    Maps Postgres udt_name → Django form field. Unknown types fall back to
    CharField. Boolean fields are always optional. NOT NULL columns get
    required=True unless `all_optional=True` (used by the import-apply
    "common values" form, where leaving fields blank means "don't impose
    this on the batch").

    `exclude` is a set of column names to skip entirely — useful for the
    import-apply form which excludes rule-populated columns and
    unique-per-record columns.
    """
    exclude = exclude or set()
    fields = {}
    for c in cols_meta:
        if c['name'] in exclude:
            continue
        name, udt, nullable, max_len = c['name'], c['udt'], c['nullable'], c.get('max_length')
        required = (not nullable) and not all_optional
        if name == 'landslide_type':
            fields[name] = forms.ChoiceField(
                choices=[('', '— none —')] + LANDSLIDE_TYPE_CHOICES if all_optional
                        else LANDSLIDE_TYPE_CHOICES,
                required=required,
            )
            continue
        if udt == 'text':
            kwargs = {'required': required}
            if max_len:
                kwargs['max_length'] = max_len
            if name in _TEXTAREA_COLS:
                kwargs['widget'] = forms.Textarea(attrs={'rows': 4})
            fields[name] = forms.CharField(**kwargs)
        elif udt == 'bool':
            fields[name] = forms.BooleanField(required=False)
        elif udt in ('int4', 'int8'):
            kwargs = {'required': required}
            if 'volume' in name:
                kwargs['min_value'] = 0
            fields[name] = forms.IntegerField(**kwargs)
        elif udt == 'float8':
            fields[name] = forms.FloatField(required=required)
        elif udt == 'date':
            # Typeable text (not the browser date-picker) so the editor can
            # enter the unambiguous "14-Sep-2010" form (and ISO still works).
            fields[name] = forms.DateField(
                required=required,
                input_formats=['%d-%b-%Y', '%d %b %Y', '%d-%B-%Y', '%d %B %Y',
                               '%Y-%m-%d', '%m/%d/%Y'],
                widget=forms.DateInput(format='%d-%b-%Y',
                                       attrs={'placeholder': 'e.g. 14-Sep-2010'}),
            )
        elif udt == 'timestamptz':
            fields[name] = forms.DateTimeField(
                required=required,
                widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
                input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S',
                               '%Y-%m-%dT%H:%M:%S'],
            )
        else:
            # Unknown type — fall back to text so the editor isn't blocked.
            fields[name] = forms.CharField(required=required)
        if name in _FIELD_LABELS:
            fields[name].label = _FIELD_LABELS[name]
        if name in _FIELD_HELP:
            fields[name].help_text = _FIELD_HELP[name]
        if name in _FIELD_TIPS:
            fields[name].tip = _FIELD_TIPS[name]     # read as field.field.tip
        if name in DISPLAY_REQUIRED and not all_optional:
            fields[name].display_req = DISPLAY_REQUIRED[name]   # field.field.display_req
    return type('LandslideEditForm', (_LandslideEditFormBase,), fields)
