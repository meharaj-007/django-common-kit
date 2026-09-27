"""``model_history.correlation_id`` — the first non-initial column (PRD §13.3).

Not ``initial``, so it runs for real everywhere, including on a table adopted by
``--fake-initial`` a moment earlier. That is the whole reason it is a separate
file. A project whose table already has this column fakes this migration.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('common_control', '0002_model_history'),
    ]

    operations = [
        migrations.AddField(
            model_name='modelhistory',
            name='correlation_id',
            field=models.CharField(blank=True, db_index=True, help_text='The X-Request-Id of the request that made this change.', max_length=64, null=True),
        ),
    ]
