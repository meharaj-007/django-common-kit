"""Per-request facts that code below the view layer needs (PRD §6.2, §8).

Three things are read far from the view that produced them: who is acting (the
history trail's actor), where they are acting from (the tracking tables' IP), and
which request a row belongs to (the correlation id that ties an audit row, a log
line and a response together).

Threading them through every ``save()`` does not survive contact with a
codebase; what is here instead is a thread-local set by middleware and cleared
in a ``finally``. The cost is stated plainly rather than hidden: **a save outside a
request has no actor, and records none rather than guessing one.** A Celery task,
a management command and a migration all write history with ``changed_by = NULL``,
which is correct — nobody clicked anything.

Under ASGI each request still gets its own thread from the pool, so the
thread-local holds; under true async views it does not, and the middleware clears
it either way. A project running async views should pass the actor explicitly.
"""

import functools
import ipaddress
import logging
import threading
import uuid

from django_common_utils.conf import app_settings

logger = logging.getLogger(__name__)

CORRELATION_HEADER = "X-Request-Id"

_state = threading.local()


# -- the current request ----------------------------------------------------

def set_current_request(request):
    """Called by ``CurrentRequestMiddleware``. Pass ``None`` to clear."""
    _state.request = request


def get_current_request():
    return getattr(_state, "request", None)


def get_current_user():
    """The authenticated user of the request in flight, or ``None``.

    ``AnonymousUser`` is returned as ``None``: it is not a row anything can
    foreign-key to, and every caller here wants an actor or nothing.
    """
    request = get_current_request()
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return user


# -- re-entrancy guard for the history receivers ----------------------------

def is_tracking_history():
    return getattr(_state, "tracking_history", False)


def set_tracking_history(value):
    """Guard against a history write triggering its own history write.

    ``ModelHistory`` is itself a model, and its ``post_save`` would otherwise
    record the recording. The flag is thread-local for the same reason the
    request is.
    """
    _state.tracking_history = value


# -- request facts ----------------------------------------------------------

def get_client_ip(request=None):
    """The caller's IP, honouring ``X-Forwarded-For`` only as far as the
    deployment actually trusts it.

    ``REMOTE_ADDR`` alone is wrong behind a proxy — every row would record the
    load balancer. Trusting the whole ``X-Forwarded-For`` chain is worse: a
    client can prepend any address it likes, so the leftmost entry is
    attacker-controlled. ``TRACKING["TRUSTED_PROXY_COUNT"]`` says how many hops
    the infrastructure appends; count that many back from the right and take the
    first entry the client could not have forged.

    The default is 0 — no proxy, header ignored. A project behind a load
    balancer must set the real number; setting it too high hands the chain back
    to the client. ``TRUSTED_PROXY_IPS``, when set, also requires the connection
    to come from one of those proxies: a request that bypassed the proxy wrote
    the whole header itself.

    Whatever the header yields must parse as an IP address, or ``REMOTE_ADDR``
    is used instead — the value goes into ``GenericIPAddressField`` columns and
    the blocker's ban list.
    """
    request = request or get_current_request()
    if request is None:
        return None

    remote = request.META.get("REMOTE_ADDR") or None
    trusted = app_settings.get("TRACKING", "TRUSTED_PROXY_COUNT") or 0
    if trusted and _from_trusted_proxy(remote):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        chain = [part.strip() for part in forwarded.split(",") if part.strip()]
        if len(chain) >= trusted:
            candidate = chain[-trusted]
            if _is_ip(candidate):
                return candidate

    return remote


def _is_ip(value):
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _from_trusted_proxy(remote):
    """Whether the peer may speak for the client. Any peer, unless
    ``TRUSTED_PROXY_IPS`` names the proxies."""
    entries = app_settings.get("TRACKING", "TRUSTED_PROXY_IPS")
    if not entries:
        return True
    if not remote or not _is_ip(remote):
        return False
    address = ipaddress.ip_address(remote)
    # A dual-stack socket reports an IPv4 peer as ::ffff:a.b.c.d.
    candidates = {address, getattr(address, "ipv4_mapped", None)} - {None}
    return any(a in network for a in candidates for network in _proxy_networks(tuple(entries)))


@functools.lru_cache(maxsize=8)
def _proxy_networks(entries):
    """``TRUSTED_PROXY_IPS`` parsed once per distinct value. An entry that is
    not an address or a network is skipped with a warning — trusting less than
    configured, never more."""
    networks = []
    for entry in entries:
        try:
            networks.append(ipaddress.ip_network(str(entry).strip(), strict=False))
        except ValueError:
            logger.warning("Ignoring TRACKING TRUSTED_PROXY_IPS entry that is not an IP or network: %r", entry)
    return tuple(networks)


def get_user_agent(request=None):
    request = request or get_current_request()
    if request is None:
        return ""
    return request.META.get("HTTP_USER_AGENT", "") or ""


def get_correlation_id(request=None):
    """The id stamped on the request by ``CurrentRequestMiddleware``.

    The id *value*, not the header name.
    """
    request = request or get_current_request()
    if request is None:
        return None
    return getattr(request, "correlation_id", None)


def new_correlation_id():
    return str(uuid.uuid4())
