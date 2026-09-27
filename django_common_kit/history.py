"""The change-history mechanism (PRD §6).

``HistoryMixin`` decides *what* a change looks like — which fields count, how a
value is written into JSON, what a diff is. ``signals.py`` decides *when* to
record one, and ``models.ModelHistory`` is the row it writes. The three are
separate so a project can call ``create_history_entry`` by hand with a
``change_reason`` from a service, without going through a save.

``get_history_exclude_fields`` returns a *copy* of the class-level list.
Returning the attribute itself and letting a caller ``extend`` it grows the
class attribute on every call for the life of the process.

An ``EncryptedTextField`` (§17.6) is never in a snapshot, whatever the settings
say. When its secret changes, the diff records the field with ``"***"`` on each
side that holds one — who changed a secret and when, never what it was or
became.
"""

import json
import logging
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Dict, Optional

from django.db import models

from django_common_kit.conf import app_settings

logger = logging.getLogger(__name__)

#: Columns the base itself writes on every save, and so never worth recording.
_ALWAYS_EXCLUDED = ("created_at", "updated_at", "created_by", "updated_by")

#: What a diff records for a secret that is set (§17.6).
SECRET_PLACEHOLDER = "***"


def make_json_safe(value):
    """Convert a value to something the stdlib JSON encoder accepts.

    Anything writing a model field's value into a ``JSONField`` needs this, and
    the need is not specific to history: psycopg2 serialises a ``JSONField`` with
    the stdlib encoder, which refuses ``Decimal``, ``datetime`` and ``UUID`` —
    all ordinary Django field types. One implementation, so a second caller
    cannot make a slightly different choice about how a Decimal is stored.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, models.Model):
        return str(value.pk)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): make_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [make_json_safe(item) for item in value]
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)



def _truncate(value):
    """Cap a stored string at ``HISTORY["MAX_VALUE_LENGTH"]``.

    A ``TextField`` will happily accept a 5MB pasted document on every edit; the
    trail exists to answer "what changed", not to be a second copy of the table.
    Only strings are cut — a nested structure is stored whole, since cutting a
    JSON value in the middle produces something nothing can parse.
    """
    limit = app_settings.get("HISTORY", "MAX_VALUE_LENGTH")
    if limit and isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value


class HistoryMixin:
    """Gives a model a snapshot and a diff. Mixed into ``BaseModel``.

    Set ``_track_history = False`` on a model to switch recording off — and do
    so on every telemetry table (§6.3). Add field names to
    ``_history_exclude_fields`` to keep a column out of the trail.
    """

    _track_history = True
    _history_exclude_fields = []

    def get_history_enabled(self) -> bool:
        if not app_settings.get("HISTORY", "ENABLED"):
            return False
        return getattr(self, "_track_history", True)

    def get_history_exclude_fields(self) -> list:
        # A copy, never the class attribute itself — see the module docstring.
        excluded = list(getattr(self, "_history_exclude_fields", []))
        excluded.extend(app_settings.get("HISTORY", "EXCLUDE_FIELDS"))
        excluded.extend(_ALWAYS_EXCLUDED)
        return excluded

    def get_history_fields(self) -> list:
        """Forward fields to track: everything but reverse relations, generic
        relations, many-to-manys and exclusions.

        A generic relation is left out because reading it loads the related
        row — a query on every save — to record what its ``content_type`` and
        ``object_id`` columns already hold.

        A many-to-many is left out because a save cannot see it. Its rows are
        written after the save, by ``add``/``set``, which fire ``m2m_changed``
        and not ``pre_save``/``post_save``; reading the pks here would cost a
        query per field per save to record the membership *before* the change
        the caller is about to make. The attribute itself is a manager, which
        serialised as the text ``"app.Model.None"``.
        """
        from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
        from django.db.models.fields.related import ManyToManyRel, ManyToOneRel, OneToOneRel

        from django_common_kit.crypto.fields import EncryptedTextField

        excluded = set(self.get_history_exclude_fields())
        names = []
        for field in self._meta.get_fields():
            if isinstance(field, (ManyToOneRel, OneToOneRel, ManyToManyRel, GenericForeignKey, GenericRelation)):
                continue
            if isinstance(field, EncryptedTextField):
                continue
            if field.many_to_many:
                continue
            if field.name in excluded:
                continue
            names.append(field.name)
        return names

    def get_object_snapshot(self) -> Dict[str, Any]:
        """``{field_name: json_safe_value}`` for every tracked field."""
        snapshot = {}
        for name in self.get_history_fields():
            try:
                snapshot[name] = _truncate(make_json_safe(getattr(self, name, None)))
            except Exception as exc:  # noqa: BLE001 - one bad field must not lose the row
                logger.warning(
                    "Could not snapshot %s.%s: %s", type(self).__name__, name, exc,
                )
                snapshot[name] = None
        return snapshot

    def get_changed_fields(self, old_instance) -> Optional[Dict[str, Dict[str, Any]]]:
        """``{field: {"before": ..., "after": ...}}`` against ``old_instance``,
        or ``None`` when nothing differs. Related objects compare by pk.

        Values that differ in Python but would be recorded identically are not
        a change. An empty file field is the case that needs it: on the
        instance that created the row its ``FieldFile`` is named ``None``, on
        the row read back it is named ``''``, and both are stored as ``''``, so
        the next save of that instance recorded ``'' -> ''``. The comparison
        is made before truncation, so two long values that differ only past
        the cut still count.
        """
        if not old_instance:
            return None

        changes = {}
        for name in self.get_history_fields():
            try:
                before = getattr(old_instance, name, None)
                after = getattr(self, name, None)
                if isinstance(before, models.Model) and isinstance(after, models.Model):
                    differs = before.pk != after.pk
                else:
                    differs = before != after
                if not differs:
                    continue
                before, after = make_json_safe(before), make_json_safe(after)
                if before != after:
                    changes[name] = {"before": _truncate(before), "after": _truncate(after)}
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Could not compare %s.%s: %s", type(self).__name__, name, exc,
                )
        changes.update(self._get_changed_secrets(old_instance))
        return changes or None

    def _get_changed_secrets(self, old_instance) -> Dict[str, Dict[str, Any]]:
        """The encrypted fields whose secret changed, each side ``"***"`` when
        it holds one and its empty value when it does not."""
        from django_common_kit.crypto.fields import encrypted_fields, raw_value, secret_changed

        excluded = set(self.get_history_exclude_fields())
        changes = {}
        for field in encrypted_fields(type(self)):
            if field.name in excluded:
                continue
            try:
                if not secret_changed(old_instance, self, field):
                    continue
                before, after = raw_value(old_instance, field), raw_value(self, field)
                changes[field.name] = {
                    "before": SECRET_PLACEHOLDER if before else before,
                    "after": SECRET_PLACEHOLDER if after else after,
                }
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Could not compare %s.%s: %s", type(self).__name__, field.name, exc,
                )
        return changes


def create_history_entry(
    instance,
    action: str,
    changed_by=None,
    field_changes: Optional[Dict] = None,
    object_snapshot_before: Optional[Dict] = None,
    object_snapshot_after: Optional[Dict] = None,
    request=None,
    change_reason: Optional[str] = None,
):
    """Write one ``ModelHistory`` row. Never raises: a failure to record a
    change must not roll back the change it was recording."""
    from django.contrib.contenttypes.models import ContentType

    from django_common_kit.models import ModelHistory
    from django_common_kit.request_context import (
        get_client_ip,
        get_correlation_id,
        get_current_request,
        get_current_user,
        get_user_agent,
    )
    from django_common_kit.tenancy import resolve_tenant

    try:
        if changed_by is None:
            changed_by = get_current_user()
        if request is None:
            request = get_current_request()

        ModelHistory.objects.create(
            content_type=ContentType.objects.get_for_model(type(instance)),
            object_id=str(instance.pk),
            action=action,
            changed_by=changed_by,
            field_changes=field_changes or {},
            object_snapshot_before=object_snapshot_before,
            object_snapshot_after=object_snapshot_after,
            change_reason=change_reason,
            ip_address=get_client_ip(request) if request else None,
            user_agent=get_user_agent(request) if request else None,
            correlation_id=get_correlation_id(request) if request else None,
            tenant_id=resolve_tenant(instance=instance, request=request),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Could not record history for %s #%s: %s",
            type(instance).__name__, getattr(instance, "pk", "?"), exc, exc_info=True,
        )


def get_model_history(instance):
    """Every history row for ``instance``, newest first."""
    from django.contrib.contenttypes.models import ContentType

    from django_common_kit.models import ModelHistory

    return ModelHistory.objects.filter(
        content_type=ContentType.objects.get_for_model(type(instance)),
        object_id=str(instance.pk),
    ).order_by("-created_at")
