"""Table ``platform_notice_dismissals`` (PRD §16).

Its own initial migration, not a second operation in 0017: ``--fake-initial``
fakes a migration only when every table it creates exists (§13). **Never add a
column to this file.**
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
        ('common_control', '0017_platform_notices'),
    ]

    operations = [
        migrations.CreateModel(
            name='PlatformNoticeDismissalModel',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('tenant_id', models.UUIDField(blank=True, db_index=True, help_text='The tenant this row belongs to, for a multi-tenant project.', null=True)),
                ('notice', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='dismissals', to='common_control.platformnoticemodel')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='platform_notice_dismissals', to=settings.AUTH_USER_MODEL)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Platform Notice Dismissal',
                'verbose_name_plural': 'Platform Notice Dismissals',
                'db_table': 'platform_notice_dismissals',
                'ordering': ['-created_at'],
                'constraints': [models.UniqueConstraint(fields=('notice', 'user'), name='platform_notice_dismissal_uniq')],
            },
            bases=(django_common_kit.history.HistoryMixin, models.Model),
        ),
    ]
