from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0016_u001automationtick_result_summary'),
    ]

    operations = [
        migrations.CreateModel(
            name='U001BootRecoveryRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('started_at', models.DateTimeField()),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(max_length=20)),
                ('db_reachable', models.BooleanField(default=False)),
                ('migrations_ok', models.BooleanField(default=False)),
                ('automation_tick_started', models.BooleanField(default=False)),
                ('automation_tick_status', models.CharField(blank=True, max_length=20, null=True)),
                ('log_path', models.CharField(blank=True, max_length=512, null=True)),
                ('notes', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-started_at', '-id'],
            },
        ),
        migrations.AddIndex(
            model_name='u001bootrecoveryrun',
            index=models.Index(fields=['started_at'], name='u001bootrecovery_started_idx'),
        ),
        migrations.AddIndex(
            model_name='u001bootrecoveryrun',
            index=models.Index(fields=['status'], name='u001bootrecovery_status_idx'),
        ),
    ]
