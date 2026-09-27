"""Retention sweeps (PRD §8). Plain functions; the management command and any
beat schedule call these."""

import logging
from datetime import timedelta
from typing import Dict

from django.utils import timezone

from django_common_kit.conf import app_settings

logger = logging.getLogger(__name__)

PURGE_BATCH_SIZE = 5_000
PURGE_MAX_BATCHES = 2_000  # backstop: 10M rows per table per run


def purge_table(model, days: int, batch_size: int = PURGE_BATCH_SIZE) -> int:
    """Delete rows older than ``days``, in batches by pk. Returns the count.

    Batched because a single ``DELETE ... WHERE created_at <`` on tens of
    millions of rows holds a lock for the whole sweep and fills the WAL.
    ``days <= 0`` means keep forever and deletes nothing — an unset window must
    never read as "delete the table".
    """
    if days <= 0:
        return 0
    cutoff = timezone.now() - timedelta(days=days)
    stale = model._default_manager.filter(created_at__lt=cutoff)
    deleted = 0
    for _ in range(PURGE_MAX_BATCHES):
        ids = list(stale.order_by("pk").values_list("pk", flat=True)[:batch_size])
        if not ids:
            break
        count, _detail = model._default_manager.filter(pk__in=ids).delete()
        deleted += count
    if deleted:
        logger.info("Purged %d %s rows older than %s", deleted, model._meta.db_table, cutoff.date())
    return deleted


def purge_request_logs() -> int:
    from django_common_kit.models import RequestLog

    return purge_table(RequestLog, app_settings.get("TRACKING", "REQUEST_LOG_RETENTION_DAYS"))


def purge_ip_tracking() -> int:
    from django_common_kit.models import IPTrackingModel

    return purge_table(IPTrackingModel, app_settings.get("TRACKING", "IP_TRACKING_RETENTION_DAYS"))


def purge_tracking_tables() -> Dict[str, int]:
    return {
        "request_logs": purge_request_logs(),
        "ip_tracking": purge_ip_tracking(),
    }
