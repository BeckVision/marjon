from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0017_u001bootrecoveryrun'),
    ]

    operations = [
        migrations.CreateModel(
            name='U001RD001ChainAuditRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('started_at', models.DateTimeField()),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(max_length=20)),
                ('options', models.JSONField(blank=True, default=dict)),
                ('coin_count', models.PositiveIntegerField(default=0)),
                ('transaction_count', models.PositiveIntegerField(default=0)),
                ('finding_count', models.PositiveIntegerField(default=0)),
                ('warning_count', models.PositiveIntegerField(default=0)),
                ('summary', models.JSONField(blank=True, default=dict)),
                ('notes', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-started_at', '-id'],
            },
        ),
        migrations.AddIndex(
            model_name='u001rd001chainauditrun',
            index=models.Index(fields=['started_at'], name='u001rd001chain_started_idx'),
        ),
        migrations.AddIndex(
            model_name='u001rd001chainauditrun',
            index=models.Index(fields=['status'], name='u001rd001chain_status_idx'),
        ),
    ]
