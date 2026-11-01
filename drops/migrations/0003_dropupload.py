from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('drops', '0002_image_stats'),
    ]

    operations = [
        migrations.CreateModel(
            name='DropUpload',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('path', models.CharField(max_length=500)),
                ('size', models.BigIntegerField()),
                ('mtime', models.BigIntegerField(blank=True, null=True)),
                ('key', models.CharField(max_length=600)),
                ('upload_id', models.CharField(max_length=1024)),
                ('part_size', models.BigIntegerField(default=0)),
                ('started_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('drop', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='uploads', to='drops.drop')),
            ],
            options={
                'unique_together': {('drop', 'path')},
            },
        ),
    ]
