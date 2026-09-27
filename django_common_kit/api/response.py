"""The response envelope (PRD §5.1).

One shape, every endpoint::

    {"status": "success"|"error", "status_code": 200, "message": "...",
     "data": {...}, "errors": {...}, "meta": {...}}

``message``, ``data``, ``errors`` and ``meta`` are omitted when unset. ``data``
is omitted only when it is ``None``, so an empty list still renders as
``"data": []`` — a list endpoint with no rows must not look like an endpoint that
returns no data.

Two keys are opt-in, for a project whose clients already rely on them: with
``RESPONSE["ERROR_CODES"]`` every error body carries ``code`` — a stable label
to branch on, where ``message`` is display text — and with
``RESPONSE["ERROR_TRACE_ID_KEY"]`` set it carries the request's correlation id
under that key, so a user quoting it back leads to the request's log lines.
Both are off by default: adding a key to the envelope is a contract change for
every frontend that did not ask for it.

``ApiResponse`` is a namespace class of staticmethods. That is the one place this
package keeps a class purely as a namespace, and it is deliberate:
``ApiResponse.success(...)`` is the call every view makes, and a bare function
per status reads worse at the call site.

Three things in here exist because of an incident each:

- the in-flight exception scrubber, below — an ORM error's text once reached a
  client, naming a column;
- ``headers=`` passthrough — ``Retry-After`` on a 429 and ``WWW-Authenticate``
  on a 401 carry the response's meaning in a header, and rebuilding DRF's
  response as an envelope drops them otherwise;
- field-error normalisation in ``validation_error`` — "Validation failed" tells
  a user nothing.
"""

import logging
import os
import sys

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response

from django_common_kit.conf import app_settings
from django_common_kit.constants.error_messages import ErrorMessage

logger = logging.getLogger(__name__)

# Exceptions whose text was written for a person: a service's ``ValueError``
# ("Client must have invoice_start_date …"), a validation error, a DRF
# ``APIException``. Their message may reach the client as-is. Anything else in
# flight — a ``ProgrammingError`` naming a missing column, a ``KeyError``, an
# ``AttributeError`` — is an internal fault, and its text is stripped from the
# response and written to the log with the traceback instead.
_AUTHORED_EXCEPTIONS = (
    ValueError,
    PermissionError,
    PermissionDenied,
    ValidationError,
    APIException,
    ObjectDoesNotExist,
    Http404,
)

_local_packages = None


def reset_local_package_cache(**kwargs):
    """Clear the memo. Connected to ``setting_changed`` so a test that overrides
    ``BASE_DIR`` or the package list is not served the previous project's."""
    global _local_packages
    _local_packages = None


def _local_package_names():
    """This project's own top-level packages.

    An exception class defined in the project (``GoogleAuthError``,
    ``ContractorPerformanceReportError``) carries a message someone wrote for the
    screen, like ``ValueError`` does; one from Django, psycopg2 or the standard
    library does not.

    Discovered by listing ``BASE_DIR`` for directories holding an
    ``__init__.py``, unless the project names them outright — a src-layout
    project, or one whose apps live a directory down, gets nothing useful from
    the scan.
    """
    global _local_packages
    if _local_packages is not None:
        return _local_packages

    configured = app_settings.get("RESPONSE", "AUTHORED_EXCEPTION_PACKAGES")
    if configured:
        _local_packages = set(configured)
        return _local_packages

    base = str(getattr(settings, "BASE_DIR", "") or "")
    found = set()
    if base and os.path.isdir(base):
        for entry in os.listdir(base):
            if os.path.isfile(os.path.join(base, entry, "__init__.py")):
                found.add(entry)
    _local_packages = found
    return _local_packages


def _exception_is_authored(exc):
    if isinstance(exc, _AUTHORED_EXCEPTIONS):
        return True
    return (type(exc).__module__ or "").split(".")[0] in _local_package_names()


def _cut(value, needles):
    """``value`` with every needle removed, recursing through lists and dicts.

    A string left empty (or only punctuation) by the cut becomes ``None``, so the
    caller can fall back to a generic message rather than send ": ".
    """
    if isinstance(value, str):
        out = value
        for needle in needles:
            out = out.replace(needle, "")
        if out == value:
            # Nothing was cut, so nothing needs tidying. Tidying unconditionally
            # eats the full stop off every untouched message.
            return value
        out = out.strip().rstrip(" :-–—(").strip()
        # Trailing punctuation left dangling by the cut ("Failed: " -> "Failed:")
        # is removed, but a message that still ends in a sentence keeps its stop.
        out = out.rstrip(":-–—(").strip()
        return out or None
    if isinstance(value, dict):
        return {key: _cut(item, needles) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_cut(item, needles) for item in value]
    return value


def _scrub_in_flight_exception(message, errors, status_code):
    """Keep an internal exception's text out of an error response, and log the trace.

    Called from every error response. If the response is being built inside an
    ``except`` block (``sys.exc_info()`` is set) and the exception is not one
    whose message was authored for people, then: the exception's text is cut out
    of ``message`` (and ``errors``, when they are that same text), a generic
    message stands in when nothing is left, and the traceback is logged at ERROR
    — whether or not the view logged anything itself. With ``DEBUG`` on the
    exception is also returned under ``debug`` so a developer still sees it.

    Returns ``(message, errors, debug)``.
    """
    exc = sys.exc_info()[1]
    if exc is None or status.is_success(status_code) or _exception_is_authored(exc):
        return message, errors, None

    detail = str(exc).strip()
    exc_name = type(exc).__name__
    needles = [needle for needle in (detail, exc_name) if needle]
    message = _cut(message, needles) if message else message
    errors = _cut(errors, needles)
    if not message:
        message = ErrorMessage.GENERIC_ERROR_MESSAGE
    logger.error(
        "[ApiResponse] %s while answering HTTP %s (%s)",
        exc_name, status_code, message, exc_info=True,
    )
    debug = {"exception": exc_name, "detail": detail} if settings.DEBUG else None
    return message, errors, debug


#: The ``code`` an error answers with when the caller names none, by status.
#: ``RESPONSE["ERROR_CODE_BY_STATUS"]`` adds to or overrides these. Deliberately
#: few: a code is a promise a client branches on, and one per status is the
#: promise the package can keep for every project. Anything finer — "this
#: tenant header is missing" — is a code the project passes in.
DEFAULT_ERROR_CODES = {
    status.HTTP_400_BAD_REQUEST: "bad_request",
    status.HTTP_401_UNAUTHORIZED: "authentication_failed",
    status.HTTP_403_FORBIDDEN: "permission_denied",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_422_UNPROCESSABLE_ENTITY: "validation_error",
    status.HTTP_429_TOO_MANY_REQUESTS: "rate_limited",
}


def error_code_for_status(status_code):
    """The default ``code`` for an error status."""
    configured = app_settings.get("RESPONSE", "ERROR_CODE_BY_STATUS") or {}
    for key, code in configured.items():
        if int(key) == status_code:
            return code
    if status_code in DEFAULT_ERROR_CODES:
        return DEFAULT_ERROR_CODES[status_code]
    return "server_error" if status_code >= 500 else "bad_request"


class ApiResponse:
    """Standardised API responses. See the module docstring for the shape."""

    # -- construction -------------------------------------------------------

    @staticmethod
    def to_dict(data=None, message=None, status_code=status.HTTP_200_OK,
                errors=None, meta=None, code=None):
        """The response body as a plain dict, for the Django views that need
        ``JsonResponse`` rather than a DRF ``Response``.

        ``code`` labels an error for a client to branch on. It is written only
        when ``RESPONSE["ERROR_CODES"]`` is on, and defaults to the status's
        code (``error_code_for_status``) when the caller gives none.
        """
        succeeded = status.is_success(status_code)
        debug = None
        if not succeeded:
            message, errors, debug = _scrub_in_flight_exception(message, errors, status_code)

        body = {"status": "success" if succeeded else "error"}
        if app_settings.get("RESPONSE", "INCLUDE_STATUS_CODE_IN_BODY"):
            body["status_code"] = status_code
        if not succeeded and app_settings.get("RESPONSE", "ERROR_CODES"):
            body["code"] = code or error_code_for_status(status_code)

        if message:
            body["message"] = message
        if debug is not None:
            body["debug"] = debug
        # `is not None`, not truthiness: an empty list is data.
        if data is not None:
            body["data"] = data
        if errors is not None:
            body["errors"] = errors
        if meta is not None:
            body["meta"] = meta

        trace_key = None if succeeded else app_settings.get("RESPONSE", "ERROR_TRACE_ID_KEY")
        if trace_key or app_settings.include_request_id():
            from django_common_kit.request_context import get_correlation_id

            request_id = get_correlation_id()
            if request_id and trace_key:
                body[trace_key] = request_id
            if request_id and app_settings.include_request_id():
                body["request_id"] = request_id

        return body

    @staticmethod
    def format_response(data=None, message=None, status_code=status.HTTP_200_OK,
                        errors=None, meta=None, headers=None, code=None):
        """
        Args:
            data: the response payload, any serialisable object
            message: a sentence describing the outcome
            status_code: HTTP status code
            errors: field errors or an error detail
            meta: pagination and other envelope metadata
            headers: extra response headers, for the few statuses whose meaning
                lives in a header rather than the body — ``Retry-After`` on a
                429, ``WWW-Authenticate`` on a 401 — which would otherwise be
                lost when the exception handler rebuilds DRF's response.
            code: an error's machine label (see ``to_dict``)
        """
        return Response(
            ApiResponse.to_dict(
                data=data, message=message, status_code=status_code,
                errors=errors, meta=meta, code=code,
            ),
            status=status_code,
            headers=headers or None,
        )

    # -- success ------------------------------------------------------------

    @staticmethod
    def success(data=None, message="Operation successful",
                status_code=status.HTTP_200_OK, meta=None, headers=None):
        return ApiResponse.format_response(
            data=data, message=message, status_code=status_code,
            meta=meta, headers=headers,
        )

    @staticmethod
    def created(data=None, message="Resource created successfully", meta=None, headers=None):
        return ApiResponse.format_response(
            data=data, message=message, status_code=status.HTTP_201_CREATED,
            meta=meta, headers=headers,
        )

    @staticmethod
    def no_content(headers=None):
        """204, with no body at all — the one status whose envelope would be a
        protocol error rather than a courtesy."""
        return Response(status=status.HTTP_204_NO_CONTENT, headers=headers or None)

    # -- errors -------------------------------------------------------------

    @staticmethod
    def error(message="An error occurred", status_code=status.HTTP_400_BAD_REQUEST,
              errors=None, meta=None, headers=None, code=None):
        # An internal exception in flight is scrubbed and logged with its
        # traceback in ``to_dict``. A server error with none in flight, or with
        # an authored one (a service's ValueError answered as a 500), is logged
        # here so that no 5xx ever goes unrecorded.
        exc = sys.exc_info()[1]
        if status_code >= 500 and (exc is None or _exception_is_authored(exc)):
            logger.error("Server error: %s", message, exc_info=exc is not None)

        return ApiResponse.format_response(
            message=message, status_code=status_code, errors=errors,
            meta=meta, headers=headers, code=code,
        )

    @staticmethod
    def bad_request(message=ErrorMessage.INVALID_REQUEST, errors=None, code=None):
        return ApiResponse.error(
            message=message, status_code=status.HTTP_400_BAD_REQUEST, errors=errors, code=code,
        )

    @staticmethod
    def unauthorized(message=ErrorMessage.AUTHENTICATION_FAILED, errors=None, headers=None, code=None):
        return ApiResponse.error(
            message=message, status_code=status.HTTP_401_UNAUTHORIZED,
            errors=errors, headers=headers, code=code,
        )

    @staticmethod
    def forbidden(message=ErrorMessage.PERMISSION_DENIED, errors=None, code=None):
        return ApiResponse.error(
            message=message, status_code=status.HTTP_403_FORBIDDEN, errors=errors, code=code,
        )

    @staticmethod
    def not_found(message=ErrorMessage.RESOURCE_NOT_FOUND, errors=None, code=None):
        return ApiResponse.error(
            message=message, status_code=status.HTTP_404_NOT_FOUND, errors=errors, code=code,
        )

    @staticmethod
    def too_many_requests(message=ErrorMessage.RATE_LIMIT_EXCEEDED, errors=None,
                          retry_after=None, code=None):
        """429. ``retry_after`` is seconds, and becomes the ``Retry-After``
        header — a 429 without it tells a client to back off but not for how
        long, so it backs off by guessing."""
        headers = {"Retry-After": str(int(retry_after))} if retry_after else None
        return ApiResponse.error(
            message=message, status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            errors=errors, headers=headers, code=code,
        )

    @staticmethod
    def validation_error(errors, message=None, code=None):
        """A validation failure, with per-field errors the frontend can attach.

        ``errors`` may be raw ``serializer.errors`` or a ``ValidationError``
        detail; it is normalised to ``{field: message}``. When ``message`` is not
        given it defaults to the first specific field error, so the top-level
        message is readable and never "Validation failed".

        **The status is 400, not 422.** DRF raises 400 for the same failure
        whenever a view calls ``is_valid(raise_exception=True)``, and a client
        should not have to handle two statuses depending on which code path the
        server happened to take. A project whose clients expect 422 sets
        ``DJANGO_COMMON_KIT["RESPONSE"]["VALIDATION_ERROR_STATUS"] = 422``.
        """
        from django_common_kit.api.errors import (
            extract_first_error_message,
            normalize_field_errors,
        )

        field_errors = normalize_field_errors(errors)
        if message is None:
            message = extract_first_error_message(errors)

        return ApiResponse.error(
            message=message,
            status_code=app_settings.get("RESPONSE", "VALIDATION_ERROR_STATUS"),
            errors=field_errors or None,
            code=code,
        )

    @staticmethod
    def server_error(message=ErrorMessage.GENERIC_ERROR_MESSAGE, errors=None, code=None):
        """500. ``errors`` is accepted and deliberately dropped: a stack trace or
        an ORM message is not a client's business, and the scrubber has already
        written the real one to the log."""
        return ApiResponse.error(
            message=message, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, code=code,
        )
