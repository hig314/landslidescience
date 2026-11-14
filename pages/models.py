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


class SavedComparison(models.Model):
    """A terrain comparison (/lidar/compare/) kept on the public list at
    /lidar/compare/saved/. The comparison itself is its query string; `code`
    is a short random hex id that names it in links (/lidar/compare/s/<code>/)
    and lets the page overwrite it ("Update") rather than add a new entry."""
    code = models.CharField(
        max_length=3, unique=True,
        help_text="Three hex digits, assigned on submit. Links use it: /lidar/compare/s/<code>/.",
    )
    name = models.CharField(max_length=200, help_text="Shown on the public list.")
    query = models.TextField(
        help_text="The comparison's query string (everything after '?' in /lidar/compare/?…), without sv=.",
    )
    notes = models.TextField(blank=True, help_text="Optional; shown under the name on the list.")
    saved_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.code} {self.name}'
