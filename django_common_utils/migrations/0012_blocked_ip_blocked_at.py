"""``blocked_ips.blocked_at``. Non-initial, so it runs for real on an adopted
table. The blocker measures a ban's expiry from this column (§8). A project
whose table already has it fakes this one.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('common_control', '0011_request_log_query_string'),
    ]

    operations = [
        migrations.AddField(
            model_name='blockedipmodel',
            name='blocked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
