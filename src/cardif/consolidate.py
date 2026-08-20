"""Turn one workbook into tidy, typed, provenance-carrying rows.

This is where the structural pass (:mod:`cardif.detect`), the header resolution
(:mod:`cardif.mapping`) and the type coercion (:mod:`cardif.coerce`) are combined, the
declared export profile is applied, and missing fields are derived where the schema says
they can be.

Two rules govern everything here:

* **Nothing is invented silently.** A derived value is labelled as derived, in its own
  ``*_source`` column, so a figure on a dashboard can always be traced to whether the
  bank reported it or the tool computed it.
* **Nothing is lost silently.** Rows the pipeline cannot use are still counted and
  reported; only structural junk identified by :mod:`cardif.detect` is removed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from .coerce import ColumnReport, coerce_column
from .config import Config
from .detect import SheetTable, extract_table, uncached_formula_columns
from .filemeta import FileMeta
from .mapping import Mapper, MappingResult

# Columns the pipeline attaches itself. They are not user-configurable: they are what
# makes a number on a dashboard traceable back to a cell in a file.
PROVENANCE_COLUMNS = ["source_file", "source_sheet", "source_row", "ingested_at", "row_hash"]


@dataclass
class FileResult:
    """Everything that happened to one workbook."""

    meta: FileMeta
    sheet: str = ""
    table: SheetTable | None = None
    mappings: list[MappingResult] = field(default_factory=list)
    frame: pd.DataFrame | None = None
    column_reports: list[ColumnReport] = field(default_factory=list)
    derived: dict[str, str] = field(default_factory=dict)
    missing_required: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.frame is not None and not self.frame.empty

    @property
    def n_rows(self) -> int:
        return 0 if self.frame is None else len(self.frame)

    @property
    def unresolved_headers(self) -> list[MappingResult]:
        return [m for m in self.mappings if m.canonical is None and m.normalized]

    @property
    def needs_review(self) -> list[MappingResult]:
        return [m for m in self.mappings if m.needs_review and m.normalized]


def choose_sheet(workbook, config: Config, bank: str | None) -> str:
    """Pick the sheet holding the sales table.

    Most files have one usable sheet, but banks sometimes leave a parameters or notes
    tab alongside it. Preference order from ``banks.yaml`` wins if it matches; otherwise
    the sheet with the most non-empty cells is used, which reliably picks the data over
    a stray notes tab.
    """
    names = workbook.sheetnames
    if len(names) == 1:
        return names[0]

    override = config.banks.overrides.get(bank or "")
    if override and override.sheet_preference:
        wanted = {n.lower() for n in override.sheet_preference}
        for name in names:
            if name.lower() in wanted:
                return name

    best, best_score = names[0], -1
    for name in names:
        ws = workbook[name]
        score = sum(
            1
            for row in ws.iter_rows(max_row=min(ws.max_row, 200))
            for cell in row
            if cell.value is not None
        )
        if score > best_score:
            best, best_score = name, score
    return best


def _row_hash(values: list, source_file: str) -> str:
    """A stable identity for a row, used to make re-ingesting a file idempotent.

    The source file is included so that the same contract legitimately appearing in two
    monthly files produces two distinct rows; cross-file duplicates are a *reporting*
    question, surfaced by :mod:`cardif.validate`, not something to silently collapse
    here.
    """
    payload = "|".join("" if v is None else str(v) for v in values)
    return hashlib.sha256(f"{source_file}::{payload}".encode("utf-8")).hexdigest()[:16]


def apply_derivations(
    columns: dict[str, list], config: Config, wanted: list[str], n_rows: int
) -> dict[str, str]:
    """Fill fields a bank did not report but whose inputs are present.

    A bank that sends ``prime nette`` and ``frais`` but no total gets ``prime_totale``
    computed and tagged ``derived``; a bank that sends the total directly is tagged
    ``reported``. Rows where neither is available keep ``None`` and are caught by the
    required-field check.
    """
    applied: dict[str, str] = {}

    for name in wanted:
        field_def = config.schema_.fields.get(name)
        if field_def is None or not field_def.derive:
            continue

        present = columns.get(name)
        already = present is not None and any(v is not None for v in present)
        if already and all(v is not None for v in present):
            continue

        for rule in field_def.derive:
            inputs = [columns.get(src) for src in rule.from_]
            if any(col is None for col in inputs):
                continue

            values = list(present) if present is not None else [None] * n_rows
            filled = 0
            for i in range(n_rows):
                if values[i] is not None:
                    continue
                parts = [col[i] for col in inputs]
                if any(p is None for p in parts):
                    continue
                if rule.rule == "sum":
                    values[i] = round(sum(parts), 2)
                elif rule.rule == "difference":
                    values[i] = round(parts[0] - sum(parts[1:]), 2)
                filled += 1

            if filled:
                columns[name] = values
                applied[name] = (
                    f"{filled} of {n_rows} values computed as "
                    f"{rule.rule}({', '.join(rule.from_)})"
                )
                present = values
            if present is not None and all(v is not None for v in present):
                break

    return applied


def build_frame(
    table: SheetTable,
    mappings: list[MappingResult],
    meta: FileMeta,
    config: Config,
    profile_name: str,
) -> tuple[pd.DataFrame, list[ColumnReport], dict[str, str], list[str]]:
    """Assemble the tidy frame for one sheet according to the export profile."""
    profile = config.schema_.profiles[profile_name]
    schema = config.schema_

    # Which sheet column feeds which canonical field.
    by_field = {m.canonical: m for m in mappings if m.canonical}

    n_rows = len(table.rows)
    columns: dict[str, list] = {}
    reports: list[ColumnReport] = []

    # Coerce every mapped field that the profile asks for, plus any field a derivation
    # needs as an input even when the profile itself does not include it.
    needed = set(profile.columns)
    for name in profile.columns:
        field_def = schema.fields.get(name)
        if field_def:
            for rule in field_def.derive:
                needed.update(rule.from_)

    for name in needed:
        field_def = schema.fields.get(name)
        if field_def is None:
            continue  # derived_fields such as banque are attached below
        mapping = by_field.get(name)
        if mapping is None:
            continue
        raw = [row[mapping.column_index] for row in table.rows]
        values, report = coerce_column(raw, field_def.type, mapping.header)
        columns[name] = values
        reports.append(report)

    # Record which fields the bank actually reported, before derivations fill any gaps.
    reported = {name for name, values in columns.items() if any(v is not None for v in values)}
    derived = apply_derivations(columns, config, list(profile.columns), n_rows)

    # --- assemble in the profile's declared order --------------------------------
    data: dict[str, list] = {}
    for name in profile.columns:
        if name == "banque":
            data[name] = [meta.bank_code] * n_rows
        elif name == "mois_reception":
            data[name] = [meta.period] * n_rows
        elif name == "annee_reception":
            data[name] = [meta.year] * n_rows
        else:
            data[name] = columns.get(name, [None] * n_rows)

    # A source marker for every field that has a derivation rule, so a dashboard can
    # always tell a reported figure from a computed one.
    for name in profile.columns:
        field_def = schema.fields.get(name)
        if field_def and field_def.derive:
            if name in derived:
                data[f"{name}_source"] = [
                    "reported" if name in reported and v is not None else "derived"
                    for v in data[name]
                ]
            else:
                data[f"{name}_source"] = [
                    "reported" if v is not None else None for v in data[name]
                ]

    # --- provenance ---------------------------------------------------------------
    source_file = str(meta.path)
    ingested = datetime.now(timezone.utc).isoformat(timespec="seconds")
    business_columns = [c for c in profile.columns]
    data["source_file"] = [source_file] * n_rows
    data["source_sheet"] = [table.sheet] * n_rows
    data["source_row"] = table.source_rows[:n_rows]
    data["ingested_at"] = [ingested] * n_rows
    data["row_hash"] = [
        _row_hash([data[c][i] for c in business_columns], source_file)
        for i in range(n_rows)
    ]

    frame = pd.DataFrame(data)

    # Give date fields a real datetime dtype rather than leaving them as objects.
    # Parquet then carries a genuine date type, which is what lets Power BI relate the
    # fact table to a date dimension and use time intelligence at all.
    for name in profile.columns:
        field_def = schema.fields.get(name)
        if field_def and field_def.type == "date" and name in frame.columns:
            frame[name] = pd.to_datetime(frame[name], errors="coerce")

    missing_required = [
        name for name in profile.required
        if name not in frame.columns or frame[name].isna().all()
    ]
    return frame, reports, derived, missing_required


def process_file(
    meta: FileMeta,
    config: Config,
    mapper: Mapper,
    profile_name: str | None = None,
    overrides: dict[str, str] | None = None,
) -> FileResult:
    """Run the whole pipeline over one workbook.

    ``overrides`` maps a normalized header to a canonical field, letting the UI inject
    the human's decisions without those decisions having to be learned first.
    """
    profile_name = profile_name or config.settings.warehouse.profile
    result = FileResult(meta=meta)

    # Messages here are read by people who did not write this tool, so each one says
    # what happened and what to do about it, in the language of the files.
    if meta.bank_code is None:
        result.error = (
            f"Banque inconnue : le dossier « {meta.path.parent.name} » ne correspond à "
            "aucune banque enregistrée. Renommez le dossier avec le nom de la banque "
            "(par exemple « BNA 2025 »), ou ajoutez la banque dans la configuration."
        )
        return result
    if meta.period is None:
        result.error = (
            f"Mois introuvable : impossible de lire un mois dans le nom du fichier "
            f"« {meta.path.name} ». Renommez-le en y mettant le mois, par exemple "
            "« Ventes_Mars_2025.xlsx » ou « ventes_03_2025.xlsx »."
        )
        return result

    try:
        workbook = load_workbook(meta.path, data_only=True)
    except Exception:                             # noqa: BLE001 - reported, not raised
        result.error = (
            "Fichier illisible : Excel n'arrive pas à ouvrir ce fichier. Il est "
            "probablement abîmé ou incomplet (copie interrompue). Ouvrez-le dans Excel "
            "pour vérifier, ou redemandez-le à la banque."
        )
        return result

    try:
        sheet_name = choose_sheet(workbook, config, meta.bank_code)
        result.sheet = sheet_name
        if len(workbook.sheetnames) > 1:
            result.notes.append(
                f"Le fichier contient {len(workbook.sheetnames)} feuilles ; "
                f"c'est « {sheet_name} » qui a été utilisée."
            )

        vocabulary = set(mapper.seed) | set(config.aliases.global_)
        table = extract_table(
            workbook[sheet_name], vocabulary, config.settings.detection
        )
        result.table = table

        if not table.rows:
            result.error = (
                "Aucun tableau de ventes trouvé dans ce fichier. Vérifiez qu'il "
                "contient bien une ligne d'en-têtes (N° contrat, prime, date…) suivie "
                "des ventes, et qu'il n'est pas vide."
            )
            return result

        # A column of uncalculated formulas reads as entirely empty and would otherwise
        # be dropped in silence, taking a premium column with it. Only worth checking
        # when something actually went missing.
        dropped_empty = [
            c.header or c.letter for c in table.dropped_columns
            if "empty" in c.reason
        ]
        if dropped_empty:
            formula_columns = uncached_formula_columns(
                meta.path, sheet_name, table.header_row
            )
            lost = [c for c in dropped_empty if c in formula_columns]
            if lost:
                result.error = (
                    "Colonnes calculées non enregistrées : "
                    + ", ".join(f"« {name} »" for name in lost)
                    + ". Ces colonnes contiennent des formules dont le résultat n'a "
                    "jamais été enregistré, elles sont donc vides à la lecture. "
                    "Solution : ouvrez le fichier dans Excel, puis enregistrez-le "
                    "(Ctrl+S) et relancez. Sans cela ces colonnes seraient perdues."
                )
                return result

        mappings = mapper.resolve_table(
            table.headers, table.rows, meta.bank_code, table.header_parts
        )
        if overrides:
            for mapping in mappings:
                target = overrides.get(mapping.normalized)
                if target:
                    mapping.canonical = None if target == "__ignore__" else target
                    mapping.method = "manual"
                    mapping.confidence = 1.0
                    mapping.reason = "set by the user"
                    mapping.warnings = []
        result.mappings = mappings

        frame, reports, derived, missing = build_frame(
            table, mappings, meta, config, profile_name
        )
        result.frame = frame
        result.column_reports = reports
        result.derived = derived
        result.missing_required = missing

        if missing:
            result.notes.append(
                "Colonnes obligatoires absentes de ce fichier : " + ", ".join(missing)
            )
    finally:
        workbook.close()

    return result
