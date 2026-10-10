"""Django admin: the User list shows each account's site role.

Roles are group memberships (inventory/auth.py), which the stock User list
hides behind each change form. The Roles column and filter make "who can do
what" readable at a glance, and flag signed-in accounts with no role at all.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User

from .auth import ROLE_GROUPS

admin.site.unregister(User)


class RoleFilter(admin.SimpleListFilter):
    title = 'role'
    parameter_name = 'role'

    def lookups(self, request, model_admin):
        return [(g, g) for g in ROLE_GROUPS] + [('none', '(no role)')]

    def queryset(self, request, queryset):
        v = self.value()
        if v == 'none':
            return (queryset.filter(is_superuser=False)
                    .exclude(groups__name__in=ROLE_GROUPS).distinct())
        if v in ROLE_GROUPS:
            return queryset.filter(groups__name=v).distinct()
        return queryset


from .models import FeatureVocab


@admin.register(FeatureVocab)
class FeatureVocabAdmin(admin.ModelAdmin):
    list_display = ('value', 'applies_to', 'sort_order', 'legacy_column', 'description')
    list_editable = ('applies_to', 'sort_order', 'description')
    ordering = ('sort_order', 'value')


@admin.register(User)
class RoleUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'first_name', 'last_name',
                    'roles', 'is_staff', 'is_active', 'last_login')
    list_filter = (RoleFilter,) + UserAdmin.list_filter

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('groups')

    @admin.display(description='Roles')
    def roles(self, obj):
        names = [g.name for g in obj.groups.all()]
        roles = [g for g in ROLE_GROUPS if g in names]
        if obj.is_superuser:
            roles.insert(0, 'superuser')
        return ', '.join(roles) or '— none —'
