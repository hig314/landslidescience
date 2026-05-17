"""Template context processors for the inventory app.

Exposes role flags on every template so navigation can conditionally show
editor-only links without requiring per-view context setup.
"""
from .auth import is_inventory_editor, is_site_admin


def user_roles(request):
    return {
        'is_inventory_editor': is_inventory_editor(request.user),
        'is_site_admin':       is_site_admin(request.user),
    }
