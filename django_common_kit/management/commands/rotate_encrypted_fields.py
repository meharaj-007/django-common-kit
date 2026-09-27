"""Re-encrypt every stored secret with the first key (PRD §17.7).

The rotation procedure:

1. Deploy with ``KEYS = [new, old]``. New writes use ``new``; everything reads.
2. Run this command. Every ``EncryptedTextField`` of every installed model —
   the project's and every package's — is re-encrypted where it is not
   already on the first key.
3. When it reports no failures, deploy with ``KEYS = [new]``.

Rows are written with a queryset ``update()``, one per row: no save signals, no
history entry, no ``updated_at``. The update is a compare-and-swap on the token
that was read, so a row the app changed meanwhile is left alone and counted as
current. Each batch is its own transaction, so the command can be stopped and
run again; a second run finds nothing to do.

Soft-deleted rows are included: a key dropped at step 3 must not strand them.
A row no key opens is named by pk, and the command exits non-zero — step 3 must
wait until there are none.
"""

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from django_common_kit.crypto.fields import StoredToken, encrypted_fields
from django_common_kit.crypto.keys import (
    DecryptionError,
    EncryptionNotConfigured,
    encrypt,
    is_current,
    looks_like_token,
    rotate_token,
)


class Command(BaseCommand):
    help = "Re-encrypt every EncryptedTextField value with the first key in DJANGO_COMMON_KIT['ENCRYPTION']['KEYS']."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Count what would change; write nothing.",
        )
        parser.add_argument(
            "--batch-size", type=int, default=500,
            help="Rows per transaction (default 500).",
        )
        parser.add_argument(
            "--app", action="append", default=[], metavar="APP_LABEL",
            help="Only this app's models. Repeatable.",
        )
        parser.add_argument(
            "--model", action="append", default=[], metavar="APP_LABEL.MODEL",
            help="Only this model. Repeatable.",
        )
        parser.add_argument(
            "--include-plaintext", action="store_true",
            help="Encrypt values that are not tokens in fields with legacy_plaintext=True.",
        )
        parser.add_argument("--database", default="default")

    def handle(self, *args, **options):
        if options["batch_size"] < 1:
            raise CommandError("--batch-size must be at least 1.")
        try:
            encrypt("check")
        except EncryptionNotConfigured as exc:
            raise CommandError(str(exc)) from None

        targets = self._targets(options["app"], options["model"])
        if not targets:
            self.stdout.write("No encrypted fields installed.")
            return

        failures = 0
        for model, field in targets:
            counts, failed = self._rotate_field(model, field, options)
            failures += len(failed)
            summary = ", ".join(f"{name} {count}" for name, count in counts.items())
            self.stdout.write(f"{model._meta.label}.{field.name}: {summary}")
            for pk in failed:
                self.stderr.write(f"  unreadable: {model._meta.label} pk={pk}")

        if options["dry_run"]:
            self.stdout.write("Dry run: nothing was written.")
        if failures:
            raise CommandError(
                f"{failures} value(s) could not be opened with any configured key. "
                "Do not drop a key until this is zero."
            )

    # -----------------------------------------------------------------------
    def _targets(self, app_labels, model_labels):
        wanted_models = {label.lower() for label in model_labels}
        wanted_apps = set(app_labels)
        known = {config.label for config in apps.get_app_configs()}
        unknown = wanted_apps - known
        if unknown:
            raise CommandError(f"No installed app with label {', '.join(sorted(unknown))}.")

        targets = []
        seen_models = set()
        for model in apps.get_models():
            if model._meta.proxy or not model._meta.managed:
                continue
            label = model._meta.label_lower
            seen_models.add(label)
            if wanted_apps and model._meta.app_label not in wanted_apps:
                continue
            if wanted_models and label not in wanted_models:
                continue
            for field in encrypted_fields(model):
                targets.append((model, field))

        missing = wanted_models - seen_models
        if missing:
            raise CommandError(f"No installed model {', '.join(sorted(missing))}.")
        return targets

    def _rotate_field(self, model, field, options):
        counts = {"rotated": 0, "current": 0, "empty": 0, "plaintext skipped": 0, "failed": 0}
        failed = []
        database = options["database"]
        manager = model._base_manager.db_manager(database)

        counts["empty"] = (
            manager.filter(**{f"{field.attname}__isnull": True}).count()
            + manager.filter(**{field.attname: ""}).count()
        )
        pending = (
            manager.exclude(**{f"{field.attname}__isnull": True})
            .exclude(**{field.attname: ""})
            .order_by("pk")
        )

        last_pk = None
        while True:
            batch_qs = pending if last_pk is None else pending.filter(pk__gt=last_pk)
            batch = list(batch_qs.values_list("pk", field.attname)[: options["batch_size"]])
            if not batch:
                break
            last_pk = batch[-1][0]
            with transaction.atomic(using=database):
                for pk, stored in batch:
                    outcome, token = self._rotate_value(field, str(stored), options)
                    if token is not None and not options["dry_run"]:
                        written = manager.filter(
                            pk=pk, **{field.attname: StoredToken(stored)}
                        ).update(**{field.attname: StoredToken(token)})
                        if not written:
                            outcome = "current"  # changed by the app since the read
                    if outcome == "failed":
                        failed.append(pk)
                    counts[outcome] += 1
        return counts, failed

    def _rotate_value(self, field, stored, options):
        """``(count name, new token or None)`` for one stored value."""
        if field.legacy_plaintext and not looks_like_token(stored):
            if not options["include_plaintext"]:
                return "plaintext skipped", None
            return "rotated", encrypt(stored)
        try:
            if is_current(stored):
                return "current", None
            return "rotated", rotate_token(stored)
        except DecryptionError:
            return "failed", None
