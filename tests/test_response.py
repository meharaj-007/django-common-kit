"""The envelope's contract (PRD §5.1)."""

from django.test import RequestFactory, SimpleTestCase, override_settings
from rest_framework import status
from rest_framework.exceptions import APIException

from django_common_kit.api.response import ApiResponse
from django_common_kit.request_context import set_current_request
from django_common_kit.constants.error_messages import ErrorMessage


class EnvelopeTests(SimpleTestCase):
    def test_success_shape(self):
        body = ApiResponse.success(data={"id": 1}, message="Done").data
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["status_code"], 200)
        self.assertEqual(body["message"], "Done")
        self.assertEqual(body["data"], {"id": 1})
        self.assertNotIn("errors", body)
        self.assertNotIn("meta", body)

    def test_empty_list_is_still_data(self):
        """A list endpoint with no rows must not look like one returning nothing."""
        body = ApiResponse.success(data=[]).data
        self.assertIn("data", body)
        self.assertEqual(body["data"], [])

    def test_none_data_is_omitted(self):
        self.assertNotIn("data", ApiResponse.success().data)

    def test_error_shape(self):
        body = ApiResponse.bad_request(errors={"email": "Required."}).data
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["status_code"], 400)
        self.assertEqual(body["errors"], {"email": "Required."})

    def test_created_is_201(self):
        self.assertEqual(ApiResponse.created().status_code, 201)

    def test_too_many_requests_sets_retry_after(self):
        response = ApiResponse.too_many_requests(retry_after=30)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "30")

    def test_server_error_never_returns_errors(self):
        """A stack trace or an ORM message is not a client's business."""
        body = ApiResponse.server_error(errors={"sql": "relation does not exist"}).data
        self.assertNotIn("errors", body)
        self.assertEqual(body["message"], ErrorMessage.GENERIC_ERROR_MESSAGE)


class ValidationErrorTests(SimpleTestCase):
    def test_flattens_field_errors(self):
        body = ApiResponse.validation_error({"email": ["Enter a valid email address."]}).data
        self.assertEqual(body["errors"], {"email": "Enter a valid email address."})

    def test_message_defaults_to_the_first_specific_error(self):
        body = ApiResponse.validation_error({"start_time": ["This field is required."]}).data
        self.assertEqual(body["message"], "Start Time is required.")
        self.assertNotEqual(body["message"], "Validation failed")

    def test_nested_errors_flatten_with_dotted_keys(self):
        body = ApiResponse.validation_error(
            {"address": {"postcode": ["Enter a valid postcode."]}}
        ).data
        self.assertEqual(body["errors"], {"address.postcode": "Enter a valid postcode."})

    def test_errors_from_a_list_of_nested_items_are_keyed_by_index(self):
        """``many=True`` children fail as a list of dicts, one per item, with
        ``{}`` for every item that passed. They must not be stringified."""
        body = ApiResponse.validation_error(
            {"areas": [{}, {"city": ["This field is required."]}]}
        ).data
        self.assertEqual(body["errors"], {"areas.1.city": "This field is required."})
        self.assertEqual(body["message"], "Areas: City is required.")

    def test_a_list_serializer_at_the_root_is_keyed_by_index(self):
        body = ApiResponse.validation_error([{}, {"email": ["Enter a valid email address."]}]).data
        self.assertEqual(body["errors"], {"1.email": "Enter a valid email address."})
        self.assertEqual(body["message"], "Enter a valid email address.")

    def test_status_is_400_by_default(self):
        self.assertEqual(ApiResponse.validation_error({"a": ["b"]}).status_code, 400)

    @override_settings(DJANGO_COMMON_KIT={"RESPONSE": {"VALIDATION_ERROR_STATUS": 422}})
    def test_status_is_configurable_for_a_repo_that_shipped_422(self):
        self.assertEqual(ApiResponse.validation_error({"a": ["b"]}).status_code, 422)


class ErrorCodeTests(SimpleTestCase):
    """``code`` and the trace id are opt-in (RESPONSE["ERROR_CODES"],
    RESPONSE["ERROR_TRACE_ID_KEY"]); off, the envelope is exactly as before."""

    def test_off_by_default(self):
        body = ApiResponse.not_found(code="custom").data
        self.assertNotIn("code", body)
        self.assertNotIn("trace_id", body)

    @override_settings(DJANGO_COMMON_KIT={"RESPONSE": {"ERROR_CODES": True}})
    def test_defaults_per_status(self):
        cases = [
            (ApiResponse.bad_request(), "bad_request"),
            (ApiResponse.unauthorized(), "authentication_failed"),
            (ApiResponse.forbidden(), "permission_denied"),
            (ApiResponse.not_found(), "not_found"),
            (ApiResponse.too_many_requests(), "rate_limited"),
            (ApiResponse.server_error(), "server_error"),
            (ApiResponse.error(status_code=418), "bad_request"),
        ]
        for response, code in cases:
            with self.subTest(status=response.status_code):
                self.assertEqual(response.data["code"], code)

    @override_settings(DJANGO_COMMON_KIT={"RESPONSE": {"ERROR_CODES": True}})
    def test_a_caller_names_its_own(self):
        body = ApiResponse.bad_request(code="organization_required").data
        self.assertEqual(body["code"], "organization_required")
        self.assertEqual(ApiResponse.validation_error({"a": ["b"]}, code="x").data["code"], "x")

    @override_settings(DJANGO_COMMON_KIT={"RESPONSE": {"ERROR_CODES": True}})
    def test_never_on_a_success(self):
        self.assertNotIn("code", ApiResponse.success().data)

    @override_settings(DJANGO_COMMON_KIT={"RESPONSE": {
        "ERROR_CODES": True, "ERROR_CODE_BY_STATUS": {409: "conflict"},
    }})
    def test_project_map_extends_the_defaults(self):
        self.assertEqual(ApiResponse.error(status_code=409).data["code"], "conflict")
        self.assertEqual(ApiResponse.not_found().data["code"], "not_found")

    @override_settings(DJANGO_COMMON_KIT={"RESPONSE": {"ERROR_TRACE_ID_KEY": "trace_id"}})
    def test_trace_id_on_errors_only_and_regardless_of_debug(self):
        request = RequestFactory().get("/")
        request.correlation_id = "9f2c"
        set_current_request(request)
        self.addCleanup(set_current_request, None)
        self.assertEqual(ApiResponse.not_found().data["trace_id"], "9f2c")
        self.assertNotIn("trace_id", ApiResponse.success().data)

    def test_no_content_has_no_body(self):
        response = ApiResponse.no_content()
        self.assertEqual(response.status_code, 204)
        self.assertIsNone(response.data)


class ScrubberTests(SimpleTestCase):
    """An internal exception's text must not reach a client (PRD §5.1)."""

    def test_internal_exception_text_is_cut_from_the_message(self):
        try:
            raise KeyError("organisation_id")
        except KeyError:
            body = ApiResponse.error(
                message="Failed: 'organisation_id'",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            ).data
        self.assertNotIn("organisation_id", body["message"])
        self.assertNotIn("debug", body)

    def test_authored_exception_text_is_kept(self):
        """A ValueError from a service was written for the screen."""
        try:
            raise ValueError("Client must have an invoice start date.")
        except ValueError:
            body = ApiResponse.bad_request(
                message="Client must have an invoice start date."
            ).data
        self.assertEqual(body["message"], "Client must have an invoice start date.")

    def test_drf_exception_text_is_kept(self):
        try:
            raise APIException("Upstream provider refused the request.")
        except APIException:
            body = ApiResponse.bad_request(
                message="Upstream provider refused the request."
            ).data
        self.assertEqual(body["message"], "Upstream provider refused the request.")

    @override_settings(DEBUG=True)
    def test_debug_returns_the_exception_to_a_developer(self):
        try:
            raise AttributeError("'NoneType' object has no attribute 'pk'")
        except AttributeError:
            body = ApiResponse.server_error().data
        self.assertEqual(body["debug"]["exception"], "AttributeError")

    def test_a_successful_response_is_never_scrubbed(self):
        try:
            raise KeyError("swallowed")
        except KeyError:
            body = ApiResponse.success(data={"note": "swallowed"}).data
        self.assertEqual(body["data"], {"note": "swallowed"})
