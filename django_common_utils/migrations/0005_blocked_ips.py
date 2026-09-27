"""Table ``blocked_ips`` (PRD §8).

``initial = True``; a project that already has this table adopts it with
``migrate --fake-initial``. **Never add a column to this file** —
``--fake-initial`` matches on table name and never compares columns, so an
added column is silently skipped wherever the table pre-exists and fails at the
first write (§13.3). One table per initial migration, because ``--fake-initial``
fakes a migration only when *every* table it creates exists.

``blocked_at`` is not here; it lands in 0012.
"""
import django.db.models.deletion
import django.utils.timezone
import django_common_utils.history
import django_common_utils.models
import django_common_utils.storage
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('common_control', '0004_request_logs'),
    ]

    operations = [
        migrations.CreateModel(
            name='BlockedIPModel',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('ip_address', models.CharField(max_length=45, unique=True)),
                ('reason', models.CharField(default='Sensitive file access attempt', max_length=255)),
                ('attempts', models.IntegerField(default=1)),
                ('first_attempt', models.DateTimeField(auto_now_add=True)),
                ('last_attempt', models.DateTimeField(default=django.utils.timezone.now)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Blocked IP',
                'verbose_name_plural': 'Blocked IPs',
                'db_table': 'blocked_ips',
            },
            bases=(django_common_utils.history.HistoryMixin, models.Model),
        ),
    ]
