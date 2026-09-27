"""``ip_tracking`` referer and UTM columns. Non-initial, so they run for real on
an adopted table. Written only when ``TRACKING["CAPTURE_ATTRIBUTION"]`` is on. A
project whose table already has them fakes this one.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('common_control', '0012_blocked_ip_blocked_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='iptrackingmodel',
            name='referer',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='iptrackingmodel',
            name='query_string',
            field=models.TextField(blank=True, help_text='Raw request query string with sensitive keys scrubbed.', null=True),
        ),
        migrations.AddField(
            model_name='iptrackingmodel',
            name='utm_source',
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='iptrackingmodel',
            name='utm_medium',
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='iptrackingmodel',
            name='utm_campaign',
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='iptrackingmodel',
            name='utm_term',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='iptrackingmodel',
            name='utm_content',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
