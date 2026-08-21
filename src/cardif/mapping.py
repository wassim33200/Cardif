"""Resolve a sheet's headers to canonical fields.

Three tiers, cheapest first, so that the expensive one is rarely reached:

1. **exact**  - the normalized header is already known, from ``schema.yaml`` or from
   ``aliases.yaml`` (which records every decision a human or the model has ever made)
2. **fuzzy**  - ``rapidfuzz`` above a high threshold, for typos and word-order drift
3. **model**  - the local LLM, consulted only for what is left, via an injected callable

Anything still unresolved, or resolved below the confidence floor, goes to the human.

Independently of the name, every mapping is checked against the column's *contents*.
This is what catches the genuinely dangerous failure: two banks using the same word for
different things. A column whose header reads perfectly as a date but whose values are
all amounts is flagged regardless of how good the name looked.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from .config import Config
from .coerce import to_date, to_number
from .normalize import is_blank, normalize_header, normalize_loose

# Signature of the optional model tier, kept as a plain callable so that mapping has no
# dependency on the LLM client and stays testable without one.
ModelResolver = Callable[[str, "ColumnProfile", list[str]], "tuple[str | None, float, str]"]


@dataclass
class ColumnProfile:
    """What a column's values actually look like.

    Doubles as the anonymised description sent to the local model: it carries shapes and
    counts, never client names or contract numbers, unless sample values are explicitly
    enabled in settings.
    """

    total: int = 0
    non_blank: int = 0
    numeric_ratio: float = 0.0
    date_ratio: float = 0.0
    unique_ratio: float = 0.0
    min_value: float | None = None
    max_value: float | None = None
    mean_length: float = 0.0
    samples: list[str] = field(default_factory=list)

    def describe(self, include_samples: bool = False) -> str:
        """A compact, privacy-safe sentence for the model prompt."""
        bits = [
            f"{self.non_blank} non-empty of {self.total} values",
            f"{self.unique_ratio:.0%} distinct",
        ]
        if self.numeric_ratio > 0.1:
            bits.append(f"{self.numeric_ratio:.0%} parse as numbers")
            if self.min_value is not None:
                bits.append(f"range {self.min_value:.2f}..{self.max_value:.2f}")
        if self.date_ratio > 0.1:
            bits.append(f"{self.date_ratio:.0%} parse as dates")
        if self.numeric_ratio < 0.5 and self.date_ratio < 0.5:
            bits.append(f"average text length {self.mean_length:.0f}")
        if include_samples and self.samples:
            bits.append("examples: " + ", ".join(self.samples))
        return "; ".join(bits)


@dataclass
class MappingResult:
    """One header's fate, with the reasoning kept for the audit and the review UI."""

    header: str
    normalized: str
    column_index: int
    canonical: str | None = None
    method: str = "unresolved"        # exact | fuzzy | model | manual | unresolved
    confidence: float = 0.0
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    profile: ColumnProfile | None = None
    suggestions: list[tuple[str, float]] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return self.canonical is None or bool(self.warnings) or self.method == "model"


def profile_column(values: list, sample_size: int = 5) -> ColumnProfile:
    """Measure a column's contents without interpreting them."""
    profile = ColumnProfile(total=len(values))
    present = [v for v in values if not is_blank(v)]
    profile.non_blank = len(present)
    if not present:
        return profile

    numbers, dates, lengths = [], 0, []
    for value in present:
        number = to_number(value)
        if number is not None:
            numbers.append(number)
        # A real date object, or text that parses as one. Plain integers are not dates:
        # otherwise every amount column would look date-like via the serial branch.
        if isinstance(value, (dt.date, dt.datetime)) or (
            isinstance(value, str) and to_date(value) is not None
        ):
            dates += 1
        lengths.append(len(str(value)))

    profile.numeric_ratio = len(numbers) / len(present)
    profile.date_ratio = dates / len(present)
    profile.unique_ratio = len({str(v) for v in present}) / len(present)
    profile.mean_length = sum(lengths) / len(lengths)
    if numbers:
        profile.min_value = min(numbers)
        profile.max_value = max(numbers)
    profile.samples = [str(v)[:30] for v in present[:sample_size]]
    return profile


def verify_against_content(
    canonical: str, profile: ColumnProfile, config: Config
) -> list[str]:
    """Check a proposed mapping against what the column actually holds.

    Returns warnings, not errors: the mapping may still be right and the data wrong.
    The human decides, but is never left unaware.
    """
    warnings: list[str] = []
    field_def = config.schema_.fields.get(canonical)
    if field_def is None or profile.non_blank == 0:
        return warnings

    expect = field_def.expect
    if expect.mostly == "numeric" and profile.numeric_ratio < 0.6:
        warnings.append(
            f"expects numbers but only {profile.numeric_ratio:.0%} of values parse as numbers"
        )
    if expect.mostly == "date" and profile.date_ratio < 0.6:
        warnings.append(
            f"expects dates but only {profile.date_ratio:.0%} of values parse as dates"
        )
    if expect.mostly in {"text", "identifier"} and profile.numeric_ratio > 0.9 and profile.date_ratio < 0.5:
        warnings.append(
            f"expects {expect.mostly} but {profile.numeric_ratio:.0%} of values are numeric"
        )
    if expect.min_value is not None and profile.min_value is not None:
        if profile.min_value < expect.min_value:
            warnings.append(
                f"minimum value {profile.min_value:.2f} is below the expected "
                f"{expect.min_value:.2f}"
            )
    if expect.max_value is not None and profile.max_value is not None:
        if profile.max_value > expect.max_value:
            warnings.append(
                f"maximum value {profile.max_value:.2f} exceeds the expected "
                f"{expect.max_value:.2f}"
            )
    if expect.min_unique_ratio is not None and profile.unique_ratio < expect.min_unique_ratio:
        warnings.append(
            f"only {profile.unique_ratio:.0%} distinct values, expected at least "
            f"{expect.min_unique_ratio:.0%} for an identifier"
        )
    if expect.max_unique_ratio is not None and profile.unique_ratio > expect.max_unique_ratio:
        warnings.append(
            f"{profile.unique_ratio:.0%} distinct values, expected at most "
            f"{expect.max_unique_ratio:.0%} for a category"
        )
    return warnings


class Mapper:
    """Resolves headers to canonical fields, learning as it goes."""

    def __init__(self, config: Config, model_resolver: ModelResolver | None = None):
        self.config = config
        self.model_resolver = model_resolver
        # schema.yaml aliases form the floor; learned aliases override them because they
        # reflect a decision an actual human made about these actual banks.
        self.seed = config.seed_aliases_from_schema()
        self.loose_seed = {
            normalize_loose(alias): canonical for alias, canonical in self.seed.items()
        }
        self.candidates = list(config.schema_.fields)

    def _exact(
        self, normalized: str, bank: str | None, produit: str | None = None
    ) -> tuple[str | None, str]:
        # A product's own aliases win over everything: "capital souscrit" means the
        # sum insured in a prevoyance file and nothing at all in an ADE file.
        specifique = self.config.aliases_du_produit(produit)
        if normalized in specifique:
            return specifique[normalized], "intitulé propre au produit"
        learned = self.config.aliases.lookup(normalized, bank)
        if learned:
            return learned, "learned alias"
        seeded = self.seed.get(normalized)
        if seeded:
            return seeded, "schema alias"
        loose = self.loose_seed.get(normalize_loose(normalized))
        if loose:
            return loose, "schema alias (spacing ignored)"
        return None, ""

    def _fuzzy(self, normalized: str) -> list[tuple[str, float]]:
        """Score the header against every known alias, best canonical field first.

        ``WRatio`` rather than ``token_set_ratio``: token-set scores 100 for any subset
        match, so the short aliases "prime" and "frais" both scored a perfect 100
        against the header "Prime frais" and the tie was broken arbitrarily. WRatio
        penalises the length mismatch and keeps the ranking meaningful.
        """
        if not normalized:
            return []
        known = list(self.seed) + list(self.config.aliases.global_)
        matches = process.extract(
            normalized, known, scorer=fuzz.WRatio, limit=10
        )
        best: dict[str, float] = {}
        for alias, score, _ in matches:
            canonical = self.config.aliases.global_.get(alias) or self.seed.get(alias)
            if canonical is None:
                continue
            best[canonical] = max(best.get(canonical, 0.0), float(score))
        return sorted(best.items(), key=lambda kv: kv[1], reverse=True)

    def resolve_header(
        self,
        header: str,
        column_index: int,
        values: list,
        bank: str | None = None,
        leaf: str | None = None,
        produit: str | None = None,
    ) -> MappingResult:
        """Resolve a single header through the tiers.

        ``leaf`` is the last part of a two-row header ("frais" under a merged "Prime").
        It is tried as an exact match after the full header, because a merged group
        label is shared by its sibling columns and cannot identify any of them.
        """
        normalized = normalize_header(header)
        settings = self.config.settings
        profile = profile_column(values, settings.llm.sample_size)
        result = MappingResult(
            header=header,
            normalized=normalized,
            column_index=column_index,
            profile=profile,
        )

        if not normalized:
            result.reason = "column has no header text"
            return result

        # --- tier 1 ---------------------------------------------------------------
        canonical, why = self._exact(normalized, bank, produit)
        if canonical is None and leaf:
            normalized_leaf = normalize_header(leaf)
            if normalized_leaf and normalized_leaf != normalized:
                canonical, why = self._exact(normalized_leaf, bank, produit)
                if canonical:
                    why = f"{why}, on the sub-heading '{leaf}'"
        if canonical:
            result.canonical = canonical
            result.method = "exact"
            result.confidence = 1.0
            result.reason = why
            result.warnings = verify_against_content(canonical, profile, self.config)
            return result

        # --- tier 2 ---------------------------------------------------------------
        ranked = self._fuzzy(normalized)
        result.suggestions = [
            (name, score) for name, score in ranked
            if score >= settings.matching.fuzzy_suggest_floor
        ][:3]

        if ranked and ranked[0][1] >= settings.matching.fuzzy_auto_accept:
            canonical, score = ranked[0]
            result.canonical = canonical
            result.method = "fuzzy"
            result.confidence = score / 100.0
            result.reason = f"fuzzy match at {score:.0f}"
            result.warnings = verify_against_content(canonical, profile, self.config)
            return result

        # --- tier 3 ---------------------------------------------------------------
        if self.model_resolver is not None and settings.llm.enabled:
            canonical, confidence, why = self.model_resolver(
                header, profile, self.candidates
            )
            if canonical and confidence >= settings.llm.confidence_floor:
                result.canonical = canonical
                result.method = "model"
                result.confidence = confidence
                result.reason = why
                result.warnings = verify_against_content(canonical, profile, self.config)
                return result
            if canonical:
                result.reason = (
                    f"model suggested {canonical} at {confidence:.0%}, "
                    f"below the {settings.llm.confidence_floor:.0%} floor"
                )
                result.suggestions.insert(0, (canonical, confidence * 100))
                return result

        result.reason = "no alias, fuzzy or model match"
        return result

    def resolve_table(
        self,
        headers: list[str],
        rows: list[list],
        bank: str | None = None,
        header_parts: list[list[str]] | None = None,
        produit: str | None = None,
    ) -> list[MappingResult]:
        """Resolve every header of a sheet, then reconcile conflicts between them.

        Two columns claiming the same canonical field is a real occurrence — a bank that
        writes both "Prime" and "Montant prime" — and must never be resolved by silently
        letting the last one win.
        """
        results = []
        for index, header in enumerate(headers):
            values = [row[index] if index < len(row) else None for row in rows]
            parts = header_parts[index] if header_parts and index < len(header_parts) else None
            leaf = parts[-1] if parts and len(parts) > 1 else None
            results.append(
                self.resolve_header(header, index, values, bank, leaf, produit)
            )

        claims: dict[str, list[MappingResult]] = {}
        for result in results:
            if result.canonical:
                claims.setdefault(result.canonical, []).append(result)

        for canonical, claimants in claims.items():
            if len(claimants) < 2:
                continue
            # Keep the most confident; demote the rest to unresolved for the human.
            claimants.sort(key=lambda r: (r.confidence, r.method == "exact"), reverse=True)
            winner = claimants[0]
            for loser in claimants[1:]:
                loser.warnings.append(
                    f"also matched '{canonical}', which column "
                    f"{loser.column_index + 1} lost to '{winner.header}'"
                )
                loser.suggestions.insert(0, (canonical, loser.confidence * 100))
                loser.canonical = None
                loser.method = "unresolved"
                loser.confidence = 0.0
                loser.reason = f"duplicate claim on '{canonical}'"

        return results

    def learn(self, result: MappingResult, canonical: str, bank: str | None = None) -> None:
        """Record a resolution so this header is never asked about again."""
        if not result.normalized:
            return
        self.config.aliases.learn(result.normalized, canonical, bank)
