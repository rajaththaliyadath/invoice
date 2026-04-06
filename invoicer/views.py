from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .pipeline import InvoiceError, monday_of_week_au, run_invoice_pipeline

from .forms import DeliveryLineForm, WeekAnchorForm

SESSION_WEEK = "invoice_week_monday"
SESSION_ROWS = "invoice_rows"
SESSION_REF_DATE = "invoice_reference_date"
SESSION_FORM_DEFAULT_DATE = "invoice_form_default_date"
SESSION_LAST_PDF = "invoice_last_pdf"
SESSION_LAST_XLSX = "invoice_last_xlsx"
SESSION_INV_NO = "invoice_last_number"


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
            for k in (SESSION_LAST_PDF, SESSION_LAST_XLSX, SESSION_INV_NO):
                request.session.pop(k, None)
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
            _ensure_session_key(request)
            sid = request.session.session_key
            output_dir = Path(settings.INVOICE_OUTPUT_ROOT) / sid
            tuples: list[tuple[datetime, int]] = []
            for r in rows:
                d = date.fromisoformat(r["d"])
                tuples.append((datetime.combine(d, datetime.min.time()), int(r["p"])))
            tuples.sort(key=lambda x: x[0])
            try:
                _xlsx, pdf_path, inv_no = run_invoice_pipeline(
                    tuples,
                    output_dir=output_dir,
                    asset_dir=Path(settings.INVOICE_ASSET_DIR),
                )
            except InvoiceError as exc:
                messages.error(request, str(exc))
                return redirect("invoicer:entries")
            request.session[SESSION_LAST_PDF] = pdf_path.name
            request.session[SESSION_LAST_XLSX] = _xlsx.name
            request.session[SESSION_INV_NO] = inv_no
            return redirect("invoicer:done")

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


def done(request):
    if not request.session.get(SESSION_LAST_PDF):
        messages.warning(request, "No invoice ready yet.")
        return redirect("invoicer:week_select")
    return render(
        request,
        "invoicer/done.html",
        {
            "pdf_name": request.session[SESSION_LAST_PDF],
            "xlsx_name": request.session[SESSION_LAST_XLSX],
            "invoice_no": request.session.get(SESSION_INV_NO),
        },
    )


def download(request, kind: str):
    if kind not in ("pdf", "xlsx"):
        raise Http404
    key = request.session.session_key
    name_key = SESSION_LAST_PDF if kind == "pdf" else SESSION_LAST_XLSX
    filename = request.session.get(name_key)
    if not key or not filename:
        raise Http404
    safe = {request.session.get(SESSION_LAST_PDF), request.session.get(SESSION_LAST_XLSX)}
    if filename not in safe:
        raise Http404
    path = Path(settings.INVOICE_OUTPUT_ROOT) / key / filename
    if not path.is_file():
        raise Http404
    return FileResponse(
        path.open("rb"),
        as_attachment=True,
        filename=filename,
        content_type="application/pdf" if kind == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
