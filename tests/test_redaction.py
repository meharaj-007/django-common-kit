"""Credential redaction (PRD §8)."""

import json

from django.test import SimpleTestCase, override_settings

from django_common_kit.tracking.redaction import (
    MASK,
    redact_text,
    scrub_query_string,
    scrub_url,
    truncate,
)


class RedactionTests(SimpleTestCase):
    def test_json_body_keys_are_masked_recursively(self):
        body = json.dumps({"email": "a@b.com", "password": "hunter2", "nested": {"token": "abc"}})
        out = json.loads(redact_text(body, "/api/login/"))
        self.assertEqual(out["email"], "a@b.com")
        self.assertEqual(out["password"], MASK)
        self.assertEqual(out["nested"]["token"], MASK)

    def test_form_encoded_body_is_masked_by_regex(self):
        out = redact_text("email=a%40b.com&password=hunter2&next=/", "/login/")
        self.assertIn(f"password={MASK}", out)
        self.assertIn("email=a%40b.com", out)

    def test_multipart_field_is_masked(self):
        body = 'Content-Disposition: form-data; name="password"\r\n\r\nhunter2\r\n--boundary'
        self.assertIn(MASK, redact_text(body, "/upload/"))
        self.assertNotIn("hunter2", redact_text(body, "/upload/"))

    def test_code_is_a_credential_only_under_auth_paths(self):
        """An OAuth code under /auth/, a discount code everywhere else."""
        body = json.dumps({"code": "SAVE10"})
        self.assertEqual(json.loads(redact_text(body, "/api/checkout/"))["code"], "SAVE10")
        self.assertEqual(json.loads(redact_text(body, "/api/auth/callback/"))["code"], MASK)

    def test_query_string_keeps_order_and_masks_signed_url_keys(self):
        out = scrub_query_string("page=2&signature=abc&token=xyz", "/media/")
        self.assertEqual(out, f"page=2&signature={MASK}&token={MASK}")

    def test_scrub_url(self):
        self.assertEqual(scrub_url("https://x/y?a=1&sig=s"), f"https://x/y?a=1&sig={MASK}")

    @override_settings(DJANGO_COMMON_KIT={"TRACKING": {"REDACT_KEYS": ["tfn"]}})
    def test_project_keys_are_added_to_the_floor(self):
        out = json.loads(redact_text(json.dumps({"tfn": "123", "password": "x"}), "/"))
        self.assertEqual(out["tfn"], MASK)
        self.assertEqual(out["password"], MASK)

    def test_truncate_marks_the_cut(self):
        self.assertEqual(truncate("abcdef", 3), "abc... [truncated]")
        self.assertEqual(truncate("abc", 3), "abc")
