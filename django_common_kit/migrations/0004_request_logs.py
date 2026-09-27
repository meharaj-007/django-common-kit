"""Table ``request_logs`` (PRD §8).

``initial = True``; a project that already has this table adopts it with
``migrate --fake-initial``. **Never add a column to this file** —
``--fake-initial`` matches on table name and never compares columns, so an
added column is silently skipped wherever the table pre-exists and fails at the
first write (§13.3). One table per initial migration, because ``--fake-initial``
fakes a migration only when *every* table it creates exists.

``query_string`` is not here; it lands in 0011.
"""
import django.db.models.deletion
import django.utils.timezone
import django_common_kit.history
import django_common_kit.models
import django_common_kit.storage
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('common_control', '0003_model_history_correlation_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='RequestLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('endpoint', models.CharField(blank=True, max_length=255, null=True)),
                ('method', models.CharField(default='', max_length=10)),
                ('request_body', models.TextField(blank=True, null=True)),
                ('status_code', models.IntegerField(blank=True, null=True)),
                ('response', models.TextField(blank=True, null=True)),
                ('user_agent', models.TextField(blank=True, null=True)),
                ('trace_id', models.CharField(blank=True, db_index=True, max_length=50, null=True)),
                ('response_time', models.FloatField(blank=True, help_text='Time taken to process request (in seconds)', null=True)),
                ('error_message', models.TextField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Request Log',
                'verbose_name_plural': 'Request Logs',
                'db_table': 'request_logs',
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['created_at'], name='request_log_created_idx'), models.Index(fields=['endpoint', 'created_at'], name='request_log_endpoint_idx')],
            },
            bases=(django_common_kit.history.HistoryMixin, models.Model),
        ),
    ]
