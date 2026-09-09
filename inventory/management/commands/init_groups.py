"""Idempotently provision the inventory_viewers, inventory_editors and
site_admins Groups.

Usage:
    python manage.py init_groups

Safe to run repeatedly. Adds permissions, never removes them.
"""
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

from files.models import HostedFile
from inventory.auth import (GROUP_INVENTORY_EDITORS, GROUP_INVENTORY_VIEWERS,
                            GROUP_SITE_ADMINS)
from pages.models import Page


class Command(BaseCommand):
    help = 'Create the inventory_viewers, inventory_editors and site_admins Groups (idempotent).'

    def handle(self, *args, **options):
        # inventory_viewers: read-only access to the restricted surfaces (the
        # non-public QMS layers and the baked imagery tiles). No Django built-in
        # permissions and no is_staff: membership is checked directly by
        # can_view_restricted(). Editors satisfy that check implicitly, so a
        # user never needs to be in both.
        viewers, vc = Group.objects.get_or_create(name=GROUP_INVENTORY_VIEWERS)
        self.stdout.write(
            f"  {GROUP_INVENTORY_VIEWERS}: {'created' if vc else 'already exists'}"
        )

        # inventory_editors: no Django built-in permissions; the custom
        # @inventory_editor_required decorator checks group membership directly.
        editors, ec = Group.objects.get_or_create(name=GROUP_INVENTORY_EDITORS)
        self.stdout.write(
            f"  {GROUP_INVENTORY_EDITORS}: {'created' if ec else 'already exists'}"
        )

        # site_admins: full CRUD on the Page model so they can edit homepage etc.
        # Members also need is_staff=True (a User flag) to access /admin/ at all.
        admins, ac = Group.objects.get_or_create(name=GROUP_SITE_ADMINS)
        page_ct = ContentType.objects.get_for_model(Page)
        page_perms = Permission.objects.filter(
            content_type=page_ct,
            codename__in=['add_page', 'change_page', 'delete_page', 'view_page'],
        )
        admins.permissions.add(*page_perms)

        # site_admins also get full CRUD on HostedFile (the `files` app) so they
        # can publish files at /files/<name> via /admin/files/hostedfile/.
        file_ct = ContentType.objects.get_for_model(HostedFile)
        file_perms = Permission.objects.filter(
            content_type=file_ct,
            codename__in=['add_hostedfile', 'change_hostedfile',
                          'delete_hostedfile', 'view_hostedfile'],
        )
        admins.permissions.add(*file_perms)
        self.stdout.write(
            f"  {GROUP_SITE_ADMINS}: {'created' if ac else 'already exists'} "
            f"with {page_perms.count()} Page + {file_perms.count()} HostedFile permissions"
        )
        self.stdout.write(self.style.SUCCESS('Done.'))
        self.stdout.write(
            '\nTo add a user to a group, use the Django admin (/admin/auth/group/), '
            'the shell, or `manage.py add_viewer` for the view-only case.\n'
            '\nLOGIN: collaborators sign in at /inventory/login/ and do NOT need '
            'is_staff. Only site_admins need is_staff=True, because only they use '
            '/admin/. (Before the /inventory/login/ view existed, /admin/login/ was '
            'the only form on the site and it rejects non-staff accounts, which is '
            'why a view-only user appeared unable to log in at all.)'
        )
