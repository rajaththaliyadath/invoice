from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

DEFAULT_EMPLOYER_NAME = "ParcelRun PTY LTD"
DEFAULT_EMPLOYER_ABN = "98 665 950 201"
DEFAULT_CONTRACTOR_NAME = "Rajath Thaliyadath"
DEFAULT_CONTRACTOR_ABN = "14 357 358 253"
DEFAULT_RATE_PER_PARCEL = 3
DEFAULT_BANK_NAME = "Commonwealth Bank Macquarie Centre North Ryde"
DEFAULT_BSB_NUMBER = "062320"
DEFAULT_ACCOUNT_NUMBER = "1179 6764"
DEFAULT_MAP_INVOICE_NUMBER_CELL = "G3"
DEFAULT_MAP_RATE_CELL = "G12"
DEFAULT_MAP_EMPLOYER_NAME_CELL = "C8"
DEFAULT_MAP_EMPLOYER_ABN_CELL = "C9"
DEFAULT_MAP_CONTRACTOR_NAME_CELL = "G8"
DEFAULT_MAP_CONTRACTOR_ABN_CELL = "G9"
DEFAULT_MAP_CONTRACTOR_NAME_LINE_CELL = "B25"
DEFAULT_MAP_BANK_NAME_CELL = "C28"
DEFAULT_MAP_BSB_CELL = "C29"
DEFAULT_MAP_ACCOUNT_NUMBER_CELL = "C30"
DEFAULT_MAP_ACCOUNT_NAME_CELL = "C31"
DEFAULT_MAP_TOTAL_LABEL_CELL = "B21"
DEFAULT_MAP_DATE_CELL = "B24"


class AccountProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="account_profile",
    )
    employer_name = models.CharField(max_length=255, default=DEFAULT_EMPLOYER_NAME)
    employer_abn = models.CharField(max_length=64, default=DEFAULT_EMPLOYER_ABN)
    contractor_name = models.CharField(max_length=255, default=DEFAULT_CONTRACTOR_NAME)
    contractor_abn = models.CharField(max_length=64, default=DEFAULT_CONTRACTOR_ABN)
    rate_per_parcel = models.DecimalField(max_digits=10, decimal_places=2, default=DEFAULT_RATE_PER_PARCEL)
    bank_name = models.CharField(max_length=255, default=DEFAULT_BANK_NAME)
    bsb_number = models.CharField(max_length=64, default=DEFAULT_BSB_NUMBER)
    account_number = models.CharField(max_length=64, default=DEFAULT_ACCOUNT_NUMBER)
    account_name = models.CharField(max_length=255, default=DEFAULT_CONTRACTOR_NAME)
    profile_photo = models.ImageField(upload_to="profiles/", blank=True, default="")
    use_custom_mapping = models.BooleanField(default=False)
    map_data_first_row = models.PositiveIntegerField(default=14)
    map_data_last_row = models.PositiveIntegerField(default=20)
    map_sum_row = models.PositiveIntegerField(default=21)
    map_table_header_row = models.PositiveIntegerField(default=13)
    map_invoice_number_cell = models.CharField(max_length=16, default=DEFAULT_MAP_INVOICE_NUMBER_CELL)
    map_rate_cell = models.CharField(max_length=16, default=DEFAULT_MAP_RATE_CELL)
    map_employer_name_cell = models.CharField(max_length=16, default=DEFAULT_MAP_EMPLOYER_NAME_CELL)
    map_employer_abn_cell = models.CharField(max_length=16, default=DEFAULT_MAP_EMPLOYER_ABN_CELL)
    map_contractor_name_cell = models.CharField(max_length=16, default=DEFAULT_MAP_CONTRACTOR_NAME_CELL)
    map_contractor_abn_cell = models.CharField(max_length=16, default=DEFAULT_MAP_CONTRACTOR_ABN_CELL)
    map_contractor_name_line_cell = models.CharField(max_length=16, default=DEFAULT_MAP_CONTRACTOR_NAME_LINE_CELL)
    map_bank_name_cell = models.CharField(max_length=16, default=DEFAULT_MAP_BANK_NAME_CELL)
    map_bsb_cell = models.CharField(max_length=16, default=DEFAULT_MAP_BSB_CELL)
    map_account_number_cell = models.CharField(max_length=16, default=DEFAULT_MAP_ACCOUNT_NUMBER_CELL)
    map_account_name_cell = models.CharField(max_length=16, default=DEFAULT_MAP_ACCOUNT_NAME_CELL)
    map_total_label_cell = models.CharField(max_length=16, default=DEFAULT_MAP_TOTAL_LABEL_CELL)
    map_date_cell = models.CharField(max_length=16, default=DEFAULT_MAP_DATE_CELL)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"AccountProfile({self.user_id})"


class InvoiceJob(models.Model):
    """Background invoice generation; tied to browser session for downloads."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="invoice_jobs",
        null=True,
        blank=True,
    )
    session_key = models.CharField(max_length=64, db_index=True)
    email = models.EmailField(blank=True, default="")
    employer_name = models.CharField(max_length=255, default=DEFAULT_EMPLOYER_NAME)
    employer_abn = models.CharField(max_length=64, default=DEFAULT_EMPLOYER_ABN)
    contractor_name = models.CharField(max_length=255, default=DEFAULT_CONTRACTOR_NAME)
    contractor_abn = models.CharField(max_length=64, default=DEFAULT_CONTRACTOR_ABN)
    rate_per_parcel = models.DecimalField(max_digits=10, decimal_places=2, default=DEFAULT_RATE_PER_PARCEL)
    bank_name = models.CharField(max_length=255, default=DEFAULT_BANK_NAME)
    bsb_number = models.CharField(max_length=64, default=DEFAULT_BSB_NUMBER)
    account_number = models.CharField(max_length=64, default=DEFAULT_ACCOUNT_NUMBER)
    account_name = models.CharField(max_length=255, default=DEFAULT_CONTRACTOR_NAME)
    save_weekly = models.BooleanField(default=True)
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
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    retry_count = models.PositiveIntegerField(default=0)
    email_sent = models.BooleanField(default=False)
    email_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"InvoiceJob({self.public_id}, {self.status})"


class SavedInvoice(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_invoices")
    job = models.OneToOneField(InvoiceJob, on_delete=models.CASCADE, related_name="saved_record")
    week_monday = models.DateField()
    pdf_name = models.CharField(max_length=255)
    xlsx_name = models.CharField(max_length=255)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
