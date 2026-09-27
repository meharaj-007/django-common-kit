"""Table ``ip_tracking`` (PRD §8).

``initial = True``; a project that already has this table adopts it with
``migrate --fake-initial``. **Never add a column to this file** —
``--fake-initial`` matches on table name and never compares columns, so an
added column is silently skipped wherever the table pre-exists and fails at the
first write (§13.3). One table per initial migration, because ``--fake-initial``
fakes a migration only when *every* table it creates exists.

The referer and UTM columns are not here; they land in 0013.
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
        ('common_control', '0005_blocked_ips'),
    ]

    operations = [
        migrations.CreateModel(
            name='IPTrackingModel',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('ip_address', models.GenericIPAddressField()),
                ('endpoint', models.CharField(max_length=255)),
                ('method', models.CharField(max_length=10)),
                ('user_agent', models.TextField(blank=True, null=True)),
                ('device_type', models.CharField(blank=True, max_length=50, null=True)),
                ('browser', models.CharField(blank=True, max_length=50, null=True)),
                ('operating_system', models.CharField(blank=True, max_length=50, null=True)),
                ('country', models.CharField(blank=True, max_length=100, null=True)),
                ('country_code', models.CharField(blank=True, max_length=10, null=True)),
                ('continent', models.CharField(blank=True, max_length=50, null=True)),
                ('continent_code', models.CharField(blank=True, max_length=10, null=True)),
                ('region', models.CharField(blank=True, max_length=100, null=True)),
                ('region_name', models.CharField(blank=True, max_length=100, null=True)),
                ('city', models.CharField(blank=True, max_length=100, null=True)),
                ('district', models.CharField(blank=True, max_length=100, null=True)),
                ('zip_code', models.CharField(blank=True, max_length=20, null=True)),
                ('latitude', models.DecimalField(blank=True, decimal_places=8, max_digits=10, null=True)),
                ('longitude', models.DecimalField(blank=True, decimal_places=8, max_digits=11, null=True)),
                ('timezone', models.CharField(blank=True, max_length=50, null=True)),
                ('currency', models.CharField(blank=True, max_length=10, null=True)),
                ('isp', models.CharField(blank=True, max_length=255, null=True)),
                ('organization', models.CharField(blank=True, max_length=255, null=True)),
                ('as_number', models.CharField(blank=True, max_length=50, null=True)),
                ('is_mobile', models.BooleanField(blank=True, null=True)),
                ('trace_id', models.CharField(blank=True, db_index=True, max_length=50, null=True)),
                ('status_code', models.IntegerField(blank=True, null=True)),
                ('response_time', models.FloatField(blank=True, help_text='Time taken to process request (in seconds)', null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'IP Tracking',
                'verbose_name_plural': 'IP Tracking',
                'db_table': 'ip_tracking',
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['ip_address'], name='ip_tracking_ip_idx'), models.Index(fields=['country'], name='ip_tracking_country_idx'), models.Index(fields=['created_at'], name='ip_tracking_created_idx'), models.Index(fields=['user'], name='ip_tracking_user_idx')],
            },
            bases=(django_common_kit.history.HistoryMixin, models.Model),
        ),
    ]
