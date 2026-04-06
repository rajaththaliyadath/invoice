from __future__ import annotations

import uuid

from django.db import models


class InvoiceJob(models.Model):
    """Background invoice generation; tied to browser session for downloads."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    session_key = models.CharField(max_length=64, db_index=True)
    email = models.EmailField(blank=True, default="")
    rows_json = models.JSONField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    error_message = models.TextField(blank=True, default="")
    pdf_name = models.CharField(max_length=255, blank=True, default="")
    xlsx_name = models.CharField(max_length=255, blank=True, default="")
    invoice_number = models.IntegerField(null=True, blank=True)
    email_sent = models.BooleanField(default=False)
    email_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"InvoiceJob({self.public_id}, {self.status})"
