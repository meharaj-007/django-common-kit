"""Table ``parameters`` (PRD §7).

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

import django_common_utils.history


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ParameterModel',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('key', models.CharField(help_text='Unique parameter key identifier', max_length=255, unique=True)),
                ('value_text', models.TextField(blank=True, null=True)),
                ('value_integer', models.IntegerField(blank=True, null=True)),
                ('value_float', models.FloatField(blank=True, null=True)),
                ('value_boolean', models.BooleanField(blank=True, null=True)),
                ('value_json', models.JSONField(blank=True, null=True)),
                ('parameter_type', models.CharField(choices=[('text', 'Text'), ('integer', 'Integer'), ('float', 'Float'), ('boolean', 'Boolean'), ('json', 'JSON')], default='text', max_length=20)),
                ('description', models.TextField(blank=True, null=True)),
                ('category', models.CharField(blank=True, max_length=100, null=True)),
                ('is_system', models.BooleanField(default=False, help_text='A system parameter is loaded into the cache and is not for users to edit.')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Parameter',
                'verbose_name_plural': 'Parameters',
                'db_table': 'parameters',
                'ordering': ['category', 'key'],
                'indexes': [
                    models.Index(fields=['key'], name='parameters_key_e33381_idx'),
                    models.Index(fields=['category'], name='parameters_categor_a46a27_idx'),
                    models.Index(fields=['parameter_type'], name='parameters_paramet_9b08b4_idx'),
                    models.Index(fields=['is_system'], name='parameters_is_syst_45d3ad_idx'),
                ],
            },
            bases=(django_common_utils.history.HistoryMixin, models.Model),
        ),
    ]
