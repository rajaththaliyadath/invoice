"""
Invoice fill + export (used by the Django invoicer app).

Templates live in ``assets/invoice/`` (see project ``INVOICE_ASSET_DIR`` setting).
"""
from __future__ import annotations

import calendar
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parent
# Default when ``asset_dir`` is not passed (e.g. tests); Django always sets INVOICE_ASSET_DIR.
INVOICE_ASSET_DIR = _PROJECT_ROOT / "assets" / "invoice"


class InvoiceError(Exception):
    """Raised when invoice generation fails (templates, PDF, validation)."""

# Layout must match ``assets/invoice/Template.xlsx`` (adjust if you change the sheet).
# Current template mapping:
# - Invoice: G3
# - Rate: G12
# - Input rows: 14..20
# - Totals: 21
DATA_FIRST_ROW = 14
DATA_LAST_ROW = 20
SUM_ROW = 21
DATE_VALUE_CELL = "B24"
TABLE_HEADER_ROW = 13
ALIGN_COLS = ("B", "C", "E", "F")

CELL_CENTER = Alignment(horizontal="center", vertical="center")
CELL_LEFT = Alignment(horizontal="left", vertical="center")

INVOICE_WEEK_1_MONDAY = date(2025, 6, 30)

XLSX_NAMES = ("Template.xlsx", "template.xlsx")


def find_soffice() -> str | None:
    """
    Resolve the LibreOffice ``soffice`` binary.

    Set ``INVOICE_SOFFICE`` to a full path if auto-detection fails (non-standard install).
    """
    env_path = os.environ.get("INVOICE_SOFFICE", "").strip()
    if env_path:
        p = Path(env_path).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return str(p.resolve())
    if sys.platform == "darwin":
        for candidate in (
            Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
            Path.home() / "Applications/LibreOffice.app/Contents/MacOS/soffice",
        ):
            if candidate.is_file():
                return str(candidate.resolve())
        apps = Path("/Applications")
        if apps.is_dir():
            for app in sorted(apps.glob("LibreOffice*.app")):
                sof = app / "Contents/MacOS/soffice"
                if sof.is_file():
                    return str(sof.resolve())
    win = Path(r"C:\Program Files\LibreOffice\program\soffice.exe")
    if win.is_file():
        return str(win)
    win_alt = Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe")
    if win_alt.is_file():
        return str(win_alt)
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _convert_xlsx_to_pdf_libreoffice(xlsx_path: Path, pdf_path: Path) -> bool:
    soffice = find_soffice()
    if not soffice:
        return False
    # Isolated profile + SVP backend: avoid attaching to a running LibreOffice GUI and
    # help the converter process exit instead of leaving the app open (common on macOS).
    env = os.environ.copy()
    env.setdefault("SAL_USE_VCLPLUGIN", "svp")
    timeout_s = int(os.environ.get("INVOICE_LIBREOFFICE_TIMEOUT", "180"))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        outdir = tmp / "out"
        outdir.mkdir()
        profile = tmp / "profile"
        profile.mkdir()
        user_inst = profile.resolve().as_uri()
        cmd = [
            soffice,
            f"-env:UserInstallation={user_inst}",
            "--headless",
            "--invisible",
            "--norestore",
            "--nologo",
            "--nofirststartwizard",
            "--convert-to",
            "pdf",
            "--outdir",
            str(outdir),
            str(xlsx_path.resolve()),
        ]
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise InvoiceError(
                f"LibreOffice PDF conversion timed out after {timeout_s}s."
            ) from exc
        if r.returncode != 0:
            msg = r.stderr or r.stdout or "LibreOffice failed"
            raise InvoiceError(msg)
        produced = outdir / (xlsx_path.stem + ".pdf")
        if not produced.is_file():
            raise InvoiceError(f"Expected PDF not found: {produced}")
        shutil.copy2(produced, pdf_path)
    return True


def convert_xlsx_to_pdf(xlsx_path: Path, pdf_path: Path) -> None:
    if _convert_xlsx_to_pdf_libreoffice(xlsx_path, pdf_path):
        return
    if sys.platform.startswith("linux"):
        hint = "On Debian/Ubuntu: sudo apt update && sudo apt install -y libreoffice-calc-nogui"
    elif sys.platform == "darwin":
        hint = (
            "Install: brew install --cask libreoffice "
            "or download from https://www.libreoffice.org/download/download/. "
            "If soffice is already installed elsewhere, set INVOICE_SOFFICE to its full path."
        )
    else:
        hint = "Install LibreOffice from https://www.libreoffice.org/download/download/"
    raise InvoiceError(
        "Could not create PDF. LibreOffice (soffice) is required for headless conversion. "
        f"{hint}"
    )


def resolve_xlsx_template(asset_dir: Path | None = None) -> Path | None:
    root = asset_dir or INVOICE_ASSET_DIR
    for name in XLSX_NAMES:
        p = root / name
        if p.is_file():
            return p
    return None


def format_date_dmy(d: date | datetime) -> str:
    """Day/month/year text, no time (e.g. 15/07/2025)."""
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime("%d/%m/%Y")


def apply_table_center_alignment_openpyxl(ws) -> None:
    for row in range(TABLE_HEADER_ROW, SUM_ROW + 1):
        for col in ALIGN_COLS:
            ws[f"{col}{row}"].alignment = CELL_CENTER
    ws["G3"].alignment = CELL_CENTER


def monday_of_week_au(day: date) -> date:
    """Return the Monday starting the Australian (ISO) week that contains ``day``."""
    return day - timedelta(days=day.weekday())


def output_pdf_path_for_week(week_monday: date, output_dir: Path) -> Path:
    """
    PDF name from ISO week of the invoice Monday: Week_{n}_{year}.pdf
    e.g. Week_1_2026.pdf for ISO week 1 of 2026.
    """
    iso_year, iso_week, _ = week_monday.isocalendar()
    return output_dir / f"Week_{iso_week}_{iso_year}.pdf"


def get_invoice_number(week_reference_date: date | datetime) -> int:
    """
    Invoice numbers count Monday-based weeks from INVOICE_WEEK_1_MONDAY:
    1 = week of 30 Jun–6 Jul 2025, 2 = week starting 7 Jul 2025, etc.
    """
    d = week_reference_date.date() if isinstance(week_reference_date, datetime) else week_reference_date
    ref_monday = monday_of_week_au(d)
    delta_weeks = (ref_monday - INVOICE_WEEK_1_MONDAY).days // 7
    return 1 + delta_weeks


def get_date(rows: list[tuple[datetime, int]]) -> str:
    """
    From ``rows``: Australian Mon–Sun week of the earliest date → week-end Sunday → +14 days
    (next-next Sunday), as DD/MM/YYYY.
    """
    earliest = min(dt.date() for dt, _ in rows)
    week_monday = monday_of_week_au(earliest)
    week_end_sunday = week_monday + timedelta(days=6)
    next_next_sunday = week_end_sunday + timedelta(days=14)
    return format_date_dmy(next_next_sunday)


def weekday_english(dt: datetime) -> str:
    return calendar.day_name[dt.weekday()]


def _maybe_add_branding_image(ws, asset_dir: Path) -> None:
    """
    openpyxl drops embedded pictures when saving; add a logo from a PNG next to the template.
    Set INVOICE_LOGO_ANCHOR (default A1), optional INVOICE_LOGO_WIDTH_PX / INVOICE_LOGO_HEIGHT_PX.
    """
    logo = asset_dir / "invoice_branding.png"
    if not logo.is_file():
        return
    try:
        from openpyxl.drawing.image import Image as XLImage
    except ImportError:
        return
    try:
        img = XLImage(str(logo))
        w = os.environ.get("INVOICE_LOGO_WIDTH_PX")
        h = os.environ.get("INVOICE_LOGO_HEIGHT_PX")
        if w:
            img.width = int(w)
        if h:
            img.height = int(h)
    except Exception:
        return
    anchor = os.environ.get("INVOICE_LOGO_ANCHOR", "A1")
    try:
        ws.add_image(img, anchor)
    except Exception:
        return


def fill_workbook(
    wb_path: Path,
    rows_data: list[tuple[datetime, int]],
    invoice_number: int,
    output_xlsx: Path,
    *,
    include_gst: bool = True,
    asset_dir: Path | None = None,
) -> None:
    wb = load_workbook(wb_path)
    ws = wb.active

    ws["G3"] = invoice_number

    for r in range(DATA_FIRST_ROW, DATA_LAST_ROW + 1):
        ws[f"B{r}"] = None
        ws[f"C{r}"] = None
        ws[f"E{r}"] = None
        ws[f"F{r}"] = None

    for i, (dt, parcels) in enumerate(rows_data):
        r = DATA_FIRST_ROW + i
        cell_b = ws[f"B{r}"]
        cell_b.value = format_date_dmy(dt)
        cell_b.number_format = "@"
        ws[f"C{r}"] = weekday_english(dt)
        ws[f"E{r}"] = parcels
        ws[f"F{r}"] = f"=E{r}*$G$12"

    first_r = DATA_FIRST_ROW
    last_r = DATA_LAST_ROW
    ws[f"E{SUM_ROW}"] = f"=SUM(E{first_r}:E{last_r})"
    ws[f"F{SUM_ROW}"] = f"=SUM(F{first_r}:F{last_r})"
    ws[f"B{SUM_ROW}"] = "TOTAL PAYABLE AMOUNT (GST)" if include_gst else "TOTAL PAYABLE AMOUNT "

    apply_table_center_alignment_openpyxl(ws)

    c_pay = ws[DATE_VALUE_CELL]
    c_pay.value = f"Date : {get_date(rows_data)}"
    c_pay.font = Font(name="Arial", bold=True)
    c_pay.alignment = CELL_LEFT
    c_pay.number_format = "@"

    ad = asset_dir or INVOICE_ASSET_DIR
    _maybe_add_branding_image(ws, ad)

    wb.save(output_xlsx)


def run_invoice_pipeline(
    rows: list[tuple[datetime, int]],
    *,
    output_dir: Path,
    include_gst: bool = True,
    asset_dir: Path | None = None,
) -> tuple[Path, Path, int]:
    """
    Build invoice XLSX + week-named PDF. Returns (xlsx_path, pdf_path, invoice_number).
    Session ``output_dir`` holds ``output.xlsx`` and ``Week_*_*.pdf`` (regenerated each run).
    """
    if not rows:
        raise InvoiceError("Add at least one delivery line.")
    max_lines = DATA_LAST_ROW - DATA_FIRST_ROW + 1
    if len(rows) > max_lines:
        raise InvoiceError(f"Too many rows (max {max_lines}).")

    ad = asset_dir or INVOICE_ASSET_DIR
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ref_date = min(dt.date() for dt, _ in rows)
    invoice_number = get_invoice_number(ref_date)
    week_monday = monday_of_week_au(ref_date)
    out_pdf = output_pdf_path_for_week(week_monday, output_dir)
    out_xlsx = output_dir / "output.xlsx"

    xlsx_tpl = resolve_xlsx_template(ad)
    if xlsx_tpl is None:
        raise InvoiceError(
            f"No invoice template in {ad}. Add {XLSX_NAMES[0]} "
            "(openpyxl fills cells; export from Numbers/Excel if you design there)."
        )

    fill_workbook(
        xlsx_tpl,
        rows,
        invoice_number,
        out_xlsx,
        include_gst=include_gst,
        asset_dir=ad,
    )
    convert_xlsx_to_pdf(out_xlsx, out_pdf)
    return out_xlsx, out_pdf, invoice_number
