"""Which notices a viewer sees, and dismissing one (PRD §16).

Every page load asks, so the notices that are switched on and have not yet
ended are held in the cache as one list — a platform has a handful of them, not
thousands — and the per-viewer questions are answered in Python from it: is it
live *now*, is it for this tenant, audience and surface, has this user
dismissed it. A notice that starts or ends while the list is cached therefore
appears or goes on time; a save or delete drops the list (``signals.py``).
Only the dismissals are read per request, and only for a signed-in user.

A failing cache or audience resolver never fails the page: the cache falls
through to the database, and a resolver that raises gives the viewer no
audiences, so they see only the notices meant for everyone.
"""

import logging

from django.core.cache import caches
from django.db.models import Q
from django.utils import timezone

from django_common_utils.conf import app_settings

logger = logging.getLogger(__name__)

CACHE_KEY = "platform_notices"


def _cache():
    return caches[app_settings.get("NOTICES", "CACHE_ALIAS")]


def _candidates():
    """Every notice switched on and not yet ended, highest priority first."""
    from django_common_utils.models import PlatformNoticeModel

    ttl = app_settings.get("NOTICES", "CACHE_TTL_SECONDS")
    if ttl:
        try:
            cached = _cache().get(CACHE_KEY)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Notice cache read failed: %s", exc)
            cached = None
        if cached is not None:
            return cached

    now = timezone.now()
    rows = list(
        PlatformNoticeModel.objects
        .filter(is_active=True, is_deleted=False)
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
    )
    if ttl:
        try:
            _cache().set(CACHE_KEY, rows, timeout=ttl)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Notice cache write failed: %s", exc)
    return rows


def invalidate_cache():
    try:
        _cache().delete(CACHE_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Notice cache delete failed: %s", exc)


def _user(request):
    user = getattr(request, "user", None)
    return user if user is not None and getattr(user, "is_authenticated", False) else None


def audiences_for(request):
    """The audience names the viewer belongs to, from ``AUDIENCE_RESOLVER``."""
    resolver = app_settings.hook("NOTICES", "AUDIENCE_RESOLVER")
    if resolver is None:
        return frozenset()
    try:
        return frozenset(resolver(request) or ())
    except Exception as exc:  # noqa: BLE001
        logger.warning("NOTICES AUDIENCE_RESOLVER failed: %s", exc)
        return frozenset()


def _visible(request, at):
    """Live at ``at`` for this viewer's tenant and audiences, on any surface."""
    from django_common_utils.tenancy import is_configured, resolve_tenant

    tenant_id = resolve_tenant(request=request) if is_configured() else None
    audiences = audiences_for(request)
    return [
        notice for notice in _candidates()
        if notice.is_live(at) and notice.is_for(tenant_id=tenant_id, audiences=audiences)
    ]


def live_notices(request, surface=None, at=None):
    """The notices to show the viewer of ``request`` on ``surface``, in order.

    ``surface`` is what the client says it is ("web", "ios"). A client that
    says nothing sees only the notices for every surface — never another
    surface's "please update the app".
    """
    at = at or timezone.now()
    notices = [notice for notice in _visible(request, at) if notice.shows_on(surface)]
    user = _user(request)
    if user is None or not notices:
        return notices

    from django_common_utils.models import PlatformNoticeDismissalModel

    dismissed = set(
        PlatformNoticeDismissalModel.objects
        .filter(user=user, notice_id__in=[notice.pk for notice in notices])
        .values_list("notice_id", flat=True)
    )
    return [notice for notice in notices if notice.pk not in dismissed]


def find_visible(request, notice_id, at=None):
    """The notice, if it is live for this viewer on some surface; else ``None``.
    A viewer cannot dismiss a notice they could not have been shown."""
    at = at or timezone.now()
    return next((notice for notice in _visible(request, at) if str(notice.pk) == str(notice_id)), None)


def dismiss(notice, user):
    """Record that ``user`` dismissed ``notice``. Idempotent.

    Raises ``ValueError`` for a notice that cannot be dismissed — a critical
    notice stays until it ends or is switched off.
    """
    from django_common_utils.models import PlatformNoticeDismissalModel

    if not notice.is_dismissible:
        raise ValueError("This notice cannot be dismissed.")
    row, _ = PlatformNoticeDismissalModel.objects.get_or_create(notice=notice, user=user)
    return row


def notice_payload(notice):
    """What a client is sent. ``audience`` and ``tenant_id`` stay server-side:
    they decided that the viewer sees the notice, and are not the viewer's
    business."""
    return {
        "id": notice.pk,
        "title": notice.title,
        "body": notice.body,
        "severity": notice.severity,
        "surface": notice.surface,
        "starts_at": notice.starts_at,
        "ends_at": notice.ends_at,
        "priority": notice.priority,
        "is_dismissible": notice.is_dismissible,
        "action_label": notice.action_label,
        "action_url": notice.action_url,
        "metadata": notice.metadata,
    }
