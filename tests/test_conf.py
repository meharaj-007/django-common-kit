"""Settings resolution (PRD §4)."""

from django.core.signals import setting_changed
from django.test import SimpleTestCase, override_settings

from django_common_utils.conf import app_settings


class SettingsCacheTests(SimpleTestCase):
    def test_override_settings_is_honoured(self):
        with override_settings(DJANGO_COMMON_UTILS={"PAGINATION": {"PAGE_SIZE": 7}}):
            self.assertEqual(app_settings.get("PAGINATION", "PAGE_SIZE"), 7)
        self.assertNotEqual(app_settings.get("PAGINATION", "PAGE_SIZE"), 7)

    def test_the_cache_reset_is_connected_by_importing_conf(self):
        """Not only by ``AppConfig.ready()``.

        A project adopting the package rewrites its imports before it adds the
        app to INSTALLED_APPS (§13). In that window ``ready()`` never runs, and
        if the reset rode on it alone every ``override_settings`` in that
        project's suite would be served a stale value — a test passing against
        the default while claiming to prove the override.
        """
        keys = [entry[0] for entry in setting_changed.receivers]
        uids = [key[0] for key in keys]
        self.assertIn("django_common_utils.conf", uids)
