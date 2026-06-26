from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import HostedFile


@admin.register(HostedFile)
class HostedFileAdmin(admin.ModelAdmin):
    list_display = ('name', 'title', 'public_link', 'size', 'inline', 'uploaded_at')
    search_fields = ('name', 'title', 'description')
    readonly_fields = ('public_link', 'size', 'uploaded_at', 'updated_at')
    fields = ('file', 'name', 'title', 'description', 'inline', 'content_type',
              'public_link', 'size', 'uploaded_at', 'updated_at')

    @admin.display(description='Public URL')
    def public_link(self, obj):
        if not obj.pk or not obj.name:
            return '—'
        url = reverse('hosted_file', args=[obj.name])
        return format_html('<a href="{}" target="_blank">{}</a>', url, url)

    @admin.display(description='Size')
    def size(self, obj):
        try:
            n = obj.file.size
        except (ValueError, OSError):
            return '—'
        for unit in ('B', 'KB', 'MB', 'GB'):
            if n < 1024 or unit == 'GB':
                return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
            n /= 1024
