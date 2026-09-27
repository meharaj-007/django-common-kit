"""The tracking middleware and writers (PRD §8)."""

import json

from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from django_common_utils.models import BlockedIPModel, IPTrackingModel, RequestLog
from django_common_utils.tracking.middleware import (
    IPBlockerMiddleware,
    IPTrackingMiddleware,
    RequestLogMiddleware,
)
from django_common_utils.tracking.patterns import is_excluded_path, matching_block_pattern
from django_common_utils.tracking.purge import purge_table
from django_common_utils.tracking.redaction import MASK

NO_GEO = {"TRACKING": {"GEO_LOOKUP_URL": ""}}


def ok(request):
    return JsonResponse({"ok": True, "token": "server-secret"})


class PatternTests(TestCase):
    def test_excluded_paths_are_anchored(self):
        self.assertTrue(is_excluded_path("/health/"))
        self.assertFalse(is_excluded_path("/health-and-safety-when-moving/"))
        self.assertTrue(is_excluded_path("/static/app.js"))

    def test_block_patterns_match_on_segment_boundaries_only(self):
        self.assertTrue(matching_block_pattern("/.env"))
        self.assertTrue(matching_block_pattern("/app/.env.production"))
        self.assertTrue(matching_block_pattern("/s3/"))
        # The s3 incident: a random token containing "s3" is not a probe.
        self.assertIsNone(matching_block_pattern("/api/auth/verify/AbCs3DeFg/"))
        self.assertIsNone(matching_block_pattern("/areas-we-serve/docker/"))

    @override_settings(DJANGO_COMMON_UTILS={"TRACKING": {"IP_BLOCK_EXEMPT_PATHS": [r"^/api/auth/verify-email/[^/]*/?$"]}})
    def test_exempt_paths_are_never_inspected(self):
        self.assertIsNone(matching_block_pattern("/api/auth/verify-email/.env/"))


@override_settings(DJANGO_COMMON_UTILS=NO_GEO)
class RequestLogMiddlewareTests(TestCase):
    def _run(self, request, view=ok):
        middleware = RequestLogMiddleware(view)
        return middleware(request)

    def test_writes_a_redacted_row(self):
        request = RequestFactory().post(
            "/api/login/?next=/&token=t",
            data=json.dumps({"email": "a@b.com", "password": "hunter2"}),
            content_type="application/json", REMOTE_ADDR="9.9.9.9",
        )
        request.user = None
        self._run(request)
        row = RequestLog.objects.get()
        self.assertEqual(row.endpoint, "/api/login/")
        self.assertEqual(row.ip_address, "9.9.9.9")
        self.assertNotIn("hunter2", row.request_body)
        self.assertNotIn("server-secret", row.response)
        self.assertEqual(row.query_string, f"next=%2F&token={MASK}")

    def test_excluded_paths_write_nothing(self):
        request = RequestFactory().get("/health/")
        self._run(request)
        self.assertEqual(RequestLog.objects.count(), 0)

    def test_binary_responses_are_not_stored(self):
        def pdf(request):
            return HttpResponse(b"%PDF-1.4", content_type="application/pdf")

        request = RequestFactory().get("/report.pdf")
        request.user = None
        self._run(request, pdf)
        self.assertIsNone(RequestLog.objects.get().response)

    @override_settings(DJANGO_COMMON_UTILS={"TRACKING": {"REQUEST_LOG_ENABLED": False, "GEO_LOOKUP_URL": ""}})
    def test_can_be_disabled(self):
        request = RequestFactory().get("/x/")
        self._run(request)
        self.assertEqual(RequestLog.objects.count(), 0)


@override_settings(DJANGO_COMMON_UTILS=NO_GEO)
class IPTrackingMiddlewareTests(TestCase):
    def test_writes_a_visit_row_without_a_network_call(self):
        request = RequestFactory().get("/pricing/", REMOTE_ADDR="8.8.8.8", HTTP_USER_AGENT="ua")
        request.user = None
        IPTrackingMiddleware(ok)(request)
        row = IPTrackingModel.objects.get()
        self.assertEqual(row.ip_address, "8.8.8.8")
        self.assertEqual(row.endpoint, "/pricing/")
        self.assertEqual(row.status_code, 200)
        # No geo provider configured: columns blank, no exception.
        self.assertEqual(row.country, "")

    @override_settings(DJANGO_COMMON_UTILS={"TRACKING": {"GEO_LOOKUP_URL": "", "CAPTURE_ATTRIBUTION": True}})
    def test_attribution_columns_when_enabled(self):
        request = RequestFactory().get("/?utm_source=google&sig=x", REMOTE_ADDR="8.8.8.8", HTTP_REFERER="https://g/")
        request.user = None
        IPTrackingMiddleware(ok)(request)
        row = IPTrackingModel.objects.get()
        self.assertEqual(row.utm_source, "google")
        self.assertEqual(row.referer, "https://g/")
        self.assertEqual(row.query_string, f"utm_source=google&sig={MASK}")


class IPBlockerTests(TestCase):
    def setUp(self):
        self.middleware = IPBlockerMiddleware(ok)

    def _get(self, path, ip="5.5.5.5"):
        return self.middleware(RequestFactory().get(path, REMOTE_ADDR=ip))

    def test_three_probes_block_the_address(self):
        self.assertEqual(self._get("/.env").status_code, 403)
        self.assertEqual(self._get("/.git/config").status_code, 403)
        self.assertFalse(BlockedIPModel.objects.get(ip_address="5.5.5.5").is_active)
        self.assertEqual(self._get("/wp-admin/").status_code, 403)
        row = BlockedIPModel.objects.get(ip_address="5.5.5.5")
        self.assertTrue(row.is_active)
        self.assertIsNotNone(row.blocked_at)
        # Now every path is refused, including an innocent one.
        self.assertEqual(self._get("/pricing/").status_code, 403)

    def test_innocent_paths_pass(self):
        self.assertEqual(self._get("/pricing/").status_code, 200)
        self.assertEqual(BlockedIPModel.objects.count(), 0)

    def test_whitelist_is_never_blocked(self):
        for _ in range(4):
            response = self._get("/.env", ip="127.0.0.1")
        self.assertEqual(response.status_code, 200)

    def test_a_hand_applied_ban_is_enforced_after_the_ttl(self):
        BlockedIPModel.objects.create(ip_address="7.7.7.7", is_active=True)
        self.middleware._cache_expires_at = 0  # force a refresh
        self.assertEqual(self._get("/pricing/", ip="7.7.7.7").status_code, 403)

    def test_expired_bans_are_lifted_and_strikes_cleared(self):
        BlockedIPModel.objects.create(
            ip_address="7.7.7.7", is_active=True, attempts=3,
            blocked_at=timezone.now() - timezone.timedelta(days=30),
        )
        self.middleware._cache_expires_at = 0
        self.assertEqual(self._get("/pricing/", ip="7.7.7.7").status_code, 200)
        row = BlockedIPModel.objects.get(ip_address="7.7.7.7")
        self.assertFalse(row.is_active)
        self.assertEqual(row.attempts, 0)

    def test_stale_strikes_restart_the_counter(self):
        BlockedIPModel.objects.create(
            ip_address="5.5.5.5", is_active=False, attempts=2,
            last_attempt=timezone.now() - timezone.timedelta(days=3),
        )
        self._get("/.env")
        self.assertEqual(BlockedIPModel.objects.get(ip_address="5.5.5.5").attempts, 1)

    def test_no_client_ip_is_not_held_responsible(self):
        request = RequestFactory().get("/.env")
        request.META.pop("REMOTE_ADDR", None)
        self.assertEqual(self.middleware(request).status_code, 200)
        self.assertEqual(BlockedIPModel.objects.count(), 0)


class PurgeTests(TestCase):
    def test_purges_only_past_the_window(self):
        old = RequestLog.objects.create(method="GET")
        RequestLog.objects.filter(pk=old.pk).update(created_at=timezone.now() - timezone.timedelta(days=40))
        RequestLog.objects.create(method="GET")
        self.assertEqual(purge_table(RequestLog, 30), 1)
        self.assertEqual(RequestLog.objects.count(), 1)

    def test_zero_days_deletes_nothing(self):
        RequestLog.objects.create(method="GET")
        self.assertEqual(purge_table(RequestLog, 0), 0)
        self.assertEqual(RequestLog.objects.count(), 1)


class MetricsTests(TestCase):
    def test_labelled_bump_also_bumps_the_rollup(self):
        from django_common_utils.tracking import metrics

        cache.clear()
        metrics.increment(metrics.TRACKING_INLINE_FALLBACK, "ip_tracking")
        self.assertEqual(metrics.get(metrics.TRACKING_INLINE_FALLBACK), 1)
        self.assertEqual(metrics.get(metrics.TRACKING_INLINE_FALLBACK, "ip_tracking"), 1)
