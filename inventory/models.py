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


class QmsLayer(models.Model):
    """An admin-curated QuickMapServices basemap promoted for shared use, so it
    appears as a selectable basemap for other users (not just in the curator's
    own browser localStorage). `public=True` makes it visible to everyone;
    `public=False` makes it visible to inventory editors (data admins) only.
    Stored in the app SQLite — it's config, not landslide data."""
    qms_id = models.IntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=200)
    tile_url = models.TextField()
    epsg = models.IntegerField(null=True, blank=True)
    scheme = models.CharField(max_length=8, default='xyz')   # 'xyz' | 'tms'
    z_min = models.IntegerField(default=0)
    z_max = models.IntegerField(default=19)
    attribution = models.TextField(blank=True, default='')
    public = models.BooleanField(default=False)              # True=everyone, False=editors only
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'QMS basemap layer'
        verbose_name_plural = 'QMS basemap layers'

    def __str__(self):
        return f'{self.name} (QMS #{self.qms_id}, {"public" if self.public else "editors"})'
