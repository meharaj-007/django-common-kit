"""Which tenant a row belongs to (PRD §3.5).

Every table the package owns carries a nullable ``tenant_id``. The package does
not know what a tenant is — an organisation, a workspace, a business — so it
never holds a foreign key to one: the column is a bare UUID, and a project with
no tenants leaves it empty and pays nothing.

A project fills it through two settings, resolved at first use:

- ``TENANT["INSTANCE_ATTRIBUTE"]`` names the attribute on the project's own
  models that holds their tenant's id (``"organization_id"``, say). A history
  row, a status transition or a file attached to such a row takes its tenant
  from the row it is about — the question "what happened in this tenant" is a
  question about the data, not about whoever happened to be signed in.
- ``TENANT["RESOLVER"]`` is a dotted path to ``callable(request) -> id | None``,
  for rows that are about a request rather than a row: request logs, visits, a
  contact form, a file attached to nothing.

A row about another row never falls back to the resolver: history of a user,
or a file attached to one, belongs to no tenant when the user has none — not
to whichever tenant the person making the change happened to be working in.

Both unset — the default — and every ``tenant_id`` stays ``NULL``.
"""

import logging
import uuid

from django_common_kit.conf import app_settings

logger = logging.getLogger(__name__)


def _as_uuid(value):
    """A tenant id as a ``UUID``: a model instance gives its pk; anything that
    will not parse is treated as no tenant rather than an error on the write."""
    if value is None:
        return None
    value = getattr(value, "pk", value)
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        logger.warning("Ignoring a tenant id that is not a UUID: %r", value)
        return None


def is_configured():
    """Whether the project uses tenants at all. When it does not, nothing is
    worth looking up — least of all a related row, which costs a query."""
    return bool(
        app_settings.get("TENANT", "INSTANCE_ATTRIBUTE") or app_settings.get("TENANT", "RESOLVER")
    )


def resolve_tenant(instance=None, request=None):
    """The tenant for a row about ``instance``, or about ``request``.

    With an ``instance``: its own ``tenant_id`` (a package row), else the
    project's ``INSTANCE_ATTRIBUTE`` on it, else ``None``. Without one: the
    ``RESOLVER`` applied to ``request``, or to the request in flight. Never
    raises — a resolver that fails must not fail the write it was labelling.
    """
    if instance is not None:
        own = getattr(instance, "tenant_id", None)
        if own:
            return _as_uuid(own)
        attribute = app_settings.get("TENANT", "INSTANCE_ATTRIBUTE")
        value = getattr(instance, attribute, None) if attribute else None
        return _as_uuid(value) if value else None

    resolver = app_settings.hook("TENANT", "RESOLVER")
    if resolver is None:
        return None
    if request is None:
        from django_common_kit.request_context import get_current_request

        request = get_current_request()
    if request is None:
        return None
    try:
        return _as_uuid(resolver(request))
    except Exception as exc:  # noqa: BLE001
        logger.warning("TENANT RESOLVER failed: %s", exc)
        return None
