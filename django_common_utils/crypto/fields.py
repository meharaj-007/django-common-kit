"""``EncryptedTextField`` (PRD §17.5, §17.6).

A ``text`` column holding a Fernet token; plaintext on the instance. Three
states can sit in the instance's ``__dict__`` under the field's attname:

* ``StoredToken`` — the value as read from the database, not yet decrypted.
  Reading the attribute decrypts it once and caches the plaintext on it.
* a plain ``str`` — plaintext assigned since the load; encrypted on save.
* ``""`` / ``None`` — not set.

Decryption is lazy, on attribute access, so loading a row or listing a
queryset never needs a key, and a row whose token no key opens raises
``DecryptionError`` where its secret is read rather than wherever the queryset
happens to be evaluated. A value read and written back unchanged is saved as
the same token, so an untouched secret is never re-encrypted and never looks
like a change.

The plaintext handed out is a ``str`` subclass that remembers the token it
came from, so a whole-row ``refresh_from_db()`` or a ``full_clean()`` — both of
which read every attribute and set it again — leave the secret untouched.

Only ``isnull`` and ``exact ""`` are lookups. Fernet tokens are randomised, so
any other filter on the column could only ever match nothing; it raises
``FieldError`` instead.
"""

import logging

from django import forms
from django.core.exceptions import FieldError
from django.db import models
from django.db.models.lookups import Exact
from django.db.models.query_utils import DeferredAttribute

from django_common_utils.crypto.keys import decrypt, encrypt, looks_like_token

logger = logging.getLogger(__name__)

#: (model label, field name) pairs already logged as holding plaintext.
_legacy_logged = set()


class StoredToken(str):
    """A value as it came from the database. ``str(token)`` is the token."""

    def __new__(cls, value, plaintext=None):
        token = super().__new__(cls, value)
        token._plaintext = plaintext
        return token

    def plaintext(self, field=None):
        if self._plaintext is None:
            raw = str(self)
            if field is not None and field.legacy_plaintext and not looks_like_token(raw):
                _log_legacy(field)
                value = raw
            else:
                value = decrypt(raw)
            self._plaintext = Plaintext(value, token=raw)
        return self._plaintext

    def __reduce__(self):
        # Pickled as the token alone: the cached plaintext stays out of any
        # cache, session or queue the instance is pickled into.
        return (StoredToken, (str(self),))


class Plaintext(str):
    """A decrypted value, carrying the token it came from.

    Assigned back to the field, it is recognised as the stored value and saved
    as the same token. Any string operation on it returns a plain ``str``, so a
    changed value is always seen as a change.
    """

    def __new__(cls, value, token=None):
        plain = super().__new__(cls, value)
        plain.token = token
        return plain

    def __reduce__(self):
        return (str, (str(self),))


def _log_legacy(field):
    key = (field.model._meta.label, field.name)
    if key not in _legacy_logged:
        _legacy_logged.add(key)
        logger.warning(
            "%s.%s holds a value that is not a Fernet token; returning it as-is "
            "(legacy_plaintext). Run rotate_encrypted_fields --include-plaintext "
            "to encrypt it.",
            key[0], key[1],
        )


def raw_value(instance, field):
    """The field's value as it would be written, without decrypting: a
    ``StoredToken``, a plaintext ``str`` not yet encrypted, ``""`` or ``None``.
    Loads the column if it was deferred."""
    data = instance.__dict__
    if field.attname not in data:
        instance.refresh_from_db(fields=[field.attname])
    value = data.get(field.attname)
    if isinstance(value, Plaintext) and value.token is not None:
        return StoredToken(value.token, plaintext=value)
    return value


class EncryptedAttribute(DeferredAttribute):
    """Decrypts on first read; tells a stored token from new plaintext on write."""

    def __get__(self, instance, cls=None):
        if instance is None:
            return self
        value = super().__get__(instance, cls)
        if isinstance(value, StoredToken):
            return value.plaintext(self.field)
        return value

    def __set__(self, instance, value):
        if isinstance(value, Plaintext) and value.token is not None:
            value = StoredToken(value.token, plaintext=value)
        instance.__dict__[self.field.attname] = value


class SecretFormField(forms.CharField):
    """A password input that never shows the stored value. Submitted blank, the
    stored secret is kept (``EncryptedTextField.save_form_data``)."""

    widget = forms.PasswordInput(render_value=False)

    def has_changed(self, initial, data):
        return bool(data)


class EncryptedTextField(models.TextField):
    """A secret the project must read back — a provider token, an OAuth refresh
    token, a signing secret — stored as a Fernet token.

    ``legacy_plaintext=True`` returns a stored value that is not a Fernet token
    as-is, and logs it once per model and field: the adoption path for a column
    that held plaintext. ``rotate_encrypted_fields --include-plaintext`` then
    encrypts those rows.
    """

    descriptor_class = EncryptedAttribute
    description = "Text encrypted at rest"

    def __init__(self, *args, legacy_plaintext=False, **kwargs):
        if kwargs.get("max_length") is not None:
            # A token is about 1.4 times the plaintext plus 57 bytes, so a cap
            # would reject a long secret after encrypting it.
            raise TypeError("EncryptedTextField does not take max_length.")
        self.legacy_plaintext = legacy_plaintext
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        # The public path, so a project's migrations survive a move of this
        # module.
        path = "django_common_utils.crypto.EncryptedTextField"
        if self.legacy_plaintext:
            kwargs["legacy_plaintext"] = True
        return name, path, args, kwargs

    # -- reading and writing the column ---------------------------------------
    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        return StoredToken(value)

    def pre_save(self, model_instance, add):
        """Encrypt plaintext assigned since the load, and keep the token on the
        instance so the next save writes the same one."""
        value = raw_value(model_instance, self)
        if value is None or value == "" or isinstance(value, StoredToken):
            return value
        plain = str(value)
        token = StoredToken(encrypt(plain))
        token._plaintext = Plaintext(plain, token=str(token))
        model_instance.__dict__[self.attname] = token
        return token

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None or value == "":
            return value
        if isinstance(value, StoredToken):
            return str(value)
        if isinstance(value, Plaintext) and value.token is not None:
            return value.token
        # A plaintext reaching the database by another path — a queryset
        # ``update()``, ``bulk_update`` — is encrypted on the way.
        return encrypt(str(value))

    def value_from_object(self, obj):
        # The token, not the plaintext: this feeds a model form's initial data
        # and ``dumpdata``, neither of which should decrypt.
        value = raw_value(obj, self)
        return str(value) if isinstance(value, StoredToken) else value

    # -- lookups ---------------------------------------------------------------
    def get_lookup(self, lookup_name):
        if lookup_name not in ("exact", "isnull"):
            raise FieldError(
                f"{self.model._meta.label}.{self.name} is encrypted and can only be "
                f"filtered with isnull or an exact empty string, not {lookup_name!r}."
            )
        return super().get_lookup(lookup_name)

    def get_transform(self, lookup_name):
        return None

    # -- forms -----------------------------------------------------------------
    def formfield(self, **kwargs):
        # The admin maps every TextField to a textarea; that must not win here,
        # or the stored token would be rendered into the page.
        kwargs.pop("widget", None)
        kwargs.pop("max_length", None)
        defaults = {
            "form_class": SecretFormField,
            "required": False,
            "strip": False,
            "help_text": self.help_text or "Leave blank to keep the current value.",
        }
        defaults.update(kwargs)
        defaults["form_class"] = SecretFormField
        return models.Field.formfield(self, **defaults)

    def save_form_data(self, instance, data):
        # Blank keeps the stored secret: the input never shows it, so an empty
        # submission is "not changed", not "cleared".
        if data in (None, ""):
            return
        super().save_form_data(instance, data)


@EncryptedTextField.register_lookup
class EncryptedExact(Exact):
    """``exact`` against ``""`` only (``None`` becomes ``isnull`` upstream).

    A ``StoredToken`` is also accepted, for the rotation command's
    compare-and-swap: it compares the stored token, not a plaintext.
    """

    def get_prep_lookup(self):
        rhs = self.rhs
        if not (rhs is None or rhs == "" or isinstance(rhs, StoredToken)):
            raise FieldError(
                f"{self.lhs.output_field.model._meta.label}.{self.lhs.output_field.name} "
                "is encrypted: an equality filter on it could only ever match "
                "nothing. Filter with isnull or an exact empty string."
            )
        return super().get_prep_lookup()


def encrypted_fields(model):
    """The ``EncryptedTextField``s on ``model``, concrete ones only."""
    return [
        field for field in model._meta.concrete_fields
        if isinstance(field, EncryptedTextField)
    ]


def secret_changed(old_instance, new_instance, field):
    """Whether ``field`` holds a different secret on ``new_instance`` than on
    ``old_instance``. Equal tokens are the same secret; different tokens are
    compared by plaintext, and one that cannot be opened counts as changed."""
    before = raw_value(old_instance, field)
    after = raw_value(new_instance, field)
    if not before or not after:
        return bool(before) != bool(after)
    if str(before) == str(after):
        return False
    try:
        return _plain(before, field) != _plain(after, field)
    except Exception:  # noqa: BLE001 - unreadable is "changed", never a crash
        return True


def _plain(value, field):
    if isinstance(value, StoredToken):
        return str(value.plaintext(field))
    return str(value)
