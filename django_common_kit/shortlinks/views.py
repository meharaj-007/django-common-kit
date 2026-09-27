"""The public redirect (PRD §10). Mount it under the path ``PATH_PREFIX`` names::

    path("s/<slug:slug>/", short_link_redirect, name="short_link"),
"""

import logging

from django.db.models import F
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.utils import timezone
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


@require_GET
def short_link_redirect(request, slug: str):
    """404 if unknown, 410 if expired or deactivated, else a 301.

    The click count is a best-effort ``F()`` update; a failure there is logged
    and never blocks the redirect.
    """
    from django_common_kit.models import ShortLinkModel

    try:
        link = ShortLinkModel.objects.get(slug=slug, is_deleted=False)
    except ShortLinkModel.DoesNotExist:
        raise Http404("Short link not found")

    if not link.is_active:
        return HttpResponse("This link is no longer active.", status=410)
    if link.expires_at and link.expires_at <= timezone.now():
        return HttpResponse("This link has expired.", status=410)

    try:
        ShortLinkModel.objects.filter(pk=link.pk).update(
            click_count=F("click_count") + 1, last_clicked_at=timezone.now(),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not increment click_count for short link %s", slug)

    return HttpResponseRedirect(link.target_url)
