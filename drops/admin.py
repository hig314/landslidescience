import threading

from django import forms
from django.contrib import admin
from django.utils.html import format_html

from .models import Drop, DropFile


class DropAdminForm(forms.ModelForm):
    """The plain passphrase is never stored, so the admin form carries a
    write-only field that hashes into passphrase_hash on save."""
    passphrase = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False),
        help_text="Optional second lock on the upload link. Leave blank to keep "
                  "the current one (if any).")
    clear_passphrase = forms.BooleanField(
        required=False, help_text="Remove the passphrase so the link alone suffices.")

    class Meta:
        model = Drop
        fields = ['title', 'note', 'created_by', 'expires_at', 'closed']

    def save(self, commit=True):
        d = super().save(commit=False)
        if self.cleaned_data.get('clear_passphrase'):
            d.set_passphrase('')
        elif self.cleaned_data.get('passphrase'):
            d.set_passphrase(self.cleaned_data['passphrase'])
        if commit:
            d.save()
        return d


@admin.register(Drop)
class DropAdmin(admin.ModelAdmin):
    form = DropAdminForm
    list_display = ('title', 'slug', 'created_by', 'created_at', 'expires_at',
                    'closed', 'passphrase_set')
    list_filter = ('closed',)
    search_fields = ('title', 'slug')
    readonly_fields = ('slug', 'upload_path', 'created_at', 'passphrase_set')
    fields = ('title', 'note', 'created_by', 'expires_at', 'closed',
              'passphrase', 'clear_passphrase', 'passphrase_set',
              'slug', 'upload_path', 'created_at')

    @admin.display(boolean=True, description='Passphrase set')
    def passphrase_set(self, obj):
        return obj.has_passphrase()

    # The absolute link needs the request's host; admin.display methods only
    # get the object, so changeform_view parks the base URL per thread.
    _tl = threading.local()

    def changeform_view(self, request, *args, **kwargs):
        self._tl.base = request.build_absolute_uri('/').rstrip('/')
        return super().changeform_view(request, *args, **kwargs)

    @admin.display(description='Upload link')
    def upload_path(self, obj):
        if not obj.pk:
            return '(appears once saved)'
        url = f'{getattr(self._tl, "base", "")}/drops/{obj.slug}/'
        return format_html('<a href="{0}" target="_blank" rel="noopener" '
                           'style="font-family:monospace">{0}</a>', url)


@admin.register(DropFile)
class DropFileAdmin(admin.ModelAdmin):
    list_display = ('path', 'drop', 'size', 'taken_at', 'thumb_state', 'uploaded_at')
    list_filter = ('drop', 'thumb_state')
    search_fields = ('path',)
    readonly_fields = ('key', 'etag', 'thumb_key')
