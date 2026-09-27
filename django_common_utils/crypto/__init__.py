"""Encryption at rest for short secrets (PRD §17).

One helper for every package and project that stores a secret it must read
back — a provider auth token, an OAuth refresh token, a webhook signing secret.
Passwords are hashed by Django's auth and never belong here.

    from django_common_utils.crypto import encrypt, decrypt, mask, EncryptedTextField

Keys come from ``DJANGO_COMMON_UTILS["ENCRYPTION"]["KEYS"]``, newest first. Needs the
``crypto`` extra; without it the package still imports and every function
raises ``EncryptionNotConfigured``.

The DRF names (``SecretField``, ``SecretFieldsMixin``) are imported on first
use, so a model module importing the field does not import DRF's serializers.
"""

from django_common_utils.crypto.fields import EncryptedTextField
from django_common_utils.crypto.keys import (
    DecryptionError,
    EncryptionNotConfigured,
    decrypt,
    encrypt,
    is_configured,
    mask,
    rotate_token,
)

_LAZY_EXPORTS = {
    "SecretField": "django_common_utils.crypto.serializers",
    "SecretFieldsMixin": "django_common_utils.crypto.serializers",
}


def __getattr__(name):
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = __import__(module_path, fromlist=[name])
    return getattr(module, name)


__all__ = [
    "DecryptionError",
    "EncryptedTextField",
    "EncryptionNotConfigured",
    "SecretField",
    "SecretFieldsMixin",
    "decrypt",
    "encrypt",
    "is_configured",
    "mask",
    "rotate_token",
]
