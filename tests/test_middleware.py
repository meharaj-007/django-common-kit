"""``CurrentRequestMiddleware`` (PRD §6.2)."""

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from django_common_kit.middleware import CurrentRequestMiddleware
from django_common_kit.request_context import get_current_request


class CurrentRequestMiddlewareTests(SimpleTestCase):
    def test_request_is_visible_during_and_cleared_after(self):
        seen = {}

        def view(request):
            seen["request"] = get_current_request()
            return HttpResponse()

        request = RequestFactory().get("/")
        CurrentRequestMiddleware(view)(request)
        self.assertIs(seen["request"], request)
        self.assertIsNone(get_current_request())

    def test_cleared_even_when_the_view_raises(self):
        def view(request):
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            CurrentRequestMiddleware(view)(RequestFactory().get("/"))
        self.assertIsNone(get_current_request())

    def test_mints_a_correlation_id_and_echoes_it(self):
        request = RequestFactory().get("/")
        response = CurrentRequestMiddleware(lambda r: HttpResponse())(request)
        self.assertTrue(request.correlation_id)
        self.assertEqual(response["X-Request-Id"], request.correlation_id)

    def test_honours_an_incoming_id(self):
        request = RequestFactory().get("/", HTTP_X_REQUEST_ID="gateway-7")
        CurrentRequestMiddleware(lambda r: HttpResponse())(request)
        self.assertEqual(request.correlation_id, "gateway-7")

    def test_keeps_an_id_an_earlier_middleware_set(self):
        """A project's own trace-id middleware runs first; its id must win, or
        the id in a response body stops matching the project's log lines."""
        request = RequestFactory().get("/", HTTP_X_REQUEST_ID="gateway-7")
        request.correlation_id = "trace-from-project"
        response = CurrentRequestMiddleware(lambda r: HttpResponse())(request)
        self.assertEqual(request.correlation_id, "trace-from-project")
        self.assertEqual(response["X-Request-Id"], "trace-from-project")


class ClientIPTests(SimpleTestCase):
    """``get_client_ip`` (PRD §8): how far ``X-Forwarded-For`` is believed."""

    def ip(self, remote="10.0.0.5", forwarded=None):
        from django_common_kit.request_context import get_client_ip

        headers = {"REMOTE_ADDR": remote}
        if forwarded is not None:
            headers["HTTP_X_FORWARDED_FOR"] = forwarded
        return get_client_ip(RequestFactory().get("/", **headers))

    def test_by_default_the_header_is_ignored(self):
        self.assertEqual(self.ip(forwarded="203.0.113.9"), "10.0.0.5")

    @override_settings(DJANGO_COMMON_KIT={"TRACKING": {"TRUSTED_PROXY_COUNT": 1}})
    def test_the_hop_the_proxy_appended_is_taken_not_the_clients(self):
        self.assertEqual(self.ip(forwarded="6.6.6.6, 203.0.113.9"), "203.0.113.9")

    @override_settings(DJANGO_COMMON_KIT={"TRACKING": {"TRUSTED_PROXY_COUNT": 1}})
    def test_a_forwarded_entry_that_is_not_an_ip_falls_back_to_the_peer(self):
        for forwarded in ("unknown", "203.0.113.9:4431", "<script>", "1.2.3"):
            with self.subTest(forwarded=forwarded):
                self.assertEqual(self.ip(forwarded=forwarded), "10.0.0.5")

    @override_settings(DJANGO_COMMON_KIT={"TRACKING": {"TRUSTED_PROXY_COUNT": 1}})
    def test_ipv6_is_an_ip(self):
        self.assertEqual(self.ip(forwarded="2001:db8::1"), "2001:db8::1")

    @override_settings(DJANGO_COMMON_KIT={"TRACKING": {
        "TRUSTED_PROXY_COUNT": 1, "TRUSTED_PROXY_IPS": ["10.0.0.0/8", "192.0.2.7"],
    }})
    def test_with_proxy_ips_only_a_named_proxy_may_speak_for_the_client(self):
        self.assertEqual(self.ip(remote="10.1.2.3", forwarded="203.0.113.9"), "203.0.113.9")
        self.assertEqual(self.ip(remote="192.0.2.7", forwarded="203.0.113.9"), "203.0.113.9")
        # Straight to the app, bypassing the proxy: the header is the client's own.
        self.assertEqual(self.ip(remote="198.51.100.4", forwarded="203.0.113.9"), "198.51.100.4")

    @override_settings(DJANGO_COMMON_KIT={"TRACKING": {
        "TRUSTED_PROXY_COUNT": 1, "TRUSTED_PROXY_IPS": ["10.0.0.0/8"],
    }})
    def test_an_ipv4_proxy_seen_through_a_dual_stack_socket_is_still_the_proxy(self):
        self.assertEqual(self.ip(remote="::ffff:10.1.2.3", forwarded="203.0.113.9"), "203.0.113.9")

    @override_settings(DJANGO_COMMON_KIT={"TRACKING": {
        "TRUSTED_PROXY_COUNT": 1, "TRUSTED_PROXY_IPS": ["not-a-network", "10.0.0.0/8"],
    }})
    def test_a_bad_proxy_entry_is_skipped_not_trusted(self):
        with self.assertLogs("django_common_kit.request_context", "WARNING"):
            self.assertEqual(self.ip(remote="10.1.2.3", forwarded="203.0.113.9"), "203.0.113.9")

    @override_settings(DJANGO_COMMON_KIT={"TRACKING": {"TRUSTED_PROXY_IPS": ["10.0.0.0/8"]}})
    def test_proxy_ips_without_a_count_still_ignore_the_header(self):
        self.assertEqual(self.ip(remote="10.1.2.3", forwarded="203.0.113.9"), "10.1.2.3")
