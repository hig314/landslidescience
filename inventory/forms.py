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
            fields[name] = forms.DateField(
                required=required,
                widget=forms.DateInput(attrs={'type': 'date'}),
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
    return type('LandslideEditForm', (_LandslideEditFormBase,), fields)
