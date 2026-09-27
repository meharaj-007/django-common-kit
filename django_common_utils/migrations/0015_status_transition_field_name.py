"""``status_transitions.field_name`` — which status field moved, for a model with
more than one. Its own migration because it is not nullable: ``adopt_tables``
fakes a migration only when its columns exist or can be added empty.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('common_control', '0014_tenant_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='statustransitionmodel',
            name='field_name',
            field=models.CharField(default='status', max_length=50),
        ),
    ]
