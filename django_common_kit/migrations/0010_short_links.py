"""Table ``short_links`` (PRD §10).

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
        ('common_control', '0009_common_files'),
    ]

    operations = [
        migrations.CreateModel(
            name='ShortLinkModel',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('slug', models.CharField(db_index=True, help_text='Short opaque identifier used in the public /s/<slug>/ URL.', max_length=16, unique=True)),
                ('target_url', models.URLField(max_length=2048)),
                ('click_count', models.PositiveIntegerField(default=0)),
                ('last_clicked_at', models.DateTimeField(blank=True, null=True)),
                ('expires_at', models.DateTimeField(blank=True, help_text='After this time the slug returns 410 Gone.', null=True)),
                ('purpose', models.CharField(blank=True, db_index=True, help_text='Optional tag (e.g. quote_offer_view) for analytics and cleanup.', max_length=64, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Short Link',
                'verbose_name_plural': 'Short Links',
                'db_table': 'short_links',
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['slug'], name='short_link_slug_idx'), models.Index(fields=['purpose'], name='short_link_purpose_idx'), models.Index(fields=['expires_at'], name='short_link_expires_idx')],
            },
            bases=(django_common_kit.history.HistoryMixin, models.Model),
        ),
    ]
