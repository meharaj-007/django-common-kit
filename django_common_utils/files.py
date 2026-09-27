"""What an upload may be (PRD §9.4).

``FILES["MAX_UPLOAD_BYTES"]`` and ``FILES["ALLOWED_MIME_TYPES"]`` are enforced
here, in one validator, because an upload reaches a project through a form, a
serializer or the admin, and each would otherwise grow its own copy of the same
two checks with slightly different messages.

It is applied at intake, never in ``save()``. A row already stored was accepted
under the limits in force when it arrived; checking on every save would make it
unsaveable the day a project lowers a limit, for an edit to its title.

The type check is a policy filter, not a defence. The declared content type and
the file name both come from the client, and neither says what the bytes are.
A project that must know what it is serving inspects the content itself.
"""

import mimetypes

from django.core.exceptions import ValidationError

from django_common_utils.conf import app_settings


def upload_mime_type(upload):
    """The type an upload says it is: the client's declared ``content_type``,
    else a guess from its name. ``None`` when neither gives one."""
    declared = getattr(upload, "content_type", None)
    if declared:
        return declared.split(";", 1)[0].strip().lower()
    guessed, _ = mimetypes.guess_type(getattr(upload, "name", "") or "")
    return guessed.lower() if guessed else None


def _type_allowed(mime_type, allowed):
    for entry in allowed:
        entry = entry.strip().lower()
        if entry.endswith("/*"):
            if mime_type and mime_type.startswith(entry[:-1]):
                return True
        elif mime_type == entry:
            return True
    return False


def validate_upload(upload):
    """Raise ``ValidationError`` when ``upload`` breaks the ``FILES`` limits.

    Usable as a Django form or DRF field validator
    (``FileField(validators=[validate_upload])``); DRF turns the Django error
    into a 400 on that field. ``MAX_UPLOAD_BYTES`` of ``0`` means no size
    limit; ``None`` is read as unset, like every key, and gives the default.
    ``ALLOWED_MIME_TYPES`` empty means any type; an entry ending ``/*``
    (``"image/*"``) allows the whole family.
    """
    if not upload:
        return

    limit = app_settings.get("FILES", "MAX_UPLOAD_BYTES")
    size = getattr(upload, "size", None)
    if limit and size is not None and size > limit:
        raise ValidationError(
            "File is too large: %(size)s bytes, the limit is %(limit)s.",
            code="file_too_large",
            params={"size": size, "limit": limit},
        )

    allowed = app_settings.get("FILES", "ALLOWED_MIME_TYPES")
    if allowed:
        mime_type = upload_mime_type(upload)
        if not _type_allowed(mime_type, allowed):
            raise ValidationError(
                "Files of type %(type)s are not accepted.",
                code="file_type_not_allowed",
                params={"type": mime_type or "unknown"},
            )
