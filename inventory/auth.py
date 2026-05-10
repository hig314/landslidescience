"""Authorization helpers for the inventory app.

Two roles:
- inventory_editors: members can use /inventory/manage/* to edit landslide records
- site_admins: members can use Django /admin/ to edit Page content (need is_staff=True too)

Superusers bypass both checks.
"""
from functools import wraps

from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse


GROUP_INVENTORY_EDITORS = 'inventory_editors'
GROUP_SITE_ADMINS = 'site_admins'


def _user_in_group(user, group_name):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=group_name).exists()


def is_inventory_editor(user):
    return _user_in_group(user, GROUP_INVENTORY_EDITORS)


def is_site_admin(user):
    return _user_in_group(user, GROUP_SITE_ADMINS)


def inventory_editor_required(view_func):
    """Require login + membership in inventory_editors (or superuser).

    Anonymous users go to the admin login page (where staff sign in). Logged-in
    users without the role get a 403, since logging in won't help them.
    """
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            login_url = reverse('admin:login')
            return redirect(f'{login_url}?next={request.get_full_path()}')
        if is_inventory_editor(request.user):
            return view_func(request, *args, **kwargs)
        return HttpResponseForbidden(
            'Your account does not have inventory editor permissions. '
            'Contact a superuser to be added to the inventory_editors group.'
        )
    return wrapped
