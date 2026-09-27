"""Postgres-only checks. Skipped on SQLite — a green SQLite run is not evidence.

The column types the package's migrations produce on Postgres. A project
adopting an existing table with ``--fake-initial`` diffs its
``pg_dump --schema-only`` against these (PRD §13); a disagreement is adopted
silently and fails at the first write that hits it.
"""

from django.db import connection
from django.test import TestCase


def _types(table):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = %s", [table],
        )
        return {name: (data_type, nullable == "YES") for name, data_type, nullable in cursor.fetchall()}


class PostgresSchemaTests(TestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Postgres only")

    def test_uuid_primary_keys(self):
        for table in ("parameters", "model_history", "request_logs", "common_files"):
            self.assertEqual(_types(table)["id"], ("uuid", False), table)

    def test_json_columns_are_jsonb(self):
        self.assertEqual(_types("model_history")["field_changes"][0], "jsonb")
        self.assertEqual(_types("parameters")["value_json"][0], "jsonb")
        self.assertEqual(_types("common_files")["metadata"][0], "jsonb")

    def test_ip_columns_are_inet(self):
        self.assertEqual(_types("request_logs")["ip_address"], ("inet", True))
        self.assertEqual(_types("ip_tracking")["ip_address"], ("inet", False))
        # blocked_ips deliberately stores the address as text; the table is
        # keyed on it as a string.
        self.assertEqual(_types("blocked_ips")["ip_address"][0], "character varying")

    def test_not_null_columns_the_writers_depend_on(self):
        """A NOT NULL column in a live table that the package does not fill
        fails every insert after adoption. The inverse — a column the package
        declares NOT NULL that the live table has nullable — is harmless. This
        pins what the package expects so the diff has a side."""
        self.assertEqual(_types("ip_tracking")["endpoint"][1], False)
        self.assertEqual(_types("ip_tracking")["method"][1], False)
        self.assertEqual(_types("status_transitions")["new_status"][1], False)
        self.assertEqual(_types("short_links")["target_url"][1], False)

    def test_late_columns_exist_after_full_migrate(self):
        self.assertIn("correlation_id", _types("model_history"))
        self.assertIn("query_string", _types("request_logs"))
        self.assertIn("blocked_at", _types("blocked_ips"))
        self.assertIn("utm_source", _types("ip_tracking"))

    def test_tenant_id_is_a_nullable_uuid_on_every_table(self):
        """A project renaming its own tenant column to adopt one of these must
        match: uuid, nullable, and no foreign key the package would not expect."""
        for table in (
            "parameters", "model_history", "request_logs", "blocked_ips", "ip_tracking",
            "contact_us", "status_transitions", "common_files", "short_links",
            "platform_notices", "platform_notice_dismissals",
        ):
            self.assertEqual(_types(table)["tenant_id"], ("uuid", True), table)

    def test_status_transition_field_name_is_not_null(self):
        self.assertEqual(_types("status_transitions")["field_name"], ("character varying", False))
