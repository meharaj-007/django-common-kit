"""Encryption at rest (PRD §17).

Everything that needs ``cryptography`` skips where it is not installed — the
legs that install no extras — so those legs still prove the package imports
and the rest of the suite passes without it.
"""

import importlib.util
import io
import json
import pickle
import unittest
from unittest import mock

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core import checks
from django.core.exceptions import FieldError
from django.core.management import CommandError, call_command
from django.forms import modelform_factory
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from rest_framework import serializers

from django_common_utils.admin import BaseModelAdmin
from django_common_utils.crypto import (
    DecryptionError,
    EncryptedTextField,
    EncryptionNotConfigured,
    SecretField,
    SecretFieldsMixin,
    decrypt,
    encrypt,
    is_configured,
    mask,
    rotate_token,
)
from django_common_utils.crypto.checks import check_encryption
from django_common_utils.history import get_model_history
from django_common_utils.models import RequestLog
from django_common_utils.tracking.redaction import redact_text
from tests.testapp.models import Vault

HAS_CRYPTOGRAPHY = importlib.util.find_spec("cryptography") is not None
needs_cryptography = unittest.skipUnless(HAS_CRYPTOGRAPHY, "cryptography is not installed")

# Test fixtures, generated for this file. KEY_A is the one tests/settings.py sets.
KEY_A = "NtShbux2yPWuYuOMN31mtF4itctD6NXO4FLk5TIweXE="
KEY_B = "ILznFs0eyumPUI2UuLBAWZ6ST-845pBH-WYTIs6OqsQ="
KEY_C = "QYOQ-FuBOv7SmPxHng_-GCKDRK5PQu1Yeq6Wol0x-6E="


def keys(*values):
    return override_settings(DJANGO_COMMON_UTILS={
        "PARAMETERS": {"AUTO_LOAD_ON_STARTUP": False},
        "ENCRYPTION": {"KEYS": list(values)},
    })


def raw_column(vault, name="secret"):
    """What the database holds, bypassing the field."""
    return Vault.objects.filter(pk=vault.pk).values_list(name, flat=True).get()


class MaskTests(SimpleTestCase):
    def test_shows_the_last_four(self):
        self.assertEqual(mask("sk_live_abcd1234"), "••••1234")

    def test_short_values_are_masked_entirely(self):
        self.assertEqual(mask("12345678"), "••••")
        self.assertEqual(mask("x"), "••••")

    def test_empty_is_empty(self):
        self.assertEqual(mask(""), "")
        self.assertEqual(mask(None), "")

    def test_keep(self):
        self.assertEqual(mask("sk_live_abcd1234", keep=2), "••••34")
        self.assertEqual(mask("sk_live_abcd1234", keep=0), "••••")

    def test_never_raises(self):
        class Broken:
            def __str__(self):
                raise RuntimeError("no")

        self.assertEqual(mask(Broken()), "••••")


class ConfigurationTests(SimpleTestCase):
    @keys()
    def test_no_keys_is_not_configured(self):
        self.assertFalse(is_configured())
        with self.assertRaisesMessage(EncryptionNotConfigured, 'DJANGO_COMMON_UTILS["ENCRYPTION"]["KEYS"]'):
            encrypt("x")

    @keys("not-a-key")
    def test_an_invalid_key_is_named_by_position_not_value(self):
        with self.assertRaises(EncryptionNotConfigured) as caught:
            encrypt("x")
        self.assertIn("[0]", str(caught.exception))
        self.assertNotIn("not-a-key", str(caught.exception))

    @keys()
    def test_empty_values_pass_through_without_keys(self):
        self.assertEqual(encrypt(""), "")
        self.assertIsNone(encrypt(None))
        self.assertEqual(decrypt(""), "")
        self.assertIsNone(decrypt(None))

    def test_without_cryptography_every_function_says_which_extra(self):
        with mock.patch.dict("sys.modules", {"cryptography": None, "cryptography.fernet": None}):
            from django_common_utils.crypto import keys as module

            module._cache.clear()
            with self.assertRaisesMessage(EncryptionNotConfigured, "django-common-utils[crypto]"):
                encrypt("x")
            self.assertFalse(is_configured())
        module._cache.clear()

    def test_the_package_imports_without_cryptography(self):
        # In a fresh interpreter: unloading modules in this one would leave
        # stale references behind for every later test.
        import subprocess
        import sys

        code = (
            "import sys; sys.modules['cryptography'] = None; "
            "import django; from django.conf import settings; settings.configure(); "
            "import django_common_utils.crypto as c; "
            "assert c.EncryptedTextField and c.encrypt and c.mask('abcdefghijkl') == '••••ijkl'"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


@needs_cryptography
class FunctionTests(SimpleTestCase):
    def test_round_trip(self):
        token = encrypt("sk_live_abcd1234")
        self.assertNotIn("abcd", token)
        self.assertEqual(decrypt(token), "sk_live_abcd1234")

    def test_unicode_round_trip(self):
        self.assertEqual(decrypt(encrypt("pässwörd ✓")), "pässwörd ✓")

    def test_tokens_are_randomised(self):
        self.assertNotEqual(encrypt("same"), encrypt("same"))

    def test_a_single_string_is_a_list_of_one(self):
        with keys(KEY_A):
            token = encrypt("x")
        with override_settings(DJANGO_COMMON_UTILS={"ENCRYPTION": {"KEYS": KEY_A}}):
            self.assertEqual(decrypt(token), "x")

    def test_a_token_no_key_opens_raises_without_the_token_in_the_message(self):
        with keys(KEY_B):
            token = encrypt("x")
        with self.assertRaises(DecryptionError) as caught:
            decrypt(token)
        self.assertNotIn(token, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_garbage_raises_decryption_error(self):
        for value in ("not a token", "ünïcode"):
            with self.subTest(value=value), self.assertRaises(DecryptionError):
                decrypt(value)

    def test_every_key_decrypts_and_the_first_encrypts(self):
        with keys(KEY_B):
            old = encrypt("x")
        with keys(KEY_A, KEY_B):
            self.assertEqual(decrypt(old), "x")
            new = encrypt("y")
        with keys(KEY_A):
            self.assertEqual(decrypt(new), "y")

    def test_rotate_token(self):
        with keys(KEY_B):
            old = encrypt("x")
        with keys(KEY_A, KEY_B):
            rotated = rotate_token(old)
        with keys(KEY_A):
            self.assertEqual(decrypt(rotated), "x")

    def test_tokens_from_other_fernet_helpers_decrypt(self):
        """A column written by any of the existing copies (§17.1) reads back
        once its key is in KEYS: they all write plain Fernet tokens."""
        from cryptography.fernet import Fernet, MultiFernet

        written_elsewhere = [
            # One key, str in and str out (the credentials helper shape).
            Fernet(KEY_B.encode()).encrypt("tok".encode("utf-8")).decode("utf-8"),
            # A MultiFernet over a key list (the LLM key helper shape).
            MultiFernet([Fernet(KEY_B.encode())]).encrypt(b"tok").decode(),
        ]
        with keys(KEY_A, KEY_B):
            for token in written_elsewhere:
                self.assertEqual(decrypt(token), "tok")


@needs_cryptography
class FieldTests(TestCase):
    def test_the_column_holds_a_token_and_the_instance_the_plaintext(self):
        vault = Vault.objects.create(secret="sk_live_abcd1234")
        stored = raw_column(vault)
        self.assertNotIn("abcd", stored)
        self.assertEqual(decrypt(stored), "sk_live_abcd1234")
        self.assertEqual(Vault.objects.get(pk=vault.pk).secret, "sk_live_abcd1234")
        self.assertEqual(vault.secret, "sk_live_abcd1234")

    def test_empty_and_null_are_stored_as_themselves(self):
        vault = Vault.objects.create()
        self.assertEqual(raw_column(vault), "")
        self.assertIsNone(raw_column(vault, "legacy"))
        self.assertTrue(Vault.objects.filter(legacy__isnull=True, secret="").exists())

    def test_an_untouched_value_is_saved_as_the_same_token(self):
        vault = Vault.objects.create(secret="s3cret-value")
        token = raw_column(vault)
        loaded = Vault.objects.get(pk=vault.pk)
        loaded.name = "renamed"
        loaded.save()
        self.assertEqual(raw_column(vault), token)
        loaded.secret  # read, not changed
        loaded.save()
        self.assertEqual(raw_column(vault), token)

    def test_full_clean_and_refresh_leave_the_token_alone(self):
        vault = Vault.objects.create(secret="s3cret-value")
        token = raw_column(vault)
        loaded = Vault.objects.get(pk=vault.pk)
        loaded.full_clean()
        loaded.refresh_from_db()
        loaded.save()
        self.assertEqual(raw_column(vault), token)

    def test_a_new_value_is_encrypted_on_save(self):
        vault = Vault.objects.create(secret="first-value")
        vault.secret = "second-value"
        vault.save()
        self.assertEqual(decrypt(raw_column(vault)), "second-value")
        self.assertEqual(Vault.objects.get(pk=vault.pk).secret, "second-value")

    def test_queryset_update_and_bulk_update_encrypt(self):
        vault = Vault.objects.create(secret="first-value")
        Vault.objects.filter(pk=vault.pk).update(secret="updated-value")
        self.assertEqual(decrypt(raw_column(vault)), "updated-value")
        vault.refresh_from_db()
        vault.secret = "bulk-value"
        Vault.objects.bulk_update([vault], ["secret"])
        self.assertEqual(decrypt(raw_column(vault)), "bulk-value")

    def test_bulk_create_encrypts(self):
        vault, = Vault.objects.bulk_create([Vault(secret="bulk-created")])
        self.assertEqual(decrypt(raw_column(vault)), "bulk-created")

    def test_loading_never_decrypts(self):
        with keys(KEY_B):
            vault = Vault.objects.create(secret="made-with-another-key")
        with mock.patch("django_common_utils.crypto.fields.decrypt") as spy:
            list(Vault.objects.all())
            Vault.objects.get(pk=vault.pk)
            Vault.objects.values_list("secret", flat=True).get(pk=vault.pk)
        spy.assert_not_called()

    def test_an_unreadable_row_loads_and_raises_only_when_read(self):
        with keys(KEY_B):
            vault = Vault.objects.create(name="orphan", secret="made-with-another-key")
        loaded = Vault.objects.get(pk=vault.pk)
        self.assertEqual(loaded.name, "orphan")
        with self.assertRaises(DecryptionError):
            loaded.secret
        loaded.name = "still saves"
        loaded.save()

    def test_deferred_load(self):
        vault = Vault.objects.create(secret="deferred-value")
        loaded = Vault.objects.only("name").get(pk=vault.pk)
        self.assertEqual(loaded.secret, "deferred-value")

    def test_only_isnull_and_exact_empty_are_lookups(self):
        Vault.objects.filter(secret="").count()
        Vault.objects.filter(secret__isnull=True).count()
        Vault.objects.filter(secret=None).count()
        Vault.objects.exclude(secret="").count()
        for lookup in ({"secret": "x"}, {"secret__icontains": "x"}, {"secret__in": ["x"]},
                       {"secret__startswith": "x"}, {"secret__lower": "x"}):
            with self.subTest(lookup=lookup), self.assertRaises(FieldError):
                Vault.objects.filter(**lookup).count()

    def test_max_length_is_refused(self):
        with self.assertRaises(TypeError):
            EncryptedTextField(max_length=100)

    def test_deconstructs_with_the_public_path(self):
        _name, path, _args, kwargs = Vault._meta.get_field("legacy").deconstruct()
        self.assertEqual(path, "django_common_utils.crypto.EncryptedTextField")
        self.assertTrue(kwargs["legacy_plaintext"])
        self.assertNotIn("legacy_plaintext", Vault._meta.get_field("secret").deconstruct()[3])

    def test_pickling_keeps_the_token_and_drops_the_plaintext(self):
        vault = Vault.objects.create(secret="pickled-value")
        loaded = Vault.objects.get(pk=vault.pk)
        loaded.secret  # decrypt, so a plaintext is cached
        data = pickle.dumps(loaded)
        self.assertNotIn(b"pickled-value", data)
        restored = pickle.loads(data)
        self.assertEqual(restored.secret, "pickled-value")
        restored.save()
        self.assertEqual(raw_column(vault), raw_column(loaded))

    def test_copying_a_secret_between_rows(self):
        source = Vault.objects.get(pk=Vault.objects.create(secret="shared-value").pk)
        target = Vault.objects.create()
        target.secret = source.secret
        target.save()
        self.assertEqual(Vault.objects.get(pk=target.pk).secret, "shared-value")


@needs_cryptography
class LegacyPlaintextTests(TestCase):
    def setUp(self):
        self.vault = Vault.objects.create()
        # A column that held plaintext before it was encrypted.
        Vault.objects.filter(pk=self.vault.pk).update(legacy=_raw("plain-old-secret"))

    def test_plaintext_reads_back_as_is_and_is_logged_once(self):
        from django_common_utils.crypto import fields

        fields._legacy_logged.clear()
        with self.assertLogs("django_common_utils.crypto.fields", "WARNING") as logs:
            self.assertEqual(Vault.objects.get(pk=self.vault.pk).legacy, "plain-old-secret")
            self.assertEqual(Vault.objects.get(pk=self.vault.pk).legacy, "plain-old-secret")
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn("plain-old-secret", logs.output[0])

    def test_a_token_in_a_legacy_field_decrypts(self):
        vault = Vault.objects.create(legacy="now-encrypted")
        self.assertEqual(decrypt(raw_column(vault, "legacy")), "now-encrypted")
        self.assertEqual(Vault.objects.get(pk=vault.pk).legacy, "now-encrypted")

    def test_without_the_option_plaintext_is_a_decryption_error(self):
        Vault.objects.filter(pk=self.vault.pk).update(secret=_raw("plain"))
        with self.assertRaises(DecryptionError):
            Vault.objects.get(pk=self.vault.pk).secret


def _raw(value):
    """A value written to the column as-is, as the rotation command does."""
    from django_common_utils.crypto.fields import StoredToken

    return StoredToken(value)


@needs_cryptography
class HistoryTests(TestCase):
    def test_snapshots_never_hold_the_secret_or_the_token(self):
        vault = Vault.objects.create(secret="s3cret-value")
        vault.secret = "n3w-value"
        vault.save()
        vault.delete()
        for row in get_model_history(vault):
            dumped = json.dumps([row.object_snapshot_before, row.object_snapshot_after, row.field_changes])
            self.assertNotIn("s3cret", dumped)
            self.assertNotIn("n3w", dumped)
            self.assertNotIn("gAAAAA", dumped)  # every Fernet token starts so
            for snapshot in (row.object_snapshot_before, row.object_snapshot_after):
                self.assertNotIn("secret", snapshot or {})

    def test_a_change_is_recorded_as_stars(self):
        vault = Vault.objects.create(secret="s3cret-value")
        vault.secret = "n3w-value"
        vault.save()
        row = get_model_history(vault).first()
        self.assertEqual(row.field_changes, {"secret": {"before": "***", "after": "***"}})

    def test_setting_and_clearing_show_the_empty_side(self):
        vault = Vault.objects.create()
        vault.secret = "s3cret-value"
        vault.save()
        self.assertEqual(get_model_history(vault).first().field_changes,
                         {"secret": {"before": "", "after": "***"}})
        vault.secret = ""
        vault.save()
        self.assertEqual(get_model_history(vault).first().field_changes,
                         {"secret": {"before": "***", "after": ""}})

    def test_the_same_secret_assigned_again_is_not_a_change(self):
        vault = Vault.objects.create(secret="s3cret-value")
        vault.secret = "s3cret-value"
        vault.save()
        self.assertEqual(get_model_history(vault).count(), 1)

    @override_settings(DJANGO_COMMON_UTILS={
        "PARAMETERS": {"AUTO_LOAD_ON_STARTUP": False},
        "ENCRYPTION": {"KEYS": [KEY_A]},
        "HISTORY": {"EXCLUDE_FIELDS": []},
    })
    def test_settings_cannot_put_the_secret_back_in_a_snapshot(self):
        vault = Vault.objects.create(secret="s3cret-value")
        self.assertNotIn("secret", get_model_history(vault).first().object_snapshot_after)


@needs_cryptography
class SerializerTests(TestCase):
    class VaultSerializer(SecretFieldsMixin, serializers.ModelSerializer):
        class Meta:
            model = Vault
            fields = ["id", "name", "secret", "legacy"]

    def test_the_mixin_maps_every_encrypted_field(self):
        fields = self.VaultSerializer().fields
        self.assertIsInstance(fields["secret"], SecretField)
        self.assertIsInstance(fields["legacy"], SecretField)

    def test_reads_never_return_the_value(self):
        vault = Vault.objects.create(secret="sk_live_abcd1234")
        data = self.VaultSerializer(Vault.objects.get(pk=vault.pk)).data
        self.assertEqual(data["secret"], {"is_set": True, "masked": "••••1234"})
        self.assertEqual(data["legacy"], {"is_set": False, "masked": None})
        self.assertNotIn("sk_live", json.dumps(data))

    def test_writes_take_the_plaintext(self):
        serializer = self.VaultSerializer(data={"name": "a", "secret": "sk_live_abcd1234"})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        vault = serializer.save()
        self.assertEqual(decrypt(raw_column(vault)), "sk_live_abcd1234")
        self.assertEqual(serializer.data["secret"]["masked"], "••••1234")

    def test_an_unreadable_secret_reads_as_set_without_a_mask(self):
        with keys(KEY_B):
            vault = Vault.objects.create(secret="made-with-another-key")
        data = self.VaultSerializer(Vault.objects.get(pk=vault.pk)).data
        self.assertEqual(data["secret"], {"is_set": True, "masked": None})


@needs_cryptography
class AdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@example.com", "pw")

    def test_the_form_never_renders_the_value_and_blank_keeps_it(self):
        vault = Vault.objects.create(secret="s3cret-value")
        token = raw_column(vault)
        loaded = Vault.objects.get(pk=vault.pk)
        Form = modelform_factory(Vault, fields=["name", "secret"])
        html = str(Form(instance=loaded)["secret"])
        self.assertIn('type="password"', html)
        self.assertNotIn("s3cret", html)
        self.assertNotIn(token, html)
        form = Form({"name": "renamed", "secret": ""}, instance=loaded)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn("secret", form.changed_data)
        form.save()
        self.assertEqual(raw_column(vault), token)

    def test_the_admin_widget_is_a_password_input(self):
        model_admin = admin.ModelAdmin(Vault, admin.site)
        form_field = model_admin.formfield_for_dbfield(Vault._meta.get_field("secret"), request=None)
        self.assertEqual(form_field.widget.input_type, "password")

    def test_a_form_can_set_a_new_secret(self):
        vault = Vault.objects.create(secret="s3cret-value")
        Form = modelform_factory(Vault, fields=["secret"])
        form = Form({"secret": "n3w-value"}, instance=Vault.objects.get(pk=vault.pk))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(Vault.objects.get(pk=vault.pk).secret, "n3w-value")

    def test_the_changelist_shows_the_mask_and_survives_an_unreadable_row(self):
        from django.contrib.admin.templatetags.admin_list import items_for_result

        class VaultAdmin(BaseModelAdmin):
            list_display = ("name", "secret")
            list_display_links = None  # Vault is not registered, so no change URL

        Vault.objects.create(name="good", secret="sk_live_abcd1234")
        with keys(KEY_B):
            Vault.objects.create(name="orphan", secret="made-with-another-key")
        request = RequestFactory().get("/admin/testapp/vault/")
        request.user = self.user
        changelist = VaultAdmin(Vault, admin.site).get_changelist_instance(request)
        cells = "".join(
            "".join(items_for_result(changelist, row, None)) for row in changelist.result_list
        )
        self.assertIn("••••1234", cells)
        self.assertIn("(unreadable)", cells)
        self.assertNotIn("sk_live", cells)


class RedactionTests(SimpleTestCase):
    def test_secret_setting_keys_are_redacted(self):
        body = json.dumps({"auth_token": "a", "webhook_token": "b", "signing_secret": "c",
                           "secret_key": "d", "api_secret": "e", "sid": "keep"})
        redacted = json.loads(redact_text(body))
        self.assertEqual(redacted["sid"], "keep")
        for key in ("auth_token", "webhook_token", "signing_secret", "secret_key", "api_secret"):
            self.assertEqual(redacted[key], "***REDACTED***")


@override_settings(DJANGO_COMMON_UTILS={"TRACKING": {"GEO_LOOKUP_URL": ""}})
class RequestLogTests(TestCase):
    def test_a_logged_post_that_sets_auth_token_is_redacted(self):
        from django.http import HttpResponse

        from django_common_utils.tracking.middleware import RequestLogMiddleware

        request = RequestFactory().post(
            "/api/provider-accounts/",
            data=json.dumps({"account_sid": "AC1", "auth_token": "tok-123"}),
            content_type="application/json", REMOTE_ADDR="9.9.9.9",
        )
        request.user = None
        RequestLogMiddleware(lambda request: HttpResponse("ok"))(request)
        body = RequestLog.objects.get().request_body
        self.assertIn("AC1", body)
        self.assertNotIn("tok-123", body)


class SystemCheckTests(SimpleTestCase):
    def ids(self):
        return [message.id for message in check_encryption()]

    @keys()
    def test_e001_encrypted_field_and_no_keys(self):
        self.assertIn("common_control.E001", self.ids())

    @keys(KEY_A, "nope")
    def test_e002_names_the_position(self):
        messages = [m for m in check_encryption() if m.id == "common_control.E002"]
        self.assertEqual(len(messages), 1)
        self.assertIn("[1]", messages[0].msg)
        self.assertNotIn("nope", messages[0].msg)

    def test_e003_cryptography_missing(self):
        with mock.patch("django_common_utils.crypto.checks.cryptography_installed", return_value=False):
            self.assertIn("common_control.E003", self.ids())

    @keys(KEY_A, KEY_B, KEY_A)
    def test_w001_repeated_key(self):
        messages = [m for m in check_encryption() if m.id == "common_control.W001"]
        self.assertEqual(len(messages), 1)
        self.assertIn("[2]", messages[0].msg)

    @keys(KEY_A)
    def test_silent_when_configured(self):
        if HAS_CRYPTOGRAPHY:
            self.assertEqual(self.ids(), [])

    @keys()
    def test_silent_with_no_field_and_no_keys(self):
        with mock.patch("django_common_utils.crypto.checks._models_with_encrypted_fields", return_value=[]):
            self.assertEqual(self.ids(), [])

    def test_registered(self):
        self.assertIn(check_encryption, checks.registry.registry.get_checks())


@needs_cryptography
class RotateCommandTests(TestCase):
    def run_command(self, *args):
        out, err = io.StringIO(), io.StringIO()
        call_command("rotate_encrypted_fields", *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def make_old(self, count=3):
        with keys(KEY_B):
            return [Vault.objects.create(name=f"v{i}", secret=f"secret-{i}") for i in range(count)]

    def test_rotation_moves_every_token_to_the_first_key(self):
        vaults = self.make_old()
        deleted = vaults[0]
        Vault.objects.filter(pk=deleted.pk).update(is_deleted=True)
        before = {v.pk: (Vault.objects.get(pk=v.pk).updated_at, get_model_history(v).count()) for v in vaults}

        with keys(KEY_A, KEY_B):
            out, _err = self.run_command("--batch-size", "2")
        self.assertIn("testapp.Vault.secret: rotated 3", out)

        with keys(KEY_A):
            for vault in vaults:
                loaded = Vault.objects.get(pk=vault.pk)
                self.assertEqual(loaded.secret, f"secret-{vaults.index(vault)}")
                self.assertEqual((loaded.updated_at, get_model_history(vault).count()), before[vault.pk])

        with keys(KEY_A, KEY_B):
            out, _err = self.run_command()
        self.assertIn("rotated 0, current 3", out)

    def test_dry_run_writes_nothing(self):
        vault, = self.make_old(1)
        token = raw_column(vault)
        with keys(KEY_A, KEY_B):
            out, _err = self.run_command("--dry-run")
        self.assertIn("rotated 1", out)
        self.assertEqual(raw_column(vault), token)

    def test_an_unreadable_row_is_named_and_fails_the_command(self):
        with keys(KEY_C):
            orphan = Vault.objects.create(secret="lost")
        self.make_old(1)
        with keys(KEY_A, KEY_B):
            with self.assertRaises(CommandError):
                out, err = io.StringIO(), io.StringIO()
                try:
                    call_command("rotate_encrypted_fields", stdout=out, stderr=err)
                finally:
                    self.assertIn(str(orphan.pk), err.getvalue())
                    self.assertIn("failed 1", out.getvalue())
                    self.assertNotIn("lost", out.getvalue() + err.getvalue())

    def test_plaintext_is_encrypted_only_when_asked(self):
        vault = Vault.objects.create()
        Vault.objects.filter(pk=vault.pk).update(legacy=_raw("plain-old-secret"))
        out, _err = self.run_command("--model", "testapp.Vault")
        self.assertIn("testapp.Vault.legacy: rotated 0, current 0, empty 0, plaintext skipped 1", out)
        self.assertEqual(raw_column(vault, "legacy"), "plain-old-secret")
        self.run_command("--include-plaintext")
        self.assertEqual(decrypt(raw_column(vault, "legacy")), "plain-old-secret")

    def test_narrowing(self):
        out, _err = self.run_command("--app", "common_control")
        self.assertIn("No encrypted fields installed.", out)
        with self.assertRaises(CommandError):
            self.run_command("--model", "testapp.Nope")
        with self.assertRaises(CommandError):
            self.run_command("--app", "nope")

    @keys()
    def test_refuses_without_keys(self):
        with self.assertRaises(CommandError):
            self.run_command()
