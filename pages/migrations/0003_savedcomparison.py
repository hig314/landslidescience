from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pages', '0002_seed_pages'),
    ]

    operations = [
        migrations.CreateModel(
            name='SavedComparison',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(help_text='Three hex digits, assigned on submit. Links use it: /lidar/compare/s/<code>/.', max_length=3, unique=True)),
                ('name', models.CharField(help_text='Shown on the public list.', max_length=200)),
                ('query', models.TextField(help_text="The comparison's query string (everything after '?' in /lidar/compare/?…), without sv=.")),
                ('notes', models.TextField(blank=True, help_text='Optional; shown under the name on the list.')),
                ('saved_by', models.CharField(blank=True, max_length=150)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
