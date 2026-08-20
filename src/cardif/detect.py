"""Find the real table inside a hand-filled bank workbook.

The sheets arrive with the table anywhere but row 1: blank rows above it, somebody's
scratch sum floating in a lone cell, a title line, a header split across two merged rows,
then the data, then a ``TOTAL`` line and an orphan number someone left behind.

The approach is deliberately *not* to let pandas guess. The sheet is read as a raw grid
and every structural decision is made explicitly and recorded, because the user has to be
able to answer "why is this row not in the database?" months later.

Nothing is discarded silently: every dropped row and column is returned with its original
coordinates and a reason.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from openpyxl.utils import get_column_letter

from .config import DetectionSettings
from .normalize import is_blank, normalize_header

# A cell that looks like a number even though it is stored as text: "1 234,56", "1.234,56"
_NUMERIC_TEXT = re.compile(r"^[\s+\-(]*[\d][\d\s.,  ]*\)?\s*[A-Za-z€$]{0,3}$")
# A cell that looks like a date written as text.
_DATE_TEXT = re.compile(r"^\s*\d{1,4}[/\-.]\d{1,2}[/\-.]\d{2,4}\s*$")


@dataclass
class DroppedRow:
    """A row removed from the table, with enough detail to find it in the source."""

    row: int          # 1-based Excel row number
    reason: str
    preview: str


@dataclass
class DroppedColumn:
    column: int       # 1-based Excel column index
    letter: str
    header: str
    reason: str


@dataclass
class SheetTable:
    """The table extracted from one sheet, plus the full record of what was removed."""

    sheet: str
    header_row: int          # 1-based; first row of the header
    header_row_end: int      # 1-based; same as header_row unless a two-row header
    data_start: int
    data_end: int
    headers: list[str] = field(default_factory=list)
    columns: list[int] = field(default_factory=list)   # 1-based Excel column indices
    rows: list[list] = field(default_factory=list)
    dropped_rows: list[DroppedRow] = field(default_factory=list)
    dropped_columns: list[DroppedColumn] = field(default_factory=list)
    header_score: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def source_rows(self) -> list[int]:
        """1-based Excel row number for each kept row, for provenance."""
        return list(range(self.data_start, self.data_start + len(self.rows)))


def read_grid(ws) -> list[list]:
    """Read a worksheet into a plain 0-indexed grid, resolving merged cells.

    A merged range holds its value only in the top-left cell; every other cell reads as
    ``None``. Filling the whole range with that value is what makes a merged two-row
    header (``Prime`` spanning three columns) legible to the header logic.
    """
    grid = [
        [cell.value for cell in row]
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=ws.max_column)
    ]
    if not grid:
        return []

    width = max((len(r) for r in grid), default=0)
    for row in grid:
        row.extend([None] * (width - len(row)))

    for rng in ws.merged_cells.ranges:
        top_left = grid[rng.min_row - 1][rng.min_col - 1]
        if top_left is None:
            continue
        for r in range(rng.min_row - 1, rng.max_row):
            for c in range(rng.min_col - 1, rng.max_col):
                if r < len(grid) and c < len(grid[r]):
                    grid[r][c] = top_left
    return grid


def looks_like_data(value: object) -> bool:
    """True when a cell holds a value rather than a label.

    Used as a *negative* signal for header detection: a row full of dates and amounts is
    not a header, however word-like a couple of its cells look.
    """
    if is_blank(value):
        return False
    if isinstance(value, (int, float, dt.date, dt.time)) and not isinstance(value, bool):
        return True
    text = str(value).strip()
    return bool(_NUMERIC_TEXT.match(text) or _DATE_TEXT.match(text))


def _row_stats(row: list, vocabulary: set[str]) -> dict:
    """Summarise a candidate row for scoring."""
    non_blank = [v for v in row if not is_blank(v)]
    if not non_blank:
        return {"non_blank": 0}
    normalized = [normalize_header(v) for v in non_blank]
    data_like = sum(1 for v in non_blank if looks_like_data(v))
    vocab = sum(1 for n in normalized if n in vocabulary)
    # Long free text ("Etat des ventes janvier 2025") is a title, not a header.
    wordy = sum(1 for n in normalized if len(n.split()) > 5)
    return {
        "non_blank": len(non_blank),
        "data_like": data_like,
        "vocab": vocab,
        "unique": len(set(normalized)),
        "wordy": wordy,
    }


def score_header_row(
    grid: list[list], r: int, vocabulary: set[str], max_width: int
) -> float:
    """Score row ``r`` (0-indexed) as a candidate header row.

    The dominant signal is how many cells are recognised insurance vocabulary — that is
    what distinguishes a real header from a title line or a stray label. The rest guards
    against degenerate wins: a two-cell row must not beat a twelve-cell one, a row of
    dates must not win because the dates are unique, and the rows below must actually
    look like a data block.
    """
    stats = _row_stats(grid[r], vocabulary)
    n = stats["non_blank"]
    if n < 2:
        return float("-inf")

    label_ratio = 1.0 - (stats["data_like"] / n)
    vocab_ratio = stats["vocab"] / n
    unique_ratio = stats["unique"] / n
    width_ratio = n / max_width if max_width else 0.0
    wordy_penalty = stats["wordy"] / n

    # How well the rows underneath behave like data in the header's own columns.
    header_cols = [c for c, v in enumerate(grid[r]) if not is_blank(v)]
    below_fill = 0.0
    below_data = 0.0
    considered = 0
    for rr in range(r + 1, min(r + 8, len(grid))):
        row = grid[rr]
        filled = sum(1 for c in header_cols if c < len(row) and not is_blank(row[c]))
        if filled == 0:
            continue
        considered += 1
        below_fill += filled / len(header_cols)
        below_data += sum(
            1 for c in header_cols if c < len(row) and looks_like_data(row[c])
        ) / len(header_cols)
    if considered == 0:
        # Nothing underneath: this cannot be the header of a data table.
        return float("-inf")
    below_fill /= considered
    below_data /= considered

    return (
        3.0 * vocab_ratio
        + 1.2 * label_ratio
        + 0.8 * unique_ratio
        + 1.0 * width_ratio
        + 1.5 * below_fill
        + 1.0 * below_data      # data-like rows *below* is a positive signal
        - 2.0 * wordy_penalty
    )


def _is_header_like(row: list, vocabulary: set[str]) -> bool:
    """Whether a row could plausibly be the second line of a two-row header."""
    stats = _row_stats(row, vocabulary)
    if stats["non_blank"] == 0:
        return False
    return stats["data_like"] / stats["non_blank"] < 0.3


def find_header_row(
    grid: list[list], vocabulary: set[str], settings: DetectionSettings
) -> tuple[int, int, float]:
    """Locate the header. Returns ``(start, end, score)`` as 0-indexed grid rows.

    ``end`` differs from ``start`` only for a two-row header, where the top line carries
    a merged group label and the line beneath carries the detail.
    """
    if not grid:
        return 0, 0, float("-inf")

    limit = min(settings.header_search_rows, len(grid))
    max_width = max((sum(1 for v in row if not is_blank(v)) for row in grid[:limit]), default=0)

    best_row, best_score = 0, float("-inf")
    for r in range(limit):
        score = score_header_row(grid, r, vocabulary, max_width)
        if score > best_score:
            best_row, best_score = r, score

    if best_score == float("-inf"):
        return 0, 0, best_score

    # Two-row header? The tell is either a merged group label (which read_grid has
    # expanded into repeated values) or gaps in the top line that the line below fills.
    end = best_row
    if best_row + 1 < len(grid):
        top, below = grid[best_row], grid[best_row + 1]
        top_non_blank = [c for c, v in enumerate(top) if not is_blank(v)]
        repeated = len(top_non_blank) - len({normalize_header(top[c]) for c in top_non_blank})
        gaps_filled = sum(
            1 for c in range(len(top))
            if is_blank(top[c]) and c < len(below) and not is_blank(below[c])
        )
        if (repeated > 0 or gaps_filled > 0) and _is_header_like(below, vocabulary):
            # Only accept if the line below is not itself the first data row: a data row
            # would carry recognisable values under the money columns.
            end = best_row + 1

    return best_row, end, best_score


def build_headers(grid: list[list], start: int, end: int) -> list[str]:
    """Compose the header text for each column, joining a two-row header.

    ``Prime`` (merged) over ``nette`` becomes ``"Prime nette"``. A repeated group label
    with nothing beneath it stays as-is rather than becoming ``"Prime Prime"``.
    """
    width = max(len(grid[r]) for r in range(start, end + 1))
    headers = []
    for c in range(width):
        parts = []
        for r in range(start, end + 1):
            value = grid[r][c] if c < len(grid[r]) else None
            if is_blank(value):
                continue
            text = str(value).strip()
            # Skip a part that merely repeats what we already have.
            if parts and normalize_header(parts[-1]) == normalize_header(text):
                continue
            parts.append(text)
        headers.append(" ".join(parts))
    return headers


def _is_terminator(row: list, settings: DetectionSettings) -> bool:
    """A ``TOTAL`` / ``SOMME`` / ``CUMUL`` line ends the table."""
    for value in row[:3]:
        if is_blank(value):
            continue
        normalized = normalize_header(value)
        for pattern in settings.terminator_patterns:
            if normalized == pattern or normalized.startswith(pattern + " "):
                return True
        # Only inspect the first non-blank cell of the row.
        break
    return False


def _preview(row: list, limit: int = 4) -> str:
    """A short human-readable rendering of a row, for the audit trail."""
    parts = [str(v)[:20] for v in row if not is_blank(v)][:limit]
    return " | ".join(parts)


def find_table_extent(
    grid: list[list], data_start: int, settings: DetectionSettings
) -> tuple[int, list[DroppedRow]]:
    """Find the last real data row, recording everything cut below it.

    Two passes. First walk down and stop at a blank run or a terminator line. Then trim
    backwards over rows that do not look like records — this is what removes the orphan
    number somebody left three rows under the table.
    """
    dropped: list[DroppedRow] = []
    end = data_start - 1
    blank_run = 0
    r = data_start

    while r < len(grid):
        row = grid[r]
        if all(is_blank(v) for v in row):
            blank_run += 1
            if blank_run >= settings.blank_run_ends_table:
                break
            r += 1
            continue

        if _is_terminator(row, settings):
            dropped.append(DroppedRow(r + 1, "total/summary line", _preview(row)))
            break

        blank_run = 0
        end = r
        r += 1

    if end < data_start:
        return end, dropped

    # Establish what a normal row looks like, using the columns that are reliably filled.
    body = grid[data_start : end + 1]
    width = max(len(row) for row in body)
    fill_counts = [
        sum(1 for row in body if c < len(row) and not is_blank(row[c]))
        for c in range(width)
    ]
    anchor_cols = [c for c, n in enumerate(fill_counts) if n >= 0.9 * len(body)]
    typical_filled = sum(
        sum(1 for v in row if not is_blank(v)) for row in body
    ) / len(body)

    # Trim trailing rows that are missing the anchor columns or are far too sparse.
    while end >= data_start:
        row = grid[end]
        filled = sum(1 for v in row if not is_blank(v))
        anchors_present = all(
            c < len(row) and not is_blank(row[c]) for c in anchor_cols
        ) if anchor_cols else True
        if anchors_present and filled >= max(2, 0.4 * typical_filled):
            break
        reason = (
            "missing identifying columns" if not anchors_present else "too sparse to be a record"
        )
        dropped.append(DroppedRow(end + 1, reason, _preview(row)))
        end -= 1

    return end, dropped


def select_columns(
    grid: list[list],
    headers: list[str],
    data_start: int,
    data_end: int,
    settings: DetectionSettings,
) -> tuple[list[int], list[DroppedColumn]]:
    """Keep the columns that carry data; report the rest.

    A column is dropped when it is empty throughout the data block, or when it has no
    header and is filled too sparsely to be anything but a stray annotation.
    """
    kept: list[int] = []
    dropped: list[DroppedColumn] = []
    n_rows = max(1, data_end - data_start + 1)

    for c, header in enumerate(headers):
        filled = sum(
            1 for r in range(data_start, data_end + 1)
            if c < len(grid[r]) and not is_blank(grid[r][c])
        )
        fill_rate = filled / n_rows
        if filled == 0:
            dropped.append(
                DroppedColumn(c + 1, get_column_letter(c + 1), header, "empty in data block")
            )
            continue
        if not header.strip() and fill_rate < settings.min_column_fill_rate:
            dropped.append(
                DroppedColumn(
                    c + 1, get_column_letter(c + 1), header,
                    f"no header and only {fill_rate:.0%} filled",
                )
            )
            continue
        kept.append(c)

    return kept, dropped


def extract_table(ws, vocabulary: set[str], settings: DetectionSettings) -> SheetTable:
    """Run the full structural pass over one worksheet."""
    grid = read_grid(ws)
    if not grid:
        return SheetTable(sheet=ws.title, header_row=0, header_row_end=0,
                          data_start=0, data_end=0,
                          notes=["sheet is empty"])

    h_start, h_end, score = find_header_row(grid, vocabulary, settings)
    if score == float("-inf"):
        return SheetTable(sheet=ws.title, header_row=0, header_row_end=0,
                          data_start=0, data_end=0,
                          notes=["no plausible header row found"])

    headers = build_headers(grid, h_start, h_end)
    data_start = h_end + 1
    data_end, dropped_rows = find_table_extent(grid, data_start, settings)

    table = SheetTable(
        sheet=ws.title,
        header_row=h_start + 1,
        header_row_end=h_end + 1,
        data_start=data_start + 1,
        data_end=data_end + 1,
        header_score=score,
    )

    if data_end < data_start:
        table.notes.append("header found but no data rows beneath it")
        table.dropped_rows = dropped_rows
        return table

    # Everything above the header is junk by construction; log it so it is accounted for.
    for r in range(h_start):
        if not all(is_blank(v) for v in grid[r]):
            table.dropped_rows.append(
                DroppedRow(r + 1, "above the header row", _preview(grid[r]))
            )

    kept_cols, dropped_cols = select_columns(grid, headers, data_start, data_end, settings)
    table.columns = [c + 1 for c in kept_cols]
    table.headers = [headers[c] for c in kept_cols]
    table.dropped_columns = dropped_cols

    for r in range(data_start, data_end + 1):
        row = grid[r]
        if all(is_blank(v) for v in row):
            table.dropped_rows.append(DroppedRow(r + 1, "blank row inside table", ""))
            continue
        table.rows.append([row[c] if c < len(row) else None for c in kept_cols])

    table.dropped_rows.extend(dropped_rows)
    table.dropped_rows.sort(key=lambda d: d.row)
    return table
