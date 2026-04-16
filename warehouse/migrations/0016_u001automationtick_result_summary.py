from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0015_u001sourceauditrun_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='u001automationtick',
            name='result_summary',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
