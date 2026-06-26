import os

from django.core.validators import RegexValidator
from django.db import models
from django.urls import reverse

# The public URL token. Constrained to a filename-safe set so the serving view
# can treat it as a path segment without traversal risk (matches the URL regex
# in files/urls.py). Dots are allowed so the readable extension survives, e.g.
# /files/tracy_arm_survey.kml.
name_validator = RegexValidator(
    r'^[A-Za-z0-9._-]+$',
    "Use only letters, numbers, dots, dashes and underscores (e.g. survey_2025.kml).",
)


class HostedFile(models.Model):
    """An arbitrary file hosted at a stable, human-readable public URL.

    Stored under MEDIA_ROOT (data/media/, volume-mounted + gitignored), served
    by files.views.serve at /files/<name>. The on-disk path is decoupled from
    the public URL: `name` is the URL token, `file` is wherever Django's storage
    put the bytes (it may suffix the disk name on collision — that's fine).
    """

    file = models.FileField(
        upload_to='hosted_files/',
        help_text="The file to host. Uploaded under MEDIA_ROOT/hosted_files/.",
    )
    name = models.CharField(
        max_length=255,
        unique=True,
        blank=True,
        validators=[name_validator],
        help_text="Public URL token: the file is served at /files/<name>. "
                  "Leave blank to derive it from the uploaded filename.",
    )
    title = models.CharField(
        max_length=200,
        blank=True,
        help_text="Optional human label (admin only — not shown to the public).",
    )
    description = models.TextField(
        blank=True,
        help_text="Optional notes (admin only).",
    )
    inline = models.BooleanField(
        default=True,
        help_text="Serve inline so browsers/apps render it directly. "
                  "Uncheck to force a download (Content-Disposition: attachment).",
    )
    content_type = models.CharField(
        max_length=100,
        blank=True,
        help_text="Override the MIME type. Blank = auto-detect from the name.",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def save(self, *args, **kwargs):
        if not self.name and self.file:
            self.name = os.path.basename(self.file.name)
        super().save(*args, **kwargs)

    @property
    def url(self):
        return reverse('hosted_file', args=[self.name])

    def __str__(self):
        return self.name or self.file.name
