"""New accounts start as data users.

Every account on this site is a collaborator (there is no public sign-up), so
the sensible floor is read access to the restricted surfaces. Editor and
site-admin roles stay explicit grants. Only creation is hooked: removing an
existing user from data_users sticks.
"""
from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .auth import GROUP_DATA_USERS


@receiver(post_save, sender=settings.AUTH_USER_MODEL,
          dispatch_uid='inventory_default_data_user')
def add_new_user_to_data_users(sender, instance, created, raw=False, **kwargs):
    if not created or raw:
        return
    from django.contrib.auth.models import Group

    def _add():
        group, _ = Group.objects.get_or_create(name=GROUP_DATA_USERS)
        instance.groups.add(group)
    # After commit: the admin's add view saves the user and then its m2m
    # fields in the same transaction, so adding immediately could be undone
    # by a later groups save.
    transaction.on_commit(_add)
