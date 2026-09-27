"""Base view classes (PRD §5.2)."""

import logging

from django.test import SimpleTestCase
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory

from django_common_kit.api.views import CustomCreateAPIView


class _EchoSerializer(serializers.Serializer):
    name = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def create(self, validated_data):
        return validated_data


class _EchoCreateView(CustomCreateAPIView):
    permission_classes = [AllowAny]
    serializer_class = _EchoSerializer


class LoggedCreateTests(SimpleTestCase):
    """The create body is personal data; it must not reach an INFO log."""

    def setUp(self):
        self.view = _EchoCreateView.as_view()
        self.factory = APIRequestFactory()

    def _post(self):
        return self.view(self.factory.post(
            "/echo/", {"name": "Ada", "password": "hunter2"}, format="json",
        ))

    def test_body_is_not_logged_at_info(self):
        with self.assertLogs("django_common_kit.api.views", level="INFO") as logs:
            logging.getLogger("django_common_kit.api.views").info("sentinel")
            self.assertEqual(self._post().status_code, 201)
        self.assertEqual(logs.output, ["INFO:django_common_kit.api.views:sentinel"])

    def test_debug_line_is_redacted(self):
        with self.assertLogs("django_common_kit.api.views", level="DEBUG") as logs:
            self._post()
        line = "\n".join(logs.output)
        self.assertIn("Ada", line)
        self.assertNotIn("hunter2", line)
        self.assertIn("REDACTED", line)
