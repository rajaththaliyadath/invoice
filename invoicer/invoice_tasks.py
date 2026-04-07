from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from .models import InvoiceJob, SavedInvoice
from .pipeline import InvoiceError, run_invoice_pipeline
from .worker_spawn import spawn_invoice_job_process

logger = logging.getLogger(__name__)


def send_invoice_files(job: InvoiceJob) -> None:
    if not job.email:
        return
    root = Path(settings.INVOICE_OUTPUT_ROOT) / job.session_key
    pdf_path = root / job.pdf_name
    xlsx_path = root / job.xlsx_name
    if not pdf_path.is_file() or not xlsx_path.is_file():
        raise FileNotFoundError("Invoice files missing for email attachment.")

    inv = job.invoice_number
    subject = f"Your invoice — week PDF & Excel{f' (No. {inv})' if inv is not None else ''}"
    body = (
        "Your invoice files are attached (PDF and Excel).\n\n"
        "If you did not request this, you can ignore this email.\n"
    )
    msg = EmailMessage(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[job.email],
    )
    msg.attach_file(str(pdf_path))
    msg.attach_file(str(xlsx_path))
    msg.send(fail_silently=False)


def process_invoice_job(public_id: UUID) -> None:
    """Run pipeline and optional email; invoked by management command (subprocess)."""
    logger.info("Invoice job start %s", public_id)
    try:
        job = InvoiceJob.objects.get(public_id=public_id)
    except InvoiceJob.DoesNotExist:
        logger.error("InvoiceJob missing: %s", public_id)
        return

    if InvoiceJob.objects.filter(status=InvoiceJob.Status.RUNNING).exclude(public_id=public_id).exists():
        logger.info("Invoice job queued (another running) %s", public_id)
        return
    n = InvoiceJob.objects.filter(
        public_id=public_id,
        status=InvoiceJob.Status.PENDING,
    ).update(status=InvoiceJob.Status.RUNNING, updated_at=timezone.now())
    if n != 1:
        logger.info("Invoice job skip (not pending) %s", public_id)
        return

    job.refresh_from_db()
    output_dir = Path(settings.INVOICE_OUTPUT_ROOT) / job.session_key
    tuples: list[tuple[datetime, int]] = []
    for r in job.rows_json:
        d = date.fromisoformat(r["d"])
        tuples.append((datetime.combine(d, datetime.min.time()), int(r["p"])))
    tuples.sort(key=lambda x: x[0])
    include_gst = True
    if job.rows_json:
        include_gst = bool(job.rows_json[0].get("gst", True))

    max_retries = int(os.environ.get("INVOICE_MAX_RETRIES", "2"))
    last_exc: Exception | None = None
    xlsx_path = pdf_path = None
    inv_no = None
    mapping = None
    if job.owner and hasattr(job.owner, "account_profile") and job.owner.account_profile.use_custom_mapping:
        p = job.owner.account_profile
        mapping = {
            "data_first_row": p.map_data_first_row,
            "data_last_row": p.map_data_last_row,
            "sum_row": p.map_sum_row,
            "table_header_row": p.map_table_header_row,
            "invoice_number_cell": p.map_invoice_number_cell,
            "rate_cell": p.map_rate_cell,
            "employer_name_cell": p.map_employer_name_cell,
            "employer_abn_cell": p.map_employer_abn_cell,
            "contractor_name_cell": p.map_contractor_name_cell,
            "contractor_abn_cell": p.map_contractor_abn_cell,
            "contractor_name_line_cell": p.map_contractor_name_line_cell,
            "bank_name_cell": p.map_bank_name_cell,
            "bsb_cell": p.map_bsb_cell,
            "account_number_cell": p.map_account_number_cell,
            "account_name_cell": p.map_account_name_cell,
            "total_label_cell": p.map_total_label_cell,
            "date_cell": p.map_date_cell,
        }
    for attempt in range(max_retries + 1):
        try:
            xlsx_path, pdf_path, inv_no = run_invoice_pipeline(
                tuples,
                output_dir=output_dir,
                include_gst=include_gst,
                employer_name=job.employer_name,
                employer_abn=job.employer_abn,
                contractor_name=job.contractor_name,
                contractor_abn=job.contractor_abn,
                rate_per_parcel=float(job.rate_per_parcel),
                bank_name=job.bank_name,
                bsb_number=job.bsb_number,
                account_number=job.account_number,
                account_name=job.account_name,
                mapping=mapping,
                asset_dir=Path(settings.INVOICE_ASSET_DIR),
            )
            break
        except Exception as exc:
            last_exc = exc
            job.retry_count = attempt + 1
            job.save(update_fields=["retry_count", "updated_at"])
            if attempt < max_retries:
                time.sleep(2 * (attempt + 1))
                continue
    if last_exc and (xlsx_path is None or pdf_path is None):
        job.status = InvoiceJob.Status.FAILED
        job.error_message = str(last_exc)[:2000]
        job.save(update_fields=["status", "error_message", "updated_at"])
        logger.warning("Invoice job failed (pipeline): %s", last_exc)
        nxt = InvoiceJob.objects.filter(status=InvoiceJob.Status.PENDING).order_by("created_at").first()
        if nxt:
            spawn_invoice_job_process(nxt.public_id)
        return

    job.pdf_name = pdf_path.name
    job.xlsx_name = xlsx_path.name
    job.invoice_number = inv_no
    job.total_amount = Decimal(sum(int(r.get("p", 0)) for r in job.rows_json)) * Decimal(job.rate_per_parcel)
    job.status = InvoiceJob.Status.DONE
    job.save(
        update_fields=[
            "pdf_name",
            "xlsx_name",
            "invoice_number",
            "total_amount",
            "status",
            "updated_at",
        ]
    )
    if job.save_weekly and job.owner_id:
        SavedInvoice.objects.update_or_create(
            job=job,
            defaults={
                "owner_id": job.owner_id,
                "week_monday": min(t[0].date() for t in tuples),
                "pdf_name": job.pdf_name,
                "xlsx_name": job.xlsx_name,
                "total_amount": job.total_amount,
            },
        )

    if job.email:
        try:
            send_invoice_files(job)
            job.email_sent = True
            job.save(update_fields=["email_sent", "updated_at"])
            logger.info("Invoice job emailed %s", job.email)
        except Exception as exc:
            logger.exception("Invoice email failed")
            job.email_error = str(exc)[:1000]
            job.save(update_fields=["email_error", "updated_at"])

    logger.info("Invoice job done %s", public_id)
    nxt = InvoiceJob.objects.filter(status=InvoiceJob.Status.PENDING).order_by("created_at").first()
    if nxt:
        spawn_invoice_job_process(nxt.public_id)
