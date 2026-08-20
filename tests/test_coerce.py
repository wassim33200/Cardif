import datetime as dt

import pytest

from cardif.coerce import (
    coerce_column, detect_decimal_separator, french_month, to_date, to_identifier,
    to_number,
)


class TestDecimalSeparator:
    """The separator is decided once per column, never per cell."""

    @pytest.mark.parametrize(
        "values,expected",
        [
            (["1 234,56", "987,20", "45,00"], ","),
            (["1,234.56", "987.20", "45.00"], "."),
            (["1.234,56", "2.500,00"], ","),
            # Three trailing digits and no other separator means grouping, so the
            # decimal separator is the *other* character.
            (["1.234", "5.678", "9.012"], ","),
            ([], ","),
        ],
    )
    def test_vote(self, values, expected):
        assert detect_decimal_separator(values) == expected

    def test_column_is_internally_consistent(self):
        # A dot followed by exactly three digits, with no other separator present, is a
        # thousands separator -- so the whole column reads as grouped integers.
        values, report = coerce_column(["1.234", "5.678"], "money")
        assert report.decimal_separator == ","
        assert values == [1234.0, 5678.0]

    def test_no_cell_is_interpreted_differently_from_its_neighbours(self):
        # The failure this guards against: guessing per cell turns one row's 1.234 into
        # 1234 and the next row's into 1.234, scattering the error invisibly through
        # the column. Whatever the vote decides, it applies to every cell.
        values, _ = coerce_column(["1.234", "2.500", "1.234,56"], "money")
        assert values == [1234.0, 2500.0, 1234.56]


class TestNumbers:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("1 234,56", 1234.56),
            ("1 234,56", 1234.56),   # non-breaking space
            ("1 234,56", 1234.56),   # narrow no-break space
            ("1.234,56", 1234.56),
            ("2 500,00 DT", 2500.0),
            ("(500,25)", -500.25),        # parenthesised negative
        ],
    )
    def test_french_formats(self, text, expected):
        assert to_number(text, ",") == pytest.approx(expected)

    def test_unparseable_returns_none_rather_than_zero(self):
        assert to_number("abc", ",") is None
        assert to_number(None, ",") is None


class TestDates:
    def test_day_first_is_forced(self):
        # The single most dangerous default in the whole pipeline: pandas would read
        # this as 3 March and never say so.
        assert to_date("03/04/2025") == dt.date(2025, 4, 3)

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("31/12/2024", dt.date(2024, 12, 31)),
            ("2025-04-03", dt.date(2025, 4, 3)),
            ("12-mars-2025", dt.date(2025, 3, 12)),
            ("12-mars-25", dt.date(2025, 3, 12)),
            (45750, dt.date(2025, 4, 3)),                 # Excel serial
            (dt.datetime(2025, 2, 14), dt.date(2025, 2, 14)),
        ],
    )
    def test_formats(self, value, expected):
        assert to_date(value) == expected

    def test_impossible_dates_rejected(self):
        assert to_date("31/02/2025") is None
        assert to_date("garbage") is None

    @pytest.mark.parametrize(
        "token,month",
        [("nove", 11), ("avri", 4), ("dece", 12), ("sept", 9), ("janv", 1),
         ("fevr", 2), ("juil", 7), ("nov", 11), ("aou", 8)],
    )
    def test_month_truncations(self, token, month):
        # Banks truncate to whatever their locale produced; every length must work.
        assert french_month(token) == month

    @pytest.mark.parametrize("token", ["jui", "ju", "j", "xyz"])
    def test_ambiguous_month_refuses_to_guess(self, token):
        # "jui" is both juin and juillet. Guessing would file sales in the wrong month.
        assert french_month(token) is None


class TestIdentifiers:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (20250001.0, "20250001"),        # float round-trip damage, undone
            ("BNA-202502-0001", "BNA-202502-0001"),
            ("  0012  ", "0012"),            # leading zeros preserved
            (12345, "12345"),
        ],
    )
    def test_stays_text(self, value, expected):
        assert to_identifier(value) == expected


def test_column_report_counts_failures():
    values, report = coerce_column(["1 234,56", "oops", None, "3 500,10"], "money", "Prime")
    assert values == [1234.56, None, None, 3500.10]
    assert (report.converted, report.failed, report.blank) == (2, 1, 1)
    assert report.failures == ["oops"]
