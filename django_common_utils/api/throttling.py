"""Rate throttling (PRD §5.2).

Two families:

- the logged throttles with fixed scopes — ``anon``/``user``, the
  ``sensitive_*`` pair for credential endpoints, the ``token_*`` pair for
  session upkeep, and the per-recipient ``email_target``;
- ``ScopedIPRateThrottle`` / ``ScopedEmailRateThrottle``, which let a *view*
  name its scope instead of the throttle class fixing it.

Every class here logs the scope, rate and key when it rejects a request. DRF's
``Throttled`` carries none of that, so without it a 429 leaves no trace of which
budget was exhausted, and "we keep getting rate limited" is unanswerable. The
other half of that answer — which endpoint and caller drained it — is logged by
``log_throttled`` in ``django_common_utils.api.exceptions``.

An email is keyed as a sha256, never the raw address, so user addresses never
sit in a shared cache in clear.
"""

import hashlib
import logging

from django.core.cache import caches
from rest_framework.throttling import (
    AnonRateThrottle,
    ScopedRateThrottle,
    SimpleRateThrottle,
    UserRateThrottle,
)

from django_common_utils.conf import app_settings

logger = logging.getLogger(__name__)

# Google's consumer mail domains, whose addressing rules are canonicalised so
# alias tricks cannot multiply the per-recipient budget. Scoped tightly to these
# hostnames: Workspace custom domains are indistinguishable by hostname, and
# other providers handle dots and +tags differently, so folding them would merge
# real users onto one bucket.
_GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}


def throttle_cache():
    """The cache every limit counts in — never the dummy backend.

    ``default`` is commonly ``DummyCache`` under DEBUG, so counting there makes
    every limit silently unenforced in development and in the test runner that
    shares those settings. A throttle that does not throttle and does not say
    so is worse than no throttle. ``RATE_LIMIT["CACHE_ALIAS"]`` names a real
    cache; the view-level limiters in ``rate_limiters.py`` count here too.
    """
    return caches[app_settings.get("RATE_LIMIT", "CACHE_ALIAS")]


def canonicalize_email(email):
    """Fold an address to the inbox it actually delivers to.

    Aliases of one Gmail account then share a single throttle bucket instead of
    each getting a fresh budget. For gmail.com / googlemail.com: drop any
    ``+tag``, strip dots from the local part, and normalise the domain (Gmail
    ignores all three). Every other domain is only lowercased and stripped —
    left alone on purpose, since dot and plus semantics vary by provider and
    over-folding would collapse distinct users onto one budget.
    """
    email = (email or "").strip().lower()
    if "@" not in email:
        return email
    local, _, domain = email.rpartition("@")
    if domain in _GMAIL_DOMAINS:
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def hash_ident(value):
    """A stable, non-reversible cache identity for an email address."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ThrottleCacheMixin:
    """Count in ``throttle_cache()`` rather than whatever ``default`` is."""

    @property
    def cache(self):
        return throttle_cache()

    @cache.setter
    def cache(self, value):  # DRF assigns ``cache`` as a class attribute
        pass


class LoggedThrottleFailureMixin:
    """Log every throttle rejection before DRF turns it into a 429.

    Logged at the throttle, not in the exception handler, because the scope, the
    configured rate and the cache key only exist here. The key embeds the
    identity the scope is keyed on — a client IP for the anon scopes, a user pk
    for the user scopes, a sha256 for ``email_target`` (never the raw address).
    """

    def throttle_failure(self):
        try:
            logger.warning(
                "[throttling] limit reached scope=%s rate=%s key=%s retry_after=%ss",
                self.scope, self.rate, self.key, int(self.wait() or 0),
            )
        except Exception as exc:  # noqa: BLE001 - logging must never break the 429
            logger.warning("[throttling] limit reached (could not describe): %s", exc)
        return super().throttle_failure()


class LoggedAnonRateThrottle(LoggedThrottleFailureMixin, ThrottleCacheMixin, AnonRateThrottle):
    """DRF's per-IP throttle (``anon`` scope), plus a log line on rejection."""


class LoggedUserRateThrottle(LoggedThrottleFailureMixin, ThrottleCacheMixin, UserRateThrottle):
    """DRF's per-user throttle (``user`` scope), plus a log line on rejection."""


class SensitiveAnonRateThrottle(LoggedAnonRateThrottle):
    """Unauthenticated requests to credential endpoints — login, register,
    password reset. Scope ``sensitive_anon``, per IP."""

    scope = "sensitive_anon"


class SensitiveUserRateThrottle(LoggedUserRateThrottle):
    """Authenticated counterpart. Scope ``sensitive_user``, per user."""

    scope = "sensitive_user"


class TokenAnonRateThrottle(LoggedAnonRateThrottle):
    """Per-IP throttle for the session-token endpoints (refresh, verify).

    Kept separate from ``sensitive_anon`` on purpose. Both endpoints take the
    token in the request body with no ``Authorization`` header, so DRF sees an
    anonymous request and keys them on the client IP — which meant ordinary
    session upkeep was spending the same per-IP budget as login and password
    reset. One office or mobile carrier behind a single NAT could exhaust the
    abuse budget by reloading tabs, 429ing logins for everyone on that address.
    This scope is sized for session traffic; ``sensitive_anon`` stays tight for
    the credential endpoints it is actually protecting.
    """

    scope = "token_anon"


class TokenUserRateThrottle(LoggedUserRateThrottle):
    """Per-user counterpart to ``TokenAnonRateThrottle``. Scope ``token_user``."""

    scope = "token_user"


class EmailTargetRateThrottle(LoggedThrottleFailureMixin, ThrottleCacheMixin, SimpleRateThrottle):
    """Per-recipient throttle for endpoints that mail an address from the body.

    Keyed on the normalised recipient, so every source IP shares one bucket per
    address. This is what actually caps inbox-bombing: the anon and user
    throttles only limit a single source, and a botnet is not a single source.

    No email in the body means no key, which means not throttled — serializer
    validation then rejects the request anyway.
    """

    scope = "email_target"

    def get_cache_key(self, request, view):
        data = getattr(request, "data", None)
        email = canonicalize_email(data.get("email") if isinstance(data, dict) else None)
        if not email:
            return None
        return self.cache_format % {"scope": self.scope, "ident": hash_ident(email)}


class ScopedIPRateThrottle(LoggedThrottleFailureMixin, ThrottleCacheMixin, ScopedRateThrottle):
    """``ScopedRateThrottle`` keyed on the client address, always.

    The parent keys an authenticated request on the user id, which is the wrong
    key here: these are the endpoints a caller reaches *before* being
    authenticated, and on the few that accept a session the interesting question
    is still "how many attempts from this client".
    """

    def get_cache_key(self, request, view):
        if not getattr(self, "scope", None):
            return None
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class ScopedEmailRateThrottle(LoggedThrottleFailureMixin, ThrottleCacheMixin, ScopedRateThrottle):
    """``ScopedRateThrottle`` keyed on the email address in the request body.

    This is what bounds an attempt spread across many clients against one
    account. The address comes from the body rather than from the authenticated
    user because on these endpoints there is no authenticated user, and it is
    canonicalised and hashed so ``Owner@example.com`` and ``owner@example.com``
    share a bucket without either landing in a cache key.

    A request with no address falls back to the client address: a caller
    hammering the endpoint with malformed bodies is still a caller hammering the
    endpoint.
    """

    scope_suffix = "_email"

    def get_cache_key(self, request, view):
        scope = getattr(self, "scope", None)
        if not scope:
            return None

        data = getattr(request, "data", None)
        email = canonicalize_email(data.get("email") if isinstance(data, dict) else None)
        ident = hash_ident(email) if email else self.get_ident(request)
        return self.cache_format % {"scope": scope, "ident": ident}

    def allow_request(self, request, view):
        """Use the ``<scope>_email`` rate when one is declared.

        A view usually wants a *tighter* per-account limit than its per-IP one —
        one person legitimately signs in a handful of times an hour, while a
        whole office shares an address. Declaring both under one scope name would
        force them to be equal.
        """
        scope = getattr(view, self.scope_attr, None)
        if scope and f"{scope}{self.scope_suffix}" in self.THROTTLE_RATES:
            self.scope = f"{scope}{self.scope_suffix}"
            self.rate = self.get_rate()
            self.num_requests, self.duration = self.parse_rate(self.rate)
            return super(ScopedRateThrottle, self).allow_request(request, view)
        return super().allow_request(request, view)


#: The scopes DRF keys on the user's pk — the only ones reconstructible from a
#: user row. Derived from the classes rather than spelled as literals so that a
#: renamed scope cannot silently orphan the reset below.
USER_KEYED_THROTTLE_SCOPES = (
    UserRateThrottle.scope,
    SensitiveUserRateThrottle.scope,
    TokenUserRateThrottle.scope,
)


def reset_user_throttles(user):
    """Clear the throttle windows keyed to this user; returns the scopes cleared.

    A support tool: hand an authenticated user their API budget back without
    waiting out the window.

    Deliberately partial, and the omission is not a gap to fill. The other scopes
    are not user-derivable: ``anon`` and ``sensitive_anon`` are keyed on the
    client IP, which a user row does not record, and ``email_target`` on a hash
    of the recipient address, reconstructible from the address and not from the
    account. An IP- or email-keyed budget has to expire on its own.
    """
    cache = throttle_cache()
    cleared = []
    for scope in USER_KEYED_THROTTLE_SCOPES:
        key = SimpleRateThrottle.cache_format % {"scope": scope, "ident": user.pk}
        try:
            cache.delete(key)
            cleared.append(scope)
        except Exception as exc:  # noqa: BLE001 - best-effort, never 500 the caller
            logger.warning("[throttling] could not clear %s for %s: %s", key, user.pk, exc)
    return cleared
