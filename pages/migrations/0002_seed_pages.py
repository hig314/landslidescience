from django.db import migrations


SEED = [
    ('home', 'Landslide Science', '<p>Site coming soon.</p>'),
    ('tracyarm2025', 'Tracy Arm 2025', '<p>Content coming soon.</p>'),
]


def seed_pages(apps, schema_editor):
    Page = apps.get_model('pages', 'Page')
    for slug, title, body in SEED:
        Page.objects.get_or_create(
            slug=slug,
            defaults={'title': title, 'body': body},
        )


def remove_pages(apps, schema_editor):
    Page = apps.get_model('pages', 'Page')
    Page.objects.filter(slug__in=[s for s, _, _ in SEED]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('pages', '0001_initial'),
    ]
    operations = [
        migrations.RunPython(seed_pages, remove_pages),
    ]
