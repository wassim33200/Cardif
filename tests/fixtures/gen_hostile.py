"""Deliberately hostile workbooks.

The generated corpus in :mod:`gen_fixtures` is messy in the ways the banks are messy.
This module is messy in the ways that *break software*: files that are not workbooks at
all, sheets with nothing in them, formulas with no cached result, a month delivered
twice under two names, headers that collide, tables that do not start at column A.

Each builder returns a description of what the pipeline is expected to do, so the tests
assert on intent rather than on whatever the code happens to do today.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from openpyxl import Workbook


def _sheet(rows: list[list], title: str = "Feuil1") -> Workbook:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = title
    for row in rows:
        worksheet.append(row)
    return workbook


def valid_baseline(path: Path, rows: int = 8, month: int = 3) -> None:
    """A perfectly ordinary file, used to prove the bad ones are what fail."""
    data = [["N° Contrat", "Prime nette", "Frais", "Prime totale", "Date d'effet"]]
    for i in range(rows):
        data.append([f"C-{i:03d}", 100.0, 10.0, 110.0, dt.date(2025, month, 5)])
    _sheet(data).save(path)


# -- files that are not usable workbooks -------------------------------------------

def not_a_workbook(path: Path) -> None:
    path.write_bytes(b"this is plain text, not a spreadsheet")


def zero_bytes(path: Path) -> None:
    path.write_bytes(b"")


def csv_with_xlsx_extension(path: Path) -> None:
    path.write_text("num,prime\nC-1,100\n", encoding="utf-8")


def truncated_zip(path: Path, source: Path) -> None:
    """A workbook cut off mid-transfer, which is what a failed copy leaves behind."""
    data = source.read_bytes()
    path.write_bytes(data[: len(data) // 2])


# -- valid workbooks with nothing usable in them ------------------------------------

def empty_workbook(path: Path) -> None:
    Workbook().save(path)


def header_but_no_data(path: Path) -> None:
    _sheet([["N° Contrat", "Prime totale", "Date d'effet"]]).save(path)


def only_scratch_numbers(path: Path) -> None:
    _sheet([[None, 1234.5, None] for _ in range(10)]).save(path)


def blank_rows_only(path: Path) -> None:
    _sheet([[None, None, None] for _ in range(50)]).save(path)


# -- valid workbooks that are structurally nasty ------------------------------------

def uncached_formulas(path: Path) -> None:
    """The dangerous one: a premium column that is a formula with no saved result.

    openpyxl reads such cells as empty, so the column would be dropped and the file
    would consolidate with its premium missing.
    """
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["N° Contrat", "Prime nette", "Frais", "Prime totale", "Date d'effet"])
    for i in range(6):
        row = i + 2
        worksheet.append(
            [f"C-{i}", 100.0, 10.0, f"=B{row}+C{row}", dt.date(2025, 3, 5)]
        )
    workbook.save(path)


def table_offset_from_origin(path: Path) -> None:
    """The table starts at column D, with three empty columns to its left."""
    data = [[None, None, None, "N° Contrat", "Prime totale", "Date d'effet"]]
    for i in range(6):
        data.append([None, None, None, f"C-{i}", 100.0, dt.date(2025, 3, 5)])
    _sheet(data).save(path)


def colliding_headers(path: Path) -> None:
    """Two columns that both name the same field."""
    data = [["N° Contrat", "Prime totale", "Montant prime", "Date d'effet"]]
    for i in range(6):
        data.append([f"C-{i}", 100.0, 100.0, dt.date(2025, 3, 5)])
    _sheet(data).save(path)


def data_resuming_after_a_gap(path: Path) -> None:
    """Rows that look like data, far below the table, after several blank rows."""
    data = [["N° Contrat", "Prime totale", "Date d'effet"]]
    for i in range(6):
        data.append([f"C-{i}", 100.0, dt.date(2025, 3, 5)])
    data += [[None, None, None]] * 5
    for i in range(3):
        data.append([f"ORPHAN-{i}", 999.0, dt.date(2025, 3, 5)])
    _sheet(data).save(path)


def merged_cells_across_data(path: Path) -> None:
    """An agency name merged down a block of rows, as branch listings often are."""
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["N° Contrat", "Agence", "Prime totale", "Date d'effet"])
    for i in range(6):
        worksheet.append([f"C-{i}", None, 100.0, dt.date(2025, 3, 5)])
    worksheet.merge_cells(start_row=2, start_column=2, end_row=7, end_column=2)
    worksheet.cell(row=2, column=2, value="Tunis Centre")
    workbook.save(path)


def junk_sheet_before_data(path: Path) -> None:
    """A parameters tab sitting in front of the sales tab."""
    workbook = Workbook()
    first = workbook.active
    first.title = "Paramètres"
    first.append(["Taux", 0.05])
    first.append(["Devise", "TND"])
    second = workbook.create_sheet("Ventes")
    second.append(["N° Contrat", "Prime totale", "Date d'effet"])
    for i in range(12):
        second.append([f"C-{i}", 100.0, dt.date(2025, 3, 5)])
    workbook.save(path)


def extreme_values(path: Path) -> None:
    """Values at the edges: enormous, tiny, negative, zero, and an impossible date."""
    data = [["N° Contrat", "Prime totale", "Date d'effet"]]
    data.append(["C-1", 1e15, dt.date(2025, 3, 5)])
    data.append(["C-2", 0.001, dt.date(2025, 3, 5)])
    data.append(["C-3", -500.0, dt.date(2025, 3, 5)])
    data.append(["C-4", 0, "99/99/9999"])
    data.append(["C-5", 100.0, dt.date(2025, 3, 5)])
    _sheet(data).save(path)


def unicode_and_long_headers(path: Path) -> None:
    data = [["N° Contrat 📋", "Prime totale (€)", "Date d'effet", "X" * 300]]
    for i in range(5):
        data.append([f"C-{i}", 100.0, dt.date(2025, 3, 5), "y"])
    _sheet(data).save(path)


def one_data_row(path: Path) -> None:
    _sheet([
        ["N° Contrat", "Prime totale", "Date d'effet"],
        ["C-1", 100.0, dt.date(2025, 3, 5)],
    ]).save(path)


def numbers_as_text_mixed(path: Path) -> None:
    """French, Anglo and space-grouped amounts in one column."""
    data = [["N° Contrat", "Prime totale", "Date d'effet"]]
    for i, amount in enumerate(["1 234,56", "2 500,00", "987,20", "1.234,56", "45,00"]):
        data.append([f"C-{i}", amount, "05/03/2025"])
    _sheet(data).save(path)


def identifiers_mangled_by_excel(path: Path) -> None:
    """Contract numbers Excel has already turned into floats."""
    data = [["N° Contrat", "Prime totale", "Date d'effet"]]
    for i, contract in enumerate([20250001.0, 20250002.0, 1.23457e11]):
        data.append([contract, 100.0, dt.date(2025, 3, 5)])
    _sheet(data).save(path)


def build_all(root: Path) -> dict[str, Path]:
    """Write every hostile case into a bank folder. Returns name -> path."""
    folder = root / "BNA 2025"
    folder.mkdir(parents=True, exist_ok=True)

    good = folder / "Ventes_Janvier_2025.xlsx"
    valid_baseline(good, month=1)

    cases: dict[str, Path] = {"valid_baseline": good}

    def add(name: str, filename: str, builder, *args) -> None:
        path = folder / filename
        builder(path, *args)
        cases[name] = path

    add("not_a_workbook", "Ventes_Fevrier_2025.xlsx", not_a_workbook)
    add("zero_bytes", "Ventes_Mars_2025.xlsx", zero_bytes)
    add("csv_with_xlsx_extension", "Ventes_Avril_2025.xlsx", csv_with_xlsx_extension)
    add("truncated_zip", "Ventes_Mai_2025.xlsx", truncated_zip, good)
    add("empty_workbook", "Ventes_Juin_2025.xlsx", empty_workbook)
    add("header_but_no_data", "Ventes_Juillet_2025.xlsx", header_but_no_data)
    add("only_scratch_numbers", "Ventes_Aout_2025.xlsx", only_scratch_numbers)
    add("blank_rows_only", "Ventes_Septembre_2025.xlsx", blank_rows_only)
    add("uncached_formulas", "Ventes_Octobre_2025.xlsx", uncached_formulas)
    add("table_offset_from_origin", "Ventes_Novembre_2025.xlsx", table_offset_from_origin)
    add("colliding_headers", "Ventes_Decembre_2025.xlsx", colliding_headers)
    return cases
