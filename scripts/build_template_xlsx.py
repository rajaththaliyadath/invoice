#!/usr/bin/env python3
"""
Generate assets/invoice/Template.xlsx for Linux servers (openpyxl fill + LibreOffice PDF).
Re-run after layout changes; replace with an export from Numbers if you need exact branding.
"""
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

OUT = Path(__file__).resolve().parent.parent / "assets" / "invoice" / "Template.xlsx"


def main() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoice"

    ws.merge_cells("A1:G1")
    ws["A1"] = "TAX INVOICE"
    ws["A1"].font = Font(size=18, bold=True)
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")

    ws["E3"] = "Invoice #"
    ws["E3"].font = Font(bold=True)
    ws["E3"].alignment = Alignment(horizontal="right", vertical="center")

    for addr, label in (
        ("B16", "Date"),
        ("C16", "Day"),
        ("E16", "Parcels"),
        ("F16", "Amount"),
    ):
        ws[addr] = label
        ws[addr].font = Font(bold=True)
        ws[addr].alignment = Alignment(horizontal="center", vertical="center")

    ws["G14"] = "Rate / parcel"
    ws["G15"] = 1.0
    ws["G15"].number_format = "#,##0.00"

    for col, w in ("B", 14), ("C", 12), ("E", 10), ("F", 14), ("G", 14):
        ws.column_dimensions[col].width = w

    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
