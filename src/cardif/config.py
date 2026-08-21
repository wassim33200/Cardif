"""Typed configuration loading.

Config is validated on load rather than trusted, so a typo in `schema.yaml` surfaces as
a clear error at startup instead of a mysterious empty column three steps later.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from .normalize import normalize_header

FieldType = Literal[
    "identifier", "text", "category", "date", "money", "integer", "period", "percent"
]


class ContentExpectation(BaseModel):
    """What a column's *values* should look like if a mapping to this field is right.

    This is what catches the dangerous case: a header whose name looks perfect but whose
    contents are something else entirely.
    """

    mostly: Literal["numeric", "date", "text", "identifier"] | None = None
    min_value: float | None = None
    max_value: float | None = None
    min_unique_ratio: float | None = None
    max_unique_ratio: float | None = None


class DerivationRule(BaseModel):
    """How to compute a field when a bank does not report it directly."""

    rule: Literal["sum", "difference"]
    from_: list[str] = Field(alias="from")

    model_config = {"populate_by_name": True}


class CanonicalField(BaseModel):
    label: str
    type: FieldType
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    expect: ContentExpectation = Field(default_factory=ContentExpectation)
    derive: list[DerivationRule] = Field(default_factory=list)

    @field_validator("aliases")
    @classmethod
    def _normalize_aliases(cls, v: list[str]) -> list[str]:
        # Aliases are authored for humans but only ever compared normalized.
        return [normalize_header(a) for a in v if normalize_header(a)]


class DerivedField(BaseModel):
    label: str
    type: FieldType


class ExportProfile(BaseModel):
    """One product's target schema: the columns its table contains.

    Products *are* the export profiles. Each bank sells several products and each
    product carries its own fields -- an ADE has a credit and a CRD, a prevoyance has a
    capital and a periodicity, a travel policy has a zone -- so there is no single
    column list that fits them all. Forcing one would give a table that is mostly empty.
    """

    columns: list[str]
    required: list[str] = Field(default_factory=list)
    label: str = ""
    famille: str = "autre"
    partenaires: list[str] = Field(default_factory=list)
    match: list[str] = Field(default_factory=list)
    aliases: dict[str, str] = Field(default_factory=dict)

    @field_validator("match")
    @classmethod
    def _normalize_match(cls, v: list[str]) -> list[str]:
        return [normalize_header(m) for m in v]

    @field_validator("aliases")
    @classmethod
    def _normalize_aliases(cls, v: dict[str, str]) -> dict[str, str]:
        return {normalize_header(k): value for k, value in v.items()}


class Schema(BaseModel):
    fields: dict[str, CanonicalField]
    derived_fields: dict[str, DerivedField] = Field(default_factory=dict)
    # Keyed by product code. Populated from produits.yaml at load time.
    profiles: dict[str, ExportProfile] = Field(default_factory=dict)

    def produits_par_famille(self) -> dict[str, list[str]]:
        """Product codes grouped by family, for reporting and for the interface."""
        familles: dict[str, list[str]] = {}
        for code, produit in self.profiles.items():
            familles.setdefault(produit.famille, []).append(code)
        return {k: sorted(v) for k, v in sorted(familles.items())}

    def produits_du_partenaire(self, banque: str | None) -> list[str]:
        """Products a given bank distributes, plus any that name no partner."""
        if banque is None:
            return sorted(self.profiles)
        return sorted(
            code for code, p in self.profiles.items()
            if not p.partenaires or banque in p.partenaires
        )

    def model_post_init(self, _context: Any) -> None:
        known = set(self.fields) | set(self.derived_fields)
        for name, profile in self.profiles.items():
            unknown = [c for c in profile.columns if c not in known]
            if unknown:
                raise ValueError(
                    f"profile '{name}' references unknown fields: {unknown}"
                )
            missing_required = [c for c in profile.required if c not in profile.columns]
            if missing_required:
                raise ValueError(
                    f"profile '{name}' requires {missing_required} "
                    "but does not include them in its columns"
                )
        for name, field in self.fields.items():
            for rule in field.derive:
                unknown = [f for f in rule.from_ if f not in self.fields]
                if unknown:
                    raise ValueError(
                        f"field '{name}' derives from unknown fields: {unknown}"
                    )

    def label_map(self, profile_name: str) -> dict[str, str]:
        """Canonical name -> human label, for the columns of one profile."""
        profile = self.profiles[profile_name]
        out = {}
        for col in profile.columns:
            if col in self.fields:
                out[col] = self.fields[col].label
            else:
                out[col] = self.derived_fields[col].label
        return out


class Bank(BaseModel):
    code: str
    label: str
    match: list[str] = Field(default_factory=list)

    @field_validator("match")
    @classmethod
    def _normalize_match(cls, v: list[str]) -> list[str]:
        return [normalize_header(m) for m in v]


class BankOverride(BaseModel):
    sheet_preference: list[str] = Field(default_factory=list)
    header_search_rows: int | None = None
    aliases: dict[str, str] = Field(default_factory=dict)


class Banks(BaseModel):
    banks: list[Bank]
    overrides: dict[str, BankOverride] = Field(default_factory=dict)


class LLMSettings(BaseModel):
    enabled: bool = True
    base_url: str = "http://localhost:1234/v1"
    model: str = "local-model"
    timeout_seconds: int = Field(default=60, gt=0)
    confidence_floor: float = Field(default=0.70, ge=0, le=1)
    send_sample_values: bool = False
    sample_size: int = Field(default=5, ge=0, le=50)
    require_loopback: bool = True


class MatchingSettings(BaseModel):
    fuzzy_auto_accept: int = Field(default=92, ge=0, le=100)
    fuzzy_suggest_floor: int = Field(default=70, ge=0, le=100)

    def model_post_init(self, _context: Any) -> None:
        if self.fuzzy_suggest_floor > self.fuzzy_auto_accept:
            raise ValueError(
                "matching.fuzzy_suggest_floor cannot exceed fuzzy_auto_accept: "
                "no header could ever be suggested without being auto-accepted first"
            )


class DetectionSettings(BaseModel):
    header_search_rows: int = 30
    blank_run_ends_table: int = 2
    terminator_patterns: list[str] = Field(default_factory=list)
    min_column_fill_rate: float = 0.05

    @field_validator("terminator_patterns")
    @classmethod
    def _normalize_terminators(cls, v: list[str]) -> list[str]:
        return [normalize_header(p) for p in v]


class ValidationSettings(BaseModel):
    # A negative tolerance would make every reconciliation fail; a negative lag would
    # flag every row. Both are silent disasters, so they are rejected at load.
    premium_tolerance: float = Field(default=0.01, ge=0)
    max_declaration_lag_months: int = Field(default=3, ge=0)
    period_agreement_threshold: float = Field(default=0.5, ge=0, le=1)


class WarehouseSettings(BaseModel):
    path: str = "warehouse"
    # Fallback product, used when a file's product cannot be identified.
    profile: str = "generique"
    write_xlsx: bool = True
    # Excel tops out just above a million rows. Refusing beats truncating in silence.
    xlsx_row_limit: int = Field(default=1_000_000, gt=0, le=1_048_575)


class Settings(BaseModel):
    llm: LLMSettings = Field(default_factory=LLMSettings)
    matching: MatchingSettings = Field(default_factory=MatchingSettings)
    detection: DetectionSettings = Field(default_factory=DetectionSettings)
    validation: ValidationSettings = Field(default_factory=ValidationSettings)
    warehouse: WarehouseSettings = Field(default_factory=WarehouseSettings)


class AliasStore(BaseModel):
    """Learned header -> canonical mappings.

    This file is the project's core asset: it is what makes the second run of any month
    free of model calls. It grows every time a human or the model resolves a header.
    """

    global_: dict[str, str] = Field(default_factory=dict, alias="global")
    by_bank: dict[str, dict[str, str]] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}

    def lookup(self, normalized_header: str, bank: str | None = None) -> str | None:
        """Bank-specific mappings win over global ones."""
        if bank and bank in self.by_bank:
            hit = self.by_bank[bank].get(normalized_header)
            if hit:
                return hit
        return self.global_.get(normalized_header)

    def learn(self, normalized_header: str, canonical: str, bank: str | None = None) -> None:
        """Record a resolution so it is never asked again."""
        if bank:
            self.by_bank.setdefault(bank, {})[normalized_header] = canonical
        else:
            self.global_[normalized_header] = canonical

    def to_yaml_dict(self) -> dict:
        return {
            "global": dict(sorted(self.global_.items())),
            "by_bank": {
                bank: dict(sorted(m.items())) for bank, m in sorted(self.by_bank.items())
            },
        }


class Config(BaseModel):
    """Everything the pipeline needs, loaded and validated together."""

    schema_: Schema = Field(alias="schema")
    banks: Banks
    settings: Settings
    aliases: AliasStore
    config_dir: Path

    model_config = {"populate_by_name": True, "arbitrary_types_allowed": True}

    def model_post_init(self, _context: Any) -> None:
        # Catching this at load turns a KeyError deep in the pipeline into a clear
        # message naming the line of configuration that is wrong.
        defaut = self.settings.warehouse.profile
        if defaut and defaut not in self.schema_.profiles:
            raise ValueError(
                f"settings.yaml names the product {defaut!r}, which does not exist in "
                f"produits.yaml. Known products: {sorted(self.schema_.profiles)}"
            )

    def produit(self, code: str) -> ExportProfile:
        """Look up a product, failing with a list of the real ones rather than KeyError."""
        try:
            return self.schema_.profiles[code]
        except KeyError:
            raise KeyError(
                f"produit inconnu : {code!r}. Produits connus : "
                f"{sorted(self.schema_.profiles)}"
            ) from None

    def aliases_du_produit(self, code: str | None) -> dict[str, str]:
        """Header meanings specific to one product, which beat the general catalogue."""
        if not code or code not in self.schema_.profiles:
            return {}
        return dict(self.schema_.profiles[code].aliases)

    def save_aliases(self) -> None:
        """Persist learned mappings back to disk."""
        path = self.config_dir / "aliases.yaml"
        header = (
            "# Learned header -> canonical field map. Grows as the tool is used.\n"
            "# Keys are normalized headers (accent-folded, lowercased, "
            "punctuation collapsed).\n"
            "# `global` applies everywhere; a bank code section overrides it "
            "for that bank only.\n\n"
        )
        body = yaml.safe_dump(
            self.aliases.to_yaml_dict(), allow_unicode=True, sort_keys=False
        )
        path.write_text(header + body, encoding="utf-8")

    def seed_aliases_from_schema(self) -> dict[str, str]:
        """Build the starting header->field map from the aliases declared in schema.yaml.

        These are combined with the learned store at match time; the learned store wins
        on conflict because it reflects a decision an actual human made.
        """
        seed: dict[str, str] = {}
        for name, field in self.schema_.fields.items():
            # The canonical name and its label are themselves valid aliases.
            for alias in {*field.aliases, normalize_header(name), normalize_header(field.label)}:
                if alias:
                    seed.setdefault(alias, name)
        return seed


def load_config(config_dir: str | Path = "config") -> Config:
    """Load and validate every config file. Raises on the first problem found."""
    config_dir = Path(config_dir)

    def _read(name: str) -> dict:
        path = config_dir / name
        if not path.exists():
            raise FileNotFoundError(f"missing config file: {path}")
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    donnees_schema = _read("schema.yaml")
    # Products live in their own file: the field catalogue is shared and stable, while
    # products are what people actually add and change.
    produits = _read("produits.yaml").get("produits", {})
    donnees_schema["profiles"] = {
        code: {
            "columns": bloc.get("colonnes", bloc.get("columns", [])),
            "required": bloc.get("requis", bloc.get("required", [])),
            "label": bloc.get("label", code),
            "famille": bloc.get("famille", "autre"),
            "partenaires": bloc.get("partenaires", []),
            "match": bloc.get("match", []),
            "aliases": bloc.get("aliases", {}),
        }
        for code, bloc in produits.items()
    }

    return Config(
        schema=Schema(**donnees_schema),
        banks=Banks(**_read("banks.yaml")),
        settings=Settings(**_read("settings.yaml")),
        aliases=AliasStore(**_read("aliases.yaml")),
        config_dir=config_dir,
    )
