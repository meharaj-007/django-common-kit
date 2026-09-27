"""Pagination (PRD §5.2): the page-size whitelist and the envelope, for both paginators.

Each case goes through ``paginate_queryset`` and ``get_paginated_response``, the
two calls DRF makes. ``CursorPagination.paginate_queryset`` assigns
``self.page_size`` itself, which a read-only ``page_size`` turned into an
``AttributeError`` on every cursor page — and nothing here exercised it.
"""

from urllib.parse import unquote

from django.test import TestCase, override_settings
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from django_common_kit.api.pagination import (
    CustomCursorSetPagination,
    CustomPageNumberPagination,
)
from tests.testapp.models import Widget

PAGINATION = {"DJANGO_COMMON_KIT": {
    "PARAMETERS": {"AUTO_LOAD_ON_STARTUP": False},
    "PAGINATION": {"PAGE_SIZE": 10, "ALLOWED_PAGE_SIZES": [5, 10, 20]},
}}


class PaginationCases:
    """Shared by both paginators; mixed into a TestCase below."""

    paginator_class = None

    @classmethod
    def setUpTestData(cls):
        Widget.objects.bulk_create(Widget(name=f"w{i}") for i in range(25))

    def page(self, **query):
        paginator = self.paginator_class()
        request = Request(APIRequestFactory().get("/widgets/", query))
        rows = paginator.paginate_queryset(Widget.objects.order_by("pk"), request)
        return rows, paginator.get_paginated_response([str(row.pk) for row in rows]).data

    def assert_page_size(self, expected, **query):
        rows, body = self.page(**query)
        self.assertEqual(len(rows), expected)
        self.assertEqual(len(body["data"]), expected)
        self.assertEqual(body["meta"]["page_size"], expected)
        self.assertEqual(body["status"], "success")

    def test_default_size(self):
        self.assert_page_size(10)

    def test_allowed_size_is_honoured(self):
        self.assert_page_size(20, page_size="20")

    def test_size_outside_the_whitelist_falls_back_to_the_default(self):
        self.assert_page_size(10, page_size="7")

    def test_non_numeric_size_falls_back_to_the_default(self):
        self.assert_page_size(10, page_size="all")

    def test_default_is_read_at_call_time(self):
        with override_settings(DJANGO_COMMON_KIT={
            **PAGINATION["DJANGO_COMMON_KIT"],
            "PAGINATION": {"PAGE_SIZE": 5, "ALLOWED_PAGE_SIZES": [5, 10, 20]},
        }):
            self.assert_page_size(5)
        self.assert_page_size(10)


@override_settings(**PAGINATION)
class CursorPaginationTests(PaginationCases, TestCase):
    paginator_class = CustomCursorSetPagination

    def test_next_page_continues_after_the_first(self):
        first, body = self.page()
        cursor = body["meta"]["next_page_id"]
        self.assertTrue(cursor)
        # Cut from the next link, so still URL-encoded; the factory encodes again.
        second, _ = self.page(page=unquote(cursor))
        self.assertEqual(len(second), 10)
        self.assertFalse({row.pk for row in first} & {row.pk for row in second})

    def test_a_size_used_on_one_instance_does_not_stick_to_the_next(self):
        self.page(page_size="20")
        self.assertEqual(self.paginator_class().page_size, 10)


@override_settings(**PAGINATION)
class PageNumberPaginationTests(PaginationCases, TestCase):
    paginator_class = CustomPageNumberPagination

    def test_paging_keys_are_in_meta(self):
        _, body = self.page(page="2")
        self.assertEqual(body["meta"]["current_page"], 2)
        self.assertEqual(body["meta"]["total_records"], 25)
        self.assertEqual(body["meta"]["total_pages"], 3)
