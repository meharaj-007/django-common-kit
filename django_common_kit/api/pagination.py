"""Pagination that answers in the envelope (PRD §5.2).

A paginated list answers with the ``ApiResponse`` envelope, paging in ``meta``,
not with a flat ``{prev_page, next_page, data}`` of its own. One client cannot
reasonably be asked to unwrap two shapes depending on whether a view happened to
be paginated.

The page-size whitelist is a project fact — it is whatever the frontend's
selector offers — so it lives in settings.
"""

from collections import OrderedDict

from rest_framework.pagination import CursorPagination, PageNumberPagination
from rest_framework.response import Response

from django_common_kit.api.response import ApiResponse
from django_common_kit.conf import app_settings


class AllowedPageSizeMixin:
    """Restrict ``page_size`` to the configured whitelist.

    Any other value — out of the set, non-numeric, or missing — falls back to the
    default, so the response always reports a size the frontend offers. This is
    stricter than DRF's ``max_page_size`` clamp on purpose: clamping 10,000 to
    200 answers a request nobody made, and a selector that offers four sizes has
    no use for the other 199.
    """

    page_size_query_param = "page_size"

    @property
    def default_page_size(self):
        return app_settings.get("PAGINATION", "PAGE_SIZE")

    @property
    def page_size(self):
        """The size set for the request in flight, else the configured default.

        Writable because DRF writes it: ``CursorPagination.paginate_queryset``
        assigns ``self.page_size = self.get_page_size(request)``, and a
        read-only property made every cursor page raise ``AttributeError``.
        The value written lives on the instance, which DRF builds per request;
        the default is still read from settings on every call.
        """
        override = getattr(self, "_page_size", None)
        return self.default_page_size if override is None else override

    @page_size.setter
    def page_size(self, value):
        self._page_size = value

    @property
    def allowed_page_sizes(self):
        return tuple(app_settings.get("PAGINATION", "ALLOWED_PAGE_SIZES"))

    def get_page_size(self, request):
        requested = request.query_params.get(self.page_size_query_param)
        if requested and requested.isdigit() and int(requested) in self.allowed_page_sizes:
            return int(requested)
        # The default, not `page_size`: on an instance that has already served
        # a page, `page_size` is the size that page was asked for.
        return self.default_page_size


class CustomPageNumberPagination(AllowedPageSizeMixin, PageNumberPagination):
    page_query_param = "page"

    @property
    def max_page_size(self):
        return app_settings.get("PAGINATION", "MAX_PAGE_SIZE")

    def get_results(self, data):
        return Response(OrderedDict([("data", data)]))

    def get_paginated_response(self, data):
        return ApiResponse.success(
            data=data,
            message="Results retrieved successfully",
            meta={
                "prev_page": self.get_previous_link(),
                "next_page": self.get_next_link(),
                "current_page": self.page.number,
                "page_size": self.page.paginator.per_page,
                "total_pages": self.page.paginator.num_pages,
                "total_records": self.page.paginator.count,
            },
        )


class CustomCursorSetPagination(AllowedPageSizeMixin, CursorPagination):
    """For tables large enough that ``COUNT(*)`` and ``OFFSET`` hurt.

    No ``total_records`` or ``total_pages`` here — that is the trade a cursor
    makes, not an omission. A view that needs a count is on the wrong paginator.
    """

    ordering = "-pk"
    cursor_query_param = "page"

    def get_results(self, data):
        return Response(OrderedDict([("data", data)]))

    def get_paginated_response(self, data):
        next_link = self.get_next_link()
        prev_link = self.get_previous_link()
        return ApiResponse.success(
            data=data,
            message="Results retrieved successfully",
            meta={
                "prev_page": prev_link,
                "next_page": next_link,
                # The bare cursor, for clients that would rather not parse a URL.
                "next_page_id": next_link.split("?page=")[-1] if next_link else None,
                "prev_page_id": prev_link.split("?page=")[-1] if prev_link else None,
                "page_size": self.get_page_size(self.request),
            },
        )

