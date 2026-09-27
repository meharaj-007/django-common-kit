from django.apps import AppConfig
from django.core.signals import setting_changed

# Friendlier admin sidebar names for third-party apps. These ship their own
# AppConfig, so unlike our apps (which set verbose_name directly) they can only
# be relabelled here, once the app registry is populated. Display-only — this
# changes no labels, tables, or migrations.
THIRD_PARTY_ADMIN_NAMES = {
    "auth": "Groups & Permissions",   # User is swapped out; only Group shows here
    "authtoken": "API Tokens",
    "token_blacklist": "JWT Tokens",
    "django_celery_beat": "Scheduled Tasks",
}


class CommonUtilsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_common_utils"
    # NOT "common". Every consuming project already has an app labelled
    # ``common`` with migrations 0001..00NN recorded against that label, and a
    # package migration numbered into the same label would be read as already
    # applied and silently skipped. The project keeps its ``common`` app for
    # what stays project-side; this one sits beside it.
    label = "common_control"
    verbose_name = "System & Logs"

    def ready(self):
        from django_common_utils.conf import app_settings

        # override_settings in tests must not be served a stale value.
        setting_changed.connect(app_settings.reset, dispatch_uid="django_common_utils.conf")

        from django_common_utils.api.response import reset_local_package_cache

        setting_changed.connect(
            reset_local_package_cache, dispatch_uid="django_common_utils.api.response"
        )

        from django_common_utils import signals  # noqa: F401

        from django.core import checks

        from django_common_utils.crypto.checks import check_encryption

        checks.register(check_encryption)

        if app_settings.RENAME_THIRD_PARTY_ADMIN_APPS:
            self._rename_third_party_apps()

        if app_settings.get("PARAMETERS", "AUTO_LOAD_ON_STARTUP"):
            self._warm_parameter_cache()

    @staticmethod
    def _warm_parameter_cache():
        """Load the parameter table into the cache on a daemon thread.

        On a thread so that startup does not block on the database, and so a
        database that is not there yet — a fresh container racing its Postgres
        sidecar, `migrate` on an empty schema — logs and moves on rather than
        crashing the process. The first `get_parameter` reloads on a miss anyway.
        """
        import logging
        import threading

        def load():
            try:
                from django_common_utils.parameters.cache import ParameterCache

                ParameterCache.load_parameters_to_cache()
            except Exception as exc:  # noqa: BLE001 - startup must not depend on this
                logging.getLogger(__name__).warning(
                    "Parameter cache not warmed at startup: %s", exc
                )

        threading.Thread(target=load, name="django-common-utils-parameters", daemon=True).start()

    @staticmethod
    def _rename_third_party_apps():
        """Apply friendlier admin sidebar names to third-party apps."""
        from django.apps import apps

        for label, verbose_name in THIRD_PARTY_ADMIN_NAMES.items():
            try:
                apps.get_app_config(label).verbose_name = verbose_name
            except LookupError:
                # App not installed (e.g. an optional dependency) — skip it.
                continue
