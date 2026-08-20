"""Checks that make the consolidated figures defensible.

Every check produces *flags*, not deletions. A flagged row stays in the database: the
tool's job is to tell the user what looks wrong, not to decide that a real sale did not
happen. Only structural junk — blank rows, ``TOTAL`` lines, orphan cells — is ever
removed, and that happens in :mod:`cardif.detect` with a full record of what went.

The checks are:

* required fields present on each row
* ``prime_nette + frais == prime_totale`` within tolerance, where all three are reported
* the filename's period against the dates actually in the rows, because a file named
  "Mars" full of April business is a real occurrence
* declaration lag: policies taking effect long before the month that reported them
* duplicate rows within a file, and the same contract appearing across files
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .config import Config

# Severity is advisory. "error" means the row cannot be trusted as it stands; "warning"
# means it is unusual and a human should look; "info" is context worth recording.
SEVERITIES = ("error", "warning", "info")


@dataclass
class Flag:
    """One thing worth a human's attention, anchored to where it came from."""

    code: str
    severity: str
    message: str
    source_file: str = ""
    source_row: int | None = None
    row_hash: str = ""
    field: str = ""
    bank: str = ""
    period: str = ""

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "banque": self.bank,
            "mois_reception": self.period,
            "source_file": self.source_file,
            "source_row": self.source_row,
            "row_hash": self.row_hash,
            "field": self.field,
        }


@dataclass
class ValidationReport:
    flags: list[Flag] = field(default_factory=list)

    def add(self, flag: Flag) -> None:
        self.flags.append(flag)

    def extend(self, flags: list[Flag]) -> None:
        self.flags.extend(flags)

    def count(self, severity: str) -> int:
        return sum(1 for f in self.flags if f.severity == severity)

    @property
    def errors(self) -> int:
        return self.count("error")

    @property
    def warnings(self) -> int:
        return self.count("warning")

    def to_frame(self) -> pd.DataFrame:
        if not self.flags:
            return pd.DataFrame(
                columns=["severity", "code", "message", "banque", "mois_reception",
                         "source_file", "source_row", "row_hash", "field"]
            )
        order = {s: i for i, s in enumerate(SEVERITIES)}
        rows = sorted(
            (f.as_dict() for f in self.flags),
            key=lambda d: (order.get(d["severity"], 9), d["code"], d["source_file"] or ""),
        )
        return pd.DataFrame(rows)


def _row_anchor(row: pd.Series) -> dict:
    """Pull the provenance fields off a row so a flag can point back at the source."""
    return {
        "source_file": str(row.get("source_file", "")),
        "source_row": int(row["source_row"]) if pd.notna(row.get("source_row")) else None,
        "row_hash": str(row.get("row_hash", "")),
        "bank": str(row.get("banque", "")),
        "period": str(row.get("mois_reception", "")),
    }


def check_required(frame: pd.DataFrame, config: Config, profile_name: str) -> list[Flag]:
    """Flag rows missing a field the profile declares as required."""
    profile = config.schema_.profiles[profile_name]
    flags: list[Flag] = []
    for name in profile.required:
        if name not in frame.columns:
            continue
        missing = frame[frame[name].isna()]
        for _, row in missing.iterrows():
            flags.append(Flag(
                code="missing_required",
                severity="error",
                message=f"required field '{name}' is empty",
                field=name,
                **_row_anchor(row),
            ))
    return flags


def check_premium_reconciliation(frame: pd.DataFrame, config: Config) -> list[Flag]:
    """Check that the premium breakdown adds up, where the bank reported all of it.

    Only rows where all three figures were *reported* are checked: a derived total is
    the sum by construction, so checking it would be circular.
    """
    needed = {"prime_nette", "frais", "prime_totale"}
    if not needed.issubset(frame.columns):
        return []

    tolerance = config.settings.validation.premium_tolerance
    subset = frame.dropna(subset=list(needed))
    if "prime_totale_source" in frame.columns:
        subset = subset[subset["prime_totale_source"] == "reported"]

    flags = []
    for _, row in subset.iterrows():
        expected = row["prime_nette"] + row["frais"]
        difference = abs(expected - row["prime_totale"])
        if difference > tolerance:
            flags.append(Flag(
                code="premium_mismatch",
                severity="warning",
                message=(
                    f"prime_nette ({row['prime_nette']:.2f}) + frais ({row['frais']:.2f}) "
                    f"= {expected:.2f}, but prime_totale is {row['prime_totale']:.2f} "
                    f"(difference {difference:.2f})"
                ),
                field="prime_totale",
                **_row_anchor(row),
            ))
    return flags


def check_period_agreement(frame: pd.DataFrame, config: Config) -> list[Flag]:
    """Cross-check the filename's period against the dates in the rows.

    The filename is a claim; the rows are the evidence. A file named for March whose
    business is overwhelmingly April means somebody mislabelled the file, and every
    figure grouped by month downstream would be wrong.
    """
    if "date_effet" not in frame.columns or "mois_reception" not in frame.columns:
        return []

    threshold = config.settings.validation.period_agreement_threshold
    flags = []
    for (source_file, period), group in frame.groupby(["source_file", "mois_reception"]):
        dates = group["date_effet"].dropna()
        if dates.empty:
            continue
        actual = dates.dt.strftime("%Y-%m")
        agreement = (actual == period).mean()
        if agreement < threshold:
            dominant = actual.mode()
            dominant_period = dominant.iloc[0] if not dominant.empty else "unknown"
            flags.append(Flag(
                code="period_disagreement",
                severity="error",
                message=(
                    f"filename indicates {period}, but only {agreement:.0%} of rows have "
                    f"a date d'effet in that month; most fall in {dominant_period}. "
                    "Check that the file is named for the right month."
                ),
                field="mois_reception",
                source_file=str(source_file),
                bank=str(group["banque"].iloc[0]) if "banque" in group else "",
                period=str(period),
            ))
    return flags


def check_declaration_lag(frame: pd.DataFrame, config: Config) -> list[Flag]:
    """Flag policies that took effect well before the month that reported them.

    Some lag is normal and expected — this is why ``date_effet`` and ``mois_reception``
    are kept as separate columns in the first place. Only an unusually long lag is worth
    a human's attention.
    """
    if "date_effet" not in frame.columns:
        return []

    max_lag = config.settings.validation.max_declaration_lag_months
    subset = frame.dropna(subset=["date_effet"])
    if subset.empty:
        return []

    reception = pd.to_datetime(subset["mois_reception"] + "-01", errors="coerce")
    lag_months = (
        (reception.dt.year - subset["date_effet"].dt.year) * 12
        + (reception.dt.month - subset["date_effet"].dt.month)
    )

    flags = []
    for (_, row), lag in zip(subset.iterrows(), lag_months):
        if pd.isna(lag):
            continue
        if lag > max_lag:
            flags.append(Flag(
                code="late_declaration",
                severity="warning",
                message=(
                    f"date d'effet {row['date_effet'].date()} is {int(lag)} months before "
                    f"the reporting month {row['mois_reception']}"
                ),
                field="date_effet",
                **_row_anchor(row),
            ))
        elif lag < -1:
            flags.append(Flag(
                code="future_effect",
                severity="warning",
                message=(
                    f"date d'effet {row['date_effet'].date()} is {abs(int(lag))} months "
                    f"after the reporting month {row['mois_reception']}"
                ),
                field="date_effet",
                **_row_anchor(row),
            ))
    return flags


def check_duplicates(frame: pd.DataFrame) -> list[Flag]:
    """Find repeated business rows, within a file and across files.

    Within one file a repeat is almost certainly a copy-paste error. Across files it may
    be legitimate — a renewal, an instalment, a correction resent — so it is reported at
    a lower severity and left for the user to judge.
    """
    key = [c for c in ("num_contrat", "date_effet", "prime_totale") if c in frame.columns]
    if "num_contrat" not in key:
        return []

    flags = []
    subset = frame.dropna(subset=["num_contrat"])

    within = subset.groupby(["source_file", *key]).size()
    for entry, count in within[within > 1].items():
        source_file = entry[0]
        contract = entry[1]
        rows = subset[(subset["source_file"] == source_file) & (subset["num_contrat"] == contract)]
        flags.append(Flag(
            code="duplicate_in_file",
            severity="error",
            message=(
                f"contract {contract} appears {count} times in the same file "
                f"(rows {', '.join(str(int(r)) for r in rows['source_row'])})"
            ),
            field="num_contrat",
            source_file=str(source_file),
            bank=str(rows["banque"].iloc[0]) if "banque" in rows else "",
            period=str(rows["mois_reception"].iloc[0]) if "mois_reception" in rows else "",
        ))

    across = subset.groupby("num_contrat")["source_file"].nunique()
    for contract, n_files in across[across > 1].items():
        rows = subset[subset["num_contrat"] == contract]
        files = sorted(set(rows["source_file"]))
        flags.append(Flag(
            code="duplicate_across_files",
            severity="info",
            message=(
                f"contract {contract} appears in {n_files} files "
                f"({', '.join(Path(f).name for f in files)}); "
                "this may be a renewal or a resent correction"
            ),
            field="num_contrat",
            bank=str(rows["banque"].iloc[0]) if "banque" in rows else "",
        ))

    return flags


def validate(frame: pd.DataFrame, config: Config, profile_name: str | None = None) -> ValidationReport:
    """Run every check over a consolidated frame."""
    profile_name = profile_name or config.settings.warehouse.profile
    report = ValidationReport()
    if frame is None or frame.empty:
        return report

    report.extend(check_required(frame, config, profile_name))
    report.extend(check_premium_reconciliation(frame, config))
    report.extend(check_period_agreement(frame, config))
    report.extend(check_declaration_lag(frame, config))
    report.extend(check_duplicates(frame))
    return report


def reconciliation_table(frame: pd.DataFrame) -> pd.DataFrame:
    """Premium totals per bank and month, for tying back to the banks' own figures.

    This is the sheet that proves the consolidation lost nothing: the user compares each
    line against what the bank said it sent.
    """
    if frame is None or frame.empty:
        return pd.DataFrame()

    money = [c for c in ("prime_nette", "frais", "prime_totale", "capital_assure")
             if c in frame.columns]
    grouped = frame.groupby(["banque", "mois_reception"], dropna=False)

    out = grouped.agg(
        lignes=("row_hash", "count"),
        fichiers=("source_file", "nunique"),
        **{f"{c}_total": (c, "sum") for c in money},
    ).reset_index()

    for c in money:
        out[f"{c}_total"] = out[f"{c}_total"].round(2)
    if "prime_totale_source" in frame.columns:
        derived = grouped["prime_totale_source"].apply(lambda s: (s == "derived").sum())
        out["primes_calculees"] = derived.reset_index(drop=True)
    return out.sort_values(["banque", "mois_reception"]).reset_index(drop=True)
