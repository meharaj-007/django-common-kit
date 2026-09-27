"""Table ``status_transitions`` (PRD §8).

``initial = True``; a project that already has this table adopts it with
``migrate --fake-initial``. **Never add a column to this file** —
``--fake-initial`` matches on table name and never compares columns, so an
added column is silently skipped wherever the table pre-exists and fails at the
first write (§13.3). One table per initial migration, because ``--fake-initial``
fakes a migration only when *every* table it creates exists.
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
        ('contenttypes', '0002_remove_content_type_name'),
        ('common_control', '0007_contact_us'),
    ]

    operations = [
        migrations.CreateModel(
            name='StatusTransitionModel',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('object_id', models.CharField(db_index=True, help_text='The ID of the object this status transition belongs to', max_length=255)),
                ('previous_status', models.CharField(blank=True, db_index=True, max_length=50, null=True)),
                ('new_status', models.CharField(db_index=True, max_length=50)),
                ('transition_source', models.CharField(db_index=True, help_text="Who or what made the change — the project's own vocabulary.", max_length=20)),
                ('transition_reason', models.TextField(blank=True, null=True)),
                ('notes', models.TextField(blank=True, null=True)),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('changed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='status_transitions', to=settings.AUTH_USER_MODEL)),
                ('content_type', models.ForeignKey(help_text='The model type this status transition belongs to', on_delete=django.db.models.deletion.CASCADE, to='contenttypes.contenttype')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Status Transition',
                'verbose_name_plural': 'Status Transitions',
                'db_table': 'status_transitions',
                'ordering': ['-timestamp'],
                'indexes': [models.Index(fields=['content_type', 'object_id'], name='status_trans_object_idx'), models.Index(fields=['new_status', 'timestamp'], name='status_trans_status_idx')],
            },
            bases=(django_common_kit.history.HistoryMixin, models.Model),
        ),
    ]
