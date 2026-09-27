"""A model that inherits BaseModel, so the history receivers have something to track."""

from django.db import models

from django_common_kit.crypto import EncryptedTextField
from django_common_kit.models import BaseModel


class Widget(BaseModel):
    name = models.CharField(max_length=100)
    notes = models.TextField(blank=True, default="")
    price = models.DecimalField(max_digits=8, decimal_places=2, null=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        app_label = "testapp"


class Telemetry(BaseModel):
    """Tracking off, as on every telemetry table (PRD §6.3)."""

    _track_history = False

    payload = models.TextField(blank=True, default="")

    class Meta:
        app_label = "testapp"


class Gadget(BaseModel):
    """A many-to-many and an optional file, the two field kinds whose raw
    values do not compare or serialise like a column's."""

    name = models.CharField(max_length=100)
    parts = models.ManyToManyField(Widget, blank=True, related_name="gadgets")
    manual = models.FileField(upload_to="manuals/", null=True, blank=True)

    class Meta:
        app_label = "testapp"


class Vault(BaseModel):
    """Encrypted fields (PRD §17): one new, one adopted from plaintext."""

    name = models.CharField(max_length=100, blank=True, default="")
    secret = EncryptedTextField(blank=True, default="")
    legacy = EncryptedTextField(null=True, blank=True, legacy_plaintext=True)

    class Meta:
        app_label = "testapp"
