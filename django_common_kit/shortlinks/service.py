"""Create short links (PRD §10)."""

import logging
import secrets
from typing import Optional

from django.db import IntegrityError, transaction
from django.utils import timezone

from django_common_kit.conf import app_settings

logger = logging.getLogger(__name__)

# Full 62-character alphabet — no ambiguous characters stripped, because
# nobody types these; maximum entropy per character is what matters.
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_MAX_SLUG_ATTEMPTS = 6


def _generate_slug(length: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


class ShortLinkService:
    @staticmethod
    def build_short_url(slug: str) -> str:
        base = (app_settings.get("SHORT_LINK", "BASE_URL") or "").rstrip("/")
        path = app_settings.get("SHORT_LINK", "PATH_PREFIX").strip("/")
        return f"{base}/{path}/{slug}/"

    @staticmethod
    def shorten(target_url: str, *, purpose: Optional[str] = None, expires_at=None, reuse: bool = True) -> str:
        """A short URL for ``target_url``.

        Reuses an existing, unexpired row with the same (target, purpose) when
        ``reuse`` is set, so the same destination does not mint slugs forever.

        **Falls back to returning ``target_url`` unchanged on any failure.** An
        SMS should still go out, just with the long URL; a shortener that can
        make a message fail to send has the wrong priorities.
        """
        from django_common_kit.models import ShortLinkModel

        if not target_url:
            return target_url
        if not app_settings.get("SHORT_LINK", "BASE_URL"):
            logger.warning("SHORT_LINK['BASE_URL'] is not set; returning the long URL")
            return target_url

        try:
            if reuse:
                now = timezone.now()
                existing = (
                    ShortLinkModel.objects
                    .filter(target_url=target_url, purpose=purpose, is_active=True, is_deleted=False)
                    .filter(models_q_unexpired(now))
                    .order_by("-created_at").first()
                )
                if existing is not None:
                    return ShortLinkService.build_short_url(existing.slug)

            base_length = int(app_settings.get("SHORT_LINK", "CODE_LENGTH"))
            for attempt in range(_MAX_SLUG_ATTEMPTS):
                # Lengthen after two collisions rather than retry forever at
                # the same size.
                slug = _generate_slug(base_length + attempt // 2)
                try:
                    with transaction.atomic():
                        link = ShortLinkModel.objects.create(
                            slug=slug, target_url=target_url, purpose=purpose, expires_at=expires_at,
                        )
                    return ShortLinkService.build_short_url(link.slug)
                except IntegrityError:
                    logger.debug("Short link slug collision on %r, retrying", slug)

            logger.error("Could not allocate a short link slug after %d attempts", _MAX_SLUG_ATTEMPTS)
            return target_url
        except Exception:  # noqa: BLE001
            logger.exception("ShortLinkService.shorten failed for %r; returning the long URL", target_url)
            return target_url


def models_q_unexpired(now):
    from django.db.models import Q

    return Q(expires_at__isnull=True) | Q(expires_at__gt=now)


shorten = ShortLinkService.shorten
