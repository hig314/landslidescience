"""Create (or update) a view-only collaborator account.

    python manage.py add_viewer <username> [--email a@b.c] [--password ...]

Grants: browse everything including the restricted QMS layers and baked
imagery, plus the data download (which is public anyway). Grants no edit
rights and no admin access — the account is deliberately NOT is_staff, and
signs in at /inventory/login/.

Idempotent: re-running adds the group to an existing user and, with
--password, resets their password. Run it once per environment (dev and prod
have separate databases).
"""
import getpass

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError

from inventory.auth import GROUP_INVENTORY_VIEWERS


class Command(BaseCommand):
    help = 'Create or update a view-only inventory account.'

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('--email', default='')
        parser.add_argument('--password', default=None,
                            help='Set/reset the password. Omitted: prompt.')

    def handle(self, *args, **o):
        try:
            group = Group.objects.get(name=GROUP_INVENTORY_VIEWERS)
        except Group.DoesNotExist:
            raise CommandError(
                f'Group "{GROUP_INVENTORY_VIEWERS}" does not exist. '
                'Run `manage.py init_groups` first.')

        user, created = User.objects.get_or_create(
            username=o['username'], defaults={'email': o['email']})
        pw = o['password']
        if pw is None and created:
            pw = getpass.getpass('Password: ')
            if pw != getpass.getpass('Confirm: '):
                raise CommandError('Passwords did not match.')
        if pw:
            user.set_password(pw)

        if o['email']:
            user.email = o['email']
        user.is_active = True
        # Explicitly NOT staff: a viewer has no business in /admin/, and
        # is_staff is not what grants inventory access.
        user.is_staff = False
        user.is_superuser = False
        user.save()
        user.groups.add(group)

        self.stdout.write(self.style.SUCCESS(
            f'{"Created" if created else "Updated"} viewer "{user.username}"'
            f'{" (password set)" if pw else ""}.'))
        self.stdout.write(
            f'  groups: {", ".join(g.name for g in user.groups.all())}\n'
            f'  is_staff={user.is_staff}  is_active={user.is_active}\n'
            '  They sign in at /inventory/login/ — not /admin/.')
