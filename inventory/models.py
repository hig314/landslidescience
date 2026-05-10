"""Django models for the inventory app.

We intentionally do NOT mirror the PostGIS landslides table here — that's the
single source of truth, accessed via raw psycopg2 in views. Models in this
file are for editor metadata only (who edited what, when), kept in the
landslidescience SQLite so the PostGIS schema stays under Tethys control.
"""
from django.conf import settings
from django.db import models


class LandslideEditMeta(models.Model):
    """Audit metadata for a landslide record.

    One row per landslide_id (unique). Updated each time an editor saves
    changes via /inventory/manage/<id>/. The landslide_id is a foreign key
    in spirit only — it references PostGIS, which Django doesn't manage.
    """
    landslide_id = models.IntegerField(unique=True, db_index=True)
    last_edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='+',
    )
    last_edited_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'landslide edit metadata'
        verbose_name_plural = 'landslide edit metadata'

    def __str__(self):
        return f'landslide {self.landslide_id} edited by {self.last_edited_by} at {self.last_edited_at:%Y-%m-%d %H:%M}'
