"""``tenant_id`` on every package table (PRD §3.5)."""

import uuid

from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.http import JsonResponse
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from django_common_kit.history import get_model_history
from django_common_kit.models import ContactUsModel, ParameterModel, RequestLog, StatusTransitionModel
from django_common_kit.request_context import set_current_request
from django_common_kit.tenancy import resolve_tenant
from django_common_kit.tracking.middleware import RequestLogMiddleware
from tests.testapp.models import Widget

TENANT = uuid.UUID("11111111-1111-1111-1111-111111111111")


def tenant_from_header(request):
    """The test project's RESOLVER: the tenant arrives in a header."""
    return request.META.get("HTTP_X_TENANT")


def broken_resolver(request):
    raise RuntimeError("tenant service down")


# The test project's tenant attribute: a widget belongs to its parent's tenant,
# and the parent's id stands in for a tenant id.
BY_PARENT = {"TENANT": {"INSTANCE_ATTRIBUTE": "parent_id"}}
BY_HEADER = {"TENANT": {"RESOLVER": "tests.test_tenancy.tenant_from_header"}}


class ResolveTenantTests(TestCase):
    def setUp(self):
        self.addCleanup(set_current_request, None)

    def test_nothing_configured_means_no_tenant(self):
        self.assertIsNone(resolve_tenant(instance=Widget(name="x"), request=RequestFactory().get("/")))

    @override_settings(DJANGO_COMMON_KIT=BY_HEADER)
    def test_resolver_reads_the_request_in_flight(self):
        set_current_request(RequestFactory().get("/", HTTP_X_TENANT=str(TENANT)))
        self.assertEqual(resolve_tenant(), TENANT)

    @override_settings(DJANGO_COMMON_KIT={"TENANT": {"RESOLVER": "tests.test_tenancy.broken_resolver"}})
    def test_a_failing_resolver_answers_no_tenant(self):
        self.assertIsNone(resolve_tenant(request=RequestFactory().get("/")))

    @override_settings(DJANGO_COMMON_KIT=BY_HEADER)
    def test_a_value_that_is_not_a_uuid_is_no_tenant(self):
        self.assertIsNone(resolve_tenant(request=RequestFactory().get("/", HTTP_X_TENANT="acme")))


class TenantOnRowsTests(TestCase):
    def setUp(self):
        self.addCleanup(set_current_request, None)
        self.parent = Widget.objects.create(name="parent")

    @override_settings(DJANGO_COMMON_KIT=BY_PARENT)
    def test_history_takes_the_tenant_of_the_row_it_is_about(self):
        child = Widget.objects.create(name="child", parent=self.parent)
        self.assertEqual(get_model_history(child).first().tenant_id, self.parent.pk)

    @override_settings(DJANGO_COMMON_KIT=BY_PARENT)
    def test_a_status_transition_takes_the_tenant_of_its_row(self):
        child = Widget.objects.create(name="child", parent=self.parent)
        row = StatusTransitionModel.objects.create(
            content_type=ContentType.objects.get_for_model(Widget), object_id=str(child.pk),
            new_status="done", transition_source="system",
        )
        self.assertEqual(row.tenant_id, self.parent.pk)
        self.assertEqual(row.field_name, "status")

    @override_settings(DJANGO_COMMON_KIT=BY_HEADER)
    def test_a_row_about_a_request_takes_the_resolvers_tenant(self):
        set_current_request(RequestFactory().get("/", HTTP_X_TENANT=str(TENANT)))
        contact = ContactUsModel.objects.create(name="a", email="a@b.com", subject="s", message="m")
        self.assertEqual(contact.tenant_id, TENANT)

    @override_settings(DJANGO_COMMON_KIT=BY_HEADER)
    def test_an_explicit_tenant_is_kept(self):
        other = uuid.uuid4()
        set_current_request(RequestFactory().get("/", HTTP_X_TENANT=str(TENANT)))
        contact = ContactUsModel.objects.create(
            name="a", email="a@b.com", subject="s", message="m", tenant_id=other,
        )
        self.assertEqual(contact.tenant_id, other)

    @override_settings(DJANGO_COMMON_KIT=BY_HEADER)
    def test_a_row_about_another_row_never_takes_the_requests_tenant(self):
        """History of a record with no tenant belongs to no tenant — not to
        whichever one the person making the change was working in."""
        set_current_request(RequestFactory().get("/", HTTP_X_TENANT=str(TENANT)))
        widget = Widget.objects.create(name="w")
        self.assertIsNone(get_model_history(widget).first().tenant_id)
        row = StatusTransitionModel.objects.create(
            content_type=ContentType.objects.get_for_model(Widget), object_id=str(widget.pk),
            new_status="done", transition_source="system",
        )
        self.assertIsNone(row.tenant_id)

    @override_settings(DJANGO_COMMON_KIT=BY_HEADER)
    def test_global_tables_are_not_labelled(self):
        """A system parameter or an IP ban belongs to no tenant by default."""
        set_current_request(RequestFactory().get("/", HTTP_X_TENANT=str(TENANT)))
        parameter = ParameterModel.objects.create(key="k", parameter_type="text", value_text="v")
        self.assertIsNone(parameter.tenant_id)

    @override_settings(DJANGO_COMMON_KIT={**BY_HEADER, "TRACKING": {"GEO_LOOKUP_URL": ""}})
    def test_a_request_log_carries_the_tenant_through_its_payload(self):
        request = RequestFactory().get("/api/x/", HTTP_X_TENANT=str(TENANT), REMOTE_ADDR="1.1.1.1")
        request.user = None
        RequestLogMiddleware(lambda r: JsonResponse({"ok": True}))(request)
        self.assertEqual(RequestLog.objects.get().tenant_id, TENANT)


class UnconfiguredTenancyTests(TestCase):
    """A project with no tenants pays nothing for the column."""

    def test_saving_a_status_transition_does_not_load_its_row(self):
        widget = Widget.objects.create(name="w")
        content_type = ContentType.objects.get_for_model(Widget)
        with CaptureQueriesContext(connection) as queries:
            StatusTransitionModel.objects.create(
                content_type=content_type, object_id=str(widget.pk),
                new_status="done", transition_source="system",
            )
        widget_reads = [q["sql"] for q in queries if "testapp_widget" in q["sql"]]
        self.assertEqual(widget_reads, [])
