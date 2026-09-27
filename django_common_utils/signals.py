"""Receivers (PRD §6, §7, §16). Connected from ``apps.ready()`` by importing this module.

Three families:

- **History.** ``pre_save`` fetches the old row and snapshots it; ``post_save``
  diffs and writes. ``pre_delete`` snapshots; ``post_delete`` writes. The
  thread-local guard stops ``ModelHistory``'s own save from recording itself.
- **Parameter cache.** Any save or delete of a ``ParameterModel`` drops the
  cache. A receiver rather than an overridden ``save()``: it cannot be bypassed
  by a subclass forgetting ``super()``, and one extra cache delete on a table
  edited a few times a month costs nothing.
- **Notice cache.** The same, for ``PlatformNoticeModel`` (§16).

Every receiver is connected without ``sender``, and filters on
``isinstance(instance, HistoryMixin)``. A ``sender=`` list would have to name
every model in every consuming project.
"""

import logging

from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from django_common_utils.history import HistoryMixin, create_history_entry
from django_common_utils.request_context import (
    get_current_request,
    get_current_user,
    is_tracking_history,
    set_tracking_history,
)

logger = logging.getLogger(__name__)


def _tracked(instance):
    return (
        not is_tracking_history()
        and isinstance(instance, HistoryMixin)
        # A model from migration state, saved by a data migration: its class
        # is built from the migration files (module ``__fake__``) and still
        # inherits HistoryMixin through the bases they record. On a fresh
        # database a project's data migration can run before `model_history`
        # exists, and one failed insert breaks the migration's transaction.
        # History is for what people and the running app change.
        and type(instance).__module__ != "__fake__"
        and instance.get_history_enabled()
    )


@receiver(pre_save, dispatch_uid="django_common_utils.history.pre_save")
def pre_save_history(sender, instance, **kwargs):
    if not _tracked(instance):
        return

    instance._history_old_instance = None
    instance._history_snapshot_before = None
    # Only an update has a before. `_state.adding` is the reliable test — a
    # UUID pk is set before the first save, so `if instance.pk` is not.
    if instance._state.adding:
        return
    try:
        old = sender._default_manager.get(pk=instance.pk)
    except sender.DoesNotExist:
        return
    instance._history_old_instance = old
    instance._history_snapshot_before = old.get_object_snapshot()


@receiver(post_save, dispatch_uid="django_common_utils.history.post_save")
def post_save_history(sender, instance, created, **kwargs):
    if not _tracked(instance):
        return

    old = getattr(instance, "_history_old_instance", None)
    snapshot_before = getattr(instance, "_history_snapshot_before", None)

    field_changes = None
    if not created and old is not None:
        field_changes = instance.get_changed_fields(old)
        if not field_changes:
            # A save that changed nothing tracked is not a change.
            _cleanup(instance)
            return

    set_tracking_history(True)
    try:
        create_history_entry(
            instance=instance,
            action="create" if created else "update",
            changed_by=get_current_user(),
            field_changes=field_changes,
            object_snapshot_before=snapshot_before,
            object_snapshot_after=instance.get_object_snapshot(),
            request=get_current_request(),
        )
    finally:
        set_tracking_history(False)
        _cleanup(instance)


@receiver(pre_delete, dispatch_uid="django_common_utils.history.pre_delete")
def pre_delete_history(sender, instance, **kwargs):
    if not _tracked(instance):
        return
    try:
        instance._history_delete_snapshot_before = instance.get_object_snapshot()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not snapshot %s before delete: %s", sender.__name__, exc)
        instance._history_delete_snapshot_before = None


@receiver(post_delete, dispatch_uid="django_common_utils.history.post_delete")
def post_delete_history(sender, instance, **kwargs):
    if not _tracked(instance):
        return

    set_tracking_history(True)
    try:
        create_history_entry(
            instance=instance,
            action="delete",
            changed_by=get_current_user(),
            object_snapshot_before=getattr(instance, "_history_delete_snapshot_before", None),
            object_snapshot_after=None,
            request=get_current_request(),
        )
    finally:
        set_tracking_history(False)
        if hasattr(instance, "_history_delete_snapshot_before"):
            del instance._history_delete_snapshot_before


def _cleanup(instance):
    for name in ("_history_old_instance", "_history_snapshot_before"):
        if hasattr(instance, name):
            delattr(instance, name)


# -- parameter cache --------------------------------------------------------

@receiver(post_save, sender="common_control.ParameterModel",
          dispatch_uid="django_common_utils.parameters.post_save")
@receiver(post_delete, sender="common_control.ParameterModel",
          dispatch_uid="django_common_utils.parameters.post_delete")
def invalidate_parameter_cache(sender, instance, **kwargs):
    from django_common_utils.parameters.cache import ParameterCache

    ParameterCache.invalidate_cache()


# -- notice cache -----------------------------------------------------------

@receiver(post_save, sender="common_control.PlatformNoticeModel",
          dispatch_uid="django_common_utils.notices.post_save")
@receiver(post_delete, sender="common_control.PlatformNoticeModel",
          dispatch_uid="django_common_utils.notices.post_delete")
def invalidate_notice_cache(sender, instance, **kwargs):
    from django.db import transaction

    from django_common_utils.notices.service import invalidate_cache

    # Now, and again once the transaction commits: a read between the two
    # would otherwise put the old list back for a whole TTL.
    invalidate_cache()
    transaction.on_commit(invalidate_cache)
