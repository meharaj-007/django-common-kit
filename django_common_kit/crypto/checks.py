"""System checks for encryption (PRD §17.8).

Caught at ``migrate`` and at every ``runserver`` start rather than at the first
save of a secret in production. A project with no encrypted field and no keys
gets no message. No message names a key's value, only its position.
"""

from django.apps import apps
from django.core import checks

from django_common_kit.crypto.fields import encrypted_fields
from django_common_kit.crypto.keys import (
    SETTING_NAME,
    configured_keys,
    cryptography_installed,
    is_valid_key,
)


def _models_with_encrypted_fields():
    return [
        model for model in apps.get_models()
        if not model._meta.proxy and encrypted_fields(model)
    ]


def check_encryption(app_configs=None, **kwargs):
    messages = []
    keys = configured_keys()
    models = _models_with_encrypted_fields()
    where = ", ".join(model._meta.label for model in models[:3])
    if len(models) > 3:
        where += f" and {len(models) - 3} more"

    if models and not keys:
        messages.append(checks.Error(
            f"{SETTING_NAME} is empty, but {where} store encrypted secrets.",
            hint="Generate a key with Fernet.generate_key() and set it from the environment.",
            id="common_control.E001",
        ))

    for position, key in enumerate(keys):
        if not is_valid_key(key):
            messages.append(checks.Error(
                f"{SETTING_NAME}[{position}] is not a Fernet key.",
                hint="A key is the 44-character output of Fernet.generate_key().",
                id="common_control.E002",
            ))

    if models and not cryptography_installed():
        messages.append(checks.Error(
            f"{where} store encrypted secrets, and the cryptography package is not installed.",
            hint="Install django-common-kit[crypto].",
            id="common_control.E003",
        ))

    seen = set()
    for position, key in enumerate(keys):
        if key in seen:
            messages.append(checks.Warning(
                f"{SETTING_NAME}[{position}] repeats an earlier key.",
                hint="Each key needs to appear once; remove the repeat.",
                id="common_control.W001",
            ))
        seen.add(key)

    return messages
