"""``purge_tracking`` — sweep ``request_logs`` and ``ip_tracking`` past their
retention windows (PRD §8). Run daily from cron or beat."""

from django.core.management.base import BaseCommand

from django_common_utils.tracking.purge import purge_tracking_tables


class Command(BaseCommand):
    help = "Delete tracking rows older than TRACKING['*_RETENTION_DAYS']."

    def handle(self, *args, **options):
        for table, count in purge_tracking_tables().items():
            self.stdout.write(f"{table}: {count} rows deleted")
