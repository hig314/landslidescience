from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0003_traceraster'),
    ]

    operations = [
        migrations.AddField(
            model_name='traceraster',
            name='render',
            field=models.CharField(choices=[('auto', 'auto'), ('nrg', 'nrg'), ('rgb', 'rgb'), ('gray', 'gray')], default='auto', max_length=8),
        ),
    ]
