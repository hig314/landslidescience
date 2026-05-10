"""Forms for the inventory editor UI.

Hand-built (not ModelForm) because we don't have Django models for the
landslide tables — they live in PostGIS, accessed via raw psycopg2.
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
    'Catastrophic',
    'Catastrophic Modern',
    'Catastrophic Holocene',
    'Catastrophic Obvious creep',
    'Catastrophic Patchy obvious creep',
    'Catastrophic Subtle creep',
    'Catastrophic Geomorph creep',
    'Small catastrophic landslide',
]


class LandslideEditForm(forms.Form):
    unique_name      = forms.CharField(max_length=200)
    landslide_type   = forms.ChoiceField(choices=LANDSLIDE_TYPE_CHOICES)
    landslide_class  = forms.CharField(max_length=200, required=False)
    inventory_subset = forms.CharField(max_length=100, required=False)
    description      = forms.CharField(widget=forms.Textarea(attrs={'rows': 4}), required=False)

    volume_preferred = forms.FloatField(required=False, min_value=0)
    volume_method    = forms.CharField(max_length=200, required=False)

    year_text        = forms.CharField(max_length=100, required=False,
                                       help_text="Free text. For 4-digit year, the histogram parses automatically.")
    date_min         = forms.DateField(required=False,
                                       widget=forms.DateInput(attrs={'type': 'date'}))
    date_max         = forms.DateField(required=False,
                                       widget=forms.DateInput(attrs={'type': 'date'}))
    seismic_datetime = forms.DateTimeField(required=False,
                                           widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
                                           input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'])

    molards                     = forms.BooleanField(required=False)
    stream_damming              = forms.CharField(max_length=200, required=False)
    exclusively_supraglacial    = forms.BooleanField(required=False)
    creeping_permafrost_mass    = forms.BooleanField(required=False)
    post_2012_activity_increase = forms.BooleanField(required=False)
    size_inclusion              = forms.BooleanField(required=False)

    def clean(self):
        cleaned = super().clean()
        d_min, d_max = cleaned.get('date_min'), cleaned.get('date_max')
        if d_min and d_max and d_max < d_min:
            self.add_error('date_max', 'date_max must be on or after date_min')
        return cleaned
