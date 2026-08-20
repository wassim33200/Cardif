"""Text normalization shared by header matching, bank lookup and filename parsing.

Every comparison in this project happens on normalized text, never on raw text. Bank
staff type headers by hand, so `"N° Contrat "`, `"n contrat"` and `"N.Contrat"` are the
same thing and must fold to the same key.
"""

from __future__ import annotations

import re
import unicodedata

# Characters that appear inside hand-typed headers and carry no meaning for matching.
# The underscore is included explicitly: it is a word character to `\w`, but in folder
# and file names ("biat_2024", "N_contrat") it is a separator like any other.
_PUNCT = re.compile(r"[^\w\s]|_", flags=re.UNICODE)
_WS = re.compile(r"\s+")

# Excel and copy-paste from web pages leave these behind; they are spaces, not letters.
_SPACE_LIKE = {
    " ": " ",  # no-break space
    " ": " ",  # narrow no-break space
    " ": " ",  # figure space
    " ": " ",  # thin space
    "​": "",   # zero-width space
    "﻿": "",   # BOM
}


def strip_accents(text: str) -> str:
    """Remove diacritics: 'Capital assuré' -> 'Capital assure'.

    Banks are inconsistent about accents even within one file, so they cannot be
    allowed to affect matching.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def clean_spaces(text: str) -> str:
    """Replace exotic Unicode spaces with plain ones and collapse runs."""
    for bad, good in _SPACE_LIKE.items():
        text = text.replace(bad, good)
    return _WS.sub(" ", text).strip()


def normalize_header(text: object) -> str:
    """Fold a header cell to its canonical comparison key.

    Lowercased, accent-free, punctuation stripped, single-spaced::

        "  N° Contrat "   -> "n contrat"
        "Prime Nette (DT)" -> "prime nette dt"
        "Date d'effet"     -> "date d effet"

    Returns an empty string for blank or non-text cells, which callers treat as
    "this column has no header".
    """
    if text is None:
        return ""
    s = str(text)
    s = clean_spaces(s)
    if not s:
        return ""
    s = strip_accents(s).lower()
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def normalize_loose(text: object) -> str:
    """Like :func:`normalize_header` but with spaces removed entirely.

    Used as a second-chance exact key so that "n contrat" and "ncontrat" collide.
    """
    return normalize_header(text).replace(" ", "")


def is_blank(value: object) -> bool:
    """True for cells that hold nothing a human would call data."""
    if value is None:
        return True
    if isinstance(value, str):
        return clean_spaces(value) == ""
    # NaN is the only value that is not equal to itself.
    return value != value
