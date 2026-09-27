"""A tenant's history and requests, newest first (PRD §3.5). Index-only, so
``adopt_tables`` fakes it where the index exists and builds it concurrently where
it does not.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('common_control', '0015_status_transition_field_name'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='modelhistory',
            index=models.Index(fields=['tenant_id', '-created_at'], name='model_history_tenant_idx'),
        ),
        migrations.AddIndex(
            model_name='requestlog',
            index=models.Index(fields=['tenant_id', '-created_at'], name='request_log_tenant_idx'),
        ),
    ]
