from __future__ import annotations

import calendar
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .invoice_tasks import process_invoice_job
from .models import InvoiceJob
from .pipeline import monday_of_week_au
from .worker_spawn import spawn_invoice_job_process

from .forms import DeliveryLineForm, WeekAnchorForm

SESSION_WEEK = "invoice_week_monday"
SESSION_ROWS = "invoice_rows"
SESSION_REF_DATE = "invoice_reference_date"
SESSION_FORM_DEFAULT_DATE = "invoice_form_default_date"
SESSION_ACTIVE_JOB = "invoice_active_job"


def _week_day_isos(monday: date) -> list[str]:
    return [(monday + timedelta(days=i)).isoformat() for i in range(7)]


def _next_day_in_week(d: date, week_monday: date) -> date:
    """Calendar next day; wrap Sunday → Monday of the same invoice week."""
    week_end = week_monday + timedelta(days=6)
    if d >= week_end:
        return week_monday
    return d + timedelta(days=1)


def _row_dict(d_iso: str, parcels: int) -> dict:
    d = date.fromisoformat(d_iso)
    return {
        "d": d_iso,
        "p": parcels,
        "label": f"{calendar.day_name[d.weekday()]} {d.strftime('%d/%m/%Y')}",
    }


def _ensure_session_key(request):
    if not request.session.session_key:
        request.session.save()


def _job_for_session(request, public_id: UUID) -> InvoiceJob:
    job = get_object_or_404(InvoiceJob, public_id=public_id)
    if job.session_key != request.session.session_key:
        raise Http404
    return job


@require_http_methods(["GET", "POST"])
def week_select(request):
    if request.GET.get("reset"):
        request.session.flush()
        return redirect("invoicer:week_select")

    if request.method == "POST":
        form = WeekAnchorForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data["reference_date"]
            mon = monday_of_week_au(d)
            request.session[SESSION_WEEK] = mon.isoformat()
            request.session[SESSION_REF_DATE] = d.isoformat()
            request.session[SESSION_ROWS] = []
            request.session.pop(SESSION_FORM_DEFAULT_DATE, None)
            request.session.pop(SESSION_ACTIVE_JOB, None)
            messages.success(
                request,
                f"Week set: Monday {mon.strftime('%d/%m/%Y')} – Sunday "
                f"{(mon + timedelta(days=6)).strftime('%d/%m/%Y')}.",
            )
            return redirect("invoicer:entries")
    else:
        form = WeekAnchorForm()
    return render(request, "invoicer/week_select.html", {"form": form})


@require_http_methods(["GET", "POST"])
def entries(request):
    mon_iso = request.session.get(SESSION_WEEK)
    if not mon_iso:
        messages.warning(request, "Choose a week first.")
        return redirect("invoicer:week_select")

    week_monday = date.fromisoformat(mon_iso)
    week_end = week_monday + timedelta(days=6)
    day_isos = _week_day_isos(week_monday)
    rows = request.session.get(SESSION_ROWS, [])
    ref_iso = request.session.get(SESSION_REF_DATE)
    default_iso = request.session.get(SESSION_FORM_DEFAULT_DATE)

    editing_index: int | None = None
    bound_form: DeliveryLineForm | None = None

    if request.method == "GET":
        edit_raw = request.GET.get("edit")
        if edit_raw is not None:
            try:
                idx = int(edit_raw)
                if 0 <= idx < len(rows):
                    editing_index = idx
            except ValueError:
                pass

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "delete":
            try:
                idx = int(request.POST.get("line_index", ""))
            except ValueError:
                messages.error(request, "Invalid line.")
                return redirect("invoicer:entries")
            if 0 <= idx < len(rows):
                rows = list(rows)
                rows.pop(idx)
                request.session[SESSION_ROWS] = rows
                messages.success(request, "Line removed.")
            return redirect("invoicer:entries")

        if action == "add":
            bound_form = DeliveryLineForm(request.POST, week_days_iso=day_isos)
            if bound_form.is_valid():
                max_lines = 9
                if len(rows) >= max_lines:
                    messages.error(request, f"Maximum {max_lines} lines for this template.")
                else:
                    d_iso = bound_form.cleaned_data["delivery_date"]
                    d = date.fromisoformat(d_iso)
                    rows = list(rows)
                    rows.append(_row_dict(d_iso, bound_form.cleaned_data["parcels"]))
                    request.session[SESSION_ROWS] = rows
                    request.session[SESSION_FORM_DEFAULT_DATE] = _next_day_in_week(
                        d, week_monday
                    ).isoformat()
                    messages.success(request, "Line added.")
                    return redirect("invoicer:entries")
        elif action == "save_line":
            try:
                idx = int(request.POST.get("line_index", ""))
            except ValueError:
                messages.error(request, "Invalid line.")
                return redirect("invoicer:entries")
            bound_form = DeliveryLineForm(request.POST, week_days_iso=day_isos)
            if bound_form.is_valid():
                if not (0 <= idx < len(rows)):
                    messages.error(request, "Invalid line.")
                    return redirect("invoicer:entries")
                d_iso = bound_form.cleaned_data["delivery_date"]
                d = date.fromisoformat(d_iso)
                rows = list(rows)
                rows[idx] = _row_dict(d_iso, bound_form.cleaned_data["parcels"])
                request.session[SESSION_ROWS] = rows
                request.session[SESSION_FORM_DEFAULT_DATE] = _next_day_in_week(
                    d, week_monday
                ).isoformat()
                messages.success(request, "Line updated.")
                return redirect("invoicer:entries")
            editing_index = idx
        elif action == "finish":
            if not rows:
                messages.error(request, "Add at least one delivery line before finishing.")
                return redirect("invoicer:entries")
            email = (request.POST.get("delivery_email") or "").strip()
            if email:
                try:
                    validate_email(email)
                except ValidationError:
                    messages.error(request, "Please enter a valid email address, or leave it blank.")
                    return redirect("invoicer:entries")

            _ensure_session_key(request)
            sid = request.session.session_key
            job = InvoiceJob.objects.create(
                session_key=sid,
                email=email,
                rows_json=list(rows),
            )
            request.session[SESSION_ACTIVE_JOB] = str(job.public_id)

            if os.environ.get("INVOICE_JOB_INLINE", "").lower() in ("1", "true", "yes"):
                process_invoice_job(job.public_id)
            else:
                spawn_invoice_job_process(job.public_id)

            if email:
                messages.success(
                    request,
                    "Your invoice is building in the background. "
                    "We will email the PDF and Excel when it is ready.",
                )
            else:
                messages.success(
                    request,
                    "Your invoice is building in the background. "
                    "This page will update when it is ready to download.",
                )
            return redirect("invoicer:job_progress", public_id=job.public_id)

    if bound_form is not None:
        form = bound_form
    elif editing_index is not None:
        row = rows[editing_index]
        form = DeliveryLineForm(
            week_days_iso=day_isos,
            initial={"delivery_date": row["d"], "parcels": row["p"]},
        )
    else:
        if default_iso and default_iso in day_isos:
            start_iso = default_iso
        elif ref_iso and ref_iso in day_isos:
            start_iso = ref_iso
        else:
            start_iso = day_isos[0]
        form = DeliveryLineForm(
            week_days_iso=day_isos,
            initial={"delivery_date": start_iso},
        )

    return render(
        request,
        "invoicer/entries.html",
        {
            "form": form,
            "rows": rows,
            "week_monday": week_monday,
            "week_end": week_end,
            "editing_index": editing_index,
            "reference_date_display": (
                date.fromisoformat(ref_iso).strftime("%d/%m/%Y") if ref_iso else None
            ),
        },
    )


@require_http_methods(["GET"])
def job_progress(request, public_id: UUID):
    job = _job_for_session(request, public_id)
    if job.status == InvoiceJob.Status.DONE:
        return redirect("invoicer:done")
    if job.status == InvoiceJob.Status.FAILED:
        return render(
            request,
            "invoicer/job_failed.html",
            {"job": job},
        )
    return render(
        request,
        "invoicer/job_progress.html",
        {"job": job},
    )


@require_http_methods(["GET"])
def job_status(request, public_id: UUID):
    job = _job_for_session(request, public_id)
    return JsonResponse(
        {
            "status": job.status,
            "error": job.error_message,
            "invoice_no": job.invoice_number,
            "pdf_name": job.pdf_name,
            "xlsx_name": job.xlsx_name,
            "email": job.email,
            "email_sent": job.email_sent,
            "email_error": job.email_error,
        }
    )


@require_http_methods(["GET"])
def done(request):
    jid = request.session.get(SESSION_ACTIVE_JOB)
    if not jid:
        messages.warning(request, "No invoice ready yet.")
        return redirect("invoicer:week_select")
    try:
        uid = UUID(jid)
    except ValueError:
        raise Http404
    job = _job_for_session(request, uid)
    if job.status in (InvoiceJob.Status.PENDING, InvoiceJob.Status.RUNNING):
        return redirect("invoicer:job_progress", public_id=job.public_id)
    if job.status == InvoiceJob.Status.FAILED:
        return render(request, "invoicer/job_failed.html", {"job": job})
    return render(
        request,
        "invoicer/done.html",
        {
            "job": job,
            "pdf_name": job.pdf_name,
            "xlsx_name": job.xlsx_name,
            "invoice_no": job.invoice_number,
        },
    )


def download_job(request, public_id: UUID, kind: str):
    if kind not in ("pdf", "xlsx"):
        raise Http404
    job = _job_for_session(request, public_id)
    if job.status != InvoiceJob.Status.DONE:
        raise Http404
    filename = job.pdf_name if kind == "pdf" else job.xlsx_name
    if not filename:
        raise Http404
    path = Path(settings.INVOICE_OUTPUT_ROOT) / job.session_key / filename
    if not path.is_file():
        raise Http404
    return FileResponse(
        path.open("rb"),
        as_attachment=True,
        filename=filename,
        content_type=(
            "application/pdf"
            if kind == "pdf"
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )
