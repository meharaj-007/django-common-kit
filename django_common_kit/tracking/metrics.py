"""Minimal counters backed by the cache (PRD §8).

For operational signals that must be *visible* rather than merely logged — the
tracking inline-fallback counter above all. A gap in analytics is
indistinguishable from a drop in business, so the thing that causes the gap has
to show up somewhere you can graph.

Counters live under ``metric:<name>`` and expire after ``DEFAULT_TIMEOUT`` so a
quiet counter does not pin memory forever. Every call is wrapped: a metric must
never be able to break a request.
"""

import logging
from typing import Dict, Iterable, Optional

from django.core.cache import cache

logger = logging.getLogger(__name__)

KEY_PREFIX = "metric"
DEFAULT_TIMEOUT = 60 * 60 * 24 * 7

TRACKING_INLINE_FALLBACK = "tracking.inline_fallback"
TRACKING_ENQUEUE_FAILED = "tracking.enqueue_failed"
TRACKING_WRITE_FAILED = "tracking.write_failed"

KNOWN_COUNTERS = (TRACKING_INLINE_FALLBACK, TRACKING_ENQUEUE_FAILED, TRACKING_WRITE_FAILED)


def _key(name: str, label: Optional[str] = None) -> str:
    return f"{KEY_PREFIX}:{name}:{label}" if label else f"{KEY_PREFIX}:{name}"


def _bump(key: str, delta: int) -> None:
    try:
        try:
            cache.incr(key, delta)
        except ValueError:
            # Key absent — seed it. A racing writer just means we lose one tick.
            cache.set(key, delta, timeout=DEFAULT_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - cache is best effort
        logger.debug("Metric increment failed for %s: %s", key, exc)


def increment(name: str, label: Optional[str] = None, delta: int = 1) -> None:
    """Bump a counter. A labelled bump also bumps the unlabelled one, because
    that is the one an alarm reads."""
    _bump(_key(name), delta)
    if label:
        _bump(_key(name, label), delta)


def get(name: str, label: Optional[str] = None) -> int:
    try:
        return int(cache.get(_key(name, label)) or 0)
    except Exception:  # noqa: BLE001
        return 0


def snapshot(names: Iterable[str] = KNOWN_COUNTERS) -> Dict[str, int]:
    return {name: get(name) for name in names}


def reset(name: str, label: Optional[str] = None) -> None:
    try:
        cache.delete(_key(name))
        if label:
            cache.delete(_key(name, label))
    except Exception:  # noqa: BLE001
        pass
