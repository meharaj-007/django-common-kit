"""``adopt_tables`` (PRD §13).

Builds a project before adoption the way ``test_migrations`` does — every table
present in its original shape, late columns and their indexes gone, no package
migration recorded — and has the command take it over.
"""

import uuid
from io import StringIO
from unittest import mock

from django.contrib.contenttypes.models import ContentType
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase
from django.utils import timezone

from django_common_utils.management.commands.adopt_tables import Command

from .test_migrations import LATE_COLUMNS, _columns, _package_migrations


def _recorded():
    return set(
        MigrationRecorder(connection).migration_qs
        .filter(app="common_control").values_list("name", flat=True)
    )


def _restore():
    """Back to a fully migrated package, whatever state a test left behind.

    TransactionTestCase flushes rows between tests but not the schema or the
    migration recorder, so a test that ends unadopted would hand the next one —
    and every later test module — tables without their late columns.
    """
    call_command("migrate", "common_control", fake_initial=True, verbosity=0)


def _unadopt():
    """Every table in its original shape, and no package migration recorded."""
    _restore()
    with connection.cursor() as cursor:
        for table, late in LATE_COLUMNS.items():
            constraints = connection.introspection.get_constraints(cursor, table)
            for name, info in constraints.items():
                if info["index"] and set(info["columns"]) & late:
                    cursor.execute(f"DROP INDEX {connection.ops.quote_name(name)}")
            for column in late:
                cursor.execute(
                    f"ALTER TABLE {connection.ops.quote_name(table)} "
                    f"DROP COLUMN {connection.ops.quote_name(column)}"
                )
    MigrationRecorder(connection).migration_qs.filter(app="common_control").delete()


def _adopt(**options):
    out = StringIO()
    call_command("adopt_tables", stdout=out, **options)
    return out.getvalue()


class AdoptMigrationsTests(TransactionTestCase):
    def setUp(self):
        _unadopt()
        self.addCleanup(_restore)

    def test_dry_run_changes_nothing(self):
        output = _adopt()
        self.assertIn("0001_parameters: fake", output)
        self.assertFalse(_recorded())
        for table, late in LATE_COLUMNS.items():
            self.assertFalse(late & _columns(table))

    def test_execute_records_every_migration_and_adds_the_late_columns(self):
        _adopt(execute=True)
        self.assertEqual(_recorded(), {name for name, _ in _package_migrations()})
        for table, late in LATE_COLUMNS.items():
            self.assertTrue(late <= _columns(table), f"{table} is missing {late - _columns(table)}")

    def test_a_dry_run_reads_a_table_created_in_the_same_run_as_empty(self):
        """0014 adds tenant_id to short_links, which 0010 creates in this run:
        a dry run must predict what an executing run will do, not crash."""
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE short_links")
        output = _adopt()
        self.assertIn("0010_short_links: apply", output)
        self.assertIn("short_links.tenant_id", output)

    def test_added_columns_keep_their_index_where_there_is_no_concurrent_step(self):
        _adopt(execute=True)
        if connection.vendor == "postgresql":
            self.skipTest("PostgreSQL builds them concurrently instead")
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, "ip_tracking")
        self.assertTrue(any(
            info["index"] and info["columns"] == ["utm_source"] for info in constraints.values()
        ))

    def test_the_tenant_indexes_are_built(self):
        _adopt(execute=True)
        with connection.cursor() as cursor:
            names = set(connection.introspection.get_constraints(cursor, "model_history"))
        self.assertIn("model_history_tenant_idx", names)

    def test_refuses_to_fake_onto_a_table_of_another_shape(self):
        """A project table that only shares the name must not be taken over."""
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE contact_us DROP COLUMN subject")
        try:
            with self.assertRaisesMessage(CommandError, "contact_us exists but lacks subject"):
                _adopt()
        finally:
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE contact_us ADD COLUMN subject varchar(255) NOT NULL DEFAULT ''")

    def test_a_second_run_finds_nothing_to_do(self):
        _adopt(execute=True)
        self.assertIn("all applied", _adopt(execute=True))


class AdoptContentTypeTests(TransactionTestCase):
    """The old app's rows are renamed, not duplicated, so history keeps its target."""

    def setUp(self):
        _unadopt()
        self.addCleanup(_restore)
        # migrate created the package's row; a project adopting its tables has
        # only its own. Raw SQL, because the ORM's cascade reads model_history
        # whole, and its late column is gone by now.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM django_content_type WHERE app_label = %s AND model = %s",
                ["common_control", "requestlog"],
            )
            (package_row,) = cursor.fetchone()
            cursor.execute("DELETE FROM auth_permission WHERE content_type_id = %s", [package_row])
            cursor.execute("DELETE FROM django_content_type WHERE id = %s", [package_row])
        ContentType.objects.clear_cache()
        self.old = ContentType.objects.create(app_label="legacy", model="requestlogmodel")
        patcher = mock.patch.object(
            Command, "_old_app_tables", return_value={"request_logs": "requestlogmodel"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_old_row_is_renamed_in_place(self):
        _adopt(old_app="legacy", execute=True)
        self.old.refresh_from_db()
        self.assertEqual((self.old.app_label, self.old.model), ("common_control", "requestlog"))
        self.assertEqual(ContentType.objects.filter(app_label="common_control", model="requestlog").count(), 1)

    def test_dry_run_leaves_it(self):
        _adopt(old_app="legacy")
        self.old.refresh_from_db()
        self.assertEqual(self.old.app_label, "legacy")

    def test_a_table_not_created_yet_holds_no_references(self):
        """A deploy script runs this on a fresh database too, before the plain
        `migrate` has created every table that points at a content type."""
        ContentType.objects.create(app_label="common_control", model="requestlog")
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE django_admin_log RENAME TO django_admin_log_away")
        self.addCleanup(self._put_admin_log_back)
        _adopt(old_app="legacy", execute=True)
        self.assertFalse(ContentType.objects.filter(pk=self.old.pk).exists())

    @staticmethod
    def _put_admin_log_back():
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE django_admin_log_away RENAME TO django_admin_log")

    def test_refuses_when_both_exist_and_the_old_one_is_referenced(self):
        new = ContentType.objects.create(app_label="common_control", model="requestlog")
        # Raw SQL naming only the original columns: the table is unadopted, so
        # the ORM's insert would name late ones it does not have yet.
        now = timezone.now()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO status_transitions (id, is_active, is_deleted, created_at, updated_at, "
                "content_type_id, object_id, new_status, transition_source, timestamp) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [uuid.uuid4().hex if connection.vendor == "sqlite" else uuid.uuid4(),
                 True, False, now, now, self.old.pk, "1", "x", "system", now],
            )
        with self.assertRaisesMessage(CommandError, "still referenced"):
            _adopt(old_app="legacy", execute=True)
        self.assertTrue(ContentType.objects.filter(pk=new.pk).exists())


class AdoptIndexTests(TransactionTestCase):
    """Index reconciliation is PostgreSQL-only; this runs in the Postgres leg."""

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Postgres only")
        _restore()

    def test_renames_a_same_definition_index_and_builds_a_missing_one(self):
        with connection.cursor() as cursor:
            cursor.execute('ALTER INDEX "request_log_created_idx" RENAME TO "request_log_created_4f2782_idx"')
            cursor.execute('DROP INDEX "status_trans_status_idx"')
        _adopt(execute=True)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexname FROM pg_indexes WHERE indexname IN "
                "('request_log_created_idx', 'request_log_created_4f2782_idx', 'status_trans_status_idx')"
            )
            names = {row[0] for row in cursor.fetchall()}
        self.assertEqual(names, {"request_log_created_idx", "status_trans_status_idx"})

    def test_leaves_an_index_the_package_does_not_declare(self):
        with connection.cursor() as cursor:
            cursor.execute('CREATE INDEX "project_own_idx" ON "request_logs" ("status_code")')
        self.assertIn("every declared index is in place", _adopt(execute=True))
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_indexes WHERE indexname = 'project_own_idx'")
            self.assertTrue(cursor.fetchone())


class AdoptOldAppTests(TransactionTestCase):
    def test_an_app_with_nothing_applied_has_nothing_to_move(self):
        """A fresh database: deploy scripts run the command there too."""
        command = Command()
        command.connection = connection
        with mock.patch.object(MigrationRecorder, "applied_migrations", return_value={}):
            self.assertEqual(command._old_app_tables("testapp"), {})

    def test_a_wrong_label_is_refused(self):
        with self.assertRaisesMessage(CommandError, "not an installed app"):
            _adopt(old_app="no_such_app")
