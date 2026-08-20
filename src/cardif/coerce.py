"""Turn hand-typed cell values into typed data without corrupting them.

This module exists because the default behaviours are dangerous:

* ``pd.to_datetime("03/04/2025")`` reads 3 March. In these files it is 3 April.
  Day-first is forced everywhere, never inferred.
* Guessing the decimal separator per cell turns ``"1.234"`` into ``1234`` in one row and
  ``1.234`` in the next. The separator is decided **once per column, by majority vote**,
  and then applied uniformly.
* Letting pandas type a contract number as a float yields ``1.23457e+11``. Identifiers
  are strings, always.

Every conversion that fails returns ``None`` and is counted, so a column that silently
refuses to parse shows up in the audit instead of arriving as an empty column.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from .normalize import clean_spaces, is_blank, strip_accents

# Excel's epoch. Serial 1 is 1900-01-01, but Excel wrongly treats 1900 as a leap year,
# so serials at or above 60 are shifted by one day relative to a naive calculation.
_EXCEL_EPOCH = dt.date(1899, 12, 30)

_CURRENCY = re.compile(r"[€$£]|\b(dt|tnd|eur|usd|dh|mad|dzd)\b", re.IGNORECASE)
_NON_NUMERIC = re.compile(r"[^\d.,\-+]")

_FRENCH_MONTH_NAMES = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12,
}
# Abbreviations that are ambiguous as a prefix, or that are not prefixes at all, and so
# cannot be resolved by the general rule below.
_FRENCH_MONTH_ALIASES = {"jan": 1, "fev": 2, "jun": 6, "jul": 7, "sept": 9}


def french_month(token: str) -> int | None:
    """Resolve a French month name or any unambiguous abbreviation of one.

    Banks truncate month names to whatever their template or locale produced: "nov",
    "nove", "novembre" all appear, as do "avri", "dece" and "sept". Matching on a
    prefix rather than an enumerated list handles every truncation length at once.

    An abbreviation matching more than one month resolves to nothing rather than to a
    guess -- "jui" is both juin and juillet, and picking one silently would put sales in
    the wrong month.
    """
    token = token.strip().lower()
    if not token:
        return None
    if token in _FRENCH_MONTH_NAMES:
        return _FRENCH_MONTH_NAMES[token]
    if token in _FRENCH_MONTH_ALIASES:
        return _FRENCH_MONTH_ALIASES[token]
    if len(token) < 3:
        return None
    matches = {
        month for name, month in _FRENCH_MONTH_NAMES.items() if name.startswith(token)
    }
    return matches.pop() if len(matches) == 1 else None
_TEXT_DATE = re.compile(r"^(\d{1,2})[\-/ .]([a-z]+)[\-/ .](\d{2,4})$")
_NUMERIC_DATE = re.compile(r"^(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{2,4})$")


@dataclass
class ColumnReport:
    """What happened when a column was coerced, for the audit trail."""

    header: str
    target_type: str
    total: int = 0
    converted: int = 0
    blank: int = 0
    failed: int = 0
    decimal_separator: str | None = None
    failures: list[str] = field(default_factory=list)

    @property
    def failure_rate(self) -> float:
        considered = self.total - self.blank
        return self.failed / considered if considered else 0.0


def _strip_currency(text: str) -> str:
    return _CURRENCY.sub("", text)


def detect_decimal_separator(values: list) -> str:
    """Decide a column's decimal separator by majority vote across its values.

    Deciding per cell is how ``1.234`` silently becomes ``1234`` in one row and stays
    ``1.234`` in the next. Voting once per column keeps a column internally consistent
    even when the vote is wrong, so an error is visible rather than scattered.

    A separator followed by exactly three digits and appearing more than once in a value
    is a thousands separator, not a decimal point.
    """
    comma_decimal = 0
    dot_decimal = 0

    for value in values:
        if not isinstance(value, str):
            continue
        text = _strip_currency(clean_spaces(value))
        text = _NON_NUMERIC.sub("", text)
        if not text:
            continue

        last_comma = text.rfind(",")
        last_dot = text.rfind(".")
        if last_comma == -1 and last_dot == -1:
            continue

        if last_comma > -1 and last_dot > -1:
            # Whichever comes last is the decimal separator.
            if last_comma > last_dot:
                comma_decimal += 1
            else:
                dot_decimal += 1
            continue

        sep = "," if last_comma > -1 else "."
        pos = last_comma if sep == "," else last_dot
        trailing = len(text) - pos - 1
        occurrences = text.count(sep)
        if occurrences > 1 or trailing == 3:
            # "1.234.567" or "1,234" -> grouping, tells us the *other* char is decimal.
            if sep == ".":
                comma_decimal += 1
            else:
                dot_decimal += 1
        else:
            if sep == ",":
                comma_decimal += 1
            else:
                dot_decimal += 1

    if comma_decimal == 0 and dot_decimal == 0:
        return ","      # French default; harmless when no separator is present at all
    return "," if comma_decimal >= dot_decimal else "."


def to_number(value: object, decimal_separator: str = ",") -> float | None:
    """Parse one cell as a number using an already-decided separator."""
    if is_blank(value):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = clean_spaces(str(value))
    if not text:
        return None

    negative = text.startswith("(") and text.endswith(")")
    text = _strip_currency(text)
    text = _NON_NUMERIC.sub("", text)
    if not text or text in {"-", "+", ".", ","}:
        return None

    if decimal_separator == ",":
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")

    # A stray second dot means grouping survived; drop all but the last.
    if text.count(".") > 1:
        head, _, tail = text.rpartition(".")
        text = head.replace(".", "") + "." + tail

    try:
        number = float(text)
    except ValueError:
        return None
    return -abs(number) if negative else number


def to_date(value: object) -> dt.date | None:
    """Parse one cell as a date, day-first, tolerating the formats banks actually use.

    Handles real Excel dates, Excel serial numbers, ``dd/mm/yyyy``, and French text
    months such as ``12-mars-2025``.
    """
    if is_blank(value):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        serial = int(value)
        # Plausible Excel serials only: 1990-01-01 to 2079-12-31.
        if 32874 <= serial <= 65746:
            return _EXCEL_EPOCH + dt.timedelta(days=serial)
        return None

    text = clean_spaces(str(value)).lower()
    if not text:
        return None
    text = strip_accents(text)

    # ISO first: unambiguous, so it must not be run through day-first logic.
    m = _NUMERIC_DATE.match(text)
    if m:
        a, b, c = m.group(1), m.group(2), m.group(3)
        if len(a) == 4:
            year, month, day = int(a), int(b), int(c)
        else:
            day, month, year = int(a), int(b), int(c)
            if year < 100:
                year += 2000 if year < 70 else 1900
        try:
            return dt.date(year, month, day)
        except ValueError:
            return None

    m = _TEXT_DATE.match(text)
    if m:
        day, month_name, year = int(m.group(1)), m.group(2), int(m.group(3))
        month = french_month(month_name)
        if month is None:
            return None
        if year < 100:
            year += 2000 if year < 70 else 1900
        try:
            return dt.date(year, month, day)
        except ValueError:
            return None

    # Datetime strings that Excel wrote out in full.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def to_identifier(value: object) -> str | None:
    """Keep an identifier as text, undoing the damage Excel may already have done.

    A contract number read as a float arrives as ``20250001.0`` or ``1.23457e+11``; both
    are recovered to their digit form rather than passed through.
    """
    if is_blank(value):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return repr(value)
    if isinstance(value, int):
        return str(value)
    text = clean_spaces(str(value))
    # "20250001.0" from a float round-trip.
    if re.fullmatch(r"\d+\.0", text):
        return text[:-2]
    return text or None


def to_text(value: object) -> str | None:
    if is_blank(value):
        return None
    return clean_spaces(str(value)) or None


def to_integer(value: object, decimal_separator: str = ",") -> int | None:
    number = to_number(value, decimal_separator)
    if number is None:
        return None
    return int(round(number))


def coerce_column(
    values: list, target_type: str, header: str = ""
) -> tuple[list, ColumnReport]:
    """Coerce a whole column at once, deciding the separator before converting.

    Returns the converted values alongside a report of what could not be parsed.
    """
    report = ColumnReport(header=header, target_type=target_type, total=len(values))

    if target_type in {"money", "integer"}:
        report.decimal_separator = detect_decimal_separator(values)

    out = []
    for value in values:
        if is_blank(value):
            report.blank += 1
            out.append(None)
            continue

        if target_type == "money":
            result = to_number(value, report.decimal_separator)
        elif target_type == "integer":
            result = to_integer(value, report.decimal_separator)
        elif target_type == "date":
            result = to_date(value)
        elif target_type == "identifier":
            result = to_identifier(value)
        else:
            result = to_text(value)

        if result is None:
            report.failed += 1
            if len(report.failures) < 10:
                report.failures.append(str(value)[:40])
        else:
            report.converted += 1
        out.append(result)

    return out, report
