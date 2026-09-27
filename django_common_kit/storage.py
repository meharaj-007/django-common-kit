"""Where uploaded files go (PRD §9.3).

Three rules this module keeps that a hand-rolled storage class usually breaks.

**Nothing from the host project is imported.** ``from base import settings``
works in the project it was written in and raises ``ModuleNotFoundError`` in
the next one. Forbidden by §2 and caught by ``tests/test_import_purity.py``.

**``storages`` is not imported at module scope.** ``django_common_kit.storage``
must import cleanly with ``django-storages`` and ``boto3`` absent, so a
local-disk project does not have to install an S3 SDK to use the rest of the
package.

**The backend is not chosen with a module-level ``if``.** A setting read once at
first import means ``override_settings`` in a test changes nothing and the
class a model captured stays whatever the first import decided.
``MediaStorage`` is a proxy that resolves its backend on first *use* instead.

The layout rules:

- a deployment writes under one root (``MEDIA_LOCATION``), which is the storage
  class's job so that no caller has to remember it and no caller can opt out;
- inside that root the first segment is the owning tenant's id, so that
  "everything belonging to this tenant" is one prefix and "delete what this
  tenant owns" is expressible at all;
- **a file with no owner goes to ``system/`` and is never guessed into one.** A
  generic foreign key may point anywhere, and inferring an owner from it would
  file one tenant's document under another's prefix.
"""

import logging

from django.core.files.storage import FileSystemStorage, Storage

from django_common_kit.conf import app_settings

logger = logging.getLogger(__name__)

#: Where a file with no owner lives. Not a tenant id, and deliberately not a
#: valid one — a UUID prefix must mean exactly one tenant owns it.
OWNERLESS_ROOT = "system"


def owner_root(owner_id) -> str:
    """The first path segment: the owner's id, or ``system/``."""
    return str(owner_id) if owner_id else OWNERLESS_ROOT


def scoped_path(owner_id, *segments) -> str:
    """``<owner_id>/<segment>/…`` — the one way a media key is built.

    Empty segments are dropped, so a caller with no period or no category still
    produces a clean key rather than one with a doubled separator.
    """
    parts = [owner_root(owner_id)]
    parts.extend(str(segment).strip("/") for segment in segments if segment)
    return "/".join(parts)


def safe_basename(filename: str, fallback: str) -> str:
    """The filename with any path the caller supplied stripped off.

    Django's ``FileField`` raises ``SuspiciousFileOperation`` for an
    ``upload_to`` that returns a directory, and happily writes outside the root
    for one that returns a caller-controlled path. Both are avoided by never
    trusting the client's filename past its basename.
    """
    return (filename or "").split("/")[-1] or fallback



def build_backend():
    """The configured storage backend, built fresh.

    ``django-storages`` and ``boto3`` are imported here and nowhere else, so a
    project on local disk never needs them installed, and a project that asked
    for S3 without installing them gets a message naming the extra rather than an
    ``ImportError`` at startup.
    """
    storage_type = (app_settings.get("STORAGE", "TYPE") or "local").lower()

    if storage_type != "s3":
        return FileSystemStorage()

    try:
        from storages.backends.s3boto3 import S3Boto3Storage
    except ImportError as exc:
        raise ImportError(
            "STORAGE['TYPE'] is 's3' but django-storages is not installed. "
            "Install django-common-kit[s3]."
        ) from exc

    from django.conf import settings

    class _MediaS3Storage(S3Boto3Storage):
        bucket_name = getattr(settings, "AWS_BUCKET_NAME", None)
        # One value builds both MEDIA_URL and the stored key, so they cannot
        # disagree about where a file is.
        location = app_settings.get("STORAGE", "MEDIA_LOCATION")
        file_overwrite = app_settings.get("STORAGE", "FILE_OVERWRITE")

    return _MediaS3Storage()


class MediaStorage(Storage):
    """The media backend, resolved on first use rather than at import.

    ``FileField(storage=MediaStorage())`` is evaluated at model-definition time — before settings are necessarily final and
    long before a test can override them. This proxy keeps the call shape and
    moves the decision to the first read or write.

    ``deconstruct`` returns this class with no arguments, so the migration a
    ``FileField`` generates names the proxy and never bakes a bucket name or a
    backend choice into the migration graph. A project that switches from local
    disk to S3 then needs no migration at all.
    """

    def __init__(self, *args, **kwargs):
        self._backend = None

    def deconstruct(self):
        return ("django_common_kit.storage.MediaStorage", [], {})

    @property
    def backend(self):
        if self._backend is None:
            self._backend = build_backend()
        return self._backend

    def reset(self, **kwargs):
        """Drop the resolved backend. Connected to ``setting_changed``."""
        self._backend = None

    # -- Storage interface, delegated --------------------------------------
    # Spelled out rather than done with __getattr__, so that a typo in a caller
    # raises AttributeError here instead of being forwarded into the backend and
    # failing somewhere less obvious.

    def open(self, name, mode="rb"):
        return self.backend.open(name, mode)

    def save(self, name, content, max_length=None):
        return self.backend.save(name, content, max_length=max_length)

    def path(self, name):
        return self.backend.path(name)

    def delete(self, name):
        return self.backend.delete(name)

    def exists(self, name):
        return self.backend.exists(name)

    def listdir(self, path):
        return self.backend.listdir(path)

    def size(self, name):
        return self.backend.size(name)

    def url(self, name):
        return self.backend.url(name)

    def get_accessed_time(self, name):
        return self.backend.get_accessed_time(name)

    def get_created_time(self, name):
        return self.backend.get_created_time(name)

    def get_modified_time(self, name):
        return self.backend.get_modified_time(name)

    def get_valid_name(self, name):
        return self.backend.get_valid_name(name)

    def get_available_name(self, name, max_length=None):
        return self.backend.get_available_name(name, max_length=max_length)

    def generate_filename(self, filename):
        return self.backend.generate_filename(filename)
