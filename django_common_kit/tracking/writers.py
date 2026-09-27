"""The tracking writers (PRD §8) — plain functions first, Celery tasks if Celery is there.

The tables are written from a worker, not from the response cycle: geo-IP and
user-agent enrichment need network or CPU, and happen here behind a 24h per-IP
cache, so no request ever waits on a lookup. Middleware calls
``enqueue_tracking_write``, which falls back to an inline write — and bumps a
counter — if the broker is unreachable. Never to a log line and a shrug.

Celery is optional. Every function is an ordinary callable that works with
nothing installed; without Celery, ``enqueue_tracking_write`` writes inline
every time, unenriched, and says so once.
"""

import logging
from typing import Any, Dict, Optional

from django.core.cache import cache

from django_common_kit.conf import app_settings
from django_common_kit.tracking import metrics

try:  # pragma: no cover - depends on what the project installed
    from celery import shared_task
except ImportError:  # pragma: no cover
    shared_task = None

logger = logging.getLogger(__name__)

GEO_CACHE_PREFIX = "geoip"
GEO_CACHE_TIMEOUT = 60 * 60 * 24
# An answer that is not a location is asked again sooner than a location is.
GEO_MISS_CACHE_TIMEOUT = 60 * 15
GEO_LOOKUP_TIMEOUT = (1.5, 2.0)  # (connect, read) seconds — in a worker, not in the response

# While this key is set no lookup is made for any IP. A provider that is down or
# has banned the server otherwise costs every new visitor a full connect timeout
# of worker time, and the worker also serves the project's other queues.
GEO_BACKOFF_KEY = f"{GEO_CACHE_PREFIX}:backoff"
GEO_UNREACHABLE_BACKOFF = 60 * 5
GEO_RATE_LIMIT_BACKOFF = 60  # when a 429 does not say how long

_EMPTY_DEVICE = {"device": "", "browser": "", "operating_system": ""}
_warned_no_celery = False


# -- enrichment -------------------------------------------------------------

def _is_public_ip(ip: str) -> bool:
    import ipaddress

    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def _cache_get(key: str) -> Any:
    try:
        return cache.get(key)
    except Exception:  # noqa: BLE001
        return None


def _cache_set(key: str, value: Any, timeout: int) -> None:
    try:
        cache.set(key, value, timeout=timeout)
    except Exception:  # noqa: BLE001
        pass


def _pause_geo_lookups(seconds: int, reason: str) -> None:
    """Stop every lookup for ``seconds``. Warns once per pause, not once per IP."""
    seconds = max(int(seconds), 1)
    try:
        started = cache.add(GEO_BACKOFF_KEY, reason, timeout=seconds)
    except Exception:  # noqa: BLE001
        return
    if started:
        logger.warning("Geo lookups paused for %ss: %s", seconds, reason)


def _header_seconds(response, *names: str) -> Optional[int]:
    for name in names:
        try:
            return int(response.headers[name])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def resolve_geo(ip: str) -> Dict[str, Any]:
    """Geo-IP lookup, cached by IP for 24h. Worker only.

    The provider URL is a setting; the default is ip-api.com's free endpoint,
    which is HTTP, rate-limited and for non-commercial use — a project in
    production points ``TRACKING["GEO_LOOKUP_URL"]`` at its own provider or
    sets it empty to skip lookups entirely.

    A provider that is unreachable or answers 429 pauses every lookup rather
    than being retried per IP. The rate-limit headers ip-api sends (``X-Rl``
    requests left, ``X-Ttl`` seconds to reset) are honoured when present, so the
    free endpoint is left alone before it bans the server, not after.
    """
    if not ip or not _is_public_ip(ip):
        return {}
    template = app_settings.get("TRACKING", "GEO_LOOKUP_URL")
    if not template:
        return {}

    cache_key = f"{GEO_CACHE_PREFIX}:{ip}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    # Paused: nothing is cached for this IP, so it is looked up once lookups resume.
    if _cache_get(GEO_BACKOFF_KEY):
        return {}

    try:
        import requests
    except ImportError:
        return {}

    try:
        response = requests.get(template.format(ip=ip), timeout=GEO_LOOKUP_TIMEOUT)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
        _pause_geo_lookups(GEO_UNREACHABLE_BACKOFF, f"provider unreachable ({type(exc).__name__})")
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Geo lookup failed for %s: %s", ip, exc)
        _cache_set(cache_key, {}, GEO_MISS_CACHE_TIMEOUT)
        return {}

    if response.status_code == 429:
        reset_in = _header_seconds(response, "Retry-After", "X-Ttl") or GEO_RATE_LIMIT_BACKOFF
        _pause_geo_lookups(reset_in, "rate limited (HTTP 429)")
        return {}
    if response.headers.get("X-Rl") == "0":
        _pause_geo_lookups(_header_seconds(response, "X-Ttl") or GEO_RATE_LIMIT_BACKOFF, "rate limit reached")

    data: Dict[str, Any] = {}
    if response.status_code == 200:
        try:
            payload = response.json()
        except ValueError:
            payload = {"status": "fail", "message": "response is not JSON"}
        if payload.get("status", "success") == "success":
            data = payload
        else:
            logger.warning("Geo lookup failed for %s: %s", ip, payload.get("message", "unknown"))
    else:
        logger.warning("Geo lookup HTTP %s for %s", response.status_code, ip)

    _cache_set(cache_key, data, GEO_CACHE_TIMEOUT if data else GEO_MISS_CACHE_TIMEOUT)
    return data


def parse_user_agent(user_agent: str) -> Dict[str, str]:
    """Device / browser / OS from a UA string. Worker only. ``user-agents`` is
    optional; without it every column is blank."""
    if not user_agent:
        return dict(_EMPTY_DEVICE)
    try:
        from user_agents import parse

        parsed = parse(user_agent)
        return {
            "device": parsed.device.family or "",
            "browser": parsed.browser.family or "",
            "operating_system": parsed.os.family or "",
        }
    except ImportError:
        return dict(_EMPTY_DEVICE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("User-agent parse failed: %s", exc)
        return dict(_EMPTY_DEVICE)


# -- enqueue with inline fallback -------------------------------------------

def enqueue_tracking_write(task, payload: Dict[str, Any], label: str) -> None:
    """Hand a tracking payload to the worker; write inline if there is none.

    Two things keep the fallback cheap enough to sit in a response:

    * ``retry=False`` plus the broker socket timeouts in the project's settings,
      so a dead broker raises in ~2s instead of retrying inside the request.
    * ``enrich=False``, so the inline write skips the geo HTTP call entirely.
      A row with no country beats a customer waiting three seconds for one.
    """
    global _warned_no_celery
    delay = getattr(task, "apply_async", None)
    if delay is not None:
        try:
            delay(args=[payload], retry=False)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tracking queue unavailable for %s, writing inline: %s", label, exc)
            metrics.increment(metrics.TRACKING_ENQUEUE_FAILED, label)
    elif not _warned_no_celery:
        logger.info("Celery not installed: tracking rows are written inline, unenriched.")
        _warned_no_celery = True

    try:
        task(payload, enrich=False)
        if delay is not None:
            metrics.increment(metrics.TRACKING_INLINE_FALLBACK, label)
    except Exception as exc:  # noqa: BLE001
        logger.error("Inline tracking write failed for %s: %s", label, exc, exc_info=True)
        metrics.increment(metrics.TRACKING_WRITE_FAILED, label)


def _resolve_user(user_id: Optional[str]):
    if not user_id:
        return None
    from django.contrib.auth import get_user_model

    return get_user_model()._default_manager.filter(pk=user_id).first()


# -- writers ----------------------------------------------------------------

def write_request_log(payload: Dict[str, Any], enrich: bool = True) -> None:
    """One audit row. The payload is already redacted and truncated.
    ``enrich`` is accepted for symmetry; this row needs none."""
    from django_common_kit.models import RequestLog

    data = dict(payload)
    data["user"] = _resolve_user(data.pop("user_id", None))
    try:
        RequestLog.objects.create(**data)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to write request log: %s", exc, exc_info=True)
        metrics.increment(metrics.TRACKING_WRITE_FAILED, "request_log")


def write_ip_tracking(payload: Dict[str, Any], enrich: bool = True) -> None:
    """Enrich (geo + UA) and write one visit row. With ``enrich=False`` — the
    inline fallback — no HTTP call ever happens in a response cycle."""
    from django_common_kit.models import IPTrackingModel

    data = dict(payload)
    geo = resolve_geo(data.get("ip_address") or "") if enrich else {}
    device = parse_user_agent(data.get("user_agent") or "") if enrich else dict(_EMPTY_DEVICE)

    data["user"] = _resolve_user(data.pop("user_id", None))
    data.update({
        "device_type": device["device"][:50],
        "browser": device["browser"][:50],
        "operating_system": device["operating_system"][:50],
        "country": geo.get("country", ""),
        "country_code": geo.get("countryCode", ""),
        "continent": geo.get("continent", ""),
        "continent_code": geo.get("continentCode", ""),
        "region": geo.get("region", ""),
        "region_name": geo.get("regionName", ""),
        "city": geo.get("city", ""),
        "district": geo.get("district", ""),
        "zip_code": geo.get("zip", ""),
        "latitude": geo.get("lat"),
        "longitude": geo.get("lon"),
        "timezone": geo.get("timezone", ""),
        "currency": geo.get("currency", ""),
        "isp": geo.get("isp", ""),
        "organization": geo.get("org", ""),
        "as_number": geo.get("as", ""),
        "is_mobile": geo.get("mobile"),
    })
    try:
        IPTrackingModel.objects.create(**data)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to write IP tracking row: %s", exc, exc_info=True)
        metrics.increment(metrics.TRACKING_WRITE_FAILED, "ip_tracking")


# -- Celery registration ----------------------------------------------------
# Registered under this module's own import path, so a beat entry and an import
# statement read as the same thing.

if shared_task is not None:  # pragma: no cover
    write_request_log = shared_task(
        name="django_common_kit.tracking.writers.write_request_log", ignore_result=True
    )(write_request_log)
    write_ip_tracking = shared_task(
        name="django_common_kit.tracking.writers.write_ip_tracking", ignore_result=True
    )(write_ip_tracking)
