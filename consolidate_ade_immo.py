#!/usr/bin/env python3
"""Deterministically consolidate BNP ADE IMMO workbooks.

Usage:
    python consolidate_ade_immo.py "C:\\data\\2026\\BNP\\ADE IMMO"
    python consolidate_ade_immo.py ./ADE_IMMO -o ./ADE_IMMO_consolide.xlsx

The reception month is always the immediate parent-folder name of each workbook
(for example ``04-2026``).  This deliberately records when old monthly files arrive
late: a January file delivered in ``04-2026`` is labelled ``04-2026``.

Requirements:
    python -m pip install pandas openpyxl
    # Only when old .xls files are present: python -m pip install xlrd

The script is intentionally self-contained: no configuration files, no network, no
LLM, and no guesses based on the filename.  It processes every worksheet that contains
an ADE IMMO header row and prints a JSON report at the end.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd


# A worksheet has 1,048,576 rows total.  Reserve one for the header.
EXCEL_MAX_DATA_ROWS = 1_048_575
SUPPORTED_EXTENSIONS = {".xlsx", ".xlsm", ".xltx", ".xltm", ".xls"}

# The fixed, human-readable target layout.  The source order changes between months;
# this order does not.
BUSINESS_COLUMNS = [
    "N° de police",
    "N° Tiers",
    "N° Dossier",
    "Type d'assuré",
    "Sexe",
    "Date de déblocage",
    "Taux",
    "Date de fin d'assurance",
    "Durée du prêt",
    "Montant assuré/financé",
    "Date 1ère échéance",
    "Prime BDD",
    "CRD",
    "Catégorie",
    "Taux de la prime d'assurance",
    "Observation",
    "Info surprime de surmortalité",
]
PROVENANCE_COLUMNS = [
    "Mois de réception",
    "Fichier source",
    "Feuille source",
    "Ligne source",
]
OUTPUT_COLUMNS = BUSINESS_COLUMNS + PROVENANCE_COLUMNS


def normalise(value: object) -> str:
    """Make a header comparable despite accents, punctuation and spacing changes."""
    text = "" if value is None else str(value)
    text = text.replace("N°", "numero ").replace("n°", "numero ")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().replace("1ere", "premiere").replace("1er", "premier")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_blank(value: object) -> bool:
    if value is None or str(value).strip() == "":
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def canonical_header(value: object) -> str | None:
    """Return the fixed output field corresponding to a source header, if known."""
    header = normalise(value)
    if not header:
        return None
    exact = {
        "numero de police": "N° de police",
        "numero police": "N° de police",
        "numero tiers": "N° Tiers",
        "numero dossier": "N° Dossier",
        "sexe": "Sexe",
        "date de deblocage": "Date de déblocage",
        "taux": "Taux",
        "date de fin d assurance": "Date de fin d'assurance",
        "duree du pret": "Durée du prêt",
        "montant assure finance": "Montant assuré/financé",
        "date premiere echeance": "Date 1ère échéance",
        "prime bdd": "Prime BDD",
        "categorie": "Catégorie",
        "taux de la prime d assurance": "Taux de la prime d'assurance",
        "observation": "Observation",
        "info surprime de surmortalite": "Info surprime de surmortalité",
    }
    if header in exact:
        return exact[header]
    # The CRD source heading is period-specific: "CRD au 31/01/2026", etc.
    if header == "crd" or header.startswith("crd au "):
        return "CRD"
    if header.startswith("type d assure emprunteur"):
        return "Type d'assuré"
    return None


def find_header_row(raw: pd.DataFrame, limit: int = 50) -> tuple[int | None, dict[int, str]]:
    """Find the strongest ADE IMMO header row in the first ``limit`` rows."""
    best_row: int | None = None
    best_mapping: dict[int, str] = {}
    best_score = 0
    for row_number in range(min(limit, len(raw))):
        mapping = {
            index: canonical
            for index, value in enumerate(raw.iloc[row_number].tolist())
            if (canonical := canonical_header(value)) is not None
        }
        # Six known headings is deliberately conservative: it avoids mistaking a
        # title, note, or a stray row for the actual table header.
        score = len(set(mapping.values()))
        if score > best_score:
            best_row, best_mapping, best_score = row_number, mapping, score
    return (best_row, best_mapping) if best_score >= 6 else (None, {})


def parse_number(value: object) -> float | None:
    """Parse French/Excel numeric values without silently coercing bad text to zero."""
    if is_blank(value):
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("\u00a0", " ").replace(" ", "")
    percent = "%" in text
    text = text.replace("%", "").replace("EUR", "").replace("€", "")
    # 1.234,56 is French thousands+decimal; 1,234.56 is English thousands+decimal.
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    return number / 100 if percent else number


def parse_date(value: object) -> pd.Timestamp | None:
    """Parse Excel serials, native dates and French text dates as real dates."""
    if is_blank(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.normalize()
    if isinstance(value, datetime):
        return pd.Timestamp(value.date())
    if isinstance(value, date):
        return pd.Timestamp(value)
    if isinstance(value, (int, float)) and 1 <= value <= 100_000:
        # Excel's 1900 date system, including its historical leap-year offset.
        return pd.Timestamp("1899-12-30") + timedelta(days=float(value))
    parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
    return None if pd.isna(parsed) else pd.Timestamp(parsed).normalize()


NUMERIC_COLUMNS = {
    "Taux", "Durée du prêt", "Montant assuré/financé", "Prime BDD", "CRD",
    "Taux de la prime d'assurance",
}
DATE_COLUMNS = {"Date de déblocage", "Date de fin d'assurance", "Date 1ère échéance"}
IDENTIFIER_COLUMNS = {"N° de police", "N° Tiers", "N° Dossier"}
TOTAL_MARKERS = ("total", "sous total", "cumul", "somme")


def clean_sheet(raw: pd.DataFrame, source: Path, sheet: str) -> tuple[pd.DataFrame | None, str | None]:
    """Extract and normalize one sheet, returning a reason when it is not a data sheet."""
    header_row, mapping = find_header_row(raw)
    if header_row is None:
        return None, "no ADE IMMO header row found"

    # If a malformed export repeats a field, retain the physical column with the most
    # populated values.  The choice is deterministic and reported in the terminal.
    by_field: dict[str, int] = {}
    data_after_header = raw.iloc[header_row + 1:].reset_index(drop=True)
    for index, field in mapping.items():
        populated = sum(not is_blank(value) for value in data_after_header.iloc[:, index])
        current = by_field.get(field)
        if current is None:
            by_field[field] = index
        else:
            current_populated = sum(
                not is_blank(value) for value in data_after_header.iloc[:, current]
            )
            if populated > current_populated:
                by_field[field] = index

    frame = pd.DataFrame(index=data_after_header.index)
    for field in BUSINESS_COLUMNS:
        if field in by_field:
            frame[field] = data_after_header.iloc[:, by_field[field]].tolist()
        else:
            frame[field] = None

    # A valid ADE sale must carry at least one identifier.  This removes blank gaps,
    # footer totals and notes without using their amount as an arbitrary signal.
    has_content = frame[BUSINESS_COLUMNS].apply(lambda row: any(not is_blank(v) for v in row), axis=1)
    has_identifier = frame[list(IDENTIFIER_COLUMNS)].apply(
        lambda row: any(not is_blank(v) for v in row), axis=1
    )
    contains_total_marker = frame[BUSINESS_COLUMNS].apply(
        lambda row: any(normalise(v).startswith(TOTAL_MARKERS) for v in row if not is_blank(v)),
        axis=1,
    )
    identifier_is_total = frame[list(IDENTIFIER_COLUMNS)].apply(
        lambda row: any(normalise(v).startswith(TOTAL_MARKERS) for v in row if not is_blank(v)),
        axis=1,
    )
    footer = contains_total_marker & (~has_identifier | identifier_is_total)
    frame = frame[has_content & has_identifier & ~footer].copy()
    if frame.empty:
        return None, "header found but no data rows remained after cleaning"

    for field in NUMERIC_COLUMNS:
        frame[field] = frame[field].map(parse_number)
    for field in DATE_COLUMNS:
        frame[field] = frame[field].map(parse_date)

    # Parent folder is the reception month by business rule, regardless of dates inside
    # the file.  This captures delayed delivery of one or several historic months.
    frame["Mois de réception"] = source.parent.name
    frame["Fichier source"] = source.name
    frame["Feuille source"] = sheet
    # pandas index zero is the first data row; Excel numbering starts at 1.
    frame["Ligne source"] = frame.index + header_row + 2
    return frame.reset_index(drop=True), None


def read_workbook(path: Path) -> Iterable[tuple[str, pd.DataFrame]]:
    """Yield every readable worksheet without assuming a particular tab name."""
    workbook = pd.ExcelFile(path)
    try:
        for sheet in workbook.sheet_names:
            yield sheet, pd.read_excel(workbook, sheet_name=sheet, header=None, dtype=object)
    finally:
        workbook.close()


def input_files(root: Path, output: Path) -> list[Path]:
    output = output.resolve()
    files = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if path.name.startswith("~$") or path.resolve() == output:
            continue
        files.append(path)
    return sorted(files, key=lambda path: str(path).lower())


def control_table(frame: pd.DataFrame) -> pd.DataFrame:
    """Make the source-level reconciliation needed to compare against manual totals."""
    controls = frame.groupby(
        ["Mois de réception", "Fichier source", "Feuille source"], dropna=False
    ).agg(
        Lignes=("Prime BDD", "size"),
        **{
            "Prime BDD - somme": ("Prime BDD", "sum"),
            "Prime BDD - valeurs vides": ("Prime BDD", lambda values: values.isna().sum()),
        },
    ).reset_index()
    total = pd.DataFrame([{
        "Mois de réception": "TOTAL",
        "Fichier source": "",
        "Feuille source": "",
        "Lignes": int(len(frame)),
        "Prime BDD - somme": frame["Prime BDD"].sum(),
        "Prime BDD - valeurs vides": int(frame["Prime BDD"].isna().sum()),
    }])
    return pd.concat([controls, total], ignore_index=True)


def write_output(frame: pd.DataFrame, output: Path) -> tuple[int, pd.DataFrame]:
    """Write all rows and a reconciliation sheet to one Excel-compatible workbook."""
    output.parent.mkdir(parents=True, exist_ok=True)
    sheets = max(1, math.ceil(len(frame) / EXCEL_MAX_DATA_ROWS))
    controls = control_table(frame)
    with pd.ExcelWriter(output, engine="openpyxl", datetime_format="DD/MM/YYYY") as writer:
        for part in range(sheets):
            chunk = frame.iloc[part * EXCEL_MAX_DATA_ROWS:(part + 1) * EXCEL_MAX_DATA_ROWS]
            name = "ADE IMMO" if sheets == 1 else f"ADE IMMO {part + 1:03d}"
            chunk.to_excel(writer, sheet_name=name, index=False)
            worksheet = writer.sheets[name]
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            for column in worksheet.columns:
                letter = column[0].column_letter
                longest = max(len(str(cell.value or "")) for cell in column[:500])
                worksheet.column_dimensions[letter].width = min(max(longest + 2, 12), 34)
        controls.to_excel(writer, sheet_name="Contrôle", index=False)
        control_sheet = writer.sheets["Contrôle"]
        control_sheet.freeze_panes = "A2"
        control_sheet.auto_filter.ref = control_sheet.dimensions
        for column in control_sheet.columns:
            letter = column[0].column_letter
            longest = max(len(str(cell.value or "")) for cell in column)
            control_sheet.column_dimensions[letter].width = min(max(longest + 2, 14), 42)
    return sheets, controls


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Clean and consolidate ADE IMMO workbooks with a fixed deterministic schema."
    )
    parser.add_argument("input_folder", type=Path, help="ADE IMMO root containing month folders")
    parser.add_argument("-o", "--output", type=Path, help="output .xlsx path")
    parser.add_argument(
        "--keep-exact-duplicates", action="store_true",
        help="keep exact copies found in the same reception month (normally removed)",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="return an error if any workbook/sheet cannot be consolidated",
    )
    args = parser.parse_args(argv)

    root = args.input_folder.expanduser().resolve()
    if not root.is_dir():
        print(f"Input folder does not exist: {root}", file=sys.stderr)
        return 2
    output = (args.output or root / "ADE_IMMO_consolide.xlsx").expanduser().resolve()
    if output.suffix.lower() != ".xlsx":
        print("Output must use the .xlsx extension.", file=sys.stderr)
        return 2

    report: dict[str, object] = {"input_folder": str(root), "output": str(output), "files": []}
    frames: list[pd.DataFrame] = []
    failures = 0
    for path in input_files(root, output):
        file_report: dict[str, object] = {"file": str(path), "sheets": []}
        try:
            sheets = list(read_workbook(path))
        except Exception as exc:  # Report unreadable files, but retain the rest of the month.
            failures += 1
            file_report["error"] = f"cannot read workbook: {exc}"
            report["files"].append(file_report)
            print(f"SKIP {path.name}: {file_report['error']}", file=sys.stderr)
            continue

        for sheet_name, raw in sheets:
            clean, reason = clean_sheet(raw, path, sheet_name)
            if clean is None:
                file_report["sheets"].append({"sheet": sheet_name, "status": "skipped", "reason": reason})
                continue
            frames.append(clean)
            file_report["sheets"].append({"sheet": sheet_name, "status": "included", "rows": len(clean)})
            print(f"OK   {path.name} | {sheet_name}: {len(clean)} rows")
        report["files"].append(file_report)

    if not frames:
        print("No ADE IMMO data table was found; no output was written.", file=sys.stderr)
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1

    consolidated = pd.concat(frames, ignore_index=True)[OUTPUT_COLUMNS]
    before = len(consolidated)
    if not args.keep_exact_duplicates:
        # Identical copies in the same monthly delivery are almost always duplicate
        # worksheets or resends.  A record in a different reception month is retained.
        duplicate_key = BUSINESS_COLUMNS + ["Mois de réception"]
        consolidated = consolidated.drop_duplicates(subset=duplicate_key, keep="first").reset_index(drop=True)
    parts, controls = write_output(consolidated, output)
    report.update({
        "rows_read": before,
        "rows_written": len(consolidated),
        "duplicates_removed": before - len(consolidated),
        "excel_sheets_written": parts,
        "failed_workbooks": failures,
        "prime_bdd_total": float(consolidated["Prime BDD"].sum(skipna=True)),
    })
    print("\nPrime BDD reconciliation (also written to the Contrôle sheet):")
    print(controls.to_string(index=False))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if args.strict and failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
