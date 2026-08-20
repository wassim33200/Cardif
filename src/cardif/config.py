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
    "identifier", "text", "category", "date", "money", "integer", "period"
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
    """The user's declared target schema: which columns an export contains."""

    columns: list[str]
    required: list[str] = Field(default_factory=list)


class Schema(BaseModel):
    fields: dict[str, CanonicalField]
    derived_fields: dict[str, DerivedField] = Field(default_factory=dict)
    profiles: dict[str, ExportProfile]

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
    timeout_seconds: int = 60
    confidence_floor: float = 0.70
    send_sample_values: bool = False
    sample_size: int = 5
    require_loopback: bool = True


class MatchingSettings(BaseModel):
    fuzzy_auto_accept: int = 92
    fuzzy_suggest_floor: int = 70


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
    premium_tolerance: float = 0.01
    max_declaration_lag_months: int = 3
    period_agreement_threshold: float = 0.5


class WarehouseSettings(BaseModel):
    path: str = "warehouse"
    profile: str = "powerbi_2025"
    write_xlsx: bool = True
    xlsx_row_limit: int = 1_000_000


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

    return Config(
        schema=Schema(**_read("schema.yaml")),
        banks=Banks(**_read("banks.yaml")),
        settings=Settings(**_read("settings.yaml")),
        aliases=AliasStore(**_read("aliases.yaml")),
        config_dir=config_dir,
    )
