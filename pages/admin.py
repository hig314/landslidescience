from django.contrib import admin
from django.utils.html import format_html

from .models import Page, SavedComparison


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ('slug', 'title', 'updated_at')
    fields = ('slug', 'title', 'body')
    readonly_fields = ()


@admin.register(SavedComparison)
class SavedComparisonAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'open_link', 'saved_by', 'updated_at')
    search_fields = ('name', 'code', 'notes')
    fields = ('name', 'code', 'notes', 'query', 'saved_by', 'created_at', 'updated_at')
    readonly_fields = ('saved_by', 'created_at', 'updated_at')

    @admin.display(description='Open')
    def open_link(self, obj):
        return format_html('<a href="/lidar/compare/s/{}/" target="_blank">open</a>', obj.code)
