"""The change-history trail (PRD §6)."""

from decimal import Decimal

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.migrations.state import ProjectState
from django.test import RequestFactory, TestCase, override_settings

from django_common_utils.history import HistoryMixin, get_model_history, make_json_safe
from django_common_utils.models import ModelHistory
from django_common_utils.request_context import set_current_request
from tests.testapp.models import Gadget, Telemetry, Widget

User = get_user_model()


class HistoryRecordingTests(TestCase):
    def test_create_writes_a_row_with_an_after_snapshot(self):
        widget = Widget.objects.create(name="lamp")
        rows = get_model_history(widget)
        self.assertEqual(rows.count(), 1)
        row = rows.first()
        self.assertEqual(row.action, "create")
        self.assertIsNone(row.object_snapshot_before)
        self.assertEqual(row.object_snapshot_after["name"], "lamp")
        self.assertEqual(row.field_changes, {})

    def test_update_records_only_what_changed(self):
        widget = Widget.objects.create(name="lamp", notes="old")
        widget.notes = "new"
        widget.save()
        row = get_model_history(widget).first()
        self.assertEqual(row.action, "update")
        self.assertEqual(row.field_changes, {"notes": {"before": "old", "after": "new"}})
        self.assertNotIn("name", row.field_changes)

    def test_a_save_that_changes_nothing_is_not_a_change(self):
        widget = Widget.objects.create(name="lamp")
        widget.save()
        self.assertEqual(get_model_history(widget).count(), 1)

    def test_delete_keeps_the_before_snapshot(self):
        widget = Widget.objects.create(name="lamp")
        pk = widget.pk
        widget.delete()
        row = ModelHistory.objects.filter(object_id=str(pk), action="delete").get()
        self.assertEqual(row.object_snapshot_before["name"], "lamp")
        self.assertIsNone(row.object_snapshot_after)

    def test_updated_at_is_never_recorded(self):
        widget = Widget.objects.create(name="lamp")
        widget.notes = "x"
        widget.save()
        row = get_model_history(widget).first()
        self.assertNotIn("updated_at", row.field_changes)
        self.assertNotIn("updated_at", row.object_snapshot_after)

    def test_related_objects_compare_by_pk(self):
        parent = Widget.objects.create(name="shelf")
        widget = Widget.objects.create(name="lamp", parent=parent)
        widget.parent = Widget.objects.get(pk=parent.pk)  # a different instance, same row
        widget.save()
        self.assertEqual(get_model_history(widget).count(), 1)

    def test_a_generic_relation_is_recorded_by_its_columns_not_its_row(self):
        """Reading content_object would load the related row on every save."""
        from django.contrib.contenttypes.models import ContentType

        from django_common_utils.models import StatusTransitionModel

        widget = Widget.objects.create(name="lamp")
        transition = StatusTransitionModel.objects.create(
            content_type=ContentType.objects.get_for_model(Widget), object_id=str(widget.pk),
            new_status="done", transition_source="system",
        )
        snapshot = get_model_history(transition).first().object_snapshot_after
        self.assertNotIn("content_object", snapshot)
        self.assertEqual(snapshot["object_id"], str(widget.pk))

    def test_telemetry_tables_are_not_tracked(self):
        """PRD §6.3: with tracking on, a retention sweep snapshots what it purges."""
        Telemetry.objects.create(payload="x")
        self.assertEqual(ModelHistory.objects.count(), 0)

    def test_a_save_by_a_data_migration_is_not_recorded(self):
        """A model from migration state still inherits HistoryMixin through the
        bases the migration files record. On a fresh database a project's data
        migration can run before `model_history` exists, and the failed insert
        breaks the migration's transaction."""
        MigrationWidget = ProjectState.from_apps(apps).apps.get_model("testapp", "Widget")
        self.assertTrue(issubclass(MigrationWidget, HistoryMixin))
        MigrationWidget.objects.create(name="seeded")
        self.assertEqual(ModelHistory.objects.count(), 0)

    def test_the_trail_does_not_record_itself(self):
        Widget.objects.create(name="lamp")
        self.assertEqual(ModelHistory.objects.count(), 1)

    @override_settings(DJANGO_COMMON_UTILS={"HISTORY": {"ENABLED": False}})
    def test_can_be_switched_off_globally(self):
        Widget.objects.create(name="lamp")
        self.assertEqual(ModelHistory.objects.count(), 0)

    @override_settings(DJANGO_COMMON_UTILS={"HISTORY": {"MAX_VALUE_LENGTH": 10}})
    def test_long_values_are_truncated(self):
        widget = Widget.objects.create(name="lamp", notes="x" * 100)
        row = get_model_history(widget).first()
        self.assertEqual(len(row.object_snapshot_after["notes"]), 11)


class HistoryFieldKindTests(TestCase):
    """Fields whose attribute is not the column's value."""

    def test_a_many_to_many_is_not_in_the_snapshot(self):
        """The attribute is a manager, which serialised as ``"testapp.Widget.None"``."""
        gadget = Gadget.objects.create(name="radio")
        gadget.parts.add(Widget.objects.create(name="dial"))
        self.assertNotIn("parts", gadget.get_history_fields())
        self.assertNotIn("parts", get_model_history(gadget).first().object_snapshot_after)

    def test_an_empty_file_is_not_a_change_on_the_next_save(self):
        """The in-memory FieldFile's name is None, the stored one's is ''."""
        gadget = Gadget.objects.create(name="radio")
        gadget.name = "wireless"
        gadget.save()
        row = get_model_history(gadget).first()
        self.assertEqual(row.field_changes, {"name": {"before": "radio", "after": "wireless"}})

    def test_a_file_that_changes_is_recorded_by_its_name(self):
        gadget = Gadget.objects.create(name="radio")
        Gadget.objects.filter(pk=gadget.pk).update(manual="manuals/old.pdf")
        gadget = Gadget.objects.get(pk=gadget.pk)
        gadget.manual = "manuals/new.pdf"
        gadget.save()
        row = get_model_history(gadget).first()
        self.assertEqual(
            row.field_changes,
            {"manual": {"before": "manuals/old.pdf", "after": "manuals/new.pdf"}},
        )


class HistoryActorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ana", password="x")
        self.addCleanup(set_current_request, None)

    def test_actor_ip_and_correlation_id_come_from_the_request(self):
        request = RequestFactory().get("/", REMOTE_ADDR="10.1.2.3", HTTP_USER_AGENT="ua")
        request.user = self.user
        request.correlation_id = "abc-123"
        set_current_request(request)

        widget = Widget.objects.create(name="lamp")
        row = get_model_history(widget).first()
        self.assertEqual(row.changed_by, self.user)
        self.assertEqual(row.ip_address, "10.1.2.3")
        self.assertEqual(row.user_agent, "ua")
        self.assertEqual(row.correlation_id, "abc-123")

    def test_a_save_outside_a_request_records_no_actor(self):
        """Rather than guessing one (PRD §6.2)."""
        widget = Widget.objects.create(name="lamp")
        row = get_model_history(widget).first()
        self.assertIsNone(row.changed_by)
        self.assertIsNone(row.ip_address)

    def test_exclude_fields_is_not_mutated_across_calls(self):
        """Returning the class list and extending it grows it for the life of the process."""
        widget = Widget(name="lamp")
        before = list(Widget._history_exclude_fields)
        widget.get_history_fields()
        widget.get_history_fields()
        self.assertEqual(Widget._history_exclude_fields, before)


class JsonSafeTests(TestCase):
    def test_decimal_uuid_and_model_are_serialisable(self):
        widget = Widget.objects.create(name="lamp", price=Decimal("9.99"))
        safe = make_json_safe({"price": widget.price, "id": widget.pk, "obj": widget})
        self.assertEqual(safe["price"], 9.99)
        self.assertEqual(safe["id"], str(widget.pk))
        self.assertEqual(safe["obj"], str(widget.pk))
