"""Authorization helpers for the inventory app.

Three roles:
- inventory_viewers: members see the restricted READ-ONLY surfaces (non-public
  QMS layers, baked imagery tiles) but cannot edit anything. For collaborators
  who need the proprietary layers and the data, not the keys.
- inventory_editors: members can use /inventory/manage/* to edit landslide records
- site_admins: members can use Django /admin/ to edit Page content (need is_staff=True too)

Superusers bypass every check.

NOTE `is_staff` is NOT an inventory role and is not consulted here. It controls
Django admin access only. A viewer needs no staff flag: they sign in at
/inventory/login/, not at /admin/login/ (which rejects non-staff by design --
that is why an "active but not staff" account appeared unable to log in at all
before this login view existed).
"""
from functools import wraps

from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse


GROUP_INVENTORY_VIEWERS = 'inventory_viewers'
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


def can_view_restricted(user):
    """May this user see the restricted read-only surfaces?

    Viewers and editors both may; editors get it implicitly because every
    editor is trusted with at least as much as a viewer. Gate READ-ONLY
    proprietary content on this, never on is_inventory_editor -- conflating the
    two is what left browsing and editing as a single indivisible privilege.
    """
    return (_user_in_group(user, GROUP_INVENTORY_VIEWERS)
            or is_inventory_editor(user))


def inventory_editor_required(view_func):
    """Require login + membership in inventory_editors (or superuser).

    Anonymous users go to the COLLABORATOR sign-in, not Django's /admin/login/.
    That was the old target and it is unreachable for an editor without
    is_staff: the admin form rejects the account even with correct credentials,
    so the only editors it ever worked for were the ones who happened to also
    be staff. Logged-in users without the role get a 403, since signing in
    again will not help them.
    """
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            login_url = reverse('inventory:login')
            return redirect(f'{login_url}?next={request.get_full_path()}')
        if is_inventory_editor(request.user):
            return view_func(request, *args, **kwargs)
        return HttpResponseForbidden(
            'Your account does not have inventory editor permissions. '
            'Contact a superuser to be added to the inventory_editors group.'
        )
    return wrapped
