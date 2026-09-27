"""``seed_parameters`` — create the parameters a project declares (PRD §7).

Parameter names are vocabulary (§2), so this command holds none: it reads a
catalogue the project points to with
``DJANGO_COMMON_KIT["PARAMETERS"]["SEED_CATALOG"]``, a dotted path to an iterable
of dicts::

    PARAMETERS = [
        {"key": "MAX_JOBS_PER_DAY", "parameter_type": "integer", "value": 12,
         "category": "scheduling", "description": "...", "is_system": True},
    ]

Existing rows are left alone unless ``--update`` is passed: a value someone
changed in the admin is not a value to overwrite on the next deploy.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from django_common_kit.conf import app_settings


class Command(BaseCommand):
    help = "Create the parameters named in DJANGO_COMMON_KIT['PARAMETERS']['SEED_CATALOG']."

    def add_arguments(self, parser):
        parser.add_argument(
            "--update", action="store_true",
            help="Overwrite the value and metadata of rows that already exist.",
        )

    def handle(self, *args, **options):
        from django_common_kit.models import ParameterModel
        from django_common_kit.parameters.cache import ParameterCache

        catalogue = app_settings.hook("PARAMETERS", "SEED_CATALOG")
        if catalogue is None:
            raise CommandError(
                "DJANGO_COMMON_KIT['PARAMETERS']['SEED_CATALOG'] is not set; "
                "nothing to seed."
            )
        entries = list(catalogue() if callable(catalogue) else catalogue)

        created = updated = skipped = 0
        with transaction.atomic():
            for entry in entries:
                entry = dict(entry)
                key = entry.pop("key")
                value = entry.pop("value", None)
                row, was_created = ParameterModel.objects.get_or_create(key=key, defaults=entry)
                if was_created:
                    row.set_value(value)
                    row.save()
                    created += 1
                elif options["update"]:
                    for name, item in entry.items():
                        setattr(row, name, item)
                    row.set_value(value)
                    row.save()
                    updated += 1
                else:
                    skipped += 1

        ParameterCache.invalidate_cache()
        self.stdout.write(
            self.style.SUCCESS(
                f"Parameters: {created} created, {updated} updated, {skipped} left as they were."
            )
        )
