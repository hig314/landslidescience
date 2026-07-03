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


class TraceRaster(models.Model):
    """An editor-uploaded georeferenced image (GeoTIFF), baked at upload into
    an XYZ PNG tile pyramid under data/trace_tiles/<id>/ and shown as an
    editor-only overlay on the inventory map for tracing geometry in-app.

    The row doubles as the provenance record: what imagery a trace came from,
    its capture date (the date-bracketing evidence), who uploaded it, and
    optionally which landslide it produced. The original upload is kept
    verbatim so the pyramid can always be re-baked (rebuild_trace_rasters).
    Editor-only everywhere — never served to the public map or snapshots.
    """
    STATUS_PROCESSING = 'processing'
    STATUS_READY = 'ready'
    STATUS_ERROR = 'error'
    STATUS_CHOICES = [(s, s) for s in (STATUS_PROCESSING, STATUS_READY, STATUS_ERROR)]

    title = models.CharField(max_length=200)
    original = models.FileField(upload_to='trace_rasters/originals/')
    image_date = models.DateField(
        null=True, blank=True,
        help_text='When the imagery was captured — evidence for date bracketing.')
    source_note = models.CharField(max_length=300, blank=True, default='')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES,
                              default=STATUS_PROCESSING)
    error_message = models.TextField(blank=True, default='')
    # WGS84 bounds of the warped raster (for zoom-to + MapLibre source bounds).
    bounds_w = models.FloatField(null=True, blank=True)
    bounds_s = models.FloatField(null=True, blank=True)
    bounds_e = models.FloatField(null=True, blank=True)
    bounds_n = models.FloatField(null=True, blank=True)
    min_zoom = models.IntegerField(null=True, blank=True)
    max_zoom = models.IntegerField(null=True, blank=True)
    tile_count = models.IntegerField(null=True, blank=True)
    tile_bytes = models.BigIntegerField(null=True, blank=True)
    # FK in spirit only — references PostGIS, which Django doesn't manage
    # (same pattern as LandslideEditMeta.landslide_id).
    landslide_id = models.IntegerField(null=True, blank=True, db_index=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'trace raster'

    def __str__(self):
        return f'{self.title} ({self.status}, #{self.pk})'
