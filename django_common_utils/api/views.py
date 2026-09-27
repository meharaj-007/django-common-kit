"""Base view classes (PRD §5.2).

``CustomModelViewSet``, ``CustomListAPIView``, ``CustomWOPListAPIView`` (list
without pagination), ``CustomRetrieveAPIView``, ``CustomCreateAPIView``,
``CustomUpdateAPIView`` and ``CustomDestroyAPIView``, each composed from the
mixins below:

- ``SwaggerSafeQuerysetMixin`` — schema generation must not run a
  ``get_queryset`` that reads ``request.user``;
- ``EnvelopeListMixin`` — an unpaginated list must still answer in the envelope;
- ``SoftDeleteMixin`` — ``is_deleted`` exists on every row and DRF's default
  ``perform_destroy`` ignores it;
- ``LoggedCreateMixin`` — the ``pre_create`` hook and a log line on POST.

Permissions default to ``IsAuthenticated`` plus whatever
``DJANGO_COMMON_UTILS["PERMISSION_CLASSES"]`` names, so a project's RBAC class is
bolted on from settings and never referenced here (§2).
"""

import functools
import logging

from django.utils import timezone
from rest_framework.generics import (
    CreateAPIView,
    DestroyAPIView,
    ListAPIView,
    RetrieveAPIView,
    UpdateAPIView,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from django_common_utils.api.pagination import CustomPageNumberPagination
from django_common_utils.api.response import ApiResponse
from django_common_utils.conf import app_settings
from django_common_utils.tracking.redaction import redact_structure

logger = logging.getLogger(__name__)


def default_permission_classes():
    """``IsAuthenticated`` plus whatever the project named in settings.

    Resolved per call rather than captured at class-definition time, so a
    project's RBAC class is imported at first request and not while the app
    registry is still loading.
    """
    return [IsAuthenticated, *app_settings.permission_classes()]


class ProjectPermissionMixin:
    """Applies ``default_permission_classes()`` unless the view names its own."""

    permission_classes = None

    def get_permissions(self):
        if self.permission_classes is None:
            return [permission() for permission in default_permission_classes()]
        return super().get_permissions()


# -- schema generation ------------------------------------------------------

def _schema_placeholder_queryset(view):
    """An empty queryset of the view's model, or ``None`` if it cannot be worked out.

    A schema generator needs *a* queryset to read the model off — for the filter
    backends and the response serializer — but never evaluates it, so an empty
    one is enough.
    """
    queryset = getattr(view, "queryset", None)
    if queryset is not None:
        return queryset.none()

    serializer_class = getattr(view, "serializer_class", None)
    model = getattr(getattr(serializer_class, "Meta", None), "model", None)
    if model is not None:
        return model._default_manager.none()

    return None


class SwaggerSafeQuerysetMixin:
    """Stops ``get_queryset()`` blowing up while the schema is built.

    drf-yasg introspects each view by instantiating it against a bare request
    with no authenticated user. Any ``get_queryset()`` that reads
    ``request.user`` — ``filter(user=self.request.user)``, a permission-service
    branch, a scope helper — therefore raises ``AttributeError``, or a UUID
    ``ValidationError``, on ``AnonymousUser``. drf-yasg swallows it and emits the
    endpoint stripped of its parameters, so those routes appear in the docs with
    no filters and no body — quietly, which is why it goes unnoticed until
    ``/swagger/`` is opened to anonymous callers.

    The documented fix is ``if getattr(self, 'swagger_fake_view', False)`` at the
    top of every such method. Rather than repeat that in forty views and rely on
    everyone remembering it in the forty-first, this wraps whatever
    ``get_queryset`` a subclass defines. The guard is inert outside schema
    generation: ``swagger_fake_view`` is set by drf-yasg alone, never during a
    real request, so a project not using drf-yasg pays nothing.

    Views with neither ``queryset`` nor a ``serializer_class.Meta.model`` have no
    model to fall back to; those run unguarded and still need handling by hand.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # Only wrap a ``get_queryset`` this class defines itself. An inherited
        # one is already wrapped, and re-wrapping nests the guard per level.
        own_get_queryset = cls.__dict__.get("get_queryset")
        if own_get_queryset is None or getattr(own_get_queryset, "_swagger_guarded", False):
            return

        @functools.wraps(own_get_queryset)
        def get_queryset(self, *args, **kwargs):
            if getattr(self, "swagger_fake_view", False):
                placeholder = _schema_placeholder_queryset(self)
                if placeholder is not None:
                    return placeholder
            return own_get_queryset(self, *args, **kwargs)

        get_queryset._swagger_guarded = True
        cls.get_queryset = get_queryset


class EnvelopeListMixin:
    """Return an unpaginated list inside the standard envelope.

    Without this, a view that sets ``pagination_class = None`` answers with a
    bare JSON array while every other endpoint answers with ``{status, data,
    …}``, and the client has to branch on which.
    """

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        if isinstance(response.data, list):
            return ApiResponse.success(
                data=response.data, message="Results retrieved successfully"
            )
        return response


class SoftDeleteMixin:
    """Delete by marking the row deleted, not by removing it.

    ``BaseModel`` carries ``is_deleted`` and every queryset filters on it, but
    DRF's ``perform_destroy`` calls ``instance.delete()``, which issues a real
    SQL DELETE, which takes the evidence with it — an approved record, and the
    history explaining who approved it, must survive someone tidying up.

    Set ``hard_delete = True`` on a subclass for the rare row that genuinely
    should disappear — a stored file with a blob behind it, for instance.
    """

    #: Override on a subclass to issue a real DELETE.
    hard_delete = False

    def perform_destroy(self, instance):
        if self.hard_delete or not hasattr(instance, "is_deleted"):
            instance.delete()
            return

        instance.is_deleted = True
        instance.is_active = False
        # Goes through save(), so the audit stamp and the history row both
        # record who deleted it and when.
        instance.save(update_fields=["is_deleted", "is_active", "updated_at"])


class LoggedCreateMixin:
    """Logs the incoming body on POST and offers a ``pre_create`` hook.

    The line is at DEBUG and the body is redacted first, with the same key set
    the request log masks. At INFO it put every create body — names, phone
    numbers, addresses, and any credential a sensitive key list missed — into
    production logs, which usually outlive and out-travel the database. The
    request log already keeps a redacted copy of the body when tracking is on,
    so the INFO line was a second, less careful copy.
    """

    def post(self, request, *args, **kwargs):
        if logger.isEnabledFor(logging.DEBUG):
            data = request.data
            if hasattr(data, "dict"):  # QueryDict from a form or multipart body
                data = data.dict()
            logger.debug(
                "[%s] Request data: %s",
                self.__class__.__name__, redact_structure(data, request.path),
            )
        self.pre_create(request, *args, **kwargs)
        return super().post(request, *args, **kwargs)

    def pre_create(self, request, *args, **kwargs):
        """Hook for pre-processing before the create runs. Override as needed."""


# -- the view classes -------------------------------------------------------

class CustomModelViewSet(ProjectPermissionMixin, SwaggerSafeQuerysetMixin,
                         SoftDeleteMixin, ModelViewSet):
    pagination_class = CustomPageNumberPagination

    def perform_create(self, serializer):
        serializer.save(created_at=timezone.now())

    def perform_update(self, serializer):
        serializer.save(updated_at=timezone.now())


class CustomListAPIView(ProjectPermissionMixin, SwaggerSafeQuerysetMixin,
                        EnvelopeListMixin, ListAPIView):
    http_method_names = ["get", "head", "options"]
    pagination_class = CustomPageNumberPagination


class CustomWOPListAPIView(ProjectPermissionMixin, SwaggerSafeQuerysetMixin,
                           EnvelopeListMixin, ListAPIView):
    """List without pagination ("WOP")."""

    http_method_names = ["get", "head", "options"]
    pagination_class = None


class CustomRetrieveAPIView(ProjectPermissionMixin, SwaggerSafeQuerysetMixin,
                            RetrieveAPIView):
    http_method_names = ["get", "head", "options"]


class CustomCreateAPIView(ProjectPermissionMixin, SwaggerSafeQuerysetMixin,
                          LoggedCreateMixin, CreateAPIView):
    http_method_names = ["post"]


class CustomUpdateAPIView(ProjectPermissionMixin, SwaggerSafeQuerysetMixin,
                          UpdateAPIView):
    http_method_names = ["put", "patch"]


class CustomDestroyAPIView(ProjectPermissionMixin, SwaggerSafeQuerysetMixin,
                           SoftDeleteMixin, DestroyAPIView):
    http_method_names = ["delete"]
