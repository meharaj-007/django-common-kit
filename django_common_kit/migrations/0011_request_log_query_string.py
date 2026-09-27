"""``request_logs.query_string``. Non-initial, so it runs for real on an
adopted table. A project whose table already has the column fakes this one.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('common_control', '0010_short_links'),
    ]

    operations = [
        migrations.AddField(
            model_name='requestlog',
            name='query_string',
            field=models.CharField(blank=True, help_text='Redacted query string — credentials in signed URLs are masked before the row is written.', max_length=2000, null=True),
        ),
    ]
