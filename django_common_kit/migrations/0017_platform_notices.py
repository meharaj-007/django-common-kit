"""Table ``platform_notices`` (PRD §16).

``initial = True`` like every table the package creates, so the §13 rules hold
for it too: **never add a column to this file** — a later column goes in a
later, non-initial migration. No project had this table before the package, so
its original shape already carries ``tenant_id``.
"""
import django.db.models.deletion
import django_common_kit.history
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('common_control', '0016_tenant_indexes'),
    ]

    operations = [
        migrations.CreateModel(
            name='PlatformNoticeModel',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('tenant_id', models.UUIDField(blank=True, db_index=True, help_text='The tenant this row belongs to, for a multi-tenant project.', null=True)),
                ('title', models.CharField(max_length=255)),
                ('body', models.TextField(blank=True, default='', help_text='Plain text or markdown; the frontend renders it.')),
                ('severity', models.CharField(choices=[('info', 'Info'), ('success', 'Success'), ('warning', 'Warning'), ('critical', 'Critical')], db_index=True, default='info', max_length=20)),
                ('audience', models.CharField(blank=True, db_index=True, help_text="Who sees it, in the project's own words. Empty = everyone.", max_length=64, null=True)),
                ('surface', models.CharField(blank=True, db_index=True, help_text="Where it shows (e.g. 'web', 'ios'), in the project's own words. Empty = everywhere.", max_length=64, null=True)),
                ('starts_at', models.DateTimeField(blank=True, help_text='Empty = from now.', null=True)),
                ('ends_at', models.DateTimeField(blank=True, help_text='Empty = until switched off.', null=True)),
                ('priority', models.IntegerField(default=0, help_text='Higher shows first.')),
                ('is_dismissible', models.BooleanField(default=True)),
                ('action_label', models.CharField(blank=True, max_length=64, null=True)),
                ('action_url', models.URLField(blank=True, max_length=2048, null=True)),
                ('metadata', models.JSONField(blank=True, help_text='Anything else the frontend needs (e.g. a minimum app version).', null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Platform Notice',
                'verbose_name_plural': 'Platform Notices',
                'db_table': 'platform_notices',
                'ordering': ['-priority', '-created_at'],
                'indexes': [models.Index(fields=['starts_at', 'ends_at'], name='platform_notice_window_idx')],
            },
            bases=(django_common_kit.history.HistoryMixin, models.Model),
        ),
    ]
