"""Admin for the package's own tables (PRD §12).

Registered here, unconditionally. A project that wants a different admin for
either model unregisters and re-registers in its own ``admin.py``; that is the
Django idiom and it is cheaper than a setting.
"""

from django.contrib import admin

from django_common_kit.models import ModelHistory, ParameterModel


def _secret_column(field):
    """A changelist column for an encrypted field: the masked secret, and a
    word instead of an error for a row no key opens (§17.6)."""
    from django_common_kit.crypto.keys import DecryptionError, EncryptionNotConfigured, mask

    def column(obj):
        try:
            value = getattr(obj, field.attname)
        except (DecryptionError, EncryptionNotConfigured):
            return "(unreadable)"
        return mask(value) or "-"

    column.short_description = field.verbose_name
    column.__name__ = f"{field.name}_masked"
    return column


class BaseModelAdmin(admin.ModelAdmin):
    """Audit columns read-only, soft-deleted rows filterable, actor stamped.

    The base for a project's admin classes, so ``readonly_fields`` is declared
    once and ``created_by`` is never forgotten. An encrypted field named in
    ``list_display`` shows its mask, never the secret.
    """

    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")
    list_filter = ("is_active", "is_deleted")

    def get_list_display(self, request):
        from django_common_kit.crypto.fields import encrypted_fields

        secrets = {field.name: field for field in encrypted_fields(self.model)}
        return [
            _secret_column(secrets[name]) if isinstance(name, str) and name in secrets else name
            for name in super().get_list_display(request)
        ]

    def save_model(self, request, obj, form, change):
        if not change and hasattr(obj, "created_by_id") and obj.created_by_id is None:
            obj.created_by = request.user
        if hasattr(obj, "updated_by_id"):
            obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(ParameterModel)
class ParameterAdmin(BaseModelAdmin):
    list_display = ("key", "parameter_type", "value", "category", "is_system", "is_active")
    list_filter = ("parameter_type", "category", "is_system", "is_active")
    search_fields = ("key", "description", "category")
    ordering = ("category", "key")
    fieldsets = (
        (None, {"fields": ("key", "parameter_type", "category", "description", "is_system")}),
        ("Value", {"fields": ("value_text", "value_integer", "value_float", "value_boolean", "value_json")}),
        ("Status", {"fields": ("is_active", "is_deleted")}),
        ("Audit", {"fields": ("id", "created_at", "created_by", "updated_at", "updated_by")}),
    )


@admin.register(ModelHistory)
class ModelHistoryAdmin(BaseModelAdmin):
    """Read-only. A trail that can be edited is not a trail."""

    list_display = ("created_at", "content_type", "object_id", "action", "changed_by", "ip_address")
    list_filter = ("action", "content_type")
    search_fields = ("object_id", "change_reason", "changed_by__email")
    date_hierarchy = "created_at"
    readonly_fields = tuple(
        field.name for field in ModelHistory._meta.get_fields() if hasattr(field, "column")
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


from django_common_kit.models import (  # noqa: E402
    BlockedIPModel,
    CommonFileModel,
    ContactUsModel,
    IPTrackingModel,
    PlatformNoticeDismissalModel,
    PlatformNoticeModel,
    RequestLog,
    ShortLinkModel,
    StatusTransitionModel,
)


class ReadOnlyAdmin(BaseModelAdmin):
    """Telemetry is read, never edited."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(RequestLog)
class RequestLogAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "method", "endpoint", "status_code", "user", "ip_address", "response_time")
    list_filter = ("method", "status_code")
    search_fields = ("endpoint", "trace_id", "ip_address", "user__email")
    date_hierarchy = "created_at"


@admin.register(IPTrackingModel)
class IPTrackingAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "ip_address", "endpoint", "country", "city", "device_type", "browser")
    list_filter = ("country", "device_type", "is_mobile")
    search_fields = ("ip_address", "endpoint", "city", "isp")
    date_hierarchy = "created_at"


@admin.register(BlockedIPModel)
class BlockedIPAdmin(BaseModelAdmin):
    """Editable: unticking ``is_active`` here is how an operator lifts a ban,
    and the blocker re-reads the table every TTL so it takes effect without a
    deploy."""

    list_display = ("ip_address", "is_active", "attempts", "reason", "first_attempt", "last_attempt", "blocked_at")
    list_filter = ("is_active",)
    search_fields = ("ip_address", "reason")
    readonly_fields = BaseModelAdmin.readonly_fields + ("first_attempt", "last_attempt")


@admin.register(ContactUsModel)
class ContactUsAdmin(BaseModelAdmin):
    list_display = ("created_at", "name", "email", "subject", "is_checked")
    list_filter = ("is_checked",)
    search_fields = ("name", "email", "subject", "message")


@admin.register(StatusTransitionModel)
class StatusTransitionAdmin(ReadOnlyAdmin):
    list_display = ("timestamp", "content_type", "object_id", "previous_status", "new_status", "transition_source", "changed_by")
    list_filter = ("transition_source", "content_type", "new_status")
    search_fields = ("object_id", "transition_reason", "notes")
    date_hierarchy = "timestamp"


@admin.register(CommonFileModel)
class CommonFileAdmin(BaseModelAdmin):
    list_display = ("created_at", "title", "original_filename", "tag", "mime_type", "file_size", "uploaded_by")
    list_filter = ("tag", "mime_type")
    search_fields = ("title", "original_filename", "description")


@admin.register(ShortLinkModel)
class ShortLinkAdmin(BaseModelAdmin):
    list_display = ("slug", "target_url", "purpose", "click_count", "last_clicked_at", "expires_at", "is_active")
    list_filter = ("purpose", "is_active")
    search_fields = ("slug", "target_url")
    readonly_fields = BaseModelAdmin.readonly_fields + ("click_count", "last_clicked_at")


@admin.register(PlatformNoticeModel)
class PlatformNoticeAdmin(BaseModelAdmin):
    """Leave ``tenant_id`` empty for a notice every tenant sees."""

    list_display = ("title", "severity", "audience", "surface", "starts_at", "ends_at", "priority", "is_dismissible", "is_active")
    list_filter = ("severity", "audience", "surface", "is_dismissible", "is_active", "is_deleted")
    search_fields = ("title", "body")


@admin.register(PlatformNoticeDismissalModel)
class PlatformNoticeDismissalAdmin(ReadOnlyAdmin):
    """Deleting a row here shows the notice to that user again."""

    list_display = ("created_at", "notice", "user")
    search_fields = ("notice__title",)
    date_hierarchy = "created_at"
