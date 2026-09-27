"""``unblock_ip`` — the escape hatch (PRD §8).

The blocker 403s a banned address on every path, the admin included, so the
office whose NAT was banned cannot reach the admin to unblock itself. This
lifts the ban from a shell. The change is visible to every worker within
``IP_BLOCK_CACHE_TTL_SECONDS``.
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Lift the ban on an IP address and clear its strikes."

    def add_arguments(self, parser):
        parser.add_argument("ip_address")

    def handle(self, *args, **options):
        from django_common_utils.models import BlockedIPModel

        try:
            row = BlockedIPModel.objects.get(ip_address=options["ip_address"])
        except BlockedIPModel.DoesNotExist:
            raise CommandError(f"{options['ip_address']} has no blocked_ips row.")
        row.is_active = False
        row.attempts = 0
        row.blocked_at = None
        row.save(update_fields=["is_active", "attempts", "blocked_at", "updated_at"])
        self.stdout.write(self.style.SUCCESS(f"Unblocked {row.ip_address}."))
