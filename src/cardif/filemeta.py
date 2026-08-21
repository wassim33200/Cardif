"""Bank and reporting period, derived from the folder and filename.

These two facts exist nowhere inside the sheet. The folder says which bank sent the
file; the filename says which month it reports. Both are resolved deterministically
wherever possible — a regex that reads "mars" is auditable, whereas a model that reads
"mars" would have to be re-verified on every run.

Resolution is tiered, cheapest first:

1. numeric patterns    - ``_03_2025``, ``2025-03``, ``202503``
2. French month names  - accent-folded, with the truncations banks actually type
3. the local model     - filename string only, never file contents
4. the human           - surfaced in the UI, then cached

The period derived here is a *claim*. :mod:`cardif.validate` cross-checks it against the
dates actually in the rows, because a file named "Mars" full of April business is a real
occurrence and must be flagged rather than trusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import Banks, Schema
from .normalize import normalize_header

# Accent-folded month stems, longest first so "juillet" is not eaten by "jui".
# Each entry maps a regex alternation to its month number.
MONTH_PATTERNS: list[tuple[str, int]] = [
    (r"janvier|janv|jan", 1),
    (r"fevrier|fevr|fev", 2),
    (r"mars|mar", 3),
    (r"avril|avr", 4),
    (r"mai", 5),
    (r"juillet|juil|jul", 7),   # before "juin": "juil" must not match "jui"
    (r"juin|jun", 6),
    (r"aout|aou", 8),
    (r"septembre|sept|sep", 9),
    (r"octobre|octo|oct", 10),
    (r"novembre|nov", 11),
    (r"decembre|dec", 12),
]

_MONTH_RE = re.compile(
    "|".join(f"(?P<m{num}>{pat})" for pat, num in MONTH_PATTERNS)
)

# yyyy-mm / yyyy_mm / yyyymm, and mm-yyyy / mm_yyyy. Bare "2025" alone is not a period.
_YM_SEPARATED = re.compile(r"(?<!\d)(20\d{2})[\-_. ]?(0[1-9]|1[0-2])(?!\d)")
_MY_SEPARATED = re.compile(r"(?<!\d)(0[1-9]|1[0-2])[\-_. ](20\d{2})(?!\d)")
_YEAR4 = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_YEAR2 = re.compile(r"(?<!\d)(\d{2})(?!\d)")


@dataclass
class FileMeta:
    """What the path tells us about a file, and how confidently."""

    path: Path
    bank_code: str | None = None
    bank_label: str | None = None
    bank_method: str = "unresolved"
    year: int | None = None
    month: int | None = None
    period_method: str = "unresolved"
    period_confidence: float = 0.0
    produit: str | None = None
    produit_label: str | None = None
    produit_method: str = "unresolved"
    notes: list[str] = field(default_factory=list)

    @property
    def period(self) -> str | None:
        """The reporting period as ``YYYY-MM``, which becomes ``mois_reception``."""
        if self.year is None or self.month is None:
            return None
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def resolved(self) -> bool:
        return (
            self.bank_code is not None
            and self.period is not None
            and self.produit is not None
        )

    def describe(self) -> str:
        bank = self.bank_code or "?"
        period = self.period or "?"
        produit = self.produit or "?"
        return f"{self.path.name}: {bank} / {produit} / {period}"


def resolve_bank(folder_name: str, banks: Banks) -> tuple[str | None, str | None, str]:
    """Match a folder name against the configured banks.

    The year is stripped first so ``"BNA 2025"``, ``"bna_2024"`` and ``"BNA"`` all land
    on the same bank. Longer match tokens are tried first so that a bank whose code is a
    prefix of another's cannot shadow it.
    """
    normalized = normalize_header(_YEAR4.sub(" ", folder_name))
    if not normalized:
        return None, None, "unresolved"

    candidates: list[tuple[str, object]] = []
    for bank in banks.banks:
        for token in bank.match:
            candidates.append((token, bank))
    candidates.sort(key=lambda t: len(t[0]), reverse=True)

    # Exact match on the whole folder name wins outright.
    for token, bank in candidates:
        if normalized == token:
            return bank.code, bank.label, "folder_exact"

    # Otherwise the token must appear as a whole word, so "bh" does not match "bhx".
    for token, bank in candidates:
        if re.search(rf"(?<!\w){re.escape(token)}(?!\w)", normalized):
            return bank.code, bank.label, "folder_token"

    return None, None, "unresolved"


def _year_from(text: str, fallback_year: int | None) -> tuple[int | None, bool]:
    """Pull a year out of a filename, falling back to the folder's year.

    Returns the year and whether it came from an explicit 4-digit token.
    """
    m = _YEAR4.search(text)
    if m:
        return int(m.group(1)), True
    # A bare 2-digit year like "25" is only trusted when it is plausible.
    for m2 in _YEAR2.finditer(text):
        candidate = 2000 + int(m2.group(1))
        if 2000 <= candidate <= 2099:
            return candidate, False
    return fallback_year, False


def resolve_period(
    filename: str, fallback_year: int | None = None
) -> tuple[int | None, int | None, str, float]:
    """Extract (year, month) from a filename deterministically.

    Returns ``(year, month, method, confidence)``. ``method`` is ``"numeric"``,
    ``"month_name"`` or ``"unresolved"`` — the model tier lives in :mod:`cardif.llm`
    and is only consulted when this returns unresolved.
    """
    stem = Path(filename).stem
    text = normalize_header(stem)
    if not text:
        return None, None, "unresolved", 0.0

    # --- tier 1: numeric patterns ------------------------------------------------
    m = _YM_SEPARATED.search(text)
    if m:
        return int(m.group(1)), int(m.group(2)), "numeric", 1.0
    m = _MY_SEPARATED.search(text)
    if m:
        return int(m.group(2)), int(m.group(1)), "numeric", 1.0

    # --- tier 2: French month names ----------------------------------------------
    hits = []
    for match in _MONTH_RE.finditer(text):
        month = int(match.lastgroup[1:])
        hits.append((match.start(), month))

    if hits:
        distinct = {month for _, month in hits}
        year, explicit = _year_from(text, fallback_year)
        if len(distinct) > 1:
            # Two month names in one filename is genuinely ambiguous; hand it upward
            # rather than silently picking the first.
            return year, None, "unresolved", 0.0
        month = hits[0][1]
        if year is None:
            return None, month, "unresolved", 0.0
        # A 2-digit or inherited year is slightly less certain than an explicit one.
        confidence = 1.0 if explicit else 0.85
        return year, month, "month_name", confidence

    return None, None, "unresolved", 0.0


def year_from_folder(folder_name: str) -> int | None:
    """The year a bank folder is labelled with, used as the filename's fallback."""
    m = _YEAR4.search(folder_name)
    return int(m.group(1)) if m else None


def resolve_produit(
    path: Path, schema: Schema, banque: str | None = None
) -> tuple[str | None, str | None, str]:
    """Work out which product a file holds, from its name and its folders.

    Each bank sends one file per product, and names it after the product: the words
    "ADE immobilier", "SAHTI", "CTP", "Visa" appear in the filename or in a sub-folder.
    Matching is done on the longest product keyword first, so "ade immobilier" is not
    shadowed by a shorter "ade", and only against products the bank actually
    distributes, which removes most of the ambiguity for free.

    Returns ``(code, label, method)``.
    """
    # The filename plus every folder above it, so "CNEP 2025/SAHTI/mars.xlsx" works
    # as well as "CNEP 2025/SAHTI_mars_2025.xlsx".
    parties = [path.stem, *(parent.name for parent in path.parents)]
    texte = normalize_header(" ".join(parties))
    if not texte:
        return None, None, "unresolved"

    candidats = schema.produits_du_partenaire(banque)
    motifs: list[tuple[str, str]] = []
    for code in candidats:
        produit = schema.profiles[code]
        for motif in produit.match:
            if motif:
                motifs.append((motif, code))
    # Longest keyword wins: "ade immobilier" must beat "ade".
    motifs.sort(key=lambda item: len(item[0]), reverse=True)

    trouves: list[tuple[str, str]] = []
    for motif, code in motifs:
        if re.search(rf"(?<!\w){re.escape(motif)}(?!\w)", texte):
            trouves.append((motif, code))

    if not trouves:
        return None, None, "unresolved"

    meilleur_motif, meilleur_code = trouves[0]
    # Two different products matched with keywords of the same length: genuinely
    # ambiguous, so hand it over rather than pick one.
    concurrents = {
        code for motif, code in trouves
        if len(motif) == len(meilleur_motif) and code != meilleur_code
    }
    if concurrents:
        return None, None, "ambiguous"

    return meilleur_code, schema.profiles[meilleur_code].label, "nom_fichier"


def read_meta(path: Path, banks: Banks, schema: Schema | None = None) -> FileMeta:
    """Resolve bank and period for a single workbook path."""
    folder = path.parent.name
    code, label, bank_method = resolve_bank(folder, banks)
    fallback = year_from_folder(folder)
    year, month, method, confidence = resolve_period(path.name, fallback)

    meta = FileMeta(
        path=path,
        bank_code=code,
        bank_label=label,
        bank_method=bank_method,
        year=year,
        month=month,
        period_method=method,
        period_confidence=confidence,
    )

    if schema is not None:
        produit, produit_label, produit_method = resolve_produit(path, schema, code)
        meta.produit = produit
        meta.produit_label = produit_label
        meta.produit_method = produit_method
        if produit is None:
            if produit_method == "ambiguous":
                meta.notes.append(
                    "Plusieurs produits correspondent au nom de ce fichier ; "
                    "précisez lequel."
                )
            else:
                meta.notes.append(
                    "Aucun produit reconnu dans le nom du fichier. Ajoutez le nom du "
                    "produit au nom du fichier (par exemple « SAHTI_Mars_2025.xlsx ») "
                    "ou rangez-le dans un sous-dossier portant ce nom."
                )
    if code is None:
        meta.notes.append(
            f"Le dossier « {folder} » ne correspond à aucune banque enregistrée."
        )
    if method == "unresolved":
        if month is not None and year is None:
            meta.notes.append(
                "Le mois est reconnu mais l'année est absente du nom du fichier "
                "comme du dossier."
            )
        elif month is None and year is not None:
            meta.notes.append("L'année est reconnue mais le mois est absent du nom.")
        else:
            meta.notes.append(
                "Aucun mois n'a pu être lu dans le nom du fichier."
            )
    elif year is not None and fallback is not None and year != fallback:
        # Not an error — a December file can be delivered in January — but worth saying.
        meta.notes.append(
            f"Le nom du fichier indique {year} alors que le dossier indique "
            f"{fallback}. Ce n'est pas forcément une erreur."
        )
    return meta


# Workbook extensions we attempt to read. `.xls` needs python-calamine; see scan().
WORKBOOK_SUFFIXES = {".xlsx", ".xlsm", ".xls"}


def scan(root: Path, banks: Banks, schema: Schema | None = None) -> list[FileMeta]:
    """Walk a data root and resolve every workbook found.

    Reads only paths, never file contents, so this is instant even on a full year of
    every bank. Temporary Excel lock files (``~$…``) are skipped.
    """
    out = []
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("~$") or path.name.startswith("."):
            continue
        if path.suffix.lower() not in WORKBOOK_SUFFIXES:
            continue
        meta = read_meta(path, banks, schema)
        if path.suffix.lower() == ".xls":
            meta.notes.append(
                "Ancien format .xls : enregistrez ce fichier au format .xlsx depuis "
                "Excel (Fichier ▸ Enregistrer sous ▸ Classeur Excel)."
            )
        out.append(meta)
    return out
