"""The shared tables (PRD §3.4, §6–§10, §16).

Every ``db_table`` is fixed and must not change: the migrations are marked
``initial`` so that a project which already has a table of that name adopts it
with ``migrate --fake-initial`` (§13). The same rule decides where a column
goes — the initial migration holds the table's original shape, and any column
added later lives in a later, non-initial migration.
"""

import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from django_common_utils.history import HistoryMixin
from django_common_utils.storage import MediaStorage


class BaseModel(HistoryMixin, models.Model):
    """The seven columns every row carries (§3.4).

    ``created_by``/``updated_by`` point at ``settings.AUTH_USER_MODEL``. A
    project whose models currently point at a literal ``'app.UserModel'`` gets
    a no-op ``AlterField`` per model when it switches to this base, because
    Django records a swappable dependency differently (§3.4).

    ``is_active`` is indexed: every list filters on it and the index is cheap.
    """

    id = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True, primary_key=True,
    )
    is_active = models.BooleanField(default=True, db_index=True)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="%(class)s_created_by",
        on_delete=models.SET_NULL, null=True, blank=True,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="%(class)s_updated_by",
        on_delete=models.SET_NULL, null=True, blank=True,
    )

    objects = models.Manager()

    class Meta:
        abstract = True

    # History configuration lives on the class, not in Meta — Django rejects
    # custom Meta attributes. Overridable per model; see HistoryMixin.
    _track_history = True
    _history_exclude_fields = ["updated_at", "updated_by"]


class TenantMixin(models.Model):
    """``tenant_id`` on every table the package owns (§3.5, ``tenancy.py``).

    A bare, nullable UUID — never a foreign key, because the package does not
    know what a project's tenant is. Filled on save from ``resolve_tenant``
    when the caller left it empty; ``_tenant_source()`` names the row the
    tenant is read from (``None``: the request in flight). A table whose rows
    are global — an IP ban, a system parameter — sets ``_fill_tenant = False``.
    """

    tenant_id = models.UUIDField(
        null=True, blank=True, db_index=True,
        help_text="The tenant this row belongs to, for a multi-tenant project.",
    )

    _fill_tenant = True

    class Meta:
        abstract = True

    def _tenant_source(self):
        return None

    def save(self, *args, **kwargs):
        if self.tenant_id is None and self._fill_tenant:
            from django_common_utils.tenancy import is_configured, resolve_tenant

            # _tenant_source() may load a related row; skip it when no tenant
            # could come of it.
            if is_configured():
                self.tenant_id = resolve_tenant(instance=self._tenant_source())
            update_fields = kwargs.get("update_fields")
            if self.tenant_id is not None and update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "tenant_id"}
        super().save(*args, **kwargs)


class ParameterType(models.TextChoices):
    TEXT = "text", "Text"
    INTEGER = "integer", "Integer"
    FLOAT = "float", "Float"
    BOOLEAN = "boolean", "Boolean"
    JSON = "json", "JSON"


#: Parameter type -> the column that holds its value. One mapping, used by the
#: model, the cache and the seed command, so they cannot disagree.
VALUE_COLUMNS = {
    ParameterType.TEXT: "value_text",
    ParameterType.INTEGER: "value_integer",
    ParameterType.FLOAT: "value_float",
    ParameterType.BOOLEAN: "value_boolean",
    ParameterType.JSON: "value_json",
}


class ParameterModel(TenantMixin, BaseModel):
    """A typed key/value row (§7). Table ``parameters``.

    One column per type rather than one text column and a cast, because a
    ``JSONField`` and an ``IntegerField`` are queryable in ways a string is not.

    Parameter *names* are project vocabulary; this package ships none.
    """

    # A system parameter is global unless the project says otherwise.
    _fill_tenant = False

    key = models.CharField(
        max_length=255, unique=True, help_text="Unique parameter key identifier",
    )
    value_text = models.TextField(null=True, blank=True)
    value_integer = models.IntegerField(null=True, blank=True)
    value_float = models.FloatField(null=True, blank=True)
    value_boolean = models.BooleanField(null=True, blank=True)
    value_json = models.JSONField(null=True, blank=True)
    parameter_type = models.CharField(
        max_length=20, choices=ParameterType.choices, default=ParameterType.TEXT,
    )
    description = models.TextField(null=True, blank=True)
    category = models.CharField(max_length=100, null=True, blank=True)
    is_system = models.BooleanField(
        default=False,
        help_text="A system parameter is loaded into the cache and is not for users to edit.",
    )

    class Meta:
        db_table = "parameters"
        verbose_name = "Parameter"
        verbose_name_plural = "Parameters"
        ordering = ["category", "key"]
        indexes = [
            models.Index(fields=["key"]),
            models.Index(fields=["category"]),
            models.Index(fields=["parameter_type"]),
            models.Index(fields=["is_system"]),
        ]

    def __str__(self):
        return f"{self.category or 'General'} - {self.key}"

    @property
    def value_column(self):
        return VALUE_COLUMNS.get(self.parameter_type)

    @property
    def value(self):
        """The value, read from whichever column its type names."""
        column = self.value_column
        return getattr(self, column) if column else None

    def set_value(self, value):
        """Write ``value`` into the column its type names, cast to that type.

        The other value columns are cleared. A row whose type changed from
        ``text`` to ``integer`` otherwise keeps its old ``value_text`` forever,
        and the next person to read the raw row cannot tell which one is live.
        """
        for column in VALUE_COLUMNS.values():
            setattr(self, column, None)
        column = self.value_column
        if column is None or value is None:
            return
        caster = {
            "value_text": str,
            "value_integer": int,
            "value_float": float,
            "value_boolean": bool,
            "value_json": lambda item: item,
        }[column]
        setattr(self, column, caster(value))


class HistoryAction(models.TextChoices):
    CREATE = "create", "Create"
    UPDATE = "update", "Update"
    DELETE = "delete", "Delete"


class ModelHistory(TenantMixin, BaseModel):
    """One row per change to any tracked model (§6). Table ``model_history``.

    Recursion — the history row's own save firing the history receiver — is
    prevented by the thread-local guard in ``signals.py``, not here.
    """

    # Not tracked, for the same reason as every telemetry table: the trail
    # recording its own rows would double every write and never stop.
    _track_history = False
    # create_history_entry resolves it from the tracked row, not from here.
    _fill_tenant = False

    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE,
        help_text="The model type this history entry belongs to",
    )
    object_id = models.CharField(
        max_length=255, db_index=True,
        help_text="The ID of the object this history entry belongs to",
    )
    content_object = GenericForeignKey("content_type", "object_id")

    action = models.CharField(
        max_length=20, choices=HistoryAction.choices, db_index=True,
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="model_history_changes",
    )

    #: ``{"field_name": {"before": value, "after": value}, ...}``
    field_changes = models.JSONField(default=dict, blank=True)
    object_snapshot_before = models.JSONField(null=True, blank=True)
    object_snapshot_after = models.JSONField(null=True, blank=True)

    change_reason = models.TextField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    # Added in 0003, not in the initial migration (§13.3).
    correlation_id = models.CharField(
        max_length=64, null=True, blank=True, db_index=True,
        help_text="The X-Request-Id of the request that made this change.",
    )

    class Meta:
        db_table = "model_history"
        verbose_name = "Model History"
        verbose_name_plural = "Model Histories"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["content_type", "object_id"], name="model_history_object_idx"),
            models.Index(fields=["action"], name="model_history_action_idx"),
            models.Index(fields=["changed_by"], name="model_history_changed_by_idx"),
            models.Index(fields=["created_at"], name="model_history_created_at_idx"),
            models.Index(
                fields=["content_type", "object_id", "created_at"],
                name="model_history_lookup_idx",
            ),
            # "This tenant's history, newest first" — the list a tenant admin opens.
            models.Index(fields=["tenant_id", "-created_at"], name="model_history_tenant_idx"),
        ]

    def __str__(self):
        model_name = self.content_type.model if self.content_type_id else "Unknown"
        actor = getattr(self.changed_by, "email", None) if self.changed_by_id else "System"
        return f"{model_name} #{self.object_id} - {self.action} by {actor} at {self.created_at}"

    @property
    def changed_fields(self):
        return list(self.field_changes.keys())

    def get_field_change(self, field_name):
        return self.field_changes.get(field_name, {})



# ---------------------------------------------------------------------------
# Tracking tables (§8)
# ---------------------------------------------------------------------------
# Append-only telemetry. `_track_history = False` on every one: with it on, the
# retention sweep writes a ModelHistory snapshot of each row it purges — the
# sweep grows the database, preserves exactly the data it exists to erase, and
# blocks Django's fast-delete path (§6.3).


class RequestLog(TenantMixin, BaseModel):
    """One row per request, read by id or trace during an incident. Table
    ``request_logs``. Bodies are redacted before they are written, never at
    display time — see ``tracking/redaction.py``."""

    _track_history = False

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    endpoint = models.CharField(max_length=255, null=True, blank=True)
    method = models.CharField(max_length=10, default="")
    request_body = models.TextField(null=True, blank=True)
    status_code = models.IntegerField(null=True, blank=True)
    response = models.TextField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    trace_id = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    response_time = models.FloatField(
        help_text="Time taken to process request (in seconds)", null=True, blank=True,
    )
    error_message = models.TextField(null=True, blank=True)
    # Added in 0011, not in the initial migration (§13.3).
    query_string = models.CharField(
        max_length=2000, null=True, blank=True,
        help_text="Redacted query string — credentials in signed URLs are masked before the row is written.",
    )

    class Meta:
        db_table = "request_logs"
        verbose_name = "Request Log"
        verbose_name_plural = "Request Logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"], name="request_log_created_idx"),
            models.Index(fields=["endpoint", "created_at"], name="request_log_endpoint_idx"),
            models.Index(fields=["tenant_id", "-created_at"], name="request_log_tenant_idx"),
        ]

    def __str__(self):
        return f"{self.method} {self.endpoint} - {self.status_code} - {self.created_at}"



class BlockedIPModel(TenantMixin, BaseModel):
    """An IP that has probed for sensitive paths. Table ``blocked_ips``.

    **``is_active`` means *currently banned* here, not the soft-delete it means
    everywhere else.** The admin and the middleware both depend on it.
    """

    _track_history = False
    # A ban is on an address, across every tenant.
    _fill_tenant = False

    ip_address = models.CharField(max_length=45, unique=True)
    reason = models.CharField(max_length=255, default="Sensitive file access attempt")
    attempts = models.IntegerField(default=1)
    first_attempt = models.DateTimeField(auto_now_add=True)
    # Deliberately NOT auto_now. It was, and any save() then restamped it —
    # including an operator opening the change form to fix a typo in `reason`,
    # which destroyed the last-probe time this row exists to record. Only
    # IPBlockerMiddleware._record_attempt advances it.
    last_attempt = models.DateTimeField(default=timezone.now)
    # Added in 0012, not in the initial migration. When the ban was applied,
    # and the only thing IP_BLOCK_DURATION_SECONDS is measured from. Measuring
    # from `last_attempt` meant blocking a quiet IP by hand in the admin was
    # undone on its very next request.
    blocked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "blocked_ips"
        verbose_name = "Blocked IP"
        verbose_name_plural = "Blocked IPs"

    def save(self, *args, **kwargs):
        """A banned row always carries the clock its expiry is measured from.

        Enforced here rather than in the admin because not every write goes
        through ``ModelAdmin.save_model``: an import path calling ``save()``
        directly with ``is_active=True`` and a NULL ``blocked_at`` reads as a
        legacy row, and the middleware expires it on the next request.
        """
        if self.is_active and self.blocked_at is None:
            self.blocked_at = timezone.now()
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and "blocked_at" not in update_fields:
                kwargs["update_fields"] = list(update_fields) + ["blocked_at"]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.ip_address} - {'Blocked' if self.is_active else 'Unblocked'}"


class IPTrackingModel(TenantMixin, BaseModel):
    """One row per visit, read as an aggregate over a date range. Table
    ``ip_tracking``. Geo and device columns are filled by the writer task, never
    in the response cycle."""

    _track_history = False

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
    )
    ip_address = models.GenericIPAddressField()
    endpoint = models.CharField(max_length=255)
    method = models.CharField(max_length=10)
    user_agent = models.TextField(null=True, blank=True)

    device_type = models.CharField(max_length=50, null=True, blank=True)
    browser = models.CharField(max_length=50, null=True, blank=True)
    operating_system = models.CharField(max_length=50, null=True, blank=True)

    country = models.CharField(max_length=100, null=True, blank=True)
    country_code = models.CharField(max_length=10, null=True, blank=True)
    continent = models.CharField(max_length=50, null=True, blank=True)
    continent_code = models.CharField(max_length=10, null=True, blank=True)
    region = models.CharField(max_length=100, null=True, blank=True)
    region_name = models.CharField(max_length=100, null=True, blank=True)
    city = models.CharField(max_length=100, null=True, blank=True)
    district = models.CharField(max_length=100, null=True, blank=True)
    zip_code = models.CharField(max_length=20, null=True, blank=True)
    latitude = models.DecimalField(max_digits=10, decimal_places=8, null=True, blank=True)
    longitude = models.DecimalField(max_digits=11, decimal_places=8, null=True, blank=True)

    timezone = models.CharField(max_length=50, null=True, blank=True)
    currency = models.CharField(max_length=10, null=True, blank=True)
    isp = models.CharField(max_length=255, null=True, blank=True)
    organization = models.CharField(max_length=255, null=True, blank=True)
    as_number = models.CharField(max_length=50, null=True, blank=True)
    is_mobile = models.BooleanField(null=True, blank=True)

    trace_id = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    status_code = models.IntegerField(null=True, blank=True)
    response_time = models.FloatField(
        help_text="Time taken to process request (in seconds)", null=True, blank=True,
    )

    # Added in 0013, not in the initial migration. Captured from the request
    # when TRACKING["CAPTURE_ATTRIBUTION"] is on; the attribution *policy*
    # (first-touch, session) is the project's.
    referer = models.TextField(null=True, blank=True)
    query_string = models.TextField(
        null=True, blank=True, help_text="Raw request query string with sensitive keys scrubbed.",
    )
    utm_source = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    utm_medium = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    utm_campaign = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    utm_term = models.CharField(max_length=255, null=True, blank=True)
    utm_content = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "ip_tracking"
        verbose_name = "IP Tracking"
        verbose_name_plural = "IP Tracking"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["ip_address"], name="ip_tracking_ip_idx"),
            models.Index(fields=["country"], name="ip_tracking_country_idx"),
            models.Index(fields=["created_at"], name="ip_tracking_created_idx"),
            models.Index(fields=["user"], name="ip_tracking_user_idx"),
        ]

    def __str__(self):
        return f"{self.ip_address} - {self.country} - {self.created_at}"


class ContactUsModel(TenantMixin, BaseModel):
    """A website contact form submission. Table ``contact_us``.

    Marketing attribution (UTM, click ids) is vocabulary and belongs to the
    project, as a mixin on its own model or a related row.
    """

    name = models.CharField(max_length=255)
    email = models.EmailField()
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    subject = models.CharField(max_length=255)
    message = models.TextField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    is_checked = models.BooleanField(default=False)

    class Meta:
        db_table = "contact_us"
        verbose_name = "Contact Us"
        verbose_name_plural = "Contact Us"

    def __str__(self):
        return self.name


class StatusTransitionModel(TenantMixin, BaseModel):
    """One row per status change on any model. Table ``status_transitions``.

    ``transition_source`` is a plain indexed string, not a ``choices`` field:
    the values ("employee", "customer", "system") are role words (§2), and a
    ``choices`` change is an ``AlterField`` migration in every project. The
    project validates the values it uses.
    """

    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE,
        help_text="The model type this status transition belongs to",
    )
    object_id = models.CharField(
        max_length=255, db_index=True,
        help_text="The ID of the object this status transition belongs to",
    )
    content_object = GenericForeignKey("content_type", "object_id")

    # Which status field moved, for a model with more than one.
    field_name = models.CharField(max_length=50, default="status")
    previous_status = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    new_status = models.CharField(max_length=50, db_index=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="status_transitions",
    )
    transition_source = models.CharField(
        max_length=20, db_index=True,
        help_text="Who or what made the change — the project's own vocabulary.",
    )
    transition_reason = models.TextField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "status_transitions"
        verbose_name = "Status Transition"
        verbose_name_plural = "Status Transitions"
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["content_type", "object_id"], name="status_trans_object_idx"),
            models.Index(fields=["new_status", "timestamp"], name="status_trans_status_idx"),
        ]

    def __str__(self):
        return f"{self.content_type.model} #{self.object_id}: {self.previous_status} -> {self.new_status}"

    def _tenant_source(self):
        # The tenant of the row whose status moved.
        return self.content_object if self.content_type_id else None


# ---------------------------------------------------------------------------
# Files (§9)
# ---------------------------------------------------------------------------


class CommonFileQuerySet(models.QuerySet):
    """Bulk deletes go row by row so the storage blob is removed with the row.

    Django's ``QuerySet.delete()`` issues SQL DELETE and never calls
    ``Model.delete()``; without this every bulk delete orphans its files.
    """

    def delete(self):
        ids = list(self.values_list("pk", flat=True))
        deleted = 0
        for pk in ids:
            self.model._default_manager.get(pk=pk).delete()
            deleted += 1
        return deleted, {self.model._meta.label: deleted}


def common_file_upload_to(instance, filename):
    """``<UPLOAD_ROOT>/{tag}/%Y/%m/{filename}`` — under the file's tenant when
    the project uses tenants: ``<tenant_id | system>/<UPLOAD_ROOT>/…``.

    The tenant-first layout is the storage rule (``storage.py``): everything a
    tenant owns is one prefix, and a file with no tenant goes to ``system/``,
    never guessed into one. A project without tenants keeps the flat layout.

    Must return a path *including* the basename — a directory alone raises
    ``SuspiciousFileOperation``. The basename is the client's, stripped of any
    path it tried to smuggle in.
    """
    import re

    from django_common_utils.conf import app_settings
    from django_common_utils.storage import safe_basename, scoped_path
    from django_common_utils.tenancy import is_configured

    tag = (getattr(instance, "tag", None) or "unknown").strip().lower()
    tag = re.sub(r"[^\w\-]", "_", tag)[:50] or "unknown"
    root = app_settings.get("FILES", "UPLOAD_ROOT").strip("/")
    tail = f"{root}/{tag}/{timezone.now():%Y/%m}/{safe_basename(filename, 'file')}"
    if is_configured():
        # FieldFile.save() asks for the path before the row is saved, so the
        # tenant may not have been filled yet.
        tenant = getattr(instance, "tenant_id", None)
        if tenant is None and hasattr(instance, "_tenant_source"):
            from django_common_utils.tenancy import resolve_tenant

            tenant = resolve_tenant(instance=instance._tenant_source())
        return scoped_path(tenant, tail)
    return tail


class CommonFileModel(TenantMixin, BaseModel):
    """A file attached to any object, or to nothing. Table ``common_files``.

    A ``GenericForeignKey`` so apps can attach files without a file table each.
    ``content_type``/``object_id`` are nullable for standalone files.
    """

    objects = CommonFileQuerySet.as_manager()

    def _tenant_source(self):
        # A file attached to a row belongs to that row's tenant.
        return self.content_object if self.content_type_id else None

    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name="common_files",
        null=True, blank=True,
    )
    object_id = models.CharField(max_length=255, db_index=True, null=True, blank=True)
    content_object = GenericForeignKey("content_type", "object_id")

    file = models.FileField(upload_to=common_file_upload_to, storage=MediaStorage())
    original_filename = models.CharField(max_length=255, null=True, blank=True)
    title = models.CharField(max_length=255, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    tag = models.CharField(
        max_length=50, default="unknown", db_index=True,
        help_text="Purpose, used for the directory and for filtering (e.g. 'profile_picture').",
    )
    mime_type = models.CharField(max_length=100, null=True, blank=True)
    file_size = models.BigIntegerField(null=True, blank=True, help_text="Size in bytes")
    metadata = models.JSONField(null=True, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="uploaded_common_files",
    )

    class Meta:
        db_table = "common_files"
        verbose_name = "Common File"
        verbose_name_plural = "Common Files"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["content_type", "object_id"], name="common_file_obj_idx"),
            models.Index(fields=["tag"], name="common_file_tag_idx"),
            models.Index(fields=["created_at"], name="common_file_created_at_idx"),
        ]

    def __str__(self):
        return self.title or self.original_filename or (self.file.name if self.file else str(self.id))

    def clean(self):
        """Apply the ``FILES`` limits to a file being uploaded, not to one
        already stored (``files.py`` says why). Forms and the admin call this;
        a serializer uses ``validate_upload`` on its field instead."""
        super().clean()
        if self.file and not self.file._committed:
            from django_common_utils.files import validate_upload

            try:
                validate_upload(self.file.file)
            except ValidationError as exc:
                raise ValidationError({"file": exc.error_list}) from exc

    def delete(self, *args, **kwargs):
        """Remove the blob before the row. A vanished object must not block
        deleting the row that points at it, so a storage failure is logged."""
        import logging

        if self.file:
            storage = self._meta.get_field("file").storage
            try:
                storage.delete(self.file.name)
            except Exception as exc:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "storage.delete failed (CommonFileModel pk=%s path=%r): %s",
                    self.pk, self.file.name, exc, exc_info=True,
                )
        super().delete(*args, **kwargs)


# ---------------------------------------------------------------------------
# Short links (§10)
# ---------------------------------------------------------------------------


class ShortLinkModel(TenantMixin, BaseModel):
    """A slug that 301s to a long URL. Table ``short_links``.

    For SMS, where a signed URL blows past a sane message length. Resolved by
    ``django_common_utils.shortlinks.views.short_link_redirect``.
    """

    slug = models.CharField(
        max_length=16, unique=True, db_index=True,
        help_text="Short opaque identifier used in the public /s/<slug>/ URL.",
    )
    target_url = models.URLField(max_length=2048)
    click_count = models.PositiveIntegerField(default=0)
    last_clicked_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="After this time the slug returns 410 Gone.",
    )
    purpose = models.CharField(
        max_length=64, null=True, blank=True, db_index=True,
        help_text="Optional tag (e.g. quote_offer_view) for analytics and cleanup.",
    )

    class Meta:
        db_table = "short_links"
        verbose_name = "Short Link"
        verbose_name_plural = "Short Links"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["slug"], name="short_link_slug_idx"),
            models.Index(fields=["purpose"], name="short_link_purpose_idx"),
            models.Index(fields=["expires_at"], name="short_link_expires_idx"),
        ]

    def __str__(self):
        return f"/s/{self.slug}/ -> {self.target_url[:60]}"


# ---------------------------------------------------------------------------
# Platform notices (§16)
# ---------------------------------------------------------------------------


class NoticeSeverity(models.TextChoices):
    INFO = "info", "Info"
    SUCCESS = "success", "Success"
    WARNING = "warning", "Warning"
    CRITICAL = "critical", "Critical"


class PlatformNoticeQuerySet(models.QuerySet):
    """The questions ``PlatformNoticeModel.is_live`` and ``is_visible_to``
    answer for one row, asked of the table. The service reads cached rows
    through the methods; these are for the ORM. A test holds the two to the
    same answers."""

    def live(self, at=None):
        """Switched on, not deleted, and inside its window at ``at`` (now)."""
        Q = models.Q
        at = at or timezone.now()
        return self.filter(
            Q(starts_at__isnull=True) | Q(starts_at__lte=at),
            Q(ends_at__isnull=True) | Q(ends_at__gt=at),
            is_active=True, is_deleted=False,
        )

    def visible_to(self, *, tenant_id=None, audiences=(), surface=None):
        """Notices for one viewer. An empty column means "every": every
        tenant, every audience, every surface."""
        Q = models.Q
        tenant = Q(tenant_id__isnull=True)
        if tenant_id is not None:
            tenant |= Q(tenant_id=tenant_id)
        where = Q(surface__isnull=True)
        if surface:
            where |= Q(surface=surface)
        return self.filter(tenant, where, Q(audience__isnull=True) | Q(audience__in=list(audiences)))


class PlatformNoticeModel(TenantMixin, BaseModel):
    """A message shown to the people using the platform — a maintenance
    window, an outage, a change of terms. Table ``platform_notices``.

    Shown, not sent: the frontend asks for the notices live for its viewer
    (``django_common_utils.notices``) and delivery by email or push is another
    package's job (§1).

    ``audience`` and ``surface`` are plain indexed strings, not ``choices``:
    "admins", "customers", "ios", "partner-portal" are the project's words
    (§2). Empty means every audience, every surface. A viewer's audiences come
    from ``NOTICES["AUDIENCE_RESOLVER"]``; the surface is what the client says
    it is.

    ``tenant_id`` empty is a notice for every tenant. It is never filled from
    the request: an operator posting a platform-wide notice while working in
    one tenant would otherwise scope it to that tenant without being told.
    """

    _fill_tenant = False

    objects = PlatformNoticeQuerySet.as_manager()

    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="", help_text="Plain text or markdown; the frontend renders it.")
    severity = models.CharField(
        max_length=20, choices=NoticeSeverity.choices, default=NoticeSeverity.INFO, db_index=True,
    )
    audience = models.CharField(
        max_length=64, null=True, blank=True, db_index=True,
        help_text="Who sees it, in the project's own words. Empty = everyone.",
    )
    surface = models.CharField(
        max_length=64, null=True, blank=True, db_index=True,
        help_text="Where it shows (e.g. 'web', 'ios'), in the project's own words. Empty = everywhere.",
    )
    starts_at = models.DateTimeField(null=True, blank=True, help_text="Empty = from now.")
    ends_at = models.DateTimeField(null=True, blank=True, help_text="Empty = until switched off.")
    priority = models.IntegerField(default=0, help_text="Higher shows first.")
    is_dismissible = models.BooleanField(default=True)
    action_label = models.CharField(max_length=64, null=True, blank=True)
    action_url = models.URLField(max_length=2048, null=True, blank=True)
    metadata = models.JSONField(
        null=True, blank=True,
        help_text="Anything else the frontend needs (e.g. a minimum app version).",
    )

    class Meta:
        db_table = "platform_notices"
        verbose_name = "Platform Notice"
        verbose_name_plural = "Platform Notices"
        ordering = ["-priority", "-created_at"]
        indexes = [
            models.Index(fields=["starts_at", "ends_at"], name="platform_notice_window_idx"),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        # A blank from a form or an API body means "every", which is NULL; an
        # empty string would match no viewer at all.
        self.audience = self.audience or None
        self.surface = self.surface or None
        super().save(*args, **kwargs)

    def is_live(self, at=None):
        at = at or timezone.now()
        return (
            self.is_active and not self.is_deleted
            and (self.starts_at is None or self.starts_at <= at)
            and (self.ends_at is None or self.ends_at > at)
        )

    def is_for(self, *, tenant_id=None, audiences=()):
        """Meant for this tenant and one of these audiences."""
        return (
            (self.tenant_id is None or (tenant_id is not None and self.tenant_id == tenant_id))
            and (self.audience is None or self.audience in audiences)
        )

    def shows_on(self, surface):
        return self.surface is None or (bool(surface) and self.surface == surface)

    def is_visible_to(self, *, tenant_id=None, audiences=(), surface=None):
        return self.is_for(tenant_id=tenant_id, audiences=audiences) and self.shows_on(surface)


class PlatformNoticeDismissalModel(TenantMixin, BaseModel):
    """One user has dismissed one notice. Table ``platform_notice_dismissals``.

    A table rather than the browser's storage, so a notice dismissed on the
    phone stays dismissed on the laptop. No history: a dismissal is a click,
    written once per user per notice, and its row is its own record.
    """

    _track_history = False

    notice = models.ForeignKey(PlatformNoticeModel, on_delete=models.CASCADE, related_name="dismissals")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="platform_notice_dismissals",
    )

    class Meta:
        db_table = "platform_notice_dismissals"
        verbose_name = "Platform Notice Dismissal"
        verbose_name_plural = "Platform Notice Dismissals"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["notice", "user"], name="platform_notice_dismissal_uniq"),
        ]

    def __str__(self):
        return f"{self.user} dismissed {self.notice}"

    def _tenant_source(self):
        # The notice's tenant: a dismissal of a notice for everyone belongs to
        # no tenant.
        return self.notice if self.notice_id else None
