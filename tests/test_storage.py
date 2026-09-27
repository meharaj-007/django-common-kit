"""Storage selection and path building (PRD §9.3)."""

from django.core.files.storage import FileSystemStorage
from django.test import SimpleTestCase, override_settings

from django_common_utils.storage import (
    MediaStorage,
    OWNERLESS_ROOT,
    safe_basename,
    scoped_path,
)


class ScopedPathTests(SimpleTestCase):
    def test_owner_is_the_first_segment(self):
        self.assertEqual(
            scoped_path("abc-123", "invoices", "2026/09"),
            "abc-123/invoices/2026/09",
        )

    def test_no_owner_goes_to_system_and_is_never_guessed(self):
        self.assertEqual(scoped_path(None, "invoices"), f"{OWNERLESS_ROOT}/invoices")

    def test_empty_segments_do_not_double_the_separator(self):
        self.assertEqual(scoped_path("abc", "", "files"), "abc/files")

    def test_safe_basename_strips_a_client_supplied_path(self):
        self.assertEqual(safe_basename("../../etc/passwd", "file"), "passwd")
        self.assertEqual(safe_basename("", "file"), "file")


class MediaStorageTests(SimpleTestCase):
    def test_defaults_to_local_disk(self):
        self.assertIsInstance(MediaStorage().backend, FileSystemStorage)

    def test_deconstruct_bakes_nothing_into_a_migration(self):
        path, args, kwargs = MediaStorage().deconstruct()
        self.assertEqual(path, "django_common_utils.storage.MediaStorage")
        self.assertEqual((args, kwargs), ([], {}))

    @override_settings(DJANGO_COMMON_UTILS={"STORAGE": {"TYPE": "s3"}})
    def test_s3_without_the_extra_names_the_extra(self):
        storage = MediaStorage()
        try:
            storage.backend
        except ImportError as exc:
            self.assertIn("django-common-utils[s3]", str(exc))
        else:
            self.skipTest("django-storages is installed; nothing to assert")

    def test_backend_is_resolved_on_use_not_at_import(self):
        """A module-level `if` on the setting would make override_settings a no-op."""
        storage = MediaStorage()
        self.assertIsNone(storage._backend)
        storage.exists("nothing")
        self.assertIsNotNone(storage._backend)
