"""Turning a validation failure into something a person can read (PRD §5.3).

DRF hands you ``serializer.errors``: nested dicts, lists, integer keys from
``ListField`` children, ``__all__`` for model-level constraints. This flattener
has met all of those shapes.

Two functions, two jobs, and they are not the same job:

- ``normalize_field_errors`` produces the ``errors`` payload: ``{field: message}``
  so a frontend can attach each message to its input.
- ``extract_first_error_message`` produces the top-level ``message``: one
  sentence naming the field, because "Validation failed" tells a user nothing.
"""

import logging
from typing import Any, Dict

from django.http import Http404
from rest_framework.exceptions import APIException
from rest_framework.settings import api_settings

logger = logging.getLogger(__name__)

_FALLBACK = "Validation failed"


def reraise_handled(exc: Exception) -> None:
    """Re-raise exceptions the exception handler already renders correctly.

    A view's broad ``except Exception`` block otherwise flattens a 404 or a
    throttle into a generic 400. Covers ``Http404`` (from ``get_object()``), DRF
    ``NotFound`` — raised by the paginator for an out-of-range page, and an
    ``APIException``, *not* an ``Http404`` — and every other DRF exception type.

    Call it as the first statement of the ``except Exception`` block::

        except Exception as exc:
            reraise_handled(exc)
            logger.error(...)
            return ApiResponse.bad_request(...)
    """
    if isinstance(exc, (Http404, APIException)):
        raise exc


def _humanise(field_name: str) -> str:
    return field_name.replace("_", " ").title()


def _is_authored_prose(message: str) -> bool:
    """True for a message written as sentences rather than a one-line field check.

    A validator that raises "Quotes cannot be accepted after the expiry date.
    Ask the customer to request a new one."
    should not come back as "Expires At: Quotes cannot be accepted…" — the
    author already wrote a whole message and naming the field on the front of it
    makes it read like a bug.
    """
    body = message.strip()
    return ". " in body or "\n" in body


def extract_first_error_message(error_detail: Any) -> str:
    """The first error, as one sentence naming its field.

    Used as the envelope's top-level ``message``. Recurses into nested details
    and keeps the field name unless the message already carries it, so a caller
    never gets "Start Time: Start time is required."
    """
    if not error_detail:
        return _FALLBACK

    if isinstance(error_detail, str):
        return error_detail

    if isinstance(error_detail, (list, tuple)):
        entry = _first_failure(error_detail)
        if entry is None:
            return _FALLBACK
        if isinstance(entry, (dict, list, tuple)):
            return extract_first_error_message(entry)
        return str(entry)

    if isinstance(error_detail, dict):
        if not error_detail:
            return _FALLBACK

        first_field = next(iter(error_detail))
        first_field_errors = error_detail[first_field]
        # ListField / ManyRelated child errors are keyed by *index* (an int),
        # e.g. {"to": {0: ["Enter a valid email address."]}} — coerce so the
        # string handling below cannot crash the handler into a 500.
        field_name = str(first_field)

        # __all__ is Django's non-field key (unique_together, clean()). There is
        # no field to name, so the message stands alone.
        if field_name == "__all__":
            if isinstance(first_field_errors, (list, tuple)) and first_field_errors:
                return str(first_field_errors[0])
            if isinstance(first_field_errors, str):
                return first_field_errors

        if isinstance(first_field_errors, dict):
            nested = extract_first_error_message(first_field_errors)
            if field_name.lower() not in nested.lower():
                return f"{_humanise(field_name)}: {nested}"
            return nested

        if isinstance(first_field_errors, (list, tuple)) and _is_structured(first_field_errors):
            nested = extract_first_error_message(first_field_errors)
            if field_name.lower() not in nested.lower():
                return f"{_humanise(field_name)}: {nested}"
            return nested

        if isinstance(first_field_errors, (list, tuple)):
            if not first_field_errors:
                return f"Field '{field_name}' has validation errors"
            message = str(first_field_errors[0])
            if "is required" in message:
                return f"{_humanise(field_name)} is required."
            if field_name.replace("_", " ").lower() in message.lower():
                return message
            if _is_authored_prose(message):
                return message
            return f"{_humanise(field_name)}: {message}"

        message = str(first_field_errors)
        if "is required" in message:
            return f"{_humanise(field_name)} is required."
        return message

    return str(error_detail)


def normalize_field_errors(error_detail: Any) -> Dict[str, str]:
    """Flatten a validation detail into ``{field: message}``.

    - a list of messages collapses to its first message
    - nested dicts flatten with dotted keys (``"address.postcode"``)
    - a list of nested details — what a ``many=True`` child or a root
      ``ListSerializer`` raises, one entry per item and ``{}`` for each item
      that passed — flattens with the item's index (``"areas.1.city"``), the
      same path a frontend walks to find the input
    - a bare string or list is keyed under DRF's ``NON_FIELD_ERRORS_KEY``

    Read through ``api_settings`` rather than captured at import: a project that
    sets ``NON_FIELD_ERRORS_KEY`` in ``REST_FRAMEWORK`` gets its key, and a test
    that overrides it is not served a stale one.
    """
    result: Dict[str, str] = {}
    _flatten(error_detail, "", result)
    return result


def _is_structured(value) -> bool:
    """True for a list that holds per-item details rather than messages."""
    return any(isinstance(entry, (dict, list, tuple)) for entry in value)


def _first_failure(entries):
    """The first entry that carries an error; ``{}`` marks an item that passed."""
    for entry in entries:
        if entry or entry == 0:
            return entry
    return None


def _flatten(value: Any, path: str, result: Dict[str, str]) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            _flatten(inner, f"{path}.{key}" if path else str(key), result)
        return

    key = path or api_settings.NON_FIELD_ERRORS_KEY

    if isinstance(value, (list, tuple)):
        if _is_structured(value):
            for index, entry in enumerate(value):
                if isinstance(entry, (dict, list, tuple)):
                    _flatten(entry, f"{path}.{index}" if path else str(index), result)
                elif entry:
                    result.setdefault(key, str(entry))
        elif value:
            result[key] = str(value[0])
        elif path:
            result[key] = "Invalid value."
        return

    if value or path:
        result[key] = str(value)
