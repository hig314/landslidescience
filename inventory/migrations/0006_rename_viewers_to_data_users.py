"""Rename the inventory_viewers group to data_users.

A rename, not create-and-delete, so members and any permissions carry over.
If both groups already exist (init_groups run with the new code before this
migration), the old group's members are moved across and it is removed.
Existing role-less accounts are deliberately NOT backfilled: only newly
created accounts join data_users by default (inventory/signals.py).
"""
from django.db import migrations

OLD, NEW = 'inventory_viewers', 'data_users'


def _rename(apps, frm, to):
    Group = apps.get_model('auth', 'Group')
    old = Group.objects.filter(name=frm).first()
    if old is None:
        return
    new = Group.objects.filter(name=to).first()
    if new is None:
        old.name = to
        old.save(update_fields=['name'])
        return
    new.user_set.add(*old.user_set.all())
    new.permissions.add(*old.permissions.all())
    old.delete()


def forward(apps, schema_editor):
    _rename(apps, OLD, NEW)


def backward(apps, schema_editor):
    _rename(apps, NEW, OLD)


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0005_traceraster_public_traceraster_source_ref'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [migrations.RunPython(forward, backward)]
