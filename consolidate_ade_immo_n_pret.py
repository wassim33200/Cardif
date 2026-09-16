#!/usr/bin/env python3
"""Deterministically consolidate ADE IMMO in the N_PRET / MNT_PRIMES_GLOBAL format.

Usage:
    python consolidate_ade_immo_n_pret.py "C:\\data\\2026\\BANK\\ADE IMMO"
    python consolidate_ade_immo_n_pret.py ./ADE_IMMO -o ./ADE_IMMO_N_PRET_consolide.xlsx

The reception month is always the immediate parent-folder name of each workbook
(for example ``04-2026``).  This deliberately records when old monthly files arrive
late: a January file delivered in ``04-2026`` is labelled ``04-2026``.

The 31 input columns follow the supplied bank screenshot. The second CODE_ASS_CDG
is preserved as CODE_ASS_CDG_2, identified by left-to-right occurrence. MOIS and ANNEE
are retained exactly as reported, separately from the folder-based reception month.
MNT_PRIMES_GLOBAL is the premium being reconciled; PRIME_MAJ and SUBPRIME2 stay separate.
Input workbooks must have horizontal header rows; the image is a field inventory.

Requirements:
    python -m pip install pandas openpyxl
    # Only when old .xls files are present: python -m pip install xlrd

The script is intentionally self-contained: no configuration files, no network, no
LLM, and no guesses based on the filename.  It processes every worksheet that contains
an ADE IMMO header row only when worksheet selection is unambiguous, and prints a JSON
report. Use --sheet "Ventes" to choose a worksheet, or --all-sheets to include several.
Duplicate business rows block export until --keep-exact-duplicates is explicitly used.
Invalid values and ambiguous rows block export; they are never silently discarded.
Use --expected-rows 12345 --expected-prime 123456.78 to enforce your manual totals.
The Contrôle tab contains per-source subtotals, without a second grand-total row.
Keep source workbooks in the input folder and put your output outside that folder.
Each run also writes individual diagnostic workbooks under cleaned_files/run-*/,
mirroring the source folders. These retain all accepted rows, including duplicates,
even if the combined export is blocked. See report.json for errors and skipped sheets.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
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
    "N_PRET",
    "DOSSIER",
    "CODE_AGENC",
    "MNT_ACCORD",
    "CRD",
    "CLIENT",
    "NUM_ASSUR",
    "TYPE_ASSUR",
    "DATE_MOBILISATION",
    "DATE_FIN_CREDIT",
    "DUREE_CREDIT",
    "DUREE_REMB",
    "ASSURANCE",
    "TYPE_ASSURANCE",
    "TAUX_PRIME",
    "MNT_PRIMES_GLOBAL",
    "TYPE_CREDIT",
    "LIBELLE_CREDIT",
    "TAUX_CREDIT",
    "TAUX_SUBPRIME",
    "PRIME_MAJ",
    "TAUX_SUBPRIME2",
    "SUBPRIME2",
    "STATUT_POLICE_ASSURANCE",
    "NUM_CONTRA",
    "CODE_C",
    "MOIS",
    "ANNEE",
    "CODE_ASS_CDG",
    "cle",
    "CODE_ASS_CDG_2"
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
    """Exact normalized source headings; no inferred banking equivalences."""
    header = normalise(value)
    return next((name for name in BUSINESS_COLUMNS if normalise(name) == header), None)


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
        occurrences = [i for i, field in mapping.items() if field == "CODE_ASS_CDG"]
        if len(occurrences) == 2 and "CODE_ASS_CDG_2" not in mapping.values():
            mapping[occurrences[1]] = "CODE_ASS_CDG_2"
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


NUMERIC_COLUMNS = {"MNT_ACCORD", "CRD", "DUREE_CREDIT", "DUREE_REMB",
                   "TAUX_PRIME", "MNT_PRIMES_GLOBAL", "TAUX_CREDIT", "TAUX_SUBPRIME",
                   "PRIME_MAJ", "TAUX_SUBPRIME2", "SUBPRIME2"}
DATE_COLUMNS = {"DATE_MOBILISATION", "DATE_FIN_CREDIT"}
IDENTIFIER_COLUMNS = {"N_PRET", "DOSSIER", "CLIENT", "NUM_ASSUR", "NUM_CONTRA"}


def clean_sheet(raw: pd.DataFrame, source: Path, sheet: str) -> tuple[pd.DataFrame | None, str | None]:
    """Extract and normalize one sheet, returning a reason when it is not a data sheet."""
    header_row, mapping = find_header_row(raw)
    if header_row is None:
        return None, "no ADE IMMO header row found"

    if len(mapping.values()) != len(set(mapping.values())):
        raise ValueError(f"{source.name}/{sheet}: duplicate column headings; mapping ambiguous")
    if not {"N_PRET", "MNT_PRIMES_GLOBAL"} <= set(mapping.values()):
        raise ValueError(f"{source.name}/{sheet}: N_PRET or MNT_PRIMES_GLOBAL heading missing")
    if any(normalise(v) == "ligne source" for v in raw.iloc[header_row]):
        return None, "generated consolidation detected; excluded"

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

    repeated_header = data_after_header.apply(
        lambda row: sum(canonical_header(v) is not None for v in row) >= 6, axis=1
    )
    has_content = frame[BUSINESS_COLUMNS].apply(lambda row: any(not is_blank(v) for v in row), axis=1)
    has_identifier = frame[list(IDENTIFIER_COLUMNS)].apply(
        lambda row: any(not is_blank(v) for v in row), axis=1
    )
    identifier_is_total = frame[list(IDENTIFIER_COLUMNS)].apply(
        lambda row: any(re.fullmatch(r"(?:total|sous total|cumul|somme)(?: general| generale)?", normalise(v)) for v in row if not is_blank(v)),
        axis=1,
    )
    footer = identifier_is_total & ~repeated_header
    ambiguous = has_content & ~has_identifier & ~footer & ~repeated_header
    if ambiguous.any():
        positions = (frame.index[ambiguous] + header_row + 2).tolist()[:20]
        raise ValueError(f"{source.name}/{sheet}: rows without identifiers require review: {positions}")
    audit = {"rows_after_header": len(frame), "blank_rows": int((~has_content).sum()),
             "repeated_headers": int(repeated_header.sum()), "footer_rows": int(footer.sum()),
             "excluded_header_positions": (frame.index[repeated_header] + header_row + 2).tolist(),
             "excluded_footer_positions": (frame.index[footer] + header_row + 2).tolist()}
    frame = frame[has_content & ~footer & ~repeated_header].copy()
    if frame.empty:
        return None, "header found but no data rows remained after cleaning"
    if frame["MNT_PRIMES_GLOBAL"].map(is_blank).any():
        positions = (frame.index[frame["MNT_PRIMES_GLOBAL"].map(is_blank)] + header_row + 2).tolist()[:20]
        raise ValueError(f"{source.name}/{sheet}: missing MNT_PRIMES_GLOBAL at rows {positions}; check source cells and formula caches")

    for field in NUMERIC_COLUMNS:
        original = frame[field]
        converted = original.map(parse_number)
        invalid = ~original.map(is_blank) & (converted.isna() | converted.map(lambda v: not is_blank(v) and not math.isfinite(v)))
        if invalid.any():
            positions = (frame.index[invalid] + header_row + 2).tolist()[:20]
            raise ValueError(f"{source.name}/{sheet}: invalid numbers in {field}, rows {positions}")
        frame[field] = converted
    for field in DATE_COLUMNS:
        original = frame[field]
        converted = original.map(parse_date)
        if (~original.map(is_blank) & converted.isna()).any():
            raise ValueError(f"{source.name}/{sheet}: invalid dates in {field}")
        frame[field] = converted

    assert len(frame) + audit["blank_rows"] + audit["repeated_headers"] + audit["footer_rows"] == audit["rows_after_header"]
    # Parent folder is the reception month by business rule, regardless of dates inside
    # the file.  This captures delayed delivery of one or several historic months.
    frame["Mois de réception"] = source.parent.name
    frame["Fichier source"] = str(source.resolve())
    frame["Feuille source"] = sheet
    # pandas index zero is the first data row; Excel numbering starts at 1.
    frame["Ligne source"] = frame.index + header_row + 2
    frame = frame.reset_index(drop=True)
    frame.attrs["audit"] = audit
    return frame, None


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
        Lignes=("MNT_PRIMES_GLOBAL", "size"),
        **{
            "MNT_PRIMES_GLOBAL - somme": ("MNT_PRIMES_GLOBAL", "sum"),
            "MNT_PRIMES_GLOBAL - valeurs vides": ("MNT_PRIMES_GLOBAL", lambda values: values.isna().sum()),
        },
    ).reset_index()
    return controls


def write_output(frame: pd.DataFrame, output: Path, diagnostic_report: dict | None = None) -> tuple[int, pd.DataFrame]:
    """Write all rows and a reconciliation sheet to one Excel-compatible workbook."""
    output.parent.mkdir(parents=True, exist_ok=True)
    sheets = max(1, math.ceil(len(frame) / EXCEL_MAX_DATA_ROWS))
    controls = control_table(frame)
    with pd.ExcelWriter(output, engine="openpyxl", datetime_format="DD/MM/YYYY") as writer:
        if diagnostic_report is not None:
            pd.DataFrame({"Diagnostic": [
                "Individual source export BEFORE global checks. Duplicate rows retained.",
                "PARTIAL: some worksheets failed." if any("error" in s for s in diagnostic_report["sheets"])
                else "Review worksheet selection and source totals before using these rows.",
                diagnostic_report.get("error", ""),
            ]}).to_excel(writer, sheet_name="À lire", index=False)
            pd.DataFrame(diagnostic_report["sheets"]).to_excel(writer, sheet_name="Journal", index=False)
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
    parser.add_argument("--cleaned-dir", type=Path,
                        help="individual diagnostic exports (default: cleaned_files beside combined output)")
    parser.add_argument(
        "--keep-exact-duplicates", action="store_true",
        help="explicitly retain duplicate business rows; default stops for review",
    )
    parser.add_argument("--sheet", action="append", help="exact worksheet name; repeat for several sheets")
    parser.add_argument("--all-sheets", action="store_true", help="explicitly include multiple data worksheets")
    parser.add_argument("--expected-rows", type=int, help="refuse export unless this row count matches")
    parser.add_argument("--expected-prime", type=float, help="refuse export unless MNT_PRIMES_GLOBAL matches within 0.01")
    parser.add_argument(
        "--strict", action="store_true",
        help="return an error if any workbook/sheet cannot be consolidated",
    )
    args = parser.parse_args(argv)

    root = args.input_folder.expanduser().resolve()
    if not root.is_dir():
        print(f"Input folder does not exist: {root}", file=sys.stderr)
        return 2
    output = (args.output or root / "ADE_IMMO_N_PRET_consolide.xlsx").expanduser().resolve()
    if output.suffix.lower() != ".xlsx":
        print("Output must use the .xlsx extension.", file=sys.stderr)
        return 2

    cleaned_base = (args.cleaned_dir or output.parent / "cleaned_files").expanduser().resolve()
    if root == cleaned_base or root.is_relative_to(cleaned_base):
        print("Cleaned directory must not contain the input root.", file=sys.stderr)
        return 2
    sources = [p for p in input_files(root, output) if not p.resolve().is_relative_to(cleaned_base)]
    cleaned_base.mkdir(parents=True, exist_ok=True)
    cleaned_run = Path(tempfile.mkdtemp(prefix="run-", dir=cleaned_base))
    print(f"Individual diagnostic exports: {cleaned_run}")

    report: dict[str, object] = {"input_folder": str(root), "output": str(output),
                               "cleaned_directory": str(cleaned_run),
                               "purpose": "Diagnostic rows before global duplicate and expected-total checks; partial files are explicitly marked.",
                               "files": []}
    frames: list[pd.DataFrame] = []
    failures = 0
    for path in sources:
        file_frames = []
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
            header_row, _ = find_header_row(raw)
            if header_row is not None and any(normalise(v) == "ligne source" for v in raw.iloc[header_row]):
                file_report["sheets"].append({"sheet": sheet_name, "status": "skipped",
                    "reason": "generated consolidation detected; excluded"})
                continue
            if args.sheet and sheet_name not in args.sheet:
                continue
            try:
                clean, reason = clean_sheet(raw, path, sheet_name)
            except (ValueError, TypeError, OverflowError) as exc:
                failures += 1
                file_report["sheets"].append({"sheet": sheet_name, "error": str(exc)})
                print(str(exc), file=sys.stderr)
                continue
            if clean is None:
                file_report["sheets"].append({"sheet": sheet_name, "status": "skipped", "reason": reason})
                continue
            frames.append(clean)
            file_frames.append(clean)
            file_report["sheets"].append({"sheet": sheet_name, "status": "included", "rows": len(clean),
                                          "prime": float(clean["MNT_PRIMES_GLOBAL"].sum()), **clean.attrs["audit"]})
            print(f"OK   {path.name} | {sheet_name}: {len(clean)} rows")
        included = sum(s.get("status") == "included" for s in file_report["sheets"])
        if included > 1 and not (args.all_sheets or args.sheet):
            failures += 1
            file_report["error"] = "multiple data sheets: select --sheet NAME or explicitly use --all-sheets"
        if not included and not any(s.get("reason", "").startswith("generated consolidation") for s in file_report["sheets"]):
            failures += 1
            file_report["error"] = "no usable data sheet"
        if file_frames:
            relative = path.relative_to(root)
            # Retain the original extension in the name to distinguish .xls/.xlsx siblings.
            target = cleaned_run / relative.parent / f"{relative.name}.cleaned.xlsx"
            diagnostic = pd.concat(file_frames, ignore_index=True)[OUTPUT_COLUMNS]
            write_output(diagnostic, target, diagnostic_report=file_report)
            partial = any("error" in s for s in file_report["sheets"])
            file_report.update({"cleaned_file": str(target), "partial": partial,
                                "diagnostic_rows": len(diagnostic),
                                "diagnostic_prime": float(diagnostic["MNT_PRIMES_GLOBAL"].sum())})
            print(f"CLEANED {'(PARTIAL) ' if partial else ''}{target} | "
                  f"{len(diagnostic)} rows | MNT_PRIMES_GLOBAL={diagnostic['MNT_PRIMES_GLOBAL'].sum():.2f}")
        report["files"].append(file_report)

    report_path = cleaned_run / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Diagnostic report: {report_path}")
    if failures:
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
        print("Combined export blocked; individual diagnostics are available; existing combined output was preserved.", file=sys.stderr)
        return 2

    if not frames:
        print("No ADE IMMO data table was found; no output was written.", file=sys.stderr)
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1

    consolidated = pd.concat(frames, ignore_index=True)[OUTPUT_COLUMNS]
    before = len(consolidated)
    if not args.keep_exact_duplicates:
        duplicates = consolidated.duplicated(subset=BUSINESS_COLUMNS, keep=False)
        if duplicates.any():
            print("Duplicate business rows require review. They may be legitimate monthly records or copies.", file=sys.stderr)
            print(consolidated.loc[duplicates, PROVENANCE_COLUMNS].head(40).to_string(index=False), file=sys.stderr)
            print("Use --keep-exact-duplicates only if these records should all be included.", file=sys.stderr)
            return 2
    if args.expected_rows is not None and len(consolidated) != args.expected_rows:
        print(f"Row mismatch: actual={len(consolidated)}, expected={args.expected_rows}", file=sys.stderr)
        return 2
    prime = float(consolidated["MNT_PRIMES_GLOBAL"].sum())
    if args.expected_prime is not None and abs(prime - args.expected_prime) > 0.0100001:
        print(f"Prime mismatch: actual={prime}, expected={args.expected_prime}", file=sys.stderr)
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(suffix=".xlsx", dir=output.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        parts, controls = write_output(consolidated, temporary)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    report.update({
        "rows_read": before,
        "rows_written": len(consolidated),
        "duplicates_removed": before - len(consolidated),
        "excel_sheets_written": parts,
        "failed_workbooks": failures,
        "mnt_primes_global_total": float(consolidated["MNT_PRIMES_GLOBAL"].sum(skipna=True)),
    })
    print("\nMNT_PRIMES_GLOBAL reconciliation (also written to the Contrôle sheet):")
    print(controls.to_string(index=False))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if args.strict and failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
