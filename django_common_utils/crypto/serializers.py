"""The DRF side of an encrypted field (PRD §17.6).

``SecretField`` takes a plaintext on write and on read returns only whether a
secret is set and its mask — never the value. ``SecretFieldsMixin`` makes it the
field every ``EncryptedTextField`` maps to, so a staff API built with a
``ModelSerializer`` cannot echo a secret by accident.
"""

from rest_framework import serializers

from django_common_utils.crypto.fields import EncryptedTextField
from django_common_utils.crypto.keys import DecryptionError, EncryptionNotConfigured, mask

#: Stands in for a stored secret that could not be opened.
_UNREADABLE = object()


class SecretField(serializers.CharField):
    """Write the plaintext; read ``{"is_set": bool, "masked": "••••1234" | null}``.

    A stored value no key opens reads as set, with no mask, rather than failing
    the whole response. Writing ``""`` clears the secret.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("trim_whitespace", False)
        kwargs.pop("max_length", None)
        super().__init__(**kwargs)

    def get_attribute(self, instance):
        try:
            value = super().get_attribute(instance)
        except (DecryptionError, EncryptionNotConfigured):
            return _UNREADABLE
        # Not None: the serializer would write a bare null for it and skip
        # to_representation, and the shape must not depend on the column.
        return "" if value is None else value

    def to_representation(self, value):
        if value is _UNREADABLE:
            return {"is_set": True, "masked": None}
        if value is None or value == "":
            return {"is_set": False, "masked": None}
        return {"is_set": True, "masked": mask(value)}


class SecretFieldsMixin:
    """For a ``ModelSerializer``: every ``EncryptedTextField`` becomes a
    ``SecretField``. List it before ``ModelSerializer`` in the bases."""

    serializer_field_mapping = {
        **serializers.ModelSerializer.serializer_field_mapping,
        EncryptedTextField: SecretField,
    }
