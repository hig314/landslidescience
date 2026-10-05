from django import forms
from django.contrib import admin

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

    @admin.display(description='Upload link')
    def upload_path(self, obj):
        return f'/drops/{obj.slug}/' if obj.pk else '(saved first)'


@admin.register(DropFile)
class DropFileAdmin(admin.ModelAdmin):
    list_display = ('path', 'drop', 'size', 'taken_at', 'thumb_state', 'uploaded_at')
    list_filter = ('drop', 'thumb_state')
    search_fields = ('path',)
    readonly_fields = ('key', 'etag', 'thumb_key')
