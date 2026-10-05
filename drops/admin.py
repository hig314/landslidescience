from django.contrib import admin

from .models import Drop, DropFile


@admin.register(Drop)
class DropAdmin(admin.ModelAdmin):
    list_display = ('title', 'slug', 'created_by', 'created_at', 'expires_at', 'closed')
    list_filter = ('closed',)
    search_fields = ('title', 'slug')
    readonly_fields = ('slug', 'created_at', 'passphrase_hash')


@admin.register(DropFile)
class DropFileAdmin(admin.ModelAdmin):
    list_display = ('path', 'drop', 'size', 'taken_at', 'thumb_state', 'uploaded_at')
    list_filter = ('drop', 'thumb_state')
    search_fields = ('path',)
    readonly_fields = ('key', 'etag', 'thumb_key')
