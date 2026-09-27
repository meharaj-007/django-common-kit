"""``purge_history`` — delete ``ModelHistory`` rows older than the retention window (PRD §6.1).

Deletes in batches by primary key rather than one ``DELETE ... WHERE created_at
<``: on a table with tens of millions of rows the single statement holds a lock
for the whole sweep and fills the WAL. Batches commit as they go.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from django_common_kit.conf import app_settings


class Command(BaseCommand):
    help = "Delete model_history rows older than DJANGO_COMMON_KIT['HISTORY']['RETENTION_DAYS']."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, help="Override the configured retention.")
        parser.add_argument("--batch-size", type=int, default=5000)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        from django_common_kit.models import ModelHistory

        days = options["days"] or app_settings.get("HISTORY", "RETENTION_DAYS")
        if not days:
            self.stdout.write("RETENTION_DAYS is 0: history is kept forever. Nothing to do.")
            return

        cutoff = timezone.now() - timedelta(days=days)
        stale = ModelHistory.objects.filter(created_at__lt=cutoff)
        total = stale.count()
        if options["dry_run"]:
            self.stdout.write(f"Would delete {total} rows older than {cutoff:%Y-%m-%d}.")
            return

        deleted = 0
        batch = options["batch_size"]
        while True:
            ids = list(stale.order_by("pk").values_list("pk", flat=True)[:batch])
            if not ids:
                break
            count, _ = ModelHistory.objects.filter(pk__in=ids).delete()
            deleted += count
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} history rows older than {cutoff:%Y-%m-%d}."))
