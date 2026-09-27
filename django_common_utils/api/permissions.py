"""Permission classes — mechanism only (PRD §5.2, §2).

The classes here read only Django's own flags — ``is_superuser``, ``is_staff``,
``is_active`` — and the ``created_by`` column ``BaseModel`` gives every row.
Anything role-shaped ("employee", "crew", "customer") is vocabulary (§2) and
belongs to the project; ``user_type_permission`` below builds it from the
project's own values.

Two rules every class follows:

- ``request.user`` is read with ``getattr``. With DRF's
  ``UNAUTHENTICATED_USER = None``, ``request.user.is_authenticated`` raises
  ``AttributeError`` on an anonymous request, and an ``AttributeError`` inside
  ``has_permission`` becomes a 500 where a 401 was meant.
- ``is_admin`` is read with ``getattr(..., False)``. It is a property some of the
  user models define and others do not, so the bare attribute access is a 500
  waiting for the first project whose model lacks it.
"""

from rest_framework.permissions import BasePermission


def _active_user(request):
    """The authenticated user, or ``None``. Never raises."""
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return user


class IsAuthenticatedActive(BasePermission):
    """Authenticated *and* not deactivated.

    DRF's ``IsAuthenticated`` passes for a user whose ``is_active`` is False when
    authentication came from a token that was issued before they were disabled —
    the token is still valid and nothing re-checks the flag.
    """

    def has_permission(self, request, view):
        user = _active_user(request)
        return bool(user and getattr(user, "is_active", True))


class IsSuperUser(BasePermission):
    def has_permission(self, request, view):
        user = _active_user(request)
        return bool(user and user.is_superuser)


class AdminUserPermission(BasePermission):
    def has_permission(self, request, view):
        user = _active_user(request)
        return bool(user and (user.is_superuser or getattr(user, "is_admin", False)))


class StaffUserPermission(BasePermission):
    def has_permission(self, request, view):
        user = _active_user(request)
        return bool(user and user.is_staff)


class AdminOrStaffUserPermission(BasePermission):
    def has_permission(self, request, view):
        user = _active_user(request)
        return bool(user and (user.is_superuser or user.is_staff))


class IsOwnerOrReadOnly(BasePermission):
    """Write only what you created; read anything.

    ``owner_field`` names the attribute holding the owner — ``created_by`` by
    default, because that is the column ``BaseModel`` gives every row. Subclass
    to point it elsewhere rather than editing this.
    """

    owner_field = "created_by"
    safe_methods = ("GET", "HEAD", "OPTIONS")

    def has_object_permission(self, request, view, obj):
        if request.method in self.safe_methods:
            return True
        user = _active_user(request)
        if user is None:
            return False
        return getattr(obj, self.owner_field, None) == user


def user_type_permission(*user_types, allow_superuser=True, attribute="user_type"):
    """Build a permission class from the project's own role values.

    The package cannot know that ``EMPLOYEE`` is a role here and ``CREW`` is one
    there (§2), but the *shape* of the check is always the same. A project
    writes::

        from user_control.constants import USER_TYPE_ADMIN, USER_TYPE_EMPLOYEE

        AdminOrEmployeePermission = user_type_permission(
            USER_TYPE_ADMIN, USER_TYPE_EMPLOYEE
        )

    and keeps its vocabulary in the app that owns it.
    """
    allowed = frozenset(user_types)

    class _UserTypePermission(BasePermission):
        def has_permission(self, request, view):
            user = _active_user(request)
            if user is None:
                return False
            if allow_superuser and user.is_superuser:
                return True
            return getattr(user, attribute, None) in allowed

    _UserTypePermission.__name__ = "UserTypePermission({})".format(
        ", ".join(str(value) for value in sorted(allowed, key=str))
    )
    return _UserTypePermission
