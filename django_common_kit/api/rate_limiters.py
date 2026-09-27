"""View-level rate limiting outside DRF's throttle classes (PRD §5.2).

``api/throttling.py`` covers DRF views through ``throttle_classes``; this module
is for everything that is not a DRF view or that needs a limit *inside* a view —
a plain Django form, a public endpoint that must count only requests that passed
validation, a guard around one expensive side effect.

Two design points:

**The counter is an atomic ``add``+``incr``, not a timestamp list.** A list of
timestamps per key with a get-filter-set is a true sliding window and a race:
two requests that read the list together both see ``n-1`` and both pass.
``add`` and ``incr`` are atomic on Redis and lock-guarded on LocMemCache, so the
limit holds under a burst. The cost is a *fixed* window — a caller who exhausts
it at second 59 waits one second — which is the right trade for an abuse guard.

**Rejected requests give back the IP slot.** When the identity limit rejects a
request, the IP hit it recorded a moment earlier is released. Otherwise one
abusive account throttles everyone behind the same office NAT.

The counters live in ``throttle_cache()``, the same cache the DRF throttles use,
so one setting (``RATE_LIMIT["CACHE_ALIAS"]``) moves every limit off a
``DummyCache`` default at once.

Every limiter fails **open** on a cache error. An infrastructure problem must
never hard-block real users; the log line is the alarm.
"""

import hashlib
import logging
import math
from dataclasses import dataclass
from functools import wraps
from typing import Optional

from django.http import HttpResponse, HttpResponseRedirect

from django_common_kit.api.throttling import throttle_cache
from django_common_kit.conf import app_settings
from django_common_kit.request_context import get_client_ip

logger = logging.getLogger(__name__)

RATE_LIMIT_429_HTML = """<!DOCTYPE html>
<html><head><title>Too many requests</title></head><body>
<h1>Too many requests</h1>
<p>You have made too many requests. Please try again later.</p>
</body></html>
"""


# -- the counter ------------------------------------------------------------

def window_hit(cache_key: str, limit: int, window: int):
    """Atomic fixed-window counter. Returns ``(allowed, retry_after_seconds)``.

    ``add()`` only writes when the key is absent, so the first request of a
    window establishes both the counter and its TTL; later hits ``incr`` the
    same key. Fails open on any cache error.
    """
    try:
        cache = throttle_cache()
        cache.add(cache_key, 0, window)
        try:
            count = cache.incr(cache_key)
        except ValueError:
            # Expired between add() and incr(); re-seed and count this hit.
            cache.add(cache_key, 0, window)
            count = cache.incr(cache_key)
        if count > limit:
            ttl = cache.ttl(cache_key) if hasattr(cache, "ttl") else None
            retry = int(ttl) if ttl and ttl > 0 else window
            return False, max(1, retry)
        return True, 0
    except Exception as exc:  # noqa: BLE001
        logger.error("[rate_limit] %s: %s", cache_key, exc)
        return True, 0



def release_hit(cache_key: str) -> None:
    """Give back a slot taken by ``window_hit``. Best-effort."""
    try:
        throttle_cache().decr(cache_key)
    except Exception:  # noqa: BLE001 - key gone or backend down; nothing to release
        pass


def _retry_minutes(seconds: int) -> int:
    return max(1, math.ceil(seconds / 60))


def _hash(value: str) -> str:
    """Identities go into cache keys hashed, never in clear."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _identity_from_request(request) -> Optional[str]:
    """The email on the request, for the per-identity limit.

    ``request.data`` first: ``request.POST`` is empty for a JSON body, and a
    limit that reads only ``POST`` never fires on the DRF views it is for.
    """
    data = getattr(request, "data", None)
    if isinstance(data, dict):
        email = data.get("email")
        if email:
            return str(email).strip().lower()
    if request.method == "POST":
        email = request.POST.get("email", "")
        if email:
            return email.strip().lower()
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        email = getattr(user, "email", "") or ""
        return email.strip().lower() or None
    return None


# -- the guard --------------------------------------------------------------

@dataclass
class RateLimitRejection:
    """Returned by ``enforce_cooldown`` when a limit is tripped."""

    message: str
    scope: str          # 'ip' | 'identity'
    retry_after: int    # seconds


def enforce_cooldown(
    request,
    *,
    key_prefix: str,
    limit: Optional[int] = None,
    window: Optional[int] = None,
    limit_param: Optional[str] = None,
    window_param: Optional[str] = None,
    limit_default: int = 5,
    window_default: int = 60,
    identity=None,
    enabled: bool = True,
):
    """IP (+ optional identity) cooldown. Returns a ``RateLimitRejection`` to
    block, or ``None`` to allow. The caller chooses the reject style.

    Call it **after** cheap validation and **before** the expensive side effect
    (the DB write, the email send), because each check records a hit — a guard
    placed before validation spends the quota on requests that would have
    failed anyway.

    The limit and window come from, in order: the explicit arguments; the
    parameter cache under ``limit_param``/``window_param`` (so ops can tune a
    live limit without a deploy); the ``*_default`` values.

    ``enabled`` switches off this one guard, under the global
    ``RATE_LIMIT["ENABLED"]``. It takes the value rather than the name of a
    setting to read: which feature has its own kill switch, and what it is
    called, is the project's vocabulary (§2), and a setting read by name from
    here would be a second place configuration comes from (§4). The caller
    reads its own flag at call time, so ``override_settings`` still reaches it.
    """
    if not enabled or not app_settings.get("RATE_LIMIT", "ENABLED"):
        return None

    if limit is None or window is None:
        from django_common_kit.parameters.cache import ParameterCache

        if limit is None:
            limit = ParameterCache.get_int(limit_param, limit_default) if limit_param else limit_default
        if window is None:
            window = ParameterCache.get_int(window_param, window_default) if window_param else window_default

    ip_key = f"{key_prefix}_ip_{get_client_ip(request) or 'unknown'}"
    ok, retry = window_hit(ip_key, limit, window)
    if not ok:
        return RateLimitRejection(
            message=f"Too many requests from your network. Please try again in {_retry_minutes(retry)} minute(s).",
            scope="ip", retry_after=retry,
        )

    if identity:
        ident = str(identity).strip().lower()
        if ident:
            ok, retry = window_hit(f"{key_prefix}_id_{_hash(ident)}", limit, window)
            if not ok:
                # This request is rejected, so give back the IP slot it took —
                # otherwise rejected traffic throttles others behind a shared IP.
                release_hit(ip_key)
                return RateLimitRejection(
                    message=f"Too many requests for this account. Please try again in {_retry_minutes(retry)} minute(s).",
                    scope="identity", retry_after=retry,
                )
    return None


# -- the decorators ---------------------------------------------------------

def _find_request(args):
    """The request, from a function view ``(request, …)`` or a method
    ``(self, request, …)``. ``None`` when neither shape fits."""
    if args and hasattr(args[0], "META"):
        return args[0], None
    if len(args) >= 2 and hasattr(args[1], "META"):
        return args[1], args[0]
    return None, None


def _rate_limit(reject, *, requests, window, key_prefix, use_email):
    """Build a decorator that answers a tripped limit with ``reject(request, rejection)``."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            request, view = _find_request(args)
            if request is None:
                logger.warning("[rate_limit] no request found for %s", func.__name__)
                return func(*args, **kwargs)

            view_name = f"{type(view).__name__}_{func.__name__}" if view is not None else func.__name__
            endpoint = f"{request.method}_{request.path_info.replace('/', '_')}"
            rejection = enforce_cooldown(
                request,
                key_prefix=f"rate_limit_{key_prefix}_{view_name}_{endpoint}",
                limit=requests, window=window,
                identity=_identity_from_request(request) if use_email else None,
            )
            if rejection is not None:
                logger.warning(
                    "[rate_limit] %s limit for %s %s retry_after=%ss",
                    rejection.scope, request.method, request.path, rejection.retry_after,
                )
                return reject(request, rejection)
            return func(*args, **kwargs)

        return wrapper

    return decorator


def _reject_api(request, rejection):
    from django_common_kit.api.response import ApiResponse

    return ApiResponse.too_many_requests(
        message=rejection.message, retry_after=rejection.retry_after,
    )


def _reject_html(request, rejection):
    """Send the browser back where it came from, or to the configured fallback.

    ``RATE_LIMIT["HTML_FALLBACK_URL"]`` names the fallback; unset, the answer
    is a plain 429 page rather than a guess at a project URL.
    """
    target = request.META.get("HTTP_REFERER") or app_settings.get("RATE_LIMIT", "HTML_FALLBACK_URL")
    if target:
        return HttpResponseRedirect(target)
    return _reject_http(request, rejection)


def _reject_http(request, rejection):
    response = HttpResponse(RATE_LIMIT_429_HTML, status=429, content_type="text/html")
    response["Retry-After"] = str(rejection.retry_after)
    return response


def api_rate_limit(requests=5, window=60, key_prefix="api", use_email=True):
    """For DRF views: a 429 in the envelope, with ``Retry-After``.

    ::

        @method_decorator(api_rate_limit(requests=5, window=60), name="post")
        class QuoteRequestView(APIView): ...
    """
    return _rate_limit(_reject_api, requests=requests, window=window, key_prefix=key_prefix, use_email=use_email)


def html_rate_limit(requests=10, window=60, key_prefix="html", use_email=True):
    """For form views: redirect back to the referer, else the fallback, else 429."""
    return _rate_limit(_reject_html, requests=requests, window=window, key_prefix=key_prefix, use_email=use_email)


def django_http_rate_limit(requests=15, window=60, key_prefix="django_http"):
    """For plain Django views: a 429 HTML page, no redirect."""
    return _rate_limit(_reject_http, requests=requests, window=window, key_prefix=key_prefix, use_email=False)

