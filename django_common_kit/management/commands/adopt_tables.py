"""``adopt_tables`` — take over tables a project already has (PRD §13).

A project that predates the package has some of its tables already, owned by its
own app. Adopting them in place is ordered work that plain ``migrate`` cannot do
alone, so it is one command that works the order out from the database itself:

1. **Content types move first.** ``--old-app`` names the app whose models owned
   these tables. Its ``django_content_type`` rows are renamed to this app,
   before any ``migrate`` runs: the first ``migrate`` fires ``post_migrate``,
   which would otherwise create fresh rows for the package's models beside the
   old ones, and a later ``remove_stale_contenttypes`` deletes the old rows —
   cascading to every ``model_history`` row that points at them. The
   ``migrate`` calls in step 2 still re-create the old rows (``post_migrate``
   builds content types from migration state, where the old app keeps its
   models until its own state-only delete runs); those copies point at nothing
   but their own permissions, and are dropped once step 2 is done.
2. **Each package migration is faked or applied on the evidence.** An initial
   migration whose table exists is faked; one whose table does not is applied.
   A migration that only adds columns is faked once the columns exist, and a
   missing nullable column is added first, *without* its index — ``ADD COLUMN``
   of a nullable column is instant, an index build on a large telemetry table
   locks it for minutes.
3. **Indexes are reconciled** (PostgreSQL). ``--fake-initial`` never compares
   indexes, so a live table can lack one the package declares, or hold it under
   another name. Same definition under another name is renamed; missing is built
   with ``CREATE INDEX CONCURRENTLY``. Indexes the package does not declare are
   left alone.

Dry run by default: it prints the plan and changes nothing. ``--execute`` does
it. Run it with the project's code already switched over (the package installed,
the project's own copies of the models gone from its ``models.py``), then run
``migrate`` for everything else. It is safe to re-run: each step checks before
it acts.
"""

import re

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.operations import AddField, AddIndex, CreateModel

APP_LABEL = "common_control"

_INDEX_SQL = re.compile(
    r'^CREATE (?P<unique>UNIQUE )?INDEX "?(?P<name>[^"\s]+)"? ON "?(?P<table>[^"\s(]+)"?'
    r"(?: USING \w+)? (?P<spec>\(.*\))",
)
_LIVE_INDEX = re.compile(r"^CREATE (?P<unique>UNIQUE )?INDEX \S+ ON \S+ USING \w+ (?P<spec>\(.*\))")


def _definition(unique, spec):
    """An index reduced to what makes two of them the same index."""
    return bool(unique), re.sub(r'["\s]', "", spec)


class Command(BaseCommand):
    help = (
        "Adopt tables the project already has: move content types, fake or apply "
        "each package migration on the evidence, reconcile indexes. Dry run unless --execute."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--old-app",
            help="Label of the project app whose models owned these tables. Its content types move here.",
        )
        parser.add_argument("--execute", action="store_true", help="Make the changes. Without it, print the plan.")
        parser.add_argument("--database", default=DEFAULT_DB_ALIAS)

    def handle(self, *args, **options):
        self.alias = options["database"]
        self.connection = connections[self.alias]
        self.execute = options["execute"]
        self.stdout.write("Executing." if self.execute else "Dry run — nothing will change. Re-run with --execute.")

        self.moved_content_types = []
        self.move_content_types(options["old_app"])
        self.walk_migrations()
        self.drop_recreated_content_types(options["old_app"])
        self.reconcile_indexes()

        self.stdout.write(
            self.style.SUCCESS("Done. Now run: manage.py migrate")
            if self.execute else "End of plan."
        )

    # -- 1. content types -----------------------------------------------------

    def move_content_types(self, old_app):
        self.stdout.write("\nContent types")
        if not old_app:
            self.stdout.write("  skipped — no --old-app given")
            return

        from django.contrib.contenttypes.models import ContentType

        ours = {model._meta.db_table: model._meta.model_name for model in self._our_models()}
        manager = ContentType.objects.db_manager(self.alias)
        moved = 0
        for table, old_name in sorted(self._old_app_tables(old_app).items()):
            new_name = ours.get(table)
            if new_name is None:
                continue
            old = manager.filter(app_label=old_app, model=old_name).first()
            new_exists = manager.filter(app_label=APP_LABEL, model=new_name).exists()
            if old is None:
                continue
            if new_exists:
                references = self._references(old)
                if references:
                    raise CommandError(
                        f"{old_app}.{old_name} and {APP_LABEL}.{new_name} both have a content type, and "
                        f"the old one is still referenced by {references}. A migrate ran with the "
                        f"package installed before this command did. Repoint those rows to "
                        f"{APP_LABEL}.{new_name}, delete the old content type, then re-run."
                    )
                # An unreferenced copy a previous run's migrate put back.
                self.stdout.write(f"  {old_app}.{old_name}: already moved; the copy left behind is dropped below")
                self.moved_content_types.append(old_name)
                continue
            self.stdout.write(f"  {old_app}.{old_name} -> {APP_LABEL}.{new_name} ({table})")
            self.moved_content_types.append(old_name)
            moved += 1
            if self.execute:
                old.app_label, old.model = APP_LABEL, new_name
                old.save(using=self.alias, update_fields=["app_label", "model"])
        if not moved:
            self.stdout.write("  nothing to move")
        ContentType.objects.clear_cache()

    def drop_recreated_content_types(self, old_app):
        """Drop the old-app rows that step 2's ``post_migrate`` put back.

        Only a row nothing references is dropped — its own permissions aside,
        which Django re-creates for the new rows anyway. One with references is
        reported and kept: something wrote against the old name after the move,
        and a person should look before those rows lose their target.
        """
        if not self.moved_content_types:
            return
        from django.contrib.contenttypes.models import ContentType

        self.stdout.write("\nContent types re-created by migrate")
        manager = ContentType.objects.db_manager(self.alias)
        if not self.execute:
            self.stdout.write(f"  any {old_app} row among {', '.join(self.moved_content_types)} is dropped here")
            return
        for old_name in self.moved_content_types:
            row = manager.filter(app_label=old_app, model=old_name).first()
            if row is None:
                continue
            references = self._references(row)
            if references:
                self.stdout.write(self.style.WARNING(f"  kept {old_app}.{old_name}: still referenced by {references}"))
                continue
            self.stdout.write(f"  dropped {old_app}.{old_name}")
            self._drop_content_type(row)
        ContentType.objects.clear_cache()

    def _drop_content_type(self, row):
        """Delete an unreferenced content type and its permissions.

        Not ``row.delete()``: its cascade reads every table that points at a
        content type, and on a fresh database some are not created yet.
        ``_references`` has already found none of them holding this row, so the
        row itself goes by primary key.
        """
        if apps.is_installed("django.contrib.auth"):
            apps.get_model("auth", "Permission")._base_manager.using(self.alias).filter(content_type=row).delete()
        table = self.connection.ops.quote_name(type(row)._meta.db_table)
        with self.connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {table} WHERE id = %s", [row.pk])

    def _references(self, content_type):
        """``{"app.Model.field": rows}`` pointing at a content type, permissions aside.

        A table that does not exist yet holds no references. A deploy script
        runs this command on a fresh database too, before the plain ``migrate``
        has created every table that points at a content type
        (``django_admin_log``, say).
        """
        permission = apps.get_model("auth", "Permission") if apps.is_installed("django.contrib.auth") else None
        tables = set(self.connection.introspection.table_names())
        references = {}
        for rel in type(content_type)._meta.related_objects:
            if rel.related_model is permission or rel.related_model._meta.db_table not in tables:
                continue
            count = rel.related_model._base_manager.using(self.alias).filter(
                **{rel.field.name: content_type}
            ).count()
            if count:
                references[f"{rel.related_model._meta.label}.{rel.field.name}"] = count
        return references

    def _old_app_tables(self, old_app):
        """``{db_table: model_name}`` for the old app, as its applied migrations left it.

        Read from migration state, not from the old app's code, because by now its
        models.py no longer has these models — that is the point of adopting.
        """
        loader = MigrationLoader(self.connection)
        if old_app not in loader.migrated_apps:
            raise CommandError(f"{old_app!r} is not an installed app with migrations; is that the right label?")
        applied = [key for key in loader.applied_migrations if key[0] == old_app]
        if not applied:
            # A fresh database: the old app never created these tables, so it
            # has no content types to hand over.
            return {}
        newest = max(applied, key=lambda key: len(loader.graph.forwards_plan(key)))
        state = loader.project_state(newest, at_end=True)
        return {
            model_state.options.get("db_table") or f"{old_app}_{model_state.name_lower}": model_state.name_lower
            for (label, _), model_state in state.models.items()
            if label == old_app
        }

    # -- 2. migrations --------------------------------------------------------

    def walk_migrations(self):
        self.stdout.write("\nMigrations")
        executor = MigrationExecutor(self.connection)
        targets = [key for key in executor.loader.graph.leaf_nodes() if key[0] == APP_LABEL]
        plan = [migration for migration, _ in executor.migration_plan(targets) if migration.app_label == APP_LABEL]
        if not plan:
            self.stdout.write("  all applied")
            return
        for migration in plan:
            action, detail, missing = self._classify(executor.loader, migration)
            self.stdout.write(f"  {migration.name}: {action} — {detail}")
            if not self.execute:
                continue
            if missing:
                self._add_columns_without_indexes(missing)
            call_command(
                "migrate", APP_LABEL, migration.name,
                fake=action != "apply", database=self.alias, verbosity=0,
            )

    def _classify(self, loader, migration):
        """``(action, why, columns_to_add_first)`` for one unapplied migration."""
        operations = migration.operations
        tables = set(self.connection.introspection.table_names())

        if migration.initial:
            created = [op for op in operations if isinstance(op, CreateModel)]
            names = [op.options["db_table"] for op in created]
            present = [table for table in names if table in tables]
            if created and len(present) == len(created):
                self._check_shape(loader, migration, created)
                return "fake", f"{', '.join(names)} exists", []
            if not present:
                return "apply", f"creates {', '.join(names) or 'nothing'}", []
            raise CommandError(f"{migration.name}: some of {names} exist and some do not.")

        if operations and all(isinstance(op, AddIndex) for op in operations):
            state = loader.project_state((APP_LABEL, migration.name), at_end=True)
            indexed = [state.apps.get_model(APP_LABEL, op.model_name)._meta.db_table for op in operations]
            missing = [
                op.index.name for op, table in zip(operations, indexed)
                if table not in tables or op.index.name not in self._index_names(table)
            ]
            if not missing:
                return "fake", "its indexes exist", []
            if self.connection.vendor == "postgresql":
                return "fake", f"{', '.join(missing)} built concurrently in the next step", []
            return "apply", f"builds {', '.join(missing)}", []

        if operations and all(isinstance(op, AddField) for op in operations):
            state = loader.project_state((APP_LABEL, migration.name), at_end=True)
            missing = []
            for op in operations:
                model = state.apps.get_model(APP_LABEL, op.model_name)
                field = model._meta.get_field(op.name)
                # A table an earlier migration in this run creates does not exist
                # yet in a dry run: it will, without this column — as an
                # executing run finds it when it gets here.
                table = model._meta.db_table
                existing = self._columns(table) if table in tables else set()
                if field.column not in existing:
                    missing.append((model, field))
            if not missing:
                return "fake", "its columns exist", []
            if all(field.null for _, field in missing):
                names = ", ".join(f"{model._meta.db_table}.{field.column}" for model, field in missing)
                later = " (indexes come in the next step)" if self.connection.vendor == "postgresql" else ""
                return "fake", f"after adding {names}{later}", missing
            return "apply", "adds a column that is not nullable", []

        return "apply", "not a create or add-column migration", []

    def _check_shape(self, loader, migration, created):
        """Refuse to fake an initial migration onto a table it did not create.

        A table name is all ``--fake-initial`` compares. A project table that
        merely shares the name — a different thing with a different shape —
        would be taken over, and the package would write into it. Every column
        the initial migration declares must be there; extra columns are fine.
        """
        state = loader.project_state((APP_LABEL, migration.name), at_end=True)
        for op in created:
            model = state.apps.get_model(APP_LABEL, op.name)
            table = model._meta.db_table
            declared = {field.column for field in model._meta.local_concrete_fields}
            absent = sorted(declared - self._columns(table))
            if absent:
                raise CommandError(
                    f"{migration.name}: {table} exists but lacks {', '.join(absent)}. It is not the "
                    f"table this migration creates — rename the project's own table, or bring it "
                    f"to this shape, before adopting."
                )

    def _index_names(self, table):
        with self.connection.cursor() as cursor:
            return set(self.connection.introspection.get_constraints(cursor, table))

    def _add_columns_without_indexes(self, missing):
        # On PostgreSQL the index is left to reconcile_indexes, which builds it
        # CONCURRENTLY; elsewhere there is no such step, so the column is added
        # with it. The state apps are built fresh for this migration, so
        # switching the index off on their field touches nothing else.
        concurrent_later = self.connection.vendor == "postgresql"
        with self.connection.schema_editor() as editor:
            for model, field in missing:
                if concurrent_later:
                    field.db_index = False
                editor.add_field(model, field)

    # -- 3. indexes -----------------------------------------------------------

    def reconcile_indexes(self):
        self.stdout.write("\nIndexes")
        if self.connection.vendor != "postgresql":
            self.stdout.write(f"  skipped — {self.connection.vendor}; compare them by hand")
            return

        tables = set(self.connection.introspection.table_names())
        changes = 0
        for model in self._our_models():
            table = model._meta.db_table
            if table not in tables:
                continue
            expected = self._expected_indexes(model)
            live = self._live_indexes(table)
            live_by_definition = {}
            for name, definition in live.items():
                live_by_definition.setdefault(definition, []).append(name)

            for name, (sql, definition) in sorted(expected.items()):
                if name in live:
                    continue
                spare = [other for other in live_by_definition.get(definition, []) if other not in expected]
                quote = self.connection.ops.quote_name
                if spare:
                    statement = f"ALTER INDEX {quote(spare[0])} RENAME TO {quote(name)}"
                    live_by_definition[definition].remove(spare[0])
                else:
                    statement = sql.replace("CREATE INDEX", "CREATE INDEX CONCURRENTLY IF NOT EXISTS", 1)
                    statement = statement.replace(
                        "CREATE UNIQUE INDEX", "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS", 1,
                    )
                self.stdout.write(f"  {table}: {statement}")
                changes += 1
                if self.execute:
                    # Outside a transaction: CONCURRENTLY refuses to run inside one.
                    with self.connection.cursor() as cursor:
                        cursor.execute(statement)
        if not changes:
            self.stdout.write("  every declared index is in place")

    def _expected_indexes(self, model):
        """``{name: (sql, definition)}`` for every index the model declares."""
        with self.connection.schema_editor(collect_sql=True, atomic=False) as editor:
            editor.create_model(model)
        expected = {}
        for statement in editor.collected_sql:
            match = _INDEX_SQL.match(str(statement))
            if match and match["table"] == model._meta.db_table:
                expected[match["name"]] = (
                    str(statement).rstrip(";"),
                    _definition(match["unique"], match["spec"]),
                )
        return expected

    def _live_indexes(self, table):
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema() AND tablename = %s",
                [table],
            )
            rows = cursor.fetchall()
        live = {}
        for name, definition in rows:
            match = _LIVE_INDEX.match(definition)
            if match:
                live[name] = _definition(match["unique"], match["spec"])
        return live

    # -- helpers --------------------------------------------------------------

    def _our_models(self):
        return list(apps.get_app_config(APP_LABEL).get_models())

    def _columns(self, table):
        with self.connection.cursor() as cursor:
            return {
                column.name
                for column in self.connection.introspection.get_table_description(cursor, table)
            }
