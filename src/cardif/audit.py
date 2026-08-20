"""The audit workbook: the record that makes every number defensible.

One Excel file, one sheet per question a person might ask months later:

``Résumé``          what happened to each file
``Correspondances`` which original header became which field, and how it was decided
``Supprimé``        every row and column removed, with its source coordinates
``Anomalies``       every validation flag
``Rapprochement``   premium totals per bank and month, to tie back to the banks' figures

The last sheet is the one that proves the consolidation lost nothing.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .consolidate import FileResult
from .validate import ValidationReport, reconciliation_table

# Column widths chosen so the sheets are readable without fiddling.
_WIDTHS = {
    "fichier": 34, "source_file": 46, "message": 80, "raison": 34, "motif": 34,
    "en_tete": 26, "champ": 18, "methode": 12, "libelle": 26, "note": 60,
    "avertissements": 60, "banque": 10, "mois_reception": 14,
}


def summary_frame(results: list[FileResult]) -> pd.DataFrame:
    """One row per file: what was found, what was kept, what needs attention."""
    rows = []
    for result in results:
        table = result.table
        rows.append({
            "fichier": Path(result.meta.path).name,
            "banque": result.meta.bank_code or "",
            "mois_reception": result.meta.period or "",
            "periode_source": result.meta.period_method,
            "feuille": result.sheet,
            "ligne_entete": table.header_row if table else None,
            "premiere_ligne": table.data_start if table else None,
            "derniere_ligne": table.data_end if table else None,
            "lignes_gardees": result.n_rows,
            "lignes_supprimees": len(table.dropped_rows) if table else 0,
            "colonnes_gardees": len(table.headers) if table else 0,
            "colonnes_supprimees": len(table.dropped_columns) if table else 0,
            "entetes_non_resolus": len(result.unresolved_headers),
            "champs_calcules": ", ".join(result.derived) or "",
            "champs_requis_absents": ", ".join(result.missing_required) or "",
            "statut": "erreur" if result.error else ("à vérifier" if result.needs_review else "ok"),
            "note": result.error or "; ".join(result.notes),
        })
    return pd.DataFrame(rows)


def mappings_frame(results: list[FileResult]) -> pd.DataFrame:
    """Every header decision, so a mapping can be questioned and traced."""
    rows = []
    for result in results:
        for mapping in result.mappings:
            if not mapping.normalized:
                continue
            rows.append({
                "fichier": Path(result.meta.path).name,
                "banque": result.meta.bank_code or "",
                "mois_reception": result.meta.period or "",
                "en_tete": mapping.header,
                "en_tete_normalise": mapping.normalized,
                "champ": mapping.canonical or "(non résolu)",
                "methode": mapping.method,
                "confiance": round(mapping.confidence, 2),
                "raison": mapping.reason,
                "avertissements": "; ".join(mapping.warnings),
                "suggestions": ", ".join(
                    f"{name} ({score:.0f})" for name, score in mapping.suggestions
                ),
            })
    return pd.DataFrame(rows)


def dropped_frame(results: list[FileResult]) -> pd.DataFrame:
    """Everything removed, with the coordinates needed to find it in the source.

    This is what lets the user answer "why is this row not in the database?" without
    having to trust anyone's memory.
    """
    rows = []
    for result in results:
        if result.table is None:
            continue
        name = Path(result.meta.path).name
        for dropped in result.table.dropped_rows:
            rows.append({
                "fichier": name,
                "banque": result.meta.bank_code or "",
                "type": "ligne",
                "position": f"ligne {dropped.row}",
                "motif": dropped.reason,
                "contenu": dropped.preview,
            })
        for column in result.table.dropped_columns:
            rows.append({
                "fichier": name,
                "banque": result.meta.bank_code or "",
                "type": "colonne",
                "position": f"colonne {column.letter}",
                "motif": column.reason,
                "contenu": column.header,
            })
    return pd.DataFrame(rows)


def coercion_frame(results: list[FileResult]) -> pd.DataFrame:
    """Columns whose values would not parse, which would otherwise arrive silently empty."""
    rows = []
    for result in results:
        for report in result.column_reports:
            if not report.failed:
                continue
            rows.append({
                "fichier": Path(result.meta.path).name,
                "banque": result.meta.bank_code or "",
                "en_tete": report.header,
                "type_cible": report.target_type,
                "separateur_decimal": report.decimal_separator or "",
                "converties": report.converted,
                "vides": report.blank,
                "echecs": report.failed,
                "taux_echec": f"{report.failure_rate:.0%}",
                "exemples": ", ".join(report.failures[:5]),
            })
    return pd.DataFrame(rows)


def _autosize(worksheet, frame: pd.DataFrame) -> None:
    """Set sensible column widths and freeze the header row."""
    from openpyxl.utils import get_column_letter

    worksheet.freeze_panes = "A2"
    for index, column in enumerate(frame.columns, start=1):
        width = _WIDTHS.get(column)
        if width is None:
            longest = frame[column].astype(str).str.len().max() if len(frame) else 0
            width = min(40, max(12, int(longest or 0) + 2, len(str(column)) + 2))
        worksheet.column_dimensions[get_column_letter(index)].width = width


def write_audit(
    path: Path | str,
    results: list[FileResult],
    validation: ValidationReport,
    fact: pd.DataFrame | None = None,
) -> Path:
    """Write the audit workbook. Returns the path written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    sheets: dict[str, pd.DataFrame] = {
        "Résumé": summary_frame(results),
        "Correspondances": mappings_frame(results),
        "Supprimé": dropped_frame(results),
        "Conversions": coercion_frame(results),
        "Anomalies": validation.to_frame(),
    }
    if fact is not None and not fact.empty:
        sheets["Rapprochement"] = reconciliation_table(fact)

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            if frame.empty:
                # An empty sheet with a note is clearer than a missing sheet.
                frame = pd.DataFrame({"": ["(rien à signaler)"]})
            frame.to_excel(writer, sheet_name=name[:31], index=False)
            _autosize(writer.sheets[name[:31]], frame)

    return path
