"""Table ``model_history`` (PRD §6).

``initial = True``; a project that already has this table adopts it with
``migrate --fake-initial``. **Never add a column to this file** —
``--fake-initial`` matches on table name and never compares columns, so an
added column is silently skipped wherever the table pre-exists and fails at the
first write (§13.3). One table per initial migration, because ``--fake-initial``
fakes a migration only when *every* table it creates exists.
"""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import django_common_kit.history


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('common_control', '0001_parameters'),
    ]

    operations = [
        migrations.CreateModel(
            name='ModelHistory',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('object_id', models.CharField(db_index=True, help_text='The ID of the object this history entry belongs to', max_length=255)),
                ('action', models.CharField(choices=[('create', 'Create'), ('update', 'Update'), ('delete', 'Delete')], db_index=True, max_length=20)),
                ('field_changes', models.JSONField(blank=True, default=dict)),
                ('object_snapshot_before', models.JSONField(blank=True, null=True)),
                ('object_snapshot_after', models.JSONField(blank=True, null=True)),
                ('change_reason', models.TextField(blank=True, null=True)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.TextField(blank=True, null=True)),
                ('changed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='model_history_changes', to=settings.AUTH_USER_MODEL)),
                ('content_type', models.ForeignKey(help_text='The model type this history entry belongs to', on_delete=django.db.models.deletion.CASCADE, to='contenttypes.contenttype')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Model History',
                'verbose_name_plural': 'Model Histories',
                'db_table': 'model_history',
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['content_type', 'object_id'], name='model_history_object_idx'),
                    models.Index(fields=['action'], name='model_history_action_idx'),
                    models.Index(fields=['changed_by'], name='model_history_changed_by_idx'),
                    models.Index(fields=['created_at'], name='model_history_created_at_idx'),
                    models.Index(fields=['content_type', 'object_id', 'created_at'], name='model_history_lookup_idx'),
                ],
            },
            bases=(django_common_kit.history.HistoryMixin, models.Model),
        ),
    ]
