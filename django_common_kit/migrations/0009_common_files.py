"""Table ``common_files`` (PRD §9).

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
        ('common_control', '0008_status_transitions'),
    ]

    operations = [
        migrations.CreateModel(
            name='CommonFileModel',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('object_id', models.CharField(blank=True, db_index=True, max_length=255, null=True)),
                ('file', models.FileField(storage=django_common_kit.storage.MediaStorage(), upload_to=django_common_kit.models.common_file_upload_to)),
                ('original_filename', models.CharField(blank=True, max_length=255, null=True)),
                ('title', models.CharField(blank=True, max_length=255, null=True)),
                ('description', models.TextField(blank=True, null=True)),
                ('tag', models.CharField(db_index=True, default='unknown', help_text="Purpose, used for the directory and for filtering (e.g. 'profile_picture').", max_length=50)),
                ('mime_type', models.CharField(blank=True, max_length=100, null=True)),
                ('file_size', models.BigIntegerField(blank=True, help_text='Size in bytes', null=True)),
                ('metadata', models.JSONField(blank=True, null=True)),
                ('content_type', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='common_files', to='contenttypes.contenttype')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL)),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='uploaded_common_files', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Common File',
                'verbose_name_plural': 'Common Files',
                'db_table': 'common_files',
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['content_type', 'object_id'], name='common_file_obj_idx'), models.Index(fields=['tag'], name='common_file_tag_idx'), models.Index(fields=['created_at'], name='common_file_created_at_idx')],
            },
            bases=(django_common_kit.history.HistoryMixin, models.Model),
        ),
    ]
