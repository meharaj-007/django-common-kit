"""The middleware that feeds the tracking tables (PRD §8).

Three, and the order in ``MIDDLEWARE`` matters:

1. ``IPBlockerMiddleware`` — early, before anything expensive; a banned address
   gets its 403 without touching a view.
2. ``RequestLogMiddleware`` — after authentication, so ``request.user`` is set.
3. ``IPTrackingMiddleware`` — likewise.

All three read the client IP through ``request_context.get_client_ip``, which
honours ``X-Forwarded-For`` only as far as ``TRUSTED_PROXY_COUNT`` allows. The
blocker in particular hands out site-wide bans, so an IP the client could
nominate for itself would let anyone ban anyone.
"""

import ipaddress
import logging
import time
from typing import Optional, Set

from django.db.models import Q
from django.http import HttpResponseForbidden
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin

from django_common_kit.conf import app_settings
from django_common_kit.request_context import get_client_ip
from django_common_kit.tracking import redaction
from django_common_kit.tracking.patterns import is_excluded_path, matching_block_pattern
from django_common_kit.tracking.writers import (
    enqueue_tracking_write,
    write_ip_tracking,
    write_request_log,
)

logger = logging.getLogger(__name__)


def _tenant_id(request) -> Optional[str]:
    """Resolved here, while the request exists: the row may be written by a
    worker that never sees it. A string, so the payload survives the queue."""
    from django_common_kit.tenancy import resolve_tenant

    tenant = resolve_tenant(request=request)
    return str(tenant) if tenant else None


def _user_id(request) -> Optional[str]:
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return str(user.pk)
    return None


class RequestLogMiddleware(MiddlewareMixin):
    """Audit log: one row per request, read by id or trace during an incident.

    Carries the request body, the response and the status — so everything it
    stores is redacted before it is written, never at display time. The write
    is handed to the worker, with an inline fallback if the broker is down.
    """

    #: Bodies above this are not read ahead of the view. Reading `request.body`
    #: loads the whole thing into memory, and a multipart upload is exactly the
    #: body that should stream.
    MAX_BODY_READ = 1024 * 1024

    def process_request(self, request):
        request.start_time = time.time()
        self._cache_body(request)

    def _cache_body(self, request):
        """Read the body now so it is still there in ``process_response``.

        Django caches ``request.body`` on first read and raises if it is read
        after a stream consumer (multipart parsing) has taken it. A view that
        never touches ``request.body`` — a plain Django view reading
        ``request.POST`` — leaves nothing to log, so read it here, bounded.
        """
        content_type = (request.META.get("CONTENT_TYPE") or "").lower()
        if content_type.startswith("multipart/"):
            return
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            length = 0
        if 0 < length <= self.MAX_BODY_READ:
            try:
                request.body  # noqa: B018 - reading caches it on request._body
            except Exception:  # noqa: BLE001 - already consumed, or unreadable
                pass

    def process_response(self, request, response):
        if not app_settings.get("TRACKING", "REQUEST_LOG_ENABLED"):
            return response
        if is_excluded_path(request.path):
            return response

        response_time = time.time() - getattr(request, "start_time", time.time())
        try:
            path = request.path
            body = getattr(request, "_body", None)
            request_body = self._capture(body.decode("utf-8", errors="replace") if body else None, path)
            response_content = None
            if not self._skip_response_body(response):
                response_content = self._capture(self._decode(response), path)

            payload = {
                "user_id": _user_id(request),
                "ip_address": get_client_ip(request) or None,
                "endpoint": path[:255],
                "query_string": (redaction.scrub_query_string(
                    request.META.get("QUERY_STRING", ""), path) or "")[:2000] or None,
                "method": request.method,
                "request_body": request_body,
                "status_code": response.status_code,
                "response": response_content,
                "user_agent": request.META.get("HTTP_USER_AGENT", ""),
                "trace_id": (getattr(request, "correlation_id", None) or "")[:50] or None,
                "response_time": response_time,
                "tenant_id": _tenant_id(request),
            }
            enqueue_tracking_write(write_request_log, payload, "request_log")
        except Exception as exc:  # noqa: BLE001 - tracking must never break a response
            logger.error("Failed to capture request log: %s", exc, exc_info=True)
        return response

    @staticmethod
    def _decode(response) -> str:
        if not hasattr(response, "content"):
            return str(response)
        try:
            return response.content.decode("utf-8")
        except UnicodeDecodeError:
            return response.content.decode("utf-8", errors="replace")

    @staticmethod
    def _skip_response_body(response) -> bool:
        """Binary or streamed responses (PDFs above all) are never stored."""
        if getattr(response, "streaming", False):
            return True
        skip = tuple(app_settings.get("TRACKING", "SKIP_BODY_CONTENT_TYPES"))
        content_type = (response.get("Content-Type", "") or "").lower()
        return content_type.startswith(skip) if skip else False

    @staticmethod
    def _capture(content: Optional[str], path: str) -> Optional[str]:
        """Bound the work first, then redact, then bound the result.

        Only the first ``MAX_CONTENT_SIZE`` characters are ever stored, so
        anything past that cut-off cannot leak — and redacting a 2MB body to
        throw 99% of it away is latency the customer pays for nothing.
        """
        if not content:
            return None
        limit = app_settings.get("TRACKING", "MAX_CONTENT_SIZE")
        window = content[:limit]
        redacted = redaction.redact_text(window, path)
        suffix = "... [truncated]" if len(content) > limit else ""
        # Masking can lengthen the text, so re-bound it afterwards.
        if len(redacted) > limit:
            return redacted[:limit] + "... [truncated]"
        return redacted + suffix


class IPTrackingMiddleware:
    """Visit log: one row per request, read as an aggregate over a date range.

    Captures only what is already in the request; geo and device enrichment
    happen in the worker.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not app_settings.get("TRACKING", "IP_TRACKING_ENABLED"):
            return self.get_response(request)
        if is_excluded_path(request.path):
            return self.get_response(request)

        if not hasattr(request, "start_time"):
            request.start_time = time.time()
        request.ip_address = get_client_ip(request)
        request.user_agent = request.META.get("HTTP_USER_AGENT", "")

        response = self.get_response(request)

        # ``ip_tracking.ip_address`` is NOT NULL — without a client IP there is
        # no visit row to write, and dropping it silently in the worker would
        # look like missing traffic.
        if not request.ip_address:
            logger.debug("No client IP for %s, skipping IP tracking", request.path)
            return response

        try:
            enqueue_tracking_write(write_ip_tracking, self._payload(request, response), "ip_tracking")
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to capture IP tracking for %s: %s", request.path, exc, exc_info=True)
        return response

    @staticmethod
    def _payload(request, response) -> dict:
        path = request.path
        payload = {
            "user_id": _user_id(request),
            "ip_address": request.ip_address,
            "endpoint": path[:255],
            "method": request.method,
            "user_agent": request.user_agent,
            "trace_id": (getattr(request, "correlation_id", None) or "")[:50] or None,
            "status_code": response.status_code,
            "response_time": time.time() - request.start_time,
            "tenant_id": _tenant_id(request),
        }
        if app_settings.get("TRACKING", "CAPTURE_ATTRIBUTION"):
            query = request.GET
            payload.update({
                "referer": request.META.get("HTTP_REFERER") or None,
                "query_string": redaction.scrub_query_string(request.META.get("QUERY_STRING", ""), path) or None,
                "utm_source": query.get("utm_source", "")[:255] or None,
                "utm_medium": query.get("utm_medium", "")[:255] or None,
                "utm_campaign": query.get("utm_campaign", "")[:255] or None,
                "utm_term": query.get("utm_term", "")[:255] or None,
                "utm_content": query.get("utm_content", "")[:255] or None,
            })
        return payload


class IPBlockerMiddleware:
    """Detect and ban addresses that probe for ``.env``, ``.git`` and the like.

    The blast radius of a false positive is much wider than it looks: the
    verdict is recorded against an IP, so blocking one signup's verification
    click takes out everyone sharing that address — the customer's whole office
    behind its NAT, or a mobile carrier's CGNAT pool. Three rules follow, all
    enforced below:

    1. A pattern only ever matches on a path-segment boundary, and our own
       token-bearing routes (``IP_BLOCK_EXEMPT_PATHS``) are never inspected.
    2. A block is reversible from the admin *without a deploy* — the blocked
       set is re-read every ``IP_BLOCK_CACHE_TTL_SECONDS`` — and strikes older
       than ``IP_BLOCK_ATTEMPT_WINDOW_SECONDS`` expire instead of accumulating.
    3. A block lifts after ``IP_BLOCK_DURATION_SECONDS``. Permanent blocks 403
       the admin too, so the office whose NAT was banned could not reach the
       admin to unblock itself. ``manage.py unblock_ip`` is the escape hatch.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        # In-process copy of the blocked set plus the moment it goes stale.
        # Loaded lazily on the first request: the database is not reliably
        # available while middleware is being constructed.
        self.blocked_ips_cache: Set[str] = set()
        self._cache_expires_at = 0.0
        # When the set was last read SUCCESSFULLY. `_cache_expires_at` is
        # re-armed even by a failed refresh, so on its own it cannot tell a
        # fresh set from one that has been failing to load for an hour.
        self._cache_loaded_at: Optional[float] = None
        self._consecutive_failures = 0

    # -- settings, read per call so override_settings is honoured ----------

    @staticmethod
    def _setting(name):
        return app_settings.get("TRACKING", name)

    def _whitelist(self):
        networks = []
        for raw in self._setting("IP_WHITELIST"):
            raw = (raw or "").strip()
            if not raw:
                continue
            try:
                networks.append(ipaddress.ip_network(raw, strict=False))
            except ValueError:
                # One bad entry in the env var must not take the site down.
                logger.error("Ignoring invalid IP_WHITELIST entry %r", raw)
        return networks

    # -- the request --------------------------------------------------------

    def __call__(self, request):
        if not self._setting("IP_BLOCKER_ENABLED"):
            return self.get_response(request)

        client_ip = get_client_ip(request)
        # No resolvable IP, nothing to hold responsible. Recording the strike
        # anyway would file every such request under one empty-string row and
        # eventually 403 all of them at once.
        if not client_ip or self._is_whitelisted(client_ip):
            return self.get_response(request)

        if client_ip in self._blocked_ips():
            # Say so. A silent 403 is indistinguishable from a permission
            # failure in a view, so every investigation starts by ruling out
            # the application code.
            logger.warning(
                "[IPBlocker] 403 for blocked IP | ip=%s path=%s (cached block, TTL %ds)",
                client_ip, request.path, self._setting("IP_BLOCK_CACHE_TTL_SECONDS"),
            )
            return HttpResponseForbidden("Access denied. Your IP has been blocked.")

        matched = matching_block_pattern(request.path)
        if not matched:
            return self.get_response(request)

        max_attempts = int(self._setting("IP_BLOCK_MAX_ATTEMPTS"))
        row = self._record_attempt(client_ip, matched)
        if row is not None and row.attempts >= max_attempts:
            if not row.is_active:
                self._block(row)
                self._refresh()
            logger.warning(
                "[IPBlocker] 403 and BLOCKED IP | ip=%s path=%s pattern=%r attempts=%d/%d",
                client_ip, request.path, matched, row.attempts, max_attempts,
            )
            return HttpResponseForbidden("Access denied. Your IP has been blocked.")

        logger.warning(
            "[IPBlocker] 403 for probing path | ip=%s path=%s pattern=%r attempts=%s/%d",
            client_ip, request.path, matched, getattr(row, "attempts", "?"), max_attempts,
        )
        return HttpResponseForbidden("Access denied. Suspicious activity detected.")

    def _is_whitelisted(self, ip: str) -> bool:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            logger.warning("Invalid IP format: %s", ip)
            return False
        return any(address in network for network in self._whitelist())

    # -- the blocked set ----------------------------------------------------

    def _blocked_ips(self) -> Set[str]:
        """Re-read from the table once every TTL.

        Loaded once at boot and never re-read, unticking ``is_active`` in the
        admin changed nothing until the process restarted, and a block issued
        by one worker was invisible to its siblings. A TTL costs one query per
        worker per minute and makes the admin the control it appears to be.
        """
        if time.monotonic() >= self._cache_expires_at:
            self._refresh()
        return self.blocked_ips_cache

    def _refresh(self) -> None:
        from django_common_kit.models import BlockedIPModel

        ttl = int(self._setting("IP_BLOCK_CACHE_TTL_SECONDS"))
        # Set first: a database that is down must not turn into a refresh on
        # every request while it stays down.
        self._cache_expires_at = time.monotonic() + ttl
        retry_after = min(ttl, 5)
        try:
            blocked = BlockedIPModel.objects.filter(is_active=True)
            duration = int(self._setting("IP_BLOCK_DURATION_SECONDS"))
            if duration > 0:
                # Measured from when the ban was applied, never from the last
                # probe; rows predating `blocked_at` fall back to the old basis.
                cutoff = timezone.now() - timezone.timedelta(seconds=duration)
                expired_q = Q(blocked_at__lt=cutoff) | Q(blocked_at__isnull=True, last_attempt__lt=cutoff)
                expired = blocked.filter(expired_q)
                if expired.exists():
                    # Expire, rather than only hide from enforcement — otherwise
                    # the admin shows an IP as blocked long after it stopped
                    # being. Strikes cleared too, so a returning address starts
                    # from zero.
                    count = expired.update(is_active=False, attempts=0, blocked_at=None)
                    logger.info("Expired %d IP block(s) older than %ds", count, duration)
                blocked = blocked.exclude(expired_q)

            self.blocked_ips_cache = set(blocked.values_list("ip_address", flat=True))
            if self._consecutive_failures:
                logger.info("Blocked-IP cache recovered after %d failed refresh(es)", self._consecutive_failures)
            self._cache_loaded_at = time.monotonic()
            self._consecutive_failures = 0
        except Exception as exc:  # noqa: BLE001
            self._refresh_failed(str(exc), retry_after)

    def _refresh_failed(self, reason: str, retry_after: float) -> None:
        """A refresh did not happen. Decide whether the old set may stand.

        Keep it, up to a point: clearing it on the first error meant one
        transient database blip lifted every block on this worker for a full
        TTL. Past ``IP_BLOCK_CACHE_MAX_STALE_SECONDS`` drop it and fail open —
        a worker enforcing a snapshot it can no longer verify produces an
        intermittent, un-reproducible 403 that survives every documented way
        of lifting it.
        """
        self._consecutive_failures += 1
        self._cache_expires_at = time.monotonic() + retry_after
        max_stale = int(self._setting("IP_BLOCK_CACHE_MAX_STALE_SECONDS"))
        stale_for = None if self._cache_loaded_at is None else time.monotonic() - self._cache_loaded_at

        if stale_for is not None and stale_for >= max_stale:
            dropped = len(self.blocked_ips_cache)
            self.blocked_ips_cache = set()
            self._cache_loaded_at = None
            logger.error(
                "Blocked-IP cache unreadable for %.0fs (limit %ds) after %d failure(s): "
                "dropping %d cached block(s) and failing open. Last error: %s",
                stale_for, max_stale, self._consecutive_failures, dropped, reason,
            )
            return
        # One loud line when it starts, then quiet.
        if self._consecutive_failures == 1:
            logger.error("Error refreshing blocked IPs cache: %s", reason)
        else:
            logger.debug("Blocked-IP cache refresh still failing (%d in a row): %s",
                         self._consecutive_failures, reason)

    # -- strikes and bans ---------------------------------------------------

    def _record_attempt(self, ip: str, pattern: str):
        """Strikes decay: a previous one older than the attempt window restarts
        the counter. Without that ``attempts`` was monotonic for the life of the
        row, so an address that tripped a pattern once a year for three years
        was eventually blocked — by which time it belonged to someone else."""
        from django_common_kit.models import BlockedIPModel

        now = timezone.now()
        window = int(self._setting("IP_BLOCK_ATTEMPT_WINDOW_SECONDS"))
        reason = f"Attempting to access {pattern}"
        try:
            row, created = BlockedIPModel.objects.get_or_create(
                ip_address=ip,
                defaults={"reason": reason, "attempts": 1, "is_active": False},
            )
            if not created:
                # `<= 0` means strikes never decay, matching how 0 disables
                # expiry for the block duration.
                stale = window > 0 and (
                    row.last_attempt is None or (now - row.last_attempt).total_seconds() > window
                )
                row.attempts = 1 if stale else row.attempts + 1
                row.reason = reason
                row.last_attempt = now
                row.save(update_fields=["attempts", "reason", "last_attempt", "updated_at"])
            return row
        except Exception as exc:  # noqa: BLE001
            logger.error("Error recording IP block attempt for %s: %s", ip, exc, exc_info=True)
            return None

    @staticmethod
    def _block(row) -> None:
        try:
            row.is_active = True
            row.blocked_at = timezone.now()
            # `created_at` is the only record of when this IP was first seen,
            # and `last_attempt` was just advanced by _record_attempt.
            row.save(update_fields=["is_active", "blocked_at", "updated_at"])
        except Exception as exc:  # noqa: BLE001
            logger.error("Error blocking IP %s: %s", row.ip_address, exc, exc_info=True)
