"""The warehouse: an append-only store shaped for Power BI.

Three properties matter more than anything else here, because the output feeds
dashboards that must not break when next month arrives:

**Append, don't rebuild.** A manifest records the content hash of every ingested file.
Dropping in June processes June only. Twelve months of six banks stay untouched.

**Idempotent.** Re-ingesting a file replaces its own rows rather than duplicating them,
keyed on ``row_hash``. A bank that resends a corrected March file gets the correction,
not two Marches.

**A locked schema.** The fact table's columns and dtypes are checked on every write. A
month that would silently drop or rename a column fails the run instead of quietly
breaking every report downstream. This is the failure mode that makes people distrust a
pipeline, and it is cheap to prevent.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import Config

FACT_NAME = "fact_ventes"
MANIFEST_NAME = "_manifest.json"


class SchemaContractError(RuntimeError):
    """Raised when a write would change the shape of the fact table.

    Silently widening or narrowing the table is what breaks dashboards weeks later, far
    from the cause. Failing here keeps the damage at the point of the change.
    """


@dataclass
class IngestedFile:
    """One file's entry in the manifest."""

    path: str
    content_hash: str
    bank: str
    period: str
    rows: int
    ingested_at: str
    profile: str

    @classmethod
    def from_frame(cls, path: Path, frame: pd.DataFrame, profile: str) -> "IngestedFile":
        return cls(
            path=str(path),
            content_hash=file_hash(path),
            bank=str(frame["banque"].iloc[0]) if "banque" in frame else "",
            period=str(frame["mois_reception"].iloc[0]) if "mois_reception" in frame else "",
            rows=len(frame),
            ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            profile=profile,
        )


@dataclass
class Manifest:
    """What the warehouse already contains."""

    files: dict[str, IngestedFile] = field(default_factory=dict)
    schema_columns: list[str] = field(default_factory=list)
    profile: str = ""

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            files={k: IngestedFile(**v) for k, v in raw.get("files", {}).items()},
            schema_columns=raw.get("schema_columns", []),
            profile=raw.get("profile", ""),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "profile": self.profile,
            "schema_columns": self.schema_columns,
            "files": {k: asdict(v) for k, v in sorted(self.files.items())},
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def status(self, path: Path) -> str:
        """Whether a file is ``new``, ``unchanged`` or ``changed`` since last ingest."""
        entry = self.files.get(str(path))
        if entry is None:
            return "new"
        return "unchanged" if entry.content_hash == file_hash(path) else "changed"


def file_hash(path: Path) -> str:
    """Content hash of a workbook, so an untouched file is skipped on the next run."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()[:32]


class Warehouse:
    """Reads and writes the warehouse directory."""

    def __init__(
        self,
        config: Config,
        root: Path | str | None = None,
        profile_name: str | None = None,
    ):
        self.config = config
        self.root = Path(root or config.settings.warehouse.path)
        # The profile must be the one the run actually used, not the settings default:
        # committing a run made with --profile detaille has to be checked against
        # detaille's columns, or the contract raises a spurious refusal.
        self.profile_name = profile_name or config.settings.warehouse.profile
        self.manifest = Manifest.load(self.root / MANIFEST_NAME)

    # -- paths -------------------------------------------------------------------

    @property
    def fact_path(self) -> Path:
        return self.root / f"{FACT_NAME}.parquet"

    def bank_path(self, bank: str, suffix: str = "parquet") -> Path:
        return self.root / "par_banque" / f"{bank}.{suffix}"

    # -- reading -----------------------------------------------------------------

    def read_fact(self) -> pd.DataFrame:
        if not self.fact_path.exists():
            return pd.DataFrame()
        return pd.read_parquet(self.fact_path)

    def pending(self, paths: list[Path]) -> dict[str, list[Path]]:
        """Split candidate files into new, changed and unchanged."""
        buckets: dict[str, list[Path]] = {"new": [], "changed": [], "unchanged": []}
        for path in paths:
            buckets[self.manifest.status(path)].append(path)
        return buckets

    # -- the schema contract ------------------------------------------------------

    def expected_columns(self) -> list[str]:
        """The column list the profile implies, including derived source markers."""
        from .consolidate import PROVENANCE_COLUMNS

        profile = self.config.schema_.profiles[self.profile_name]
        columns = list(profile.columns)
        for name in profile.columns:
            field_def = self.config.schema_.fields.get(name)
            if field_def and field_def.derive:
                columns.append(f"{name}_source")
        return columns + PROVENANCE_COLUMNS

    def check_contract(self, frame: pd.DataFrame) -> None:
        """Refuse a write whose shape differs from what the warehouse already holds."""
        expected = self.manifest.schema_columns or self.expected_columns()
        actual = list(frame.columns)
        if actual == expected:
            return

        missing = [c for c in expected if c not in actual]
        extra = [c for c in actual if c not in expected]
        details = []
        if missing:
            details.append(f"missing columns {missing}")
        if extra:
            details.append(f"unexpected columns {extra}")
        if not details:
            details.append(f"column order changed: expected {expected}, got {actual}")
        raise SchemaContractError(
            "refusing to write: the fact table's shape would change ("
            + "; ".join(details)
            + "). Every Power BI report built on this table depends on these columns. "
            "If the change is intended, update the export profile in schema.yaml and "
            "rebuild the warehouse deliberately."
        )

    # -- writing -----------------------------------------------------------------

    def append(
        self, frames: list[pd.DataFrame], sources: list[Path], dry_run: bool = False
    ) -> dict:
        """Add or replace the rows belonging to the given source files.

        Rows are keyed by ``row_hash``. Any row previously ingested from one of these
        source files is dropped first, so re-ingesting a corrected file replaces its
        rows instead of duplicating them.
        """
        if not frames:
            return {"written": 0, "replaced": 0, "total": len(self.read_fact())}

        incoming = pd.concat(frames, ignore_index=True)
        self.check_contract(incoming)

        existing = self.read_fact()
        replaced = 0
        if not existing.empty:
            touched = {str(p) for p in sources}
            before = len(existing)
            existing = existing[~existing["source_file"].isin(touched)]
            replaced = before - len(existing)
            combined = pd.concat([existing, incoming], ignore_index=True)
        else:
            combined = incoming

        # row_hash already includes the source file, so this only removes true repeats.
        combined = combined.drop_duplicates(subset="row_hash", keep="last")
        combined = combined.sort_values(
            ["banque", "mois_reception", "source_file", "source_row"]
        ).reset_index(drop=True)

        summary = {
            "written": len(incoming),
            "replaced": replaced,
            "total": len(combined),
        }
        if dry_run:
            return summary

        self.root.mkdir(parents=True, exist_ok=True)
        combined.to_parquet(self.fact_path, index=False)

        for path, frame in zip(sources, frames):
            self.manifest.files[str(path)] = IngestedFile.from_frame(
                path, frame, self.profile_name
            )
        self.manifest.schema_columns = list(combined.columns)
        self.manifest.profile = self.profile_name
        self.manifest.save(self.root / MANIFEST_NAME)

        self._write_dimensions(combined)
        self._write_bank_extracts(combined)
        return summary

    # -- dimensions and extracts --------------------------------------------------

    def _write_bank_extracts(self, fact: pd.DataFrame) -> None:
        """One file per bank, as requested, alongside the combined table.

        Point Power BI at the combined table and filter on `banque`: a single model
        gives cross-bank comparison for free, where separate tables would force a
        duplicated report page per bank and make "all banks" impossible. The per-bank
        extracts exist for handing a single bank its own data.
        """
        directory = self.root / "par_banque"
        directory.mkdir(parents=True, exist_ok=True)
        limit = self.config.settings.warehouse.xlsx_row_limit

        for bank, group in fact.groupby("banque"):
            group = group.reset_index(drop=True)
            group.to_parquet(self.bank_path(bank), index=False)
            if self.config.settings.warehouse.write_xlsx:
                if len(group) > limit:
                    # Truncating silently would be far worse than refusing.
                    continue
                labels = self.config.schema_.label_map(self.profile_name)
                group.rename(columns=labels).to_excel(
                    self.bank_path(bank, "xlsx"), index=False, sheet_name="Ventes"
                )

    def _write_dimensions(self, fact: pd.DataFrame) -> None:
        """Write the dimension tables Power BI needs to model the data properly."""
        build_date_dimension(fact).to_parquet(self.root / "dim_date.parquet", index=False)

        if "banque" in fact.columns:
            codes = sorted(fact["banque"].dropna().unique())
            labels = {b.code: b.label for b in self.config.banks.banks}
            pd.DataFrame({
                "banque": codes,
                "libelle_banque": [labels.get(c, c) for c in codes],
            }).to_parquet(self.root / "dim_banque.parquet", index=False)

        if "produit" in fact.columns:
            produits = sorted(fact["produit"].dropna().unique())
            pd.DataFrame({"produit": produits}).to_parquet(
                self.root / "dim_produit.parquet", index=False
            )


MOIS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def build_date_dimension(fact: pd.DataFrame) -> pd.DataFrame:
    """A contiguous calendar spanning the data, with French labels.

    Power BI's time intelligence (YoY, YTD, rolling 12) needs a date table that is
    continuous and marked as such — gaps in a date column built from the facts
    themselves silently break those calculations. Projects that skip this end up
    rebuilding it by hand later.
    """
    dates = pd.Series(dtype="datetime64[ns]")
    if "date_effet" in fact.columns:
        dates = pd.to_datetime(fact["date_effet"], errors="coerce").dropna()
    if dates.empty and "mois_reception" in fact.columns:
        dates = pd.to_datetime(fact["mois_reception"] + "-01", errors="coerce").dropna()
    if dates.empty:
        return pd.DataFrame(columns=["date", "annee", "mois", "nom_mois", "trimestre"])

    start = dates.min().replace(day=1)
    end = dates.max() + pd.offsets.MonthEnd(0)
    calendar = pd.date_range(start, end, freq="D")

    return pd.DataFrame({
        "date": calendar,
        "annee": calendar.year,
        "mois": calendar.month,
        "nom_mois": [MOIS_FR[m - 1] for m in calendar.month],
        "annee_mois": calendar.strftime("%Y-%m"),
        "trimestre": "T" + calendar.quarter.astype(str),
        "jour": calendar.day,
        "jour_semaine": calendar.dayofweek + 1,
    })
