"""Geo-IP enrichment (PRD §8): the cache, the pause and the provider's rate limit.

``requests`` is an optional extra and the suite runs without it, so each test
installs a stand-in module with the same exception hierarchy.
"""

import sys
import types
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from django_common_kit.tracking import writers
from django_common_kit.tracking.writers import GEO_BACKOFF_KEY, GEO_MISS_CACHE_TIMEOUT, resolve_geo

IP = "1.1.1.1"
OTHER_IP = "8.8.8.8"
LOCATION = {"status": "success", "country": "Australia", "countryCode": "AU"}
GEO = {"TRACKING": {"GEO_LOOKUP_URL": "http://geo.example/json/{ip}"}}


class RequestException(Exception):
    pass


class ConnectionError(RequestException):  # noqa: A001 - mirrors requests' name
    pass


class Timeout(RequestException):
    pass


class ConnectTimeout(ConnectionError, Timeout):
    pass


def _response(status=200, body=None, headers=None):
    response = mock.Mock(status_code=status, headers=headers or {})
    response.json.return_value = LOCATION if body is None else body
    return response


@override_settings(DJANGO_COMMON_KIT=GEO)
class ResolveGeoTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        fake = types.ModuleType("requests")
        fake.exceptions = types.SimpleNamespace(
            RequestException=RequestException,
            ConnectionError=ConnectionError,
            Timeout=Timeout,
            ConnectTimeout=ConnectTimeout,
        )
        fake.get = self.get = mock.Mock(return_value=_response())
        patcher = mock.patch.dict(sys.modules, {"requests": fake})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_location_is_looked_up_once_and_cached(self):
        self.assertEqual(resolve_geo(IP)["country"], "Australia")
        self.assertEqual(resolve_geo(IP)["country"], "Australia")
        self.assertEqual(self.get.call_count, 1)

    def test_an_unreachable_provider_pauses_every_lookup(self):
        self.get.side_effect = ConnectTimeout("timed out")

        self.assertEqual(resolve_geo(IP), {})
        self.assertEqual(resolve_geo(OTHER_IP), {})

        self.assertEqual(self.get.call_count, 1)
        self.assertTrue(cache.get(GEO_BACKOFF_KEY))

    def test_an_ip_skipped_during_a_pause_is_looked_up_after_it(self):
        self.get.side_effect = ConnectTimeout("timed out")
        resolve_geo(IP)
        cache.delete(GEO_BACKOFF_KEY)  # the pause runs out
        self.get.side_effect = None

        self.assertEqual(resolve_geo(IP)["country"], "Australia")

    def test_the_pause_warns_once_not_once_per_ip(self):
        self.get.side_effect = ConnectTimeout("timed out")
        with self.assertLogs(writers.logger, "WARNING") as logs:
            for ip in (IP, OTHER_IP, "9.9.9.9"):
                resolve_geo(ip)
        self.assertEqual(len(logs.output), 1)

    def test_429_pauses_for_as_long_as_the_provider_says(self):
        self.get.return_value = _response(status=429, headers={"X-Ttl": "37"})

        with mock.patch.object(writers.cache, "add", wraps=writers.cache.add) as add:
            self.assertEqual(resolve_geo(IP), {})

        self.assertEqual(add.call_args.kwargs["timeout"], 37)
        self.assertIsNone(cache.get(f"{writers.GEO_CACHE_PREFIX}:{IP}"))

    def test_the_last_allowed_lookup_is_kept_and_then_lookups_pause(self):
        self.get.return_value = _response(headers={"X-Rl": "0", "X-Ttl": "20"})

        self.assertEqual(resolve_geo(IP)["country"], "Australia")
        self.assertEqual(resolve_geo(OTHER_IP), {})
        self.assertEqual(self.get.call_count, 1)

    def test_a_failed_answer_is_cached_briefly(self):
        self.get.return_value = _response(body={"status": "fail", "message": "reserved range"})

        with mock.patch.object(writers.cache, "set", wraps=writers.cache.set) as cache_set:
            self.assertEqual(resolve_geo(IP), {})

        self.assertEqual(cache_set.call_args.kwargs["timeout"], GEO_MISS_CACHE_TIMEOUT)

    def test_non_public_addresses_are_never_looked_up(self):
        for ip in ("127.0.0.1", "10.0.0.4", "172.20.3.9", "192.168.1.2", "169.254.0.1",
                   "100.64.0.1", "0.0.0.0", "::1", "fd00::1", "not-an-ip"):
            self.assertEqual(resolve_geo(ip), {}, ip)
        self.get.assert_not_called()

    @override_settings(DJANGO_COMMON_KIT={"TRACKING": {"GEO_LOOKUP_URL": ""}})
    def test_an_empty_url_makes_no_lookup(self):
        self.assertEqual(resolve_geo(IP), {})
        self.get.assert_not_called()
