"""Encrypting short secrets at rest (PRD §17.3, §17.4).

Fernet, with a key list: the first key encrypts, every key decrypts, so a key
is rotated by putting the new one in front, re-encrypting, and dropping the old
one. Tokens are plain Fernet tokens with no prefix, so a column written by any
other Fernet helper reads back unchanged once its key is in the list.

``cryptography`` is imported inside the functions, never at module scope: the
package must import without the ``crypto`` extra, and only a project that
stores a secret needs it.

There is no key derived from ``SECRET_KEY``. Rotating ``SECRET_KEY`` is a
routine response to a leak, and it would make every stored secret unreadable,
silently, at the next read. No key means no encryption, and an error that
says so.

Nothing here logs a value, a token or a key.
"""

import base64
import binascii

from django.core.exceptions import ImproperlyConfigured

from django_common_utils.conf import app_settings

SETTING_NAME = 'DJANGO_COMMON_UTILS["ENCRYPTION"]["KEYS"]'

GENERATE_HINT = (
    'python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())"'
)

#: What ``mask`` shows in place of the hidden characters. A fixed width, so a
#: mask does not give away the secret's length.
MASK = "••••"


class EncryptionNotConfigured(ImproperlyConfigured):
    """No keys, a key that is not a Fernet key, or ``cryptography`` missing."""


class DecryptionError(Exception):
    """No configured key opens the value: a wrong or dropped key, or a
    corrupted value. The message never contains the value."""


def configured_keys():
    """The key list as strings, newest first. A single string is a list of one;
    blank entries are dropped."""
    keys = app_settings.get("ENCRYPTION", "KEYS")
    if isinstance(keys, (str, bytes)):
        keys = [keys]
    result = []
    for key in keys or ():
        if isinstance(key, bytes):
            key = key.decode("ascii", errors="replace")
        key = (key or "").strip()
        if key:
            result.append(key)
    return result


def is_valid_key(key):
    """Whether ``key`` is the output of ``Fernet.generate_key()``: 32 bytes in
    url-safe base64. Checked without ``cryptography``, so the system check can
    say which key is wrong even where the library is missing."""
    if not isinstance(key, str) or len(key) != 44:
        return False
    try:
        return len(base64.urlsafe_b64decode(key.encode("ascii"))) == 32
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return False


def cryptography_installed():
    import importlib.util

    return importlib.util.find_spec("cryptography") is not None


def _fernet_module():
    try:
        from cryptography import fernet
    except ImportError:
        raise EncryptionNotConfigured(
            "Encrypting secrets needs the cryptography package: install "
            "django-common-utils[crypto]."
        ) from None
    return fernet


def _validated_keys():
    keys = configured_keys()
    if not keys:
        raise EncryptionNotConfigured(
            f"{SETTING_NAME} is empty, so secrets cannot be encrypted or read. "
            f"Generate a key with {GENERATE_HINT} and set it from the environment."
        )
    for position, key in enumerate(keys):
        if not is_valid_key(key):
            raise EncryptionNotConfigured(
                f"{SETTING_NAME}[{position}] is not a Fernet key. A key is the "
                f"output of {GENERATE_HINT}."
            )
    return keys


# Built once per key list. Keyed on the list itself, so a changed setting —
# ``override_settings`` in a test, or a key added at runtime — builds a new one
# without a signal to remember.
_cache = {}


def _multi_fernet():
    keys = tuple(_validated_keys())
    built = _cache.get(keys)
    if built is None:
        fernet = _fernet_module()
        built = (
            fernet.MultiFernet([fernet.Fernet(key.encode("ascii")) for key in keys]),
            fernet.Fernet(keys[0].encode("ascii")),
        )
        _cache.clear()
        _cache[keys] = built
    return built


def is_configured():
    """``True`` when ``KEYS`` is set, every key is valid and ``cryptography``
    is installed — when ``encrypt`` would succeed."""
    try:
        _multi_fernet()
    except EncryptionNotConfigured:
        return False
    return True


def encrypt(value):
    """Plaintext ``str`` to a Fernet token ``str``, with the first key.

    ``""`` and ``None`` come back unchanged: an empty secret is "not set", and
    the column must still answer ``isnull`` and empty checks.
    """
    if value is None or value == "":
        return value
    if not isinstance(value, str):
        raise TypeError(f"encrypt() takes a str, not {type(value).__name__}")
    multi, _first = _multi_fernet()
    return multi.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(token):
    """A token made with any key in ``KEYS`` back to its plaintext."""
    if token is None or token == "":
        return token
    multi, _first = _multi_fernet()
    fernet = _fernet_module()
    try:
        return multi.decrypt(_as_bytes(token)).decode("utf-8")
    except (fernet.InvalidToken, UnicodeError):
        # `from None`: the chained exception is no use to anyone reading the
        # trace, and the message must not carry the value.
        raise DecryptionError(
            f"No key in {SETTING_NAME} opens this value."
        ) from None


def rotate_token(token):
    """``token`` re-encrypted with the first key. Its plaintext is unchanged."""
    if token is None or token == "":
        return token
    multi, _first = _multi_fernet()
    fernet = _fernet_module()
    try:
        return multi.rotate(_as_bytes(token)).decode("ascii")
    except (fernet.InvalidToken, UnicodeError):
        raise DecryptionError(f"No key in {SETTING_NAME} opens this value.") from None


def is_current(token):
    """Whether the first key alone opens ``token`` — nothing to rotate."""
    _multi, first = _multi_fernet()
    fernet = _fernet_module()
    try:
        first.decrypt(_as_bytes(token))
    except (fernet.InvalidToken, UnicodeError):
        return False
    return True


def looks_like_token(value):
    """Whether ``value`` has the shape of a Fernet token: url-safe base64 of a
    version byte 0x80, an 8-byte timestamp, a 16-byte IV, whole AES blocks and a
    32-byte HMAC. Shape only — it says nothing about which key made it."""
    if not isinstance(value, str) or len(value) < 100:
        return False
    try:
        raw = base64.urlsafe_b64decode(value.encode("ascii"))
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return False
    return raw[:1] == b"\x80" and len(raw) >= 73 and (len(raw) - 57) % 16 == 0


def mask(value, keep=4):
    """``"sk_live_abcd1234"`` to ``"••••1234"``, for showing that a secret is
    set and telling two apart. Eight characters or fewer are masked entirely;
    empty is ``""``. Never raises."""
    try:
        if value is None or value == "":
            return ""
        text = str(value)
        if len(text) <= 8 or keep <= 0:
            return MASK
        return MASK + text[-keep:]
    except Exception:  # noqa: BLE001 - a display helper must not break a page
        return MASK


def _as_bytes(token):
    return token.encode("ascii") if isinstance(token, str) else bytes(token)
