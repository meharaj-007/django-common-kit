"""The exception handler, driven through a real request (PRD §5.3)."""

from django.test import TestCase, override_settings

from django_common_utils.constants.error_messages import ErrorMessage


@override_settings(ROOT_URLCONF="tests.urls")
class ExceptionHandlerTests(TestCase):
    def test_validation_error_renders_readable_message_and_field_errors(self):
        response = self.client.post(
            "/validated/", {"email": "not-an-email"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["status"], "error")
        self.assertIn("email", body["errors"])
        self.assertIn("start_time", body["errors"])
        self.assertNotEqual(body["message"], "Validation failed")

    def test_raise_exception_and_the_helper_agree_on_the_status(self):
        """The reason validation_error is 400 and not 422 (§5.1)."""
        from django_common_utils.api.response import ApiResponse

        raised = self.client.post(
            "/validated/", {"email": "nope"}, content_type="application/json"
        ).status_code
        helper = ApiResponse.validation_error({"email": ["Invalid."]}).status_code
        self.assertEqual(raised, helper)

    def test_an_internal_exception_never_reaches_the_client(self):
        with self.assertLogs("django_common_utils", level="ERROR"):
            response = self.client.get("/boom/")
        self.assertEqual(response.status_code, 500)
        body = response.json()
        self.assertNotIn("organisation_id", body["message"])
        self.assertEqual(body["message"], ErrorMessage.GENERIC_ERROR_MESSAGE)
        self.assertNotIn("errors", body)

    def test_throttle_response_carries_retry_after_in_header_and_meta(self):
        self.assertEqual(self.client.get("/throttled/").status_code, 200)
        with self.assertLogs("django_common_utils", level="WARNING"):
            response = self.client.get("/throttled/")
        self.assertEqual(response.status_code, 429)
        # Retry-After only reaches a browser if CORS exposes it, so the wait is
        # mirrored into meta — clients read the header first.
        self.assertIn("Retry-After", response)
        self.assertIn("retry_after", response.json()["meta"])
