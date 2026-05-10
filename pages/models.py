from django.db import models


class Page(models.Model):
    slug = models.SlugField(
        unique=True,
        help_text="URL identifier — must match the slug used in pages/urls.py (e.g. 'home', 'tracyarm2025').",
    )
    title = models.CharField(
        max_length=200,
        help_text="Used as both the browser tab title and the page heading.",
    )
    body = models.TextField(
        blank=True,
        help_text="HTML allowed. Renders inside the page body.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['slug']

    def __str__(self):
        return self.slug
