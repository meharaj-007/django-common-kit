"""``tenant_id`` on every table (PRD §3.5). Non-initial, so it runs for real on an
adopted table; nullable and without a foreign key, so it costs a project with no
tenants nothing. ``adopt_tables`` fakes it where a project already has the column
(renamed from its own tenant column first) and builds the indexes concurrently.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('common_control', '0013_ip_tracking_attribution'),
    ]

    operations = [
        migrations.AddField(
            model_name='blockedipmodel',
            name='tenant_id',
            field=models.UUIDField(blank=True, db_index=True, help_text='The tenant this row belongs to, for a multi-tenant project.', null=True),
        ),
        migrations.AddField(
            model_name='commonfilemodel',
            name='tenant_id',
            field=models.UUIDField(blank=True, db_index=True, help_text='The tenant this row belongs to, for a multi-tenant project.', null=True),
        ),
        migrations.AddField(
            model_name='contactusmodel',
            name='tenant_id',
            field=models.UUIDField(blank=True, db_index=True, help_text='The tenant this row belongs to, for a multi-tenant project.', null=True),
        ),
        migrations.AddField(
            model_name='iptrackingmodel',
            name='tenant_id',
            field=models.UUIDField(blank=True, db_index=True, help_text='The tenant this row belongs to, for a multi-tenant project.', null=True),
        ),
        migrations.AddField(
            model_name='modelhistory',
            name='tenant_id',
            field=models.UUIDField(blank=True, db_index=True, help_text='The tenant this row belongs to, for a multi-tenant project.', null=True),
        ),
        migrations.AddField(
            model_name='parametermodel',
            name='tenant_id',
            field=models.UUIDField(blank=True, db_index=True, help_text='The tenant this row belongs to, for a multi-tenant project.', null=True),
        ),
        migrations.AddField(
            model_name='requestlog',
            name='tenant_id',
            field=models.UUIDField(blank=True, db_index=True, help_text='The tenant this row belongs to, for a multi-tenant project.', null=True),
        ),
        migrations.AddField(
            model_name='shortlinkmodel',
            name='tenant_id',
            field=models.UUIDField(blank=True, db_index=True, help_text='The tenant this row belongs to, for a multi-tenant project.', null=True),
        ),
        migrations.AddField(
            model_name='statustransitionmodel',
            name='tenant_id',
            field=models.UUIDField(blank=True, db_index=True, help_text='The tenant this row belongs to, for a multi-tenant project.', null=True),
        ),
    ]
