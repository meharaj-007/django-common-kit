"""The DRF exception handler and the package's own exception types (PRD §5.3).

Every error leaving a view renders into the envelope, including the ones that
are easy to forget: ``Http404``, ``PermissionDenied``,
``ValidationError`` with its field errors preserved, ``Throttled`` with its wait,
and ``IntegrityError`` with the offending column named.

Install it::

    REST_FRAMEWORK = {
        "EXCEPTION_HANDLER": "django_common_kit.api.exceptions.custom_exception_handler",
    }
"""

import logging
import re
from typing import Any, Dict

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, Throttled
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.views import exception_handler as drf_exception_handler

from django_common_kit.api.errors import (  # noqa: F401  (re-exported)
    extract_first_error_message,
    normalize_field_errors,
    reraise_handled,
)
from django_common_kit.api.response import ApiResponse

logger = logging.getLogger(__name__)


# -- the package's own exception types --------------------------------------
# Deliberately plain ``Exception`` subclasses, not ``APIException``. They are
# raised by service-layer code that has no business importing DRF, and the
# handler below is what gives them a status code.

class BusinessLogicException(Exception):
    """A rule the domain enforces was broken. Rendered as 400."""


class ValidationException(Exception):
    """A validation failure raised outside a serializer. Rendered as 400."""


class ResourceNotFoundException(Exception):
    """Rendered as 404. Distinct from ``Http404`` so a service can raise it
    without importing ``django.http``."""


class PermissionDeniedException(Exception):
    """Rendered as 403."""


_LOCAL_EXCEPTION_STATUS = {
    BusinessLogicException: 400,
    ValidationException: 400,
    ResourceNotFoundException: 404,
    PermissionDeniedException: 403,
}

#: Headers DRF derives from the exception itself, which the envelope must carry
#: through: ``Retry-After`` says how long a 429'd client should back off, and
#: ``WWW-Authenticate`` is the 401 challenge. Every other header on DRF's
#: response is renderer-owned (Content-Type and friends) and is regenerated when
#: this handler builds its own Response, so only these two are copied.
EXCEPTION_HEADERS = ("Retry-After", "WWW-Authenticate")


def passthrough_exception_headers(response: Any) -> Dict[str, str]:
    """Pull the exception-derived headers off DRF's response so that rebuilding
    the body as an envelope does not drop them."""
    headers = getattr(response, "headers", None) or {}
    return {name: headers[name] for name in EXCEPTION_HEADERS if name in headers}


def log_throttled(exc: Throttled, context: Dict[str, Any]) -> None:
    """Record the request a throttle just rejected, so 429s are not invisible.

    ``Throttled`` carries only ``wait`` — not the scope, the rate, or the key it
    was counted against — so this pairs with ``LoggedThrottleFailureMixin`` in
    ``django_common_kit.api.throttling``, which logs that half from inside the
    throttle. Together they answer both halves of "we keep getting rate limited":
    which budget ran out, and which endpoint and caller drained it.

    Best-effort and never raises: a failure here must not turn a 429 into a 500.
    """
    try:
        request = context.get("request")
        view = context.get("view")
        user = getattr(request, "user", None)
        logger.warning(
            "[throttling] 429 %s %s view=%s user=%s retry_after=%ss",
            getattr(request, "method", "?"),
            getattr(request, "path", "?"),
            type(view).__name__ if view is not None else "?",
            getattr(user, "pk", None) if getattr(user, "is_authenticated", False) else "anonymous",
            int(exc.wait or 0),
        )
    except Exception as exc_logging:  # noqa: BLE001 - logging must never break the response
        logger.warning("[throttling] 429 (could not describe request): %s", exc_logging)


def handle_validation_error(exc, context) -> Any:
    """Render any flavour of validation error as one consistent response:
    a readable top-level ``message`` plus per-field ``{field: message}``."""
    logger.warning("Validation failed: %s", type(exc).__name__, exc_info=True)

    # DRFValidationError and serializers.ValidationError are the same class,
    # both exposing ``.detail``. Django's has ``message_dict`` only when the
    # error was raised against fields.
    if isinstance(exc, DjangoValidationError):
        detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
    else:
        detail = getattr(exc, "detail", None)

    return ApiResponse.validation_error(errors=detail)


def custom_exception_handler(exc: Exception, context: Dict[str, Any]):
    """Centralised exception handler for every API error."""
    # Validation errors are handled *before* DRF's handler. DRF renders them as
    # a bare detail dict, which loses the readable top-level message.
    if isinstance(exc, (DRFValidationError, DjangoValidationError, serializers.ValidationError)):
        return handle_validation_error(exc, context)

    for exc_type, status_code in _LOCAL_EXCEPTION_STATUS.items():
        if isinstance(exc, exc_type):
            logger.info("%s: %s", type(exc).__name__, exc)
            return ApiResponse.error(message=str(exc) or None, status_code=status_code)

    if isinstance(exc, Throttled):
        log_throttled(exc, context)

    response = drf_exception_handler(exc, context)

    if response is not None:
        if hasattr(exc, "detail"):
            detail = exc.detail
            # PermissionDenied with a dict detail (code + message): prefer the
            # message for display but return the whole thing, because a frontend
            # that branches on the code needs the code.
            if isinstance(exc, PermissionDenied) and isinstance(detail, dict) and "message" in detail:
                error_message = detail["message"]
                errors = detail
            else:
                error_message = extract_first_error_message(detail)
                errors = None
        else:
            error_message = str(exc)
            errors = None

        # ``Retry-After`` only reaches a browser client if CORS exposes it, so
        # the wait is mirrored into ``meta.retry_after`` — clients read the
        # header first and fall back to this.
        meta = None
        if isinstance(exc, Throttled) and exc.wait is not None:
            meta = {"retry_after": int(exc.wait)}

        return ApiResponse.error(
            message=error_message,
            status_code=response.status_code,
            errors=errors,
            meta=meta,
            headers=passthrough_exception_headers(response),
        )

    if isinstance(exc, IntegrityError):
        logger.error("Database integrity error", exc_info=True)
        # Postgres spells a unique violation as
        # ``Key (email)=(a@b.com) already exists``. Naming the column turns an
        # opaque 400 into something a form can highlight. The value itself is
        # deliberately not echoed back.
        match = re.search(r"Key \((.*?)\)=\((.*?)\)", str(exc))
        if match:
            field, _value = match.groups()
            return ApiResponse.bad_request(
                message=f"A record with this {field} already exists."
            )
        return ApiResponse.bad_request(message="A data integrity error occurred.")

    logger.critical("Unhandled server error: %s", type(exc).__name__, exc_info=True)
    return ApiResponse.server_error()


def format_serializer_errors(serializer_errors: Dict[str, Any]) -> str:
    """The first error message from ``serializer.errors``, for a view that
    validates by hand rather than with ``raise_exception=True``."""
    return extract_first_error_message(serializer_errors)
