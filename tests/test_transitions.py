"""track_status_transition (PRD §6.4)."""

import uuid

from django.contrib.auth import get_user_model
from django.db import models
from django.test import RequestFactory, TestCase, override_settings

from django_common_kit.request_context import set_current_request
from django_common_kit.transitions import get_status_transitions, track_status_transition
from tests.testapp.models import Widget

User = get_user_model()


class Stage(models.TextChoices):
    DRAFT = "draft", "Draft"
    LIVE = "live", "Live"


class TrackStatusTransitionTests(TestCase):
    def setUp(self):
        self.addCleanup(set_current_request, None)
        self.widget = Widget.objects.create(name="draft")

    def test_previous_status_is_read_from_the_instance(self):
        row = track_status_transition(self.widget, "live", "system", field_name="name")
        self.assertEqual(row.previous_status, "draft")
        self.assertEqual(row.new_status, "live")
        self.assertEqual(row.field_name, "name")
        self.assertEqual(row.content_object, self.widget)

    def test_field_name_defaults_to_status(self):
        row = track_status_transition(self.widget, "live", "system", previous_status="draft")
        self.assertEqual(row.field_name, "status")

    def test_choices_are_stored_by_value(self):
        row = track_status_transition(
            self.widget, Stage.LIVE, "system", previous_status=Stage.DRAFT,
        )
        row.refresh_from_db()
        self.assertEqual((row.previous_status, row.new_status), ("draft", "live"))

    def test_the_actor_comes_from_the_request(self):
        user = User.objects.create_user(username="ana", password="x")
        request = RequestFactory().post("/")
        request.user = user
        set_current_request(request)
        row = track_status_transition(self.widget, "live", "customer", previous_status="draft")
        self.assertEqual(row.changed_by, user)

    def test_outside_a_request_there_is_no_actor(self):
        row = track_status_transition(self.widget, "live", "system", previous_status="draft")
        self.assertIsNone(row.changed_by)

    def test_a_source_that_does_not_fit_the_column_is_refused(self):
        """PostgreSQL would reject it and SQLite would store it."""
        with self.assertRaises(ValueError):
            track_status_transition(self.widget, "live", "x" * 21, previous_status="draft")
        with self.assertRaises(ValueError):
            track_status_transition(self.widget, "live", "", previous_status="draft")

    @override_settings(DJANGO_COMMON_KIT={"TENANT": {"INSTANCE_ATTRIBUTE": "parent_id"}})
    def test_the_tenant_is_the_instances(self):
        parent = Widget.objects.create(name="shelf")
        child = Widget.objects.create(name="draft", parent=parent)
        row = track_status_transition(child, "live", "system", previous_status="draft")
        self.assertEqual(row.tenant_id, parent.pk)

    def test_transitions_are_listed_newest_first_and_by_field(self):
        first = track_status_transition(self.widget, "live", "system", previous_status="draft")
        second = track_status_transition(self.widget, "archived", "system", previous_status="live")
        other = track_status_transition(self.widget, "b", "system", previous_status="a", field_name="stage")
        rows = list(get_status_transitions(self.widget))
        self.assertEqual(set(rows), {first, second, other})
        self.assertEqual(
            list(get_status_transitions(self.widget, field_name="status").values_list("new_status", flat=True)),
            ["archived", "live"],
        )

    def test_another_objects_transitions_are_not_listed(self):
        track_status_transition(self.widget, "live", "system", previous_status="draft")
        other = Widget.objects.create(name="lamp")
        self.assertFalse(get_status_transitions(other).exists())
