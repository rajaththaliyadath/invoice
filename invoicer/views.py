from __future__ import annotations

import calendar
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .invoice_tasks import process_invoice_job, send_invoice_files
from .models import (
    AccountProfile,
    DEFAULT_ACCOUNT_NUMBER,
    DEFAULT_BANK_NAME,
    DEFAULT_BSB_NUMBER,
    DEFAULT_CONTRACTOR_ABN,
    DEFAULT_CONTRACTOR_NAME,
    DEFAULT_EMPLOYER_ABN,
    DEFAULT_EMPLOYER_NAME,
    DEFAULT_RATE_PER_PARCEL,
    InvoiceJob,
    SavedInvoice,
)
from .pipeline import monday_of_week_au
from .worker_spawn import spawn_invoice_job_process

from .forms import AccountProfileForm, DeliveryLineForm, MappingSettingsForm, SignupForm, WeekAnchorForm

SESSION_WEEK = "invoice_week_monday"
SESSION_ROWS = "invoice_rows"
SESSION_REF_DATE = "invoice_reference_date"
SESSION_FORM_DEFAULT_DATE = "invoice_form_default_date"
SESSION_ACTIVE_JOB = "invoice_active_job"
SESSION_INCLUDE_GST = "invoice_include_gst"


def _week_day_isos(monday: date) -> list[str]:
    return [(monday + timedelta(days=i)).isoformat() for i in range(7)]


def _next_day_in_week(d: date, week_monday: date) -> date:
    """Calendar next day; wrap Sunday → Monday of the same invoice week."""
    week_end = week_monday + timedelta(days=6)
    if d >= week_end:
        return week_monday
    return d + timedelta(days=1)


def _selectable_days_in_week(week_monday: date) -> list[str]:
    """ISO dates Mon–Sun of the week that are today or earlier (no future delivery days)."""
    today = timezone.localdate()
    week_end = week_monday + timedelta(days=6)
    out: list[str] = []
    d = week_monday
    while d <= week_end:
        if d <= today:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _next_delivery_default_after(d: date, week_monday: date) -> date:
    """Next calendar day in the week for a new line, capped to not go past today."""
    today = timezone.localdate()
    nxt = _next_day_in_week(d, week_monday)
    week_end = week_monday + timedelta(days=6)
    if nxt <= today and nxt <= week_end:
        return nxt
    valid = [
        week_monday + timedelta(days=i)
        for i in range(7)
        if week_monday + timedelta(days=i) <= today
    ]
    return max(valid) if valid else d


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
    if not request.user.is_staff and job.owner_id != request.user.id:
        raise Http404
    return job


def _get_or_create_profile(user):
    profile, _ = AccountProfile.objects.get_or_create(
        user=user,
        defaults={
            "employer_name": DEFAULT_EMPLOYER_NAME,
            "employer_abn": DEFAULT_EMPLOYER_ABN,
            "contractor_name": DEFAULT_CONTRACTOR_NAME,
            "contractor_abn": DEFAULT_CONTRACTOR_ABN,
            "rate_per_parcel": DEFAULT_RATE_PER_PARCEL,
            "bank_name": DEFAULT_BANK_NAME,
            "bsb_number": DEFAULT_BSB_NUMBER,
            "account_number": DEFAULT_ACCOUNT_NUMBER,
            "account_name": DEFAULT_CONTRACTOR_NAME,
            "map_data_first_row": 14,
            "map_data_last_row": 20,
            "map_sum_row": 21,
            "map_table_header_row": 13,
        },
    )
    return profile


@require_http_methods(["GET", "POST"])
def signup(request):
    if request.user.is_authenticated:
        return redirect("invoicer:week_select")
    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            _get_or_create_profile(user)
            login(request, user)
            messages.success(request, "Account created. You are now signed in.")
            return redirect("invoicer:week_select")
    else:
        form = SignupForm()
    return render(request, "invoicer/signup.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def account_settings(request):
    profile = _get_or_create_profile(request.user)
    if request.method == "POST":
        form = AccountProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, "Account settings updated.")
            return redirect("invoicer:account_settings")
    else:
        form = AccountProfileForm(instance=profile)
    return render(request, "invoicer/account_settings.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def advanced_settings(request):
    profile = _get_or_create_profile(request.user)
    if request.method == "POST":
        form = MappingSettingsForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, "Advanced mapping settings updated.")
            return redirect("invoicer:advanced_settings")
    else:
        form = MappingSettingsForm(instance=profile)
    return render(request, "invoicer/advanced_settings.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def week_select(request):
    _get_or_create_profile(request.user)
    if request.GET.get("reset"):
        for key in (
            SESSION_WEEK,
            SESSION_ROWS,
            SESSION_REF_DATE,
            SESSION_FORM_DEFAULT_DATE,
            SESSION_ACTIVE_JOB,
            SESSION_INCLUDE_GST,
        ):
            request.session.pop(key, None)
        return redirect("invoicer:week_select")

    if request.method == "POST":
        form = WeekAnchorForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data["reference_date"]
            mon = monday_of_week_au(d)
            request.session[SESSION_WEEK] = mon.isoformat()
            request.session[SESSION_REF_DATE] = d.isoformat()
            request.session[SESSION_ROWS] = []
            request.session[SESSION_INCLUDE_GST] = True
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


@login_required
@require_http_methods(["GET", "POST"])
def entries(request):
    mon_iso = request.session.get(SESSION_WEEK)
    if not mon_iso:
        messages.warning(request, "Choose a week first.")
        return redirect("invoicer:week_select")

    week_monday = date.fromisoformat(mon_iso)
    week_end = week_monday + timedelta(days=6)
    day_isos = _selectable_days_in_week(week_monday)
    if not day_isos:
        messages.error(
            request,
            "This week has no delivery days on or before today. Choose a different week in step 1.",
        )
        return redirect("invoicer:week_select")
    rows = request.session.get(SESSION_ROWS, [])
    gst_included = bool(request.session.get(SESSION_INCLUDE_GST, True))
    profile = _get_or_create_profile(request.user)
    total_parcels = sum(int(r.get("p", 0)) for r in rows)
    rate_per_parcel = float(profile.rate_per_parcel)
    preview_amount = (total_parcels * rate_per_parcel) if rate_per_parcel is not None else None
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
                    if d > timezone.localdate():
                        messages.error(request, "Delivery date cannot be in the future.")
                        return redirect("invoicer:entries")
                    rows = list(rows)
                    rows.append(_row_dict(d_iso, bound_form.cleaned_data["parcels"]))
                    request.session[SESSION_ROWS] = rows
                    request.session[SESSION_FORM_DEFAULT_DATE] = _next_delivery_default_after(
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
                if d > timezone.localdate():
                    messages.error(request, "Delivery date cannot be in the future.")
                    return redirect("invoicer:entries")
                rows = list(rows)
                rows[idx] = _row_dict(d_iso, bound_form.cleaned_data["parcels"])
                request.session[SESSION_ROWS] = rows
                request.session[SESSION_FORM_DEFAULT_DATE] = _next_delivery_default_after(
                    d, week_monday
                ).isoformat()
                messages.success(request, "Line updated.")
                return redirect("invoicer:entries")
            editing_index = idx
        elif action == "finish":
            if not rows:
                messages.error(request, "Add at least one delivery line before finishing.")
                return redirect("invoicer:entries")
            today = timezone.localdate()
            for row in rows:
                if date.fromisoformat(row["d"]) > today:
                    messages.error(request, "Remove or fix lines with a future delivery date before finishing.")
                    return redirect("invoicer:entries")
            email = (request.POST.get("delivery_email") or "").strip()
            include_gst = request.POST.get("include_gst") == "on"
            save_weekly = request.POST.get("save_weekly") == "on"
            request.session[SESSION_INCLUDE_GST] = include_gst
            if email:
                try:
                    validate_email(email)
                except ValidationError:
                    messages.error(request, "Please enter a valid email address, or leave it blank.")
                    return redirect("invoicer:entries")

            _ensure_session_key(request)
            sid = request.session.session_key
            profile = _get_or_create_profile(request.user)
            job = InvoiceJob.objects.create(
                owner=request.user,
                session_key=sid,
                email=email,
                employer_name=profile.employer_name,
                employer_abn=profile.employer_abn,
                contractor_name=profile.contractor_name,
                contractor_abn=profile.contractor_abn,
                rate_per_parcel=profile.rate_per_parcel,
                bank_name=profile.bank_name,
                bsb_number=profile.bsb_number,
                account_number=profile.account_number,
                account_name=profile.account_name,
                save_weekly=save_weekly,
                rows_json=[{**row, "gst": include_gst} for row in rows],
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
            "gst_included": gst_included,
            "total_parcels": total_parcels,
            "rate_per_parcel": rate_per_parcel,
            "preview_amount": preview_amount,
        },
    )


@login_required
@require_http_methods(["GET"])
def invoice_history(request):
    if request.user.is_staff:
        qs = SavedInvoice.objects.select_related("owner", "job").order_by("-created_at")
    else:
        qs = SavedInvoice.objects.select_related("job").filter(owner=request.user).order_by("-created_at")
    return render(request, "invoicer/invoice_history.html", {"items": qs[:200]})


@login_required
@require_http_methods(["POST"])
def remove_saved_invoice(request, saved_id: int):
    if request.user.is_staff:
        saved = get_object_or_404(SavedInvoice, id=saved_id)
    else:
        saved = get_object_or_404(SavedInvoice, id=saved_id, owner=request.user)
    saved.delete()
    messages.success(request, "Removed from history.")
    return redirect("invoicer:invoice_history")


@login_required
@require_http_methods(["GET"])
def income_report(request):
    start = request.GET.get("start", "")
    end = request.GET.get("end", "")
    qs = SavedInvoice.objects.all()
    if not request.user.is_staff:
        qs = qs.filter(owner=request.user)
    if start:
        try:
            qs = qs.filter(week_monday__gte=date.fromisoformat(start))
        except ValueError:
            pass
    if end:
        try:
            qs = qs.filter(week_monday__lte=date.fromisoformat(end))
        except ValueError:
            pass
    qs = qs.select_related("job").order_by("-created_at")
    total = sum(i.total_amount for i in qs)
    return render(
        request,
        "invoicer/income_report.html",
        {"start": start, "end": end, "total": total, "count": qs.count(), "items": qs[:200]},
    )


@login_required
@require_http_methods(["POST"])
def save_invoice(request, public_id: UUID):
    job = _job_for_session(request, public_id)
    if job.status != InvoiceJob.Status.DONE:
        messages.error(request, "Invoice is not ready yet.")
        return redirect("invoicer:job_progress", public_id=job.public_id)
    if not job.owner_id:
        messages.error(request, "No owner found for this invoice.")
        return redirect("invoicer:done")
    rows = job.rows_json or []
    if rows:
        d = min(date.fromisoformat(r["d"]) for r in rows if "d" in r)
        week_monday = d - timedelta(days=d.weekday())
    else:
        week_monday = timezone.localdate() - timedelta(days=timezone.localdate().weekday())
    SavedInvoice.objects.update_or_create(
        job=job,
        defaults={
            "owner_id": job.owner_id,
            "week_monday": week_monday,
            "pdf_name": job.pdf_name,
            "xlsx_name": job.xlsx_name,
            "total_amount": job.total_amount,
        },
    )
    messages.success(request, "Week invoice saved to history.")
    return redirect("invoicer:done")


@login_required
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


@login_required
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


@login_required
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


@login_required
@require_http_methods(["POST"])
def resend_email(request, public_id: UUID):
    job = _job_for_session(request, public_id)
    if job.status != InvoiceJob.Status.DONE:
        messages.error(request, "Invoice is not ready yet.")
        return redirect("invoicer:job_progress", public_id=job.public_id)
    if not job.email:
        messages.error(request, "No delivery email was provided for this invoice.")
        return redirect("invoicer:done")
    try:
        send_invoice_files(job)
        job.email_sent = True
        job.email_error = ""
        job.save(update_fields=["email_sent", "email_error", "updated_at"])
        messages.success(request, f"Email sent again to {job.email}.")
    except Exception as exc:
        job.email_error = str(exc)[:1000]
        job.save(update_fields=["email_error", "updated_at"])
        messages.error(request, "Could not resend email. Please try again.")
    return redirect("invoicer:done")


@login_required
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
