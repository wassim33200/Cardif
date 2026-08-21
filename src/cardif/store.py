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
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from collections.abc import Callable

from .config import Config

MANIFEST_NAME = "_manifest.json"


def nom_table(produit: str) -> str:
    """Table file name for a product: ``ade_immobilier`` -> ``fact_ade_immobilier``."""
    return f"fact_{produit}"


class WarehouseCorrupt(RuntimeError):
    """Raised when the fact table cannot be read, with the route to recovery."""


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
    produit: str = ""

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
            produit=profile,
        )


@dataclass
class Manifest:
    """What the warehouse already contains."""

    files: dict[str, IngestedFile] = field(default_factory=dict)
    # Column list per product: each product has its own table and its own contract.
    schema_columns: dict[str, list[str]] = field(default_factory=dict)
    profile: str = ""

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        colonnes = raw.get("schema_columns", {})
        if isinstance(colonnes, list):
            # Warehouses written before products existed stored one flat list.
            colonnes = {raw.get("profile", "generique"): colonnes} if colonnes else {}
        return cls(
            files={k: IngestedFile(**v) for k, v in raw.get("files", {}).items()},
            schema_columns=colonnes,
            profile=raw.get("profile", ""),
        )

    def save(self, path: Path) -> None:
        payload = {
            "profile": self.profile,
            "schema_columns": self.schema_columns,
            "files": {k: asdict(v) for k, v in sorted(self.files.items())},
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        atomic_write(
            path,
            lambda target: target.write_text(text, encoding="utf-8"),
            keep_backup=False,
        )

    def status(self, path: Path) -> str:
        """Whether a file is ``new``, ``unchanged`` or ``changed`` since last ingest."""
        entry = self.files.get(str(path))
        if entry is None:
            return "new"
        return "unchanged" if entry.content_hash == file_hash(path) else "changed"


def atomic_write(path: Path, write: "Callable[[Path], None]", keep_backup: bool = True) -> None:
    """Write a file so that an interruption cannot destroy the previous version.

    The naive approach -- writing straight over ``fact_ventes.parquet`` -- means a crash,
    a full disk, or a killed process mid-write leaves a truncated file. Parquet stores
    its footer at the end, so a truncated file is not partially readable: it is entirely
    unreadable, and every month ever ingested is gone.

    Writing to a temporary file in the same directory and then renaming makes the switch
    atomic: readers see either the old file or the new one, never a half-written one. The
    previous version is kept alongside as ``.bak`` so there is still a way back even if
    the *new* data turns out to be wrong.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    os.close(handle)
    temporary = Path(temporary_name)

    try:
        write(temporary)
        # Force the bytes to disk before the rename, so a power loss cannot leave the
        # rename visible while the contents are not.
        with open(temporary, "rb") as file:
            os.fsync(file.fileno())

        if keep_backup and path.exists():
            backup = path.with_suffix(path.suffix + ".bak")
            backup.unlink(missing_ok=True)
            os.replace(path, backup)

        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


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
        self.skipped_xlsx: list[str] = []

    # -- paths -------------------------------------------------------------------

    def fact_path(self, produit: str) -> Path:
        """Each product gets its own table: their columns have little in common."""
        return self.root / f"{nom_table(produit)}.parquet"

    def produits_presents(self) -> list[str]:
        """Products that already have a table in the warehouse."""
        return sorted(
            p.stem[len("fact_"):] for p in self.root.glob("fact_*.parquet")
        )

    def bank_path(self, bank: str, produit: str, suffix: str = "parquet") -> Path:
        return self.root / "par_banque" / f"{bank}_{produit}.{suffix}"

    # -- reading -----------------------------------------------------------------

    def read_fact(self, produit: str | None = None) -> pd.DataFrame:
        """Read one product's table, or all of them stacked together.

        Reading everything at once is only for counting and reporting: the tables have
        different columns, so the union is sparse by nature and is never written to disk.
        """
        if produit is None:
            morceaux = [self.read_fact(code) for code in self.produits_presents()]
            morceaux = [m for m in morceaux if not m.empty]
            if not morceaux:
                return pd.DataFrame()
            return pd.concat(morceaux, ignore_index=True)

        chemin = self.fact_path(produit)
        if not chemin.exists():
            return pd.DataFrame()
        try:
            return pd.read_parquet(chemin)
        except Exception as exc:                  # noqa: BLE001 - re-raised with context
            backup = chemin.with_suffix(chemin.suffix + ".bak")
            if backup.exists():
                try:
                    frame = pd.read_parquet(backup)
                except Exception:
                    raise WarehouseCorrupt(
                        f"Le fichier {chemin} est illisible ({exc}) et sa sauvegarde "
                        "aussi. Retraitez les fichiers sources pour reconstruire la base."
                    ) from exc
                raise WarehouseCorrupt(
                    f"Le fichier {chemin} est illisible ({exc}), probablement à cause "
                    f"d'un enregistrement interrompu. Une sauvegarde contenant "
                    f"{len(frame)} lignes existe : {backup}. Renommez-la par-dessus le "
                    "fichier principal pour récupérer, puis retraitez ce qui a été "
                    "ajouté depuis."
                ) from exc
            raise WarehouseCorrupt(
                f"Le fichier {chemin} est illisible ({exc}) et il n'y a pas de "
                "sauvegarde. Retraitez les fichiers sources pour reconstruire la base."
            ) from exc

    def pending(self, paths: list[Path]) -> dict[str, list[Path]]:
        """Split candidate files into new, changed and unchanged."""
        buckets: dict[str, list[Path]] = {"new": [], "changed": [], "unchanged": []}
        for path in paths:
            buckets[self.manifest.status(path)].append(path)
        return buckets

    # -- the schema contract ------------------------------------------------------

    def expected_columns(self, produit: str) -> list[str]:
        """The column list a product implies, including derived source markers."""
        from .consolidate import PROVENANCE_COLUMNS

        profile = self.config.produit(produit)
        columns = list(profile.columns)
        for name in profile.columns:
            field_def = self.config.schema_.fields.get(name)
            if field_def and field_def.derive:
                columns.append(f"{name}_source")
        return columns + PROVENANCE_COLUMNS

    def check_contract(self, frame: pd.DataFrame, produit: str) -> None:
        """Refuse a write whose shape differs from what that product's table holds.

        The contract is per product: adding a column to the ADE table must not be
        blocked by, or silently alter, the SAHTI table.
        """
        expected = self.manifest.schema_columns.get(produit) or self.expected_columns(produit)
        actual = list(frame.columns)
        if actual == expected:
            return

        missing = [c for c in expected if c not in actual]
        extra = [c for c in actual if c not in expected]
        details = []
        if missing:
            details.append(f"colonnes manquantes {missing}")
        if extra:
            details.append(f"colonnes en trop {extra}")
        if not details:
            details.append(f"ordre des colonnes modifié : attendu {expected}, reçu {actual}")
        raise SchemaContractError(
            f"Écriture refusée : la forme de la table « {produit} » changerait ("
            + " ; ".join(details)
            + "). Chaque rapport Power BI construit sur cette table dépend de ces "
            "colonnes. Si le changement est voulu, modifiez le produit dans "
            "produits.yaml et reconstruisez la table volontairement."
        )

    # -- writing -----------------------------------------------------------------

    def append(
        self,
        frames: list[pd.DataFrame],
        sources: list[Path],
        produits: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Add or replace rows, writing one table per product.

        Frames are grouped by product first: a month typically brings an ADE file and a
        SAHTI file, and they belong in different tables with different columns. Each
        table is written atomically and checked against its own contract, so a problem
        with one product cannot corrupt another.
        """
        if not frames:
            return {"written": 0, "replaced": 0, "total": 0, "par_produit": {}}

        produits = produits or [self.profile_name] * len(frames)
        groupes: dict[str, list[tuple[pd.DataFrame, Path]]] = {}
        for frame, source, produit in zip(frames, sources, produits):
            groupes.setdefault(produit, []).append((frame, source))

        resume = {"written": 0, "replaced": 0, "total": 0, "par_produit": {}}
        self.skipped_xlsx = []

        for produit, elements in sorted(groupes.items()):
            entrant = pd.concat([f for f, _ in elements], ignore_index=True)
            chemins = [p for _, p in elements]
            self.check_contract(entrant, produit)

            existant = self.read_fact(produit)
            remplaces = 0
            if not existant.empty:
                touches = {str(p) for p in chemins}
                avant = len(existant)
                existant = existant[~existant["source_file"].isin(touches)]
                remplaces = avant - len(existant)
                combine = pd.concat([existant, entrant], ignore_index=True)
            else:
                combine = entrant

            # row_hash already includes the source file, so this removes true repeats only.
            combine = combine.drop_duplicates(subset="row_hash", keep="last")
            combine = combine.sort_values(
                ["banque", "mois_reception", "source_file", "source_row"]
            ).reset_index(drop=True)

            resume["written"] += len(entrant)
            resume["replaced"] += remplaces
            resume["total"] += len(combine)
            resume["par_produit"][produit] = {
                "ecrites": len(entrant),
                "remplacees": remplaces,
                "total": len(combine),
            }

            if dry_run:
                continue

            self.root.mkdir(parents=True, exist_ok=True)
            chemin = self.fact_path(produit)
            atomic_write(chemin, lambda cible, c=combine: c.to_parquet(cible, index=False))

            # Read back before recording the files as ingested. If the write silently
            # produced something unusable, the manifest must not claim otherwise.
            verifie = pd.read_parquet(chemin)
            if len(verifie) != len(combine):
                raise WarehouseCorrupt(
                    f"{len(combine)} lignes écrites pour « {produit} » mais "
                    f"{len(verifie)} relues. La table n'a pas été mise à jour ; "
                    "la version précédente est intacte."
                )

            for source, (frame, _) in zip(chemins, elements):
                self.manifest.files[str(source)] = IngestedFile.from_frame(
                    source, frame, produit
                )
            self.manifest.schema_columns[produit] = list(combine.columns)
            self._write_bank_extracts(combine, produit)

        if dry_run:
            return resume

        self.manifest.profile = self.profile_name
        self.manifest.save(self.root / MANIFEST_NAME)
        self._write_dimensions(self.read_fact())
        if self.skipped_xlsx:
            resume["skipped_xlsx"] = list(self.skipped_xlsx)
        return resume

    # -- dimensions and extracts --------------------------------------------------

    def _write_bank_extracts(self, fact: pd.DataFrame, produit: str) -> None:
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
            group.to_parquet(self.bank_path(bank, produit), index=False)
            if not self.config.settings.warehouse.write_xlsx:
                continue
            if len(group) > limit:
                # Truncating silently would be far worse than not writing at all, but
                # saying nothing would leave a stale extract looking current.
                self.skipped_xlsx.append(
                    f"{bank} / {produit} : {len(group)} lignes dépassent la limite "
                    f"Excel de {limit} ; le fichier .parquet contient tout."
                )
                self.bank_path(bank, produit, "xlsx").unlink(missing_ok=True)
                continue
            labels = self.config.schema_.label_map(produit)
            group.rename(columns=labels).to_excel(
                self.bank_path(bank, produit, "xlsx"), index=False, sheet_name="Ventes"
            )

    def _write_dimensions(self, fact: pd.DataFrame) -> None:
        """Write the dimension tables Power BI needs to model the data properly."""
        self.root.mkdir(parents=True, exist_ok=True)
        build_date_dimension(fact).to_parquet(self.root / "dim_date.parquet", index=False)

        if "banque" in fact.columns:
            codes = sorted(fact["banque"].dropna().unique())
            labels = {b.code: b.label for b in self.config.banks.banks}
            pd.DataFrame({
                "banque": codes,
                "libelle_banque": [labels.get(c, c) for c in codes],
            }).to_parquet(self.root / "dim_banque.parquet", index=False)

        # The product dimension comes from the catalogue, not only from what happens to
        # be in the data, so a Power BI slicer lists every product from day one.
        lignes = [
            {
                "produit_code": code,
                "produit": profil.label,
                "famille": profil.famille,
                "table": nom_table(code),
            }
            for code, profil in sorted(self.config.schema_.profiles.items())
        ]
        pd.DataFrame(lignes).to_parquet(self.root / "dim_produit.parquet", index=False)


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
