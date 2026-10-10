"""Photo drops: a link somebody can be sent to upload a folder of photos.

The bytes never touch the droplet. A Drop is a bookkeeping row (SQLite) that
owns a key prefix in a PRIVATE Cloudflare R2 bucket (`drops/<slug>/`); the
browser uploads straight to R2 with URLs this app presigns, and DropFile rows
record what arrived. Reads go the same way: signed-in collaborators get
short-lived presigned GET URLs, so the droplet serves pages and the CDN
serves photos.

The slug IS the credential for uploading (a 12-char random token in the
URL), optionally behind a passphrase. Viewing needs a site account.
"""
import re
import secrets

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone

# What a slug may look like, shared with drops/urls.py. token_urlsafe(9)
# gives 12 chars of [A-Za-z0-9_-].
SLUG_RE = r'[A-Za-z0-9_-]{6,40}'


def new_slug():
    return secrets.token_urlsafe(9)


class PathError(ValueError):
    pass


_CONTROL = re.compile(r'[\x00-\x1f\x7f]')


def clean_rel_path(p):
    """Normalise a browser-supplied relative path into an object-key suffix.

    Keeps the folder structure the uploader dropped (``100MEDIA/IMG_0001.JPG``)
    because that is how a game camera names things and the order matters,
    but refuses anything that could escape the drop's prefix. Raises
    PathError rather than guessing.
    """
    if not isinstance(p, str):
        raise PathError('path must be a string')
    p = p.replace('\\', '/')
    segs = []
    for s in p.split('/'):
        s = _CONTROL.sub('', s).strip()
        if not s or s in ('.', '..'):
            continue
        segs.append(s)
    if not segs:
        raise PathError('empty path')
    out = '/'.join(segs)
    if len(out) > 500:
        raise PathError('path too long')
    if out.startswith('.thumbs/'):
        raise PathError('reserved prefix')
    return out


class Drop(models.Model):
    slug = models.CharField(max_length=40, unique=True, default=new_slug)
    title = models.CharField(max_length=200)
    note = models.TextField(
        blank=True,
        help_text="Shown to the uploader above the drop zone — what you want, "
                  "what to expect.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Uploads refused after this. Blank = no expiry. Viewing is "
                  "unaffected.")
    passphrase_hash = models.CharField(max_length=128, blank=True)
    closed = models.BooleanField(
        default=False,
        help_text="Refuse further uploads (reversible). Files stay viewable.")

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.title} ({self.slug})'

    @property
    def prefix(self):
        return f'drops/{self.slug}/'

    @property
    def thumb_prefix(self):
        return f'{self.prefix}.thumbs/'

    def is_expired(self):
        return bool(self.expires_at and self.expires_at <= timezone.now())

    def accepts_uploads(self):
        return not self.closed and not self.is_expired()

    def has_passphrase(self):
        return bool(self.passphrase_hash)

    def set_passphrase(self, raw):
        self.passphrase_hash = make_password(raw) if raw else ''

    def check_passphrase(self, raw):
        return bool(self.passphrase_hash) and check_password(raw or '', self.passphrase_hash)

    def key_for(self, rel_path):
        return self.prefix + clean_rel_path(rel_path)

    def rel_path_of(self, key):
        """Inverse of key_for, refusing keys outside this drop."""
        if not isinstance(key, str) or not key.startswith(self.prefix):
            raise PathError('key outside this drop')
        rel = clean_rel_path(key[len(self.prefix):])
        if self.prefix + rel != key:
            raise PathError('key not in normal form')
        return rel


class DropFile(models.Model):
    """One object that finished uploading. In-flight multipart uploads live
    only in R2's own state until they complete."""
    THUMB_STATES = (
        ('pending', 'pending'), ('working', 'working'), ('done', 'done'),
        ('skip', 'not an image'), ('error', 'error'),
    )

    drop = models.ForeignKey(Drop, on_delete=models.CASCADE, related_name='files')
    path = models.CharField(max_length=500)           # relative to the drop
    key = models.CharField(max_length=600)            # full object key
    size = models.BigIntegerField()
    etag = models.CharField(max_length=80, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    uploaded_at = models.DateTimeField(default=timezone.now)
    # From EXIF, as the camera wrote it (no timezone — stored as-if-UTC like
    # inventory field photos). The hook a time-lapse type will hang off.
    taken_at = models.DateTimeField(null=True, blank=True)
    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)
    thumb_key = models.CharField(max_length=600, blank=True)
    thumb_state = models.CharField(max_length=10, choices=THUMB_STATES, default='pending')
    thumb_error = models.CharField(max_length=300, blank=True)

    class Meta:
        unique_together = [('drop', 'path')]
        indexes = [models.Index(fields=['drop', 'taken_at'])]
        ordering = ['path']

    def __str__(self):
        return self.path

    @property
    def name(self):
        return self.path.rsplit('/', 1)[-1]

    @property
    def is_image(self):
        return self.name.lower().endswith(IMAGE_EXTS)


IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.heic', '.heif', '.tif', '.tiff', '.webp')
