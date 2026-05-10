from django.contrib import admin

from .models import Page


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ('slug', 'title', 'updated_at')
    fields = ('slug', 'title', 'body')
    readonly_fields = ()
