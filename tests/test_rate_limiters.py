"""View-level rate limiting (PRD §5.2)."""

from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.utils.decorators import method_decorator
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from django_common_utils.api.rate_limiters import (
    api_rate_limit,
    django_http_rate_limit,
    enforce_cooldown,
    html_rate_limit,
    window_hit,
)
from django_common_utils.api.response import ApiResponse


class WindowHitTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_allows_up_to_the_limit_then_rejects(self):
        for _ in range(3):
            self.assertEqual(window_hit("k", 3, 60), (True, 0))
        allowed, retry = window_hit("k", 3, 60)
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry, 1)


class EnforceCooldownTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def _request(self, ip="1.1.1.1"):
        return self.factory.post("/quote/", REMOTE_ADDR=ip)

    def test_ip_limit(self):
        for _ in range(2):
            self.assertIsNone(enforce_cooldown(self._request(), key_prefix="q", limit=2, window=60))
        rejection = enforce_cooldown(self._request(), key_prefix="q", limit=2, window=60)
        self.assertEqual(rejection.scope, "ip")

    def test_identity_rejection_refunds_the_ip_slot(self):
        """One abusive account must not throttle the office NAT behind it."""
        # Exhaust the identity from one address…
        for _ in range(2):
            enforce_cooldown(self._request("1.1.1.1"), key_prefix="q", limit=2, window=60, identity="a@b.com")
        # …then trip it from a second address whose own IP quota is untouched.
        rejection = enforce_cooldown(self._request("9.9.9.9"), key_prefix="q", limit=2, window=60, identity="a@b.com")
        self.assertEqual(rejection.scope, "identity")
        # 9.9.9.9 got its slot back: two more identities pass before the IP limit.
        self.assertIsNone(enforce_cooldown(self._request("9.9.9.9"), key_prefix="q", limit=2, window=60, identity="c@d.com"))
        self.assertIsNone(enforce_cooldown(self._request("9.9.9.9"), key_prefix="q", limit=2, window=60, identity="e@f.com"))

    @override_settings(DJANGO_COMMON_UTILS={"RATE_LIMIT": {"ENABLED": False}})
    def test_master_switch(self):
        for _ in range(10):
            self.assertIsNone(enforce_cooldown(self._request(), key_prefix="q", limit=1, window=60))

    def test_per_guard_switch(self):
        """A project's own kill switch for one feature, passed as a value."""
        for _ in range(10):
            self.assertIsNone(enforce_cooldown(self._request(), key_prefix="q", limit=1, window=60, enabled=False))
        # The switch skipped the counter too: the guard is fresh when it comes back on.
        self.assertIsNone(enforce_cooldown(self._request(), key_prefix="q", limit=1, window=60))


_DUMMY_DEFAULT = {
    "default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"},
    "limits": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "limits"},
}


@override_settings(CACHES=_DUMMY_DEFAULT)
class CacheAliasTests(TestCase):
    """``default`` is often DummyCache under DEBUG, where no limit holds."""

    def setUp(self):
        from django.core.cache import caches

        caches["limits"].clear()

    def test_counts_in_default_unless_told_otherwise(self):
        # DummyCache: add() and incr() never stick, so the counter fails open.
        for _ in range(5):
            self.assertEqual(window_hit("k", 1, 60), (True, 0))

    @override_settings(DJANGO_COMMON_UTILS={"RATE_LIMIT": {"CACHE_ALIAS": "limits"}})
    def test_counts_in_the_named_cache(self):
        self.assertEqual(window_hit("k", 1, 60), (True, 0))
        self.assertFalse(window_hit("k", 1, 60)[0])

    @override_settings(DJANGO_COMMON_UTILS={"RATE_LIMIT": {"CACHE_ALIAS": "limits"}})
    def test_drf_throttles_share_the_alias(self):
        from django.core.cache import caches

        from django_common_utils.api.throttling import throttle_cache

        self.assertIs(throttle_cache(), caches["limits"])

    @override_settings(THROTTLE_CACHE_ALIAS="limits")
    def test_the_top_level_name_is_not_honoured(self):
        """One settings dict (§4): the old top-level name is gone."""
        from django.core.cache import caches

        from django_common_utils.api.throttling import throttle_cache

        self.assertIs(throttle_cache(), caches["default"])


class DecoratorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def test_api_rate_limit_on_a_drf_view_answers_in_the_envelope(self):
        @method_decorator(api_rate_limit(requests=1, window=60), name="post")
        class View(APIView):
            permission_classes = [AllowAny]

            def post(self, request):
                return ApiResponse.success()

        view = View.as_view()
        self.assertEqual(view(self.factory.post("/x/", REMOTE_ADDR="2.2.2.2")).status_code, 200)
        response = view(self.factory.post("/x/", REMOTE_ADDR="2.2.2.2"))
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)
        self.assertEqual(response.data["status"], "error")

    def test_api_email_limit_reads_a_json_body(self):
        """request.POST is empty for JSON; a limit reading only POST never fires."""
        @method_decorator(api_rate_limit(requests=1, window=60), name="post")
        class View(APIView):
            permission_classes = [AllowAny]

            def post(self, request):
                return ApiResponse.success()

        view = View.as_view()
        body = {"email": "a@b.com"}
        view(self.factory.post("/x/", body, content_type="application/json", REMOTE_ADDR="3.3.3.3"))
        response = view(self.factory.post("/x/", body, content_type="application/json", REMOTE_ADDR="4.4.4.4"))
        self.assertEqual(response.status_code, 429)
        self.assertIn("account", response.data["message"])

    def test_html_rate_limit_redirects_to_referer(self):
        @html_rate_limit(requests=1, window=60, use_email=False)
        def view(request):
            return HttpResponse("ok")

        view(self.factory.get("/form/", REMOTE_ADDR="5.5.5.5"))
        response = view(self.factory.get("/form/", REMOTE_ADDR="5.5.5.5", HTTP_REFERER="/back/"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/back/")

    def test_html_rate_limit_without_referer_or_fallback_is_a_429_page(self):
        @html_rate_limit(requests=1, window=60, use_email=False)
        def view(request):
            return HttpResponse("ok")

        view(self.factory.get("/form/", REMOTE_ADDR="6.6.6.6"))
        self.assertEqual(view(self.factory.get("/form/", REMOTE_ADDR="6.6.6.6")).status_code, 429)

    def test_django_http_rate_limit(self):
        @django_http_rate_limit(requests=1, window=60)
        def view(request):
            return HttpResponse("ok")

        view(self.factory.get("/p/", REMOTE_ADDR="7.7.7.7"))
        response = view(self.factory.get("/p/", REMOTE_ADDR="7.7.7.7"))
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Content-Type"], "text/html")
