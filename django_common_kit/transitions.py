"""Recording a status change (PRD §6.4).

``StatusTransitionModel`` is the row; this is the one call that writes it, so a
project does not keep its own helper beside the package's table. The row is
written only when a caller says so: which changes count, and who or what made
them, is the project's business (§2), and a signal cannot tell a customer
accepting a quote from a script backfilling one.

Unlike a history row, a failure here raises. The caller made the call inside
the service that changed the status, usually in its transaction, and is the one
placed to decide whether the change stands without its record.

Nothing is imported from Django's model layer at module scope: a project's own
``models.py`` re-exports this helper, and is imported before the app registry
is ready.
"""

#: ``transition_source`` is a ``varchar(20)``. SQLite does not enforce that and
#: PostgreSQL rejects the insert, so the length is checked here, on both.
_SOURCE_MAX_LENGTH = 20


def _plain(value):
    # A TextChoices member is a str subclass; store its value, not its repr.
    return getattr(value, "value", value)


def track_status_transition(
    instance,
    new_status,
    transition_source,
    transition_reason=None,
    notes=None,
    changed_by=None,
    previous_status=None,
    field_name="status",
):
    """Write one ``StatusTransitionModel`` row for ``instance`` and return it.

    Call it *before* assigning the new status: ``previous_status`` defaults to
    the instance's current value of ``field_name``, which after the assignment
    is already the new one. ``changed_by`` defaults to the signed-in user of
    the request in flight, and to nobody outside a request (§6.2).
    ``transition_source`` is the project's own word (``"customer"``,
    ``"system"``), at most 20 characters.
    """
    from django.contrib.contenttypes.models import ContentType

    from django_common_kit.models import StatusTransitionModel
    from django_common_kit.request_context import get_current_user
    from django_common_kit.tenancy import is_configured, resolve_tenant

    if previous_status is None:
        previous_status = getattr(instance, field_name, None)
    transition_source = _plain(transition_source)
    if not transition_source or len(str(transition_source)) > _SOURCE_MAX_LENGTH:
        raise ValueError(
            f"transition_source must be 1–{_SOURCE_MAX_LENGTH} characters, got {transition_source!r}"
        )
    if changed_by is None:
        changed_by = get_current_user()

    return StatusTransitionModel.objects.create(
        content_type=ContentType.objects.get_for_model(type(instance)),
        object_id=str(instance.pk),
        field_name=field_name,
        previous_status=_plain(previous_status),
        new_status=_plain(new_status),
        changed_by=changed_by,
        transition_source=transition_source,
        transition_reason=transition_reason,
        notes=notes,
        # From the instance in hand; left empty, the model would load it again
        # through the generic relation to find the same tenant.
        tenant_id=resolve_tenant(instance=instance) if is_configured() else None,
    )


def get_status_transitions(instance, field_name=None):
    """Every transition recorded for ``instance``, newest first; only those of
    ``field_name`` when given."""
    from django.contrib.contenttypes.models import ContentType

    from django_common_kit.models import StatusTransitionModel

    rows = StatusTransitionModel.objects.filter(
        content_type=ContentType.objects.get_for_model(type(instance)),
        object_id=str(instance.pk),
    )
    if field_name is not None:
        rows = rows.filter(field_name=field_name)
    return rows.order_by("-timestamp")
