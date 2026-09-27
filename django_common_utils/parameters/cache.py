"""The read-through cache over ``parameters`` (PRD §7).

A miss falls through to the database and logs at DEBUG, not WARNING: a key that
does not exist at all is the common case for ``get_parameter("x", default)``.

Only ``is_system=True`` rows are cached and returned. A non-system parameter is
admin data, not runtime configuration, and ``get_parameter`` is not the way to
read it.
"""

import logging
from typing import Any, Dict

from django.core.cache import caches

from django_common_utils.conf import app_settings

logger = logging.getLogger(__name__)


def _cache():
    return caches[app_settings.get("PARAMETERS", "CACHE_ALIAS")]


def _timeout():
    ttl = app_settings.get("PARAMETERS", "CACHE_TTL_SECONDS")
    return None if not ttl else ttl


class ParameterCache:
    """Load, get, refresh and drop the parameter cache. All classmethods."""

    PARAMETER_CACHE_KEY = "system_parameters"
    PARAMETER_CACHE_VERSION_KEY = "system_parameters_version"

    @classmethod
    def load_parameters_to_cache(cls) -> Dict[str, Any]:
        """Every active system parameter, from the database into the cache."""
        from django_common_utils.models import VALUE_COLUMNS, ParameterModel

        rows = ParameterModel.objects.filter(is_active=True, is_system=True).values(
            "key", "parameter_type", *VALUE_COLUMNS.values()
        )
        loaded = {}
        for row in rows:
            column = VALUE_COLUMNS.get(row["parameter_type"])
            loaded[row["key"]] = row[column] if column else None

        cache = _cache()
        cache.set(cls.PARAMETER_CACHE_KEY, loaded, timeout=_timeout())
        cache.set(cls.PARAMETER_CACHE_VERSION_KEY, cls._get_next_version(), timeout=None)
        logger.info("Loaded %d parameters to cache", len(loaded))
        return loaded

    @classmethod
    def get_parameter(cls, key: str, default: Any = None) -> Any:
        """From the cache, else the database, else ``default``. Never raises."""
        try:
            cached = _cache().get(cls.PARAMETER_CACHE_KEY)
            if cached and key in cached:
                return cached[key]
            logger.debug("Parameter %r not in cache; reading the database", key)
            return cls._get_parameter_from_db(key, default)
        except Exception as exc:  # noqa: BLE001 - configuration must not 500 a request
            logger.error("Could not read parameter %r: %s", key, exc)
            return default

    @classmethod
    def get_all_parameters(cls) -> Dict[str, Any]:
        try:
            cached = _cache().get(cls.PARAMETER_CACHE_KEY)
            if cached:
                return cached
            return cls._load_parameters_from_db()
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not read parameters: %s", exc)
            return {}

    # -- typed accessors ----------------------------------------------------
    # Casting once here rather than ad hoc at every call site, with the
    # default returned for a missing key *and* for a value that will not cast —
    # a parameter typed "5 " by hand should not 500 the request that reads it.

    @classmethod
    def get_str(cls, key: str, default: str = "") -> str:
        value = cls.get_parameter(key, default)
        return default if value is None else str(value)

    @classmethod
    def get_int(cls, key: str, default: int = 0) -> int:
        value = cls.get_parameter(key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def get_float(cls, key: str, default: float = 0.0) -> float:
        value = cls.get_parameter(key, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def get_bool(cls, key: str, default: bool = False) -> bool:
        value = cls.get_parameter(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        if value is None:
            return default
        return bool(value)

    @classmethod
    def get_json(cls, key: str, default: Any = None) -> Any:
        value = cls.get_parameter(key, default)
        return default if value is None else value

    # -- maintenance --------------------------------------------------------

    @classmethod
    def invalidate_cache(cls) -> None:
        """Drop the cache and bump the version; the next read reloads."""
        try:
            cache = _cache()
            cache.delete(cls.PARAMETER_CACHE_KEY)
            cache.set(cls.PARAMETER_CACHE_VERSION_KEY, cls._get_next_version(), timeout=None)
            logger.info("Parameter cache invalidated")
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not invalidate parameter cache: %s", exc)

    @classmethod
    def refresh_parameter(cls, key: str) -> None:
        """Re-read one key into the cache, or remove it if the row is gone."""
        from django_common_utils.models import ParameterModel

        try:
            cache = _cache()
            cached = cache.get(cls.PARAMETER_CACHE_KEY) or {}
            row = ParameterModel.objects.filter(key=key, is_active=True, is_system=True).first()
            if row is not None:
                cached[key] = row.value
            else:
                cached.pop(key, None)
            cache.set(cls.PARAMETER_CACHE_KEY, cached, timeout=_timeout())
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not refresh parameter %r: %s", key, exc)

    @classmethod
    def is_cache_valid(cls) -> bool:
        try:
            cached = _cache().get(cls.PARAMETER_CACHE_KEY)
            return bool(cached)
        except Exception:  # noqa: BLE001
            return False

    @classmethod
    def get_cache_info(cls) -> Dict[str, Any]:
        try:
            cache = _cache()
            cached = cache.get(cls.PARAMETER_CACHE_KEY)
            return {
                "cache_valid": cached is not None,
                "parameter_count": len(cached) if cached else 0,
                "version": cache.get(cls.PARAMETER_CACHE_VERSION_KEY),
                "parameters": sorted(cached.keys()) if cached else [],
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not describe parameter cache: %s", exc)
            return {"cache_valid": False, "parameter_count": 0, "version": None, "parameters": []}

    # -- database -----------------------------------------------------------

    @staticmethod
    def _get_parameter_from_db(key: str, default: Any = None) -> Any:
        from django_common_utils.models import ParameterModel

        row = ParameterModel.objects.filter(key=key, is_active=True, is_system=True).first()
        return row.value if row is not None else default

    @staticmethod
    def _load_parameters_from_db() -> Dict[str, Any]:
        from django_common_utils.models import ParameterModel

        return {
            row.key: row.value
            for row in ParameterModel.objects.filter(is_active=True, is_system=True)
        }

    @classmethod
    def _get_next_version(cls) -> int:
        try:
            return int(_cache().get(cls.PARAMETER_CACHE_VERSION_KEY, 0)) + 1
        except Exception:  # noqa: BLE001
            return 1


# Module-level convenience functions.

def get_parameter(key: str, default: Any = None) -> Any:
    return ParameterCache.get_parameter(key, default)


def get_all_parameters() -> Dict[str, Any]:
    return ParameterCache.get_all_parameters()


def invalidate_parameter_cache() -> None:
    ParameterCache.invalidate_cache()
