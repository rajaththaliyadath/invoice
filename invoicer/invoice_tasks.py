from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from .models import InvoiceJob
from .pipeline import InvoiceError, run_invoice_pipeline

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

    try:
        xlsx_path, pdf_path, inv_no = run_invoice_pipeline(
            tuples,
            output_dir=output_dir,
            include_gst=include_gst,
            asset_dir=Path(settings.INVOICE_ASSET_DIR),
        )
    except InvoiceError as exc:
        job.status = InvoiceJob.Status.FAILED
        job.error_message = str(exc)[:2000]
        job.save(update_fields=["status", "error_message", "updated_at"])
        logger.warning("Invoice job failed (pipeline): %s", exc)
        return
    except Exception as exc:
        logger.exception("Invoice pipeline failed")
        job.status = InvoiceJob.Status.FAILED
        job.error_message = str(exc)[:2000]
        job.save(update_fields=["status", "error_message", "updated_at"])
        return

    job.pdf_name = pdf_path.name
    job.xlsx_name = xlsx_path.name
    job.invoice_number = inv_no
    job.status = InvoiceJob.Status.DONE
    job.save(
        update_fields=[
            "pdf_name",
            "xlsx_name",
            "invoice_number",
            "status",
            "updated_at",
        ]
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
