"""Middleware the package's mechanisms depend on (PRD §6.2).

``CurrentRequestMiddleware`` is the one piece a project must install for the
history trail to record an actor. Setting the thread-local request and stamping
a correlation id are one middleware because they are one job: make the request
in flight visible to code below the view.

Install it **first** in ``MIDDLEWARE``. Anything above it in the list runs
before the request is visible and after it is cleared.
"""

from django_common_kit.request_context import (
    CORRELATION_HEADER,
    new_correlation_id,
    set_current_request,
)

_INCOMING_HEADER = "HTTP_" + CORRELATION_HEADER.upper().replace("-", "_")


class CurrentRequestMiddleware:
    """Expose the request to the history trail, and give it a correlation id.

    An id an earlier middleware already put on ``request.correlation_id`` is
    kept — a project with its own trace-id middleware (and log lines already
    stamped with that id) sets it there first, and the id in a response body
    then matches its logs. Otherwise an incoming ``X-Request-Id`` is honoured
    so a gateway's id threads through, and failing both one is minted. It is
    echoed on the response, so a client can quote it, and it is what
    ``ApiResponse`` returns under ``request_id`` and ``ERROR_TRACE_ID_KEY``.

    The request is cleared in ``finally``. Django's own ``log_response`` runs in
    ``BaseHandler`` outside the middleware chain, so the *correlation id* is
    left on the request object rather than unset — an id cleared on the way out
    leaves exactly the ``Forbidden: /api/...`` lines we most need to correlate
    with nothing.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.correlation_id = (
            str(getattr(request, "correlation_id", "") or "")[:64].strip()
            or request.META.get(_INCOMING_HEADER, "")[:64].strip()
            or new_correlation_id()
        )
        set_current_request(request)
        try:
            response = self.get_response(request)
        finally:
            set_current_request(None)
        response[CORRELATION_HEADER] = request.correlation_id
        return response

