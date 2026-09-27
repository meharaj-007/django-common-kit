"""Adoption by ``--fake-initial`` (PRD §13).

Simulates a project that already has every table: migrate forward to build them,
forget that the initial migrations ran, then adopt. Every ``initial`` migration
must be faked (its table exists) and every non-initial one must run for real
(its column does not).
"""

from django.core.management import call_command
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase

BASE = {"id", "is_active", "is_deleted", "created_at", "updated_at", "created_by", "updated_by"}

#: The original shape of each table — what an initial migration may declare.
#: Anything else goes in a later, non-initial migration.
REPO_COLUMNS = {
    "parameters": BASE | {
        "key", "value_text", "value_integer", "value_float", "value_boolean", "value_json",
        "parameter_type", "description", "category", "is_system",
    },
    "model_history": BASE | {
        "content_type", "object_id", "action", "changed_by", "field_changes",
        "object_snapshot_before", "object_snapshot_after", "change_reason", "ip_address", "user_agent",
    },
    "request_logs": BASE | {
        "user", "ip_address", "endpoint", "method", "request_body", "status_code", "response",
        "user_agent", "trace_id", "response_time", "error_message",
    },
    "blocked_ips": BASE | {"ip_address", "reason", "attempts", "first_attempt", "last_attempt"},
    "ip_tracking": BASE | {
        "user", "ip_address", "endpoint", "method", "user_agent", "device_type", "browser",
        "operating_system", "country", "country_code", "continent", "continent_code", "region",
        "region_name", "city", "district", "zip_code", "latitude", "longitude", "timezone",
        "currency", "isp", "organization", "as_number", "is_mobile", "trace_id", "status_code",
        "response_time",
    },
    "contact_us": BASE | {
        "name", "email", "phone_number", "subject", "message", "ip_address", "user_agent", "is_checked",
    },
    "status_transitions": BASE | {
        "content_type", "object_id", "previous_status", "new_status", "changed_by",
        "transition_source", "transition_reason", "notes", "timestamp",
    },
    "common_files": BASE | {
        "content_type", "object_id", "file", "original_filename", "title", "description", "tag",
        "mime_type", "file_size", "metadata", "uploaded_by",
    },
    "short_links": BASE | {
        "slug", "target_url", "click_count", "last_clicked_at", "expires_at", "purpose",
    },
    # Tables no project had before the package: born with tenant_id.
    "platform_notices": BASE | {
        "tenant_id", "title", "body", "severity", "audience", "surface", "starts_at", "ends_at",
        "priority", "is_dismissible", "action_label", "action_url", "metadata",
    },
    "platform_notice_dismissals": BASE | {"tenant_id", "notice", "user"},
}

#: Columns added after the initial shape, each in its own non-initial migration.
LATE_COLUMNS = {
    "parameters": {"tenant_id"},
    "model_history": {"correlation_id", "tenant_id"},
    "request_logs": {"query_string", "tenant_id"},
    "blocked_ips": {"blocked_at", "tenant_id"},
    "ip_tracking": {
        "referer", "query_string", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "tenant_id",
    },
    "contact_us": {"tenant_id"},
    "status_transitions": {"field_name", "tenant_id"},
    "common_files": {"tenant_id"},
    "short_links": {"tenant_id"},
}


def _columns(table):
    with connection.cursor() as cursor:
        return {col.name for col in connection.introspection.get_table_description(cursor, table)}


def _package_migrations():
    loader = MigrationLoader(connection)
    return sorted(
        (name, migration) for (app, name), migration in loader.disk_migrations.items()
        if app == "common_control"
    )


class FakeInitialAdoptionTests(TransactionTestCase):
    def test_initial_migrations_are_faked_and_later_ones_run(self):
        migrations = _package_migrations()

        # A project before adoption: every table present in its original shape,
        # and no package migration recorded. Built by migrating
        # fully and then dropping the late columns, because the non-initial
        # migrations are interleaved with the initial ones.
        call_command("migrate", "common_control", verbosity=0)
        # Raw SQL rather than schema_editor.remove_field: on SQLite the latter
        # remakes the table from the *model*, which puts back every late column
        # removed a moment earlier.
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
        for table, late in LATE_COLUMNS.items():
            self.assertFalse(late & _columns(table), f"{table} should not yet have {late}")

        call_command("migrate", "common_control", fake_initial=True, verbosity=0)

        applied = set(
            MigrationRecorder(connection).migration_qs
            .filter(app="common_control").values_list("name", flat=True)
        )
        self.assertEqual(applied, {name for name, _ in migrations})
        for table, late in LATE_COLUMNS.items():
            self.assertTrue(late <= _columns(table), f"{table} is missing {late - _columns(table)}")

    def test_initial_migrations_add_no_column_the_repos_lack(self):
        """The §13.3 rule, as a test."""
        seen = set()
        for name, migration in _package_migrations():
            if not migration.initial:
                continue
            for operation in migration.operations:
                self.assertEqual(
                    type(operation).__name__, "CreateModel",
                    f"{name}: an initial migration holds only CreateModel",
                )
                table = operation.options["db_table"]
                declared = {field_name for field_name, _ in operation.fields}
                self.assertEqual(
                    declared, REPO_COLUMNS[table],
                    f"{name} declares columns outside the original shape: {declared - REPO_COLUMNS[table]}",
                )
                seen.add(table)
        self.assertEqual(seen, set(REPO_COLUMNS), "every surveyed table has an initial migration")

    def test_one_table_per_initial_migration(self):
        """--fake-initial fakes a migration only when every table it creates
        exists, and not every project has every table."""
        for name, migration in _package_migrations():
            if migration.initial:
                self.assertEqual(len(migration.operations), 1, name)
