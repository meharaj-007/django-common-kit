"""CommonFileModel (PRD §9) and short links (PRD §10)."""

import tempfile

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone

from django_common_kit.models import CommonFileModel, ShortLinkModel
from django_common_kit.shortlinks import shorten

MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA)
class CommonFileTests(TestCase):
    def _make(self, name="a.txt", tag="doc"):
        row = CommonFileModel(tag=tag, original_filename=name)
        row.file.save(name, ContentFile(b"hello"), save=True)
        return row

    def test_upload_path_is_tagged_and_dated(self):
        row = self._make()
        self.assertRegex(row.file.name, r"^common/files/doc/\d{4}/\d{2}/a(_\w+)?\.txt$")

    @override_settings(DJANGO_COMMON_KIT={"TENANT": {"INSTANCE_ATTRIBUTE": "parent_id"}})
    def test_with_tenants_the_path_starts_with_the_tenant(self):
        from django.contrib.contenttypes.models import ContentType

        from tests.testapp.models import Widget

        parent = Widget.objects.create(name="parent")
        child = Widget.objects.create(name="child", parent=parent)
        row = CommonFileModel(
            tag="doc", content_type=ContentType.objects.get_for_model(Widget), object_id=str(child.pk),
        )
        row.file.save("a.txt", ContentFile(b"hello"), save=True)
        self.assertTrue(row.file.name.startswith(f"{parent.pk}/common/files/doc/"), row.file.name)
        self.assertEqual(row.tenant_id, parent.pk)

    @override_settings(DJANGO_COMMON_KIT={"TENANT": {"INSTANCE_ATTRIBUTE": "parent_id"}})
    def test_with_tenants_a_file_with_none_goes_to_system(self):
        self.assertTrue(self._make().file.name.startswith("system/common/files/doc/"))

    def test_client_path_is_stripped_to_the_basename(self):
        row = self._make(name="../../etc/passwd")
        self.assertNotIn("..", row.file.name)
        self.assertTrue(row.file.name.endswith("passwd") or "passwd_" in row.file.name)

    def test_row_delete_removes_the_blob(self):
        row = self._make()
        storage, name = row.file.storage, row.file.name
        self.assertTrue(storage.exists(name))
        row.delete()
        self.assertFalse(storage.exists(name))

    def test_bulk_delete_removes_every_blob(self):
        """Django's QuerySet.delete() never calls Model.delete()."""
        rows = [self._make(name=f"{i}.txt") for i in range(3)]
        names = [(r.file.storage, r.file.name) for r in rows]
        CommonFileModel.objects.all().delete()
        for storage, name in names:
            self.assertFalse(storage.exists(name))


@override_settings(DJANGO_COMMON_KIT={"SHORT_LINK": {"BASE_URL": "https://sms.example"}})
class ShortLinkTests(TestCase):
    def test_shorten_returns_a_short_url_and_reuses_it(self):
        first = shorten("https://long.example/quote/abc?sig=1", purpose="quote")
        self.assertRegex(first, r"^https://sms\.example/s/[0-9A-Za-z]{7}/$")
        self.assertEqual(shorten("https://long.example/quote/abc?sig=1", purpose="quote"), first)
        self.assertEqual(ShortLinkModel.objects.count(), 1)

    @override_settings(DJANGO_COMMON_KIT={"SHORT_LINK": {"BASE_URL": ""}})
    def test_without_a_base_url_the_long_url_is_returned(self):
        target = "https://long.example/x"
        self.assertEqual(shorten(target), target)
        self.assertEqual(ShortLinkModel.objects.count(), 0)

    @override_settings(ROOT_URLCONF="tests.urls")
    def test_redirect_counts_clicks_and_honours_expiry(self):
        link = ShortLinkModel.objects.create(slug="abc1234", target_url="https://long.example/x")
        response = self.client.get("/s/abc1234/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://long.example/x")
        link.refresh_from_db()
        self.assertEqual(link.click_count, 1)

        link.expires_at = timezone.now() - timezone.timedelta(minutes=1)
        link.save()
        self.assertEqual(self.client.get("/s/abc1234/").status_code, 410)
        self.assertEqual(self.client.get("/s/nope/").status_code, 404)


class UploadLimitTests(TestCase):
    """FILES["MAX_UPLOAD_BYTES"] and FILES["ALLOWED_MIME_TYPES"] (PRD §9.4)."""

    def _upload(self, name="a.pdf", size=10, content_type="application/pdf"):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(name, b"x" * size, content_type=content_type)

    @override_settings(DJANGO_COMMON_KIT={"FILES": {"MAX_UPLOAD_BYTES": 5}})
    def test_a_file_over_the_limit_is_refused(self):
        from django.core.exceptions import ValidationError

        from django_common_kit.files import validate_upload

        with self.assertRaises(ValidationError) as caught:
            validate_upload(self._upload(size=6))
        self.assertEqual(caught.exception.code, "file_too_large")
        validate_upload(self._upload(size=5))

    @override_settings(DJANGO_COMMON_KIT={"FILES": {"MAX_UPLOAD_BYTES": 0}})
    def test_no_limit_when_the_limit_is_zero(self):
        from django_common_kit.files import validate_upload

        validate_upload(self._upload(size=10_000))

    @override_settings(DJANGO_COMMON_KIT={"FILES": {"ALLOWED_MIME_TYPES": ["application/pdf", "image/*"]}})
    def test_only_listed_types_are_accepted(self):
        from django.core.exceptions import ValidationError

        from django_common_kit.files import validate_upload

        validate_upload(self._upload())
        validate_upload(self._upload("p.png", content_type="image/png"))
        with self.assertRaises(ValidationError) as caught:
            validate_upload(self._upload("x.exe", content_type="application/x-msdownload"))
        self.assertEqual(caught.exception.code, "file_type_not_allowed")

    @override_settings(DJANGO_COMMON_KIT={"FILES": {"ALLOWED_MIME_TYPES": ["application/pdf"]}})
    def test_without_a_declared_type_the_name_decides(self):
        from django.core.files.base import ContentFile

        from django_common_kit.files import upload_mime_type, validate_upload

        self.assertEqual(upload_mime_type(ContentFile(b"x", name="a.pdf")), "application/pdf")
        validate_upload(ContentFile(b"x", name="a.pdf"))

    @override_settings(MEDIA_ROOT=MEDIA, DJANGO_COMMON_KIT={"FILES": {"MAX_UPLOAD_BYTES": 5}})
    def test_the_model_checks_a_new_upload_on_clean(self):
        from django.core.exceptions import ValidationError

        row = CommonFileModel(tag="doc", file=self._upload(size=6))
        with self.assertRaises(ValidationError) as caught:
            row.full_clean()
        self.assertIn("file", caught.exception.message_dict)

    @override_settings(MEDIA_ROOT=MEDIA)
    def test_a_stored_file_is_not_rechecked_when_the_limit_drops(self):
        """It was accepted under the limit in force when it arrived."""
        row = CommonFileModel(tag="doc")
        row.file.save("a.txt", ContentFile(b"hello world"), save=True)
        row = CommonFileModel.objects.get(pk=row.pk)
        with override_settings(MEDIA_ROOT=MEDIA, DJANGO_COMMON_KIT={"FILES": {"MAX_UPLOAD_BYTES": 5}}):
            row.title = "renamed"
            row.full_clean()
