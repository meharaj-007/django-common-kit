"""django-common-kit — the shared Django foundation.

The foundation a Django REST backend starts from: a UUID ``BaseModel``,
a change-history trail, a parameter table with a cache in front of it, request
and IP tracking, a generic file attachment, and one REST response envelope.

The dividing line is vocabulary: **if it has no business meaning, this package
owns it; the moment it names a lead, a job, an order, a crew or a role, it does
not.** A scope helper that filters by crew membership, a status enum for quotes,
an email template — all project, however much they currently sit in ``common/``.

See PRD.md §2 for the boundary in full.
"""

__version__ = "0.11.1"

default_app_config = "django_common_kit.apps.CommonKitConfig"

#: Attribute name -> module it lives in. Both are imported lazily so that
#: ``import django_common_kit`` does not pull models in before the app registry is
#: ready — the package is imported by ``INSTALLED_APPS`` itself.
_LAZY_EXPORTS = {
    "ApiResponse": "django_common_kit.api.response",
    "BaseModel": "django_common_kit.models",
    "ParameterCache": "django_common_kit.parameters.cache",
}


def __getattr__(name):
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = __import__(module_path, fromlist=[name])
    return getattr(module, name)


__all__ = ["ApiResponse", "BaseModel", "ParameterCache", "__version__"]
